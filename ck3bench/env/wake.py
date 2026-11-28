"""Wake conditions: a step whose length is set by what happens in the world.

A scenario that declares `[wake]` (check_days, max_days) is a WAKE task. In it a
seat may end its turn with a wake request instead of taking the fixed cycle:

    {"max_days": 365, "when": [{"event": "claim_gained"},
                               {"metric": "gold", "op": ">=", "value": 120}]}

and the step's clock runs until the FIRST check (every `check_days` game-days) at
which any live seat's condition holds, or until the earliest `max_days` among the
seats, or the horizon, whichever comes first. Every live seat then gets its turn
(the game paused), as in a fixed task. A seat that sends no request takes the
scenario's `cycle_days`; that default is recorded as `declared: false`, never as
a request the seat made.

The vocabulary is closed and small, evaluated on the reads the backend already
makes for metrics, never on free-form code:

  {"event": E}                       E in EVENTS: something the backend witnessed
                                     between the step's start and the check
  {"metric": M, "op": OP, "value": V}  M in METRICS, OP ">=" or "<=", V a number:
                                     the metric as read at the check

Conditions are OR'ed. A condition already true when the step starts fires at the
first check. A backend says which of these it can witness
(`Backend.wake_vocabulary`); a condition it cannot witness is refused when the
request is made (`wake_condition_unwitnessable`), never evaluated as false.
"""
from __future__ import annotations

from typing import Any, Mapping, Optional

from .errors import R_WAKE_SHAPE, R_WAKE_UNDECLARED, R_WAKE_UNSUPPORTED, R_WAKE_UNWITNESSABLE, EnvRefusal

__all__ = ["EVENTS", "METRICS", "OPS", "MAX_CONDITIONS", "parse_request", "holds", "fired", "R_WAKE_SHAPE",
           "R_WAKE_UNDECLARED", "R_WAKE_UNSUPPORTED", "R_WAKE_UNWITNESSABLE"]

#: The events, each a fact two reads of the backend witnessed (start of the step, the check).
EVENTS = {
    "ruler_changed": "the line's current character is not the one at the step's start (a death and succession)",
    "realm_changed": "the realm's county count (realm_counties) differs from the step's start",
    "title_gained": "the character holds the scenario's target title and did not at the step's start",
    "war_ended": "a war the seat was in ended since the step's start",
    "war_started": "a war the seat is in started since the step's start",
    "claim_gained": "the character gained a claim since the step's start (a fabrication arrived)",
    "fabrication_ended": "a fabrication that was running at the step's start is no longer running",
    "title_lost": "the character no longer holds a county-or-higher title it held at the step's start",
    "war_declared_on": "a war in which the seat is on the DEFENDING side started since the step's start",
    # a birth can turn a succession into a split, and a long step can run on from it to the ruler's death with no
    # read between
    "child_born": "the character has a child he did not have at the step's start (a birth can split a partition)",
    # a faction far over the threshold (171 % of the ruler's power) can send its demand months after the seat's last
    # read, and win the war that follows
    "faction_over_threshold": ("a faction against the character is at or over the demand power threshold (80) "
                               "and was not at the step's start: its discontent now climbs to the demand"),
    # an heir of another dynasty (a matrilineal marriage's son) can be born and inherit with no read between
    "heir_other_dynasty": ("the primary heir of the character's primary title is of another dynasty and was not "
                           "at the step's start (a birth, a death or a marriage's child)"),
}
#: The events a backend reports as id lists (the ids the reading names: claims by title, wars by war,
#: fabrications by target, titles by key); the prototype's fake reports `war_ended` as a monotone count.
#: A GAINED kind fires on an id the check names that the step's start did not; a LOST kind on one the start
#: named that the check does not.
GAINED = ("war_started", "claim_gained", "war_declared_on", "child_born", "faction_over_threshold",
          "heir_other_dynasty")
LOST = ("war_ended", "fabrication_ended", "title_lost")
#: The metrics a threshold may read (types.METRIC_DEFINITIONS defines each; `piety` is the line's
#: character's piety as the backend reads it at a check, not a reward metric).
METRICS = ("gold", "prestige", "realm_counties", "dejure_counties", "piety")
OPS = (">=", "<=")
#: The most conditions one seat may hold for one step (a cap, refused above, never cut).
MAX_CONDITIONS = 8


def parse_request(seat: str, req: Any, *, cap_days: int, default_days: int,
                  vocabulary: Mapping[str, tuple]) -> dict[str, Any]:
    """One seat's wake request -> its record {declared, max_days, when}. `req` None
    is the default (the scenario's cycle, no conditions; declared false)."""
    if req is None:
        return {"declared": False, "max_days": default_days, "when": []}
    if not isinstance(req, Mapping):
        raise EnvRefusal(R_WAKE_SHAPE, f"{seat}: a wake request is an object, got {type(req).__name__}", "env")
    extra = set(req) - {"max_days", "when"}
    if extra:
        raise EnvRefusal(R_WAKE_SHAPE, f"{seat}: unknown keys {sorted(extra)} (max_days, when)", "env")
    md = req.get("max_days", default_days)
    if not isinstance(md, int) or isinstance(md, bool) or not 1 <= md <= cap_days:
        raise EnvRefusal(R_WAKE_SHAPE, f"{seat}: max_days {md!r} must be an int in 1..{cap_days}", "env")
    when = req.get("when", [])
    if not isinstance(when, list):
        raise EnvRefusal(R_WAKE_SHAPE, f"{seat}: when is a list of conditions", "env")
    if len(when) > MAX_CONDITIONS:
        raise EnvRefusal(R_WAKE_SHAPE, f"{seat}: {len(when)} conditions, at most {MAX_CONDITIONS}", "env")
    return {"declared": True, "max_days": md,
            "when": [_condition(seat, i, c, vocabulary) for i, c in enumerate(when)]}


def _condition(seat: str, i: int, c: Any, vocabulary: Mapping[str, tuple]) -> dict[str, Any]:
    where = f"{seat}: when[{i}]"
    if not isinstance(c, Mapping):
        raise EnvRefusal(R_WAKE_SHAPE, f"{where} is not an object", "env")
    if set(c) == {"event"}:
        e = c["event"]
        if e not in EVENTS:
            raise EnvRefusal(R_WAKE_SHAPE, f"{where}: event {e!r} not in {sorted(EVENTS)}", "env")
        if e not in vocabulary.get("events", ()):
            raise EnvRefusal(R_WAKE_UNWITNESSABLE, f"{where}: this backend does not witness {e!r} (it witnesses "
                                                   f"{sorted(vocabulary.get('events', ()))})", "env")
        return {"event": e}
    if set(c) == {"metric", "op", "value"}:
        m, op, v = c["metric"], c["op"], c["value"]
        if m not in METRICS:
            raise EnvRefusal(R_WAKE_SHAPE, f"{where}: metric {m!r} not in {list(METRICS)}", "env")
        if m not in vocabulary.get("metrics", ()):
            raise EnvRefusal(R_WAKE_UNWITNESSABLE, f"{where}: this backend does not read {m!r} at a check", "env")
        if op not in OPS:
            raise EnvRefusal(R_WAKE_SHAPE, f"{where}: op {op!r} not in {list(OPS)}", "env")
        if not isinstance(v, (int, float)) or isinstance(v, bool) or v != v:
            raise EnvRefusal(R_WAKE_SHAPE, f"{where}: value {v!r} is not a number", "env")
        return {"metric": m, "op": op, "value": v}
    raise EnvRefusal(R_WAKE_SHAPE, f"{where}: a condition is {{event}} or {{metric, op, value}}, got "
                                   f"{sorted(c)}", "env")


def holds(cond: Mapping[str, Any], start: Mapping[str, Any], now: Mapping[str, Any]) -> tuple[bool, Any]:
    """(fired, the value read) for one condition, from two `Backend.wake_facts`
    readings: the step's start and the check. A metric not read at the check
    (`None`) does not fire, and the read says so (None)."""
    if "event" in cond:
        e = cond["event"]
        before, after = (start.get("events") or {}).get(e), (now.get("events") or {}).get(e)
        if e == "ruler_changed":
            return now.get("character_id") != start.get("character_id"), now.get("character_id")
        if e == "realm_changed":
            a, b = (start.get("metrics") or {}).get("realm_counties"), (now.get("metrics") or {}).get("realm_counties")
            return (a is not None and b is not None and a != b), b
        if e == "title_gained":
            a, b = (start.get("metrics") or {}).get("holds_target_title"), \
                (now.get("metrics") or {}).get("holds_target_title")
            return (a is False and b is True), b
        if isinstance(before, list) and isinstance(after, list):
            # id lists (the real backend): what the two readings name, compared as sets
            new = sorted(set(map(str, after)) - set(map(str, before))) if e in GAINED else \
                sorted(set(map(str, before)) - set(map(str, after))) if e in LOST else []
            return bool(new), new or None
        # counted events: the backend keeps a monotone count per kind; a rise fired
        return (isinstance(before, int) and isinstance(after, int) and after > before), after
    v = (now.get("metrics") or {}).get(cond["metric"])
    if v is None:
        return False, None
    return (v >= cond["value"] if cond["op"] == ">=" else v <= cond["value"]), v


def fired(requests: Mapping[str, dict], start: Mapping[str, Mapping], now: Mapping[str, Mapping],
          date: Optional[str]) -> list[dict[str, Any]]:
    """Every condition that holds at this check, per seat, in seat order then
    condition order: [{seat, index, condition, read, date}]."""
    out = []
    for seat, rec in requests.items():
        if seat not in now:
            continue
        for i, c in enumerate(rec["when"]):
            ok, read = holds(c, start[seat], now[seat])
            if ok:
                cond = {k: v for k, v in c.items() if k != "forced"}
                out.append({"seat": seat, "index": i, "condition": cond, "read": read, "date": date,
                            **({"forced": True} if c.get("forced") else {})})
    return out
