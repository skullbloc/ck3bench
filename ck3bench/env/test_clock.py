"""The clock belongs to the step: a seat's call that would pause, unpause, set
the speed or wait (the toolkit's `session` clock actions) is refused by the
environment with a typed code, on every route a call can take (an in-turn
`call`, a step action, the env server's /call and /step, the MCP shim, the
tool-calling agent, the mixed-policy runner), on the fake backend and on the
real one (its seat transport a recorder). The refused call never reaches the
backend; the step's own advance is the only thing that moves the clock."""
from __future__ import annotations

import io
import json

import pytest

from ck3bench.env import NoOpPolicy, ParallelEnv, ScriptedPolicy, ToolCall, run_episode
from ck3bench.env.agent import ScriptedModel, ToolCallingAgent, calls
from ck3bench.env.episode import run_mixed_episode
from ck3bench.env.errors import R_CLOCK_VERB, R_SESSION_ACTION_UNVETTED
from ck3bench.env.mcp_shim import SeatShim
from ck3bench.env.scenario import BUILTIN_DICTS, scenario_from_dict
from ck3bench.env.server import EnvClient, EnvServer
from ck3bench.env.trajectory import TrajectoryWriter
from ck3bench.env.types import SESSION_CLOCK_ACTIONS, clock_refusal

CLOCK_CALLS = [ToolCall("session", {"action": a, **({"speed": 3} if a == "speed" else {}),
                                    **({"game_days": 30} if a == "wait" else {})})
               for a in SESSION_CLOCK_ACTIONS]


def _fake(name="fake-solo"):
    d = dict(BUILTIN_DICTS[name])
    d["tools"] = list(d["tools"]) + ["session"]
    return scenario_from_dict(d)


def _refused(res, code=R_CLOCK_VERB):
    d = res if isinstance(res, dict) else res.to_dict()
    return d["ok"] is False and d["error"].startswith(code + ":") and d["result"]["code"] == code


def _backend_saw_session(env) -> list:
    b = env.backend
    if b.name == "fake":
        return [c for c in b.calls if c[1] == "session"]
    return [c for tr in b.transports.values() for c in tr.calls if c[0] == "session"]


def _ck3_env(tmp_path):
    from ck3bench.env.test_ck3_backend import _backend, _scn
    b, *_ = _backend(tmp_path)
    return ParallelEnv(_scn(), backend=b)


@pytest.fixture(params=["fake", "ck3"])
def env(request, tmp_path):
    e = ParallelEnv(_fake()) if request.param == "fake" else _ck3_env(tmp_path)
    e.reset(seed=1)
    yield e
    e.close()


@pytest.mark.parametrize("c", CLOCK_CALLS, ids=SESSION_CLOCK_ACTIONS)
def test_an_in_turn_clock_call_is_refused_and_never_reaches_the_backend(env, c):
    date = env.state()["game_date"]
    res = env.call("king", c)
    assert _refused(res), res
    assert _backend_saw_session(env) == []
    assert env.state()["game_date"] == date


@pytest.mark.parametrize("c", CLOCK_CALLS, ids=SESSION_CLOCK_ACTIONS)
def test_a_clock_call_as_a_step_action_is_refused_and_the_step_runs_one_cycle(env, c):
    date = env.state()["game_date"]
    obs, *_ = env.step({"king": [c]})
    assert _refused(obs["king"]["last_results"][0]["result"])
    assert _backend_saw_session(env) == []
    from ck3bench.env import calendar
    assert env.state()["game_date"] == calendar.add_days(date, env.scenario.cycle_days)


def test_a_clock_free_session_action_reaches_the_backend(env):
    """CONTROL: the guard refuses the clock actions, not the tool."""
    res = env.call("king", ToolCall("session", {"action": "whoami"}))
    assert not res.to_dict()["error"] or not res.to_dict()["error"].startswith(R_CLOCK_VERB)
    assert len(_backend_saw_session(env)) == 1


@pytest.mark.parametrize("args", [{}, {"action": "rewind"}, {"action": ["wait"]}])
def test_an_unclassified_session_action_is_refused_not_passed_through(env, args):
    res = env.call("king", ToolCall("session", args))
    assert _refused(res, R_SESSION_ACTION_UNVETTED), res
    assert _backend_saw_session(env) == []


def test_the_predicate_names_only_the_clock_actions():
    assert [clock_refusal(c)[0] for c in CLOCK_CALLS] == [R_CLOCK_VERB] * 4
    assert clock_refusal(ToolCall("session", {"action": "health"})) is None
    assert clock_refusal(ToolCall("observe", {"action": "wait"})) is None


def test_the_offered_session_schema_says_the_clock_is_the_steps(tmp_path):
    from ck3bench.env.test_ck3_backend import FakeTransport, _backend, _scn

    class Listing(FakeTransport):
        def list_tool_specs(self):
            return [{"name": t, "description": f"{t} tool", "inputSchema": {"type": "object"}}
                    for t in BUILTIN_DICTS["france1066-treasury-2y"]["tools"]]
    tr: dict = {}
    b, *_ = _backend(tmp_path, transports=tr)
    b.transport_factory = lambda s, cid, e: tr.setdefault(s, Listing())
    env = ParallelEnv(_scn(), backend=b)
    env.reset(seed=1)
    spec = next(t for t in env.tool_specs("king") if t["name"] == "session")
    assert spec["description"].startswith("session tool") and R_CLOCK_VERB in spec["description"]
    env.close()


# ── the routes that are not ParallelEnv.call ──────────────────────────────

@pytest.fixture
def served():
    made = []

    def make(scenario, turn_timeout_s=None, traj=None):
        srv = EnvServer(ParallelEnv(scenario, trajectory=traj), turn_timeout_s=turn_timeout_s).start()
        made.append(srv)
        return srv, EnvClient(srv.url, timeout=60)
    yield make
    for s in made:
        s.stop()


def test_the_server_refuses_a_clock_call_on_call_and_on_step(served):
    srv, cl = served(_fake())
    cl.reset(seed=2)
    assert _refused(cl.call("king", "session", {"action": "wait", "game_days": 30}))
    obs, *_ = cl.step({"king": [{"tool_name": "session", "arguments": {"action": "unpause"}}]})
    assert _refused(obs["king"]["last_results"][0]["result"])
    assert _backend_saw_session(srv.env) == []


def test_the_mcp_shim_returns_the_refusal_as_an_error_result(served):
    srv, cl = served(_fake(), turn_timeout_s=5)
    cl.reset(seed=2)
    payload, is_error = SeatShim(cl, "king", 5).call("session", {"action": "speed", "speed": 3})
    assert _refused(payload) and not is_error, "the MCP layer marks ok:false results isError (build_server)"
    assert _backend_saw_session(srv.env) == []


def test_the_agent_loop_sees_the_refusal_as_a_tool_error_and_plays_on():
    buf = io.StringIO()
    env = ParallelEnv(_fake(), trajectory=TrajectoryWriter(stream=buf))
    replies = [calls(("session", {"action": "wait", "game_days": 365}), ("do", {"intent": "raise_taxes"}))]
    agent = ToolCallingAgent(ScriptedModel(replies), max_calls=5, max_wall_s=60)
    res = run_episode(env, {"king": agent}, seed=3)
    env.close()
    tool_msgs = [m for m in agent.transcripts[0] if m["role"] == "tool"]
    assert tool_msgs[0]["is_error"] and R_CLOCK_VERB in tool_msgs[0]["content"]
    assert not tool_msgs[1]["is_error"]
    assert res["steps"] == env.scenario.n_steps and _backend_saw_session(env) == []


def test_the_mixed_policy_runner_records_the_refusal(served):
    buf = io.StringIO()
    srv, cl = served(_fake("fake-council"), turn_timeout_s=10, traj=TrajectoryWriter(stream=buf))
    pol = ScriptedPolicy([[ToolCall("session", {"action": "pause"})]],
                         turn_calls=[ToolCall("session", {"action": "wait", "seconds": 60})])
    out = run_mixed_episode(cl, {"king": pol, "duke1": NoOpPolicy(), "duke2": NoOpPolicy()}, seed=4,
                            turn_timeout_s=10)
    assert out["end_reason"] == "horizon"
    recs = [json.loads(x) for x in buf.getvalue().splitlines()]
    sess = [r for r in recs if r["kind"] == "call" and r["call"]["tool_name"] == "session"]
    assert [r["phase"] for r in sess][:2] == ["turn", "action"]
    assert all(_refused(r["result"]) for r in sess) and len(sess) == 8 + 1
    assert _backend_saw_session(srv.env) == []
