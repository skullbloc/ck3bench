"""An order dies with the ruler who set it; one that names no character is handed back ready to set again.

An order that creates a title by its key (`do mint_title` whose arguments are the title's key alone) names no
character, so it is as valid for the heir. The end_turn listing says an order ends when the ruler changes, so it is
still cancelled; the cancel record carries the order as end_turn takes it, and the turn's ruler_changed row carries it
again with the title's price read for the new ruler beside the threshold, when that price reads. Every field a
hand-back's `how` or a price note's `at_death` names is in the record that carries it.
"""
from __future__ import annotations

import copy

from ck3bench.env import ParallelEnv
from ck3bench.env import orders as O
from ck3bench.env.scenario import BUILTIN_DICTS, scenario_from_dict

MINT = {"id": "mintk", "when": [{"metric": "gold", "op": ">=", "value": 325.0}],
        "do": {"tool": "do", "args": {"intent": "mint_title", "args": {"title_key": "k_fake"}}}, "steps": 50}
TAXES = {"id": "tax", "when": [{"metric": "gold", "op": ">=", "value": 1e9}], "do": {"tool": "do", "args": {
    "intent": "raise_taxes"}}}


def _fire(*a):
    raise AssertionError("nothing fires on a ruler change")


def test_RED_the_cancel_of_a_mint_order_hands_it_back_and_a_named_call_does_not():
    b = O.Book()
    b.add("s", [dict(MINT), dict(TAXES)], "1078.09.09", ruler=1)
    b.check("s", {"character_id": 1}, {"character_id": 99, "metrics": {"gold": 58.0}}, "1078.12.16", _fire)
    recs = {r["order"]: r for r in b.records["s"] if r["outcome"] == "cancelled"}
    assert set(recs) == {"mintk", "tax"} and "set_again" not in recs["tax"]
    again = recs["mintk"]["set_again"]
    assert again["order"] == {"id": "mintk", "when": MINT["when"], "do": MINT["do"], "steps": 50}
    assert "end_turn orders" in again["how"]


def test_RED_the_handed_back_order_is_priced_for_the_new_ruler():
    b = O.Book()
    b.add("s", [dict(MINT)], "1078.09.09", ruler=1)
    b.check("s", {"character_id": 1}, {"character_id": 99}, "1078.12.16", _fire)
    (again,) = O.set_again_of(b.records["s"], lambda k: {"title_key": k, "cost": 425.0, "currency": "gold"})
    assert again["cancelled"] == "mintk" and again["price"]["price"] == 425.0
    assert "below the price read, 425" in again["price"]["words"]
    (unpriced,) = O.set_again_of(b.records["s"], None)
    assert "price" not in unpriced                                   # no read: no price, never a guessed one


def test_CONTROL_a_named_war_order_is_cancelled_as_before():
    """Non-discriminating on the hand-back: any order still ends at the ruler change."""
    war = {"id": "w", "when": [{"metric": "gold", "op": ">=", "value": 1e9}],
           "do": {"tool": "declare_war", "args": {"declare_args": {"target_id": 5, "cb": "claim_cb"}}}}
    b = O.Book()
    b.add("s", [war], "1078.09.09", ruler=1)
    b.check("s", {"character_id": 1}, {"character_id": 99}, "1078.12.16", _fire)
    (rec,) = [r for r in b.records["s"] if r["outcome"] == "cancelled"]
    assert "set_again" not in rec and not b.live("s")


def test_RED_the_turns_ruler_changed_row_carries_it_and_the_order_sets_again(monkeypatch):
    d = copy.deepcopy(BUILTIN_DICTS["fake-kingdom-wake"])
    env = ParallelEnv(scenario_from_dict(d))
    env.reset(seed=3)
    seat = env.agents[0]
    mint = dict(MINT, when=[{"metric": "gold", "op": ">=", "value": 1e9}])
    env.step({s: [] for s in env.agents}, wake={s: {"max_days": 30} for s in env.agents}, orders={seat: [mint]})
    real_facts, real_obs = env.backend.wake_facts, env.backend.observation
    monkeypatch.setattr(env.backend, "wake_facts", lambda s, need=None: dict(real_facts(s, need), character_id=99))

    def obs(s):
        w = dict(real_obs(s))
        w["news"] = dict(w.get("news") or {}, ruler_changed={"from": 1, "to": 99})
        return w
    monkeypatch.setattr(env.backend, "observation", obs)
    monkeypatch.setattr(env.backend, "title_price", lambda s, k: {"title_key": k, "cost": 425.0, "currency": "gold"},
                        raising=False)
    o, *_ = env.step({s: [] for s in env.agents}, wake={s: {"max_days": 30} for s in env.agents})
    oc = o[seat]["world"]["news"]["ruler_changed"]["orders_cancelled"]
    (again,) = oc["set_again"]
    assert oc["orders"] == ["mintk"] and again["price"]["price"] == 425.0
    monkeypatch.setattr(env.backend, "wake_facts", real_facts)     # the fake's ruler reads as the setter again
    o, *_ = env.step({s: [] for s in env.agents}, wake={s: {"max_days": 30} for s in env.agents},
                     orders={seat: [again["order"]]})
    assert o[seat]["orders_standing"][0].startswith("mintk: do mint_title")


# ── every field a text names is in the record carrying it ────────────────────

import re

FIELD = re.compile(r"\bset_again(?:\.(\w+))?")
PRICE = lambda k: {"title_key": k, "cost": 425.0, "currency": "gold", "at_death": 640.0}
NAMED = {"id": "mintn", "when": [{"metric": "gold", "op": ">=", "value": 325.0}],
         "do": {"tool": "do", "args": {"intent": "mint_title", "args": {"title_key": "k_fake", "character_id": 7}}}}


def _fields(text):
    """(names `set_again` at all, the set_again.<field> names) in a text."""
    hits = FIELD.findall(text)
    return bool(hits), {f for f in hits if f}


def _blocks(records, price_of):
    """Every set_again block a seat is shown: the cancel records' copies, then the ruler_changed news' copies."""
    return [r["set_again"] for r in records if isinstance(r.get("set_again"), dict)] + \
        O.set_again_of(records, price_of)


def _cancelled(order):
    b = O.Book()
    b.add("s", [dict(order)], "1078.09.09", ruler=1)
    b.check("s", {"character_id": 1}, {"character_id": 99}, "1078.12.16", _fire)
    return b.records["s"]


def test_every_field_a_how_text_names_is_in_its_block():
    """The cancel record's copy carries no price; the news copy carries `price` only when it read one. A `how` that
    names set_again.<field> names a field of the block that carries it, priced, unpriced and in the cancel record."""
    seen = 0
    for price_of in (PRICE, None, lambda k: None):
        for blk in _blocks(_cancelled(MINT), price_of):
            _, named = _fields(blk["how"])
            assert named and named <= set(blk), (named, sorted(blk))
            seen += 1
    assert seen == 6


def test_CONTROL_the_superseded_how_names_a_price_the_cancel_record_lacks():
    """The superseded text (one `how` for every copy, naming set_again.price) is refused by the same check."""
    old = ("this order names no character, so it can stand for your new ruler: send set_again.order in this turn's "
           "end_turn orders to set it again; set_again.price is the title's price read for him")
    (blk,) = [r["set_again"] for r in _cancelled(MINT) if "set_again" in r]
    _, named = _fields(old)
    assert not named <= set(blk) and "price" in named - set(blk)


def test_an_at_death_text_promises_a_hand_back_only_to_an_order_that_gets_one():
    """A price note's at_death names set_again only for an order whose cancel record carries set_again."""
    for order, handed in ((MINT, True), (NAMED, False)):
        note = O.price_note(order, PRICE)
        assert note and "at_death" in note, note
        names, _ = _fields(note["at_death"])
        got = any("set_again" in r for r in _cancelled(order))
        assert names is handed and got is handed, (order["id"], note["at_death"], got)


def test_CONTROL_a_named_order_still_gets_its_price_note_and_its_cancel():
    """Non-discriminating on the hand-back: the named order is priced and cancelled as before."""
    note = O.price_note(NAMED, PRICE)
    assert note["price"] == 425.0 and "below the price read, 425" in note["words"]
    assert [r["outcome"] for r in _cancelled(NAMED) if r["outcome"] == "cancelled"] == ["cancelled"]
