"""The shipped baseline: its file reads strictly, its per-game values agree with
the summary rows it states, and a reading refuses what the set did not measure
instead of filling it in."""
from __future__ import annotations

import copy
import json

import pytest

from ck3bench.env import baselines as bl
from ck3bench.env import cli
from ck3bench.env.errors import EnvRefusal
from ck3bench.env.scenario import BUILTIN_DICTS, scenario_from_dict


def _doc():
    return json.loads((bl.HERE / bl.FILES["france1066-unsteered"]).read_text(encoding="utf-8"))


def test_the_shipped_file_reads_and_rests_on_eight_games():
    b = bl.load_baseline()
    assert b.games == 8 and all(len(m.counties) == 8 for m in b.marks)
    assert [m.years for m in b.marks] == [10, 20, 25, 30, 40, 50, 75, 100]
    assert b.doc["schema"] == "ck3bench.baseline/2"
    assert b.start["realm_counties"] == 84 and b.start["date"] == "1066.09.15"


@pytest.mark.parametrize("years", ["25", "50", "75", "100"])
def test_per_game_values_reproduce_the_stated_summary(years):
    """Two readings of one set: the per-game rows and the summary rows the
    file states. A typo in either reds here (the summary is stated to one
    decimal, so agreement is to 0.05)."""
    b = bl.load_baseline()
    stated = b.doc["stated_summary"][years]
    got = bl.summarize(b.mark(int(years)).counties)
    assert abs(got["mean"] - stated["mean"]) < 0.05
    assert abs(got["sd"] - stated["sd"]) < 0.05
    assert got["median"] == stated["median"] and got["under_10"] == stated["under_10"]


def test_quantiles_are_type_7_and_sd_is_the_sample_sd():
    xs = sorted([81, 88, 83, 82, 1, 75, 83, 87])
    assert bl.quantile(xs, 0.5) == 82.5 and bl.quantile(xs, 0.0) == 1 and bl.quantile(xs, 1.0) == 88
    assert bl.quantile(xs, 0.25) == 79.5           # h = 1.75: 75 + 0.75 * (81 - 75)
    assert bl.summarize([0, 2])["sd"] == round(2 ** 0.5, 3)
    with pytest.raises(ValueError):
        bl.summarize([5])


def _game(d, i=0):
    return d["series"]["games"][i]


@pytest.mark.parametrize("mutate, needle", [
    (lambda d: d["marks"][0]["counties"].pop(), "want 8"),
    (lambda d: d["marks"][2]["counties"].__setitem__(0, 81.0), "want 8"),
    (lambda d: d["marks"][1].__setitem__("date", "1086.09.16"), "not 7300 days"),
    (lambda d: d.__setitem__("extra", 1), "missing or unknown"),
    (lambda d: d["start"].pop("save"), "missing or unknown"),
    (lambda d: d.__setitem__("schema", "ck3bench.baseline/1"), "schema"),
    (lambda d: d["marks"].reverse(), "not after"),
    (lambda d: d["marks"][0]["counties"].__setitem__(0, 76), "the series reads"),     # a mark the series contradicts
    (lambda d: _game(d)["day_gaps"].pop(), "day gaps"),
    (lambda d: _game(d)["day_gaps"].__setitem__(3, 0), "day gaps"),
    (lambda d: _game(d)["realm_counties"][0].__setitem__(1, 10 ** 6), "runs hold"),
    (lambda d: _game(d)["line_status"].__setitem__(0, ["fine", 1300]), "not [value"),
    (lambda d: d["series"]["games"].pop(), "series holds 7 games"),
    (lambda d: d["series"].pop("rule"), "missing or unknown"),
])
def test_a_bad_file_is_refused_never_repaired(mutate, needle):
    d = copy.deepcopy(_doc())
    mutate(d)
    with pytest.raises(EnvRefusal) as e:
        bl.baseline_from_dict(d)
    assert e.value.code == bl.R_BASELINE_FILE and needle in e.value.detail, e.value.detail


def test_the_series_covers_every_game_whole_and_states_its_sampling():
    b = bl.load_baseline()
    assert len(b.series) == 8 and all(len(g.days) == 1300 and g.days[0] == 0 and g.days[-1] == 36500
                                      for g in b.series)
    s = b.doc["series"]["sampling"]
    assert s["gap_days"] == {"min": 1, "median": 30.0, "max": 52} and s["last_day"] == 36500
    assert all(set(g.line) == {"following"} for g in b.series)    # no line ended in this set


def test_the_nearest_sample_rule_takes_the_earlier_on_a_tie():
    g = bl.GameSeries(1, (0, 10, 20), (84, 80, 70), ("following",) * 3)
    assert [g.nearest(d) for d in (0, 4, 5, 6, 10, 15, 16, 20)] == [0, 0, 0, 1, 1, 1, 2, 2]


def test_any_horizon_the_series_covers_is_read_with_its_gap():
    """A horizon that is not one of the eight marks is read, each game's
    distance to its nearest sample reported; past the last sample it is
    refused."""
    r = bl.reading("france1066-treasury-2y")
    h = r["horizon"]
    assert h["read_days"] == 730 and h["matches_scenario"] is True and h["read_date"] == "1068.09.15"
    assert h["per_game_gap_days"] == [-10, -10, -9, -9, -9, -10, -10, -10] and h["max_abs_gap_days"] == 10
    assert r["realm_counties"]["values"] == [84] * 8
    assert not hasattr(bl, "R_BASELINE_HORIZON")
    r = bl.reading("france1066-hold-10y", years=11)
    assert r["horizon"]["matches_scenario"] is False and r["horizon"]["days_apart"] == 365
    with pytest.raises(EnvRefusal) as e:
        bl.reading("france1066-hold-10y", years=101)
    assert e.value.code == bl.R_BASELINE_BEYOND and "36500" in e.value.detail
    d = copy.deepcopy(BUILTIN_DICTS["france1066-hold-10y"])
    d.update(id="too-long", horizon_days=36865, cycle_days=365)
    with pytest.raises(EnvRefusal) as e:
        bl.reading(scenario_from_dict(d))
    assert e.value.code == bl.R_BASELINE_BEYOND


def test_a_reading_between_marks_takes_each_games_nearest_sample():
    """At 3600 game-days (50 days before the 10-year mark) the per-game series
    read 75, 88, 81, 81, 75, 75, 75, 81."""
    assert [r["realm_counties"] for r in bl.load_baseline().at(3600)] == [75, 88, 81, 81, 75, 75, 75, 81]


def test_the_ten_year_task_is_read_at_its_own_horizon():
    """france1066-hold-10y ends on the set's 10-year mark (3650 days, 1076.09.15)."""
    r = bl.reading("france1066-hold-10y")
    assert r["horizon"]["read_date"] == "1076.09.15" and r["horizon"]["matches_scenario"] is True
    assert r["realm_counties"]["values"] == [75, 88, 81, 81, 75, 75, 76, 81] and r["games"] == 8
    assert r["two_peaked"] is False
    assert r["noop_return"]["state"] == "read"
    assert r["noop_return"]["values"] == [v - 84 for v in (75, 88, 81, 81, 75, 75, 76, 81)]
    assert r["reward_terms"]["line_survival"]["per_game"] == [0.0] * 8


def test_the_goal_task_replays_the_envs_own_end():
    """france1066-gain-county-5y ends a seat on the first step its realm
    exceeds the start: one game (game 2) reached it in the series."""
    r = bl.reading("france1066-gain-county-5y")
    ret = r["noop_return"]
    assert ret["values"] == [0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
    assert ret["ends"][1] == "goal_reached" and ret["steps"][1] < 25 and ret["ends"].count("horizon") == 7


def test_a_long_task_gets_the_noop_return():
    d = copy.deepcopy(BUILTIN_DICTS["france1066-hold-10y"])
    d.update(id="hold-25y-counties", horizon_days=9125, cycle_days=365,
             reward={"terms": [{"name": "realm_counties_delta", "weight": 1.0}], "team": "none"})
    r = bl.reading(scenario_from_dict(d))
    assert r["horizon"]["matches_scenario"] is True and r["two_peaked"] is False
    assert r["noop_return"]["values"] == [v - 84 for v in (81, 88, 83, 82, 1, 75, 83, 87)]
    assert r["noop_return"]["mean"] == -11.5
    d.update(horizon_days=18250, cycle_days=365)
    r = bl.reading(scenario_from_dict(d))
    assert r["two_peaked"] is True and r["realm_counties"]["under_10"] == 3


def test_metrics_the_set_did_not_read_are_absent_with_the_reason():
    r = bl.reading("france1066-treasury-2y")
    assert r["reward_terms"]["gold_delta"] == {"state": "absent", "why": "the set did not read gold"}
    assert r["noop_return"]["state"] == "absent"
    r = bl.reading("france1066-council-10y")
    assert r["seat"] == "king" and r["noop_return"]["state"] == "read"
    assert set(r["noop_return"]["other_seats"]) == {f"duke{i}" for i in range(1, 6)}


def test_a_task_from_another_start_has_no_baseline():
    for name in ("fake-solo", "fake-duel"):
        with pytest.raises(EnvRefusal) as e:
            bl.reading(name, years=10)
        assert e.value.code == bl.R_BASELINE_NONE
    d = copy.deepcopy(BUILTIN_DICTS["france1066-hold-10y"])
    d["start"] = {"save": "other.ck3", "date": "1066.09.15"}
    with pytest.raises(EnvRefusal) as e:
        bl.reading(scenario_from_dict(d), years=10)
    assert e.value.code == bl.R_BASELINE_NONE


def test_the_cli_reads_and_refuses(capsys):
    assert cli.main(["baseline", "france1066-hold-10y", "--years", "25", "--compact"]) == cli.EXIT_OK
    row = json.loads(capsys.readouterr().out)
    assert row["realm_counties"]["mean"] == 72.5 and row["rests_on"].startswith("8 games")
    assert cli.main(["baseline", "france1066-hold-10y", "--compact"]) == cli.EXIT_OK
    assert json.loads(capsys.readouterr().out)["horizon"]["matches_scenario"] is True
    assert cli.main(["baseline", "france1066-treasury-2y", "--compact"]) == cli.EXIT_OK
    capsys.readouterr()
    assert cli.main(["baseline", "france1066-hold-10y", "--years", "200", "--compact"]) == cli.EXIT_REFUSED
    assert json.loads(capsys.readouterr().out)["code"] == bl.R_BASELINE_BEYOND
    assert cli.main(["baseline", "--marks", "--compact"]) == cli.EXIT_OK
    row = json.loads(capsys.readouterr().out)
    assert [m["years"] for m in row["marks"]][-1] == 100 and row["readable"]["to_days"] == 36500
    assert cli.main(["baseline"]) == cli.EXIT_REFUSED
    assert json.loads(capsys.readouterr().out)["code"] == "bad_request"
