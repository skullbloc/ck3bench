"""The turn's goal line: one line, at the top of a turn, on what the target title still waits for.

Gold only. When the target title's creation is blocked by its gold price ALONE (every other requirement met; the
game's own check names nothing but the gold), the turn opens with one line: the price, the gold held, the shortfall,
the months to afford it at the realm's net income (the toolkit's own `months_to_target` in the price row, read as it
is), and that the price rises at the ruler's death when the reads say a discount of his own dies with him (a ruler's
perk discount ends with him: 325 gold can become 425 at a succession). Facts only.

Creatable now. When the row says the title can be created now (the game's own check and the price met, nothing
unmet, the row not blocked), the line says so with the exact call.

A chain. When the target's tier rule still lacks titles of the tier below and the rest is gold (a kingdom that waits
on a second duchy which is itself creatable, or waits only for gold), the line covers the chain: what is creatable
now with the exact call, what then remains and whether it waits only for gold, the gold still needed in all, the
months at the last step's net, the price rise at the ruler's death. Its rows are the same formable_titles read with
the target's de-jure titles of that tier not held (the target row's `tier_rule.dejure_unheld`).

More than gold. When the target waits on more than that, the line names what the game's own check says is missing
instead of going silent. The game's check can add a requirement late ("You only have 7 of 8 required Counties"
appears when a county leaves the realm), and negative gold alone must not hide the line.

`goal_line(row, rows)` takes the target's row of the toolkit's `do(action_class="formable_titles",
candidates=[<title>, ...])` (redesign.title_requirements, with price_basis) and that reply's rows.
"""
from __future__ import annotations

import json
import math
from typing import Any, Mapping, Optional

def _num(v: Any) -> Optional[float]:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _g(v: float) -> str:
    return str(int(round(v))) if abs(v - round(v)) < 0.005 or abs(v) >= 100 else f"{v:.2f}"


def _p(v: float) -> str:
    """A price as the game states it (162.5, not 162): two decimals at most, trailing zeros dropped."""
    return f"{v:.2f}".rstrip("0").rstrip(".")


def gold_only(row: Mapping[str, Any]) -> Optional[dict]:
    """{cost, have, short} when the row's only unmet requirement is its gold price (the game's check naming
    nothing but gold), else None."""
    reqs = [q for q in row.get("requirements") or [] if isinstance(q, Mapping)]
    price = next((q for q in reqs if q.get("requirement") == "price"), None)
    if not price or price.get("met") is not False or price.get("currency") != "gold":
        return None
    for q in reqs:
        name = q.get("requirement")
        if name == "price":
            continue
        if name == "game_check":
            reasons = [str(x) for x in q.get("reasons") or []]
            if q.get("met") is True or (q.get("met") is False and reasons
                                        and all("gold" in r.lower() for r in reasons)):
                continue
            return None
        if q.get("met") is not True:
            return None
    cost, have = _num(price.get("cost")), _num(price.get("have"))
    if cost is None or have is None:
        return None
    return {"cost": cost, "have": have, "short": round(cost - have, 2), "price": price}


def creatable_now(row: Mapping[str, Any]) -> Optional[dict]:
    """{call, price} when the row can be created now: not blocked, the game's own check and the price read met,
    no requirement unmet; else None."""
    if row.get("blocked") is not None:
        return None
    reqs = [q for q in row.get("requirements") or [] if isinstance(q, Mapping)]
    by = {q.get("requirement"): q for q in reqs}
    if (by.get("game_check") or {}).get("met") is not True or (by.get("price") or {}).get("met") is not True:
        return None
    if any(q.get("met") is False for q in reqs):
        return None
    dc = row.get("do_call") if isinstance(row.get("do_call"), Mapping) else {
        "intent": "mint_title", "args": {"title_key": row.get("title_key")}}
    tool = "do"
    if isinstance(dc.get("args"), Mapping) and dc.get("tool"):
        # the row as served: {"tool": "<the do tool, as the client names it>", "args": {"intent", "args"}} (live 0869.07.08)
        tool, dc = str(dc["tool"]), dc["args"]
    call = f'{tool}(intent="{dc.get("intent")}", args={json.dumps(dc.get("args") or {}, ensure_ascii=False)})'
    return {"call": call, "price": by["price"]}


#: The most bytes the death clause takes when the partition takes goal titles out of the realm, and the most levers
#: it names. Uncapped, the clause ran to 1.2-1.9 KB a turn (two or three sons, each with his county list, and five
#: to seven levers, each with its full call, most of them "not allowed now").
DEATH_CLAUSE_MAX = 700
DEATH_LEVERS_SHOWN = 3
WAKE_WORDS = ". A long wake may pass his death with none of it done"


def _death_clause(row: Mapping[str, Any]) -> str:
    at = (row.get("price_basis") or {}).get("at_succession") if isinstance(row.get("price_basis"), Mapping) else None
    price = ""
    if isinstance(at, Mapping) and _num(at.get("rises")) and _num(at.get("price")):
        why = str(at.get("why") or "").split(";", 1)[0].strip()
        price = f" the price rises to {_g(_num(at['price']))}" + (f" ({why})" if why else "")
    ad = row.get("at_death") if isinstance(row.get("at_death"), Mapping) else None
    leaves = [x for x in (ad or {}).get("leaves_realm") or [] if isinstance(x, Mapping)]
    if not leaves:
        return f". At your death{price}" if price else ""
    # What the death takes from the goal's chain, every turn it holds (the target row's at_death): a partition can
    # take a duchy and counties of the target kingdom to a son, and a forecast left in the turn's news is easy to miss.
    # Within DEATH_CLAUSE_MAX bytes, in this order: the goal titles that leave the realm, the price after the
    # death, at most DEATH_LEVERS_SHOWN levers the game allows now (the sure ones first), the wake warning when it
    # fits; the rest behind one pointer to the call that shows the whole forecast with every lever and its reason.
    levers = [x for x in ad.get("levers") or [] if isinstance(x, Mapping)]
    pre = _tool_prefix(levers, row)
    ptr = (f". Every lever and the whole forecast: {pre}succession_plan()"
           if levers else f". No lever read changes it; the whole forecast: {pre}succession_plan()")
    head = f". At your death{_ruler_words(ad.get('ruler'))} partition takes out of your realm "
    now = sorted((x for x in levers if x.get("allowed_now") is True), key=_lever_rank)
    r = ad.get("ruler") if isinstance(ad.get("ruler"), Mapping) else {}

    def build(keys: bool, why: bool) -> tuple:
        p = price if why or not price else price.split(" (", 1)[0]
        tail = (";" + p) if p else ""
        out = None
        words = [_leave_words(x, keys) for x in leaves]
        for n in range(len(words), 0, -1):
            more = f"; and {len(words) - n} more" if n < len(words) else ""
            cand = head + "; ".join(words[:n]) + more + tail
            if _b(cand) + _b(ptr) <= DEATH_CLAUSE_MAX:
                out = cand
                break
        if out is None:                               # nothing fits whole: the first title, cut, and the pointer
            out = _cut(head + _leave_words(leaves[0], False), DEATH_CLAUSE_MAX - _b(ptr) - _b(tail)) + tail
        shown = 0
        for x in now[:DEATH_LEVERS_SHOWN]:
            piece = ("; " if shown else ". Allowed now: ") + _lever_words(x, under_allowed=True)
            if _b(out) + _b(piece) + _b(ptr) <= DEATH_CLAUSE_MAX:
                out += piece
                shown += 1
        if levers and not now:
            out += ". No lever is allowed now"
        wake = bool(r.get("act_now")) and _b(out) + _b(WAKE_WORDS) + _b(ptr) <= DEATH_CLAUSE_MAX
        return _cut(out + (WAKE_WORDS if wake else "") + ptr, DEATH_CLAUSE_MAX), shown, wake

    # the details go first, before a lever or the wake warning: the county keys, then why the price rises; the
    # richest form that shows as much as the barest is the one given
    forms = [build(True, True), build(False, True), build(False, False)]
    return next(f[0] for f in forms if f[1:] == forms[-1][1:])


def _b(s: str) -> int:
    return len(s.encode("utf-8"))


def _cut(s: str, n: int) -> str:
    """At most n bytes of s, never inside a character."""
    return s.encode("utf-8")[:max(n, 0)].decode("utf-8", errors="ignore")


def _lever_rank(x: Mapping[str, Any]) -> tuple:
    """The sure levers first (the ruler's own act), then the ones the target decides, likeliest first."""
    if not x.get("he_decides"):
        return (0, 0.0)
    c = _num(x.get("chance_pct"))
    return (1, -(c if c is not None else (100.0 if x.get("answer") == "yes" else 0.0)))


def _tool_prefix(levers: list, row: Mapping[str, Any]) -> str:
    """'mcp__ck3seat__' when a lever's call (or the row's own do_call) names the do tool as the client does, else ''."""
    for dc in [x.get("call") for x in levers] + [row.get("do_call")]:
        t = dc.get("tool") if isinstance(dc, Mapping) else None
        if isinstance(t, str) and t.endswith("do"):
            return t[:-2]
    return ""


def _ruler_words(r: Any) -> str:
    """' (he is 47, health 2.9; 1 in 4 he dies within 1.8 years)', or '' when nothing was read."""
    if not isinstance(r, Mapping):
        return ""
    parts = []
    age, h = _num(r.get("age")), _num(r.get("health"))
    if age is not None:
        parts.append(f"he is {int(age)}")
    if h is not None:
        parts.append(f"health {h:g}")
    p25 = _num((r.get("dies_within_years") or {}).get("p25")) if isinstance(r.get("dies_within_years"), Mapping) \
        else None
    words = ", ".join(parts)
    if p25 is not None:
        words += ("; " if words else "") + f"1 in 4 he dies within {p25:g} years"
    return f" ({words})" if words else ""


def _leave_words(x: Mapping[str, Any], keys: bool = True) -> str:
    """'d_ulster (made by the law; c_ulster, c_oriel, c_ailech: 3 of 3 counties in your realm) to Donnchad 66915';
    without the county keys when `keys` is false (they go first when the clause is over its cap)."""
    inner = []
    if x.get("created_by_the_law"):
        inner.append("made by the law")
    cn = x.get("counties") if isinstance(x.get("counties"), Mapping) else None
    if cn:
        ck = [str(k) for k in x.get("county_keys") or []] if keys else []
        inner.append((", ".join(ck) + ": " if ck else "") + f"{cn.get('in_your_realm')} of {cn.get('of')} counties "
                     "in your realm")
    who = " ".join(str(v) for v in (x.get("name"), x.get("to")) if v not in (None, ""))
    return f"{x.get('takes')}" + (f" ({'; '.join(inner)})" if inner else "") + f" to {who}"


def _lever_words(x: Mapping[str, Any], under_allowed: bool = False) -> str:
    """'disinherit 66915: do(...) (not allowed now: <why>)'. When the target answers the send (vows: his AI's
    maybe), an allowed lever says he decides, with the game's own pre-answer from the send check when it was read;
    `under_allowed`: listed under "Allowed now", so "allowed now" is not said again."""
    what = f"{x.get('lever')} {x.get('son') if x.get('son') is not None else x.get('law') or ''}".strip()
    call = _call_of(x.get("call"), None) if isinstance(x.get("call"), Mapping) else "no call"
    ok = x.get("allowed_now")
    why = "; ".join(str(w) for w in x.get("why_not") or [])[:90]
    state = ("allowed now" if ok is True else f"not allowed now{': ' + why if why else ''}" if ok is False
             else "the game's check not read")
    if ok is True and x.get("he_decides"):
        state = decides_words(x) if under_allowed else f"{state}; {decides_words(x)}"
    elif ok is True and under_allowed:
        return f"{what}: {call}"
    return f"{what}: {call} ({state})"


def decides_words(x: Mapping[str, Any]) -> str:
    """'he decides: the game's pre-answer maybe, 25 in 100' (the send check's answer and its maybe-band score), else
    'he decides; his answer not read'. It is his answer at the check: he answers days later, on his state then."""
    ans, c = x.get("answer"), _num(x.get("chance_pct"))
    if ans == "maybe" and c is not None:
        return f"he decides; the game reads maybe, {c:g} in 100"
    if ans in ("yes", "maybe"):
        return f"he decides; the game reads {ans} now, he answers days later"
    return "he decides; his answer not read"


def _months(price: Mapping[str, Any], short: float) -> str:
    """'; N months at the last step's net +X gold a month' for `short` gold at the toolkit's own net (its
    months_to_target), with what standing the raised armies down would make it; or why there is no count."""
    mt = price.get("months_to_target")
    if not isinstance(mt, Mapping):
        return ""
    net = _num(mt.get("net_monthly"))
    if isinstance(mt.get("months"), int) and net is not None and net > 0:
        out = (f"; {math.ceil(short / net) if short > 0 else 0} months at the last step's net {net:+.2f} gold a month"
               + _one_off_clause(mt))
    elif mt.get("why"):
        out = f"; months to it: {str(mt['why']).split(':', 1)[0]}"
    else:
        return ""
    return out + _armies_clause(mt, short)


def _one_off_clause(mt: Any) -> str:
    """' (one-off -162.50 gold left out)' when the toolkit's net a month excludes the step's priced one-offs
    (a purchase by the seat, a priced row of the purse); '' otherwise. The 'never' wording carries it already."""
    one = mt.get("one_offs_excluded") if isinstance(mt, Mapping) else None
    tot = _num(one.get("total")) if isinstance(one, Mapping) else None
    return f" (one-off {tot:+.2f} gold left out)" if tot is not None else ""


def _armies_clause(mt: Any, short: float) -> str:
    """' (N men raised cost X a month; stood down, M months at +Y)' from the toolkit's months_to_target, when
    armies were raised at the step's end; '' otherwise."""
    ar = mt.get("armies_raised") if isinstance(mt, Mapping) and isinstance(mt.get("armies_raised"), Mapping) else None
    if not ar or not _num(ar.get("upkeep_a_month")):
        return ""
    sd = ar.get("if_stood_down") if isinstance(ar.get("if_stood_down"), Mapping) else None
    out = f" ({ar.get('men')} men raised cost {_g(_num(ar['upkeep_a_month']))} a month"
    sn = _num((sd or {}).get("net_monthly"))
    if sn is not None and sn > 0 and short > 0:
        out += f"; stood down, {math.ceil(short / sn)} months at {sn:+.2f}"
    return out + ")"


def _tier_rule(row: Mapping[str, Any]) -> Optional[Mapping[str, Any]]:
    return next((q for q in row.get("requirements") or [] if isinstance(q, Mapping)
                 and q.get("requirement") == "tier_rule"), None)


def _waits_on_chain_and_gold(row: Mapping[str, Any]) -> Optional[dict]:
    """{short_titles, tier, price} when the target's unmet requirements are its tier rule (titles of the tier
    below) and, at most, its gold price; else None."""
    tr = _tier_rule(row)
    if not tr or tr.get("met") is not False:
        return None
    opts = [o for o in tr.get("one_of") or [] if isinstance(o, Mapping)]
    unheld = tr.get("dejure_unheld")
    if not opts or not isinstance(unheld, list):
        return None
    o = opts[0]
    short = int(o.get("need") or 0) - int(o.get("have") or 0)
    if short <= 0:
        return None
    price = None
    for q in row.get("requirements") or []:
        if not isinstance(q, Mapping) or q.get("requirement") == "tier_rule":
            continue
        name = q.get("requirement")
        if name == "price":
            if q.get("currency") != "gold" or _num(q.get("cost")) is None or _num(q.get("have")) is None:
                return None
            price = q
            continue
        if name == "game_check":
            reasons = [str(x) for x in q.get("reasons") or []]
            if q.get("met") is True or not reasons or all("gold" in r.lower() for r in reasons):
                continue
            return None
        if q.get("met") is not True:
            return None
    if price is None:
        return None
    return {"short_titles": short, "tier": o.get("tier"), "price": price, "unheld": [str(k) for k in unheld]}


#: The most de-jure titles of the tier below the goal line prices in its second read
CHAIN_READ_MAX = 6


def chain_keys(row: Any) -> list:
    """The target's de-jure titles of the tier below that the ruler does not hold, when its tier rule still
    lacks them (the row's tier_rule.dejure_unheld), for the goal line's second read; [] otherwise."""
    if not isinstance(row, Mapping):
        return []
    tr = _tier_rule(row)
    unheld = tr.get("dejure_unheld") if tr and tr.get("met") is False else None
    return [str(k) for k in unheld if isinstance(k, str)][:CHAIN_READ_MAX] if isinstance(unheld, list) else []


def has_chain(row: Any) -> bool:
    """The target row carries the toolkit's own reading of its chain titles (tier_rule.chain)."""
    return isinstance(row, Mapping) and isinstance((_tier_rule(row) or {}).get("chain"), list)


def chain_line(row: Mapping[str, Any], rows: list) -> Optional[str]:
    """The goal line while the target waits on titles of the tier below (its tier rule) and gold."""
    c = _waits_on_chain_and_gold(row)
    if c is None:
        return None
    key = row.get("title_key") or "the title"
    ranked = []
    chain = (_tier_rule(row) or {}).get("chain")
    if isinstance(chain, list):
        # the toolkit's own reading of each chain title (redesign.chain_step), the one inspect ranks by, read off the
        # target row, so the line and inspect name the same buyable duchy; the rows of the same reply are not needed,
        # so a paged reply cannot leave one out
        for st in chain:
            cost = _num((st or {}).get("cost")) if isinstance(st, Mapping) else None
            if cost is None or st.get("currency") not in (None, "gold"):
                continue
            if st.get("state") == "creatable_now":
                ranked.append((0, cost, str(st.get("title_key")), _call_of(st.get("do_call"), st.get("title_key"))))
            elif st.get("state") == "gold_only":
                ranked.append((1, cost, str(st.get("title_key")), None))
        return _chain_words(row, c, key, ranked)
    by = {r.get("title_key"): r for r in rows if isinstance(r, Mapping)}
    for k in c["unheld"]:
        r = by.get(k)
        if r is None:
            continue
        now, g = creatable_now(r), gold_only(r)
        pr = (now or {}).get("price") or (g or {}).get("price") or {}
        cost = _num(pr.get("cost"))
        if cost is None or pr.get("currency") not in (None, "gold"):
            continue
        if now is not None:
            ranked.append((0, cost, k, now["call"]))
        elif g is not None:
            ranked.append((1, cost, k, None))
    return _chain_words(row, c, key, ranked)


def _call_of(dc: Any, title_key: Any) -> str:
    """The call as creatable_now writes it, from a do_call {"tool", "args": {"intent", "args"}} or {"intent", "args"}."""
    dc = dc if isinstance(dc, Mapping) else {"intent": "mint_title", "args": {"title_key": title_key}}
    tool = "do"
    if isinstance(dc.get("args"), Mapping) and dc.get("tool"):
        tool, dc = str(dc["tool"]), dc["args"]
    return f'{tool}(intent="{dc.get("intent")}", args={json.dumps(dc.get("args") or {}, ensure_ascii=False)})'


def _chain_words(row: Mapping[str, Any], c: dict, key: str, ranked: list) -> str:
    ranked.sort()
    pick = ranked[:c["short_titles"]]
    n = c["short_titles"]
    head = f"{key} needs {n} more {c['tier']} title{'s' if n != 1 else ''} first"
    if len(pick) < n:
        return (head + f": none of its de-jure {c['tier']} titles you do not hold can be created for gold alone yet "
                f"({', '.join(c['unheld']) or 'none read'}); inspect title_key={key} names what each lacks.")
    p = c["price"]
    have = _num(p["have"]) or 0.0
    parts = []
    for rank, cost, k, call in pick:
        if rank == 0:
            parts.append(f"{k} can be created now (it costs {_p(cost)}, you have {_g(have)}); the call: {call}")
        else:
            parts.append(f"{k} waits only for gold (it costs {_p(cost)}, you have {_g(have)})")
    total = sum(x[1] for x in pick) + float(p["cost"])
    still = round(total - have, 2)
    then = f"then {key} waits only for gold ({_p(float(p['cost']))})" if p.get("met") is False or still > 0 else \
        f"then {key} can be created ({_p(float(p['cost']))})"
    out = head + ": " + "; ".join(parts) + f". {then[0].upper() + then[1:]}: {_p(max(still, 0.0))} gold still needed " \
        f"in all ({_p(total)})" + _months(p, still)
    return out + _death_clause(row) + "."


def missing_line(row: Mapping[str, Any]) -> Optional[str]:
    """What the target still lacks when it waits on more than gold, in the game's own words; None when nothing
    unmet was read."""
    key = row.get("title_key") or "the title"
    reqs = [q for q in row.get("requirements") or [] if isinstance(q, Mapping)]
    words = []
    game_counties = any("required counties" in str(x).lower() for q in reqs if q.get("requirement") == "game_check"
                        for x in q.get("reasons") or [])
    for q in reqs:
        if q.get("requirement") == "game_check" and q.get("met") is False:
            words += [str(x) for x in q.get("reasons") or [] if "gold" not in str(x).lower()]
        elif q.get("requirement") == "tier_rule" and q.get("met") is False and q.get("rule"):
            words.append(str(q["rule"]))
        elif q.get("requirement") == "de_jure_counties" and q.get("met") is False and not game_counties:
            # said once: not "7 of 8 de-jure counties" beside the game's own "You only have 7 of 8 required Counties"
            words.append(f"{q.get('have')} of {q.get('need')} de-jure counties")
    if not words:
        return None
    price = next((q for q in reqs if q.get("requirement") == "price"), {})
    cost, have = _num(price.get("cost")), _num(price.get("have"))
    gold = (f"; and gold: it costs {_p(cost)}, you have {_g(have)}"
            if price.get("met") is False and cost is not None and have is not None else "")
    if gold:
        gold += _bill_words(row, price, have)
    return f"{key} waits on more than gold: {'; '.join(dict.fromkeys(words))}{gold}" + _death_clause(row) + "."


def _bill_words(row: Mapping[str, Any], price: Mapping[str, Any], have: float) -> str:
    """With titles of the tier below still to make (k_ireland waiting on a county and a second duchy: the bill is
    d_connacht 162.5 + k_ireland 325 = 487.5, not the 325 of the target alone, and at the realm's net it can run past
    the ruler's even-odds death, when the price rises to 425), the whole bill the toolkit reads (the target row's tier_rule.bill), the months to it at
    the last step's net, and his even-odds death when the months run past it. '' when no bill was read."""
    bill = (_tier_rule(row) or {}).get("bill")
    cost = _num(bill.get("cost")) if isinstance(bill, Mapping) and bill.get("currency") in (None, "gold") else None
    if cost is None or cost <= (_num(price.get("cost")) or 0.0):
        return ""
    of = [str(k) for k in bill.get("of") or []]
    short = round(cost - have, 2)
    out = f"; with the {(_tier_rule(row) or {}).get('one_of', [{}])[0].get('tier') or 'lower'} title" \
          f"{'s' if len(of) > 2 else ''} the bill is {_p(cost)}" + (f" ({', '.join(of)})" if of else "")
    m = _months(price, short) if short > 0 else ""
    out += m
    mt = price.get("months_to_target") if isinstance(price.get("months_to_target"), Mapping) else {}
    net = _num(mt.get("net_monthly"))
    r = (row.get("at_death") or {}).get("ruler") if isinstance(row.get("at_death"), Mapping) else None
    p50 = _num((r.get("dies_within_years") or {}).get("p50")) if isinstance(r, Mapping) and isinstance(
        r.get("dies_within_years"), Mapping) else None
    if m and net and net > 0 and p50 is not None and short / net > p50 * 12:
        out += f", past his even-odds death ({p50:g} years)"
    return out


def price_of(row: Any) -> Optional[dict]:
    """{title_key, cost, currency, at_death?} from a formable_titles row's price requirement (the game's own
    price, as the line says it), for a standing order's threshold; None when no price was read."""
    if not isinstance(row, Mapping):
        return None
    q = next((q for q in row.get("requirements") or [] if isinstance(q, Mapping) and q.get("requirement") == "price"),
             None)
    cost = _num((q or {}).get("cost"))
    if cost is None:
        return None
    at = (row.get("price_basis") or {}).get("at_succession") if isinstance(row.get("price_basis"), Mapping) else None
    after = _num(at.get("price")) if isinstance(at, Mapping) and _num(at.get("rises")) else None
    return {"title_key": row.get("title_key"), "cost": cost, "currency": q.get("currency") or "gold",
            **({"at_death": after} if after is not None else {})}


def goal_line(row: Any, rows: Optional[list] = None) -> Optional[str]:
    """The goal line for one formable_titles row: it can be created now, it waits only for gold, it
    waits on the titles of the tier below and gold (`rows`: the same reply's rows), or what it still lacks."""
    if not isinstance(row, Mapping):
        return None
    key = row.get("title_key") or "the title"
    now = creatable_now(row)
    if now is not None:
        pr = now["price"]
        cost, have = _num(pr.get("cost")), _num(pr.get("have"))
        paid = (f" (it costs {_g(cost)} {pr.get('currency') or 'gold'}, you have {_g(have)})"
                if cost is not None and have is not None else "")
        return f"{key} can be created now: every requirement is met{paid}; the call: {now['call']}" \
            + _death_clause(row) + "."
    g = gold_only(row)
    if g is None:
        return chain_line(row, list(rows or [])) or missing_line(row)
    price = g["price"]
    out = f"{key} now waits only for gold: it costs {_g(g['cost'])}, you have {_g(g['have'])} ({_g(g['short'])} short)"
    mt = price.get("months_to_target")       # the toolkit's own count (redesign.months_to_target), never redone here
    if isinstance(mt, Mapping) and isinstance(mt.get("months"), int):
        out += (f"; {mt['months']} months at the last step's net {_num(mt.get('net_monthly')) or 0:+.2f} gold a month"
                + _one_off_clause(mt))
    elif isinstance(mt, Mapping) and mt.get("why"):
        out += f"; months to it: {str(mt['why']).split(':', 1)[0]}"
    out += _armies_clause(mt, g["short"])
    return out + _death_clause(row) + "."
