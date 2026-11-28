"""The env side of one new seat session per turn: carried notes pads (a run option, env.py
`notes_pad_bytes`, kept by comms.py's private-pad code) and the MCP shim's `--end-turn ack`.

The continuous mode stays the default and byte-identical: a run without a pad has no `notes_pad` in any
observation and no `notes_pads` in any record, and the shim's default listing is the one without the
`--end-turn ack` (test_wake.py checks it byte for byte)."""
from __future__ import annotations

import io
import json

import pytest

from ck3bench.env import ParallelEnv, ToolCall
from ck3bench.env.errors import EnvRefusal
from ck3bench.env.mcp_shim import _OWN_SPECS, ACK_NEXT, SeatShim, own_specs
from ck3bench.env.server import EnvClient, EnvServer
from ck3bench.env.trajectory import TrajectoryWriter, check_trajectory, summary


def _recs(buf):
    return [json.loads(x) for x in buf.getvalue().splitlines()]


def test_a_carried_pad_is_shown_next_turn_and_recorded_at_every_step():
    buf = io.StringIO()
    env = ParallelEnv("fake-solo", trajectory=TrajectoryWriter(stream=buf), notes_pad_bytes=64)
    obs, _ = env.reset(seed=1)
    assert obs["king"]["notes_pad"] == {"text": "", "version": 0, "bytes": 0, "max_bytes": 64}
    env.write_notes("king", "claim Meath; gold 120 first")
    obs, *_ = env.step({"king": []})
    assert obs["king"]["notes_pad"]["text"] == "claim Meath; gold 120 first"
    assert obs["king"]["notes_pad"]["version"] == 1
    env.write_notes("king", "rewritten")             # a replace, not an append
    env.step({"king": []})
    env.close()
    recs = _recs(buf)
    assert recs[0]["notes_pad_bytes"] == 64 and check_trajectory(recs) == []
    steps = [r for r in recs if r["kind"] == "step"]
    assert [s["notes_pads"]["king"]["text"] for s in steps] == ["claim Meath; gold 120 first", "rewritten"]
    writes = [r for r in recs if r["kind"] == "call" and r["phase"] == "notes"]
    assert len(writes) == 2 and all(w["result"]["ok"] for w in writes)


def test_notes_over_the_bound_are_refused_not_cut_and_recorded():
    buf = io.StringIO()
    env = ParallelEnv("fake-solo", trajectory=TrajectoryWriter(stream=buf), notes_pad_bytes=8)
    env.reset(seed=1)
    env.write_notes("king", "short")
    with pytest.raises(EnvRefusal) as e:
        env.write_notes("king", "x" * 9)
    assert e.value.code == "notes_too_long"
    assert env.observe("king")["notes_pad"]["text"] == "short"
    with pytest.raises(EnvRefusal) as e:
        env.write_notes("king", 5)
    assert e.value.code == "notes_malformed"
    env.close()
    w = [r for r in _recs(buf) if r["kind"] == "call" and r["phase"] == "notes"]
    assert [x["result"]["ok"] for x in w] == [True, False]


def test_a_run_without_a_pad_keeps_none_and_refuses_notes():
    env = ParallelEnv("fake-solo")
    obs, _ = env.reset(seed=1)
    assert "notes_pad" not in obs["king"]
    with pytest.raises(EnvRefusal) as e:
        env.write_notes("king", "x")
    assert e.value.code == "notes_not_kept"
    with pytest.raises(EnvRefusal):
        ParallelEnv("fake-solo", notes_pad_bytes=0)


def _served(name="fake-solo", pad=64, **kw):
    env = ParallelEnv(name, notes_pad_bytes=pad, **kw)
    srv = EnvServer(env, turn_timeout_s=30).start()
    cl = EnvClient(srv.url, timeout=30)
    cl.reset(seed=1)
    return env, srv, cl


def test_the_default_shim_listing_is_unchanged_and_ack_lists_notes_without_await():
    assert json.dumps(own_specs(False), sort_keys=True) == json.dumps(_OWN_SPECS, sort_keys=True)
    ack = {t["name"]: t for t in own_specs(False, ack=True)}
    assert set(ack) == {"seat_observation", "end_turn"}
    assert set(ack["end_turn"]["inputSchema"]["properties"]) == {"summary", "notes"}
    wake = {t["name"]: t for t in own_specs(True, ack=True)}
    assert set(wake["end_turn"]["inputSchema"]["properties"]) == {"summary", "notes", "wake"}


def test_ack_end_turn_returns_no_observation_and_the_session_takes_no_further_call():
    import io
    from ck3bench.env.trajectory import TrajectoryWriter
    buf = io.StringIO()
    env, srv, cl = _served(trajectory=TrajectoryWriter(stream=buf))
    try:
        shim = SeatShim(cl, "king", 30, ack=True)
        res, err = shim.call("observe", {})
        assert not err
        # over-long notes are not written and the turn ENDS (no second call); the next turn says so
        over, err = shim.call("end_turn", {"summary": "x", "notes": "y" * 65})
        assert not err and over["turn_ended"] is True and over["notes_written"] is False
        assert "65 bytes > 64" in over["notes_unwritten"]
        o = cl.observation("king")["observation"]
        assert o["notes_pad"]["text"] == "" and "NOT written (65 bytes > 64" in o["notes_pad"]["unwritten"]
        notes = [r for r in map(json.loads, buf.getvalue().splitlines()) if r.get("kind") == "call"
                 and r.get("phase") == "notes"]
        assert [n["result"]["ok"] for n in notes] == [False]           # the refused write is recorded once
        shim = SeatShim(cl, "king", 30, ack=True)                        # the next turn: a new session
        out, err = shim.call("end_turn", {"summary": "done", "notes": "plan: gold"})
        assert not err and out["turn_ended"] is True and out["stepped"] is True and "observation" not in out
        assert out["next"] == ACK_NEXT and out["notes_bytes"] == 10 and "notes_written" not in out
        assert "unwritten" not in cl.observation("king")["observation"]["notes_pad"]   # said for one turn only
        again, err = shim.call("observe", {})
        assert err and again["code"] == "turn_ended"
        again, err = shim.call("end_turn", {"summary": "twice"})
        assert err and again["code"] == "turn_ended"
        assert cl.observation("king")["observation"]["notes_pad"]["text"] == "plan: gold"
        # the next session (a new shim) plays the next step
        nxt = SeatShim(cl, "king", 30, ack=True)
        out, err = nxt.call("end_turn", {"summary": "keep"})
        assert not err and out["step"] == 3
        assert cl.observation("king")["observation"]["notes_pad"]["text"] == "plan: gold", "omitted: kept"
    finally:
        srv.stop()
        env.close()


def test_ack_carries_wake_on_a_wake_task():
    env, srv, cl = _served("fake-kingdom-wake")
    try:
        shim = SeatShim(cl, "count", 30, ack=True)
        cl.submit("rival", [])
        out, err = shim.call("end_turn", {"summary": "w", "notes": "n",
                                          "wake": {"max_days": 10, "when": [{"event": "war_ended"}]}})
        assert not err and out["stepped"]
        o = cl.observation("count")["observation"]
        assert o["wake"]["last_step"]["days"] == 10 and o["notes_pad"]["text"] == "n"
    finally:
        srv.stop()
        env.close()


def test_the_check_summary_says_how_each_step_s_days_were_read():
    for name, by in (("fake-kingdom", "cycle_days"), ("fake-kingdom-wake", "wake.days")):
        buf = io.StringIO()
        env = ParallelEnv(name, trajectory=TrajectoryWriter(stream=buf))
        env.reset(seed=1)
        env.step({"count": [], "rival": []}, **({"wake": {"count": {"max_days": 30}}} if "wake" in name else {}))
        env.close()
        (row,) = summary(_recs(buf))
        assert row["days_by"] == by and row["days"] == (30 if "wake" in name else 73)
        assert row["notes"] == "not recorded" and row["steps"] == 1


def test_the_checker_holds_a_wake_step_to_its_days():
    buf = io.StringIO()
    env = ParallelEnv("fake-kingdom-wake", trajectory=TrajectoryWriter(stream=buf))
    env.reset(seed=1)
    env.step({"count": [], "rival": []}, wake={"count": {"max_days": 30}})
    env.close()
    recs = _recs(buf)
    assert check_trajectory(recs) == []
    j = next(i for i, r in enumerate(recs) if r["kind"] == "step")
    recs[j] = dict(recs[j], wake=dict(recs[j]["wake"], days=31))
    assert any("wake.days 31" in p for p in check_trajectory(recs))
    recs[j] = dict(recs[j], wake={k: v for k, v in recs[j]["wake"].items() if k != "days"})
    with pytest.raises(ValueError):
        from ck3bench.env.trajectory import step_days
        step_days(recs[0], recs[j])
