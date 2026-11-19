"""A reference tool-calling agent: drives one seat from any chat model.

The model sits behind a small, provider-neutral `Model` protocol: messages and
tool specs in, a reply with text and tool calls out. Adapters translate to a
provider's API (`anthropic_model.py` is one, optional); `ScriptedModel` is a
deterministic one for tests and for wiring a loop before paying for a model.

ONE TURN (one environment step). The agent shows the model the seat's
observation (`policy.render_text` plus the seat's comms), offers it the seat's
tools (their input schemas from the env: `ToolHandle.specs`) and one more,
`end_turn`. Each tool call the model makes runs AT ONCE as an in-turn call
(the game paused; its result goes back to the model in the same turn), so the
agent acts through in-turn calls and its step action is `[]`. The turn ends,
typed (`TURN_ENDS`), when the model calls `end_turn`, when it replies with no
tool call (`model_stopped`), when the turn budget runs out (`max_calls` tool
calls, `max_wall_s` seconds), or when the model call raises (`model_error`).
Every tool call the model asked for gets a result message, including the ones
the budget refused, so a provider that pairs calls and results is satisfied.

The budget is checked between calls: a single model call that blocks is not
interrupted here (Python cannot stop a thread). The episode runner
(`episode.py`) bounds the whole turn from outside and yields `[]` with a typed
timeout record when it overruns.

Each turn starts a fresh conversation: what a seat remembers across steps is
what it wrote in its notepads (comms.py) and what the observation carries.

Usage: the tokens each model call REPORTED are summed per turn and handed to
the step's `usage` (`report_usage`); a model call that reported nothing is
counted as such (`model_calls_unreported`), never estimated. The turn record
(`report_turn`) goes to the step's `turns`.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Optional, Protocol, Sequence

from .policy import ToolHandle, render_text
from .types import ToolCall

END_TURN = "end_turn"
END_TURN_SPEC = {"name": END_TURN,
                 "description": "End your turn for this step. The game then runs the step's game-days and you "
                                "see the result next turn. Call it when you have done what you want this step.",
                 "inputSchema": {"type": "object", "additionalProperties": False,
                                 "properties": {"summary": {"type": "string",
                                                            "description": "one line on what you did and why"}}}}

#: How a turn ended.
T_END_TURN = "end_turn"
T_MODEL_STOPPED = "model_stopped"
T_MAX_CALLS = "max_calls"
T_MAX_WALL = "max_wall"
T_MODEL_ERROR = "model_error"
TURN_ENDS = (T_END_TURN, T_MODEL_STOPPED, T_MAX_CALLS, T_MAX_WALL, T_MODEL_ERROR)

DEFAULT_SYSTEM = ("You play one seat (a ruler) in a game of Crusader Kings III exposed as an environment. "
                  "Each turn you see your seat's observation and may call tools; each call runs at once, the "
                  "game paused, and you see its result. When you are done for this step, call end_turn.")


@dataclass(frozen=True)
class ModelToolCall:
    id: str
    name: str
    arguments: Any            # a JSON object when well formed; the agent checks


@dataclass
class ModelReply:
    text: str = ""
    tool_calls: list[ModelToolCall] = field(default_factory=list)
    #: tokens as the provider reported them for this call (input_tokens, output_tokens, ...); None = unreported
    usage: Optional[dict[str, int]] = None
    #: the provider's stop reason, as given (end_turn, tool_use, max_tokens, refusal, ...)
    stop: Optional[str] = None
    #: the provider's own assistant content, replayed unchanged on the next call (an adapter's business)
    raw: Any = None


class Model(Protocol):
    """messages: [{"role": "system"|"user", "content": str},
                  {"role": "assistant", "content": str, "tool_calls": [{"id", "name", "arguments"}], "raw": ...},
                  {"role": "tool", "tool_call_id": str, "name": str, "content": str, "is_error": bool}]
    tools: [{"name", "description", "inputSchema"}] (JSON Schema, the MCP shape)."""

    def complete(self, messages: list[dict], tools: list[dict]) -> ModelReply: ...


class ScriptedModel:
    """A deterministic model for tests: each `complete` returns the next reply
    from `replies` (a ModelReply, or a function of (messages, tools) returning
    one). When the script is exhausted it calls `end_turn`. Records every
    request it saw in `requests`."""

    def __init__(self, replies: Sequence[ModelReply | Callable[[list, list], ModelReply]] = ()):
        self.replies = list(replies)
        self.requests: list[dict] = []
        self._n = 0

    def complete(self, messages, tools):
        self.requests.append({"messages": [dict(m) for m in messages], "tools": [t["name"] for t in tools]})
        self._n += 1
        if not self.replies:
            return ModelReply(tool_calls=[ModelToolCall(f"s{self._n}", END_TURN, {})], stop="tool_use")
        r = self.replies.pop(0)
        return r(messages, tools) if callable(r) else r


class ObserveThenEndModel:
    """A deterministic model for wiring and smoke runs (`ck3bench env play ...
    SEAT=agent-scripted`): each turn it calls `observe` once (when the seat has
    it) and then `end_turn`. It reports no usage (it spends none)."""

    def complete(self, messages, tools):
        started = any(m["role"] == "assistant" for m in messages)
        if not started and any(t["name"] == "observe" for t in tools):
            return ModelReply(tool_calls=[ModelToolCall("o1", "observe", {})], stop="tool_use")
        return ModelReply(tool_calls=[ModelToolCall("e1", END_TURN, {"summary": "observed"})], stop="tool_use")


def calls(*pairs: tuple[str, Mapping[str, Any]], usage: Optional[dict] = None) -> ModelReply:
    """A scripted reply making the given tool calls: calls(("observe", {}), ("do", {...}))."""
    return ModelReply(tool_calls=[ModelToolCall(f"c{i}", n, dict(a)) for i, (n, a) in enumerate(pairs)],
                      usage=usage, stop="tool_use")


def render_observation(obs: Mapping[str, Any]) -> str:
    """render_text plus the seat's comms (inbox, shared-pad outcomes)."""
    lines = [render_text(obs)]
    c = obs.get("comms") or {}
    for m in c.get("inbox") or []:
        lines.append(f"Message from {m['from']} (sent step {m['sent_step']}): {m['text']}")
    for w in c.get("shared_pad_results") or []:
        lines.append(f"Your shared-pad write {w['write_id']}: {w['verdict']} (pad version {w['version_after']})")
    if "messages_left" in c:
        lines.append(f"You may send {c['messages_left']} message(s) this step.")
    return "\n".join(lines)


class ToolCallingAgent:
    """A Policy (`act(observation, tools) -> []`) driving one seat from a Model."""

    name = "tool-calling-agent"

    def __init__(self, model: Model, *, max_calls: int, max_wall_s: float, system: str = DEFAULT_SYSTEM,
                 clock: Callable[[], float] = time.monotonic):
        if not (isinstance(max_calls, int) and max_calls >= 1) or not max_wall_s > 0:
            raise ValueError("a turn budget is max_calls >= 1 and max_wall_s > 0")
        self.model, self.max_calls, self.max_wall_s = model, max_calls, max_wall_s
        self.system, self.clock = system, clock
        self._usage: Optional[dict] = None
        self._turn: Optional[dict] = None
        self.transcripts: list[list[dict]] = []

    def act(self, observation: Mapping[str, Any], tools: ToolHandle) -> list[ToolCall]:
        specs = list(tools.specs or []) + [END_TURN_SPEC]
        msgs: list[dict] = [{"role": "system", "content": self.system},
                            {"role": "user", "content": render_observation(observation)}]
        t0 = self.clock()
        n_calls = n_model = n_unreported = 0
        usage: dict[str, int] = {}
        status: Optional[str] = None
        detail = ""
        stop = None
        while status is None:
            if self.clock() - t0 >= self.max_wall_s:
                status, detail = T_MAX_WALL, f"{self.max_wall_s:g} s before a model call"
                break
            try:
                reply = self.model.complete(msgs, specs)
            except Exception as e:  # noqa: BLE001 - any provider failure ends the turn, typed
                status, detail = T_MODEL_ERROR, f"{type(e).__name__}: {e}"
                break
            n_model += 1
            stop = reply.stop
            if reply.usage:
                for k, v in reply.usage.items():
                    if isinstance(v, int) and not isinstance(v, bool):
                        usage[k] = usage.get(k, 0) + v
            else:
                n_unreported += 1
            msgs.append({"role": "assistant", "content": reply.text,
                         "tool_calls": [{"id": c.id, "name": c.name, "arguments": c.arguments}
                                        for c in reply.tool_calls], "raw": reply.raw})
            if not reply.tool_calls:
                status, detail = T_MODEL_STOPPED, f"the model replied with no tool call (stop {reply.stop})"
                break
            for c in reply.tool_calls:
                if status is not None:
                    content, err = f"not run: the turn already ended ({status})", True
                elif c.name == END_TURN:
                    status, content, err = T_END_TURN, "turn ended", False
                    summary = c.arguments.get("summary") if isinstance(c.arguments, Mapping) else None
                    detail = summary if isinstance(summary, str) else ""
                elif n_calls >= self.max_calls:
                    status, detail = T_MAX_CALLS, f"{self.max_calls} tool calls"
                    content, err = f"not run: the turn's budget is spent ({status})", True
                elif self.clock() - t0 >= self.max_wall_s:
                    status, detail = T_MAX_WALL, f"{self.max_wall_s:g} s"
                    content, err = f"not run: the turn's budget is spent ({status})", True
                else:
                    n_calls += 1
                    if not isinstance(c.arguments, Mapping):
                        content, err = (f"not run: arguments must be a JSON object, "
                                        f"got {type(c.arguments).__name__}"), True
                    else:
                        r = tools.call(c.name, **dict(c.arguments))
                        content, err = json.dumps(r.to_dict(), default=str), not r.ok
                msgs.append({"role": "tool", "tool_call_id": c.id, "name": c.name, "content": content,
                             "is_error": err})
        self.transcripts.append(msgs)
        self._usage = ({**usage, "model_calls": n_model, "model_calls_unreported": n_unreported}
                       if n_model else None)
        self._turn = {"status": status, "tool_calls": n_calls, "model_calls": n_model,
                      "wall_s": round(self.clock() - t0, 6), "stop": stop, **({"detail": detail} if detail else {})}
        return []

    def report_usage(self) -> Optional[dict]:
        u, self._usage = self._usage, None
        return u

    def report_turn(self) -> Optional[dict]:
        t, self._turn = self._turn, None
        return t
