"""An episode runner for N seats with MIXED policies, over the env server.

    srv = EnvServer(ParallelEnv("fake-council"), turn_timeout_s=60).start()
    out = run_mixed_episode(EnvClient(srv.url), {"king": ToolCallingAgent(model, max_calls=20, max_wall_s=50),
                                                 "duke1": NoOpPolicy(), "duke2": scripted},
                            seed=7, turn_timeout_s=50)

Each step, every live seat this runner drives takes its turn CONCURRENTLY (one
thread per seat): the policy reads its observation, makes in-turn calls over
/call, and returns its action, which the runner /submits with the usage and
turn record the policy reports. A seat whose policy has not returned within
`turn_timeout_s` has its tool handle closed (any later call answers
`turn_closed` without reaching the server) and is submitted with `[]` and the
turn record {"status": "timeout", "by": "runner", "timeout_s", "elapsed_s"};
a policy that raises is submitted with `[]` and {"status": "policy_error"}, one
that returns something that is not a list of tool calls with `[]` and
{"status": "action_malformed"}.
The overrunning thread itself cannot be killed (Python); it is a daemon and
its result is discarded.

Seats named in `external` are driven by someone else (a Claude Code session
through mcp_shim.py, another process) and submit for themselves; the server's
own deadline (`EnvServer(turn_timeout_s=...)`, which should be at least the
runner's) covers a seat that never does. The runner then awaits the step's
result, bounded (`await_s`); a step that has not run in that bound is a typed
refusal (`step_pending`), never a hang.
"""
from __future__ import annotations

import math
import threading
import time
from typing import Any, Mapping, Optional, Sequence

from .errors import R_ALREADY_SUBMITTED, R_POLICY_UNKNOWN, EnvRefusal
from .policy import ToolHandle
from .server import EnvClient
from .types import ToolCall, ToolResult, parse_action

#: How a runner seat's turn ended when its policy did not end it (turn records' `status`).
T_TIMEOUT = "timeout"                 # the policy overran turn_timeout_s (the server uses it too)
T_POLICY_ERROR = "policy_error"       # the policy raised
T_ACTION_MALFORMED = "action_malformed"   # the policy returned something that is not a list of tool calls

#: How long past the turn timeout the runner waits for the server to run a step.
AWAIT_MARGIN_S = 30.0


def _remote_call(client: EnvClient):
    def call(seat: str, c: ToolCall) -> ToolResult:
        try:
            d = client.call(seat, c.tool_name, dict(c.arguments))
        except EnvRefusal as e:
            return ToolResult(c.tool_name, False, error=f"{e.code}: {e.detail}")
        return ToolResult(d.get("tool_name", c.tool_name), bool(d.get("ok")), d.get("result"), d.get("error"))
    return call


class _Turn(threading.Thread):
    def __init__(self, policy: Any, obs: Mapping[str, Any], handle: ToolHandle):
        super().__init__(daemon=True)
        self.policy, self.obs, self.handle = policy, obs, handle
        self.action: Any = None
        self.error: Optional[BaseException] = None
        self.usage: Optional[dict] = None
        self.turn: Optional[dict] = None

    def run(self) -> None:
        try:
            self.action = self.policy.act(self.obs, self.handle)
            self.usage = getattr(self.policy, "report_usage", lambda: None)()
            self.turn = getattr(self.policy, "report_turn", lambda: None)()
        except BaseException as e:  # noqa: BLE001 - a policy's failure is a typed turn record
            self.error = e


def run_mixed_episode(client: EnvClient, policies: Mapping[str, Any], *, seed: Optional[int],
                      turn_timeout_s: float, external: Sequence[str] = (), options: Optional[dict] = None,
                      await_s: Optional[float] = None) -> dict[str, Any]:
    """Reset the server's environment and drive it to the end. Returns
    {returns, steps, end_reason, seed, turns}: `turns` holds, per step, each
    runner-driven seat's turn record and its submission outcome."""
    if not turn_timeout_s > 0:
        raise ValueError("turn_timeout_s must be > 0")
    spec = client.spec()
    seats = list(spec["possible_agents"])
    unknown = [s for s in list(policies) + list(external) if s not in seats]
    both = sorted(set(policies) & set(external))
    missing = [s for s in seats if s not in policies and s not in external]
    if unknown or both or missing:
        raise EnvRefusal(R_POLICY_UNKNOWN, f"seats {seats}: unknown {unknown}, both local and external {both}, "
                                           f"driven by nobody {missing}", "runner")
    wait = await_s if await_s is not None else turn_timeout_s + AWAIT_MARGIN_S
    obs, infos = client.reset(seed=seed, options=options)
    seed_used = next(iter(infos.values()))["seed"] if infos else seed
    agents = list(obs)
    returns: dict[str, Optional[float]] = {s: 0.0 for s in agents}
    turns_log: list[dict] = []
    tools_of = {s: tuple(spec["scenario"]["tools"]) for s in seats}
    step = 0
    while agents:
        step += 1
        local = [s for s in agents if s in policies]
        threads: dict[str, tuple[_Turn, ToolHandle]] = {}
        for s in local:
            h = ToolHandle(_remote_call(client), s, tools_of[s], specs=lambda s=s: client.tools(s))
            t = _Turn(policies[s], obs[s], h)
            threads[s] = (t, h)
            t.start()
        t0 = time.monotonic()
        rec: dict[str, Any] = {}
        for s, (t, h) in threads.items():
            t.join(max(0.0, turn_timeout_s - (time.monotonic() - t0)))
            if t.is_alive():
                h.close()
                action, usage = [], None
                turn = {"status": T_TIMEOUT, "by": "runner", "timeout_s": turn_timeout_s,
                        "elapsed_s": round(time.monotonic() - t0, 3)}
            elif t.error is not None:
                h.close()
                action, usage = [], None
                turn = {"status": T_POLICY_ERROR, "detail": f"{type(t.error).__name__}: {t.error}"}
            else:
                h.close()
                action, usage, turn = t.action, t.usage, t.turn
                try:
                    action = [c.to_dict() for c in parse_action(action)]
                except EnvRefusal as e:
                    action, turn = [], {"status": T_ACTION_MALFORMED, "detail": e.detail, "policy_turn": turn}
            try:
                sub = client.submit(s, action, usage=usage, turn=turn)
                rec[s] = {"turn": turn, "submitted": True, "stepped": sub.get("stepped", False)}
            except EnvRefusal as e:
                # the server's deadline ran the step first: the seat was stepped with []
                rec[s] = {"turn": turn, "submitted": False, "refusal": e.code}
                if e.code != R_ALREADY_SUBMITTED and e.code != "unknown_seat":
                    raise
        turns_log.append({"step": step, "seats": rec})
        summary = client.await_step(step, timeout_s=wait)
        if "error" in summary:
            raise EnvRefusal(summary["error"].get("code", "step_failed"), summary["error"].get("detail", ""),
                             "runner")
        for s, r in summary["rewards"].items():
            cur = returns.get(s)
            returns[s] = None if (cur is None or r is None or (isinstance(r, float) and math.isnan(r))) \
                else cur + r
        new_agents = list(summary["agents"])
        for s in new_agents:
            if s in policies:
                obs[s] = client.await_step(step, seat=s, timeout_s=wait)["observation"]
        agents = new_agents
    state = client.state()
    return {"returns": returns, "steps": step, "end_reason": state.get("end_reason"), "seed": seed_used,
            "turns": turns_log}
