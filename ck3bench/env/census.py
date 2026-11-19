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
"""
from __future__ import annotations

from typing import Any, Callable, Mapping, Optional

from .errors import R_CENSUS_READ, R_CENSUS_TOO_FEW, EnvRefusal

DUKE_TIER = 3
LISTING_PARAMS = {"filter": "ruler", "sort": "realm_counties", "max_results": 5000, "verbose": True}
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
