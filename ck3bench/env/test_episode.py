"""The mixed-policy runner (episode.py) and the server's submit barrier, over a
real HTTP env server on the fake backend; and the MCP shim (mcp_shim.py) driven
by a real MCP client over stdio."""
from __future__ import annotations

import asyncio
import io
import json
import os
import sys
import threading
import time
from pathlib import Path

import pytest

from ck3bench.env import NoOpPolicy, ParallelEnv, ScriptedPolicy, ToolCall
from ck3bench.env.agent import END_TURN, ScriptedModel, ToolCallingAgent, calls
from ck3bench.env.episode import run_mixed_episode
from ck3bench.env.errors import EnvRefusal
from ck3bench.env.server import EnvClient, EnvServer
from ck3bench.env.trajectory import TrajectoryWriter, check_trajectory

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture
def served():
    made = []

    def make(scenario="fake-council", turn_timeout_s=None, traj=None):
        srv = EnvServer(ParallelEnv(scenario, trajectory=traj), turn_timeout_s=turn_timeout_s).start()
        made.append(srv)
        return srv, EnvClient(srv.url, timeout=60)
    yield make
    for s in made:
        s.stop()


class Sleeper:
    """Overruns its turn, then tries to act: the closed handle must stop it."""

    def __init__(self, s):
        self.s, self.late = s, []

    def act(self, obs, tools):
        time.sleep(self.s)
        self.late.append(tools.call("do", intent="raise_taxes"))
        return [ToolCall("do", {"intent": "raise_taxes"})]


class Raiser:
    def act(self, obs, tools):
        raise RuntimeError("policy bug")


def test_mixed_policies_run_concurrently_and_an_overrun_is_a_typed_timeout_never_a_hang(served):
    buf = io.StringIO()
    srv, cl = served(turn_timeout_s=30, traj=TrajectoryWriter(stream=buf))
    replies = []
    for _ in range(8):
        replies += [calls(("observe", {}), ("msg_send", {"to": "duke2", "text": "hold"}),
                          usage={"input_tokens": 5, "output_tokens": 1}), calls((END_TURN, {}))]
    king = ToolCallingAgent(ScriptedModel(replies), max_calls=5, max_wall_s=30)
    slow = Sleeper(0.6)
    t0 = time.monotonic()
    out = run_mixed_episode(cl, {"king": king, "duke1": slow, "duke2": Raiser()}, seed=9, turn_timeout_s=0.3)
    wall = time.monotonic() - t0
    assert out["end_reason"] == "horizon" and out["steps"] == 8
    assert wall < 8 * 0.3 + 6, "the turns ran concurrently and every step was bounded"
    first = out["turns"][0]["seats"]
    assert first["duke1"]["turn"]["status"] == "timeout" and first["duke1"]["turn"]["by"] == "runner"
    assert first["duke2"]["turn"]["status"] == "policy_error" and "policy bug" in first["duke2"]["turn"]["detail"]
    assert first["king"]["turn"]["status"] == "end_turn"
    time.sleep(0.5)
    assert slow.late and all((not r.ok) and r.error.startswith("turn_closed") for r in slow.late), \
        "a late thread's call never reached the server"
    srv.env.close()
    recs = [json.loads(x) for x in buf.getvalue().splitlines()]
    assert check_trajectory(recs) == []
    duke1_calls = [r for r in recs if r["kind"] == "call" and r["seat"] == "duke1"]
    assert duke1_calls == [], "nothing duke1's overrunning thread tried was executed"
    step1 = [r for r in recs if r["kind"] == "step"][0]
    assert step1["actions"]["duke1"] == [] and step1["turns"]["duke1"]["status"] == "timeout"
    assert step1["usage"]["king"]["input_tokens"] == 5
    assert step1["comms"]["delivered"][0]["from"] == "king"


def test_the_server_deadline_steps_a_seat_that_never_submits(served):
    srv, cl = served(turn_timeout_s=0.5)
    out = run_mixed_episode(cl, {"duke1": NoOpPolicy(), "duke2": NoOpPolicy()}, external=["king"], seed=1,
                            turn_timeout_s=5, await_s=10)
    assert out["end_reason"] == "horizon" and out["steps"] == 8


def test_a_deadline_with_nobody_waiting_steps_nothing_and_the_next_submission_runs_it(served):
    srv, cl = served(turn_timeout_s=0.2)
    cl.reset(seed=1)
    time.sleep(0.5)
    assert cl.state()["step"] == 0, "nobody submitted: the deadline stepped nothing"
    sub = cl.submit("king", [])
    assert sub["stepped"] is True
    res = cl.await_step(1, timeout_s=0)
    assert res["agents"] and cl.state()["step"] == 1
    step1 = cl.await_step(1, seat="duke1", timeout_s=0)
    assert step1["info"]["game_date"]


def test_submit_rules(served):
    srv, cl = served()
    cl.reset(seed=1)
    with pytest.raises(EnvRefusal) as e:
        cl.submit("king", [])
    assert e.value.code == "turn_timeout_not_declared"
    srv2, cl2 = served(turn_timeout_s=30)
    cl2.reset(seed=1)
    assert cl2.submit("king", [{"tool_name": "pass_turn", "arguments": {}}])["waiting_for"] == ["duke1", "duke2"]
    for bad, code in ((lambda: cl2.submit("king", []), "already_submitted"),
                      (lambda: cl2.call("king", "observe"), "already_submitted"),
                      (lambda: cl2.step({"king": [], "duke1": [], "duke2": []}), "submissions_pending"),
                      (lambda: cl2.submit("pope", []), "unknown_seat"),
                      (lambda: cl2.submit("duke1", "observe"), "action_malformed"),
                      (lambda: cl2.await_step(1, timeout_s=0.2), "step_pending"),
                      (lambda: cl2.await_step(1, timeout_s=10 ** 6), "bad_request")):
        with pytest.raises(EnvRefusal) as e:
            bad()
        assert e.value.code == code
    assert cl2.call("duke1", "observe")["ok"], "a seat that has not submitted still plays"
    cl2.submit("duke1", [])
    assert cl2.submit("duke2", [])["stepped"] is True
    assert cl2.observation("king")["step"] == 2


def test_tools_and_observation_routes(served):
    srv, cl = served()
    cl.reset(seed=1)
    names = [t["name"] for t in cl.tools("duke1")]
    assert names == list(srv.env.scenario.tools)
    o = cl.observation("duke1")
    assert o["step"] == 1 and o["observation"]["seat"] == "duke1" and "comms" in o["observation"]


def test_the_runner_refuses_a_seat_nobody_drives(served):
    srv, cl = served(turn_timeout_s=5)
    with pytest.raises(EnvRefusal) as e:
        run_mixed_episode(cl, {"king": NoOpPolicy()}, seed=1, turn_timeout_s=1)
    assert e.value.code == "policy_unknown"


def test_scripted_and_noop_seats_mixed_with_a_one_seat_env_over_the_server(served):
    srv, cl = served("fake-solo", turn_timeout_s=5)
    pol = ScriptedPolicy([[ToolCall("do", {"intent": "raise_taxes"})]] * 12)
    out = run_mixed_episode(cl, {"king": pol}, seed=3, turn_timeout_s=5)
    assert out["steps"] == 12 and out["returns"]["king"] > 0


# ── the MCP shim, with a real MCP client ──────────────────────────────────

def test_a_claude_code_seat_through_the_mcp_shim_plays_a_whole_episode(served):
    pytest.importorskip("mcp")
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    buf = io.StringIO()
    srv, cl = served(turn_timeout_s=20, traj=TrajectoryWriter(stream=buf))
    res: dict = {}

    def runner():
        res["out"] = run_mixed_episode(EnvClient(srv.url), {"duke1": NoOpPolicy(), "duke2": NoOpPolicy()},
                                       external=["king"], seed=5, turn_timeout_s=10)

    async def play():
        env = {**os.environ, "PYTHONPATH": str(REPO) + os.pathsep + os.environ.get("PYTHONPATH", "")}
        params = StdioServerParameters(command=sys.executable, env=env,
                                       args=["-m", "ck3bench.env.mcp_shim", "--url", srv.url, "--seat", "king",
                                             "--step-wait-s", "30"])
        seen = []
        async with stdio_client(params) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                tools = {t.name: t for t in (await s.list_tools()).tools}
                assert list(tools) == list(srv.env.scenario.tools) + ["seat_observation", "end_turn", "await_step"]
                assert tools["do"].inputSchema["properties"]["intent"]["enum"]
                th = threading.Thread(target=runner)
                th.start()
                for _ in range(100):
                    if cl.state()["open"]:
                        break
                    await asyncio.sleep(0.05)
                while True:
                    o = json.loads((await s.call_tool("seat_observation", {})).content[0].text)
                    seen.append(o["step"])
                    r1 = await s.call_tool("do", {"intent": "raise_taxes"})
                    r2 = await s.call_tool("msg_send", {"to": "duke1", "text": f"step {o['step']}"})
                    bad = await s.call_tool("do", {"intent": "abdicate"})
                    assert not r1.isError and not r2.isError and bad.isError
                    e = await s.call_tool("end_turn", {"summary": "taxed"})
                    d = json.loads(e.content[0].text)
                    assert not e.isError and d["step"] == o["step"]
                    if d["episode_over"]:
                        break
                th.join(30)
        return seen
    seen = asyncio.run(play())
    assert seen == list(range(1, 9))
    assert res["out"]["end_reason"] == "horizon" and res["out"]["steps"] == 8
    srv.env.close()
    recs = [json.loads(x) for x in buf.getvalue().splitlines()]
    assert check_trajectory(recs) == []
    king_calls = [r["call"]["tool_name"] for r in recs if r["kind"] == "call" and r["seat"] == "king"]
    assert king_calls == ["do", "msg_send", "do"] * 8, "every shim call is an env call, recorded"
    steps = [r for r in recs if r["kind"] == "step"]
    assert all(s["turns"]["king"] == {"status": "end_turn", "by": "mcp_shim", "detail": "taxed"} for s in steps)
    assert all("king" not in s["usage"] for s in steps), "a Claude Code seat reports no usage on this route"


def test_a_policy_returning_a_malformed_action_is_stepped_with_nothing_typed(served):
    class Bad:
        def act(self, obs, tools):
            return "raise taxes"
    srv, cl = served("fake-solo", turn_timeout_s=5)
    out = run_mixed_episode(cl, {"king": Bad()}, seed=3, turn_timeout_s=5)
    assert out["steps"] == 12
    assert out["turns"][0]["seats"]["king"]["turn"]["status"] == "action_malformed"
