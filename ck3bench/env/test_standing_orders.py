"""A standing order STANDS until it fires, is cancelled, the ruler changes, or its own step limit passes.

An order that lived one step expired before its condition came true more often than not, and a seat could read
the expiry as a refusal by the game; an order such as "do mint_title k_ireland when gold >= 325" must stand until
the gold comes. Proved here on the fake backend and against the toolkit's own `do` schema.
"""
from __future__ import annotations

import copy
import json
import subprocess
import sys

import pytest

from ck3bench.env import ParallelEnv
from ck3bench.env import orders as O
from ck3bench.env.errors import EnvRefusal
from ck3bench.env.fake import MINT_GOLD
from ck3bench.env.scenario import BUILTIN_DICTS, scenario_from_dict

NEVER = {"metric": "gold", "op": ">=", "value": 1e9}
TAXES = {"tool": "do", "args": {"intent": "raise_taxes"}}


def _sc(**fake):
    d = copy.deepcopy(BUILTIN_DICTS["fake-kingdom-wake"])
    d["fake"].update(fake)
    return scenario_from_dict(d)


def _step(env, orders=None, days=30):
    obs, rew, te, tr, inf = env.step({s: [] for s in env.agents}, wake={s: {"max_days": days} for s in env.agents},
                                     **({"orders": {"count": orders}} if orders is not None else {}))
    return obs["count"], te["count"] or tr["count"], inf["count"]


def test_the_kingdom_order_stands_across_steps_listed_every_turn_and_fires_when_the_gold_comes():
    """The recorded case, on the fake: the de jure share is met, only gold is short; `mint_title when gold >=
    price` stands step after step (each observation lists it) and fires at the first check the gold is there."""
    env = ParallelEnv(_sc(counties=[10, 1], dejure=[5, 1], gold=[MINT_GOLD - 3.0, 80.0]))
    env.reset(seed=3)
    order = {"id": "kingdom", "when": [{"metric": "gold", "op": ">=", "value": MINT_GOLD + 0.5}],
             "do": {"tool": "do", "args": {"intent": "mint_title", "args": {"title_key": "k_fake"}}}}
    seen = []
    obs, done, info = _step(env, [order])
    seen.append(obs)
    k = 1
    while not done and k < 8:
        obs, done, info = _step(env)
        seen.append(obs)
        k += 1
    stood = [o for o in seen if "orders_standing" in o]
    assert len(stood) >= 2                                   # it stood across steps (one step would have lost it)
    for o in stood:
        assert o["orders_standing"][0].startswith("kingdom: do mint_title {\"title_key\":\"k_fake\"} when gold >= 60.5")
    assert [r["outcome"] for r in seen[0]["orders"]] == ["set"]
    fired = [r for o in seen for r in o.get("orders") or [] if r["outcome"] == "fired"]
    assert len(fired) == 1 and fired[0]["verdict"] == "applied"
    assert done and info["termination_reason"] == "goal_reached"   # the title held ends the episode
    assert "orders_standing" not in seen[-1]


def test_an_order_stands_its_default_steps_then_ends_unfired_and_says_so():
    env = ParallelEnv(_sc())
    env.reset(seed=3)
    obs, _, _ = _step(env, [{"id": "n", "when": [NEVER], "do": TAXES}], days=10)
    left = []
    for _ in range(O.DEFAULT_STEPS - 1):
        left.append(obs["orders_standing"][0].rsplit(", ", 1)[1])
        obs, _, _ = _step(env, days=10)
    assert left[0] == f"{O.DEFAULT_STEPS - 1} steps left" and left[-1] == "1 step left"
    rec = obs["orders"][-1]
    assert rec["outcome"] == "expired" and rec["why"].startswith(f"ended UNFIRED: it stood {O.DEFAULT_STEPS} steps")
    assert "orders_standing" not in obs


def test_cancel_replace_and_the_cap_on_standing_orders():
    env = ParallelEnv(_sc())
    env.reset(seed=3)
    _step(env, [{"id": f"o{i}", "when": [NEVER], "do": TAXES} for i in range(3)])
    with pytest.raises(EnvRefusal) as e:                          # 3 standing + 2 new > 4: refused, nothing ran
        _step(env, [{"id": "x", "when": [NEVER], "do": TAXES}, {"id": "y", "when": [NEVER], "do": TAXES}])
    assert e.value.code == "order_malformed" and "5 orders would stand" in e.value.detail and "o0, o1, o2" in e.value.detail
    with pytest.raises(EnvRefusal) as e:
        _step(env, [{"id": "zz", "cancel": True}])
    assert "no standing order is named 'zz'" in e.value.detail
    obs, _, _ = _step(env, [{"id": "o0", "cancel": True}, {"id": "o1", "when": [NEVER], "do": TAXES, "steps": 2},
                            {"id": "x", "when": [NEVER], "do": TAXES}, {"id": "y", "when": [NEVER], "do": TAXES}])
    outs = [(r["order"], r["outcome"], r.get("why")) for r in obs["orders"]]
    assert ("o0", "cancelled", "you cancelled it") in outs
    assert ("o1", "cancelled", "replaced by the order you set with the same id") in outs and ("o1", "set", None) in outs
    assert [x.split(":")[0] for x in obs["orders_standing"]] == ["o2", "o1", "x", "y"]
    assert "o1: do raise_taxes when gold >= 1000000000; set" in obs["orders_standing"][1]
    assert obs["orders_standing"][1].endswith("1 step left")


@pytest.mark.parametrize("steps", [0, O.MAX_STEPS + 1, True, 2.0])
def test_a_step_limit_outside_1_to_the_max_is_refused(steps):
    env = ParallelEnv(_sc())
    env.reset(seed=3)
    with pytest.raises(EnvRefusal) as e:
        _step(env, [{"id": "n", "when": [NEVER], "do": TAXES, "steps": steps}])
    assert e.value.code == "order_malformed" and f"1..{O.MAX_STEPS}" in e.value.detail


def test_a_ruler_change_steps_later_cancels_the_order_set_by_the_ruler_before(monkeypatch):
    env = ParallelEnv(_sc())
    env.reset(seed=3)
    _step(env, [{"id": "n", "when": [NEVER], "do": TAXES}])
    obs, _, _ = _step(env)
    assert obs["orders_standing"]
    real = env.backend.wake_facts
    monkeypatch.setattr(env.backend, "wake_facts", lambda s, need=None: dict(real(s, need), character_id=99))
    obs, _, _ = _step(env)
    rec = obs["orders"][-1]
    assert rec["outcome"] == "cancelled" and "your ruler changed (1 -> 99)" in rec["why"]
    assert "orders_standing" not in obs


def test_CONTROL_an_order_that_fires_in_its_first_step_is_recorded_as_before():
    """Non-discriminating on the lifetime: the one-step case (set, fired, nothing standing) is unchanged."""
    env = ParallelEnv(_sc())
    env.reset(seed=3)
    obs, _, _ = _step(env, [{"id": "t", "when": [{"metric": "gold", "op": ">=", "value": 0}], "do": TAXES}])
    assert [r["outcome"] for r in obs["orders"]] == ["set", "fired"] and "orders_standing" not in obs
    obs, _, _ = _step(env)
    assert "orders" not in obs and "orders_standing" not in obs


_DO_SCHEMA = r"""
import json, sys
sys.path.insert(0, sys.argv[1])
import mcp_server as ms
print(json.dumps(next(t["input_schema"] for t in ms.registered_tool_definitions() if t["name"] == "do")))
"""


def test_create_k_ireland_when_gold_reaches_the_price_is_an_order_the_toolkit_s_do_takes():
    """The order the Munster seat needed, checked against the toolkit's own served `do` schema."""
    from ck3bench.env import toolkit_source
    d = toolkit_source.server_dir()
    if d is None:
        pytest.skip(toolkit_source.ABSENT)
    r = subprocess.run([sys.executable, "-c", _DO_SCHEMA, str(d)], capture_output=True, text=True, timeout=300)
    if r.returncode != 0 and "No module named" in r.stderr:
        pytest.skip("the toolkit server's dependencies are not installed here")
    assert r.returncode == 0, r.stderr[-2000:]
    schema = json.loads(r.stdout.strip().splitlines()[-1])
    o = {"id": "kingdom", "when": [{"metric": "gold", "op": ">=", "value": 325}], "steps": 40,
         "do": {"tool": "do", "args": {"intent": "mint_title", "args": {"title_key": "k_ireland"}}}}
    vocab = {"events": ("ruler_changed",), "metrics": ("gold", "prestige", "realm_counties")}
    (rec,) = O.parse_orders("munster", [o], vocabulary=vocab, tools=("do", "declare_war"), schema_of=lambda t: schema)
    assert rec["steps"] == 40 and rec["do"]["args"]["intent"] == "mint_title"
    b = O.Book({"munster": [rec]}, "1085.07.20", {"munster": 56200})
    assert b.standing("munster") == ['kingdom: do mint_title {"title_key":"k_ireland"} when gold >= 325; '
                                     'set 1085.07.20, 40 steps left']
    with pytest.raises(EnvRefusal):                                   # CONTROL: the schema check is not blind
        O.parse_orders("munster", [dict(o, do={"tool": "do", "args": {"intent": "mint_title", "title": "x"}})],
                       vocabulary=vocab, tools=("do",), schema_of=lambda t: schema)


# ── the shim's listing: the two texts the standing-order lifetime changed, named, so the earlier listing's pins
# still hold for everything else ──

#: end_turn's `orders` property and its `notes` description as they were before orders stood past one step
BASE_ORDERS_PROPERTY = {
    "type": "array", "maxItems": 4,
    "description": "Standing orders for the step you end: when every `when` holds (and no `unless`) at a check, "
                   "the env sends `do` once, verbatim, as if you called it; the clock keeps running. when/unless "
                   "take wake conditions. An order lives one step; it dies if your ruler changes; a refused one "
                   "wakes you. A surrender (make_peace) or we_yield (end_war) needs confirm set to that word. "
                   "Your next observation's `orders` says what each did.",
    "items": {"type": "object", "required": ["id", "when", "do"], "additionalProperties": False,
              "properties": {"id": {"type": "string"}, "when": {"type": "array"}, "unless": {"type": "array"},
                             "do": {"type": "object", "required": ["tool"], "additionalProperties": False,
                                    "properties": {"tool": {"enum": ["declare_war", "do"]},
                                                   "args": {"type": "object"}}},
                             "on_refusal": {"enum": ["wake", "continue"]}, "confirm": {"type": "string"}}}}
BASE_NOTES_DESCRIPTION = ("your whole notes pad for your next turn (it replaces the previous one; over the pad's byte "
                          "bound it is refused, not cut, and your turn stays open); omit it to keep the pad as it is")


def with_earlier_texts(specs):
    """The shim's own specs with those two texts put back as they were (each must be there)."""
    from ck3bench.env import mcp_shim as S
    out = json.loads(json.dumps(specs))
    for t in out:
        props = (t.get("inputSchema") or {}).get("properties") or {}
        if "notes" in props:
            assert props["notes"] == S.NOTES_PROPERTY
            props["notes"] = dict(props["notes"], description=BASE_NOTES_DESCRIPTION)
        if "orders" in props:
            assert props["orders"] == S.ORDERS_PROPERTY
            props["orders"] = json.loads(json.dumps(BASE_ORDERS_PROPERTY))
    return out


def test_the_listing_differs_from_the_base_only_in_the_two_named_texts():
    from ck3bench.env import mcp_shim as S
    for wake in (False, True):
        for ack in (False, True):
            for orders in (False, True):
                now = S.own_specs(wake, ack, orders)
                assert with_earlier_texts(now) != now or not (ack or (wake and orders))   # CONTROL: the revert does something
    assert "stands until it fires" in S.ORDERS_PROPERTY["description"]
    assert "your turn still ends" in S.NOTES_PROPERTY["description"]
