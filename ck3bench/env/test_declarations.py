"""Rewards, scenarios and the trajectory format: each declaration is read by
one strict reader, and the trajectory checker finds each kind of defect."""
from __future__ import annotations

import io
import json
import math

import pytest

from ck3bench.env import TrajectoryWriter, check_trajectory, read_trajectory
from ck3bench.env.errors import EnvRefusal
from ck3bench.env.rewards import RewardContext, RewardUnavailable, spec_from
from ck3bench.env.scenario import STATUSES, BUILTIN_DICTS, builtin, load_scenario, resolve, scenario_from_dict
from ck3bench.env.types import METRIC_OK, METRIC_UNREADABLE, PUBLIC_TOOLS, Metrics


def _m(**v):
    m = Metrics("1066.09.15", 1)
    for k, x in v.items():
        if x is None:
            m.status[k] = METRIC_UNREADABLE
        else:
            m.values[k], m.status[k] = x, METRIC_OK
    return m


# ── rewards ──────────────────────────────────────────────────────────────

def test_delta_terms_sum_to_the_change_over_the_episode():
    spec = spec_from([("realm_counties_delta", 1.0)])
    seq = [_m(realm_counties=c) for c in (84, 80, 81, 90)]
    total = sum(spec.seat_reward(RewardContext(seq[0], a, b, False, False))[0] for a, b in zip(seq, seq[1:]))
    assert total == 90 - 84


def test_terminal_terms_pay_only_on_the_final_step():
    spec = spec_from([("gold_terminal", 2.0)])
    s, a, b = _m(gold=10.0), _m(gold=20.0), _m(gold=50.0)
    assert spec.seat_reward(RewardContext(s, a, b, False, False))[0] == 0.0
    assert spec.seat_reward(RewardContext(s, a, b, True, False))[0] == 80.0


def test_an_unreadable_metric_raises_instead_of_counting_as_zero():
    spec = spec_from([("realm_counties_delta", 1.0)])
    with pytest.raises(RewardUnavailable) as e:
        spec.seat_reward(RewardContext(_m(realm_counties=84), _m(realm_counties=84), _m(realm_counties=None),
                                       False, False))
    assert e.value.metric == "realm_counties" and e.value.status == METRIC_UNREADABLE


def test_a_metric_never_read_is_not_declared_not_zero():
    spec = spec_from([("gold_delta", 1.0)])
    with pytest.raises(RewardUnavailable) as e:
        spec.seat_reward(RewardContext(_m(), _m(), _m(), False, False))
    assert e.value.status == "not_declared"


def test_team_modes():
    own = {"a": 1.0, "b": 3.0}
    assert spec_from([("gold_delta", 1)], "none").team_rewards(own) == own
    assert spec_from([("gold_delta", 1)], "sum").team_rewards(own) == {"a": 4.0, "b": 4.0}
    assert spec_from([("gold_delta", 1)], "mean").team_rewards(own) == {"a": 2.0, "b": 2.0}


@pytest.mark.parametrize("terms,team", [([("happiness", 1.0)], "none"), ([], "none"),
                                        ([("gold_delta", "1")], "none"), ([("gold_delta", 1.0)], "max")])
def test_an_unknown_or_malformed_reward_is_refused(terms, team):
    with pytest.raises(EnvRefusal) as e:
        spec_from(terms, team)
    assert e.value.code == "reward_unknown"


def test_the_reward_reads_only_the_metrics_its_terms_name():
    assert spec_from([("gold_delta", 1), ("line_survival", 5), ("gold_terminal", 1)]).metrics() == \
        ("gold", "line_alive")


# ── scenarios ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("name", sorted(BUILTIN_DICTS))
def test_every_builtin_loads_and_round_trips_through_the_one_reader(name):
    s = builtin(name)
    assert scenario_from_dict(s.to_dict()) == s
    assert s.status in STATUSES and s.status_reason


def test_the_short_ck3_tasks_are_declared_with_their_status():
    ck3 = {n: builtin(n) for n in BUILTIN_DICTS if builtin(n).backend == "ck3"}
    assert 3 <= len(ck3) <= 5
    assert {n for n, s in ck3.items() if s.status == "buildable_now"} >= {"france1066-treasury-2y"}
    assert all(s.horizon_days <= 3650 for s in ck3.values()), "short tasks: ten game-years at most"
    assert all(set(s.tools) <= set(PUBLIC_TOOLS) | {"msg_send", "note_read", "note_write"} for s in ck3.values())


def _base():
    return json.loads(json.dumps(BUILTIN_DICTS["fake-solo"]))


@pytest.mark.parametrize("mutate,code", [
    (lambda d: d.update(colour="red"), "scenario_unknown_key"),
    (lambda d: d.pop("horizon_days"), "scenario_missing_key"),
    (lambda d: d.update(horizon_days=True), "scenario_wrong_type"),
    (lambda d: d.update(horizon_days=100), "scenario_bad_value"),
    (lambda d: d.update(backend="sim"), "scenario_bad_value"),
    (lambda d: d["start"].update(date="1066.02.30"), "scenario_bad_value"),
    (lambda d: d["seats"].append({"name": "king", "character_id": 9}), "scenario_bad_value"),
    (lambda d: d["seats"][0].update(census_rank=1), "scenario_bad_value"),
    (lambda d: d.update(tools=["observe", "teleport"]), "scenario_bad_value"),
    (lambda d: d.update(goal="win"), "scenario_bad_value"),
    (lambda d: d.update(game_speed=9), "scenario_bad_value"),
    (lambda d: d.update(backend="ck3"), "scenario_missing_key"),      # a ck3 scenario needs a save
    (lambda d: d["reward"].update(team="max"), "scenario_bad_value"),
])
def test_a_bad_declaration_is_refused_by_name(mutate, code):
    d = _base()
    mutate(d)
    with pytest.raises(EnvRefusal) as e:
        scenario_from_dict(d)
    assert e.value.code == code


def test_a_scenario_toml_is_read_by_the_same_reader(tmp_path):
    p = tmp_path / "s.toml"
    p.write_text('id = "t"\ndescription = "d"\nbackend = "fake"\nhorizon_days = 60\ncycle_days = 30\n'
                 'tools = ["observe", "pass_turn"]\ngame_speed = 3\nstatus = "buildable_now"\n'
                 'status_reason = "test"\n[start]\ndate = "1066.09.15"\n'
                 '[[seats]]\nname = "k"\ncharacter_id = 1\n'
                 '[reward]\nterms = [{name = "gold_delta", weight = 1.0}]\n'
                 '[fake]\ncounties = [3]\ngold = [1.0]\nprestige = [1.0]\nages = [30]\n', encoding="utf-8")
    s = load_scenario(p)
    assert s.n_steps == 2 and s.end_date == "1066.11.14"
    assert resolve(str(p)) == s
    with pytest.raises(EnvRefusal):
        resolve("no-such-task")


# ── the trajectory ───────────────────────────────────────────────────────

def _w():
    w = TrajectoryWriter(stream=io.StringIO())
    w.begin(backend="fake", fake=True, episode_id="e1")
    return w


def _good(w):
    w.write("header", scenario={}, seed=1, seats={}, provenance={})
    w.write("call", step=1, seat="k", phase="turn", call={}, result={}, game_date="x", wall_s=0.0)
    w.write("step", step=1, game_date="x", order=[], actions={}, observations={}, rewards={"k": math.nan},
            terminations={}, truncations={}, infos={}, metrics={}, advance={})
    w.write("end", reason="horizon", steps=1, returns={})


def _recs(w):
    return [json.loads(x) for x in w._fh.getvalue().splitlines()]


def test_a_well_formed_trajectory_checks_clean_and_nan_is_written_null():
    w = _w()
    _good(w)
    recs = _recs(w)
    assert check_trajectory(recs) == []
    assert recs[2]["rewards"] == {"k": None}


@pytest.mark.parametrize("damage,needle", [
    (lambda r: r.pop(), "ends inside an episode"),
    (lambda r: r[2].update(step=2), "step 2 after 0"),
    (lambda r: r.insert(0, dict(r[0])), "header inside an episode"),
    (lambda r: r[1].update(episode_id="other"), "episode_id changed"),
    (lambda r: r[2].pop("rewards"), "missing ['rewards']"),
    (lambda r: r[3].update(steps=5), "end says 5 steps"),
    (lambda r: r[0].update(schema="other/9"), "schema"),
])
def test_the_checker_finds_each_defect(damage, needle):
    w = _w()
    _good(w)
    recs = _recs(w)
    damage(recs)
    assert any(needle in p for p in check_trajectory(recs)), check_trajectory(recs)


def test_read_trajectory_names_the_bad_line(tmp_path):
    p = tmp_path / "t.jsonl"
    p.write_text('{"a": 1}\nnot json\n', encoding="utf-8")
    with pytest.raises(ValueError, match="line 2"):
        read_trajectory(p)
