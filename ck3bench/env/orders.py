"""Standing orders: a lever call the seat wrote in advance, sent by the env when its condition holds.

On a wake task (wake.py) a seat may end its turn with `orders` beside its wake request:

    {"orders": [{"id": "press-athlone",
                 "when": [{"event": "claim_gained"}],                       # all must hold
                 "unless": [{"metric": "gold", "op": "<=", "value": 0}],    # any one blocks
                 "do": {"tool": "declare_war", "args": {"declare_args": {"target_id": 29585, "cb": "claim_cb",
                                                                        "cb_title_id": 469}}}}]}

At every check of the step that follows (every `check_days`, the clock running) the env reads the conditions on
the same readings a wake condition reads; when every `when` holds and no `unless` does, it sends the `do` call
ONCE, verbatim, through the same route as a call the seat makes in its turn (env._exec: the scenario's tools, the
clock refusal, the toolkit's own argument checks and the bridge's), and the clock keeps running. No seat turn
opens and no model is involved.

The rules, each a refusal at set time or a record the seat reads, never a default:

  * CONDITIONS are wake.py's closed vocabulary, parsed by wake.py's own parser: a condition the backend cannot
    witness is refused when the order is set (`wake_condition_unwitnessable`). Events are judged against the
    step's start, as a wake condition is.
  * The ACTION is one call of ORDER_TOOLS (the seat's war and `do` levers). Its arguments are checked at set time
    against the tool's own listed input schema with the rule the toolkit server applies to a live call
    (`unknown_arguments`: a top-level key the schema does not declare is refused), plus the schema's required keys,
    types and enums. Keys nested inside `do.args` / `declare_war.declare_args` are the tool body's to check, at
    fire, as in a live call: a refusal there is a `fired` record with verdict `refused`.
  * A call that ENDS A WAR AS YOUR DEFEAT (CONFIRM) needs `confirm` naming it at set time. A tool's own
    confirmation flag (`confirm_straddle`, `confirm_debt`) goes in the args, verbatim, and the tool checks it at
    fire. So an order cannot SPEND INTO DEBT unconfirmed: a hire whose price is above the gold read at fire
    is refused by the tool (`debt_unconfirmed`, no new wars while in debt) unless its args carry
    `confirm_debt: true`; the refusal is the order's `fired` record with verdict `refused` and its gate.
  * LIFETIME: an order STANDS from the turn that set it until it fires (once), the seat cancels it
    (`{"id": .., "cancel": true}`, or a new order with the same id replaces it), the ruler changes (`cancelled`:
    the arguments were written for another ruler), or its own `steps` limit passes (DEFAULT_STEPS, at most
    MAX_STEPS; `expired`, and the record says it ended unfired). At most MAX_ORDERS stand at once; orders a seat
    sends are added to the ones standing. A seat whose episode ends drops its standing orders (`dropped`).
    (An order that lived one step mostly expired before its condition came true.) Every observation lists the orders still standing (`orders_standing`, one line each).
  * An UNREAD condition never fires an order and an unread `unless` BLOCKS it (`blocked`, `unread`): absence is not
    agreement.
  * A refused fire WAKES the seat (`on_refusal: "wake"`, the default): the step stops at that check as a wake
    condition stops it, the fire listed in the step's `wake.fired` as {"seat", "order", ...}.

Records ({order, outcome, date, ...}; outcome in OUTCOMES) are the seat's in its next observation under `orders`
and in the step's trajectory record; a fired call is a trajectory `call` record with phase `order` and `order_id`.
"""
from __future__ import annotations

import json
from typing import Any, Mapping, Optional

from . import wake as _wake
from .errors import EnvRefusal

R_ORDERS_UNDECLARED = "orders_not_declared"        # orders on a task without them (a fixed task, or switched off)
R_ORDER_SHAPE = "order_malformed"                  # an order outside the shape or its bounds
R_ORDER_LEVER = "order_lever_refused"              # an action that is not one call of ORDER_TOOLS, or whose args fail
R_ORDER_UNCONFIRMED = "order_needs_confirm"        # a defeat-ending call without `confirm` naming it

#: The levers an order may pull: the seat's war declaration and its `do` (council tasks, peace, interactions...).
#: Read-only tools are refused (an order that only reads is a wake condition).
ORDER_TOOLS = ("declare_war", "do")
MAX_ORDERS = 4
#: an order stands at most this many steps unless it sets `steps` (1..MAX_STEPS)
DEFAULT_STEPS = 10
MAX_STEPS = 50
MAX_WHEN = 4
MAX_UNLESS = 4
#: one order's JSON, at most (refused above, never cut)
MAX_ORDER_BYTES = 1024
ON_REFUSAL = ("wake", "continue")
#: The calls that end a war as the seat's defeat: (tool, do intent, arg, value) -> the word `confirm` must carry.
#: make_peace surrender is always taken by the enemy AI; end_war we_yield is the same defeat by its other name.
CONFIRM = {("do", "make_peace", "terms", "surrender"): "surrender",
           ("do", "end_war", "terms", "we_yield"): "we_yield"}
OUTCOMES = ("set", "fired", "blocked", "expired", "cancelled", "dropped")
#: What a refused fire leaves, in the fired record the next observation carries (a seat can read `steps: 3` with
#: on_refusal continue as "send it again at each check, for up to 3 steps"; it fires once, is refused, and is gone). An order is ONE call: a refusal ends it (sending it again at every check would repeat a
#: refused call every 7 days, and the seat should judge the reason first); `steps` is how long it waits to fire.
ORDER_GONE = ("this order is gone: an order fires once, and a refused fire ends it ('steps' is how long it waits "
              "to fire, not retries); to try again, set it again in your next turn's orders")
_KEYS = {"id", "when", "unless", "do", "on_refusal", "confirm", "steps"}
_ERR_CHARS = 200


def unknown_arguments(input_schema: Any, arguments: Any) -> list[str]:
    """The argument names a call sent that the tool's schema does not declare, sorted. A transcription of
    ck3agent's mcp_server.unknown_arguments (the toolkit server's refusal of a live call);
    test_orders checks the two agree."""
    if not isinstance(arguments, dict):
        return []
    declared = set((input_schema or {}).get("properties") or {})
    return sorted(k for k in arguments if k not in declared)


_JSON_TYPES = {"integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
               "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
               "string": lambda v: isinstance(v, str), "boolean": lambda v: isinstance(v, bool),
               "object": lambda v: isinstance(v, dict), "array": lambda v: isinstance(v, list),
               "null": lambda v: v is None}


def _type_ok(prop: Mapping[str, Any], v: Any) -> Optional[bool]:
    """True/False when the property names its JSON type(s) (also through anyOf), None when it names none."""
    types = []
    for p in [prop] + [a for a in prop.get("anyOf") or [] if isinstance(a, Mapping)]:
        t = p.get("type")
        types += t if isinstance(t, list) else [t] if isinstance(t, str) else []
    known = [t for t in types if t in _JSON_TYPES]
    if not known:
        return None
    return any(_JSON_TYPES[t](v) for t in known)


def shape_problems(input_schema: Any, arguments: Mapping[str, Any]) -> list[str]:
    """What a live call with `arguments` would be refused for by the tool's schema, before the tool runs."""
    out = []
    unknown = unknown_arguments(input_schema, dict(arguments))
    if unknown:
        out.append(f"no argument named {', '.join(unknown)} (it takes "
                   f"{', '.join(sorted((input_schema or {}).get('properties') or {})) or 'none'})")
    props = (input_schema or {}).get("properties") or {}
    for k in (input_schema or {}).get("required") or []:
        if k not in arguments:
            out.append(f"{k} is required")
    for k, v in arguments.items():
        p = props.get(k)
        if not isinstance(p, Mapping):
            continue
        if _type_ok(p, v) is False:
            out.append(f"{k} {v!r} is not of its type")
        if isinstance(p.get("enum"), list) and v not in p["enum"]:
            out.append(f"{k} {v!r} not in {p['enum']}")
    return out


def confirm_needed(tool: str, args: Mapping[str, Any]) -> Optional[str]:
    """The word `confirm` must carry for this call (CONFIRM), or None."""
    if tool != "do":
        return None
    inner = args.get("args") if isinstance(args.get("args"), Mapping) else {}
    for (t, intent, k, v), word in CONFIRM.items():
        if t == tool and args.get("intent") == intent and inner.get(k) == v:
            return word
    return None


def parse_orders(seat: str, req: Any, *, vocabulary: Mapping[str, tuple], tools: tuple,
                 schema_of, standing: tuple = ()) -> list[dict[str, Any]]:
    """One seat's orders from its turn -> their records {id, when, unless, do, on_refusal, steps, confirm?}, or
    {id, cancel: True} for a cancel. `standing`: the ids of the seat's orders standing now (a cancel names one; the
    orders standing after this turn are at most MAX_ORDERS). `tools`: the scenario's tools; `schema_of(tool)`: the
    tool's listed input schema (raises EnvRefusal when the backend cannot describe it: an order whose shape
    cannot be checked is refused)."""
    if not isinstance(req, list):
        raise EnvRefusal(R_ORDER_SHAPE, f"{seat}: orders is a list of orders, got {type(req).__name__}", "env")
    if len(req) > MAX_ORDERS:
        raise EnvRefusal(R_ORDER_SHAPE, f"{seat}: {len(req)} orders, at most {MAX_ORDERS}", "env")
    out, ids = [], set()
    for i, o in enumerate(req):
        if isinstance(o, Mapping) and o.get("cancel") is not None:
            rec = _cancel(seat, i, o, standing)
        else:
            rec = _order(seat, i, o, vocabulary, tools, schema_of)
        if rec["id"] in ids:
            raise EnvRefusal(R_ORDER_SHAPE, f"{seat}: orders[{i}].id {rec['id']!r} repeats", "env")
        ids.add(rec["id"])
        out.append(rec)
    after = (set(standing) | {r["id"] for r in out}) - {r["id"] for r in out if r.get("cancel")}
    if len(after) > MAX_ORDERS:
        raise EnvRefusal(R_ORDER_SHAPE, f"{seat}: {len(after)} orders would stand, at most {MAX_ORDERS} (standing: "
                                        f"{', '.join(standing) or 'none'}; cancel one with {{id, cancel: true}})",
                         "env")
    return out


def _cancel(seat: str, i: int, o: Mapping[str, Any], standing: tuple) -> dict[str, Any]:
    where = f"{seat}: orders[{i}]"
    if set(o) != {"id", "cancel"} or o.get("cancel") is not True:
        raise EnvRefusal(R_ORDER_SHAPE, f"{where}: a cancel is {{id, cancel: true}} and nothing else", "env")
    if o["id"] not in standing:
        raise EnvRefusal(R_ORDER_SHAPE, f"{where}: no standing order is named {o['id']!r} (standing: "
                                        f"{', '.join(standing) or 'none'})", "env")
    return {"id": o["id"], "cancel": True}


def _order(seat: str, i: int, o: Any, vocabulary, tools, schema_of) -> dict[str, Any]:
    where = f"{seat}: orders[{i}]"
    if not isinstance(o, Mapping):
        raise EnvRefusal(R_ORDER_SHAPE, f"{where} is not an object", "env")
    extra = set(o) - _KEYS
    if extra:
        raise EnvRefusal(R_ORDER_SHAPE, f"{where}: unknown keys {sorted(extra)} ({sorted(_KEYS)})", "env")
    size = len(json.dumps(o, default=str))
    if size > MAX_ORDER_BYTES:
        raise EnvRefusal(R_ORDER_SHAPE, f"{where}: {size} bytes, at most {MAX_ORDER_BYTES}", "env")
    oid = o.get("id")
    if not isinstance(oid, str) or not 1 <= len(oid) <= 40:
        raise EnvRefusal(R_ORDER_SHAPE, f"{where}.id is a string of 1..40 characters", "env")
    when, unless = o.get("when"), o.get("unless", [])
    if not isinstance(when, list) or not 1 <= len(when) <= MAX_WHEN:
        raise EnvRefusal(R_ORDER_SHAPE, f"{where}.when is a list of 1..{MAX_WHEN} conditions", "env")
    if not isinstance(unless, list) or len(unless) > MAX_UNLESS:
        raise EnvRefusal(R_ORDER_SHAPE, f"{where}.unless is a list of at most {MAX_UNLESS} conditions", "env")
    when = [_wake._condition(f"{where}.when", j, c, vocabulary) for j, c in enumerate(when)]
    unless = [_wake._condition(f"{where}.unless", j, c, vocabulary) for j, c in enumerate(unless)]
    on_ref = o.get("on_refusal", "wake")
    if on_ref not in ON_REFUSAL:
        raise EnvRefusal(R_ORDER_SHAPE, f"{where}.on_refusal {on_ref!r} not in {list(ON_REFUSAL)}", "env")
    do = o.get("do")
    if not isinstance(do, Mapping) or set(do) - {"tool", "args"} or "tool" not in do:
        raise EnvRefusal(R_ORDER_SHAPE, f"{where}.do is {{tool, args}}", "env")
    tool, args = do["tool"], do.get("args", {})
    if tool not in ORDER_TOOLS:
        raise EnvRefusal(R_ORDER_LEVER, f"{where}.do.tool {tool!r}: an order pulls one of {list(ORDER_TOOLS)}",
                         "env")
    if tool not in tools:
        raise EnvRefusal(R_ORDER_LEVER, f"{where}.do.tool {tool!r} is not in this task's tools", "env")
    if not isinstance(args, Mapping):
        raise EnvRefusal(R_ORDER_SHAPE, f"{where}.do.args is an object", "env")
    bad = shape_problems(schema_of(tool), args)
    if bad:
        raise EnvRefusal(R_ORDER_LEVER, f"{where}.do: a live {tool} call would be refused: {'; '.join(bad)}", "env")
    word, conf = confirm_needed(tool, args), o.get("confirm")
    if word is not None and conf != word:
        raise EnvRefusal(R_ORDER_UNCONFIRMED, f"{where}: this call ends a war as YOUR DEFEAT; set confirm "
                                              f"{word!r} to order it", "env")
    if conf is not None and word is None:
        raise EnvRefusal(R_ORDER_SHAPE, f"{where}.confirm {conf!r}: this call needs no confirmation", "env")
    steps = o.get("steps", DEFAULT_STEPS)
    if not isinstance(steps, int) or isinstance(steps, bool) or not 1 <= steps <= MAX_STEPS:
        raise EnvRefusal(R_ORDER_SHAPE, f"{where}.steps {steps!r}: the steps it stands, 1..{MAX_STEPS} "
                                        f"(default {DEFAULT_STEPS})", "env")
    return {"id": oid, "when": when, "unless": unless, "do": {"tool": tool, "args": json.loads(json.dumps(args))},
            "on_refusal": on_ref, "steps": steps, **({"confirm": conf} if conf is not None else {})}


def needs(orders: list[dict]) -> frozenset:
    """The events and metrics the orders' conditions read (a backend reads only these at a check)."""
    return frozenset(c.get("event") or c.get("metric") for o in orders for c in o["when"] + o["unless"])


def _unread(cond: Mapping[str, Any], now: Mapping[str, Any]) -> bool:
    """The check's reading has no value for this condition (it did not read)."""
    if "metric" in cond:
        return (now.get("metrics") or {}).get(cond["metric"]) is None
    e = cond["event"]
    if e == "ruler_changed":
        return now.get("character_id") is None
    if e == "realm_changed":
        return (now.get("metrics") or {}).get("realm_counties") is None
    if e == "title_gained":
        return (now.get("metrics") or {}).get("holds_target_title") is None
    return (now.get("events") or {}).get(e) is None



#: the start of the record's `why` when a ruler change cancelled the order (check)
RULER_CHANGED_WHY = "your ruler changed"
#: An order that creates a title by its key names no character, so the same order is valid for the new ruler; it is
#: still cancelled (an order ends when the ruler changes, as end_turn's listing says), and the record hands it back
#: ready to set again. The cancel record's copy (the observation's `orders`) carries no price: the price for the new
#: ruler is read when the turn's ruler_changed news is written, and that copy says set_again.price only when it read
#: one (a discount of the late ruler's dies with him). Each text names only the fields of the block that carries it.
SET_AGAIN = ("this order names no character, so it can stand for your new ruler: send set_again.order in this turn's "
             "end_turn orders to set it again; the turn's ruler_changed news carries the title's price read for him, "
             "when it reads")
#: the ruler_changed news' copy when the price for the new ruler read
SET_AGAIN_PRICED = ("this order names no character, so it can stand for your new ruler: send set_again.order in this "
                    "turn's end_turn orders to set it again; set_again.price is the title's price read for him")
#: the ruler_changed news' copy when no price for the new ruler read
SET_AGAIN_UNPRICED = ("this order names no character, so it can stand for your new ruler: send set_again.order in this "
                      "turn's end_turn orders to set it again; no price of the title was read for him")


def ruler_free(o: Mapping[str, Any]) -> bool:
    """Whether an order's call is valid for any ruler of the seat: a `do` that creates a title (MINT_INTENTS)
    whose arguments name nothing but the title's key."""
    do = o.get("do") or {}
    args = do.get("args") if isinstance(do.get("args"), Mapping) else {}
    inner = args.get("args") if isinstance(args.get("args"), Mapping) else {}
    return (do.get("tool") == "do" and args.get("intent") in MINT_INTENTS and set(args) <= {"intent", "args"}
            and set(inner) == {"title_key"} and isinstance(inner.get("title_key"), str))


def as_set(o: Mapping[str, Any]) -> dict:
    """The order as end_turn takes it, with the steps it had left."""
    out = {k: o[k] for k in ("id", "when", "unless", "do", "on_refusal", "confirm") if k in o and o[k] not in (None, [])}
    out["steps"] = max(1, int(o.get("steps", DEFAULT_STEPS)) - int(o.get("ran", 0)))
    return json.loads(json.dumps(out))


def set_again_of(records, price_of=None) -> list:
    """The orders a ruler change cancelled that can be set again for the new ruler (their set_again blocks), each
    with the title's price as `price_of` reads it now (price_note), when it reads."""
    out = []
    for r in records or []:
        if isinstance(r, dict) and r.get("outcome") == "cancelled" and isinstance(r.get("set_again"), dict):
            again = dict(r["set_again"], cancelled=r.get("order"))
            note = price_note(again["order"], price_of)
            if note:
                again["price"] = {k: v for k, v in note.items() if k != "at_death"}
            again["how"] = SET_AGAIN_PRICED if note else SET_AGAIN_UNPRICED
            out.append(again)
    return out


def cancelled_by_ruler_change(records) -> list:
    """The ids of the orders a ruler change cancelled, from one step's records (the turn's news names them beside
    what carried to the new ruler)."""
    return [r.get("order") for r in records or [] if isinstance(r, dict) and r.get("outcome") == "cancelled"
            and str(r.get("why") or "").startswith(RULER_CHANGED_WHY)]

class Book:
    """A seat's standing orders across steps, and what became of them each step (records per seat).

    `Book(orders, date)` stands the orders set at `date` (the turn before a step); `add` stands more at a later
    turn; `check` runs at every check of a step; `close` ends the step: an order past its `steps` expires, the
    others stand on, and the step's records are handed out (and cleared for the next step)."""

    def __init__(self, orders: Optional[Mapping[str, list[dict]]] = None, date: Optional[str] = None,
                 rulers: Optional[Mapping[str, Any]] = None):
        self.orders: dict[str, list[dict]] = {}
        self.records: dict[str, list[dict]] = {}
        self.wakes: list[dict] = []        # sticky: a refused fire holds at every later check of the step
        for s, os_ in (orders or {}).items():
            self.add(s, os_, date, (rulers or {}).get(s))

    def __bool__(self) -> bool:
        return any(self.live(s) for s in self.orders)

    def live(self, seat: str) -> list[dict]:
        return [o for o in self.orders.get(seat, []) if o["state"] == "live"]

    def ids(self, seat: str) -> tuple:
        return tuple(o["id"] for o in self.live(seat))

    def seats(self) -> list[str]:
        return [s for s in self.orders if self.live(s)]

    def need(self, seat: str) -> frozenset:
        return needs(self.live(seat))

    def _rec(self, seat: str, rec: dict) -> None:
        self.records.setdefault(seat, []).append(rec)

    def add(self, seat: str, orders: list[dict], date: Optional[str], ruler: Any = None, price_of=None) -> None:
        """A turn's orders (parse_orders records) for `seat`: a cancel ends the standing order it names; an order
        with a standing order's id replaces it; the rest stand beside the ones standing. `ruler`: the character
        that set them (a ruler change cancels them). `price_of(title_key)`: the price the tools read for a title
        (the set record of an order that mints it on a gold threshold says it), or None."""
        if not orders:
            return
        live = {o["id"]: o for o in self.live(seat)}
        for o in orders:
            old = live.get(o["id"])
            if old is not None:
                old["state"] = "cancelled"
                self._rec(seat, {"order": o["id"], "outcome": "cancelled", "date": date,
                                 "why": "you cancelled it" if o.get("cancel") else
                                        "replaced by the order you set with the same id"})
            if o.get("cancel"):
                continue
            new = dict(o, state="live", ran=0, set_date=date, ruler=ruler)
            self.orders.setdefault(seat, []).append(new)
            note = price_note(o, price_of)
            self._rec(seat, {"order": o["id"], "outcome": "set", "date": date, "do": _brief(o["do"]),
                             "steps": o.get("steps", DEFAULT_STEPS), **({"price": note} if note else {})})

    def check(self, seat: str, start: Mapping[str, Any], now: Mapping[str, Any], date: Optional[str], fire) -> None:
        """One check for `seat`: cancel on a ruler change, else fire every live order whose conditions hold.
        `fire(seat, order, date)` sends the call and returns the ToolResult."""
        live = self.live(seat)
        if not live:
            return
        cid = now.get("character_id")
        if cid is None:
            return                          # the line did not read: nothing judged at this check
        for o in live:
            if o.get("ruler") is None:
                o["ruler"] = start.get("character_id")
        changed = [o for o in live if cid != o["ruler"]]
        for o in changed:
            o["state"] = "cancelled"
            rec = {"order": o["id"], "outcome": "cancelled", "date": date,
                   "why": f"{RULER_CHANGED_WHY} ({o['ruler']} -> {cid}): an order dies with the ruler who set it"}
            if ruler_free(o):
                # the price for the new ruler is read when the turn's observation is written (env._world)
                rec["set_again"] = {"order": as_set(o), "how": SET_AGAIN}
            self._rec(seat, rec)
        for o in [o for o in live if o["state"] == "live"]:
            reads = []
            ok = True
            for c in o["when"]:
                held, read = _wake.holds(c, start, now)
                reads.append({"when": c, "read": read})
                ok = ok and held
            if not ok:
                continue
            block = None
            for c in o["unless"]:
                if _unread(c, now):
                    block = {"unless": c, "read": "unread"}
                    break
                held, read = _wake.holds(c, start, now)
                if held:
                    block = {"unless": c, "read": read}
                    break
            if block is not None:
                if not o.get("blocked"):
                    o["blocked"] = True       # one record per order per step, the first check it was blocked at
                    self._rec(seat, {"order": o["id"], "outcome": "blocked", "date": date, **block})
                continue
            res = fire(seat, o, date)
            o["state"] = "fired"
            rec = {"order": o["id"], "outcome": "fired", "date": date, "do": _brief(o["do"]),
                   "verdict": "applied" if res.ok else "refused", "reads": reads}
            if not res.ok:
                rec["error"] = str(res.error or "not ok")[:_ERR_CHARS]
                # the refusal's reason code and gate, in the order's own record (not only in last_results)
                rec.update(refusal_of(res))
                rec["standing"] = ORDER_GONE
                if o["on_refusal"] == "wake":
                    self.wakes.append({"seat": seat, "order": o["id"], "condition": {"order_refused": o["id"]},
                                       "read": rec["error"], "date": date})
            self._rec(seat, rec)

    def close(self, date: Optional[str], ended: Mapping[str, str]) -> dict[str, list[dict]]:
        """The step is over: an order that has now stood its `steps` expires, unfired (or every order is dropped,
        for a seat whose episode ended: `ended` seat -> why); the rest stand on. Returns the step's records per
        seat (only seats with a record) and clears them for the next step."""
        for s, os_ in self.orders.items():
            for o in os_:
                if o["state"] != "live":
                    continue
                o["ran"] += 1
                o.pop("blocked", None)
                if s in ended:
                    o["state"] = "dropped"
                    self._rec(s, {"order": o["id"], "outcome": "dropped", "date": date,
                                  "why": f"your episode ended ({ended[s]})"})
                elif o["ran"] >= o.get("steps", DEFAULT_STEPS):
                    o["state"] = "expired"
                    self._rec(s, {"order": o["id"], "outcome": "expired", "date": date,
                                  "why": f"ended UNFIRED: it stood {o['ran']} step{'s' if o['ran'] != 1 else ''} "
                                         f"(its limit; set {o.get('set_date')}) and its conditions never held"})
        out = {s: list(r) for s, r in self.records.items() if r}
        self.records = {}
        self.wakes = []
        for s in list(self.orders):
            self.orders[s] = self.live(s)
        return out

    def standing(self, seat: str) -> list[str]:
        """The seat's orders still standing, one line each (every observation lists them)."""
        return [standing_line(o) for o in self.live(seat)]


#: The do intents that create a title. An order that mints on a gold threshold is told the price read beside its
#: threshold: an order set at gold >= 326 for a price of 325 can be beaten by the game's AI creating the title first.
MINT_INTENTS = ("mint_title", "create_title")


def price_note(o: Mapping[str, Any], price_of) -> Optional[dict]:
    """For an order that creates a title when gold reaches a threshold, the price the tools read for that title
    beside the threshold: {title, price, threshold, words[, at_death]}; None when the order is not one, or no price
    was read. The order dies with the ruler who set it, so the price after his death is not this order's."""
    do = o.get("do") or {}
    args = do.get("args") if isinstance(do.get("args"), Mapping) else {}
    inner = args.get("args") if isinstance(args.get("args"), Mapping) else {}
    if do.get("tool") != "do" or args.get("intent") not in MINT_INTENTS or not inner.get("title_key") \
            or not callable(price_of):
        return None
    gold = [c for c in o.get("when") or [] if c.get("metric") == "gold" and c.get("op") == ">="]
    if not gold:
        return None
    try:
        p = price_of(inner["title_key"])
    except Exception:  # noqa: BLE001  no price read: no note, never a guessed one
        p = None
    cost = p.get("cost") if isinstance(p, Mapping) else None
    if not isinstance(cost, (int, float)) or isinstance(cost, bool) or (p.get("currency") or "gold") != "gold":
        return None
    v = max(float(c["value"]) for c in gold)
    if v > cost:
        words = (f"your threshold {v:g} is {v - cost:g} above the price read, {cost:g}: the call is valid from {cost:g}, "
                 f"and the game's AI may create the title before your order fires; gold >= {cost:g} fires at the price")
    elif v < cost:
        words = (f"your threshold {v:g} is below the price read, {cost:g}: a fire under {cost:g} gold is refused and "
                 "ends the order")
    else:
        words = f"your threshold is the price read, {cost:g}"
    out = {"title": inner["title_key"], "price": cost, "threshold": v, "words": words}
    if isinstance(p.get("at_death"), (int, float)):
        # the hand-back is promised only to an order the cancel record hands back (ruler_free)
        out["at_death"] = (f"{p['at_death']:g} after your ruler's death; this order dies with him, and the turn after "
                           "it hands it back to set again (set_again), priced for his heir" if ruler_free(o) else
                           f"{p['at_death']:g} after your ruler's death; this order dies with him, and its call names "
                           "more than the title, so it is not handed back to set again")
    return out


def _cond_text(c: Mapping[str, Any]) -> str:
    if "event" in c:
        return str(c["event"])
    v = c.get("value")
    return f"{c.get('metric')} {c.get('op')} {int(v) if isinstance(v, float) and v.is_integer() else v}"


def standing_line(o: Mapping[str, Any]) -> str:
    """One standing order in one line: id, the call, its conditions, and the steps it has left."""
    left = o.get("steps", DEFAULT_STEPS) - o.get("ran", 0)
    do = o["do"]
    args = do.get("args") or {}
    call = f"{do['tool']} {args.get('intent')}" if do["tool"] == "do" and args.get("intent") else do["tool"]
    inner = args.get("args") if do["tool"] == "do" else args.get("declare_args", args)
    if isinstance(inner, Mapping) and inner:
        call += " " + json.dumps(inner, sort_keys=True, separators=(",", ":"))[:80]
    unless = f" unless {' or '.join(_cond_text(c) for c in o.get('unless') or [])}" if o.get("unless") else ""
    return (f"{o['id']}: {call} when {' and '.join(_cond_text(c) for c in o['when'])}{unless}; "
            f"set {o.get('set_date')}, {left} step{'s' if left != 1 else ''} left")


def refusal_of(res: Any) -> dict[str, Any]:
    """A refused fire's reason, read from the reply it got: {gate, reason_code, why} from the toolkit's
    `blocked` (the envelope, or the envelope under `result`), else the reason code an env refusal puts before
    its colon ("code: detail"). Nothing readable: {"reason": "unread", ...} (absence named, never a default)."""
    env = getattr(res, "result", None)
    for _ in range(3):
        if isinstance(env, Mapping) and isinstance(env.get("blocked"), Mapping):
            b = env["blocked"]
            out = {"gate": b.get("gate"), "reason_code": b.get("raw_reason") or b.get("gate")}
            why = b.get("summary") or b.get("why")
            if why and " " not in str(why).strip():
                # a summary that is only a code ("interaction_not_valid_for_target") gives way to the game's
                # words, then the gate's sentence
                said = [str(x) for x in (b.get("detail"), b.get("why")) if isinstance(x, str) and " " in x]
                why = "; ".join(said) or why
            if why:
                out["why"] = str(why)[:_ERR_CHARS]
            return out
        env = env.get("result") if isinstance(env, Mapping) else None
    err = str(getattr(res, "error", None) or "")
    code = err.split(":", 1)[0].strip()
    if code and code != "not ok" and " " not in code:
        return {"gate": None, "reason_code": code, "why": err[:_ERR_CHARS]}
    return {"gate": None, "reason_code": None, "reason": "unread: the reply carried no blocked and no code"}


def _brief(do: Mapping[str, Any]) -> str:
    """The call in one line: tool and arguments."""
    return f"{do['tool']} {json.dumps(do.get('args') or {}, sort_keys=True, separators=(',', ':'))}"[:_ERR_CHARS]
