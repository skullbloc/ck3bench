"""Standing orders (orders.py): lever calls a seat leaves at end_turn, sent by the env at the step's checks.

On by default on every wake task; `wake.orders = false` is the task-level switch. Proved here on the FAKE backend
and in-process (no game).

  * the shim's listing with the switch off is the listing from before standing orders existed (sha pins);
  * an order fires once at the first check its conditions hold, the clock runs on, the call is recorded with phase
    `order` and its id, and the seat reads set/fired in its next observation;
  * a refused fire wakes the seat (default) or not (`continue`); `unless` blocks, and an UNREAD `unless` blocks;
  * a ruler change cancels; a step's end expires; an episode's end drops;
  * set-time refusals: shape, caps, unknown top-level args (the toolkit server's own rule, checked against it),
    a read-only tool, an unwitnessable condition, a defeat without confirm, orders where the task takes none.
"""
from __future__ import annotations

import copy
import dataclasses
import hashlib
import io
import json
import subprocess
import sys

import pytest

from ck3bench.env import ParallelEnv, ToolCall
from ck3bench.env import orders as O
from ck3bench.env.errors import EnvRefusal
from ck3bench.env.mcp_shim import SeatShim, own_specs
from ck3bench.env.scenario import BUILTIN_DICTS, scenario_from_dict
from ck3bench.env.server import EnvClient, EnvServer
from ck3bench.env.trajectory import TrajectoryWriter, check_trajectory
from ck3bench.env.types import ToolResult

WAKE = "fake-kingdom-wake"
NEVER = {"metric": "gold", "op": ">=", "value": 1e9}
TAXES = {"tool": "do", "args": {"intent": "raise_taxes"}}


def _sc(off=False, **wake):
    d = copy.deepcopy(BUILTIN_DICTS[WAKE])
    d["wake"].update(wake)
    if off:
        d["wake"]["orders"] = False
    return scenario_from_dict(d)


def _recs(buf):
    return [json.loads(x) for x in buf.getvalue().splitlines()]


def _run(sc, steps, orders_at=None, seed=3):
    buf = io.StringIO()
    env = ParallelEnv(sc, trajectory=TrajectoryWriter(stream=buf))
    obs, _ = env.reset(seed=seed)
    out = [obs]
    for k in range(steps):
        if not env.agents:
            break
        kw = {}
        if orders_at and k in orders_at:
            kw["orders"] = orders_at[k]
        out.append(env.step({s: [] for s in env.agents}, wake={"count": {"max_days": 30}}, **kw)[0])
    env.close()
    return _recs(buf), out, env


# ── off = no standing orders ───────────────────────────────────────────────────────────────────────────────────────

def test_the_header_records_the_setting_either_way():
    for sc, want in ((_sc(), True), (_sc(off=True), False)):
        recs, _, _ = _run(sc, 1)
        assert recs[0]["orders"]["enabled"] is want
    recs, _, _ = _run(_sc(off=True), 1)
    assert recs[0]["orders"]["why"] == "the task declares wake.orders = false"
    assert recs[0]["scenario"]["wake"]["orders"] is False
    recs, _, _ = _run(_sc(), 1)
    assert "orders" not in recs[0]["scenario"]["wake"]         # the default is not written into the declaration


#: sha256 of json.dumps(own_specs(wake_task, ack)) as the shim listed it before standing orders existed
SHIM_PINS = {(False, False): "35947a2296e90792f764511ccfa80dbaeaca7d32e2e2dfd75a949d928f5b5614",
             (False, True): "8e8a431be289f0095a37a344e7b17e2d854eb92c80b7e0885dd09aabbe1d0959",
             (True, False): "eadd993e25af63395f7e8b6f8a82a4b62654b420257afd7cd03ae68e3324b2e7",
             (True, True): "9fd7f4ba02dbb426506fd21cbaf5df707f1dc52bda050f77c31e37f8093c6470"}


@pytest.mark.parametrize("key", sorted(SHIM_PINS))
def test_the_shim_listing_with_orders_off_is_the_base(key):
    from ck3bench.env.test_standing_orders import with_earlier_texts      # the two later texts, put back
    assert hashlib.sha256(json.dumps(with_earlier_texts(own_specs(*key))).encode()).hexdigest() == SHIM_PINS[key]
    on = own_specs(*key, orders=True)
    et = next(t for t in on if t["name"] == "end_turn")
    assert ("orders" in et["inputSchema"]["properties"]) is key[0]     # orders only beside wake


def _shim_listing(sc, ack):
    env = ParallelEnv(sc)
    srv = EnvServer(env, turn_timeout_s=30).start()
    try:
        cl = EnvClient(srv.url, timeout=30)
        cl.reset(seed=1)
        return SeatShim(cl, "count", 5, ack=ack).list_tools()
    finally:
        srv.stop()


def test_the_served_listing_carries_orders_by_default_and_not_when_off():
    for ack in (False, True):
        on = {t["name"]: t for t in _shim_listing(_sc(), ack)}
        off = {t["name"]: t for t in _shim_listing(_sc(off=True), ack)}
        assert "orders" in on["end_turn"]["inputSchema"]["properties"]
        assert "orders" not in off["end_turn"]["inputSchema"]["properties"]
        from ck3bench.env.test_standing_orders import with_earlier_texts  # the two later texts, put back
        assert hashlib.sha256(json.dumps(with_earlier_texts(own_specs(True, ack))).encode()).hexdigest() \
            == SHIM_PINS[(True, ack)]
        assert off["end_turn"] == next(t for t in own_specs(True, ack) if t["name"] == "end_turn")


# ── what an order does ─────────────────────────────────────────────────────────────────────────────────────────

def test_an_order_fires_once_at_the_first_check_and_the_clock_runs_on():
    o = {"id": "tax", "when": [{"metric": "gold", "op": ">=", "value": 100.05}], "do": TAXES}
    recs, obs, _ = _run(_sc(), 2, {0: {"count": [o]}})
    step1 = [r for r in recs if r["kind"] == "step"][0]
    assert step1["wake"]["stop"] == "max_days" and step1["wake"]["days"] == 30     # no stop at the fire
    calls = [r for r in recs if r["kind"] == "call" and r["phase"] == "order"]
    assert len(calls) == 1 and calls[0]["order_id"] == "tax" and calls[0]["result"]["ok"] is True
    assert calls[0]["call"] == {"tool_name": "do", "arguments": {"intent": "raise_taxes"}}
    assert calls[0]["game_date"] not in (None, step1["game_date"])       # the check's date, mid-step
    got = obs[1]["count"]["orders"]
    assert [r["outcome"] for r in got] == ["set", "fired"]
    assert got[1]["verdict"] == "applied" and got[1]["reads"][0]["read"] >= 100.05
    assert [r["phase"] for r in obs[1]["count"]["last_results"]] == ["order"]
    assert step1["orders"]["count"] == got and "orders" not in obs[1]["rival"]
    assert "orders" not in obs[2]["count"]                              # one step: the next has none
    assert check_trajectory(recs) == []


def test_an_order_whose_condition_never_holds_expires_when_its_steps_pass_and_says_it_ended_unfired():
    recs, obs, _ = _run(_sc(), 1, {0: {"count": [{"id": "n", "when": [NEVER], "do": TAXES, "steps": 1}]}})
    got = obs[1]["count"]["orders"]
    assert [r["outcome"] for r in got] == ["set", "expired"] and "ended UNFIRED" in got[1]["why"]
    assert "orders_standing" not in obs[1]["count"]
    assert not [r for r in recs if r["kind"] == "call"]


def test_a_refused_fire_wakes_the_seat_and_continue_does_not():
    bad = {"tool": "declare_war", "args": {"target_id": 999}}           # not a neighbour: the backend refuses
    o = {"id": "war", "when": [{"metric": "gold", "op": ">=", "value": 0}], "do": bad}
    recs, obs, _ = _run(_sc(), 1, {0: {"count": [o]}})
    st = [r for r in recs if r["kind"] == "step"][0]
    assert st["wake"]["stop"] == "condition" and st["wake"]["days"] == 1
    assert st["wake"]["fired"] == [{"seat": "count", "order": "war", "condition": {"order_refused": "war"},
                                    "read": obs[1]["count"]["orders"][1]["error"], "date": st["game_date"]}]
    assert obs[1]["count"]["orders"][1]["verdict"] == "refused" and "invalid_target" in obs[1]["count"]["orders"][1]["error"]
    recs, obs, _ = _run(_sc(), 1, {0: {"count": [dict(o, on_refusal="continue")]}})
    st = [r for r in recs if r["kind"] == "step"][0]
    assert st["wake"]["stop"] == "max_days" and st["wake"]["fired"] == []


def test_unless_blocks_and_is_recorded_once():
    o = {"id": "t", "when": [{"metric": "gold", "op": ">=", "value": 0}],
         "unless": [{"metric": "prestige", "op": ">=", "value": 0}], "do": TAXES}
    recs, obs, _ = _run(_sc(), 1, {0: {"count": [dict(o, steps=1)]}})
    assert [r["outcome"] for r in obs[1]["count"]["orders"]] == ["set", "blocked", "expired"]
    assert not [r for r in recs if r["kind"] == "call"]


def _book(o, **kw):
    return O.Book({"count": [O.parse_orders("count", [o], vocabulary=_VOCAB, tools=("do",),
                                            schema_of=lambda t: _DO_SCHEMA)[0]]}, "1066.09.15")


_VOCAB = {"events": ("ruler_changed", "war_ended"), "metrics": ("gold", "prestige")}
_DO_SCHEMA = {"type": "object", "required": ["intent"],
              "properties": {"intent": {"type": "string"}, "args": {"type": "object"}}}
_START = {"character_id": 1, "metrics": {"gold": 10.0, "prestige": 5.0}, "events": {"war_ended": 0}}


def _fire_ok(seat, o, date):
    _fire_ok.calls.append((seat, o["id"], date))
    return ToolResult("do", True, {"verdict": "applied"})


def test_an_unread_unless_blocks_and_an_unread_when_does_not_fire():
    _fire_ok.calls = []
    o = {"id": "u", "when": [{"metric": "gold", "op": ">=", "value": 0}],
         "unless": [{"metric": "prestige", "op": "<=", "value": -100}], "do": {"tool": "do", "args": {"intent": "x"}}}
    b = _book(o)
    b.check("count", _START, {"character_id": 1, "metrics": {"gold": 20.0, "prestige": None}}, "d1", _fire_ok)
    assert _fire_ok.calls == [] and b.records["count"][-1] == {"order": "u", "outcome": "blocked", "date": "d1",
                                                               "unless": o["unless"][0], "read": "unread"}
    b = _book(dict(o, unless=[]))
    b.check("count", _START, {"character_id": 1, "metrics": {"gold": None, "prestige": 1.0}}, "d1", _fire_ok)
    assert _fire_ok.calls == []
    b.check("count", _START, {"character_id": 1, "metrics": {"gold": 1.0, "prestige": 1.0}}, "d2", _fire_ok)
    b.check("count", _START, {"character_id": 1, "metrics": {"gold": 1.0, "prestige": 1.0}}, "d3", _fire_ok)
    assert _fire_ok.calls == [("count", "u", "d2")]                     # once


def test_a_ruler_change_cancels_every_live_order():
    _fire_ok.calls = []
    b = _book({"id": "c", "when": [{"metric": "gold", "op": ">=", "value": 0}], "do": {"tool": "do",
                                                                                     "args": {"intent": "x"}}})
    b.check("count", _START, {"character_id": 2, "metrics": {"gold": 99.0}}, "d1", _fire_ok)
    assert _fire_ok.calls == [] and b.records["count"][-1]["outcome"] == "cancelled"
    b.check("count", _START, {"character_id": 2, "metrics": {"gold": 99.0}}, "d2", _fire_ok)
    assert _fire_ok.calls == []
    assert [r["outcome"] for r in b.close("d3", {})["count"]] == ["set", "cancelled"]       # not also expired
    assert not b and b.standing("count") == []


def test_a_ruler_change_in_the_env_cancels_before_anything_fires(monkeypatch):
    env = ParallelEnv(_sc())
    env.reset(seed=3)
    real = env.backend.wake_facts
    n = {"k": 0}

    def facts(seat, need=None):
        f = real(seat, need)
        n["k"] += 1
        if n["k"] > 1:                                  # after the start read, a new ruler
            f = dict(f, character_id=f["character_id"] + 1000)
        return f
    monkeypatch.setattr(env.backend, "wake_facts", facts)
    obs, *_ = env.step({s: [] for s in env.agents}, wake={"count": {"max_days": 5}},
                       orders={"count": [{"id": "x", "when": [{"metric": "gold", "op": ">=", "value": 0}],
                                          "do": TAXES}]})
    assert [r["outcome"] for r in obs["count"]["orders"]] == ["set", "cancelled"]
    assert obs["count"]["last_results"] == []


def test_a_seat_whose_episode_ends_drops_its_orders():
    b = _book({"id": "d", "when": [NEVER], "do": {"tool": "do", "args": {"intent": "x"}}})
    recs = b.close("d9", {"count": "line_lost_to_other_dynasty"})
    assert recs["count"][-1] == {"order": "d", "outcome": "dropped", "date": "d9",
                                 "why": "your episode ended (line_lost_to_other_dynasty)"}


# ── refused when set ───────────────────────────────────────────────────────────────────────────────────────────

def _refused(orders, sc=None, seat="count"):
    env = ParallelEnv(sc or _sc())
    env.reset(seed=1)
    with pytest.raises(EnvRefusal) as e:
        env.step({s: [] for s in env.agents}, orders={seat: orders})
    assert env.steps_done == 0                  # refused before anything ran
    return e.value


@pytest.mark.parametrize("orders,code,words", [
    ([{"id": "a", "when": [{"event": "claim_gained"}], "do": TAXES}], "wake_condition_unwitnessable", "claim_gained"),
    ([{"id": "a", "when": [{"event": "nope"}], "do": TAXES}], "wake_malformed", "nope"),
    ([{"id": "a", "when": [], "do": TAXES}], "order_malformed", "1..4"),
    ([{"id": "a", "when": [NEVER], "do": {"tool": "observe"}}], "order_lever_refused", "declare_war"),
    ([{"id": "a", "when": [NEVER], "do": {"tool": "do", "args": {"intent": "raise_taxes", "clock": 1}}}],
     "order_lever_refused", "no argument named clock"),
    ([{"id": "a", "when": [NEVER], "do": {"tool": "do", "args": {"intent": "pray"}}}], "order_lever_refused", "pray"),
    ([{"id": "a", "when": [NEVER], "do": {"tool": "do", "args": {}}}], "order_lever_refused", "intent is required"),
    ([{"id": "a", "when": [NEVER], "do": {"tool": "declare_war", "args": {"target_id": "100"}}}],
     "order_lever_refused", "not of its type"),
    ([{"id": "a", "when": [NEVER], "do": TAXES, "extra": 1}], "order_malformed", "extra"),
    ([{"id": "a", "when": [NEVER], "do": TAXES}] * 2, "order_malformed", "repeats"),
    ([{"id": f"a{i}", "when": [NEVER], "do": TAXES} for i in range(5)], "order_malformed", "at most 4"),
    ([{"id": "a", "when": [NEVER], "do": TAXES, "confirm": "surrender"}], "order_malformed", "needs no confirmation"),
    ([{"id": "a", "when": [NEVER] * 4, "unless": [NEVER] * 4, "do": {"tool": "do", "args": {"intent": "raise_taxes",
                                                                                         "args": {"x": "y" * 900}}}}],
     "order_malformed", "bytes"),
])
def test_refused_at_set_time(orders, code, words):
    e = _refused(orders)
    assert e.code == code and words in e.detail, (e.code, e.detail)


def test_orders_where_the_task_takes_none_are_refused():
    assert _refused([{"id": "a", "when": [NEVER], "do": TAXES}], _sc(off=True)).code == "orders_not_declared"
    env = ParallelEnv("fake-kingdom")
    env.reset(seed=1)
    with pytest.raises(EnvRefusal) as e:
        env.step({s: [] for s in env.agents}, orders={"count": [{"id": "a", "when": [NEVER], "do": TAXES}]})
    assert e.value.code == "orders_not_declared" and "fixed-cadence" in e.value.detail


@pytest.mark.parametrize("intent,terms,word", [("make_peace", "surrender", "surrender"), ("end_war", "we_yield", "we_yield")])
def test_a_defeat_needs_confirm_naming_it(intent, terms, word):
    o = {"id": "p", "when": [NEVER], "do": {"tool": "do", "args": {"intent": intent,
                                                                    "args": {"war_id": 7, "terms": terms}}}}
    kw = dict(vocabulary=_VOCAB, tools=("do",), schema_of=lambda t: _DO_SCHEMA)
    with pytest.raises(EnvRefusal) as e:
        O.parse_orders("s", [o], **kw)
    assert e.value.code == "order_needs_confirm" and "DEFEAT" in e.value.detail
    with pytest.raises(EnvRefusal):
        O.parse_orders("s", [dict(o, confirm="yes")], **kw)
    assert O.parse_orders("s", [dict(o, confirm=word)], **kw)[0]["confirm"] == word
    white = copy.deepcopy(o)
    white["do"]["args"]["args"]["terms"] = "white_peace"
    assert "confirm" not in O.parse_orders("s", [white], **kw)[0]


def test_an_order_whose_tool_cannot_be_described_is_refused():
    def none(t):
        raise EnvRefusal("tool_specs_unavailable", "no schema", "backend")
    with pytest.raises(EnvRefusal) as e:
        O.parse_orders("s", [{"id": "a", "when": [NEVER], "do": TAXES}], vocabulary=_VOCAB, tools=("do",),
                       schema_of=none)
    assert e.value.code == "tool_specs_unavailable"


_MIRROR = r"""
import json, sys
sys.path.insert(0, sys.argv[1])
import mcp_server as ms
cases = json.loads(sys.argv[2])
print(json.dumps([ms.unknown_arguments(s, a) for s, a in cases]))
"""


def test_the_unknown_argument_rule_is_the_toolkit_servers():
    cases = [({"properties": {"a": {}, "b": {}}}, {"a": 1, "c": 2, "z": 0}), ({}, {"x": 1}),
             ({"properties": {}}, {}), ({"properties": {"a": {}}}, ["a"]), (None, {"q": 1})]
    from ck3bench.env import toolkit_source
    d = toolkit_source.server_dir()
    if d is None:
        pytest.skip(toolkit_source.ABSENT)
    r = subprocess.run([sys.executable, "-c", _MIRROR, str(d), json.dumps(cases)],
                       capture_output=True, text=True, timeout=300)
    if r.returncode != 0 and "No module named" in r.stderr:
        pytest.skip("the toolkit server's dependencies are not installed here")
    assert r.returncode == 0, r.stderr[-2000:]
    theirs = json.loads(r.stdout.strip().splitlines()[-1])
    assert theirs == [O.unknown_arguments(s, a) for s, a in cases] == [["c", "z"], ["x"], [], [], ["q"]]


# ── the routes: server and shim ────────────────────────────────────────────────────────────────────────────────

def test_the_shim_carries_orders_and_a_refused_order_keeps_the_turn_open():
    env = ParallelEnv(_sc(), notes_pad_bytes=64)
    srv = EnvServer(env, turn_timeout_s=30).start()
    try:
        cl = EnvClient(srv.url, timeout=30)
        cl.reset(seed=1)
        sh = SeatShim(cl, "count", 5, ack=True)
        out, err = sh.call("end_turn", {"notes": "n", "orders": [{"id": "a", "when": [NEVER], "do": {"tool": "observe"}}]})
        assert err and out["code"] == "order_lever_refused" and sh.ended is None
        assert env.observe("count")["notes_pad"]["text"] == ""          # refused before the notes were written
        out, err = sh.call("end_turn", {"notes": "n", "wake": {"max_days": 3},
                                        "orders": [{"id": "a", "when": [NEVER], "do": TAXES}]})
        assert not err and out["orders_set"] == 1 and out["turn_ended"]
        cl.submit("rival", [])
        seen = env.observe("count")
        assert [r["outcome"] for r in seen["orders"]] == ["set"]          # it stands: the step ended, it did not
        assert seen["orders_standing"] == ["a: do raise_taxes when gold >= 1000000000; set 1066.09.15, 9 steps left"]
    finally:
        srv.stop()


def test_a_fired_declaration_is_scored_as_the_seats_and_marked_as_an_orders(monkeypatch):
    from ck3bench.env import env as E
    calls = []
    env = ParallelEnv(_sc())
    env.reset(seed=3)
    target = env.observe("count")["world"]["neighbours"][0]
    env._outcome_calls = calls
    # the decision-task record path only (the fake reads no outcomes): the strength read stubbed
    env.scenario = dataclasses.replace(env.scenario, outcome="stub")
    monkeypatch.setattr(E._outcome, "power", lambda reader, tid: {"stub": tid})
    monkeypatch.setattr(env.backend, "outcome_reader", lambda seat: None)
    env.step({s: [] for s in env.agents}, wake={"count": {"max_days": 3}},
             orders={"count": [{"id": "w", "when": [{"metric": "gold", "op": ">=", "value": 0}],
                                "do": {"tool": "declare_war", "args": {"target_id": target}}}]})
    assert [(c["phase"], c["order_id"], c["call"]["tool_name"]) for c in calls] == [("order", "w", "declare_war")]


def test_a_refused_fire_still_wakes_at_the_games_paused_stop_read(monkeypatch):
    """The real game's loop (ck3.py _run_clock_probed) stops on a running check's fire only if the probe holds
    again when read paused at the stop; the fired order is not sent twice, so its wake must hold on its own."""
    env = ParallelEnv(_sc())
    env.reset(seed=3)
    real = env.backend.advance_until

    def like_the_game(max_days, check_days, probe):
        def twice(date):
            got = probe(date)
            return probe(date) if got else got          # the stop's paused re-read decides
        return real(max_days, check_days, twice)
    monkeypatch.setattr(env.backend, "advance_until", like_the_game)
    o = {"id": "war", "when": [{"metric": "gold", "op": ">=", "value": 0}],
         "do": {"tool": "declare_war", "args": {"target_id": 999}}}
    obs, _, _, _, info = env.step({s: [] for s in env.agents}, wake={"count": {"max_days": 20}},
                                  orders={"count": [o]})
    assert env._last_wake["stop"] == "condition" and env._last_wake["days"] == 1
    assert [r["outcome"] for r in obs["count"]["orders"]] == ["set", "fired"]
