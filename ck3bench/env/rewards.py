"""Rewards, declared by name and read from game state.

A scenario declares its reward as weighted TERMS (`[[reward.terms]]`, each a
name from `TERMS` and a weight) and a TEAM rule. A term is a pure function of
the seat's metrics at the episode start, before the step and after it. Every
term names the metrics it needs; when one of them did not read `ok`, the term
does not produce a number: `RewardUnavailable` is raised, and the environment
truncates the episode with `reward_unreadable` and reports the step's reward
as NaN (loud in any sum), never 0.

Terms are dense (`*_delta`: the change over the step, so an episode's sum is
the change over the episode) or sparse (`*_terminal`, `county_gain_goal`,
`target_title_gained`, `line_survival`).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Mapping, Optional, Sequence

from .errors import R_REWARD_UNKNOWN, EnvRefusal
from .types import Metrics


class RewardUnavailable(Exception):
    def __init__(self, term: str, metric: str, status: str):
        super().__init__(f"{term}: metric {metric} is {status}")
        self.term, self.metric, self.status = term, metric, status


@dataclass(frozen=True)
class RewardContext:
    start: Metrics           # the seat's metrics at reset
    prev: Metrics            # before this step
    cur: Metrics             # after this step
    final: bool              # this step ends the seat's episode (any reason)
    line_ended: bool         # the seat's line ended on this step
    #: the seat's line held the target title at the start or at the end of an earlier step of this episode (the
    #: env's latch: it survives the title's loss and a change of ruler); read by `target_title_first_gained` only
    title_held_before: bool = False


def _num(m: Metrics, name: str, term: str) -> float:
    v, st = m.get(name)
    if v is None:
        raise RewardUnavailable(term, name, st)
    return float(v)


def _delta(metric: str) -> Callable[[RewardContext], float]:
    def f(c: RewardContext) -> float:
        return _num(c.cur, metric, f"{metric}_delta") - _num(c.prev, metric, f"{metric}_delta")
    return f


def _terminal(metric: str) -> Callable[[RewardContext], float]:
    def f(c: RewardContext) -> float:
        if not c.final:
            return 0.0
        return _num(c.cur, metric, f"{metric}_terminal") - _num(c.start, metric, f"{metric}_terminal")
    return f


def _line_survival(c: RewardContext) -> float:
    """-1 on the step the played line ends, else 0."""
    return -1.0 if c.line_ended else 0.0


def _county_gain_goal(c: RewardContext) -> float:
    """+1 on the first step the realm holds more counties than at the start
    (the scenario's goal predicate ends the seat there)."""
    return 1.0 if county_goal_met(c.start, c.cur) else 0.0


def county_goal_met(start: Metrics, cur: Metrics) -> bool:
    return _num(cur, "realm_counties", "county_gain_goal") > _num(start, "realm_counties", "county_gain_goal")


def _held(m: Metrics, term: str) -> bool:
    v, st = m.get("holds_target_title")
    if v is None or not isinstance(v, bool):
        raise RewardUnavailable(term, "holds_target_title", st if v is None else "not_a_bool")
    return v


def _target_title_gained(c: RewardContext) -> float:
    """+1 on a step that ends with the line's character holding the target
    title when it did not hold it before the step, else 0."""
    return 1.0 if _held(c.cur, "target_title_gained") and not _held(c.prev, "target_title_gained") else 0.0


def _target_title_first_gained(c: RewardContext) -> float:
    """+1 on the first step of the episode that ends with the line's character holding the target title, never
    again: not when the title is lost and regained, nor when an heir regains it (the env's latch,
    RewardContext.title_held_before). A title held at the start is never paid."""
    return 1.0 if _held(c.cur, "target_title_first_gained") and not c.title_held_before else 0.0


#: goal name -> (predicate on (start, cur), the metrics it reads)
GOAL_METRICS: dict[str, tuple[str, ...]] = {"none": (), "county_gain": ("realm_counties",),
                                            "kingdom_held": ("holds_target_title",),
                                            "empire_held": ("holds_target_title",)}


#: line_kept_peacefully: the line passes from ruler to ruler at least this many times (one: the 1066 Lorraine ruler is
#: 63 with one son of 26, so two passes in 25 years were near-impossible; the Carinthia ruler is 47 with sons of 18,
#: 16 and 14; one global constant, the same for every task of the goal)
LINE_KEPT_RULER_CHANGES = 1
GOAL_METRICS["line_kept_peacefully"] = ("line_alive", "ruler_changes", "offensive_wars_declared", "realm_counties")
#: Goals judged at the task's end date only: they say what held over the whole episode, so meeting their legs
#: earlier does not end it (env.py). Every other goal ends the seat on the first step it holds.
HORIZON_GOALS = ("line_kept_peacefully",)
#: Goals met by the target title being held at ANY step by the end date (the env's latch): holding it does not end
#: the episode, which runs to the end date; the goal is judged there, on whether the title was ever held.
ONCE_GOALS = ("kingdom_held_once",)
GOAL_METRICS["kingdom_held_once"] = ("holds_target_title",)


def line_kept_legs(start: Metrics, cur: Metrics) -> dict[str, dict]:
    """The legs of `line_kept_peacefully`, each {value, status, holds}: the line still has a ruler, it has passed
    at least LINE_KEPT_RULER_CHANGES times, no offensive war was declared, and the realm holds at least the
    counties it held at the start. A leg whose metric did not read `ok` has `holds` None (absence, never a
    verdict)."""
    def leg(name: str, m: Metrics, test, want_bool: bool = False) -> dict:
        v, st = m.get(name)
        if v is None:
            return {"value": None, "status": st, "holds": None}
        if want_bool != isinstance(v, bool) or not isinstance(v, (bool, int, float)):
            return {"value": v, "status": "not_a_bool" if want_bool else "not_a_number", "holds": None}
        return {"value": v, "status": st, "holds": bool(test(v))}
    s0 = leg("realm_counties", start, lambda v: True)
    if s0["holds"] is None:
        rc = {"value": cur.get("realm_counties")[0], "status": f"start {s0['status']}", "holds": None}
    else:
        rc = leg("realm_counties", cur, lambda v: v >= s0["value"])
    return {"line_alive": leg("line_alive", cur, lambda v: v is True, want_bool=True),
            "ruler_changes": leg("ruler_changes", cur, lambda v: v >= LINE_KEPT_RULER_CHANGES),
            "offensive_wars_declared": leg("offensive_wars_declared", cur, lambda v: v == 0),
            "realm_counties": {**rc, "start": s0["value"]}}


def goal_met(goal: str, start: Metrics, cur: Metrics, held_before: Optional[bool] = None) -> bool:
    """Whether the scenario's goal holds after a step. `kingdom_held`: the
    line's character holds the target title (a kingdom); `empire_held` the
    same for an empire (the 867 Francia task); `kingdom_held_once`: held now or
    at any earlier step (`held_before`, the env's latch; required for it).
    Raises RewardUnavailable when the goal's metric did not read."""
    if goal in ONCE_GOALS:
        if held_before is None:
            raise EnvRefusal(R_REWARD_UNKNOWN, f"goal {goal!r} is judged on the env's latch; none was given", "reward")
        return _held(cur, f"goal {goal}") or held_before
    if goal == "none":
        return False
    if goal == "county_gain":
        return county_goal_met(start, cur)
    if goal == "line_kept_peacefully":
        # the predicate over its legs; the environment asks it at the end date only (HORIZON_GOALS)
        legs = line_kept_legs(start, cur)
        for name, leg in legs.items():
            if leg["holds"] is None:
                raise RewardUnavailable("goal line_kept_peacefully", name, leg["status"])
        return all(leg["holds"] for leg in legs.values())
    if goal in ("kingdom_held", "empire_held"):
        return _held(cur, f"goal {goal}")
    raise EnvRefusal(R_REWARD_UNKNOWN, f"goal {goal!r} is not one of {sorted(GOAL_METRICS)}", "reward")


#: term name -> (function, the metrics it reads)
TERMS: dict[str, tuple[Callable[[RewardContext], float], tuple[str, ...]]] = {
    "realm_counties_delta": (_delta("realm_counties"), ("realm_counties",)),
    "demesne_counties_delta": (_delta("demesne_counties"), ("demesne_counties",)),
    "gold_delta": (_delta("gold"), ("gold",)),
    "prestige_delta": (_delta("prestige"), ("prestige",)),
    "realm_counties_terminal": (_terminal("realm_counties"), ("realm_counties",)),
    "gold_terminal": (_terminal("gold"), ("gold",)),
    "line_survival": (_line_survival, ("line_alive",)),
    "county_gain_goal": (_county_gain_goal, ("realm_counties",)),
    "dejure_counties_delta": (_delta("dejure_counties"), ("dejure_counties",)),
    "target_title_gained": (_target_title_gained, ("holds_target_title",)),
    "target_title_first_gained": (_target_title_first_gained, ("holds_target_title",)),
}

#: How a seat's reward becomes the reward every seat is handed. `seat` keys the
#: team on ONE seat (`team_seat`, e.g. the king): every live seat is handed that
#: seat's own reward, so a council is paid by the king's realm and nothing else.
TEAM_MODES = ("none", "sum", "mean", "seat")


@dataclass(frozen=True)
class RewardSpec:
    terms: tuple[tuple[str, float], ...]
    team: str = "none"
    team_seat: Optional[str] = None

    def __post_init__(self):
        if not self.terms:
            raise EnvRefusal(R_REWARD_UNKNOWN, "a reward declares at least one term", "reward")
        for name, w in self.terms:
            if name not in TERMS:
                raise EnvRefusal(R_REWARD_UNKNOWN, f"{name!r} is not one of {sorted(TERMS)}", "reward")
            if not isinstance(w, (int, float)) or isinstance(w, bool):
                raise EnvRefusal(R_REWARD_UNKNOWN, f"weight of {name} is not a number", "reward")
        if self.team not in TEAM_MODES:
            raise EnvRefusal(R_REWARD_UNKNOWN, f"team {self.team!r} is not one of {TEAM_MODES}", "reward")
        if (self.team == "seat") != (self.team_seat is not None):
            raise EnvRefusal(R_REWARD_UNKNOWN, "team = 'seat' and team_seat go together "
                                               f"(team {self.team!r}, team_seat {self.team_seat!r})", "reward")

    def metrics(self) -> tuple[str, ...]:
        """The metrics this reward reads (the backend reads only these)."""
        out: list[str] = []
        for name, _ in self.terms:
            for m in TERMS[name][1]:
                if m not in out:
                    out.append(m)
        return tuple(out)

    def seat_reward(self, ctx: RewardContext) -> tuple[float, dict[str, float]]:
        """(total, per-term) for one seat; raises RewardUnavailable."""
        parts = {name: w * TERMS[name][0](ctx) for name, w in self.terms}
        return sum(parts.values()), parts

    def team_rewards(self, own: Mapping[str, float]) -> dict[str, float]:
        if self.team == "none" or not own:
            return dict(own)
        if self.team == "seat":
            if self.team_seat not in own:
                # the environment ends every seat when the keyed seat ends, so
                # this is a caller error, never a reason to hand out a default
                raise EnvRefusal(R_REWARD_UNKNOWN, f"team seat {self.team_seat!r} has no reward this step "
                                                   f"(seats {sorted(own)})", "reward")
            return {k: own[self.team_seat] for k in own}
        total = sum(own.values())
        value = total if self.team == "sum" else total / len(own)
        return {k: value for k in own}

    def to_dict(self) -> dict:
        d = {"terms": [{"name": n, "weight": w} for n, w in self.terms], "team": self.team}
        if self.team_seat is not None:
            d["team_seat"] = self.team_seat
        return d


def spec_from(terms: Sequence[tuple[str, float]], team: str = "none", team_seat: Optional[str] = None
              ) -> RewardSpec:
    return RewardSpec(tuple((str(n), w) for n, w in terms), team, team_seat)


def reward_unavailable_detail(e: RewardUnavailable) -> dict:
    return {"term": e.term, "metric": e.metric, "status": e.status}


__all__ = ["RewardSpec", "RewardContext", "RewardUnavailable", "TERMS", "TEAM_MODES", "spec_from",
           "county_goal_met", "goal_met", "GOAL_METRICS", "reward_unavailable_detail", "HORIZON_GOALS", "ONCE_GOALS",
           "line_kept_legs", "LINE_KEPT_RULER_CHANGES"]
