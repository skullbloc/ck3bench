"""The goal line covers the chain (a duchy, then the kingdom) and never goes silent while gold is part of
the gap. The rows are the toolkit's formable_titles reply for k_ireland and d_leinster as the toolkit served it on the
Munster start at 1071.11.07 (recorded_chain_rows.json: d_leinster creatable at 162.5 with 192.89 in hand, k_ireland waiting on the
second duchy and 325 gold); the toolkit that served them had no `dejure_unheld`, which the tests add as it writes it now.
No game."""
from __future__ import annotations

import copy
import json
from pathlib import Path

from ck3bench.env import goal_line as G
from ck3bench.env.test_ck3_backend import FakeBridge, _backend, _scn

ROWS = json.loads((Path(__file__).parent / "recorded_chain_rows.json").read_text())["rows"]
UNHELD = ["d_leinster", "d_meath", "d_ulster", "d_connacht"]


def _rows(unheld=UNHELD):
    rows = copy.deepcopy(ROWS)
    k = next(r for r in rows if r["title_key"] == "k_ireland")
    next(q for q in k["requirements"] if q["requirement"] == "tier_rule")["dejure_unheld"] = list(unheld)
    return k, rows


def _price(row):
    return next(q for q in row["requirements"] if q["requirement"] == "price")


def test_the_recorded_1071_rows_give_the_duchy_call_then_the_kingdom():
    k, rows = _rows()
    line = G.goal_line(k, rows)
    assert line.startswith("k_ireland needs 1 more duchy title first: d_leinster can be created now (it costs 162.5, "
                           "you have 193); the call: mcp__ck3seat__do(intent=\"mint_title\", "
                           "args={\"title_key\": \"d_leinster\"}). Then k_ireland waits only for gold (325): 294.61 gold "
                           "still needed in all (487.5)"), line
    assert line.endswith("At your death the price rises to 425 (your own ducal_conquest_perk discount dies with you).")
    assert len(line.encode()) < 500


def test_the_months_are_at_the_toolkit_s_net_and_say_what_standing_down_is_worth():
    k, rows = _rows()
    _price(k)["months_to_target"] = {"short": 132.11, "months": 23, "net_monthly": 6.0,
                                     "armies_raised": {"men": 1800, "upkeep_a_month": 5.4,
                                                       "if_stood_down": {"net_monthly": 11.4, "months": 12}}}
    line = G.goal_line(k, rows)
    assert "294.61 gold still needed in all (487.5); 50 months at the last step's net +6.00 gold a month (1800 men " \
           "raised cost 5.40 a month; stood down, 26 months at +11.40)" in line, line


def test_a_duchy_waiting_only_for_gold_is_said_with_the_whole_bill():
    k, rows = _rows()
    d = next(r for r in rows if r["title_key"] == "d_leinster")
    for q in d["requirements"]:
        if q["requirement"] == "price":
            q.update(have=100.0, met=False)
        if q["requirement"] == "game_check":
            q.update(met=False, reasons=["Murchad does not have gold 162.5"])
    d["blocked"] = {"gate": "cannot_afford"}
    _price(k)["have"] = 100.0
    line = G.goal_line(k, rows)
    assert "d_leinster waits only for gold (it costs 162.5, you have 100). Then k_ireland waits only for gold (325): " \
           "387.5 gold still needed in all (487.5)" in line, line


def test_no_creatable_duchy_says_so_and_names_where_to_look():
    k, rows = _rows(unheld=["d_meath"])          # no row read for it
    line = G.goal_line(k, rows)
    assert line.startswith("k_ireland needs 1 more duchy title first: none of its de-jure duchy titles") and \
        "inspect title_key=k_ireland" in line


def test_CONTROL_the_line_without_the_chain_is_silent_on_these_rows():
    """Without the chain (no dejure_unheld, a read without it) the tier rule leaves no gold-only
    line; with the chain, the missing requirement is named."""
    k = copy.deepcopy(next(r for r in ROWS if r["title_key"] == "k_ireland"))
    assert G.gold_only(k) is None and G.creatable_now(k) is None
    assert G.goal_line(k, copy.deepcopy(ROWS)).startswith("k_ireland waits on more than gold: hold 2 duchy titles")


def test_negative_gold_keeps_the_line_and_a_county_line_names_itself():
    """Negative gold (-1.30 .. -12.05) keeps the line; when the game's check adds 'You only have 7 of 8 required
    Counties' beside the gold, the line names it instead of going silent."""
    row = {"title_key": "k_ireland", "requirements": [
        {"requirement": "de_jure_counties", "have": 7, "need": 7, "of": 14, "met": True},
        {"requirement": "price", "currency": "gold", "cost": 425.0, "have": -20.4, "met": False},
        {"requirement": "game_check", "met": False, "reasons": ["Petty King Brian II does not have gold 425"]},
        {"requirement": "tier_rule", "met": True, "rule": "met"}]}
    assert G.goal_line(row).startswith("k_ireland now waits only for gold: it costs 425, you have -20.40 (445 short)")
    row["requirements"][2]["reasons"].insert(0, "You only have 7 of 8 required Counties")
    assert G.goal_line(row) == ("k_ireland waits on more than gold: You only have 7 of 8 required Counties; and gold: it "
                                "costs 425, you have -20.40.")


def test_chain_keys_are_the_target_row_s_unheld_titles_only_while_the_rule_is_unmet():
    k, _rows_ = _rows()
    assert G.chain_keys(k) == UNHELD
    next(q for q in k["requirements"] if q["requirement"] == "tier_rule")["met"] = True
    assert G.chain_keys(k) == [] and G.chain_keys(None) == []


class _Tr:
    def __init__(self):
        self.calls = []

    def call(self, tool, args):
        self.calls.append((tool, dict(args)))
        if tool == "do" and args.get("action_class") == "formable_titles":
            k, rows = _rows()
            want = set(args.get("candidates") or [])
            return {"ok": True, "result": {"action_class": "formable_titles", "available": [],
                                           "unavailable": [r for r in rows if r["title_key"] in want]}}
        return {"ok": True, "result": {"depth": "news", "news": {"nothing_new": True}}}

    def close(self):
        pass


def test_the_backend_reads_the_chain_with_the_target_and_puts_its_line_in_the_world(tmp_path):
    tr = _Tr()
    b, br, host, _ = _backend(tmp_path, bridge=FakeBridge(speed=0, rate=7))
    b.transport_factory = lambda seat, cid, env: tr
    b.open(_scn(), seed=1)
    b.target_title = "k_ireland"
    w = b.observation("king")
    assert w["goal"].startswith("k_ireland needs 1 more duchy title first: d_leinster can be created now")
    reads = [a["candidates"] for t, a in tr.calls if t == "do"]
    assert reads == [["k_ireland"], ["k_ireland"] + UNHELD]
