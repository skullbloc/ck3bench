"""Decision-task outcomes: what a short task scores, read from the game.

A scenario may declare `outcome = "<kind>"` (one of KINDS). The env then reads
the world with the public tools on the seat's own toolkit server three ways:

  start    the standard read (READS) once the world is open, written in the
           trajectory header as `outcome_start`;
  witness  after each APPLIED `declare_war` call of the seat, the strength
           read for that target at that moment (query relative_power, and
           cbs target_state), written on that call's record as `witness`;
  end      the standard read again when the episode ends, before the game
           closes, written in the end record as `outcome_end`, with
           `outcome` = score(kind, start, end, calls): the task's score.

Nothing here reads the seat's words: the score is a function of the reads and
of the seat's tool calls as the game answered them. A read that failed is
`{"unread": <the call and why>}`, never a default; a score that needs it says
`unscored` with the reason.

The kinds (scenario.py declares the tasks and their start states):
  target_choice  which claim the seat declared on, the strength read at that
                 declaration, and the war's result within the episode
  succession     how many counties stay with the primary heir at the ruler's
                 death (or, with no death in the episode, the forecast at the
                 end), and which succession levers were used, at what price
  faction        the counties kept when a faction near its threshold makes its
                 demand, and whether the seat set an answer (a faction
                 respond_demand or an event_rule on a faction demand) before the
                 first demand was answered
"""
from __future__ import annotations

from typing import Any, Callable, Mapping, Optional, Sequence

KINDS = ("target_choice", "succession", "faction")
#: A read: (tool name, arguments) -> (the envelope's `result` or None, the call as written or why it failed)
Reader = Callable[[str, dict], "tuple[Any, str]"]
#: offensive_ratio at or above this, by the toolkit's own hint ("offensive_ratio>=1.5 means winnable")
WINNABLE_RATIO = 1.5
#: the `do` intents and interactions counted as succession levers, by substring of the call's arguments
LEVER_WORDS = ("disinherit", "vow", "designate_heir", "change_law", "succession_law", "mint_title", "create_title",
               "crown_authority", "grant_title", "abdicate")


def _pick(d: Any, keys: Sequence[str]) -> dict:
    return {k: d.get(k) for k in keys} if isinstance(d, Mapping) else {}


def _read(tool: Reader, name: str, args: dict) -> tuple[Optional[dict], Optional[dict]]:
    res, what = tool(name, args)
    if not isinstance(res, Mapping):
        return None, {"unread": what}
    return dict(res), None


def power(tool: Reader, target_id: int) -> dict:
    """The strength read for one target: the toolkit's relative_power and the cbs target_state."""
    rp, miss = _read(tool, "query", {"domain": "relative_power", "target_id": target_id})
    out: dict[str, Any] = {"target_id": target_id}
    out["relative_power"] = miss or _pick(rp, ("my_strength", "my_callable_strength", "target_strength",
                                               "target_callable_strength", "offensive_ratio", "offensive_verdict"))
    cb, miss = _read(tool, "cbs", {"target_id": target_id})
    out["target_state"] = miss or (cb.get("target_state") if isinstance(cb.get("target_state"), Mapping)
                                   else {"unread": "cbs gave no target_state"})
    return out


def read_standard(tool: Reader) -> dict:
    """The world as a decision task's score reads it (every section a read or `unread`)."""
    out: dict[str, Any] = {}
    ch, miss = _read(tool, "inspect", {"subject": "character"})
    out["character"] = miss or _pick(ch, ("id", "name", "age", "alive", "gold", "prestige", "piety", "health",
                                          "game_date"))
    eco, miss = _read(tool, "inspect", {"subject": "economy"})
    out["economy"] = miss or _pick(eco, ("character_id", "domain_size", "realm_total_counties", "military_strength"))
    t, miss = _read(tool, "inspect", {"subject": "title", "title_key": "k_ireland"})
    out["k_ireland"] = miss or {"holder_id": t.get("holder_id"), "summary": t.get("summary"),
                                "counties": [_pick(c, ("title_id", "key", "holder_id", "realm_ruler_id",
                                                       "you_control", "your_claim")) for c in t.get("counties") or []]}
    cl, miss = _read(tool, "inspect", {"subject": "claims"})
    out["claims"] = miss or [_pick(c, ("title_id", "title_name", "holder_id", "pressed", "holder_in_alliance",
                                       "declarable", "block_reason")) for c in cl.get("claims") or []]
    ws, miss = _read(tool, "inspect", {"subject": "war_stance"})
    out["wars"] = miss or [_pick(w, ("war_id", "name", "side", "war_score", "attacker_id", "defender_id"))
                           for w in ws.get("wars") or []]
    sp, miss = _read(tool, "succession_plan", {})
    if miss:
        out["succession"] = miss
    else:
        ph = sp.get("primary_heir") if isinstance(sp.get("primary_heir"), Mapping) else {}
        out["succession"] = {"outcome": sp.get("outcome"), "passes_intact": sp.get("passes_intact"),
                             "eligible_heir_count": sp.get("eligible_heir_count"),
                             "primary_heir_id": ph.get("id"),
                             "primary_keeps": [k.get("key") for k in ph.get("keeps_titles") or []
                                               if isinstance(k, Mapping)],
                             "succession_eta_years": sp.get("succession_eta_years")}
    fa, miss = _read(tool, "inspect", {"subject": "factions"})
    out["factions"] = miss or {
        "factions": [_pick(f, ("faction_id", "type", "county_member_count", "discontent", "discontent_threshold",
                               "power_pct_of_liege", "ultimatum", "at_war")) for f in fa.get("factions") or []],
        "demand_response": fa.get("demand_response"),
        "demands_answered": [_pick(d, ("seq", "date", "event", "answered", "by", "refused"))
                             for d in fa.get("demands_answered") or []]}
    if isinstance(out["claims"], list):
        out["claim_power"] = [power(tool, c["holder_id"]) for c in out["claims"]
                              if isinstance(c.get("holder_id"), int)]
    return out


# ── scoring ────────────────────────────────────────────────────────────────

def _counties_in_realm(read: Mapping) -> Optional[set]:
    k = read.get("k_ireland")
    if not isinstance(k, Mapping) or "counties" not in k:
        return None
    return {c["key"] for c in k["counties"] if isinstance(c, Mapping) and c.get("you_control")}


def _applied(call: Mapping) -> bool:
    res = call.get("result") if isinstance(call.get("result"), Mapping) else {}
    env = res.get("result") if isinstance(res.get("result"), Mapping) else {}
    return res.get("ok") is True and env.get("ok") is True and env.get("blocked") in (None, {}) \
        and env.get("applied", True) is not False


def _declarations(calls: Sequence[Mapping]) -> list[dict]:
    out = []
    for c in calls:
        call = c.get("call") or {}
        if call.get("tool_name") != "declare_war":
            continue
        args = call.get("arguments") or {}
        da = args.get("declare_args") if isinstance(args.get("declare_args"), Mapping) else args
        out.append({"step": c.get("step"), "game_date": c.get("game_date"), "applied": _applied(c),
                    "target_id": da.get("target_id"), "cb": da.get("cb"), "cb_title_id": da.get("cb_title_id"),
                    "witness": c.get("witness")})
    return out


def score_target_choice(start: Mapping, end: Mapping, calls: Sequence[Mapping]) -> dict:
    decl = _declarations(calls)
    applied = [d for d in decl if d["applied"]]
    s_cty, e_cty = _counties_in_realm(start), _counties_in_realm(end)
    out: dict[str, Any] = {"kind": "target_choice", "declarations_tried": len(decl), "declared": bool(applied)}
    if s_cty is None or e_cty is None:
        out["counties"] = {"unscored": "k_ireland was not read at the start or the end"}
    else:
        out["counties"] = {"start": len(s_cty), "end": len(e_cty), "gained": sorted(e_cty - s_cty),
                           "lost": sorted(s_cty - e_cty)}
    if not applied:
        return out
    first = applied[0]
    w = first.get("witness") if isinstance(first.get("witness"), Mapping) else None
    rp = (w or {}).get("relative_power")
    ratio = rp.get("offensive_ratio") if isinstance(rp, Mapping) else None
    out["first"] = {"game_date": first["game_date"], "target_id": first["target_id"], "cb": first["cb"],
                    "cb_title_id": first["cb_title_id"], "strength_at_declaration": rp if rp else
                    {"unscored": "no witness read on this declaration"},
                    "target_state_at_declaration": (w or {}).get("target_state")}
    out["beatable_at_declaration"] = (ratio >= WINNABLE_RATIO) if isinstance(ratio, (int, float)) else None
    # the start's claim targets ranked by callable strength: did the seat pick the weakest it held a claim on?
    cp = [p for p in start.get("claim_power") or [] if isinstance(p, Mapping)
          and isinstance((p.get("relative_power") or {}).get("target_callable_strength"), (int, float))]
    if cp:
        weakest = min(cp, key=lambda p: p["relative_power"]["target_callable_strength"])
        out["weakest_claim_target_at_start"] = weakest["target_id"]
        out["chose_weakest"] = first["target_id"] == weakest["target_id"]
    # the war's result at the end: still at war with the target, or the war is over and the county came or not
    wars = end.get("wars")
    title_key = None
    for c in start.get("claims") or []:
        if isinstance(c, Mapping) and c.get("title_id") == first["cb_title_id"]:
            title_key = c.get("title_name")
    if not isinstance(wars, list):
        out["war_result"] = {"unscored": "wars not read at the end"}
    else:
        open_w = [x for x in wars if x.get("side") == "attacker" and x.get("defender_id") == first["target_id"]]
        if open_w:
            out["war_result"] = {"state": "ongoing", "war_score": open_w[0].get("war_score")}
        else:
            k = end.get("k_ireland") if isinstance(end.get("k_ireland"), Mapping) else {}
            row = [c for c in k.get("counties") or [] if isinstance(c, Mapping)
                   and c.get("title_id") == first["cb_title_id"]]
            if row:
                out["war_result"] = {"state": "won" if row[0].get("you_control") else "not_won",
                                     "note": "no war on the target at the end; won = the claimed county is in the "
                                             "realm at the end"}
            else:
                out["war_result"] = {"unscored": f"the claimed title {first['cb_title_id']} is not a county of "
                                                 "k_ireland as read at the end"}
    out["claim_title"] = title_key
    return out


def _levers(calls: Sequence[Mapping]) -> list[dict]:
    out = []
    for c in calls:
        call = c.get("call") or {}
        if call.get("tool_name") != "do":
            continue
        text = repr(call.get("arguments")).lower()
        hit = [w for w in LEVER_WORDS if w in text]
        if hit:
            out.append({"game_date": c.get("game_date"), "words": hit, "applied": _applied(c),
                        "arguments": call.get("arguments")})
    return out


def score_succession(start: Mapping, end: Mapping, calls: Sequence[Mapping]) -> dict:
    sc, ec = start.get("character") or {}, end.get("character") or {}
    out: dict[str, Any] = {"kind": "succession", "levers": _levers(calls)}
    s_cty, e_cty = _counties_in_realm(start), _counties_in_realm(end)
    out["counties"] = ({"start": len(s_cty), "end": len(e_cty)} if s_cty is not None and e_cty is not None
                       else {"unscored": "k_ireland was not read at the start or the end"})
    died = isinstance(sc.get("id"), int) and isinstance(ec.get("id"), int) and sc["id"] != ec["id"]
    out["ruler_changed"] = died if isinstance(ec.get("id"), int) else None
    if died:
        out["basis"] = "death in the episode: the counties in the heir's realm at the end"
        out["score_counties_kept"] = len(e_cty) if e_cty is not None else None
    else:
        su = end.get("succession") if isinstance(end.get("succession"), Mapping) else {}
        keeps = su.get("primary_keeps")
        out["basis"] = ("no death in the episode: the forecast at the end (succession_plan primary heir's "
                        "keeps_titles, county tier)")
        out["forecast_end"] = {"outcome": su.get("outcome"), "passes_intact": su.get("passes_intact")}
        if su.get("passes_intact") is True and e_cty is not None:
            out["score_counties_kept"] = len(e_cty)
        elif isinstance(keeps, list) and e_cty is not None:
            out["score_counties_kept"] = len([k for k in keeps if isinstance(k, str) and k.startswith("c_")
                                              and k in e_cty])
        else:
            out["score_counties_kept"] = None
            out["unscored"] = su.get("unread") or "the end forecast named no primary heir titles"
    for cur in ("gold", "prestige", "piety"):
        a, b = sc.get(cur), ec.get(cur)
        if isinstance(a, (int, float)) and isinstance(b, (int, float)):
            out.setdefault("change", {})[cur] = round(b - a, 2)
    return out


def _answer_calls(calls: Sequence[Mapping]) -> list[dict]:
    out = []
    for c in calls:
        call = c.get("call") or {}
        args = call.get("arguments") or {}
        if call.get("tool_name") != "do":
            continue
        inner = args.get("args") if isinstance(args.get("args"), Mapping) else {}
        is_resp = args.get("intent") == "faction" and inner.get("action") == "respond_demand"
        is_rule = args.get("intent") == "event_rule" and "faction_demand" in str(inner.get("event") or "")
        if is_resp or is_rule:
            out.append({"game_date": c.get("game_date"), "what": "respond_demand" if is_resp else "event_rule",
                        "args": inner, "applied": _applied(c)})
    return out


def score_faction(start: Mapping, end: Mapping, calls: Sequence[Mapping]) -> dict:
    out: dict[str, Any] = {"kind": "faction"}
    s_cty, e_cty = _counties_in_realm(start), _counties_in_realm(end)
    if s_cty is None or e_cty is None:
        out["counties"] = {"unscored": "k_ireland was not read at the start or the end"}
    else:
        out["counties"] = {"start": len(s_cty), "end": len(e_cty), "lost": sorted(s_cty - e_cty),
                           "gained": sorted(e_cty - s_cty)}
        out["score_counties_kept"] = len(s_cty & e_cty)
    fe = end.get("factions") if isinstance(end.get("factions"), Mapping) else {}
    if "unread" in fe or not fe:
        out["demands"] = {"unscored": "factions not read at the end"}
        return out
    dem = [d for d in fe.get("demands_answered") or [] if isinstance(d, Mapping)]
    out["demands"] = dem
    out["factions_end"] = fe.get("factions")
    answers = [a for a in _answer_calls(calls) if a["applied"]]
    out["answers_set"] = answers
    first = min((d.get("date") for d in dem if isinstance(d.get("date"), str)), default=None)
    out["first_demand_date"] = first
    out["answer_before_letter"] = (any(isinstance(a["game_date"], str) and a["game_date"] <= first for a in answers)
                                   if first is not None else None)
    # per demand: the seat's last applied rule on that event before it, and whether the game took that option
    # (a rule names an option key or an index; an index is not resolved here: `matched` is then null)
    rows = []
    for dm in dem:
        prior = [a for a in answers if a["what"] == "event_rule" and a["args"].get("event") == dm.get("event")
                 and isinstance(a["game_date"], str) and isinstance(dm.get("date"), str)
                 and a["game_date"] <= dm["date"]]
        opt = prior[-1]["args"].get("option") if prior else None
        rows.append({"date": dm.get("date"), "event": dm.get("event"), "answered": dm.get("answered"),
                     "by": dm.get("by"), "rule_option": opt,
                     "matched": (opt == dm.get("answered")) if isinstance(opt, str) else None})
    out["demand_vs_rule"] = rows
    return out


def score(kind: str, start: Mapping, end: Mapping, calls: Sequence[Mapping]) -> dict:
    if kind not in KINDS:
        raise ValueError(f"outcome kind {kind!r} not in {KINDS}")
    if not isinstance(start, Mapping) or "unread" in start:
        return {"kind": kind, "unscored": "no start read"}
    if not isinstance(end, Mapping) or "unread" in end:
        return {"kind": kind, "unscored": f"no end read: {(end or {}).get('unread')}"}
    return {"target_choice": score_target_choice, "succession": score_succession,
            "faction": score_faction}[kind](start, end, calls)
