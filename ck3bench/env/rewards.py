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
`line_survival`).
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
           "county_goal_met", "reward_unavailable_detail"]
