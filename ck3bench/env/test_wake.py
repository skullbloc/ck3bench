"""Wake tasks (wake.py): a step whose length the world sets.

On a fixed cadence a seat spends many turns on a bare end_turn (waiting for a
claim or for gold), and a claim or a war's end can sit up to a whole 73-day
step before the seat can react. A wake task lets the seat end its turn with
conditions; the step's clock runs until the first check at which one holds.

Proved here on the FAKE backend (test_wake_ck3.py runs the real backend's wake
route against a fake bridge; a backend with no wake route refuses a wake task,
`wake_unsupported`):
  * a fixed task refuses a wake request and is otherwise untouched;
  * a wake task whose seats ask nothing is the fixed task, draw for draw;
  * a threshold stops the clock on the first check day it holds (one day less,
    under the same draws, it does not hold);
  * an event (a war's end) wakes the seat the day it happens;
  * max_days and the horizon bound a step; the episode ends on the end date;
  * the closed vocabulary: malformed and unwitnessable conditions are refused,
    before any call of the step runs;
  * the trajectory records every request, the days run and why the step stopped,
    and the checker holds both kinds of task to their own rule;
  * the server and the MCP shim carry `wake`, and a fixed task's shim listing is
    byte-identical to before.
"""
from __future__ import annotations

import copy
import io
import json

import pytest

from ck3bench.env import ParallelEnv, ToolCall, contract
from ck3bench.env.errors import EnvRefusal
from ck3bench.env.mcp_shim import _OWN_SPECS, SeatShim
from ck3bench.env.scenario import BUILTIN_DICTS, scenario_from_dict
from ck3bench.env.server import EnvClient, EnvServer
from ck3bench.env.trajectory import TrajectoryWriter, check_trajectory, read_trajectory

WAKE = "fake-kingdom-wake"
NEVER = {"metric": "gold", "op": ">=", "value": 1e9}


def _env(name=WAKE, **kw):
    return ParallelEnv(name, **kw)


def _records(env_name, seed, script, **kw):
    buf = io.StringIO()
    env = _env(env_name, trajectory=TrajectoryWriter(stream=buf))
    env.reset(seed=seed)
    while env.agents:
        acts, wake = script(env)
        env.step(acts, **({"wake": wake} if wake is not None else {}))
    env.close()
    return [json.loads(l) for l in buf.getvalue().splitlines()]


def _strip(rec):
    """A record without what differs between two runs of the same episode (ids, wall clock)."""
    r = {k: v for k, v in rec.items() if k not in ("episode_id", "wall_utc", "wall_s")}
    if r.get("kind") == "header":
        r["scenario"] = {k: v for k, v in r["scenario"].items() if k not in ("id", "description", "wake")}
        r.pop("orders", None)     # the standing-orders setting, a wake task's as `wake` is
    if isinstance(r.get("advance"), dict):
        r["advance"] = {k: v for k, v in r["advance"].items() if k != "wall_s"}
    for o in (r.get("observations") or {}).values():
        o.pop("wake", None)
        o.pop("steps_total", None)
    r.pop("wake", None)
    return r


# ── a fixed task is untouched ───────────────────────────────────────────────

def test_a_fixed_task_refuses_a_wake_request_before_any_call_runs():
    env = _env("fake-kingdom")
    env.reset(seed=1)
    with pytest.raises(EnvRefusal) as e:
        env.step({"count": [ToolCall("do", {"intent": "raise_taxes"})], "rival": []},
                 wake={"count": {"when": [NEVER]}})
    assert e.value.code == "wake_not_declared"
    assert env.backend.calls == [] and env.steps_done == 0, "nothing ran: refused before the step"
    obs, *_ = env.step({"count": [], "rival": []})
    assert "wake" not in obs["count"] and obs["count"]["steps_total"] == 15


def test_a_wake_task_with_no_requests_is_the_fixed_task_draw_for_draw():
    def idle(env):
        return {s: [] for s in env.agents}, None
    fixed = _records("fake-kingdom", 5, idle)
    woke = _records(WAKE, 5, idle)
    assert [_strip(r) for r in fixed] == [_strip(r) for r in woke]
    steps = [r for r in woke if r["kind"] == "step"]
    assert len(steps) == 15 and all(s["wake"]["days"] == 73 and s["wake"]["stop"] in ("max_days", "horizon")
                                    for s in steps)
    assert all(s["wake"]["requests"]["count"] == {"declared": False, "max_days": 73, "when": []} for s in steps), \
        "a seat that asked nothing is recorded as not declared, not as a request"


# ── the clock stops on the day ───────────────────────────────────────────────

def _gold(env, seat="count"):
    return env.call(seat, ToolCall("observe")).result["gold"]


def test_a_gold_threshold_stops_the_clock_on_the_first_day_it_holds():
    target = 100.5          # one county: about +0.0033 gold a day, so this lies about 150 days away
    rival = {"max_days": 365, "when": [NEVER]}      # a seat that asks nothing would end the step at 73 days
    env = _env()
    env.reset(seed=3)
    obs, *_ = env.step({"count": [], "rival": []},
                       wake={"count": {"max_days": 365, "when": [{"metric": "gold", "op": ">=", "value": target}]},
                             "rival": rival})
    w = obs["count"]["wake"]["last_step"]
    assert w["stop"] == "condition" and 73 < w["days"] < 365
    assert w["fired"][0]["seat"] == "count" and w["fired"][0]["read"] >= target
    assert _gold(env) == w["fired"][0]["read"] >= target
    d = w["days"]
    # the same draws, one day short: the threshold does not hold yet
    env2 = _env()
    env2.reset(seed=3)
    obs2, *_ = env2.step({"count": [], "rival": []}, wake={"count": {"max_days": d - 1, "when": [NEVER]},
                                                            "rival": rival})
    assert obs2["count"]["wake"]["last_step"]["stop"] == "max_days"
    assert _gold(env2) < target
    assert obs2["count"]["wake"]["days_left"] == obs["count"]["wake"]["days_left"] + 1


def test_a_seat_that_asks_nothing_bounds_the_step_at_the_default_cycle():
    env = _env()
    env.reset(seed=3)
    obs, *_ = env.step({"count": [], "rival": []}, wake={"count": {"max_days": 365, "when": [NEVER]}})
    assert obs["rival"]["wake"]["last_step"]["days"] == 73


def test_a_war_that_ends_wakes_the_seat_the_day_it_ends():
    env = _env()
    env.reset(seed=2)
    obs, *_ = env.step({"count": [ToolCall("declare_war", {"target_id": 100})], "rival": []},
                       wake={"count": {"max_days": 365, "when": [{"event": "war_ended"}]}})
    w = obs["count"]["wake"]["last_step"]
    assert (w["stop"], w["days"], w["checks"]) == ("condition", 1, 1)
    assert w["fired"] == [{"seat": "count", "index": 0, "condition": {"event": "war_ended"}, "read": 1,
                           "date": obs["count"]["game_date"]}]
    # the rival asked nothing and took the step the count's condition ended
    assert obs["rival"]["game_date"] == obs["count"]["game_date"] == "1066.09.16"


def test_an_event_that_did_not_happen_does_not_wake_the_seat():
    env = _env()
    env.reset(seed=2)
    obs, *_ = env.step({"count": [], "rival": []},
                       wake={s: {"max_days": 200, "when": [{"event": "war_ended"}, {"event": "ruler_changed"}]}
                             for s in ("count", "rival")})
    w = obs["count"]["wake"]["last_step"]
    assert (w["stop"], w["days"], w["checks"], w["fired"]) == ("max_days", 200, 200, [])


def test_max_days_and_the_horizon_bound_a_step_and_the_episode_ends_on_its_end_date():
    env = _env()
    out = env.reset(seed=4)
    contract.check_reset(env, out)
    stops, last_obs, last_infos = [], None, None
    while env.agents:
        live = list(env.agents)
        res = env.step({s: [] for s in env.agents}, wake={s: {"max_days": 365, "when": [NEVER]} for s in env.agents})
        contract.check_step(env, live, res)
        last_obs, last_infos = res[0], res[4]
        stops.append(next(iter(last_obs.values()))["wake"]["last_step"])
    assert [(s["days"], s["stop"]) for s in stops] == [(365, "max_days"), (365, "max_days"), (365, "horizon")]
    contract.check_wake_horizon(env, last_obs, last_infos)


def test_the_shortest_max_days_among_the_seats_ends_the_step_for_all():
    env = _env()
    env.reset(seed=1)
    obs, *_ = env.step({"count": [], "rival": []},
                       wake={"count": {"max_days": 300, "when": [NEVER]}, "rival": {"max_days": 20}})
    assert obs["count"]["wake"]["last_step"]["days"] == 20 and obs["rival"]["game_date"] == "1066.10.05"


# ── the closed vocabulary ────────────────────────────────────────────────────

@pytest.mark.parametrize("req,code", [
    ({"when": [{"event": "claim_gained"}]}, "wake_condition_unwitnessable"),     # the fake has no claims
    ({"when": [{"event": "comet_seen"}]}, "wake_malformed"),
    # piety joined the closed set with the real backend; the fake does not read it at a check
    ({"when": [{"metric": "piety", "op": ">=", "value": 1}]}, "wake_condition_unwitnessable"),
    ({"when": [{"metric": "faith", "op": ">=", "value": 1}]}, "wake_malformed"),
    ({"when": [{"metric": "gold", "op": ">", "value": 1}]}, "wake_malformed"),
    ({"when": [{"metric": "gold", "op": ">=", "value": "1"}]}, "wake_malformed"),
    ({"when": [{"metric": "gold", "op": ">=", "value": 1, "then": "declare_war"}]}, "wake_malformed"),
    ({"when": [NEVER] * 9}, "wake_malformed"),
    ({"max_days": 366}, "wake_malformed"),
    ({"max_days": 0}, "wake_malformed"),
    ({"max_days": True}, "wake_malformed"),
    ({"until": 5}, "wake_malformed"),
    ("soon", "wake_malformed"),
])
def test_a_request_outside_the_vocabulary_is_refused_before_the_step(req, code):
    env = _env()
    env.reset(seed=1)
    with pytest.raises(EnvRefusal) as e:
        env.step({"count": [ToolCall("do", {"intent": "raise_taxes"})], "rival": []}, wake={"count": req})
    assert e.value.code == code
    assert env.backend.calls == [] and env.steps_done == 0


def test_a_wake_for_a_seat_that_is_not_live_is_refused():
    env = _env()
    env.reset(seed=1)
    with pytest.raises(EnvRefusal) as e:
        env.step({"count": [], "rival": []}, wake={"duke": {"max_days": 5}})
    assert e.value.code == "unknown_seat"


@pytest.mark.parametrize("wake,why", [
    ({"check_days": 0, "max_days": 365}, "check_days 0"),
    ({"check_days": 10, "max_days": 5}, "check above max"),
    ({"check_days": 1, "max_days": 60}, "max below the cycle (73)"),
    ({"check_days": 1, "max_days": 2000}, "max above the horizon"),
    ({"check_days": 1}, "max_days missing"),
])
def test_the_scenario_declaration_is_checked(wake, why):
    d = copy.deepcopy(BUILTIN_DICTS[WAKE])
    d["wake"] = wake
    with pytest.raises(EnvRefusal):
        scenario_from_dict(d)


def test_the_real_game_backend_takes_a_wake_task_and_refuses_only_what_it_cannot_witness(tmp_path):
    """The ck3 backend runs wake tasks (test_wake_ck3.py proves the route against fakes). A task
    that forces a wake the backend cannot witness is refused before anything launches; one it can
    witness goes on to the launch (here refused for the missing save: nothing was launched)."""
    from ck3bench.env.ck3 import Ck3Backend
    d = copy.deepcopy(BUILTIN_DICTS["ireland1066-kingdom-30y"])
    d.update(id="ireland-wake-probe", wake={"check_days": 1, "max_days": 365, "forced": ["ruler_changed"]})
    b = Ck3Backend(saves_dir=tmp_path)
    with pytest.raises(EnvRefusal) as e:
        ParallelEnv(scenario_from_dict(d), backend=b).reset(seed=1)
    assert e.value.code == "start_save_missing" and b.pid is None and b.link is None
    fake = copy.deepcopy(BUILTIN_DICTS[WAKE])
    fake["wake"] = {"check_days": 1, "max_days": 365, "forced": ["war_declared_on"]}
    env = ParallelEnv(scenario_from_dict(fake))
    with pytest.raises(EnvRefusal) as e:
        env.reset(seed=1)
    assert e.value.code == "wake_condition_unwitnessable" and env.steps_done == 0


# ── determinism and the record ───────────────────────────────────────────────

def _waker(env):
    acts = {s: [] for s in env.agents}
    if env.steps_done == 0 and "count" in acts:
        acts["count"] = [ToolCall("declare_war", {"target_id": 101})]
    return acts, {s: {"max_days": 200, "when": [{"event": "war_ended"}, {"event": "realm_changed"},
                                                {"metric": "gold", "op": ">=", "value": 110}]}
                  for s in env.agents}


def test_the_same_seed_and_requests_give_the_same_episode_and_a_well_formed_record():
    a, b = _records(WAKE, 9, _waker), _records(WAKE, 9, _waker)
    assert [_strip(r) for r in a] == [_strip(r) for r in b]
    assert check_trajectory(a) == []
    steps = [r for r in a if r["kind"] == "step"]
    days = [s["wake"]["days"] for s in steps]
    assert sum(days) == 1095 and len(set(days)) > 1, "variable-length steps that sum to the horizon"
    assert steps[0]["wake"]["requests"]["count"]["declared"] is True
    assert {s["wake"]["stop"] for s in steps} <= {"condition", "max_days", "horizon"}


def test_the_checker_holds_each_kind_of_task_to_its_own_rule():
    woke = _records(WAKE, 9, _waker)
    bad = [dict(r) for r in woke]
    i = next(k for k, r in enumerate(bad) if r["kind"] == "step")
    bad[i] = {k: v for k, v in bad[i].items() if k != "wake"}
    assert any("records no wake days" in p for p in check_trajectory(bad))
    fixed = _records("fake-kingdom", 9, lambda env: ({s: [] for s in env.agents}, None))
    assert check_trajectory(fixed) == []
    j = next(k for k, r in enumerate(fixed) if r["kind"] == "step")
    fixed[j] = dict(fixed[j], wake={"days": 73, "stop": "max_days"})
    assert any("wake on a fixed-cadence task" in p for p in check_trajectory(fixed))


# ── over the server and the MCP shim ─────────────────────────────────────────

def test_the_shim_lists_wake_only_on_a_wake_task_and_carries_it_to_the_step(tmp_path):
    traj = tmp_path / "t.jsonl"
    env = _env(trajectory=TrajectoryWriter(str(traj)))
    srv = EnvServer(env, turn_timeout_s=30).start()
    try:
        cl = EnvClient(srv.url, timeout=30)
        cl.reset(seed=2)
        shim = SeatShim(cl, "count", 30)
        et = next(t for t in shim.list_tools() if t["name"] == "end_turn")
        assert "wake" in et["inputSchema"]["properties"]
        bad, err = shim.call("end_turn", {"summary": "x", "wake": {"when": [{"event": "claim_gained"}]}})
        assert err and bad["code"] == "wake_condition_unwitnessable", "refused to the seat that sent it"
        res, err = shim.call("declare_war", {"target_id": 100})
        assert not err
        cl.submit("rival", [])
        out, err = shim.call("end_turn", {"summary": "war", "wake": {"max_days": 100,
                                                                      "when": [{"event": "war_ended"}]}})
        assert not err and out["observation"]["wake"]["last_step"]["stop"] == "condition"
        assert out["observation"]["game_date"] == "1066.09.16"
    finally:
        srv.stop()
        env.close()
    step = next(r for r in read_trajectory(str(traj)) if r["kind"] == "step")
    assert step["wake"]["requests"]["count"] == {"declared": True, "max_days": 100, "when": [{"event": "war_ended"}]}
    assert step["wake"]["requests"]["rival"]["declared"] is False


def test_a_fixed_task_shim_listing_is_unchanged_and_refuses_wake(tmp_path):
    env = _env("fake-solo")
    srv = EnvServer(env, turn_timeout_s=30).start()
    try:
        cl = EnvClient(srv.url, timeout=30)
        cl.reset(seed=1)
        shim = SeatShim(cl, "king", 30)
        own = [t for t in shim.list_tools() if t["name"] in ("seat_observation", "end_turn", "await_step")]
        assert json.dumps(own, sort_keys=True) == json.dumps(_OWN_SPECS, sort_keys=True)
        res, err = shim.call("end_turn", {"wake": {"max_days": 5}})
        assert err and res["code"] == "wake_not_declared"
    finally:
        srv.stop()
        env.close()


def test_a_direct_step_driver_sends_wake_over_http():
    env = _env()
    srv = EnvServer(env).start()
    try:
        cl = EnvClient(srv.url, timeout=30)
        cl.reset(seed=3)
        obs, *_ = cl.step({"count": [], "rival": []}, wake={"rival": {"max_days": 10}})
        assert obs["rival"]["wake"]["last_step"]["days"] == 10
    finally:
        srv.stop()
        env.close()
