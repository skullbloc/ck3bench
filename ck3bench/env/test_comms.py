"""The env-owned multi-agent layer (comms.py) and the seat-keyed team reward,
on the fake backend and on the real backend's route with fakes of the game."""
from __future__ import annotations

import copy
import io
import math

import pytest

from ck3bench.env import ParallelEnv, ToolCall, contract
from ck3bench.env.comms import COMMS_TOOLS
from ck3bench.env.errors import TERM_TEAM_SEAT_DONE, EnvRefusal
from ck3bench.env.rewards import spec_from
from ck3bench.env.scenario import BUILTIN_DICTS, builtin, scenario_from_dict
from ck3bench.env.trajectory import TrajectoryWriter, check_trajectory


def _council(**over):
    d = copy.deepcopy(BUILTIN_DICTS["fake-council"])
    d.update(over)
    return scenario_from_dict(d)


def _env(sc=None, traj=None):
    env = ParallelEnv(sc or builtin("fake-council"), trajectory=traj)
    env.reset(seed=11)
    return env


def C(name, **a):
    return ToolCall(name, a)


# ── messages ───────────────────────────────────────────────────────────────

def test_a_message_is_queued_then_delivered_at_the_step_end_into_the_recipients_observation():
    env = _env()
    r = env.call("duke1", C("msg_send", to="king", text="the east is weak"))
    assert r.ok and r.result["verdict"] == "queued"
    assert env.observe("king")["comms"]["inbox"] == [], "nothing arrives before the step ends"
    obs, *_ = env.step({s: [] for s in env.agents})
    inbox = obs["king"]["comms"]["inbox"]
    assert [(m["from"], m["text"], m["sent_step"]) for m in inbox] == [("duke1", "the east is weak", 1)]
    assert obs["duke2"]["comms"]["inbox"] == [], "one message, one recipient"
    obs, *_ = env.step({s: [] for s in env.agents})
    assert obs["king"]["comms"]["inbox"] == [], "a message is delivered once"


def test_a_message_sent_as_a_step_action_is_delivered_the_same_way():
    env = _env()
    obs, *_ = env.step({"king": [C("msg_send", to="duke2", text="hold")], "duke1": [], "duke2": []})
    assert [m["text"] for m in obs["duke2"]["comms"]["inbox"]] == ["hold"]


def test_delivery_is_in_the_seeded_seat_order_whatever_order_the_calls_arrived_in():
    def run(arrival):
        env = _env()
        for seat in arrival:
            env.call(seat, C("msg_send", to="king" if seat != "king" else "duke1", text=f"from {seat}"))
            env.call(seat, C("note_write", pad="shared", text=f"[{seat}]", mode="append"))
        obs, _, _, _, infos = env.step({s: [] for s in env.agents})
        return obs, env, infos["king"]["order"]
    a_obs, a_env, order = run(["duke1", "duke2", "king"])
    b_obs, b_env, b_order = run(["king", "duke2", "duke1"])
    assert order == b_order, "same seed, same order"
    assert a_obs["king"]["comms"]["inbox"] == b_obs["king"]["comms"]["inbox"]
    assert [m["from"] for m in a_obs["king"]["comms"]["inbox"]] == [s for s in order if s != "king"]
    assert a_env.comms.shared["text"] == b_env.comms.shared["text"] == "".join(f"[{s}]" for s in order)


@pytest.mark.parametrize("args,verdict", [
    ({"to": "king", "text": "x" * 201}, "text_too_long"),
    ({"to": "duke1", "text": "me"}, "recipient_self"),
    ({"to": "pope", "text": "hi"}, "recipient_unknown"),
    ({"to": "king"}, "args_malformed"),
    ({"to": "king", "text": 5}, "args_malformed"),
    ({"to": "king", "text": "hi", "cc": "duke2"}, "args_malformed"),
])
def test_a_bad_message_is_refused_and_never_truncated(args, verdict):
    env = _env()
    r = env.call("duke1", ToolCall("msg_send", args))
    assert not r.ok and r.result["verdict"] == verdict and r.error.startswith(verdict)
    obs, *_ = env.step({s: [] for s in env.agents})
    assert all(not o["comms"]["inbox"] for o in obs.values()), "a refused message is never delivered"


def test_the_quota_is_per_seat_per_step_and_a_refusal_spends_none():
    env = _env()
    assert not env.call("duke1", C("msg_send", to="king", text="y" * 500)).ok      # refused: spends nothing
    assert env.call("duke1", C("msg_send", to="king", text="1")).ok
    assert env.call("duke1", C("msg_send", to="duke2", text="2")).ok
    r = env.call("duke1", C("msg_send", to="king", text="3"))
    assert not r.ok and r.result["verdict"] == "quota_exceeded"
    assert env.call("duke2", C("msg_send", to="king", text="other seat")).ok
    obs, *_ = env.step({s: [] for s in env.agents})
    assert obs["duke1"]["comms"]["messages_left"] == 2, "the quota resets at the step end"
    assert env.call("duke1", C("msg_send", to="king", text="4")).ok


def test_a_message_to_a_seat_that_ended_this_step_is_dropped_and_recorded():
    sc = _council(fake={**BUILTIN_DICTS["fake-council"]["fake"], "ages": [40, 30, 200]})
    buf = io.StringIO()
    w = TrajectoryWriter(stream=buf)
    env = ParallelEnv(sc, trajectory=w)
    env.reset(seed=1)
    env.backend.realms["duke2"].counties = 0      # its ruler dies with no realm at the advance
    env.call("king", C("msg_send", to="duke2", text="too late"))
    obs, r, te, tr, infos = env.step({s: [] for s in env.agents})
    assert te["duke2"] and "duke2" not in env.agents
    import json
    step = [json.loads(x) for x in buf.getvalue().splitlines() if '"kind": "step"' in x][0]
    assert [(d["to"], d["why"]) for d in step["comms"]["dropped"]] == [("duke2", "recipient_done")]
    assert step["comms"]["delivered"] == []


# ── notepads ───────────────────────────────────────────────────────────────

def test_a_private_pad_applies_at_once_and_is_the_seats_alone():
    env = _env()
    assert env.call("king", C("note_write", pad="private", text="plan", mode="append")).result["version"] == 1
    assert env.call("king", C("note_read", pad="private")).result["text"] == "plan"
    assert env.call("duke1", C("note_read", pad="private")).result["text"] == ""
    r = env.call("king", C("note_write", pad="private", text="new", mode="replace", version=0))
    assert not r.ok and r.result["verdict"] == "version_stale"
    assert env.call("king", C("note_write", pad="private", text="new", mode="replace", version=1)).ok
    assert env.call("king", C("note_read", pad="private")).result["text"] == "new"


def test_the_shared_pad_changes_only_at_step_ends_and_a_stale_replace_is_refused_there():
    env = _env()
    assert env.call("duke1", C("note_write", pad="shared", text="A", mode="append")).result["verdict"] == "queued"
    seen = env.call("king", C("note_read", pad="shared")).result
    assert seen["text"] == "" and seen["version"] == 0, "a read shows the pad as of the last step end"
    assert env.call("duke1", C("note_read", pad="shared")).result["your_pending_writes"][0]["mode"] == "append"
    env.call("king", C("note_write", pad="shared", text="KING", mode="replace", version=0))
    obs, _, _, _, infos = env.step({s: [] for s in env.agents})
    order = infos["king"]["order"]
    shared = env.comms.shared
    if order.index("duke1") < order.index("king"):
        assert shared["text"] == "A"
        assert obs["king"]["comms"]["shared_pad_results"][0]["verdict"] == "version_stale"
    else:
        assert shared["text"] == "KINGA"
        assert obs["king"]["comms"]["shared_pad_results"][0]["verdict"] == "applied"
    assert obs["duke1"]["comms"]["shared_pad_results"][0]["verdict"] == "applied"
    assert env.call("duke2", C("note_read", pad="shared")).result["text"] == shared["text"]


@pytest.mark.parametrize("args,verdict", [
    ({"pad": "council", "text": "x", "mode": "append"}, "pad_unknown"),
    ({"pad": "shared", "text": "x", "mode": "prepend"}, "mode_invalid"),
    ({"pad": "shared", "text": "x", "mode": "replace"}, "version_required"),
    ({"pad": "private", "text": "x" * 201, "mode": "append"}, "write_too_long"),
    ({"pad": "private", "text": "x", "mode": "replace", "version": True}, "args_malformed"),
])
def test_bad_writes_are_refused_at_the_call(args, verdict):
    env = _env()
    r = env.call("king", ToolCall("note_write", args))
    assert not r.ok and r.result["verdict"] == verdict


def test_a_pad_that_would_overflow_is_refused_never_truncated():
    env = _env()
    for _ in range(2):
        assert env.call("king", C("note_write", pad="private", text="x" * 200, mode="append")).ok
    r = env.call("king", C("note_write", pad="private", text="y", mode="append"))
    assert not r.ok and r.result["verdict"] == "pad_full"
    assert env.call("king", C("note_read", pad="private")).result["bytes"] == 400


def test_an_undeclared_pad_is_refused():
    d = copy.deepcopy(BUILTIN_DICTS["fake-council"])
    d["comms"]["notepads"] = {"private_max_bytes": 100, "max_write_bytes": 50}
    env = _env(scenario_from_dict(d))
    r = env.call("king", C("note_read", pad="shared"))
    assert not r.ok and r.result["verdict"] == "pad_not_declared"
    assert "shared_pad_results" not in env.observe("king")["comms"]


# ── declarations ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("mutate,code", [
    (lambda d: d["tools"].remove("msg_send"), "scenario_bad_value"),           # declared, not listed
    (lambda d: d["comms"].pop("messages"), "scenario_bad_value"),              # listed, not declared
    (lambda d: d["comms"]["messages"].update(quota_per_step=0), "scenario_bad_value"),
    (lambda d: d["comms"]["messages"].update(quota_per_step=1.5), "scenario_wrong_type"),
    (lambda d: d["comms"]["messages"].update(topology="star"), "scenario_unknown_key"),
    (lambda d: d["comms"].update(broadcast={}), "scenario_unknown_key"),
    (lambda d: d["comms"]["notepads"].pop("max_write_bytes"), "scenario_missing_key"),
    (lambda d: d["comms"].update(notepads={"max_write_bytes": 5}), "scenario_missing_key"),
    (lambda d: d["reward"].pop("team_seat"), "scenario_bad_value"),
    (lambda d: d["reward"].update(team_seat="pope"), "scenario_bad_value"),
    (lambda d: d["reward"].update(team="mean"), "scenario_bad_value"),          # team_seat without seat mode
    (lambda d: d["seats"].reverse(), "scenario_bad_value"),                    # census seat first
    (lambda d: d["seats"][2].update(census_rank=1), "scenario_bad_value"),     # repeated rank
])
def test_a_bad_comms_or_team_declaration_is_refused(mutate, code):
    d = copy.deepcopy(BUILTIN_DICTS["fake-council"])
    mutate(d)
    with pytest.raises(EnvRefusal) as e:
        scenario_from_dict(d)
    assert e.value.code == code


def test_a_scenario_without_comms_has_no_comms_and_its_tools_never_reach_the_layer():
    env = _env(builtin("fake-duel"))
    assert env.comms is None and "comms" not in env.observe("east")
    assert not env.call("east", C("msg_send", to="west", text="x")).ok   # not in its tools


def test_tool_specs_cover_every_scenario_tool_in_order():
    env = _env()
    specs = env.tool_specs("king")
    assert [t["name"] for t in specs] == list(env.scenario.tools)
    assert all(isinstance(t["inputSchema"], dict) for t in specs)
    assert {t["name"] for t in specs} >= set(COMMS_TOOLS)


# ── the seat-keyed team reward ─────────────────────────────────────────────

def test_team_seat_mode_hands_every_seat_the_keyed_seats_own_reward():
    assert spec_from([("gold_delta", 1)], "seat", "a").team_rewards({"a": 3.0, "b": -1.0}) == {"a": 3.0, "b": 3.0}
    with pytest.raises(EnvRefusal):
        spec_from([("gold_delta", 1)], "seat", "a").team_rewards({"b": 1.0})
    with pytest.raises(EnvRefusal):
        spec_from([("gold_delta", 1)], "seat")
    env = _env()
    for _ in range(8):
        if not env.agents:
            break
        live = list(env.agents)
        obs, r, te, tr, infos = env.step({s: [] for s in env.agents})
        assert set(r) == set(live)
        assert all(v == infos["king"]["reward_own"] for v in r.values()), "one reward, the king's"


def test_when_the_keyed_seat_ends_every_seat_terminates_team_seat_done():
    env = _env()
    env.backend.realms["king"].counties = 0       # the king dies with no realm: the line ends
    live = list(env.agents)
    out = env.step({s: [] for s in env.agents})
    contract.check_step(env, live, out)
    _, r, te, tr, infos = out
    assert te == {"king": True, "duke1": True, "duke2": True}
    assert infos["king"]["termination_reason"] == "line_ended"
    assert infos["duke1"]["termination_reason"] == TERM_TEAM_SEAT_DONE
    assert infos["duke1"]["team_seat_reason"] == "line_ended"
    assert r["duke1"] == r["king"] == infos["king"]["reward_own"] < 0
    assert env.agents == [] and env.end_reason == "all_seats_done"


def test_a_failure_step_is_still_nan_for_every_seat_in_team_seat_mode():
    d = copy.deepcopy(BUILTIN_DICTS["fake-council"])
    d["fake"]["fail"] = {"at_step": 1, "kind": "game_exited"}
    env = _env(scenario_from_dict(d))
    live = list(env.agents)
    out = env.step({s: [] for s in env.agents})
    contract.check_step(env, live, out)
    assert all(math.isnan(v) for v in out[1].values())


# ── the trajectory ─────────────────────────────────────────────────────────

def test_the_trajectory_records_comms_calls_and_step_ends_and_the_checker_wants_them():
    import json
    buf = io.StringIO()
    env = _env(traj=TrajectoryWriter(stream=buf))
    env.call("duke1", C("msg_send", to="king", text="hi"))
    env.step({s: [] for s in env.agents})
    env.close()
    recs = [json.loads(x) for x in buf.getvalue().splitlines()]
    assert check_trajectory(recs) == []
    calls = [r for r in recs if r["kind"] == "call"]
    assert calls[0]["call"]["tool_name"] == "msg_send" and calls[0]["result"]["result"]["verdict"] == "queued"
    step = [r for r in recs if r["kind"] == "step"][0]
    assert step["comms"]["delivered"][0]["text"] == "hi"
    assert recs[0]["scenario"]["comms"]["messages"]["quota_per_step"] == 2
    del step["comms"]
    assert any("records none" in p for p in check_trajectory(recs))


# ── the real backend inherits it ───────────────────────────────────────────

def test_the_real_backend_route_inherits_the_layer_and_never_sees_its_tools(tmp_path):
    from ck3bench.env import test_ck3_backend as t
    d = copy.deepcopy(BUILTIN_DICTS["france1066-treasury-2y"])
    d["seats"] = [{"name": "king", "character_id": 37067}, {"name": "rival", "character_id": 37068}]
    d["tools"] = ["observe", "msg_send"]
    d["comms"] = {"messages": {"quota_per_step": 1, "max_text_bytes": 100}}
    sc = scenario_from_dict(d)
    b, br, _, tr = t._backend(tmp_path)
    br.realm[37068] = 10
    br.watch = {"status": "following", "line_character_id": 37067}
    env = ParallelEnv(sc, backend=b)
    env.reset(seed=1)
    assert env.call("king", C("msg_send", to="rival", text="peace?")).ok
    assert env.call("king", C("observe")).ok
    obs, *_ = env.step({s: [] for s in env.agents})
    assert obs["rival"]["comms"]["inbox"][0]["text"] == "peace?"
    # (the backend's own per-turn news read, observe(depth="news"), is not the seat's call)
    seen = [name for tp in tr.values() for name, a in tp.calls if a.get("depth") != "news"]
    assert seen == ["observe"], "the game's tool server saw only the game tool"
    env.close()


def test_the_real_backend_describes_game_tools_from_the_seats_own_mcp_server(tmp_path):
    """McpStdioTransport.list_tool_specs against a real MCP server over stdio;
    Ck3Backend.tool_specs refuses a tool the server does not register."""
    pytest.importorskip("mcp")
    import os
    import sys
    from ck3bench.env.ck3 import Ck3Backend, McpStdioTransport
    from ck3bench.env.test_mirrors import TINY_SERVER
    p = tmp_path / "tiny_server.py"
    p.write_text(TINY_SERVER, encoding="utf-8")
    t = McpStdioTransport([sys.executable, str(p)], dict(os.environ), timeout=60)
    try:
        b = Ck3Backend(None)
        b.transports["king"] = t
        (spec,) = b.tool_specs("king", ["observe"])
        assert spec["name"] == "observe" and spec["inputSchema"]["type"] == "object"
        with pytest.raises(EnvRefusal) as e:
            b.tool_specs("king", ["observe", "declare_war"])
        assert e.value.code == "tool_specs_unavailable"
    finally:
        t.close()
