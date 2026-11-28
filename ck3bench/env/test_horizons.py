"""Every built-in task's horizon agrees with its name, its description and the
shipped baseline's marks, on the env's own calendar (365 days a year).

A task named `-Ny` runs N game-years: `horizon_days == N * 365` and it ends on
the start date's anniversary. When the shipped baseline for its start was read
at N game-years, the task ends ON that mark (same days, same date), so
`ck3bench env baseline TASK` reads it without `--years`. A description that
states steps x days, a number of game-years or the baseline's spread states
what the declaration and the shipped file say."""
from __future__ import annotations

import re

import pytest

from ck3bench.env import baselines, calendar
from ck3bench.env.errors import EnvRefusal
from ck3bench.env.scenario import BUILTIN_DICTS, builtin

YEARS_IN_ID = re.compile(r"-(\d+)y(?:-wake\d*)?$")   # a wake twin keeps its horizon in its id
WORDS = {"One": 1, "Two": 2, "Three": 3, "Four": 4, "Five": 5, "Ten": 10, "Twenty": 20, "Twenty-five": 25,
         "Thirty": 30}
STEPS = re.compile(r"(\d+) steps of (\d+) (?:game-)?days")
YEARS_WORD = re.compile(r"\b(" + "|".join(WORDS) + r") game-years?\b")
SPREAD = re.compile(r"read (\d+) to (\d+) counties at (\d+) years from (\d+)")


def _years(name: str):
    m = YEARS_IN_ID.search(name)
    return int(m.group(1)) if m else None


def _baseline(sc):
    try:
        return baselines.baseline_for(sc)
    except EnvRefusal as e:
        assert e.code == baselines.R_BASELINE_NONE
        return None


@pytest.mark.parametrize("name", sorted(BUILTIN_DICTS))
def test_a_named_horizon_is_that_many_game_years(name):
    sc = builtin(name)
    years = _years(name)
    if years is None:
        return
    assert sc.horizon_days == years * calendar.DAYS_PER_YEAR, (name, sc.horizon_days)
    y, rest = sc.start_date.split(".", 1)
    assert sc.end_date == f"{int(y) + years:04d}.{rest}", (name, sc.end_date)


@pytest.mark.parametrize("name", sorted(BUILTIN_DICTS))
def test_a_horizon_the_baseline_was_read_at_lands_on_its_mark(name):
    sc = builtin(name)
    years, b = _years(name), _baseline(sc)
    if years is None or b is None or years not in [m.years for m in b.marks]:
        return
    mark = b.mark(years)
    assert (sc.horizon_days, sc.end_date) == (mark.days, mark.date), name
    r = baselines.reading(sc)                     # no --years: the task's own horizon is a mark
    assert r["horizon"]["matches_scenario"] is True and r["horizon"]["read_date"] == mark.date
    assert r["realm_counties"]["values"] == list(mark.counties)


@pytest.mark.parametrize("name", sorted(BUILTIN_DICTS))
def test_the_description_states_what_the_declaration_says(name):
    sc = builtin(name)
    for n, d in STEPS.findall(sc.description):
        assert (int(n), int(d)) == (sc.n_steps, sc.cycle_days), (name, sc.description)
    for w in YEARS_WORD.findall(sc.description):
        assert WORDS[w] == _years(name), (name, w)
    for lo, hi, at, start in SPREAD.findall(sc.description):
        mark = baselines.baseline_for(sc).mark(int(at))
        assert (int(lo), int(hi)) == (min(mark.counties), max(mark.counties)), (name, mark.counties)
        assert int(start) == baselines.baseline_for(sc).start["realm_counties"]


def test_the_checks_are_not_vacuous():
    named = [n for n in BUILTIN_DICTS if _years(n) is not None]
    on_mark = [n for n in named if (b := _baseline(builtin(n))) is not None
               and _years(n) in [m.years for m in b.marks]]
    stated = [n for n in BUILTIN_DICTS if STEPS.search(builtin(n).description)]
    assert len(named) >= 4 and set(on_mark) >= {"france1066-hold-10y", "france1066-council-10y"}
    assert len(stated) >= 2 and any(SPREAD.search(builtin(n).description) for n in BUILTIN_DICTS)


def test_control_a_ten_year_task_that_is_ten_thirty_day_years_is_red():
    """CONTROL: a "10-year" task of 20 steps of 180 days (3600 game-days)
    fails the whole-year check, and the baseline reads it at 3600 days, not at
    the 10-year mark (it says so)."""
    d = dict(BUILTIN_DICTS["france1066-hold-10y"], horizon_days=3600, cycle_days=180)
    from ck3bench.env.scenario import scenario_from_dict
    sc = scenario_from_dict(d)
    assert sc.horizon_days != 10 * calendar.DAYS_PER_YEAR
    r = baselines.reading(sc)
    assert r["horizon"]["read_days"] == 3600 and r["horizon"]["read_date"] != baselines.load_baseline().mark(10).date
