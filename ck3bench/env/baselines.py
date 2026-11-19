"""Baselines: what the game's own AI does on a task when nobody steers.

A baseline is a shipped data file (`baselines_*.json` beside this module)
holding per-game readings of an unsteered set, with its provenance: the
toolkit, the game build, the start save, what was sampled and how. Nothing in
it is estimated or filled in. What the set did not read is listed under
`absent`, and a question it cannot answer is refused by name.

    from ck3bench.env.baselines import reading
    reading("france1066-hold-10y")             # the task's own horizon
    reading("france1066-treasury-2y", years=3) # realm counties at another time

The one baseline shipped is `france1066-unsteered`: eight 100-game-year games
from the 1066 France start, no seat. The file carries each game's whole
sampled series (realm counties and the line watch's status, one sample about
every 30 game-days), so a reading can be taken at ANY time the series covers,
by one rule: each game's NEAREST sample to the time asked (a tie takes the
earlier one), with the distance in game-days reported beside every value. A
time past the last sample is refused. The file also states eight marks (10 to
100 game-years) read from the same series by the last sample on or before
each; the reader refuses the file unless they agree with the nearest-sample
rule.

A task's noop return is the task's own reward replayed over the series: at
each of its step boundaries the nearest sample stands for the world the env
would read there, the step's reward is computed by the task's own reward
declaration, and the episode ends where the env would end it (the horizon, the
goal, or the line ending). Eight games give a rough spread; from about year 40
the outcome is two-peaked (France held, or the line holding almost nothing),
so a mean there describes neither peak, and the reading says so.
"""
from __future__ import annotations

import json
import math
import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from . import calendar
from .errors import EnvRefusal
from .rewards import RewardContext, RewardUnavailable
from .scenario import Scenario, resolve
from .types import METRIC_OK, Metrics

#: Refusals of this module (EnvRefusal codes, leg "baseline").
R_BASELINE_UNKNOWN = "baseline_unknown"
R_BASELINE_FILE = "baseline_file_invalid"
R_BASELINE_NONE = "baseline_none_for_scenario"
R_BASELINE_BEYOND = "baseline_beyond_series"

SCHEMA = "ck3bench.baseline/2"
HERE = Path(__file__).resolve().parent
#: baseline id -> its data file beside this module.
FILES: dict[str, str] = {"france1066-unsteered": "baselines_france1066.json"}
#: Quantiles every reading reports, and how they are computed.
QUANTILES = (0.1, 0.25, 0.5, 0.75, 0.9)
QUANTILE_METHOD = ("linear interpolation between order statistics (Hyndman-Fan type 7, numpy's default): "
                   "q(p) = x[floor(h)] + (h - floor(h)) * (x[floor(h)+1] - x[floor(h)]), h = (n-1)p")
#: A reading's "under 10 counties" count: the set's own statistic for a collapsed line.
COLLAPSED_BELOW = 10
#: How a time is read from a game's series (the file states it too).
SERIES_RULE = ("each game's nearest sample to the time asked (a tie takes the earlier sample); the distance "
               "in game-days is reported beside the value; a time past the series' last sample is refused")
#: The line watch's statuses the series may hold (the bridge's played-line watch).
LINE_FOLLOWING, LINE_ENDED = "following", "line_ended"
LINE_STATUSES = ("following", "successor_pending", "line_ended", "unreadable", "ambiguous", "not_opened")
#: From about this many game-years the set's outcome is two-peaked.
TWO_PEAKED_FROM_YEARS = 40

_TOP = {"schema": str, "id": str, "title": str, "policy": str, "policy_meaning": str, "games": int,
        "start": dict, "provenance": dict, "metric": dict, "marks": list, "stated_summary": dict,
        "games_detail": list, "shape": str, "absent": dict, "series": dict}
_START = {"date": str, "save": str, "save_sha256": str, "save_note": str, "followed_title": str,
          "followed_character_id": int, "realm_counties": int}
_MARK = {"years": int, "date": str, "days": int, "counties": list}
_SERIES = {"rule": str, "sampling": dict, "encoding": str, "games": list}
_GAME = {"game": int, "episode_id": str, "source": str, "source_sha256": str, "samples": int, "day_gaps": list,
         "realm_counties": list, "line_status": list}


@dataclass(frozen=True)
class Mark:
    years: int
    date: str
    days: int
    counties: tuple[int, ...]


@dataclass(frozen=True)
class GameSeries:
    game: int
    days: tuple[int, ...]
    counties: tuple[int, ...]
    line: tuple[str, ...]

    def nearest(self, day: int) -> int:
        """The index of the nearest sample to `day` (a tie takes the earlier)."""
        lo, hi = 0, len(self.days) - 1
        while lo < hi:                                   # first sample at or after `day`
            mid = (lo + hi) // 2
            if self.days[mid] < day:
                lo = mid + 1
            else:
                hi = mid
        if lo > 0 and day - self.days[lo - 1] <= self.days[lo] - day:
            return lo - 1
        return lo


@dataclass(frozen=True)
class Baseline:
    id: str
    games: int
    start: Mapping[str, Any]
    marks: tuple[Mark, ...]
    series: tuple[GameSeries, ...]
    doc: Mapping[str, Any]

    @property
    def last_day(self) -> int:
        return min(g.days[-1] for g in self.series)

    def at(self, day: int) -> list[dict[str, Any]]:
        """Per game: the nearest sample to `day` game-days after the start."""
        if not isinstance(day, int) or isinstance(day, bool) or day < 0 or day > self.last_day:
            raise EnvRefusal(R_BASELINE_BEYOND, f"{self.id} covers 0 to {self.last_day} game-days after "
                                                f"{self.start['date']}; asked for {day!r}", "baseline")
        out = []
        for g in self.series:
            i = g.nearest(day)
            out.append({"game": g.game, "realm_counties": g.counties[i], "line_status": g.line[i],
                        "sample_day": g.days[i], "sample_date": calendar.add_days(self.start["date"], g.days[i]),
                        "gap_days": g.days[i] - day})
        return out

    def mark(self, years: int) -> Mark:
        """Realm counties at `years` game-years, read from the series."""
        day = years * calendar.DAYS_PER_YEAR
        return Mark(years, calendar.add_days(self.start["date"], day), day,
                    tuple(r["realm_counties"] for r in self.at(day)))


def _bad(detail: str) -> EnvRefusal:
    return EnvRefusal(R_BASELINE_FILE, detail, "baseline")


def _typed(d: Any, keys: Mapping[str, type], where: str) -> None:
    if not isinstance(d, Mapping):
        raise _bad(f"{where} is not an object")
    if set(d) != set(keys):
        raise _bad(f"{where}: keys {sorted(set(d) ^ set(keys))} missing or unknown")
    for k, t in keys.items():
        if not isinstance(d[k], t) or (t is int and isinstance(d[k], bool)):
            raise _bad(f"{where}.{k} is {type(d[k]).__name__}, not {t.__name__}")


def _nonneg_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and v >= 0


def _unrun(runs: Any, n: int, ok, where: str) -> list:
    if not isinstance(runs, list):
        raise _bad(f"{where} is not a list of runs")
    out: list = []
    for r in runs:
        if not (isinstance(r, list) and len(r) == 2 and ok(r[0]) and isinstance(r[1], int)
                and not isinstance(r[1], bool) and r[1] > 0):
            raise _bad(f"{where}: run {r!r} is not [value, count >= 1]")
        out += [r[0]] * r[1]
    if len(out) != n:
        raise _bad(f"{where}: the runs hold {len(out)} samples, the game {n}")
    return out


def _series_from(doc: Mapping[str, Any], n_games: int) -> tuple[GameSeries, ...]:
    s = doc["series"]
    _typed(s, _SERIES, "baseline.series")
    if len(s["games"]) != n_games:
        raise _bad(f"series holds {len(s['games'])} games, the file declares {n_games}")
    out = []
    for i, g in enumerate(s["games"]):
        where = f"series.games[{i}]"
        _typed(g, _GAME, where)
        if g["game"] != i + 1:
            raise _bad(f"{where}.game is {g['game']}, want {i + 1}")
        n = g["samples"]
        gaps = g["day_gaps"]
        if n < 2 or len(gaps) != n - 1 or not all(isinstance(x, int) and not isinstance(x, bool) and x > 0
                                                   for x in gaps):
            raise _bad(f"{where}: want {n - 1} positive integer day gaps for {n} samples")
        days = [0]
        for x in gaps:
            days.append(days[-1] + x)
        counties = _unrun(g["realm_counties"], n, _nonneg_int, f"{where}.realm_counties")
        line = _unrun(g["line_status"], n, lambda v: v in LINE_STATUSES, f"{where}.line_status")
        out.append(GameSeries(i + 1, tuple(days), tuple(counties), tuple(line)))
    return tuple(out)


def baseline_from_dict(doc: Mapping[str, Any]) -> Baseline:
    """The one reader: every key typed, each game's series whole (its sample
    days increasing from the start, one realm value and one line status per
    sample), and every stated mark equal to what the series reads there. A
    file that does not hold is refused, never repaired."""
    _typed(doc, _TOP, "baseline")
    if doc["schema"] != SCHEMA:
        raise _bad(f"schema {doc['schema']!r}, want {SCHEMA!r}")
    _typed(doc["start"], _START, "baseline.start")
    n = doc["games"]
    series = _series_from(doc, n)
    if any(g.counties[0] != doc["start"]["realm_counties"] for g in series):
        raise _bad(f"a game's first sample is not the start's {doc['start']['realm_counties']} counties")
    marks: list[Mark] = []
    for i, m in enumerate(doc["marks"]):
        _typed(m, _MARK, f"baseline.marks[{i}]")
        vals = m["counties"]
        if len(vals) != n or not all(_nonneg_int(v) for v in vals):
            raise _bad(f"marks[{i}] ({m['years']} y) holds {vals!r}: want {n} non-negative integers, one per game")
        if calendar.ordinal(m["date"]) is None or \
                calendar.days_between(doc["start"]["date"], m["date"]) != m["days"]:
            raise _bad(f"marks[{i}]: {m['date']} is not {m['days']} days after {doc['start']['date']}")
        if m["days"] != m["years"] * calendar.DAYS_PER_YEAR:
            raise _bad(f"marks[{i}]: {m['days']} days is not {m['years']} game-years")
        if marks and m["days"] <= marks[-1].days:
            raise _bad(f"marks[{i}] is not after marks[{i - 1}]")
        marks.append(Mark(m["years"], m["date"], m["days"], tuple(vals)))
    if not marks:
        raise _bad("no marks")
    b = Baseline(doc["id"], n, dict(doc["start"]), tuple(marks), series, doc)
    for m in marks:
        got = b.mark(m.years).counties
        if got != m.counties:
            raise _bad(f"the {m.years}-year mark states {list(m.counties)}, the series reads {list(got)}")
    return b


def load_baseline(baseline_id: str = "france1066-unsteered", path: Optional[str | Path] = None) -> Baseline:
    """A shipped baseline by id, or a baseline file by path."""
    if path is None:
        if baseline_id not in FILES:
            raise EnvRefusal(R_BASELINE_UNKNOWN, f"{baseline_id!r} is not one of {sorted(FILES)}", "baseline")
        path = HERE / FILES[baseline_id]
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise _bad(f"{path}: {e}") from None
    b = baseline_from_dict(doc)
    if path == HERE / FILES.get(baseline_id, "") and b.id != baseline_id:
        raise _bad(f"{path} holds {b.id!r}, not {baseline_id!r}")
    return b


# ── statistics ─────────────────────────────────────────────────────────────

def quantile(sorted_vals: Sequence[float], p: float) -> float:
    if not sorted_vals:
        raise ValueError("no values")
    h = (len(sorted_vals) - 1) * p
    lo = math.floor(h)
    hi = min(lo + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (h - lo) * (sorted_vals[hi] - sorted_vals[lo])


def summarize(values: Sequence[float]) -> dict[str, Any]:
    """n, mean, sample SD (n-1), median, quantiles, min, max and how many read
    under 10. Rounded to 3 decimals for reading; `values` are exact."""
    xs = sorted(values)
    n = len(xs)
    if n < 2:
        raise ValueError("a spread needs at least two games")
    return {"n": n, "values": list(values), "mean": round(statistics.fmean(xs), 3),
            "sd": round(statistics.stdev(xs), 3), "median": round(statistics.median(xs), 3),
            "quantiles": {f"p{round(p * 100):02d}": round(quantile(xs, p), 3) for p in QUANTILES},
            "quantile_method": QUANTILE_METHOD, "min": xs[0], "max": xs[-1],
            f"under_{COLLAPSED_BELOW}": sum(1 for x in xs if x < COLLAPSED_BELOW)}


# ── a reading for a task ───────────────────────────────────────────────────

def baseline_for(sc: Scenario) -> Baseline:
    """The shipped baseline whose start is this scenario's (same start date,
    same save file, the followed character one of its seats). Refused when
    none is: a baseline from another start says nothing about this task."""
    seen = []
    seat_ids = {s.character_id for s in sc.seats}
    for bid in FILES:
        b = load_baseline(bid)
        if (sc.backend == "ck3" and sc.start_date == b.start["date"] and sc.start_save == b.start["save"]
                and b.start["followed_character_id"] in seat_ids):
            return b
        seen.append(f"{bid} (start {b.start['date']}, save {b.start['save']}, "
                    f"character {b.start['followed_character_id']})")
    raise EnvRefusal(R_BASELINE_NONE, f"{sc.id} (backend {sc.backend}, start {sc.start_date}, save "
                                      f"{sc.start_save!r}) has no shipped baseline; the shipped ones: {seen}",
                     "baseline")


def _metrics(b: Baseline, row: Mapping[str, Any]) -> Metrics:
    """What the env's metric read gives at a sample: realm counties (0 once the
    line has ended, the meter's rule) and whether the line lives. Gold and
    prestige were not read by the set: their status is `not_declared`."""
    ended = row["line_status"] == LINE_ENDED
    m = Metrics(row["sample_date"], b.start["followed_character_id"], line_status=row["line_status"])
    m.values["realm_counties"], m.status["realm_counties"] = (0 if ended else row["realm_counties"]), METRIC_OK
    if row["line_status"] in (LINE_FOLLOWING, LINE_ENDED, "successor_pending"):
        m.values["line_alive"], m.status["line_alive"] = not ended, METRIC_OK
    return m


def replay(sc: Scenario, b: Baseline) -> dict[str, Any]:
    """The task's own reward over each game's series at the task's step
    boundaries (nearest sample at each), ending where the env would end it."""
    if sc.horizon_days > b.last_day:
        raise EnvRefusal(R_BASELINE_BEYOND, f"{sc.id} runs {sc.horizon_days} game-days; {b.id} covers "
                                            f"{b.last_day}", "baseline")
    start_row = {"realm_counties": b.start["realm_counties"], "line_status": LINE_FOLLOWING,
                 "sample_date": b.start["date"]}
    per_game, absent, max_gap = [], {}, 0
    for gi in range(b.games):
        start = prev = _metrics(b, start_row)
        total: Optional[float] = 0.0
        terms_sum = {n: 0.0 for n, _ in sc.reward.terms}
        end, steps = "horizon", 0
        for k in range(1, sc.n_steps + 1):
            row = b.at(k * sc.cycle_days)[gi]
            max_gap = max(max_gap, abs(row["gap_days"]))
            cur = _metrics(b, row)
            ended = row["line_status"] == LINE_ENDED
            try:
                goal = sc.goal == "county_gain" and not ended and \
                    cur.values["realm_counties"] > start.values["realm_counties"]
                own, parts = sc.reward.seat_reward(RewardContext(start, prev, cur, final=ended or goal or
                                                                 k == sc.n_steps, line_ended=ended))
            except RewardUnavailable as e:
                absent[e.term] = e.metric
                total = None
                break
            total += own
            for n_, v in parts.items():
                terms_sum[n_] += v
            prev, steps = cur, k
            if ended or goal:
                end = "line_ended" if ended else "goal_reached"
                break
        per_game.append({"game": gi + 1, "return": total, "steps": steps, "end": end, "terms": terms_sum})
    return {"per_game": per_game, "absent_terms": absent, "max_gap_days": max_gap}


def _absent_reason(b: Baseline, metric: str) -> str:
    return b.doc["absent"].get(metric) or f"the set did not read {metric}"


def reading(scenario: str | Scenario, years: Optional[int] = None) -> dict[str, Any]:
    """The baseline for a task: realm counties at the task's horizon (or at
    `years` game-years), each game's nearest sample with its distance in
    game-days, and the task's noop return replayed over the series."""
    sc = resolve(scenario) if isinstance(scenario, str) else scenario
    b = baseline_for(sc)
    day = sc.horizon_days if years is None else years * calendar.DAYS_PER_YEAR
    rows = b.at(day)
    rc = summarize([r["realm_counties"] for r in rows])
    start = b.start["realm_counties"]
    seat = next(s.name for s in sc.seats if s.character_id == b.start["followed_character_id"])
    others = [s.name for s in sc.seats if s.name != seat]
    rp = replay(sc, b)
    terms: dict[str, Any] = {}
    for name, _w in sc.reward.terms:
        if name in rp["absent_terms"]:
            terms[name] = {"state": "absent", "why": _absent_reason(b, rp["absent_terms"][name])}
        else:
            vals = [g["terms"][name] for g in rp["per_game"]]
            terms[name] = {"state": "read", "per_game": vals,
                           **{k: v for k, v in summarize(vals).items() if k in ("mean", "sd", "median", "min", "max")}}
    if rp["absent_terms"]:
        ret: dict[str, Any] = {"state": "absent", "why": "; ".join(
            f"{t}: {_absent_reason(b, m)}" for t, m in rp["absent_terms"].items())}
    else:
        rets = [g["return"] for g in rp["per_game"]]
        ret = {"state": "read", **{k: v for k, v in summarize(rets).items() if k in ("n", "values", "mean", "sd")},
               "ends": [g["end"] for g in rp["per_game"]], "steps": [g["steps"] for g in rp["per_game"]],
               "max_gap_days": rp["max_gap_days"],
               "rule": f"the task's own reward at each of its {sc.n_steps} step boundaries, read at each game's "
                       f"nearest sample (at most {rp['max_gap_days']} game-days away)"}
    if others:
        ret = {**ret, "seat": seat, "other_seats": {o: f"absent: the set read the realm of {seat!r} only"
                                                    for o in others}}
    gaps = [r["gap_days"] for r in rows]
    peaked = day >= TWO_PEAKED_FROM_YEARS * calendar.DAYS_PER_YEAR
    return {
        "ok": True, "baseline": b.id, "scenario": sc.id, "seat": seat, "policy": b.doc["policy"],
        "games": b.games, "rests_on": f"{b.games} games; a rough spread, not a tight one",
        "horizon": {"scenario_days": sc.horizon_days, "scenario_end_date": sc.end_date,
                    "read_days": day, "read_date": calendar.add_days(b.start["date"], day),
                    "matches_scenario": day == sc.horizon_days, "days_apart": day - sc.horizon_days,
                    "rule": SERIES_RULE, "per_game_gap_days": gaps, "max_abs_gap_days": max(abs(g) for g in gaps),
                    "samples": [{k: r[k] for k in ("game", "sample_date", "gap_days")} for r in rows]},
        "realm_counties": {"start": start, **rc, "mean_change": round(rc["mean"] - start, 3)},
        "line_status": sorted({r["line_status"] for r in rows}),
        "two_peaked": peaked,
        "shape": (b.doc["shape"] if peaked else
                  f"at {day} game-days the games have not split into two peaks (that happens from about "
                  f"year {TWO_PEAKED_FROM_YEARS}); " + b.doc["shape"]),
        "reward_terms": terms,
        "noop_return": ret,
        "sampling": dict(b.doc["series"]["sampling"]),
        "provenance": {"start": dict(b.start), **b.doc["provenance"], "metric": b.doc["metric"]},
        "absent": dict(b.doc["absent"]),
    }


def list_marks(baseline_id: str = "france1066-unsteered") -> dict[str, Any]:
    b = load_baseline(baseline_id)
    return {"ok": True, "baseline": b.id, "games": b.games, "title": b.doc["title"],
            "start": dict(b.start), "marks": [{"years": m.years, "date": m.date, "days": m.days} for m in b.marks],
            "readable": {"from_days": 0, "to_days": b.last_day, "rule": SERIES_RULE},
            "sampling": dict(b.doc["series"]["sampling"]), "absent": dict(b.doc["absent"])}


__all__ = ["Baseline", "Mark", "GameSeries", "load_baseline", "baseline_from_dict", "summarize", "quantile",
           "reading", "replay", "baseline_for", "list_marks", "R_BASELINE_UNKNOWN", "R_BASELINE_FILE",
           "R_BASELINE_NONE", "R_BASELINE_BEYOND", "SERIES_RULE"]
