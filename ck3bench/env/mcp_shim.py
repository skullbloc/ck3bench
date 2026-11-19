"""The Claude Code route: one seat's tools from a running env server, as a stdio MCP server.

    python -m ck3bench.env.mcp_shim --url http://127.0.0.1:8770 --seat king --step-wait-s 900

Point a Claude Code session's MCP config at this command and the session plays
that seat. It lists:

  * the seat's tools, with the env's own input schemas (GET /tools): every call
    is a /call, an in-turn call recorded in the trajectory like any other;
  * `seat_observation`: the seat's current observation and the step now open;
  * `end_turn(summary)`: submits the seat's turn for this step (/submit, action
    `[]`, turn record {"status": "end_turn", "by": "mcp_shim"}) and waits, up
    to --step-wait-s, for the step to run (every seat submitted, or the
    server's deadline). It returns the seat's next observation, reward and
    whether it is done, so a session can play step after step;
  * `await_step`: waits again when `end_turn` came back `step_pending`.

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


class SeatShim:
    """The shim's logic, transport-free (the MCP layer below only adapts it)."""

    def __init__(self, client: EnvClient, seat: str, step_wait_s: float):
        self.client, self.seat, self.step_wait_s = client, seat, step_wait_s
        self.pending: Optional[int] = None

    def list_tools(self) -> list[dict]:
        specs = self.client.tools(self.seat)
        clash = [t["name"] for t in specs if t["name"] in SHIM_TOOLS]
        if clash:
            raise EnvRefusal(R_TOOL_NAME_CLASH, f"the scenario's tools {clash} clash with the shim's own", "shim")
        return specs + [dict(t) for t in _OWN_SPECS]

    def call(self, name: str, arguments: dict) -> tuple[Any, bool]:
        """(payload, is_error)."""
        try:
            if name == "seat_observation":
                return self.client.observation(self.seat), False
            if name == "end_turn":
                summary = arguments.get("summary", "")
                sub = self.client.submit(self.seat, [], turn={"status": "end_turn", "by": "mcp_shim",
                                                              "detail": str(summary)})
                self.pending = sub["step"]
                return self._wait()
            if name == "await_step":
                if self.pending is None:
                    return {"code": R_NOTHING_PENDING, "detail": "no turn has been ended"}, True
                return self._wait()
            return self.client.call(self.seat, name, arguments), False
        except EnvRefusal as e:
            return {"ok": False, "code": e.code, "detail": e.detail, "leg": e.leg}, True

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
        return out, False


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
    a = p.parse_args(argv)
    import anyio
    anyio.run(_serve, SeatShim(EnvClient(a.url), a.seat, a.step_wait_s))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
