"""`end_turn` through the shim hands the session its next observation with the
turn's call results cut to one line per call (the session got each whole reply
when it made the call); the trajectory keeps them whole."""
from __future__ import annotations

import json

from ck3bench.env import ParallelEnv
from ck3bench.env.mcp_shim import SeatShim, compact_step
from ck3bench.env.server import EnvClient, EnvServer
from ck3bench.env.trajectory import TrajectoryWriter, read_trajectory


def test_end_turn_returns_one_line_per_call_and_the_trajectory_keeps_the_replies(tmp_path):
    traj = tmp_path / "t.jsonl"
    env = ParallelEnv("fake-solo", trajectory=TrajectoryWriter(str(traj)))
    srv = EnvServer(env, turn_timeout_s=30).start()
    try:
        cl = EnvClient(srv.url, timeout=30)
        cl.reset(seed=1)
        shim = SeatShim(cl, "king", 30)
        shim.call("observe", {})
        shim.call("do", {"intent": "no_such_intent"})
        res, err = shim.call("end_turn", {"summary": "looked"})
        assert not err
        rows = res["observation"]["last_results"]
        assert rows == [{"phase": "turn", "tool": "observe", "ok": True, "error": None},
                        {"phase": "turn", "tool": "do", "ok": False, "error": rows[1]["error"]}]
        assert rows[1]["error"].startswith("intent_unknown") and "last_results_note" in res["observation"]
    finally:
        srv.stop()
        env.close()
    step = next(r for r in read_trajectory(str(traj)) if r["kind"] == "step")
    whole = step["observations"]["king"]["last_results"]
    assert whole[0]["result"]["result"]["counties"] == 12, "the trajectory keeps each call's whole reply"


def test_seat_observation_mid_turn_returns_one_line_per_call(tmp_path):
    """Read live: a seat_observation after four reads in the turn came back 22,688 bytes (each whole
    reply again); the calls' replies are the session's already."""
    env = ParallelEnv("fake-solo")
    srv = EnvServer(env, turn_timeout_s=30).start()
    try:
        cl = EnvClient(srv.url, timeout=30)
        cl.reset(seed=1)
        shim = SeatShim(cl, "king", 30)
        shim.call("observe", {})
        shim.call("observe", {})
        res, err = shim.call("seat_observation", {})
        assert not err
        rows = res["observation"]["last_results"]
        assert rows == [{"phase": "turn", "tool": "observe", "ok": True, "error": None}] * 2
        assert "result" not in json.dumps(rows)
    finally:
        srv.stop()
        env.close()


def test_compact_step_leaves_a_reply_without_results_alone():
    for out in ({"ok": False, "code": "step_pending"}, {"observation": {"seat": "king"}}, [1, 2], None):
        assert compact_step(out) == out
