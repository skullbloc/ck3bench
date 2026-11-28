"""Scenario (task) declarations.

A scenario is everything an episode is reset FROM: the backend it needs, the
start save and date, the seats played (the agents), the horizon and the cycle
length (one step = one cycle of `cycle_days` game-days), the reward, an
optional goal, the tools a seat may call, the mods, and the game speed.

One reader for every source: a TOML file (`load_scenario`) and the built-in
tasks (`BUILTIN`) both go through `scenario_from_dict`, which types every key
and refuses an unknown one, a missing one or a wrong type. Nothing defaults
except the keys documented as optional below.

Every built-in carries `status` (`buildable_now`: the task's declaration is
complete, which says nothing of whether you can run it; `start_availability`
says that) and `status_reason`: what has and has not been run of it is said in
the declaration, not discovered at run time. A game task's reason states one of RUN_STATES: run live with a
model seat, run live with scripted or no-op seats only (`unseated`), or not run
live.
"""
from __future__ import annotations

import copy
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Optional

from . import calendar
from .errors import (R_SCENARIO_KEY, R_SCENARIO_MISSING, R_SCENARIO_SPEED, R_SCENARIO_TYPE,
                     R_SCENARIO_UNKNOWN, R_SCENARIO_VALUE, EnvRefusal)
from .comms import COMMS_TOOLS, CommsDecl, comms_from_dict
from .rewards import GOAL_METRICS, ONCE_GOALS, TEAM_MODES, RewardSpec
from .types import METRIC_NAMES, PUBLIC_TOOLS, SEAT_TOOLS, TARGET_METRICS

BACKENDS = ("ck3", "fake")
GOALS = tuple(GOAL_METRICS)
#: A title key as the game spells it: tier letter, underscore, the name.
TITLE_KEY = re.compile(r"^[bcdek]_[a-z0-9_]+$")
STATUSES = ("buildable_now",)
#: How far a game task has run, as its status_reason states it (README's task table says the same)
RUN_MODEL, RUN_UNSEATED, RUN_NONE = "run live with a model", "run live unseated only", "not run live"
RUN_STATES = (RUN_MODEL, RUN_UNSEATED, RUN_NONE)
SELECTORS = ("character_id", "census_rank", "holder_of")

#: The fastest game speed the bridge reaches. The game's own limit is 5; the bridge caps a speed
#: write at 3 unless the episode DECLARES a faster one, and the env declares the scenario's speed at
#: launch (CK3_EPISODE_GAME_SPEED), so a scenario may ask for 4 or 5. A bridge from before that
#: change writes 3 for a 4 or 5 and the open refuses `speed_not_established` on the read-back.
MAX_GAME_SPEED = 5
SPEED_BOUND_SOURCE = ("the game's own fastest speed is 5; the bridge writes a speed above 3 only when the episode "
                      "declared it")
#: The speed every built-in task runs at (unchanged): the speed the wall-time estimates were measured at.
DEFAULT_GAME_SPEED = 3
#: Clock rates measured through the env on the pause alarm, one game on one host
#: (CK3 1.18.3.1, Linux): game-days per wall second at a speed. Speeds not here
#: were not measured, and a wall-time estimate for them is refused.
MEASURED_GD_PER_S = {3: 1.6, 2: 0.89}


def wall_minutes(horizon_days: int, speed: int) -> int:
    """Wall minutes of clock for `horizon_days` at `speed`, from the measured
    rate (rounded; launch, metric reads and policy time come on top)."""
    rate = MEASURED_GD_PER_S.get(speed)
    if rate is None:
        raise EnvRefusal(R_SCENARIO_VALUE, f"no measured clock rate at speed {speed}; measured: "
                                           f"{sorted(MEASURED_GD_PER_S)}", "scenario")
    return round(horizon_days / rate / 60)


@dataclass(frozen=True)
class SeatDecl:
    """One played seat. Exactly one selector: `character_id` (the ruler at the
    start), `census_rank` (the n-th largest vassal of the first seat at the
    start, read from the world at open by the census rule, census.py) or
    `holder_of` (the holder of that title at the start, confirmed by his name:
    `holder_name`, and `holder_name_key` for a save whose names read as keys;
    census.py select_by_holder)."""
    name: str
    character_id: Optional[int] = None
    census_rank: Optional[int] = None
    role: str = "ruler"
    holder_of: Optional[str] = None
    holder_name: Optional[str] = None
    holder_name_key: Optional[str] = None

    @property
    def selector(self) -> str:
        return next(k for k in SELECTORS if getattr(self, k) is not None)


@dataclass(frozen=True)
class WakeDecl:
    """A WAKE task (wake.py): a seat may end its turn with conditions, and the
    step's clock runs until the first check (every `check_days`) at which one
    holds, or `max_days` (the most any one step may run), or the horizon. A seat
    that sends no conditions takes the scenario's `cycle_days`."""
    check_days: int
    max_days: int
    #: FORCED wakes: events every live seat is woken on whatever it asked (wake.EVENTS); () for none.
    #: A forced event the backend cannot witness refuses the task at reset.
    forced: tuple[str, ...] = ()
    #: STANDING ORDERS (orders.py): a seat may end its turn with lever calls the env sends when their conditions
    #: hold at a check. On by default on every wake task; `orders = false` is the task-level switch that turns
    #: them off (comparison runs, tasks registered before them). Written in the declaration only when off.
    orders: bool = True
    #: ALIGNED checks: the backend stops the clock on the pause alarm at each check date (start +
    #: k * check_days, the step's end last), checks paused, and runs on; the step records each check's intended
    #: date beside the date read (`wake.check_dates`). Off (the default): the checks are made while the clock
    #: runs, whenever a wall-polled read finds the date `check_days` past the last (late by up to the days one
    #: poll covers). A declared speed (`--game-speed`) turns it on; a wake task faster than DEFAULT_GAME_SPEED
    #: must have it. Written in the declaration only when on.
    aligned: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {"check_days": self.check_days, "max_days": self.max_days,
                **({"forced": list(self.forced)} if self.forced else {}),
                **({"orders": False} if not self.orders else {}),
                **({"aligned_checks": True} if self.aligned else {})}


@dataclass(frozen=True)
class Scenario:
    id: str
    description: str
    backend: str
    start_save: Optional[str]          # a FILE NAME in the user's own saves (the user brings the game)
    start_save_sha256: Optional[str]   # when declared, checked before launch
    start_date: str
    seats: tuple[SeatDecl, ...]
    horizon_days: int
    cycle_days: int
    reward: RewardSpec
    goal: str
    tools: tuple[str, ...]
    policy_mod: bool
    proposal_mod: bool
    game_speed: int
    status: str
    status_reason: str
    fake: Mapping[str, Any] = field(default_factory=dict)
    #: the env-owned multi-agent layer (comms.py); None when the scenario declares none
    comms: Optional[CommsDecl] = None
    #: the title the target metrics read (types.TARGET_METRICS), e.g. "k_ireland"; None when none is read
    target_title: Optional[str] = None
    #: a wake task's cadence (wake.py); None for a fixed-cadence task (every step `cycle_days`)
    wake: Optional[WakeDecl] = None
    #: a decision task's outcome kind (outcome.KINDS): read at the start, at each applied declaration and at the
    #: end, and scored into the end record; None for a task scored by its reward only
    outcome: Optional[str] = None
    #: metrics read and recorded after every step though no reward term or goal reads them ((): none)
    record_metrics: tuple[str, ...] = ()
    #: the toolkit's shared-notes set its seats are served (ck3agent notes.json `sets`, passed as
    #: CK3_NOTES_SET); None = the file's default set
    notes_set: Optional[str] = None

    def metric_names(self) -> tuple[str, ...]:
        """The metrics read after every step: the reward's, then the goal's, then the recorded ones."""
        out = list(self.reward.metrics())
        for m in GOAL_METRICS[self.goal] + self.record_metrics:
            if m not in out:
                out.append(m)
        return tuple(out)

    @property
    def n_steps(self) -> int:
        return self.horizon_days // self.cycle_days

    @property
    def end_date(self) -> str:
        return calendar.add_days(self.start_date, self.horizon_days)

    @property
    def seat_names(self) -> tuple[str, ...]:
        return tuple(s.name for s in self.seats)

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "description": self.description, "backend": self.backend,
                "start": {"save": self.start_save, "save_sha256": self.start_save_sha256,
                          "date": self.start_date},
                "seats": [{k: v for k, v in (("name", s.name), ("character_id", s.character_id),
                                             ("census_rank", s.census_rank), ("role", s.role),
                                             ("holder_of", s.holder_of), ("holder_name", s.holder_name),
                                             ("holder_name_key", s.holder_name_key))
                           if v is not None} for s in self.seats],
                "horizon_days": self.horizon_days, "cycle_days": self.cycle_days,
                "reward": self.reward.to_dict(), "goal": self.goal, "tools": list(self.tools),
                "mods": {"policy_mod": self.policy_mod, "proposal_mod": self.proposal_mod},
                "game_speed": self.game_speed, "status": self.status, "status_reason": self.status_reason,
                **({"target_title": self.target_title} if self.target_title is not None else {}),
                **({"fake": dict(self.fake)} if self.fake else {}),
                **({"comms": self.comms.to_dict()} if self.comms is not None else {}),
                **({"wake": self.wake.to_dict()} if self.wake is not None else {}),
                **({"outcome": self.outcome} if self.outcome is not None else {}),
                **({"record_metrics": list(self.record_metrics)} if self.record_metrics else {}),
                **({"notes_set": self.notes_set} if self.notes_set is not None else {})}


# ── the one reader ─────────────────────────────────────────────────────────

_TOP_REQUIRED = {"id": str, "description": str, "backend": str, "start": dict, "seats": list,
                 "horizon_days": int, "cycle_days": int, "reward": dict, "tools": list,
                 "game_speed": int, "status": str, "status_reason": str}
_TOP_OPTIONAL = {"goal": str, "mods": dict, "fake": dict, "comms": dict, "target_title": str, "wake": dict,
                 "outcome": str, "record_metrics": list, "notes_set": str}
_START_KEYS = {"save": (str, type(None)), "save_sha256": (str, type(None)), "date": str}
_SEAT_KEYS = {"name": str, "character_id": int, "census_rank": int, "role": str, "holder_of": str,
              "holder_name": str, "holder_name_key": str}


def _type_ok(v: Any, t: Any) -> bool:
    if t is int or (isinstance(t, tuple) and int in t):
        if isinstance(v, bool):
            return False
    return isinstance(v, t)


def _check_keys(d: Mapping[str, Any], required: Mapping[str, Any], optional: Mapping[str, Any], where: str):
    if not isinstance(d, Mapping):
        raise EnvRefusal(R_SCENARIO_TYPE, f"{where} is not a table", "scenario")
    unknown = set(d) - set(required) - set(optional)
    if unknown:
        raise EnvRefusal(R_SCENARIO_KEY, f"{where}: {sorted(unknown)}", "scenario")
    for k, t in required.items():
        if k not in d:
            raise EnvRefusal(R_SCENARIO_MISSING, f"{where}.{k}", "scenario")
    for k, t in {**required, **optional}.items():
        if k in d and not _type_ok(d[k], t):
            raise EnvRefusal(R_SCENARIO_TYPE, f"{where}.{k} is {type(d[k]).__name__}", "scenario")


def scenario_from_dict(d: Mapping[str, Any]) -> Scenario:
    _check_keys(d, _TOP_REQUIRED, _TOP_OPTIONAL, "scenario")
    sid = d["id"]
    if d["backend"] not in BACKENDS:
        raise EnvRefusal(R_SCENARIO_VALUE, f"backend {d['backend']!r} not in {BACKENDS}", "scenario")
    if d["status"] not in STATUSES:
        raise EnvRefusal(R_SCENARIO_VALUE, f"status {d['status']!r} not in {STATUSES}", "scenario")
    _check_keys(d["start"], {"date": str}, {"save": _START_KEYS["save"], "save_sha256": _START_KEYS["save_sha256"]},
                f"{sid}.start")
    start = d["start"]
    if calendar.ordinal(start["date"]) is None:
        raise EnvRefusal(R_SCENARIO_VALUE, f"start.date {start['date']!r} is not Y.M.D", "scenario")
    if d["backend"] == "ck3" and not start.get("save"):
        raise EnvRefusal(R_SCENARIO_MISSING, f"{sid}.start.save (the ck3 backend launches a save)", "scenario")
    seats = []
    for i, s in enumerate(d["seats"]):
        _check_keys(s, {"name": str}, {k: v for k, v in _SEAT_KEYS.items() if k != "name"}, f"{sid}.seats[{i}]")
        chosen = [k for k in SELECTORS if k in s]
        if len(chosen) != 1:
            raise EnvRefusal(R_SCENARIO_VALUE, f"{sid}.seats[{i}] needs exactly one of {SELECTORS}, has {chosen}",
                             "scenario")
        if "holder_of" in s:
            # resolved by a census read on the game at open (census.py select_by_holder); confirmed by a name
            if not TITLE_KEY.match(s["holder_of"]):
                raise EnvRefusal(R_SCENARIO_VALUE, f"{sid}.seats[{i}].holder_of {s['holder_of']!r} is not a title "
                                                   "key", "scenario")
            if not s.get("holder_name"):
                raise EnvRefusal(R_SCENARIO_MISSING, f"{sid}.seats[{i}].holder_name (a holder_of seat is confirmed "
                                                     "by his name)", "scenario")
            if d["backend"] != "ck3":
                raise EnvRefusal(R_SCENARIO_VALUE, f"{sid}.seats[{i}]: holder_of is read from the game at open; "
                                                   f"backend {d['backend']} has no game", "scenario")
        elif "holder_name" in s or "holder_name_key" in s:
            raise EnvRefusal(R_SCENARIO_VALUE, f"{sid}.seats[{i}]: holder_name and holder_name_key confirm a "
                                               "holder_of seat; this seat has none", "scenario")
        seats.append(SeatDecl(s["name"], s.get("character_id"), s.get("census_rank"), s.get("role", "ruler"),
                              s.get("holder_of"), s.get("holder_name"), s.get("holder_name_key")))
    if not seats:
        raise EnvRefusal(R_SCENARIO_VALUE, f"{sid}: no seats", "scenario")
    ranks = [s.census_rank for s in seats if s.census_rank is not None]
    if ranks:
        # census ranks are read among the vassals of the FIRST seat, which is
        # therefore named by its character id (census.py)
        if seats[0].character_id is None:
            raise EnvRefusal(R_SCENARIO_VALUE, f"{sid}: census_rank seats rank the first seat's vassals; the "
                                               "first seat must be chosen by character_id", "scenario")
        if any(r < 1 for r in ranks) or len(set(ranks)) != len(ranks):
            raise EnvRefusal(R_SCENARIO_VALUE, f"{sid}: census ranks must be distinct and >= 1, got {ranks}",
                             "scenario")
    names = [s.name for s in seats]
    if len(set(names)) != len(names):
        raise EnvRefusal(R_SCENARIO_VALUE, f"{sid}: seat names repeat: {names}", "scenario")
    h, c = d["horizon_days"], d["cycle_days"]
    if c <= 0 or h <= 0 or h % c:
        raise EnvRefusal(R_SCENARIO_VALUE, f"{sid}: horizon_days {h} must be a positive multiple of "
                                           f"cycle_days {c}", "scenario")
    r = d["reward"]
    _check_keys(r, {"terms": list}, {"team": str, "team_seat": str}, f"{sid}.reward")
    terms = []
    for i, t in enumerate(r["terms"]):
        _check_keys(t, {"name": str, "weight": (int, float)}, {}, f"{sid}.reward.terms[{i}]")
        terms.append((t["name"], t["weight"]))
    team = r.get("team", "none")
    if team not in TEAM_MODES:
        raise EnvRefusal(R_SCENARIO_VALUE, f"{sid}.reward.team {team!r} not in {TEAM_MODES}", "scenario")
    team_seat = r.get("team_seat")
    if (team == "seat") != (team_seat is not None):
        raise EnvRefusal(R_SCENARIO_VALUE, f"{sid}.reward: team = 'seat' needs team_seat, and team_seat needs "
                                           f"team = 'seat' (team {team!r}, team_seat {team_seat!r})", "scenario")
    if team_seat is not None and team_seat not in names:
        raise EnvRefusal(R_SCENARIO_VALUE, f"{sid}.reward.team_seat {team_seat!r} is not a seat {names}",
                         "scenario")
    reward = RewardSpec(tuple(terms), team, team_seat)
    goal = d.get("goal", "none")
    if goal not in GOALS:
        raise EnvRefusal(R_SCENARIO_VALUE, f"{sid}.goal {goal!r} not in {GOALS}", "scenario")
    recorded = d.get("record_metrics", [])
    if any(not isinstance(m, str) or m not in METRIC_NAMES for m in recorded) or len(set(recorded)) != len(recorded):
        raise EnvRefusal(R_SCENARIO_VALUE, f"{sid}.record_metrics {recorded!r}: distinct names of "
                                           f"{list(METRIC_NAMES)}", "scenario")
    target = d.get("target_title")
    reads = [m for m in reward.metrics() + GOAL_METRICS[goal] + tuple(recorded) if m in TARGET_METRICS]
    if target is not None and not TITLE_KEY.match(target):
        raise EnvRefusal(R_SCENARIO_VALUE, f"{sid}.target_title {target!r} is not a title key (k_ireland)", "scenario")
    if reads and target is None:
        raise EnvRefusal(R_SCENARIO_MISSING, f"{sid}.target_title (read by {sorted(set(reads))})", "scenario")
    if target is not None and not reads:
        raise EnvRefusal(R_SCENARIO_VALUE, f"{sid}.target_title {target!r} is declared but no reward term or goal "
                                           "reads it", "scenario")
    names_terms = [n for n, _w in terms]
    if goal in ONCE_GOALS and "target_title_gained" in names_terms:
        raise EnvRefusal(R_SCENARIO_VALUE, f"{sid}: goal {goal} pays the title once; target_title_gained pays it again "
                                           "after a loss and a regain (use target_title_first_gained)", "scenario")
    if "target_title_first_gained" in names_terms and goal not in ONCE_GOALS:
        raise EnvRefusal(R_SCENARIO_VALUE, f"{sid}: target_title_first_gained is the reward of a once goal "
                                           f"{ONCE_GOALS}; goal {goal!r} ends the episode at the first holding",
                         "scenario")
    if goal in ("kingdom_held", "kingdom_held_once") and not str(target).startswith("k_"):
        raise EnvRefusal(R_SCENARIO_VALUE, f"{sid}: goal {goal} needs a kingdom target_title, got {target!r}",
                         "scenario")
    if goal == "empire_held" and not str(target).startswith("e_"):
        raise EnvRefusal(R_SCENARIO_VALUE, f"{sid}: goal empire_held needs an empire target_title, got {target!r}",
                         "scenario")
    tools = tuple(d["tools"])
    bad = [t for t in tools if t not in PUBLIC_TOOLS + SEAT_TOOLS]
    if bad or not tools:
        raise EnvRefusal(R_SCENARIO_VALUE, f"{sid}.tools: unknown {bad}" if bad else f"{sid}.tools is empty",
                         "scenario")
    comms = comms_from_dict(d["comms"], names, f"{sid}.comms") if "comms" in d else None
    need = comms.tools() if comms is not None else ()
    listed = tuple(t for t in tools if t in COMMS_TOOLS)
    if set(listed) != set(need):
        raise EnvRefusal(R_SCENARIO_VALUE, f"{sid}: the env-owned tools listed {sorted(listed)} must be exactly "
                                           f"those its [comms] table declares {sorted(need)}", "scenario")
    mods = d.get("mods", {})
    _check_keys(mods, {}, {"policy_mod": bool, "proposal_mod": bool}, f"{sid}.mods")
    if not 1 <= d["game_speed"] <= 5:
        raise EnvRefusal(R_SCENARIO_VALUE, f"{sid}.game_speed {d['game_speed']} not in 1..5", "scenario")
    if d["game_speed"] > MAX_GAME_SPEED:
        raise EnvRefusal(R_SCENARIO_SPEED, f"{sid}.game_speed {d['game_speed']}: the fastest speed the bridge "
                                           f"reaches is {MAX_GAME_SPEED} ({SPEED_BOUND_SOURCE})", "scenario")
    wake = None
    if "wake" in d:
        w = d["wake"]
        _check_keys(w, {"check_days": int, "max_days": int}, {"forced": list, "orders": bool, "aligned_checks": bool},
                    f"{sid}.wake")
        from .wake import EVENTS as _WAKE_EVENTS
        forced = w.get("forced", [])
        if any(e not in _WAKE_EVENTS for e in forced) or len(set(forced)) != len(forced):
            raise EnvRefusal(R_SCENARIO_VALUE, f"{sid}.wake.forced {forced!r}: distinct events of "
                                               f"{sorted(_WAKE_EVENTS)}", "scenario")
        if not 1 <= w["check_days"] <= w["max_days"] <= h or w["max_days"] < c:
            raise EnvRefusal(R_SCENARIO_VALUE, f"{sid}.wake: need 1 <= check_days ({w['check_days']}) <= max_days "
                                               f"({w['max_days']}) <= horizon_days ({h}), and max_days >= "
                                               f"cycle_days ({c})", "scenario")
        if d["game_speed"] > DEFAULT_GAME_SPEED and not w.get("aligned_checks", False):
            raise EnvRefusal(R_SCENARIO_VALUE, f"{sid}: a wake task at game_speed {d['game_speed']} needs "
                                               f"wake.aligned_checks = true (above {DEFAULT_GAME_SPEED} a wall-polled "
                                               f"check lands days after its date)", "scenario")
        wake = WakeDecl(w["check_days"], w["max_days"], tuple(forced), w.get("orders", True),
                        w.get("aligned_checks", False))
    outcome = d.get("outcome")
    if outcome is not None:
        from .outcome import KINDS as _OUTCOME_KINDS
        if outcome not in _OUTCOME_KINDS:
            raise EnvRefusal(R_SCENARIO_VALUE, f"{sid}.outcome {outcome!r} not in {_OUTCOME_KINDS}", "scenario")
        if len(seats) != 1:
            raise EnvRefusal(R_SCENARIO_VALUE, f"{sid}.outcome is scored for one seat; {len(seats)} declared",
                             "scenario")
    fake = d.get("fake", {})
    if fake and d["backend"] != "fake":
        raise EnvRefusal(R_SCENARIO_VALUE, f"{sid}: a [fake] table on a {d['backend']} scenario", "scenario")
    return Scenario(id=sid, description=d["description"], backend=d["backend"], start_save=start.get("save"),
                    start_save_sha256=start.get("save_sha256"), start_date=start["date"], seats=tuple(seats),
                    horizon_days=h, cycle_days=c, reward=reward, goal=goal, tools=tools,
                    policy_mod=mods.get("policy_mod", False), proposal_mod=mods.get("proposal_mod", False),
                    game_speed=d["game_speed"], status=d["status"], status_reason=d["status_reason"],
                    fake=dict(fake), comms=comms, target_title=target, wake=wake,
                    outcome=outcome, record_metrics=tuple(recorded), notes_set=d.get("notes_set"))


def load_scenario(path: str | Path) -> Scenario:
    p = Path(path)
    try:
        data = tomllib.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise EnvRefusal(R_SCENARIO_UNKNOWN, f"no file {p}", "scenario") from None
    except tomllib.TOMLDecodeError as e:
        raise EnvRefusal(R_SCENARIO_TYPE, f"{p}: {e}", "scenario") from None
    return scenario_from_dict(data)


# ── the built-in tasks ─────────────────────────────────────────────────────

#: France at the 1066 start: the king's character id as the bridge reads it on
#: that save. The save is the user's own (a 1066.09.15 start saved from the game).
FRANCE_1066_SAVE = "_1066_09_15.ck3"
FRANCE_1066_KING = 37067
_GAME_TOOLS = list(PUBLIC_TOOLS)


def _ck3(id_, desc, *, seats, horizon, cycle, terms, status, why, goal="none", team="none", tools=None,
         speed=DEFAULT_GAME_SPEED, team_seat=None, comms=None, target_title=None, wake=None):
    # the wall time is computed from the measured rate, never written by hand
    why = (f"{why}. Time: about {wall_minutes(horizon, speed)} wall-minutes of clock at speed {speed} "
           f"({MEASURED_GD_PER_S[speed]} game-days/s, measured on one host), plus about a minute to launch "
           "and the policy's own time")
    reward = {"terms": [{"name": n, "weight": w} for n, w in terms], "team": team}
    if team_seat is not None:
        reward["team_seat"] = team_seat
    return {"id": id_, "description": desc, "backend": "ck3",
            "start": {"save": FRANCE_1066_SAVE, "date": "1066.09.15"}, "seats": seats,
            "horizon_days": horizon, "cycle_days": cycle, "reward": reward,
            "goal": goal, "tools": tools or _GAME_TOOLS, "game_speed": speed, "status": status,
            "status_reason": why, **({"comms": comms} if comms is not None else {}),
            **({"target_title": target_title} if target_title is not None else {}),
            **({"wake": wake} if wake is not None else {})}


#: Ireland at the 1066 start, read live on the same save: de jure k_ireland is 14 counties in five duchies, and
#: nobody holds the kingdom. The seat is Murchad of Dublin (c_dublin, independent, no liege): one county, the
#: most developed in Ireland (10 against 6 to 8 for each neighbour), inside d_meath, whose only other county,
#: Athlone, is held by a lone count; his father holds c_leinster next door. Five independent one-county
#: neighbours and no casus belli on day one, so the claim route, alliances and the treasury are real choices.
IRELAND_DUBLIN = 31506
IRELAND_TITLE = "k_ireland"

#: The council's channel and pads, per step: two messages of 600 bytes, a
#: 4096-byte private pad and an 8192-byte shared one, writes of 1024 bytes.
COUNCIL_COMMS = {"messages": {"quota_per_step": 2, "max_text_bytes": 600},
                 "notepads": {"private_max_bytes": 4096, "shared_max_bytes": 8192, "max_write_bytes": 1024}}


_KING = [{"name": "king", "character_id": FRANCE_1066_KING}]
_YEAR = calendar.DAYS_PER_YEAR

#: The France tasks run whole game-years on the game's calendar (365 days, no
#: leap years), so a task ends on its start's anniversary and a 10-year task
#: lands on the shipped baseline's 10-year mark (test_horizons.py). A year does
#: not divide into 30-day months: cycles are 73 days (a fifth of a year) or 146.
BUILTIN_DICTS: dict[str, dict[str, Any]] = {d["id"]: d for d in (
    _ck3("france1066-treasury-2y",
         "Two game-years as the King of France: grow the treasury. 10 steps of 73 days.",
         seats=_KING, horizon=2 * _YEAR, cycle=73, terms=[("gold_delta", 1.0)], status="buildable_now",
         why=RUN_UNSEATED + ": its reads (query_character gold) and the clock route (the pause alarm) have run "
             "live: the same task at 24 steps of 30 days completed its horizon with no policy; this declaration's "
             "73-day cycles have not been run"),
    _ck3("france1066-gain-county-5y",
         "Five game-years as the King of France: end with more counties than you started with. "
         "25 steps of 73 days; ends for the seat on the first step its realm exceeds the start.",
         seats=_KING, horizon=5 * _YEAR, cycle=73, terms=[("county_gain_goal", 1.0), ("line_survival", 1.0)],
         goal="county_gain", status="buildable_now",
         why=RUN_NONE + ": realm counties are read the way the baseline read them (query_realm "
             "realm_total_counties), and that read has run live; this task has not been run"),
    _ck3("france1066-hold-10y",
         "Ten game-years as the King of France: hold and grow the realm. 25 steps of 146 days. "
         "Baseline: eight unsteered games read 75 to 88 counties at 10 years from 84.",
         seats=_KING, horizon=10 * _YEAR, cycle=146, terms=[("realm_counties_delta", 1.0), ("line_survival", 50.0)],
         status="buildable_now",
         why=RUN_UNSEATED + ": ends on the baseline's 10-year mark; a variant (20 steps of 180 days, ending 50 "
             "days short of the mark) completed its horizon live with no policy; this declaration's 146-day cycles "
             "have not been run"),
    _ck3("france1066-council-10y",
         "Ten game-years: the King of France and his five largest vassals, each a seat, with messages and "
         "notepads; one shared reward, the king's realm counties. 25 steps of 146 days.",
         seats=_KING + [{"name": f"duke{i}", "census_rank": i, "role": "vassal"} for i in range(1, 6)],
         horizon=10 * _YEAR, cycle=146, terms=[("realm_counties_delta", 1.0), ("line_survival", 50.0)],
         team="seat", team_seat="king", comms=COUNCIL_COMMS,
         tools=_GAME_TOOLS + ["msg_send", "note_read", "note_write"], status="buildable_now",
         why=RUN_UNSEATED + ": a two-year variant (10 steps of 73 days, scripted policies, no model) ran live to its "
             "horizon: the census read through this backend chose 31808, 31466, 29249, 29247 and 36151, six "
             "toolkit servers (one per seat, each bound to its ruler) ran together, messages and the shared "
             "notepad were delivered in seat order, and a succession in the king's line was followed; this "
             "declaration's 146-day cycles over ten years have not been run, nor any model-driven seat"),
    _ck3("ireland1066-kingdom-15y",
         "Fifteen game-years as Murchad, Count of Dublin, from the 1066 start: bring as much of de jure Ireland "
         "as you can into your realm and create the Kingdom of Ireland. 75 steps of 73 days; ends for the seat "
         "on the first step its ruler holds k_ireland.",
         seats=[{"name": "dublin", "character_id": IRELAND_DUBLIN}], horizon=15 * _YEAR, cycle=73,
         terms=[("dejure_counties_delta", 1.0), ("target_title_gained", 10.0), ("line_survival", 5.0)],
         goal="kingdom_held", target_title=IRELAND_TITLE, status="buildable_now",
         why=RUN_MODEL + ": model seats have played it on the 1066 start, one to its horizon; its reads "
             "(inspect title k_ireland summary.held; inspect title_hierarchy holder_id, both public tools on the "
             "seat's own server) and the clock route have run live, and its 75 steps have run with no policy. "
             "Feasibility: ireland1066-kingdom-30y's declaration judges fifteen years too short for the counties "
             "and the gold the kingdom needs"),
    _ck3("ireland1066-kingdom-30y",
         "Thirty game-years as Murchad, Count of Dublin, from the 1066 start: bring as much of de jure Ireland "
         "as you can into your realm and create the Kingdom of Ireland. 150 steps of 73 days; ends for the seat "
         "on the first step its ruler holds k_ireland.",
         seats=[{"name": "dublin", "character_id": IRELAND_DUBLIN}], horizon=30 * _YEAR, cycle=73,
         terms=[("dejure_counties_delta", 1.0), ("target_title_gained", 10.0), ("line_survival", 5.0)],
         goal="kingdom_held", target_title=IRELAND_TITLE, status="buildable_now",
         why=RUN_MODEL + ": model seats have played its 150 steps on the 1066 start; the same seat, reads and "
             "clock route as ireland1066-kingdom-15y, twice the horizon: a fabricated claim took 14 to 21 "
             "game-months and cost about 95 gold in live runs, so fifteen years do not hold the eight counties and "
             "the 850 gold the kingdom needs"),
    _ck3("ireland1066-kingdom-30y-wake",
         "ireland1066-kingdom-30y as a WAKE task: the same seat, start, goal, reward terms and end date "
         "(1096.09.15); a turn may end with wake conditions (wake.py) and the step's clock runs until one "
         "holds (checked every 7 game-days while the clock runs), at most 365 days; with none, 73 days. A "
         "succession in the seat's line and a war declared on the seat wake it whatever it asked.",
         seats=[{"name": "dublin", "character_id": IRELAND_DUBLIN}], horizon=30 * _YEAR, cycle=73,
         terms=[("dejure_counties_delta", 1.0), ("target_title_gained", 10.0), ("line_survival", 5.0)],
         goal="kingdom_held", target_title=IRELAND_TITLE, status="buildable_now",
         wake={"check_days": 7, "max_days": 365, "forced": ["ruler_changed", "war_declared_on"]},
         why=RUN_MODEL + ": model seats have played it on the 1066 start; the reads and clock route of "
             "ireland1066-kingdom-30y; the wake conditions are read on the env's control session while the clock "
             "runs and a fire stops the clock on the pause alarm (ck3.py advance_until); a scripted seat with no "
             "model ran six game-years of it (28 steps, 1066.09.15 to 1072.10.22: checks while the clock ran never "
             "read a torn fact, every stop on a condition landed the day after the check that fired)"),
    {"id": "fake-duel", "description": "Two rulers on the fake backend's scripted map. For tests and "
                                       "policy plumbing; nothing here is the game.",
     "backend": "fake", "start": {"date": "1066.09.15"},
     "seats": [{"name": "east", "character_id": 1}, {"name": "west", "character_id": 2}],
     "horizon_days": 720, "cycle_days": 90,
     "reward": {"terms": [{"name": "realm_counties_delta", "weight": 1.0},
                          {"name": "line_survival", "weight": 10.0}], "team": "none"},
     "goal": "none", "tools": ["observe", "do", "declare_war", "pass_turn", "query"], "game_speed": 3,
     "status": "buildable_now", "status_reason": "the fake backend is in-process and deterministic",
     "fake": {"counties": [10, 8], "gold": [100.0, 80.0], "prestige": [50.0, 40.0], "ages": [40, 60]}},
    {"id": "fake-kingdom", "description": "One count on the fake backend who can create a title once he holds "
                                          "more than half its de jure counties. Nothing here is the game.",
     "backend": "fake", "start": {"date": "1066.09.15"},
     "seats": [{"name": "count", "character_id": 1}, {"name": "rival", "character_id": 2}],
     "horizon_days": 1095, "cycle_days": 73,
     "reward": {"terms": [{"name": "dejure_counties_delta", "weight": 1.0},
                          {"name": "target_title_gained", "weight": 10.0},
                          {"name": "line_survival", "weight": 5.0}], "team": "none"},
     "goal": "kingdom_held", "target_title": "k_fake", "tools": ["observe", "do", "declare_war", "pass_turn"],
     "game_speed": 3, "status": "buildable_now", "status_reason": "the fake backend is in-process and deterministic",
     "fake": {"counties": [1, 1], "gold": [100.0, 80.0], "prestige": [50.0, 40.0], "ages": [30, 40],
              "dejure": [1, 1], "dejure_total": 7}},
    {"id": "fake-kingdom-wake", "description": "fake-kingdom as a WAKE task: a seat may end its turn with wake "
                                               "conditions (wake.py) and the step runs until one holds, a check "
                                               "every game-day, at most a year; with none, 73 days as in "
                                               "fake-kingdom. Nothing here is the game.",
     "backend": "fake", "start": {"date": "1066.09.15"},
     "seats": [{"name": "count", "character_id": 1}, {"name": "rival", "character_id": 2}],
     "horizon_days": 1095, "cycle_days": 73, "wake": {"check_days": 1, "max_days": 365},
     "reward": {"terms": [{"name": "dejure_counties_delta", "weight": 1.0},
                          {"name": "target_title_gained", "weight": 10.0},
                          {"name": "line_survival", "weight": 5.0}], "team": "none"},
     "goal": "kingdom_held", "target_title": "k_fake", "tools": ["observe", "do", "declare_war", "pass_turn"],
     "game_speed": 3, "status": "buildable_now", "status_reason": "the fake backend is in-process and deterministic",
     "fake": {"counties": [1, 1], "gold": [100.0, 80.0], "prestige": [50.0, 40.0], "ages": [30, 40],
              "dejure": [1, 1], "dejure_total": 7}},
    {"id": "fake-solo", "description": "One ruler on the fake backend; a treasury task.",
     "backend": "fake", "start": {"date": "1066.09.15"},
     "seats": [{"name": "king", "character_id": 1}], "horizon_days": 360, "cycle_days": 30,
     "reward": {"terms": [{"name": "gold_delta", "weight": 1.0}], "team": "none"},
     "goal": "none", "tools": ["observe", "do", "pass_turn"], "game_speed": 3,
     "status": "buildable_now", "status_reason": "the fake backend is in-process and deterministic",
     "fake": {"counties": [12], "gold": [100.0], "prestige": [50.0], "ages": [35]}},
    {"id": "fake-council", "description": "A king and two vassals on the fake backend, paid by the king's "
                                          "realm, with messages and notepads. Nothing here is the game.",
     "backend": "fake", "start": {"date": "1066.09.15"},
     "seats": [{"name": "king", "character_id": 1}, {"name": "duke1", "census_rank": 1, "role": "vassal"},
               {"name": "duke2", "census_rank": 2, "role": "vassal"}],
     "horizon_days": 720, "cycle_days": 90,
     "reward": {"terms": [{"name": "realm_counties_delta", "weight": 1.0},
                          {"name": "line_survival", "weight": 10.0}], "team": "seat", "team_seat": "king"},
     "goal": "none", "tools": ["observe", "do", "pass_turn", "msg_send", "note_read", "note_write"],
     "game_speed": 3, "status": "buildable_now", "status_reason": "the fake backend is in-process and deterministic",
     "comms": {"messages": {"quota_per_step": 2, "max_text_bytes": 200},
               "notepads": {"private_max_bytes": 400, "shared_max_bytes": 600, "max_write_bytes": 200}},
     "fake": {"counties": [20, 6, 5], "gold": [100.0, 40.0, 30.0], "prestige": [50.0, 20.0, 20.0],
              "ages": [40, 30, 50]}},
)}

#: The Ireland tasks' seats are served the toolkit's Ireland notes set (its default set is general advice, true of
#: any start); the tasks copied from them below (the 180-day wake, the Munster seat, the decision starts) carry it.
for _tid in ("ireland1066-kingdom-15y", "ireland1066-kingdom-30y", "ireland1066-kingdom-30y-wake"):
    BUILTIN_DICTS[_tid]["notes_set"] = "ireland1066"


def _wake_variant(base_id: str, new_id: str, max_days: int, why: str) -> dict:
    """A copy of a wake task whose only change is the step's maximum (wake.max_days): the same seat, start,
    horizon, cycle, reward terms, goal, tools, speed and wake conditions; the id, the description's
    "at most N days" and the status reason name the new maximum (a 365-day step lets the game's AI answer
    demands, letters and mints between turns), so runs can compare the two."""
    base = BUILTIN_DICTS[base_id]
    old = f"at most {base['wake']['max_days']} days"
    if old not in base["description"]:
        raise ValueError(f"{base_id}: its description does not say {old!r}")
    return {**base, "id": new_id, "description": base["description"].replace(old, f"at most {max_days} days"),
            "wake": {**base["wake"], "max_days": max_days},
            "status_reason": (f"{base_id} with a {max_days}-day maximum step, everything else the same; " + why
                              + ". Time: " + base["status_reason"].split(". Time: ", 1)[1])}


BUILTIN_DICTS["ireland1066-kingdom-30y-wake180"] = _wake_variant(
    "ireland1066-kingdom-30y-wake", "ireland1066-kingdom-30y-wake180", 180,
    RUN_UNSEATED + ": the same reads and clock route; this declaration ran live once with a scripted seat and no "
    "model for six steps (1066.09.15 to 1068.01.25: two of 180 days, a request of 181 refused), its full horizon "
    "and any model-driven seat have not been run")


#: The same start read for another seat: on 1066.09.15 Munster's top title is the duchy d_munster (tier 3, a de jure
#: duchy of k_ireland), held by Murchad ua Briain (31762, Irish, Insular Celtic, 39): Thomond and Ennis in his
#: domain and Ormond under his one vassal, three of the kingdom's 14 counties, the strongest realm in Ireland
#: (803 men against 565 for Dublin), 203 gold, 1,300 prestige, partition succession. Read live on the same save.
IRELAND_MUNSTER = 31762


#: The rule every task scores by (env.py): a line that passes out of the seat's dynasty is a loss, there.
LINE_LOST_TEXT = "If your titles pass to a ruler of another dynasty, the episode ends there as a loss."
#: For the line tasks, exactly what ends it (LINE_LOST_TEXT alone can be read as "losing the duchy to a claimant war
#: ends the episode"). The env judges the line's dynasty only when the line's ruler changes (env.py _judge_dynasty),
#: and the toolkit bridge's watch follows the ruler to whoever holds his primary title at his death, the title
#: refreshed while he lives; a county lost while he lives changes realm_counties only.
LINE_TASK_LOST_TEXT = ("The episode ends as a loss only when, at your ruler's death, the one who succeeds him "
                       "is of another dynasty. A title or county lost while he lives, as in a war declared on you, "
                       "does not end it: it lowers your realm's counties, the last of the four.")


def _seat_variant(base_id: str, new_id: str, seat: dict, desc: str, why: str) -> dict:
    """A copy of a task whose only change is the seat it is played from: the same start, horizon, cycle, reward
    terms, goal, target title, tools, speed and wake rules; the id, the description and the status reason are
    the new seat's (the reason keeps the base task's measured wall time)."""
    base = BUILTIN_DICTS[base_id]
    return {**base, "id": new_id, "description": desc, "seats": [seat],
            "status_reason": why + ". Time: " + base["status_reason"].split(". Time: ", 1)[1]}


BUILTIN_DICTS["munster1066-kingdom-30y-wake"] = _seat_variant(
    "ireland1066-kingdom-30y-wake", "munster1066-kingdom-30y-wake",
    {"name": "munster", "character_id": IRELAND_MUNSTER},
    "Thirty game-years as Murchad ua Briain, Duke of Munster, from the 1066 start: bring as much of de jure "
    "Ireland as you can into your realm and create the Kingdom of Ireland. What is scored is your ruler HOLDING "
    "k_ireland, however it comes to him: created by your own call, created for him by the game's AI, or "
    "otherwise; the episode ends for the seat on the first step he holds it, and the last reply says so (the "
    "title, its holder, the date and who created it). " + LINE_LOST_TEXT + " The wake task of "
    "ireland1066-kingdom-30y-wake played from another seat, with the "
    "same start, goal, reward terms, wake rules and end date (1096.09.15): a turn may end with wake conditions "
    "(wake.py) and the step's clock runs until one holds (checked every 7 game-days while the clock runs), at "
    "most 365 days; with none, 73 days. A succession in the seat's line and a war declared on the seat wake it "
    "whatever it asked.",
    RUN_MODEL + ": model seats have played it on the 1066 start; the reads and clock route of "
    "ireland1066-kingdom-30y-wake, from the duchy seat: the seat's ruler and his start were read live on the 1066 "
    "start through the public tools")


#: The title rule as a seat is told it, ONE source read from the task (a seat prompt takes it from here):
#: what holding the target title does to the episode. TITLE_ENDS is the words every task that ends at the first
#: holding has been played with (the seat prompt templates carry them); TITLE_ONCE is a once goal's (rewards.py
#: ONCE_GOALS: paid once, the episode runs on).
TITLE_ENDS = "holding it ends the episode at once"
TITLE_ONCE = "paid once, never again if it is lost and regained; the episode goes on to the end date"


def title_rule(sc: "Scenario") -> Optional[str]:
    """What holding the target title does, in the words a seat is told: TITLE_ONCE for a once goal, TITLE_ENDS for a
    goal held at the first holding (kingdom_held, empire_held), None for a task with no such goal."""
    if sc.goal in ONCE_GOALS:
        return TITLE_ONCE
    if sc.goal in ("kingdom_held", "empire_held"):
        return TITLE_ENDS
    return None


#: The Munster task a series is declared for: the Munster wake task with the kingdom paid once and the
#: episode run to its end date whatever the title does. Everything else is munster1066-kingdom-30y-wake's.
BUILTIN_DICTS["munster1066-kingdom-once-30y-wake"] = {
    **BUILTIN_DICTS["munster1066-kingdom-30y-wake"],
    "id": "munster1066-kingdom-once-30y-wake",
    "description": (
        "Thirty game-years as Murchad ua Briain, Duke of Munster, from the 1066 start: bring as much of de jure "
        "Ireland as you can into your realm and create the Kingdom of Ireland. Reward: +1 for each de jure Irish "
        "county gained, -1 for each lost; +10 once, on the first step your ruler holds k_ireland, however it comes to "
        "him (never again if it is lost and regained); -5 if the line ends (no heir) or passes to a ruler of another "
        "dynasty. "
        "Holding the kingdom does not end the "
        "episode: play goes on to the end date (1096.09.15), and the task is met if your line held k_ireland at any "
        "step by then. " + LINE_LOST_TEXT + " A wake task: a turn may end with wake conditions (wake.py) and the "
        "step's clock runs until one holds (checked every 7 game-days while the clock runs), at most 365 days; with "
        "none, 73 days. A succession in the seat's line and a war declared on the seat wake it whatever it asked."),
    "reward": {"terms": [{"name": "dejure_counties_delta", "weight": 1.0},
                         {"name": "target_title_first_gained", "weight": 10.0},
                         {"name": "line_survival", "weight": 5.0}], "team": "none"},
    "goal": "kingdom_held_once", "notes_set": "munster1066once",
    "status_reason": (
        RUN_MODEL + ": model seats have played it on the 1066 start; munster1066-kingdom-30y-wake with the kingdom "
        "paid once and the episode run to its end date: the same seat, start, reads, wake rules and clock route; the "
        "once rule is also tested on replay fixtures (ck3bench/env/recorded_munster_rows.json: the step rows the env wrote in two "
        "recorded episodes of the Munster wake task, test inputs, not results). "
        "Time: " + BUILTIN_DICTS["munster1066-kingdom-30y-wake"]["status_reason"].split(". Time: ", 1)[1]),
}


#: The 867 start (the game's 867.01.01, a save of that start whose sha256 is declared below), read live through the
#: public tools on the seat's own server (a scripted pre-flight on 867.01.01): 12015 Charles, King of West Francia (k_france,
#: tier kingdom) also holds k_aquitaine; his realm has 89 of the 117 de jure counties of e_france (the Empire of
#: Francia: k_france, k_aquitaine, k_brittany, k_burgundy), nobody holds e_france, and the game's own creation check
#: says "You only have 89 of 94 required Counties" and "does not have gold 850" (he has 453). Ludwig of East Francia
#: (10465) holds 0 of the 117 (his kingdom is de jure Germania). Partition succession: at his death Aquitaine goes to
#: a younger son who leaves the realm (succession_plan, the same read).
FRANCIA_867_SAVE = "_867_01_01.ck3"
FRANCIA_867_SHA256 = "1a08927d4515ebaeb20f528f56430084348556359606ac54d2ba4b47a82bc5be"
FRANCIA_867_KING = 12015
FRANCIA_TITLE = "e_france"

BUILTIN_DICTS["francia867-empire-40y-wake"] = {
    **BUILTIN_DICTS["ireland1066-kingdom-30y-wake"],
    "id": "francia867-empire-40y-wake",
    "description": (
        "Forty game-years as Charles, King of West Francia, from the 867 start: bring as much of de jure Francia "
        "(the empire title e_france: the kingdoms of France, Aquitaine, Brittany and Burgundy) as you can into your "
        "realm and create the Empire of Francia. What is scored is your ruler HOLDING e_france, however it comes to "
        "him: created by your own call, created for him by the game's AI, or otherwise; the episode ends for the seat "
        "on the first step he holds it, and the last reply says so (the title, its holder, the date and who created "
        "it). If he dies, his heir continues as the seat. " + LINE_LOST_TEXT + " A wake task: a turn may end with wake conditions (wake.py) "
        "and the step's clock runs until one holds (checked every 7 game-days while the clock runs), at most 365 "
        "days; with none, 73 days. A succession in the seat's line and a war declared on the seat wake it whatever "
        "it asked. Ends 0907.01.01."),
    "start": {"save": FRANCIA_867_SAVE, "save_sha256": FRANCIA_867_SHA256, "date": "0867.01.01"},
    "seats": [{"name": "francia", "character_id": FRANCIA_867_KING}],
    "horizon_days": 40 * _YEAR, "goal": "empire_held", "target_title": FRANCIA_TITLE, "notes_set": "francia867",
    "status_reason": (
        RUN_MODEL + ": model seats have played it on the 867 start, one to its horizon; the reads and clock route of "
        "ireland1066-kingdom-30y-wake from the 867 start and an empire goal: the seat's ruler, his realm and the "
        "empire's creation check were read live on the 867 start through the public tools (a scripted pre-flight, "
        "no model, twelve game-months run); forty years because the land is near (89 of 94 counties) but a "
        "partition at the first succession (about 17 years ahead by his health) takes Aquitaine out of the realm. "
        "Time: about " + str(wall_minutes(40 * _YEAR, DEFAULT_GAME_SPEED)) + " wall-minutes of clock at speed "
        + str(DEFAULT_GAME_SPEED) + " (" + str(MEASURED_GD_PER_S[DEFAULT_GAME_SPEED]) + " game-days/s, measured on one "
        "host), plus about a minute to launch and the policy's own time"),
}


#: Starts whose seat is found on the save at open (census.py select_by_holder): the holder of a named title,
#: confirmed by his name, so no runtime character id is written here. The names are the game's own: the history
#: file's name key and its English localisation (a save may read either: every ruler row of the 867 save read
#: name_pending with the key). The 867 save is the observer save the 867 experiments register, by sha256; its
#: date reads 0867.01.01. Both saves are declared by sha256, so another file under the same name is refused.
SAVE_867 = ("_867_01_01.ck3", "1a08927d4515ebaeb20f528f56430084348556359606ac54d2ba4b47a82bc5be")
#: The 1066 save the line tasks start from, by the sha256 a game opened from that file reads at open.
SAVE_1066 = (FRANCE_1066_SAVE, "0c94b59880e0549a5a0c8fbe4dc675a3de171f28d79f7628ad765116de782d5d")
_WAKE_FORCED = {"check_days": 7, "max_days": 365, "forced": ["ruler_changed", "war_declared_on"]}
_WAKE_TEXT = ("A wake task: a turn may end with wake conditions (wake.py) and the step's clock runs until one holds "
              "(checked every 7 game-days while the clock runs), at most 365 days; with none, 73 days. A succession "
              "in the seat's line and a war declared on the seat wake it whatever it asked.")
_HOLDER_RUN = ("the seat is found at open by the holder census (query_de_jure by title key, then query_character "
               "for the holder's name), which has run live on this save, and the recorded metrics (ruler_changes, "
               "offensive_wars_declared, heir_present) have been read live; the clock and wake route are those of "
               "ireland1066-kingdom-30y-wake")


def _holder_task(id_: str, desc: str, *, seat: dict, save: tuple[str, Optional[str]], date: str, years: int,
                 terms: list, goal: str, why: str, target_title: Optional[str] = None,
                 record_metrics: Optional[list] = None, notes_set: Optional[str] = None) -> dict:
    return {"id": id_, "description": desc, "backend": "ck3",
            "start": {"save": save[0], "save_sha256": save[1], "date": date}, "seats": [seat],
            "horizon_days": years * _YEAR, "cycle_days": 73,
            "reward": {"terms": [{"name": n, "weight": w} for n, w in terms], "team": "none"},
            "goal": goal, "tools": list(_GAME_TOOLS), "game_speed": DEFAULT_GAME_SPEED, "status": "buildable_now",
            "status_reason": (f"{why}. Time: about {wall_minutes(years * _YEAR, DEFAULT_GAME_SPEED)} wall-minutes of "
                              f"clock at speed {DEFAULT_GAME_SPEED} ({MEASURED_GD_PER_S[DEFAULT_GAME_SPEED]} game-days/s, "
                              "measured on one host), plus about a minute to launch and the policy's own time"),
            "wake": copy.deepcopy(_WAKE_FORCED),
            **({"target_title": target_title} if target_title is not None else {}),
            **({"record_metrics": list(record_metrics)} if record_metrics else {}),
            **({"notes_set": notes_set} if notes_set else {})}


BUILTIN_DICTS["sweden867-kingdom-20y-wake"] = _holder_task(
    "sweden867-kingdom-20y-wake",
    "Twenty game-years as Björn Ironside, from the 867 start (0867.01.01): bring as much of de jure Sweden as you "
    "can into your realm and hold the Kingdom of Sweden. What is scored is your ruler HOLDING k_sweden, however it "
    "comes to him: created by your own call, created for him by the game's AI, or otherwise; the episode ends for "
    "the seat on the first step he holds it, and the last reply says so. " + LINE_LOST_TEXT + " Your ruler is found on the save at the "
    "start: the holder of d_svealand, confirmed by his name. He begins in a war already under way; it is not "
    "counted as one he declared. Recorded at every step and not scored: the offensive wars your line declares, "
    "the rulers it passes through, and whether your ruler has an heir. " + _WAKE_TEXT,
    seat={"name": "bjorn", "holder_of": "d_svealand", "holder_name": "Björn", "holder_name_key": "BjO_rn"},
    save=SAVE_867, date="0867.01.01", years=20,
    terms=[("dejure_counties_delta", 1.0), ("target_title_gained", 10.0), ("line_survival", 5.0)],
    goal="kingdom_held", target_title="k_sweden",
    record_metrics=["offensive_wars_declared", "ruler_changes", "heir_present"], notes_set="sweden867",
    why=RUN_MODEL + ": model seats have played it on the 867 save; the reward terms and goal of "
        "ireland1066-kingdom-30y-wake on the 867 save, k_sweden for k_ireland; " + _HOLDER_RUN)

BUILTIN_DICTS["lorraine1066-line-25y-wake"] = _holder_task(
    "lorraine1066-line-25y-wake",
    "Twenty-five game-years as Gottfried the Bearded, Duke of Lower Lorraine, a vassal in the Holy Roman Empire, "
    "from the 1066 start: keep your line ruling, in peace. Scored at the end date (1091.09.15), all four "
    "together: your line still has a ruler of your dynasty; it has passed from one ruler to the next at least once; no ruler of "
    "your line declared an offensive war, whether by your call or by the game's AI for him (a war declared on you "
    "does not count; the war of a faction he leads counts, since he is its attacker; joining an ally's war does not "
    "count); and your realm holds at least as many counties as at the start. Meeting them early does not "
    "end the episode: it runs to the end date. Your ruler is found on the save at the start: the holder of "
    "d_lower_lorraine, confirmed by his name. Reward at each step: the change in your realm's counties, and -5 if "
    "the line ends. " + LINE_TASK_LOST_TEXT + " Recorded at every step and not scored: whether your ruler has an heir. "
    + _WAKE_TEXT,
    seat={"name": "lorraine", "holder_of": "d_lower_lorraine", "holder_name": "Gottfried",
          "holder_name_key": "Gottfried"},
    save=SAVE_1066, date="1066.09.15", years=25,
    terms=[("realm_counties_delta", 1.0), ("line_survival", 5.0)], goal="line_kept_peacefully",
    record_metrics=["heir_present"], notes_set="lorraine1066",
    why=RUN_MODEL + ": a model seat has played it on the 1066 save; a goal judged at the end date over four legs "
        "(rewards.py line_kept_peacefully), on the 1066 save the France and Ireland tasks start from; "
        + _HOLDER_RUN + ". Feasibility: this start was judged not winnable for the line goal in the recorded save, "
        "where the ruler's heir is matrilineally married, so the son who succeeds him is of his wife's dynasty; "
        "carinthia1066-line-25y-wake is the same goal from another seat")

BUILTIN_DICTS["carinthia1066-line-25y-wake"] = _holder_task(
    "carinthia1066-line-25y-wake",
    "Twenty-five game-years as Berthold, Duke of Carinthia, Carniola and Verona, a vassal in the Holy Roman Empire, "
    "from the 1066 start: keep your line ruling, in peace. Scored at the end date (1091.09.15), all four together: "
    "your line still has a ruler of your dynasty; it has passed from one ruler to the next at least once; no ruler of "
    "your line declared an offensive war, whether by your call or by the game's AI for him (a war declared on you "
    "does not count; the war of a faction he leads counts, since he is its attacker; joining an ally's war does not "
    "count); and your realm holds at least as many counties as at the start. Meeting them early does not end the "
    "episode: it runs to the end date. Your ruler is found on the save at the start: the holder of d_carinthia, "
    "confirmed by his name. Reward at each step: the change in your realm's counties, and -5 if the line ends. "
    + LINE_TASK_LOST_TEXT + " Recorded at every step and not scored: whether your ruler has an heir. " + _WAKE_TEXT,
    seat={"name": "carinthia", "holder_of": "d_carinthia", "holder_name": "Berthold",
          "holder_name_key": "Berthold"},
    save=SAVE_1066, date="1066.09.15", years=25,
    terms=[("realm_counties_delta", 1.0), ("line_survival", 5.0)], goal="line_kept_peacefully",
    record_metrics=["heir_present"], notes_set="carinthia1066",
    why=RUN_MODEL + ": a model seat has played it on the 1066 save; lorraine1066-line-25y-wake's goal and terms "
        "from another seat on the same save (the Lorraine heir was matrilineally married in the save); "
        + _HOLDER_RUN)


#: DECISION tasks. Each starts from a mid-game save of the Ireland seat (Murchad 31506), taken from the game's own
#: yearly autosave of a game played from the 1066 start; the declared sha256 is checked before launch, so only that
#: exact file opens the task (the game is not deterministic: the same calls again make another file). The seat, title, reward terms, goal and wake cadence are those of
#: ireland1066-kingdom-30y-wake; what differs is the start, the horizon, and the `outcome` scored at the end
#: (outcome.py), which measures the ONE decision the start was built for.
def _decision(id_: str, desc: str, *, save: str, sha256: str, date: str, years: int, cycle: int, outcome: str,
              why: str) -> dict:
    base = BUILTIN_DICTS["ireland1066-kingdom-30y-wake"]
    return {**base, "id": id_, "description": desc, "start": {"save": save, "save_sha256": sha256, "date": date},
            "horizon_days": years * _YEAR, "cycle_days": cycle, "outcome": outcome,
            "status_reason": (f"{why}. Time: about {wall_minutes(years * _YEAR, DEFAULT_GAME_SPEED)} wall-minutes of "
                              f"clock at speed {DEFAULT_GAME_SPEED} ({MEASURED_GD_PER_S[DEFAULT_GAME_SPEED]} game-days/s, "
                              "measured on one host), plus about a minute to launch and the policy's own time")}


BUILTIN_DICTS["ireland1070-target-choice-3y"] = _decision(
    "ireland1070-target-choice-3y",
    "A decision task from a game under way: Murchad, Count of Dublin, on 1070.01.01, holds two unpressed, "
    "declarable claims: Oriel, whose ruler can call about 1,470 men, and Ossory, whose ruler can call about 530, "
    "against his own 1,840. Three game-years (to 1073.01.01), the Ireland task's reward, goal and wake cadence. "
    "Scored at the end (outcome target_choice): which claim was declared on, the strength read at that "
    "declaration, and the war's result.",
    save="scen_b2_1070_01_01.ck3", sha256="a666fb332ff59375c4384804a8cd7bc722bc2d2a863b822c02c09fadf1f5db7b",
    date="1070.01.01", years=3, cycle=73, outcome="target_choice",
    why=RUN_MODEL + ": model seats have played it from this save; the save was made by a scripted game from the "
        "1066 start (no seat) and banked from the game's yearly autosave; a reload through this backend was "
        "compared with the read taken when it was made")

_SAVE_1075 = ("scen_b2_1075_01_01.ck3", "f25087724420ab306cb64163226a55e75d476af0c17651bd256df78c8d35a495")
_WHY_1075 = (RUN_MODEL + ": a model seat has played it from this save; the save was made by a scripted game from the 1066 start (no seat), the same game as "
             "ireland1070-target-choice-3y five years on, and banked from the game's yearly autosave; a reload "
             "through this backend was compared with the read taken when it was made")
BUILTIN_DICTS["ireland1075-succession-5y"] = _decision(
    "ireland1075-succession-5y",
    "A decision task from a game under way: Murchad, Count of Dublin, 50, on 1075.01.01, rules five counties "
    "(Dublin, Oriel, Breifne, Leinster with its duchy, Ossory) with three sons of age, and the forecast says the "
    "realm splits at his death: the second son leaves with Dublin and Breifne. He is 308 gold in debt. Five "
    "game-years (to 1080.01.01), the Ireland task's reward, goal and wake cadence, a 365-day step when no wake "
    "is asked. Scored at the end (outcome succession): the counties that stay with the primary heir (at the "
    "death if he dies, else by the forecast at the end) and the succession levers used, at their price.",
    save=_SAVE_1075[0], sha256=_SAVE_1075[1], date="1075.01.01", years=5, cycle=365, outcome="succession",
    why=_WHY_1075)
BUILTIN_DICTS["ireland1075-faction-3y"] = _decision(
    "ireland1075-faction-3y",
    "A decision task from a game under way: Murchad, Count of Dublin, on 1075.01.01, faces a peasant faction at "
    "68 of 100 discontent (three of his counties, 72% of his power) and a populist one at 61 (one county, 54%); "
    "the game's AI answers their demand unless he sets an answer. Three game-years (to 1078.01.01), the Ireland "
    "task's reward, goal and wake cadence. Scored at the end (outcome faction): the counties kept, and whether "
    "an answer was set before the first demand was answered.",
    save=_SAVE_1075[0], sha256=_SAVE_1075[1], date="1075.01.01", years=3, cycle=73, outcome="faction",
    why=_WHY_1075)

# A succession that bites: the yearly autosave of 1076.01.01 from a model-played game of
# ireland1066-kingdom-30y-wake: Murchad 51, five counties, health under 4, the forecast saying the realm splits; he
# died in 1077 in that game and confederate partition took the realm from 5 counties to 2. Scored by the counties in
# the primary heir's realm at the horizon (the death is not scripted: a replay's death falls where the game's own
# health checks put it).
BUILTIN_DICTS["ireland1076-succession-death-4y"] = _decision(
    "ireland1076-succession-death-4y",
    "A decision task from a game under way: Murchad, Count of Dublin, 51, health under 4, on 1076.01.01, a vassal "
    "of Munster, rules counties his three adult sons will split at his death under confederate partition (in the "
    "game this save comes from he died in 1077 and five counties became two). Four game-years (to 1080.01.01), "
    "the Ireland task's reward, goal and wake cadence. Scored at the end (outcome succession): the counties in the "
    "primary heir's realm (at the death if he dies, else by the forecast at the end) and the levers used.",
    save="dublin_1076_01_01.ck3", sha256="8b3dc2cfdff0caf3c8973b45993233b6ba7272ee9bf045d243c4024e004cab8b",
    date="1076.01.01", years=4, cycle=73, outcome="succession",
    why=RUN_UNSEATED + ": a no-op seat played it from this save; the save is the yearly autosave of a "
        "model-played game of ireland1066-kingdom-30y-wake (the file is not shipped); a reload through this "
        "backend was checked by a scripted driver")


def builtin(name: str) -> Scenario:
    if name not in BUILTIN_DICTS:
        raise EnvRefusal(R_SCENARIO_UNKNOWN, f"{name!r} is not one of {sorted(BUILTIN_DICTS)}", "scenario")
    return scenario_from_dict(BUILTIN_DICTS[name])


def resolve(name_or_path: str | Path) -> Scenario:
    """A built-in id, or a path to a scenario TOML."""
    s = str(name_or_path)
    if s in BUILTIN_DICTS:
        return builtin(s)
    if s.endswith(".toml"):
        return load_scenario(s)
    raise EnvRefusal(R_SCENARIO_UNKNOWN, f"{s!r} is neither a built-in id nor a .toml file", "scenario")


#: whether a user can obtain a task's start: no save needed (the fake backend), a save the user makes (no sha256
#: declared: any save of the start under that name), or one declared by its sha256, which nothing ships and which a
#: user cannot make (its bytes are one game's)
START_NONE, START_YOURS, START_NOT_OBTAINABLE = "no_save_needed", "your_own_save", "declared_save_not_shipped"


def start_availability(sc: "Scenario") -> tuple[str, bool]:
    """(how the task's start is had, whether a user can run the task with what they can obtain)."""
    if sc.backend != "ck3":
        return START_NONE, True
    if sc.start_save_sha256 is None:
        return START_YOURS, True
    return START_NOT_OBTAINABLE, False

