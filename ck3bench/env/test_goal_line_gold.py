"""The turn's goal line when the target title waits only for gold (goal_line.py).

The row below is the toolkit's `do(action_class="formable_titles", candidates=["k_ireland"])` row as the toolkit served
it on the Munster start at 1082.03.21, cut to the fields the line reads.
"""
from __future__ import annotations

import copy

from ck3bench.env import goal_line as G
from ck3bench.env.test_ck3_backend import FakeBridge, _backend, _scn

#: (this row carries no months_to_target; the tests below add it as the toolkit writes it)
ROW_1082 = {
    "title_key": "k_ireland",
    "price_basis": {"at_succession": {"heir_id": 36303, "price": 425.0, "rises": 100.0,
                                      "why": "your own ducal_conquest_perk discount dies with you; create k_ireland "
                                             "before your death to pay 325.0"}},
    "requirements": [
        {"requirement": "de_jure_counties", "have": 12, "need": 7, "of": 14, "met": True},
        {"requirement": "price", "currency": "gold", "cost": 325.0, "have": 103.42, "met": False,
         "months_of_income": 152,
         "income_basis": "221.58 gold short at +1.46 a month (the income read, before the expenses it leaves out; "
                         "armies raised cost more); what raises it: do action_class=income_levers",
         "net_realised": "unread: no step read yet"},
        {"requirement": "game_check", "met": False, "reasons": ["Petty King Murchad does not have gold 325"]},
        {"requirement": "tier_rule", "met": True, "rule": "met"}]}


def test_the_recorded_1082_row_gives_the_shortfall_and_the_death_price():
    line = G.goal_line(ROW_1082)
    assert line == ("k_ireland now waits only for gold: it costs 325, you have 103 (222 short). At your death the "
                    "price rises to 425 (your own ducal_conquest_perk discount dies with you).")
    assert len(line.encode()) < 300


def test_the_months_are_the_toolkit_s_months_to_target_read_as_they_are():
    """The months come from the price row's months_to_target (the toolkit's one helper), never recomputed here."""
    row = copy.deepcopy(ROW_1082)
    row["requirements"][1]["months_to_target"] = {"short": 221.58, "months": 39, "net_monthly": 5.8,
                                                  "from": "1075.03.31", "to": "1075.06.12"}
    assert "(222 short); 39 months at the last step's net +5.80 gold a month. At your death" in G.goal_line(row)
    row["requirements"][1]["months_to_target"] = {"short": 221.58, "months": None, "net_monthly": -2.1,
                                                  "why": "never at the last step's net (-2.1 gold a month): the "
                                                         "purse fell; income_levers raise it"}
    assert "; months to it: never at the last step's net (-2.1 gold a month). At" in G.goal_line(row)


def test_no_line_when_more_than_gold_is_missing_or_the_price_is_met():
    # what is missing beside the gold (the game's check can add a county line) is named, not left out; the gold-only wording is kept for gold alone
    for mutate, said in ((lambda r: r["requirements"][0].update(met=False), "12 of 7 de-jure counties"),
                         (lambda r: r["requirements"][2].update(reasons=["Must hold 2 duchies"]), "Must hold 2 duchies")):
        row = copy.deepcopy(ROW_1082)
        mutate(row)
        line = G.goal_line(row)
        assert line.startswith("k_ireland waits on more than gold: " + said) and "only for gold" not in line
    for mutate in (lambda r: r["requirements"][3].update(met=None),           # tier rule unread
                   lambda r: r["requirements"][1].update(met=True),           # it can be paid now
                   lambda r: r["requirements"][1].update(currency="piety")):
        row = copy.deepcopy(ROW_1082)
        mutate(row)
        assert G.goal_line(row) is None
    row = copy.deepcopy(ROW_1082)
    row["price_basis"] = {"at_succession": {"same": True}}
    assert "death" not in G.goal_line(row)                                    # no rise read: none said


class _Tr:
    def __init__(self, row):
        self.row, self.calls = row, []

    def call(self, tool, args):
        self.calls.append((tool, dict(args)))
        if tool == "do" and args.get("action_class") == "formable_titles":
            return {"ok": True, "result": {"action_class": "formable_titles", "available": [],
                                           "unavailable": [self.row] if self.row else []}}
        return {"ok": True, "result": {"depth": "news", "news": {"nothing_new": True}}}

    def close(self):
        pass


def test_the_backend_puts_the_line_in_the_world_once_per_date(tmp_path):
    tr = _Tr(ROW_1082)
    b, br, host, _ = _backend(tmp_path, bridge=FakeBridge(speed=0, rate=7))
    b.transport_factory = lambda seat, cid, env: tr
    sc = _scn()
    b.open(sc, seed=1)
    b.target_title = "k_ireland"
    w = b.observation("king")
    assert w["goal"].startswith("k_ireland now waits only for gold") and "news" in w
    b.observation("king")
    assert sum(1 for t, a in tr.calls if t == "do") == 1                       # read once per game date
    tr.row = dict(ROW_1082, requirements=[dict(ROW_1082["requirements"][0], met=False)] + ROW_1082["requirements"][1:])
    b.date = "1082.04.01"
    assert b.observation("king")["goal"].startswith("k_ireland waits on more than gold")   # named, not gone
    tr.row = None
    b.date = "1082.05.01"
    assert "goal" not in b.observation("king")                                # no row read: no line


def test_CONTROL_no_target_title_no_read_no_line(tmp_path):
    tr = _Tr(ROW_1082)
    b, br, host, _ = _backend(tmp_path, bridge=FakeBridge(speed=0, rate=7))
    b.transport_factory = lambda seat, cid, env: tr
    b.open(_scn(), seed=1)
    b.target_title = None
    assert "goal" not in b.observation("king") and not [t for t, _ in tr.calls if t == "do"]
