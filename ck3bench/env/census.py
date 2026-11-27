"""Seats chosen from the world at the start: the census rule.

A scenario may name a seat by `census_rank` instead of a character id: rank r
is the r-th of the FIRST seat's vassals under the duke rule, read at the
scenario's start date:

    a vassal of the first seat (`query_realm(character_id=first).vassals[].id`)
    whose ruler row (`search_characters(filter=ruler, ...)`) has tier 3 (duke)
    and at least one realm county, ranked by `military_strength` (from the
    vassal row), ties by lowest id.

"Realm county" here is the ruler row's `realm_counties`: the counties held by
the vassal or by anyone in his vassal tree (the bridge credits each holder's
own counties to him and to every liege above him). It is the env's
`realm_counties` metric for a vassal, read from the same listing
(`ck3.py`), and NOT `query_realm`'s `realm_total_counties`, which for a vassal
counts only the counties he holds himself (it credits holders to their
independent top liege alone). On the recorded replies of a live 1066 France
census the two read 9 and 1 for the Duke of Normandy. The census record a
trajectory header carries names each ranked vassal's `realm_counties` and
`military_strength`, the values it ranked on.

The reads are existing bridge verbs on the control session; this module only
ranks what `read(action, params)` returns, so it runs unchanged against a
recording (`test_census.py`).

Refused, never defaulted: a read that is refused or malformed
(`census_read_unavailable`), fewer qualifying vassals than the highest declared
rank (`census_too_few_dukes`).

A seat may also be named by the title its ruler holds at the start
(`holder_of`, `select_by_holder`): a runtime character id differs from the
game's history id and from one save to the next, so such a seat is resolved at
open and never written into the task. The rule:

    query_de_jure(title_key=T) names T (`title.key` == T) and its holder
    (`title.holder_id`); query_character(holder) reads his name, which must be
    the declared one: `name` against `holder_name`, or, while the game has not
    localised it (`name_pending`), `name_key` against `holder_name_key`.

A title with no holder, a holder whose name is not the declared one, or a name
that does not read (or reads only as a key the task does not declare) is
refused (`census_seat_unconfirmed`), the refusal naming what was read.
"""
from __future__ import annotations

from typing import Any, Callable, Mapping, Optional

from .errors import R_CENSUS_READ, R_CENSUS_SEAT, R_CENSUS_TOO_FEW, EnvRefusal

DUKE_TIER = 3
LISTING_PARAMS = {"filter": "ruler", "sort": "realm_counties", "max_results": 5000, "verbose": True}
HOLDER_RULE = ("the holder of the declared title at the start (query_de_jure title.holder_id), confirmed by his "
               "name (query_character name, or name_key while the name is pending)")
RULE = ("vassals of the first seat with a tier-3 ruler row and realm_counties >= 1, ranked by "
        "military_strength, ties by lowest id")

Read = Callable[[str, dict], Any]


def _refusal(reply: Any) -> Optional[str]:
    """The bridge's refusal in any of its shapes (error / success false / ok false)."""
    if not isinstance(reply, dict):
        return f"non_object_reply:{type(reply).__name__}"
    if reply.get("error"):
        return str(reply["error"])
    if reply.get("ok") is False or reply.get("success") is False:
        return str(reply.get("error") or reply.get("reason") or "success_false")
    return None


def _rows(reply: Any, key: str) -> list[dict]:
    v = reply.get(key) if isinstance(reply, dict) else None
    return [x for x in v if isinstance(x, dict)] if isinstance(v, list) else []


def _military(row: Mapping[str, Any]) -> int:
    v = row.get("military_strength")
    return v if isinstance(v, int) and not isinstance(v, bool) else -1


def rank_vassals(read: Read, liege_id: int) -> tuple[list[dict], list[dict]]:
    """(qualifying vassals ranked, rejected vassals with why) for `liege_id`."""
    by_id = realm_rows(read)
    realm = read("query_realm", {"character_id": liege_id})
    why = _refusal(realm)
    if why is not None:
        raise EnvRefusal(R_CENSUS_READ, f"query_realm(character_id={liege_id}) answered {why!r}", "census")
    if not isinstance(realm.get("vassals"), list):
        raise EnvRefusal(R_CENSUS_READ, f"query_realm({liege_id}) carries no vassals list", "census")
    dukes: list[dict] = []
    rejected: list[dict] = []
    for v in _rows(realm, "vassals"):
        vid = v.get("id")
        row = by_id.get(vid)
        if not isinstance(vid, int) or row is None:
            rejected.append({"id": vid, "why": "no_ruler_row"})
        elif row.get("tier") != DUKE_TIER:
            rejected.append({"id": vid, "why": f"tier={row.get('tier')}"})
        elif not isinstance(row.get("realm_counties"), int) or row["realm_counties"] < 1:
            rejected.append({"id": vid, "why": f"realm_counties={row.get('realm_counties')!r}"})
        else:
            dukes.append({"id": vid, "military_strength": v.get("military_strength"),
                          "realm_counties": row["realm_counties"]})
    dukes.sort(key=lambda d: (-_military(d), d["id"]))
    return dukes, rejected


def select_by_rank(read: Read, liege_id: int, ranks: Mapping[str, int]) -> tuple[dict[str, int], dict]:
    """seat -> character id for every `census_rank` seat, and the census record
    (rule, liege, ranked ids, rejected) a trajectory header carries."""
    dukes, rejected = rank_vassals(read, liege_id)
    need = max(ranks.values()) if ranks else 0
    if len(dukes) < need:
        raise EnvRefusal(R_CENSUS_TOO_FEW, f"liege {liege_id} has {len(dukes)} qualifying vassals; the scenario "
                                           f"seats rank {need}", "census")
    chosen = {seat: dukes[r - 1]["id"] for seat, r in ranks.items()}
    return chosen, {"rule": RULE, "liege_id": liege_id, "ranked": [d["id"] for d in dukes],
                    "ranked_rows": [dict(d) for d in dukes], "rejected": len(rejected), "chosen": chosen}


def realm_rows(read: Read) -> dict[int, dict]:
    """The ruler listing the census ranks on, by character id (its rows carry
    `realm_counties` and `is_independent`). Refused like a census read."""
    listing = read("search_characters", dict(LISTING_PARAMS))
    why = _refusal(listing)
    if why is not None:
        raise EnvRefusal(R_CENSUS_READ, f"search_characters(ruler) answered {why!r}", "census")
    return {r["id"]: r for r in _rows(listing, "characters") if isinstance(r.get("id"), int)}


def _int_id(v: Any) -> Optional[int]:
    return v & 0xFFFFFF if isinstance(v, int) and not isinstance(v, bool) and v > 0 else None


def select_by_holder(read: Read, seat: str, title_key: str, holder_name: str,
                     holder_name_key: Optional[str]) -> tuple[int, dict]:
    """(the character id of `seat`: the holder of `title_key` now, the record a
    trajectory header carries: the rule, the title, the holder, the name as
    read and the field it came from). Refused as the module docstring says."""
    rec: dict[str, Any] = {"rule": HOLDER_RULE, "seat": seat, "title_key": title_key,
                           "declared_name": holder_name, "declared_name_key": holder_name_key}
    dj = read("query_de_jure", {"title_key": title_key})
    why = _refusal(dj)
    if why is not None:
        raise EnvRefusal(R_CENSUS_READ, f"query_de_jure(title_key={title_key}) answered {why!r}", "census")
    title = dj.get("title")
    got = title.get("key") if isinstance(title, dict) else None
    if got != title_key:
        raise EnvRefusal(R_CENSUS_READ, f"query_de_jure(title_key={title_key}) names {got!r}, not {title_key}",
                         "census")
    hid = _int_id(title.get("holder_id"))
    rec["title_id"] = title.get("title_id")
    if hid is None:
        raise EnvRefusal(R_CENSUS_SEAT, f"seat {seat}: {title_key} has no holder (holder_id "
                                        f"{title.get('holder_id')!r})", "census")
    rec["holder_id"] = hid
    prof = read("query_character", {"character_id": hid})
    why = _refusal(prof)
    if why is not None:
        raise EnvRefusal(R_CENSUS_READ, f"query_character(character_id={hid}) answered {why!r}", "census")
    for k in ("house_name", "dynasty_name", "age"):
        if k in prof:
            rec[k] = prof[k]
    name, key = prof.get("name"), prof.get("name_key")
    if isinstance(name, str) and name:
        rec.update(name_read=name, name_field="name")
        want: Optional[str] = holder_name
    elif prof.get("name_pending") is True and isinstance(key, str) and key:
        rec.update(name_read=key, name_field="name_key")
        if holder_name_key is None:
            raise EnvRefusal(R_CENSUS_SEAT, f"seat {seat}: {title_key}'s holder {hid} reads only as the name key "
                                            f"{key!r} (the game has not localised it) and the task declares no "
                                            "holder_name_key", "census")
        want = holder_name_key
    else:
        raise EnvRefusal(R_CENSUS_SEAT, f"seat {seat}: {title_key}'s holder {hid} has no name in query_character "
                                        "(neither name nor a pending name_key)", "census")
    if rec["name_read"] != want:
        raise EnvRefusal(R_CENSUS_SEAT, f"seat {seat}: {title_key} is held by {hid}, whose {rec['name_field']} "
                                        f"reads {rec['name_read']!r}, not the declared {want!r}", "census")
    return hid, rec
