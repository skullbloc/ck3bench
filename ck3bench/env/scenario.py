"""Scenario (task) declarations.

A scenario is everything an episode is reset FROM: the backend it needs, the
start save and date, the seats played (the agents), the horizon and the cycle
length (one step = one cycle of `cycle_days` game-days), the reward, an
optional goal, the tools a seat may call, the mods, and the game speed.

One reader for every source: a TOML file (`load_scenario`) and the built-in
tasks (`BUILTIN`) both go through `scenario_from_dict`, which types every key
and refuses an unknown one, a missing one or a wrong type. Nothing defaults
except the keys documented as optional below.

Every built-in carries `status` (`buildable_now`) and `status_reason`: what
has and has not been run of it is said in the declaration, not discovered at
run time.
"""
from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Optional

from . import calendar
from .errors import (R_SCENARIO_KEY, R_SCENARIO_MISSING, R_SCENARIO_SPEED, R_SCENARIO_TYPE,
                     R_SCENARIO_UNKNOWN, R_SCENARIO_VALUE, EnvRefusal)
from .comms import COMMS_TOOLS, CommsDecl, comms_from_dict
from .rewards import TEAM_MODES, RewardSpec
from .types import PUBLIC_TOOLS, SEAT_TOOLS

BACKENDS = ("ck3", "fake")
GOALS = ("none", "county_gain")
STATUSES = ("buildable_now",)
SELECTORS = ("character_id", "census_rank")

#: The fastest game speed the bridge reaches. Measured live (CK3 1.18.3.1, ck3agent
#: 2026.12.11): `set_speed 4` answers success and writes
#: 3, so a scenario at 4 or 5 could only be refused after a full game launch. The
#: reader refuses it when the declaration is read instead.
MAX_GAME_SPEED = 3
SPEED_BOUND_SOURCE = ("measured live on CK3 1.18.3.1 with ck3agent 2026.12.11: set_speed 4 answers success "
                      "and the game reads speed 3")
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
    start) or `census_rank` (the n-th largest vassal of the first seat at the
    start, read from the world at open by the census rule, census.py)."""
    name: str
    character_id: Optional[int] = None
    census_rank: Optional[int] = None
    role: str = "ruler"

    @property
    def selector(self) -> str:
        return "character_id" if self.character_id is not None else "census_rank"


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
                                             ("census_rank", s.census_rank), ("role", s.role))
                           if v is not None} for s in self.seats],
                "horizon_days": self.horizon_days, "cycle_days": self.cycle_days,
                "reward": self.reward.to_dict(), "goal": self.goal, "tools": list(self.tools),
                "mods": {"policy_mod": self.policy_mod, "proposal_mod": self.proposal_mod},
                "game_speed": self.game_speed, "status": self.status, "status_reason": self.status_reason,
                **({"fake": dict(self.fake)} if self.fake else {}),
                **({"comms": self.comms.to_dict()} if self.comms is not None else {})}


# ── the one reader ─────────────────────────────────────────────────────────

_TOP_REQUIRED = {"id": str, "description": str, "backend": str, "start": dict, "seats": list,
                 "horizon_days": int, "cycle_days": int, "reward": dict, "tools": list,
                 "game_speed": int, "status": str, "status_reason": str}
_TOP_OPTIONAL = {"goal": str, "mods": dict, "fake": dict, "comms": dict}
_START_KEYS = {"save": (str, type(None)), "save_sha256": (str, type(None)), "date": str}
_SEAT_KEYS = {"name": str, "character_id": int, "census_rank": int, "role": str}


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
        seats.append(SeatDecl(s["name"], s.get("character_id"), s.get("census_rank"), s.get("role", "ruler")))
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
    fake = d.get("fake", {})
    if fake and d["backend"] != "fake":
        raise EnvRefusal(R_SCENARIO_VALUE, f"{sid}: a [fake] table on a {d['backend']} scenario", "scenario")
    return Scenario(id=sid, description=d["description"], backend=d["backend"], start_save=start.get("save"),
                    start_save_sha256=start.get("save_sha256"), start_date=start["date"], seats=tuple(seats),
                    horizon_days=h, cycle_days=c, reward=reward, goal=goal, tools=tools,
                    policy_mod=mods.get("policy_mod", False), proposal_mod=mods.get("proposal_mod", False),
                    game_speed=d["game_speed"], status=d["status"], status_reason=d["status_reason"],
                    fake=dict(fake), comms=comms)


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
         speed=MAX_GAME_SPEED, team_seat=None, comms=None):
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
            "status_reason": why, **({"comms": comms} if comms is not None else {})}


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
         why="its reads (query_character gold) and the clock route (the pause alarm) have run live: the same "
             "task at 24 steps of 30 days completed its horizon with no policy; this declaration's 73-day "
             "cycles have not been run"),
    _ck3("france1066-gain-county-5y",
         "Five game-years as the King of France: end with more counties than you started with. "
         "25 steps of 73 days; ends for the seat on the first step its realm exceeds the start.",
         seats=_KING, horizon=5 * _YEAR, cycle=73, terms=[("county_gain_goal", 1.0), ("line_survival", 1.0)],
         goal="county_gain", status="buildable_now",
         why="realm counties are read the way the baseline read them (query_realm realm_total_counties), "
             "and that read has run live; this task has not been run"),
    _ck3("france1066-hold-10y",
         "Ten game-years as the King of France: hold and grow the realm. 25 steps of 146 days. "
         "Baseline: eight unsteered games read 75 to 88 counties at 10 years from 84.",
         seats=_KING, horizon=10 * _YEAR, cycle=146, terms=[("realm_counties_delta", 1.0), ("line_survival", 50.0)],
         status="buildable_now",
         why="ends on the baseline's 10-year mark; a variant (20 steps of 180 days, ending 50 days short of "
             "the mark) completed its horizon live with no policy; this declaration's 146-day cycles have not "
             "been run"),
    _ck3("france1066-council-10y",
         "Ten game-years: the King of France and his five largest vassals, each a seat, with messages and "
         "notepads; one shared reward, the king's realm counties. 25 steps of 146 days.",
         seats=_KING + [{"name": f"duke{i}", "census_rank": i, "role": "vassal"} for i in range(1, 6)],
         horizon=10 * _YEAR, cycle=146, terms=[("realm_counties_delta", 1.0), ("line_survival", 50.0)],
         team="seat", team_seat="king", comms=COUNCIL_COMMS,
         tools=_GAME_TOOLS + ["msg_send", "note_read", "note_write"], status="buildable_now",
         why="a two-year variant (10 steps of 73 days, scripted policies, no model) ran live to its "
             "horizon: the census read through this backend chose 31808, 31466, 29249, 29247 and 36151, six "
             "toolkit servers (one per seat, each bound to its ruler) ran together, messages and the shared "
             "notepad were delivered in seat order, and a succession in the king's line was followed; this "
             "declaration's 146-day cycles over ten years have not been run, nor any model-driven seat"),
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
