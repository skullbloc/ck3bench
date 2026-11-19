"""The env-owned multi-agent layer: seat-to-seat messages and notepads.

These are ENVIRONMENT tools, not game tools: the environment answers them
itself, beside the backend, so every backend (the fake one and the real one
alike) and every policy (a script, a model behind any provider, a Claude
Code seat through the MCP shim) gets the same channel. A scenario declares them
in a `[comms]` table and lists the tools in `tools`; both must agree.

    [comms.messages]   quota_per_step, max_text_bytes          -> msg_send(to, text)
    [comms.notepads]   private_max_bytes, shared_max_bytes,    -> note_read(pad), note_write(pad, text, mode,
                       max_write_bytes                              version)

The tool names and argument shapes are the ck3agent toolkit's own (its
msg_send / note_read / note_write), so a prompt written for one reads right on
the other; the shared pad is called `shared` here (the toolkit calls it `council`).

THE DELIVERY RULE (deterministic whatever order concurrent seats' calls arrive in):

  * A message sent in step k (an in-turn call or a step action) is QUEUED. At
    the end of step k, after the clock has advanced, every queued message is
    delivered in the step's seeded seat order, each sender's in the order it
    sent them, into the recipient's next observation (`comms.inbox`). A message
    to a seat that is no longer live after the step is DROPPED (`recipient_done`),
    and the drop is recorded. There is no broadcast: one message, one recipient.
  * A seat's PRIVATE pad is its own: reads and writes apply at once.
  * The SHARED pad changes only at step ends. A read returns the pad as of the
    last step end (its `version`) and the reader's own writes still pending. A
    write is queued; at the end of the step the queued writes apply in the
    seeded seat order. `append` applies unless the pad would overflow;
    `replace` applies only if the pad is still at the version the writer read
    (an earlier write in the same step makes it `version_stale`). Each write's
    outcome reaches the writer in its next observation (`comms.shared_pad_results`).
  * Bounds are refused, never truncated: a text over `max_text_bytes`, a write
    over `max_write_bytes`, a pad that would exceed its size, an
    over-quota message. A refusal spends no quota.

Every call is recorded like a game tool call (the trajectory's `call` records);
every step end's deliveries, drops and shared-pad outcomes are in the step
record's `comms`.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional, Sequence

from .errors import R_SCENARIO_KEY, R_SCENARIO_MISSING, R_SCENARIO_TYPE, R_SCENARIO_VALUE, EnvRefusal
from .types import ToolCall, ToolResult

COMMS_TOOLS = ("msg_send", "note_read", "note_write")
PAD_PRIVATE, PAD_SHARED = "private", "shared"
PADS = (PAD_PRIVATE, PAD_SHARED)
MODES = ("append", "replace")

# verdicts: what happened to one call or one queued item
V_QUEUED = "queued"
V_DELIVERED = "delivered"
V_APPLIED = "applied"
V_READ = "read"
V_ARGS = "args_malformed"
V_RECIPIENT_UNKNOWN = "recipient_unknown"
V_RECIPIENT_SELF = "recipient_self"
V_RECIPIENT_DONE = "recipient_done"
V_TEXT_TOO_LONG = "text_too_long"
V_QUOTA = "quota_exceeded"
V_PAD_UNKNOWN = "pad_unknown"
V_PAD_NOT_DECLARED = "pad_not_declared"
V_MODE = "mode_invalid"
V_VERSION_REQUIRED = "version_required"
V_VERSION_STALE = "version_stale"
V_WRITE_TOO_LONG = "write_too_long"
V_PAD_FULL = "pad_full"


@dataclass(frozen=True)
class MessagesDecl:
    quota_per_step: int
    max_text_bytes: int


@dataclass(frozen=True)
class NotepadsDecl:
    private_max_bytes: Optional[int]
    shared_max_bytes: Optional[int]
    max_write_bytes: int


@dataclass(frozen=True)
class CommsDecl:
    messages: Optional[MessagesDecl]
    notepads: Optional[NotepadsDecl]

    def tools(self) -> tuple[str, ...]:
        return (("msg_send",) if self.messages else ()) + (("note_read", "note_write") if self.notepads else ())

    def pads(self) -> tuple[str, ...]:
        n = self.notepads
        if n is None:
            return ()
        return tuple(p for p, b in ((PAD_PRIVATE, n.private_max_bytes), (PAD_SHARED, n.shared_max_bytes))
                     if b is not None)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {}
        if self.messages:
            d["messages"] = {"quota_per_step": self.messages.quota_per_step,
                             "max_text_bytes": self.messages.max_text_bytes}
        if self.notepads:
            n = self.notepads
            d["notepads"] = {k: v for k, v in (("private_max_bytes", n.private_max_bytes),
                                               ("shared_max_bytes", n.shared_max_bytes),
                                               ("max_write_bytes", n.max_write_bytes)) if v is not None}
        return d


def _int_table(d: Any, required: Sequence[str], optional: Sequence[str], where: str) -> dict[str, int]:
    if not isinstance(d, Mapping):
        raise EnvRefusal(R_SCENARIO_TYPE, f"{where} is not a table", "scenario")
    unknown = set(d) - set(required) - set(optional)
    if unknown:
        raise EnvRefusal(R_SCENARIO_KEY, f"{where}: {sorted(unknown)}", "scenario")
    for k in required:
        if k not in d:
            raise EnvRefusal(R_SCENARIO_MISSING, f"{where}.{k}", "scenario")
    for k, v in d.items():
        if not isinstance(v, int) or isinstance(v, bool):
            raise EnvRefusal(R_SCENARIO_TYPE, f"{where}.{k} is {type(v).__name__}, not an int", "scenario")
        if v < 1:
            raise EnvRefusal(R_SCENARIO_VALUE, f"{where}.{k} = {v}: a bound is at least 1", "scenario")
    return dict(d)


def comms_from_dict(d: Any, seats: Sequence[str], where: str = "comms") -> CommsDecl:
    """The strict reader of a scenario's `[comms]` table."""
    if not isinstance(d, Mapping):
        raise EnvRefusal(R_SCENARIO_TYPE, f"{where} is not a table", "scenario")
    unknown = set(d) - {"messages", "notepads"}
    if unknown:
        raise EnvRefusal(R_SCENARIO_KEY, f"{where}: {sorted(unknown)}", "scenario")
    if not d:
        raise EnvRefusal(R_SCENARIO_VALUE, f"{where} declares neither messages nor notepads", "scenario")
    msgs = None
    if "messages" in d:
        if len(seats) < 2:
            raise EnvRefusal(R_SCENARIO_VALUE, f"{where}.messages: a one-seat scenario has nobody to write to",
                             "scenario")
        m = _int_table(d["messages"], ("quota_per_step", "max_text_bytes"), (), f"{where}.messages")
        msgs = MessagesDecl(m["quota_per_step"], m["max_text_bytes"])
    pads = None
    if "notepads" in d:
        n = _int_table(d["notepads"], ("max_write_bytes",), ("private_max_bytes", "shared_max_bytes"),
                       f"{where}.notepads")
        if "private_max_bytes" not in n and "shared_max_bytes" not in n:
            raise EnvRefusal(R_SCENARIO_MISSING, f"{where}.notepads declares no pad (private_max_bytes and/or "
                                                 "shared_max_bytes)", "scenario")
        if "shared_max_bytes" in n and len(seats) < 2:
            raise EnvRefusal(R_SCENARIO_VALUE, f"{where}.notepads: a shared pad needs two seats", "scenario")
        pads = NotepadsDecl(n.get("private_max_bytes"), n.get("shared_max_bytes"), n["max_write_bytes"])
    return CommsDecl(msgs, pads)


#: Input schemas for the env-owned tools (JSON Schema, the shape MCP and every
#: tool-calling API take).
TOOL_SPECS: dict[str, dict[str, Any]] = {
    "msg_send": {"name": "msg_send",
                 "description": "Send a short message to another seat. It is delivered at the end of this step, "
                                "into the recipient's next observation (comms.inbox). One recipient per message; "
                                "a quota per step and a byte bound apply; over-long text is refused, never cut.",
                 "inputSchema": {"type": "object", "additionalProperties": False, "required": ["to", "text"],
                                 "properties": {"to": {"type": "string", "description": "the recipient seat's name"},
                                                "text": {"type": "string"}}}},
    "note_read": {"name": "note_read",
                  "description": "Read one of your notepads whole: 'private' (yours alone) or 'shared' (one pad "
                                 "every seat reads; it changes only at step ends). Returns text and version.",
                  "inputSchema": {"type": "object", "additionalProperties": False, "required": ["pad"],
                                  "properties": {"pad": {"type": "string", "enum": list(PADS)}}}},
    "note_write": {"name": "note_write",
                   "description": "Write to a notepad. mode 'append' adds text at the end; 'replace' sets the "
                                  "whole pad and must name the version you last read. Writes to the shared pad "
                                  "apply at the end of the step in the seeded seat order; their outcome is in "
                                  "your next observation. Bounds are refused, never truncated.",
                   "inputSchema": {"type": "object", "additionalProperties": False,
                                   "required": ["pad", "text", "mode"],
                                   "properties": {"pad": {"type": "string", "enum": list(PADS)},
                                                  "text": {"type": "string"},
                                                  "mode": {"type": "string", "enum": list(MODES)},
                                                  "version": {"type": "integer"}}}},
}


def _nbytes(t: str) -> int:
    return len(t.encode("utf-8"))


class Comms:
    """One episode's channel and pads. The environment calls `handle` for an
    env-owned tool call and `end_step` once per step, after the advance."""

    def __init__(self, decl: CommsDecl, seats: Sequence[str]):
        self.decl, self.seats = decl, tuple(seats)
        self.reset()

    def reset(self) -> None:
        self._queue: list[dict] = []                    # messages sent this step, in arrival order
        self._sent: dict[str, int] = {s: 0 for s in self.seats}
        self._n: dict[str, int] = {s: 0 for s in self.seats}
        self.private: dict[str, dict] = {s: {"text": "", "version": 0} for s in self.seats}
        self.shared: dict[str, Any] = {"text": "", "version": 0, "updated_by": None, "updated_step": None}
        self._writes: list[dict] = []                   # shared-pad writes queued this step
        self._view: dict[str, dict] = {s: self._fresh_view(s) for s in self.seats}

    def _fresh_view(self, seat: str) -> dict:
        v: dict[str, Any] = {}
        if self.decl.messages:
            v["inbox"] = []
            v["messages_left"] = self.decl.messages.quota_per_step - self._sent.get(seat, 0)
        if PAD_SHARED in self.decl.pads():
            v["shared_pad_results"] = []
            v["shared_pad_version"] = self.shared["version"]
        return v

    def observation(self, seat: str) -> dict:
        """The seat's comms view: what reached it at the last step end."""
        v = dict(self._view.get(seat, {}))
        if self.decl.messages:
            v["messages_left"] = self.decl.messages.quota_per_step - self._sent.get(seat, 0)
        return v

    # ── calls ──────────────────────────────────────────────────────────────
    def handle(self, seat: str, call: ToolCall, step: int, game_date: Optional[str]) -> ToolResult:
        name, a = call.tool_name, dict(call.arguments)
        if name == "msg_send":
            return self._msg_send(seat, a, step, game_date)
        if name == "note_read":
            return self._note_read(seat, a)
        if name == "note_write":
            return self._note_write(seat, a, step)
        raise EnvRefusal(R_SCENARIO_VALUE, f"{name} is not an env-owned tool", "comms")

    @staticmethod
    def _no(name: str, verdict: str, detail: str, **row: Any) -> ToolResult:
        return ToolResult(name, False, {"verdict": verdict, **row}, f"{verdict}: {detail}")

    def _check_args(self, name: str, a: Mapping[str, Any]) -> Optional[ToolResult]:
        props = TOOL_SPECS[name]["inputSchema"]["properties"]
        req = TOOL_SPECS[name]["inputSchema"]["required"]
        extra = sorted(set(a) - set(props))
        missing = [k for k in req if k not in a]
        bad = [k for k, v in a.items() if k in props and not (
            (props[k]["type"] == "string" and isinstance(v, str))
            or (props[k]["type"] == "integer" and isinstance(v, int) and not isinstance(v, bool)))]
        if extra or missing or bad:
            return self._no(name, V_ARGS, f"unknown {extra}, missing {missing}, wrong type {bad}")
        return None

    def _msg_send(self, seat: str, a: dict, step: int, game_date: Optional[str]) -> ToolResult:
        if (r := self._check_args("msg_send", a)) is not None:
            return r
        decl = self.decl.messages
        assert decl is not None
        to, text = a["to"], a["text"]
        if to == seat:
            return self._no("msg_send", V_RECIPIENT_SELF, "a seat does not write to itself")
        if to not in self.seats:
            return self._no("msg_send", V_RECIPIENT_UNKNOWN, f"{to!r} is not a seat {list(self.seats)}")
        if _nbytes(text) > decl.max_text_bytes:
            return self._no("msg_send", V_TEXT_TOO_LONG, f"{_nbytes(text)} bytes > {decl.max_text_bytes}; "
                                                         "nothing sent, nothing cut", bytes=_nbytes(text))
        if self._sent[seat] >= decl.quota_per_step:
            return self._no("msg_send", V_QUOTA, f"{decl.quota_per_step} messages already sent this step")
        self._sent[seat] += 1
        self._n[seat] += 1
        msg = {"msg_id": f"{step}-{seat}-{self._n[seat]}", "from": seat, "to": to, "text": text,
               "sent_step": step, "sent_game_date": game_date, "bytes": _nbytes(text)}
        self._queue.append(msg)
        return ToolResult("msg_send", True, {"verdict": V_QUEUED, "msg_id": msg["msg_id"], "to": to,
                                             "delivers": f"at the end of step {step}",
                                             "messages_left": decl.quota_per_step - self._sent[seat]})

    def _pad_ok(self, name: str, pad: Any) -> Optional[ToolResult]:
        if pad not in PADS:
            return self._no(name, V_PAD_UNKNOWN, f"pad is one of {list(PADS)}")
        if pad not in self.decl.pads():
            return self._no(name, V_PAD_NOT_DECLARED, f"this scenario declares the pads {list(self.decl.pads())}")
        return None

    def _note_read(self, seat: str, a: dict) -> ToolResult:
        if (r := self._check_args("note_read", a)) is not None:
            return r
        if (r := self._pad_ok("note_read", a["pad"])) is not None:
            return r
        n = self.decl.notepads
        assert n is not None
        if a["pad"] == PAD_PRIVATE:
            p = self.private[seat]
            return ToolResult("note_read", True, {"verdict": V_READ, "pad": PAD_PRIVATE, "text": p["text"],
                                                  "version": p["version"], "bytes": _nbytes(p["text"]),
                                                  "max_bytes": n.private_max_bytes})
        mine = [{"write_id": w["write_id"], "mode": w["mode"], "bytes": w["bytes"]}
                for w in self._writes if w["seat"] == seat]
        return ToolResult("note_read", True, {"verdict": V_READ, "pad": PAD_SHARED, "text": self.shared["text"],
                                              "version": self.shared["version"],
                                              "bytes": _nbytes(self.shared["text"]),
                                              "max_bytes": n.shared_max_bytes,
                                              "updated_by": self.shared["updated_by"],
                                              "updated_step": self.shared["updated_step"],
                                              "your_pending_writes": mine})

    def _note_write(self, seat: str, a: dict, step: int) -> ToolResult:
        if (r := self._check_args("note_write", a)) is not None:
            return r
        if (r := self._pad_ok("note_write", a["pad"])) is not None:
            return r
        n = self.decl.notepads
        assert n is not None
        pad, text, mode, version = a["pad"], a["text"], a["mode"], a.get("version")
        if mode not in MODES:
            return self._no("note_write", V_MODE, f"mode is one of {list(MODES)}")
        if mode == "replace" and version is None:
            return self._no("note_write", V_VERSION_REQUIRED, "a replace names the version note_read returned")
        if _nbytes(text) > n.max_write_bytes:
            return self._no("note_write", V_WRITE_TOO_LONG, f"{_nbytes(text)} bytes > {n.max_write_bytes}; "
                                                            "nothing written, nothing cut", bytes=_nbytes(text))
        if pad == PAD_PRIVATE:
            p = self.private[seat]
            out = self._apply(p, text, mode, version, n.private_max_bytes)
            if out is not None:
                return self._no("note_write", out, f"private pad at version {p['version']}",
                                version=p["version"])
            return ToolResult("note_write", True, {"verdict": V_APPLIED, "pad": PAD_PRIVATE,
                                                   "version": p["version"], "bytes": _nbytes(p["text"])})
        wid = f"{step}-{seat}-w{sum(1 for w in self._writes if w['seat'] == seat) + 1}"
        self._writes.append({"write_id": wid, "seat": seat, "mode": mode, "text": text, "version": version,
                             "bytes": _nbytes(text), "step": step, "seq": len(self._writes)})
        return ToolResult("note_write", True, {"verdict": V_QUEUED, "pad": PAD_SHARED, "write_id": wid,
                                               "applies": f"at the end of step {step}, in the seeded seat order"})

    @staticmethod
    def _apply(p: dict, text: str, mode: str, version: Optional[int], max_bytes: Optional[int]) -> Optional[str]:
        """Apply one write to a pad dict in place; the refusal verdict, or None."""
        if mode == "replace" and version != p["version"]:
            return V_VERSION_STALE
        new = text if mode == "replace" else p["text"] + text
        if max_bytes is not None and _nbytes(new) > max_bytes:
            return V_PAD_FULL
        p["text"] = new
        p["version"] += 1
        return None

    # ── the step end ──────────────────────────────────────────────────────
    def end_step(self, step: int, order: Sequence[str], live_after: Sequence[str]) -> dict:
        """Deliver the queued messages and apply the queued shared-pad writes,
        in the step's seeded seat order. Returns the step record's `comms`."""
        rank = {s: i for i, s in enumerate(order)}
        rec: dict[str, Any] = {"delivered": [], "dropped": [], "shared_pad_applied": [], "shared_pad_refused": []}
        views = {s: self._fresh_view(s) for s in self.seats}
        for m in sorted(self._queue, key=lambda m: (rank.get(m["from"], len(rank)),
                                                    int(m["msg_id"].rsplit("-", 1)[1]))):
            if m["to"] in live_after:
                rec["delivered"].append(dict(m))
                views[m["to"]]["inbox"].append({k: m[k] for k in ("msg_id", "from", "text", "sent_step",
                                                                  "sent_game_date")})
            else:
                rec["dropped"].append({**m, "why": V_RECIPIENT_DONE})
        n = self.decl.notepads
        for w in sorted(self._writes, key=lambda w: (rank.get(w["seat"], len(rank)), w["seq"])):
            before = self.shared["version"]
            why = self._apply(self.shared, w["text"], w["mode"], w["version"], n.shared_max_bytes if n else None)
            row = {"write_id": w["write_id"], "seat": w["seat"], "mode": w["mode"], "bytes": w["bytes"],
                   "version_before": before, "version_after": self.shared["version"]}
            if why is None:
                self.shared["updated_by"], self.shared["updated_step"] = w["seat"], step
                row["verdict"] = V_APPLIED
                rec["shared_pad_applied"].append(row)
            else:
                row.update(verdict=why, version_named=w["version"])
                rec["shared_pad_refused"].append(row)
            views[w["seat"]]["shared_pad_results"].append({k: v for k, v in row.items() if k != "seat"})
        for s in self.seats:
            if "shared_pad_version" in views[s]:
                views[s]["shared_pad_version"] = self.shared["version"]
        rec["shared_pad_version"] = self.shared["version"] if n and n.shared_max_bytes else None
        self._queue, self._writes = [], []
        self._sent = {s: 0 for s in self.seats}
        self._view = views
        return rec


def tool_specs(decl: Optional[CommsDecl]) -> list[dict[str, Any]]:
    return [dict(TOOL_SPECS[t]) for t in (decl.tools() if decl else ())]
