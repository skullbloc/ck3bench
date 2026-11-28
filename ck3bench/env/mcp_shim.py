"""The Claude Code route: one seat's tools from a running env server, as a stdio MCP server.

    python -m ck3bench.env.mcp_shim --url http://127.0.0.1:8770 --seat king --step-wait-s 900

Point a Claude Code session's MCP config at this command and the session plays
that seat. It lists:

  * the seat's tools, with the env's own input schemas (GET /tools): every call
    is a /call, an in-turn call recorded in the trajectory like any other;
  * `seat_observation`: the seat's current observation and the step now open,
    the turn's calls so far one line each (compact_step);
  * `end_turn(summary)`: submits the seat's turn for this step (/submit, action
    `[]`, turn record {"status": "end_turn", "by": "mcp_shim"}) and waits, up
    to --step-wait-s, for the step to run (every seat submitted, or the
    server's deadline). It returns the seat's next observation, reward and
    whether it is done, so a session can play step after step; the
    observation's `last_results` come back one line per call (compact_step);
  * `await_step`: waits again when `end_turn` came back `step_pending`.

With `--end-turn ack` (a new session for every turn) `end_turn` takes
`notes` (the seat's whole carried notes pad, written at submit; the run's env keeps
the pads, `env play --notes-pad-bytes`), submits, and returns ONLY an
acknowledgement {turn_ended, step, stepped, episode_over, next}: no observation,
and, when the step just run ended this seat's episode, `your_episode_ended` (why: the goal
met, with the title, its holder, the date and who created it; or the horizon),
so the reply ends there. Every later call in that session is refused
`turn_ended`; the runner opens the next turn in a NEW session whose first
message carries the observation and the pad. Notes over the pad's bytes are
not written and the turn still ends: the pad stays as it was and the next turn's observation says so
(`notes_pad.unwritten`); a notes_edit that names no line of the pad likewise. The default (`observation`) is unchanged and its
tool listing byte-identical.

The seat-surface switches. `--reply-envelope lean` sends a game tool's reply to the session
without the envelope fields no recorded call ever drew on (`lean_reply`: the tool-name echoes, the actor and game
blocks, bridge_health when "ok", and null or empty fields); the trajectory keeps the reply whole. `--notes-edit`
(ack mode) lists end_turn's `notes_edit`: a patch to the pad by line number (`apply_notes_edit`) in place of the
whole pad. A `notes_edit` is ACCEPTED in ack mode whether or not it is listed (additive: a call without it is
unchanged); listing it is the switch. Both off (the defaults): listing and replies byte for byte as before.

On a WAKE task (the scenario declares [wake], wake.py) `end_turn` also takes
`wake` {max_days, when}: the step then runs until one of the seat's conditions
holds (or max_days); its schema lists the property only on such a task, so a
fixed task's tool listing is unchanged. On a wake task with standing orders on
(the default; `wake.orders = false` turns them off, orders.py) it also takes
`orders`: lever calls the env sends during the step when their conditions hold.

The env server must collect per-seat submissions (`EnvServer(turn_timeout_s=...)`);
the episode is reset by whoever runs the episode (episode.py's runner with this
seat in `external`, or a driver of its own). A Claude Code session reports no
token usage through this route, so the step's `usage` for the seat is absent.

Nothing here hides an error: a refusal from the env server comes back as an
error result carrying its code.
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Optional

from .errors import EnvRefusal
from .server import EnvClient

SHIM_TOOLS = ("seat_observation", "end_turn", "await_step")
R_TOOL_NAME_CLASH = "tool_name_clash"       # a scenario tool named like one of the shim's own
R_NOTHING_PENDING = "nothing_pending"       # await_step before any end_turn
R_TURN_ENDED = "turn_ended"                 # ack mode: a call after this session's end_turn
R_TOOL_NOT_LISTED = "tool_not_listed"       # ack mode: await_step (not listed there)
_OWN_SPECS = [
    {"name": "seat_observation", "description": "Your seat's current observation (date, metrics, messages, the "
                                                "step now open, the results of your calls this turn).",
     "inputSchema": {"type": "object", "additionalProperties": False, "properties": {}}},
    {"name": "end_turn", "description": "End your turn for this step and wait for the step to run. Returns "
                                        "your next observation and this step's reward, or step_pending if "
                                        "other seats are still playing past the wait (then call await_step).",
     "inputSchema": {"type": "object", "additionalProperties": False,
                     "properties": {"summary": {"type": "string",
                                                "description": "one line on what you did and why"}}}},
    {"name": "await_step", "description": "Wait again for the step you ended to run.",
     "inputSchema": {"type": "object", "additionalProperties": False, "properties": {}}},
]
#: end_turn's `wake` property, listed only on a wake task (wake.py states the vocabulary).
WAKE_PROPERTY = {
    "type": "object", "additionalProperties": False,
    "description": "Wake me early: the step runs until the first check where one of these holds, or max_days. "
                   "Without it the step runs the task's default days. Your observation's `wake` lists the "
                   "conditions this world can read.",
    "properties": {
        "max_days": {"type": "integer", "minimum": 1, "description": "the most game-days this step may run"},
        "when": {"type": "array", "items": {"type": "object", "oneOf": [
            {"required": ["event"], "additionalProperties": False,
             "properties": {"event": {"type": "string"}}},
            {"required": ["metric", "op", "value"], "additionalProperties": False,
             "properties": {"metric": {"type": "string"}, "op": {"enum": [">=", "<="]},
                            "value": {"type": "number"}}}]}}}}
WAKE_NOTE = (" On this task you may pass `wake` {max_days, when}: the step then runs until one of your "
             "conditions holds (or max_days), so you are woken when something happens instead of after a fixed "
             "number of days.")
#: end_turn's `orders` property (orders.py), listed only on a wake task with standing orders on.
ORDERS_PROPERTY = {
    "type": "array", "maxItems": 4,
    "description": "Standing orders: when every `when` holds (and no `unless`) at a check, the env sends `do` once, "
                   "verbatim, as if you called it; the clock keeps running. when/unless take wake conditions. An "
                   "order stands until it fires, you cancel it ({id, cancel: true}; the same id replaces it), your "
                   "ruler changes, or `steps` steps pass (default 10, at most 50); at most 4 stand. A refused one "
                   "wakes you. A surrender (make_peace) or we_yield (end_war) needs confirm set to that word. Your "
                   "observation's `orders` says what each did, `orders_standing` what still stands.",
    "items": {"type": "object", "required": ["id"], "additionalProperties": False,
              "properties": {"id": {"type": "string"}, "when": {"type": "array"}, "unless": {"type": "array"},
                             "do": {"type": "object", "required": ["tool"], "additionalProperties": False,
                                    "properties": {"tool": {"enum": ["declare_war", "do"]},
                                                   "args": {"type": "object"}}},
                             "on_refusal": {"enum": ["wake", "continue"]}, "confirm": {"type": "string"},
                             "steps": {"type": "integer", "minimum": 1, "maximum": 50},
                             "cancel": {"type": "boolean"}}}}


#: end_turn in ack mode: its description and the `notes` property (listed only in that mode).
ACK_DESCRIPTION = ("End your turn for this step. Pass `notes`: your whole notes pad, rewritten (it replaces the "
                   "last one; your next turn opens in a NEW conversation that holds only the task, the "
                   "observation and these notes). Returns only an acknowledgement; this conversation then ends.")
NOTES_PROPERTY = {"type": "string", "description": "your whole notes pad for your next turn (it replaces the "
                                                   "previous one; over the pad's byte bound it is not written, the "
                                                   "pad stays, your turn still ends); omit it to keep the pad"}
ACK_NEXT = "your turn is over; this conversation ends here. Your next turn opens in a new one."
#: end_turn's `notes_edit` (listed with --notes-edit only; accepted in ack mode always)
NOTES_EDIT_PROPERTY = {
    "type": "array",
    "description": "in place of notes: edits to your pad by the line numbers shown in it this turn (the n| "
                   "prefixes are not part of the text): {op: replace|delete, line, text} or {op: append, text}",
    "items": {"type": "object", "required": ["op"], "additionalProperties": False,
              "properties": {"op": {"enum": ["append", "replace", "delete"]}, "line": {"type": "integer"},
                             "text": {"type": "string"}}}}
NOTES_EDIT_NOTE = " Or pass `notes_edit` to change only some lines."
R_NOTES_EDIT = "notes_edit_refused"          # a patch that names no line of the pad, or both notes and notes_edit


def apply_notes_edit(text: str, edits: Any) -> str:
    """The pad `text` after `edits`: each {op: replace|delete, line} names a line of `text` as it is now (1-based,
    the numbers the turn prompt showed); {op: append, text} adds lines at the end. Every line number refers to the
    pad before any edit (edits are a set, not a sequence); a line named twice, a number out of range, an unknown
    op or a missing text is refused (ValueError), nothing applied."""
    if not isinstance(edits, list) or not edits:
        raise ValueError("notes_edit is a non-empty list of edits")
    lines = text.split("\n") if text else []
    out: dict[int, Optional[str]] = {}
    tail: list[str] = []
    for e in edits:
        if not isinstance(e, dict):
            raise ValueError(f"an edit is an object, got {type(e).__name__}")
        op, t = e.get("op"), e.get("text")
        if op == "append":
            if not isinstance(t, str):
                raise ValueError("append needs text")
            tail.extend(t.split("\n"))
            continue
        if op not in ("replace", "delete"):
            raise ValueError(f"op {op!r} is not append, replace or delete")
        n = e.get("line")
        if not isinstance(n, int) or isinstance(n, bool) or not 1 <= n <= len(lines):
            raise ValueError(f"line {n!r} is not a line of the pad (1..{len(lines)})")
        if n in out:
            raise ValueError(f"line {n} is edited twice")
        if op == "replace":
            if not isinstance(t, str):
                raise ValueError("replace needs text")
            out[n] = t
        else:
            out[n] = None
    new = []
    for i, ln in enumerate(lines, 1):
        v = out.get(i, ln)
        if v is not None:
            new.extend(v.split("\n") if i in out else [v])
    return "\n".join(new + tail)


#: The lean envelope: the fields a lean reply leaves out (outer: the env's ToolResult; inner: the toolkit's)
LEAN_OUTER_DROP = ("tool_name",)
LEAN_INNER_DROP = ("tool", "actor", "game")
_KEEP = ("ok", "result")


def lean_reply(payload: Any) -> Any:
    """A game tool's reply as a lean envelope: the outer {tool_name, ok, result, error} without tool_name and a null
    error; the toolkit's envelope inside it without tool, actor, game, bridge_health when "ok", and every field that
    is null or empty (blocked null, warnings [], errors [], delta []). ok, verdict, applied, a set blocked, non-empty
    warnings and errors, pending and the result itself stay. Anything else is returned as it is."""
    if not isinstance(payload, dict):
        return payload

    def thin(d: dict, drop: tuple) -> dict:
        return {k: v for k, v in d.items()
                if k in _KEEP or (k not in drop and v not in (None, [], {}, "")
                                  and not (k == "bridge_health" and v == "ok"))}
    out = thin(payload, LEAN_OUTER_DROP)
    inner = out.get("result")
    if isinstance(inner, dict) and "ok" in inner and "result" in inner:
        out["result"] = thin(inner, LEAN_INNER_DROP)
    return out


def own_specs(wake_task: bool, ack: bool = False, orders: bool = False, notes_edit: bool = False) -> list[dict]:
    """The shim's own tools; end_turn gains `wake` on a wake task only, `orders` on a wake
    task with standing orders on only, `notes` (with the ack description) in ack mode only, and
    `notes_edit` in ack mode with --notes-edit only."""
    out = [json.loads(json.dumps(t)) for t in _OWN_SPECS]
    et = next(t for t in out if t["name"] == "end_turn")
    if ack:
        et["description"] = ACK_DESCRIPTION + (NOTES_EDIT_NOTE if notes_edit else "")
        et["inputSchema"]["properties"]["notes"] = dict(NOTES_PROPERTY)
        if notes_edit:
            et["inputSchema"]["properties"]["notes_edit"] = json.loads(json.dumps(NOTES_EDIT_PROPERTY))
        out = [t for t in out if t["name"] != "await_step"]       # nothing to wait for: the reply ends
    if wake_task:
        et["description"] += WAKE_NOTE
        et["inputSchema"]["properties"]["wake"] = json.loads(json.dumps(WAKE_PROPERTY))
        if orders:
            et["inputSchema"]["properties"]["orders"] = json.loads(json.dumps(ORDERS_PROPERTY))
    return out


class SeatShim:
    """The shim's logic, transport-free (the MCP layer below only adapts it)."""

    def __init__(self, client: EnvClient, seat: str, step_wait_s: float, ack: bool = False, lean: bool = False,
                 notes_edit: bool = False):
        self.client, self.seat, self.step_wait_s, self.ack = client, seat, step_wait_s, ack
        self.lean, self.notes_edit = lean, notes_edit
        self.pending: Optional[int] = None
        self.ended: Optional[dict] = None          # ack mode: the turn this session ended (no call after it)

    def list_tools(self) -> list[dict]:
        specs = self.client.tools(self.seat)
        clash = [t["name"] for t in specs if t["name"] in SHIM_TOOLS]
        if clash:
            raise EnvRefusal(R_TOOL_NAME_CLASH, f"the scenario's tools {clash} clash with the shim's own", "shim")
        wake = (self.client.spec().get("scenario") or {}).get("wake")
        return specs + own_specs(wake is not None, self.ack, orders=isinstance(wake, dict) and wake.get("orders", True)
                                 is not False, notes_edit=self.ack and self.notes_edit)

    def call(self, name: str, arguments: dict) -> tuple[Any, bool]:
        """(payload, is_error)."""
        try:
            if self.ended is not None:
                return {"ok": False, "code": R_TURN_ENDED, "detail": f"this session ended its turn (step "
                        f"{self.ended['step']}); {ACK_NEXT}", "leg": "shim"}, True
            if self.ack and name == "end_turn":
                return self._end_ack(arguments)
            if self.ack and name == "await_step":
                return {"ok": False, "code": R_TOOL_NOT_LISTED, "detail": "await_step is not listed in ack mode",
                        "leg": "shim"}, True
            if name == "seat_observation":
                # the turn's calls so far, one line each: the session already holds each whole reply
                return compact_step(self.client.observation(self.seat)), False
            if name == "end_turn":
                summary = arguments.get("summary", "")
                wake, orders = arguments.get("wake"), arguments.get("orders")
                sub = self.client.submit(self.seat, [], turn={"status": "end_turn", "by": "mcp_shim",
                                                              "detail": str(summary)},
                                         **({"wake": wake} if wake is not None else {}),
                                         **({"orders": orders} if orders is not None else {}))
                self.pending = sub["step"]
                return self._wait()
            if name == "await_step":
                if self.pending is None:
                    return {"code": R_NOTHING_PENDING, "detail": "no turn has been ended"}, True
                return self._wait()
            out = self.client.call(self.seat, name, arguments)
            return (lean_reply(out) if self.lean else out), False
        except EnvRefusal as e:
            return {"ok": False, "code": e.code, "detail": e.detail, "leg": e.leg}, True

    def _end_ack(self, arguments: dict) -> tuple[Any, bool]:
        wake, notes, orders = arguments.get("wake"), arguments.get("notes"), arguments.get("orders")
        edits = arguments.get("notes_edit")
        unwritten: Optional[dict] = None
        if edits is not None:                    # notes_edit: the pad as it is now, patched
            if notes is not None:
                return {"ok": False, "code": R_NOTES_EDIT, "detail": "pass notes or notes_edit, not both",
                        "leg": "shim"}, True
            pad = ((self.client.observation(self.seat).get("observation") or {}).get("notes_pad") or {})
            try:
                notes = apply_notes_edit(str(pad.get("text") or ""), edits)
            except ValueError as e:
                # a bad patch (line 16 of a 14-line pad) costs no second end_turn: the pad stays as it was, the
                # turn ends, and the next turn's notes say so
                notes, unwritten = None, {"code": R_NOTES_EDIT, "why": f"notes_edit: {e}; nothing applied"}
        sub = self.client.submit(self.seat, [], turn={"status": "end_turn", "by": "mcp_shim", "mode": "ack",
                                                      "detail": str(arguments.get("summary", ""))},
                                 **({"wake": wake} if wake is not None else {}),
                                 **({"notes": notes, "notes_if_refused": "end_turn"} if notes is not None else {}),
                                 **({"notes_unwritten": unwritten} if unwritten is not None else {}),
                                 **({"orders": orders} if orders is not None else {}))
        over = False
        why: Optional[dict] = None
        if sub.get("stepped"):
            try:
                over = bool(self.client.state().get("open")) is False
            except EnvRefusal:
                over = False
            why = self._why_ended(sub["step"])
        self.ended = {"step": sub["step"]}
        return {"ok": True, "turn_ended": True, "step": sub["step"], "stepped": bool(sub.get("stepped")),
                "waiting_for": sub.get("waiting_for", []), "episode_over": over,
                **({"your_episode_ended": why} if why is not None else {}),
                **({"notes_bytes": len(notes.encode("utf-8"))} if isinstance(notes, str) else {}),
                **({"notes_written": False, "notes_unwritten": sub["notes_unwritten"]}
                   if sub.get("notes_unwritten") else {}),
                **({"orders_set": len(orders)} if isinstance(orders, list) else {}),
                **({"order_notes": sub["order_notes"]} if sub.get("order_notes") else {}),
                "next": ACK_NEXT}, False

    def _why_ended(self, step: int) -> Optional[dict]:
        """When the step just run ended this seat's episode, why (the env's `info.end`: goal met with the title, its
        holder, the date and who created it; or the horizon); a bare `episode_over: true` reads as a failure. None while the seat plays on; unread says so."""
        try:
            sl = self.client.await_step(step, seat=self.seat, timeout_s=0)
        except EnvRefusal as e:
            return {"unread": f"the step's result was not read: {e.code}"}
        if not isinstance(sl, dict) or not (sl.get("done") or sl.get("terminated") or sl.get("truncated")):
            return None
        info = sl.get("info") if isinstance(sl.get("info"), dict) else {}
        end = info.get("end")
        if isinstance(end, dict):
            return dict(end)
        return {"reason": info.get("termination_reason") or info.get("truncation_reason") or "unread",
                "date": info.get("game_date")}

    def _wait(self) -> tuple[Any, bool]:
        assert self.pending is not None
        try:
            out = self.client.await_step(self.pending, seat=self.seat, timeout_s=self.step_wait_s)
        except EnvRefusal as e:
            if e.code == "step_pending":
                return {"ok": False, "code": e.code, "step": self.pending, "detail": e.detail,
                        "next": "call await_step"}, True
            raise
        self.pending = None
        return compact_step(out), False


def compact_step(out: Any) -> Any:
    """The step result `end_turn` hands the session, with the observation's
    `last_results` (every call of the turn just ended, each with its whole
    reply, which the session already received when it made the call) cut to
    one line per call: phase, tool, ok, error. The trajectory keeps them whole."""
    obs = out.get("observation") if isinstance(out, dict) else None
    rows = obs.get("last_results") if isinstance(obs, dict) else None
    if not isinstance(rows, list):
        return out
    brief = []
    for r in rows:
        res = r.get("result") if isinstance(r, dict) else None
        call = r.get("call") if isinstance(r, dict) else None
        brief.append({"phase": r.get("phase") if isinstance(r, dict) else None,
                      "tool": call.get("tool_name") if isinstance(call, dict) else None,
                      "ok": res.get("ok") if isinstance(res, dict) else None,
                      "error": res.get("error") if isinstance(res, dict) else None})
    return {**out, "observation": {**obs, "last_results": brief,
                                   "last_results_note": "one line per call of the turn just ended; each call's "
                                                        "whole reply came back when it was made"}}


def build_server(shim: SeatShim):
    import mcp.types as types
    from mcp.server.lowlevel import Server

    server = Server(f"ck3bench-seat-{shim.seat}")

    @server.list_tools()
    async def _list() -> list[types.Tool]:
        import anyio
        specs = await anyio.to_thread.run_sync(shim.list_tools)
        return [types.Tool(name=t["name"], description=t.get("description", ""),
                           inputSchema=t.get("inputSchema") or {"type": "object"}) for t in specs]

    # validate_input=False: every call reaches the environment, which checks it
    # and records it; a call the MCP layer refused would leave no record
    @server.call_tool(validate_input=False)
    async def _call(name: str, arguments: dict) -> types.CallToolResult:
        import anyio
        payload, err = await anyio.to_thread.run_sync(shim.call, name, dict(arguments or {}))
        if not err and isinstance(payload, dict) and payload.get("ok") is False:
            err = True          # a tool result the env reported not ok (a refused game action)
        return types.CallToolResult(content=[types.TextContent(type="text", text=json.dumps(payload,
                                                                                            default=str))],
                                    isError=err)
    return server


async def _serve(shim: SeatShim) -> None:
    from mcp.server.stdio import stdio_server
    server = build_server(shim)
    async with stdio_server() as (r, w):
        await server.run(r, w, server.create_initialization_options())


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(prog="python -m ck3bench.env.mcp_shim")
    p.add_argument("--url", required=True, help="the env server (ck3bench env serve)")
    p.add_argument("--seat", required=True)
    p.add_argument("--step-wait-s", type=float, required=True,
                   help="how long end_turn waits for the step to run before answering step_pending")
    p.add_argument("--end-turn", choices=("observation", "ack"), default="observation",
                   help="ack: end_turn returns an acknowledgement only and the session takes no further call")
    p.add_argument("--reply-envelope", choices=("full", "lean"), default="full",
                   help="lean: game tools' replies without the envelope fields no call draws on (lean_reply)")
    p.add_argument("--notes-edit", action="store_true", help="ack mode: list end_turn's notes_edit (a pad patch)")
    a = p.parse_args(argv)
    import anyio
    anyio.run(_serve, SeatShim(EnvClient(a.url), a.seat, a.step_wait_s, ack=a.end_turn == "ack",
                               lean=a.reply_envelope == "lean", notes_edit=a.notes_edit))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
