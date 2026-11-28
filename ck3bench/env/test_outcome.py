"""Decision-task outcomes (outcome.py): the reads, the scores, and the env's wiring (start read in the header, a
witness on each applied declaration, the end read and the score in the end record). The world here is the fake
backend with a stub reader standing in for the seat's toolkit server: nothing here is the game."""
from __future__ import annotations

import copy
import io

import pytest

from ck3bench.env import ParallelEnv, ToolCall
from ck3bench.env import outcome as O
from ck3bench.env.errors import EnvRefusal
from ck3bench.env.fake import FakeBackend
from ck3bench.env.scenario import BUILTIN_DICTS, scenario_from_dict
from ck3bench.env.trajectory import TrajectoryWriter, check_trajectory
from ck3bench.env.types import ToolResult

ATHLONE, OSSORY, DUBLIN = 469, 513, 463


def county(tid, key, yours):
    return {"title_id": tid, "key": key, "holder_id": 1 if yours else 2, "realm_ruler_id": 1 if yours else 2,
            "you_control": yours, "your_claim": False}


def world(*, yours=("c_dublin",), cid=31506, wars=(), keeps=("c_dublin",), intact=False, gold=50.0, prestige=300.0):
    rows = [county(DUBLIN, "c_dublin", "c_dublin" in yours), county(ATHLONE, "c_athlone", "c_athlone" in yours),
            county(OSSORY, "c_ossory", "c_ossory" in yours)]
    return {"character": {"id": cid, "name": "x", "alive": True, "gold": gold, "prestige": prestige, "piety": 100.0},
            "economy": {"realm_total_counties": len(yours)},
            "k_ireland": {"holder_id": None, "summary": {}, "counties": rows},
            "claims": [{"title_id": ATHLONE, "title_name": "Athlone", "holder_id": 700, "pressed": False},
                       {"title_id": OSSORY, "title_name": "Ossory", "holder_id": 800, "pressed": False}],
            "wars": list(wars),
            "succession": {"outcome": "intact" if intact else "splits", "passes_intact": intact,
                           "primary_heir_id": 9, "primary_keeps": list(keeps)},
            "claim_power": [{"target_id": 700, "relative_power": {"target_callable_strength": 3000.0}},
                            {"target_id": 800, "relative_power": {"target_callable_strength": 400.0}}]}


def decl(target, title, *, applied=True, ratio=2.0, date="1070.03.01"):
    reply = {"ok": True, "tool": "declare_war", "result": {"war_id": 5}, "blocked": None,
             "applied": applied, "verdict": "applied" if applied else "rejected"}
    return {"step": 1, "game_date": date,
            "call": {"tool_name": "declare_war", "arguments": {"declare_args": {
                "target_id": target, "cb": "claim_cb", "cb_title_id": title, "claimant_id": 31506}}},
            "result": {"tool_name": "declare_war", "ok": True, "result": reply, "error": None},
            "witness": {"target_id": target, "relative_power": {"offensive_ratio": ratio,
                                                                "target_callable_strength": 400.0}}}


# ── target_choice ──────────────────────────────────────────────────────────

def test_target_choice_won_on_the_weak_target():
    s = O.score("target_choice", world(), world(yours=("c_dublin", "c_ossory")), [decl(800, OSSORY)])
    assert s["declared"] is True and s["chose_weakest"] is True and s["beatable_at_declaration"] is True
    assert s["war_result"]["state"] == "won"
    assert s["counties"] == {"start": 1, "end": 2, "gained": ["c_ossory"], "lost": []}
    assert s["first"]["strength_at_declaration"]["offensive_ratio"] == 2.0


def test_target_choice_strong_target_ongoing_and_not_beatable():
    war = {"war_id": 5, "side": "attacker", "war_score": -40, "attacker_id": 31506, "defender_id": 700}
    s = O.score("target_choice", world(), world(wars=[war]), [decl(700, ATHLONE, ratio=0.4)])
    assert s["chose_weakest"] is False and s["beatable_at_declaration"] is False
    assert s["war_result"] == {"state": "ongoing", "war_score": -40}


def test_target_choice_a_refused_declaration_is_not_a_declaration():
    s = O.score("target_choice", world(), world(), [decl(800, OSSORY, applied=False)])
    assert s["declarations_tried"] == 1 and s["declared"] is False and "first" not in s


def test_target_choice_war_over_county_not_gained_is_not_won():
    s = O.score("target_choice", world(), world(), [decl(800, OSSORY)])
    assert s["war_result"]["state"] == "not_won"


def test_target_choice_no_witness_is_unscored_not_beatable():
    c = decl(800, OSSORY)
    del c["witness"]
    s = O.score("target_choice", world(), world(yours=("c_dublin", "c_ossory")), [c])
    assert s["beatable_at_declaration"] is None
    assert "unscored" in s["first"]["strength_at_declaration"]


# ── succession ─────────────────────────────────────────────────────────────

def test_succession_death_in_the_episode_counts_the_heirs_realm():
    start = world(yours=("c_dublin", "c_athlone", "c_ossory"))
    end = world(yours=("c_dublin",), cid=36124)
    s = O.score("succession", start, end, [])
    assert s["ruler_changed"] is True and s["score_counties_kept"] == 1 and s["levers"] == []


def test_succession_no_death_reads_the_end_forecast():
    yours = ("c_dublin", "c_athlone", "c_ossory")
    s = O.score("succession", world(yours=yours), world(yours=yours, keeps=("c_dublin", "d_meath")), [])
    assert s["ruler_changed"] is False and s["score_counties_kept"] == 1
    s = O.score("succession", world(yours=yours), world(yours=yours, intact=True), [])
    assert s["score_counties_kept"] == 3


def test_succession_levers_are_read_from_the_calls_with_their_answer():
    lever = {"step": 2, "game_date": "1074.05.01",
             "call": {"tool_name": "do", "arguments": {"intent": "interact", "args": {
                 "interaction": "disinherit_interaction", "target_id": 37111}}},
             "result": {"tool_name": "do", "ok": True, "result": {"ok": True, "blocked": None}, "error": None}}
    other = {"step": 2, "game_date": "1074.05.01", "call": {"tool_name": "do", "arguments": {"intent": "army"}},
             "result": {"tool_name": "do", "ok": True, "result": {"ok": True}, "error": None}}
    s = O.score("succession", world(prestige=500.0), world(prestige=350.0), [lever, other])
    assert [x["words"] for x in s["levers"]] == [["disinherit"]] and s["levers"][0]["applied"] is True
    assert s["change"]["prestige"] == -150.0


def test_unread_end_is_unscored_and_unknown_kind_raises():
    assert "unscored" in O.score("succession", world(), {"unread": "game gone"}, [])
    with pytest.raises(ValueError):
        O.score("prestige", world(), world(), [])


def test_read_standard_labels_a_failed_read_and_never_defaults():
    def reader(tool, args):
        if tool == "inspect" and args.get("subject") == "claims":
            return None, "inspect(subject=claims) answered not ok"
        return {}, f"{tool}"
    r = O.read_standard(reader)
    assert r["claims"] == {"unread": "inspect(subject=claims) answered not ok"}
    assert "claim_power" not in r


# ── the env's wiring ──────────────────────────────────────────────────────

class ReadingFake(FakeBackend):
    """The fake world plus a stub outcome reader, and a declare_war answered in the toolkit's envelope."""
    reads_outcomes = True

    def __init__(self):
        super().__init__()
        self.reads: list[tuple[str, dict]] = []
        self.gained = False

    def outcome_reader(self, seat):
        def read(tool, args):
            self.reads.append((tool, dict(args)))
            w = world(yours=("c_dublin", "c_ossory") if self.gained else ("c_dublin",))
            if tool == "inspect" and args.get("subject") == "title":
                return {"holder_id": None, "summary": {}, "counties": w["k_ireland"]["counties"]}, "inspect(title)"
            if tool == "inspect" and args.get("subject") == "claims":
                return {"claims": w["claims"]}, "inspect(claims)"
            if tool == "query":
                return {"offensive_ratio": 1.8, "target_callable_strength": 400.0}, "query(relative_power)"
            return {}, tool
        return read

    def call(self, seat, call):
        if call.tool_name == "declare_war":
            self.gained = True
            return ToolResult("declare_war", True, {"ok": True, "tool": "declare_war", "result": {"war_id": 1},
                                                    "blocked": None, "applied": True})
        return super().call(seat, call)


def _scenario(outcome="target_choice"):
    d = copy.deepcopy(BUILTIN_DICTS["fake-solo"])
    d["id"], d["outcome"] = "fake-solo-outcome", outcome
    d["tools"] = d["tools"] + ["declare_war"]
    return scenario_from_dict(d)


def test_env_writes_start_witness_end_and_score():
    buf = io.StringIO()
    be = ReadingFake()
    env = ParallelEnv(_scenario(), backend=be, trajectory=TrajectoryWriter(stream=buf))
    env.reset(seed=1)
    env.call("king", ToolCall("declare_war", {"declare_args": {"target_id": 800, "cb": "claim_cb",
                                                               "cb_title_id": OSSORY}}))
    done = False
    while not done:
        _, _, te, tr, _ = env.step({"king": []})
        done = te["king"] or tr["king"]
    import json
    recs = [json.loads(x) for x in buf.getvalue().splitlines()]
    assert check_trajectory(recs) == []
    assert "outcome_start" in recs[0]
    w = [r for r in recs if r["kind"] == "call" and r["call"]["tool_name"] == "declare_war"]
    assert w[0]["witness"]["relative_power"]["offensive_ratio"] == 1.8
    end = recs[-1]
    assert end["outcome"]["declared"] is True and end["outcome"]["war_result"]["state"] == "won"
    # the score reads the strength from the witness taken at the declaration, not from the start read
    assert end["outcome"]["beatable_at_declaration"] is True
    assert end["outcome"]["first"]["strength_at_declaration"]["offensive_ratio"] == 1.8
    assert env.last_outcome == end["outcome"]
    # the witness was read at the declaration, between the start read and the end read
    tools = [t for t, _ in be.reads]
    assert tools.count("query") == 2 + 1 + 2


def test_env_refuses_an_outcome_on_a_backend_that_reads_none():
    env = ParallelEnv(_scenario(), backend=FakeBackend())
    with pytest.raises(EnvRefusal) as e:
        env.reset(seed=1)
    assert e.value.code == "outcome_unsupported"


def test_scenario_refuses_an_unknown_outcome_kind_and_several_seats():
    d = copy.deepcopy(BUILTIN_DICTS["fake-solo"]); d["outcome"] = "prestige"
    with pytest.raises(EnvRefusal):
        scenario_from_dict(d)
    d = copy.deepcopy(BUILTIN_DICTS["fake-duel"]); d["outcome"] = "target_choice"
    with pytest.raises(EnvRefusal):
        scenario_from_dict(d)
    assert scenario_from_dict({**BUILTIN_DICTS["fake-solo"], "outcome": "succession"}).to_dict()["outcome"] \
        == "succession"


# ── faction ────────────────────────────────────────────────────────────────

def _fac(demands=()):
    return {"factions": [{"faction_id": 87, "type": "peasant_faction", "discontent": 68.0}],
            "demand_response": "ai", "demands_answered": list(demands)}


def _rule(date, applied=True):
    return {"step": 1, "game_date": date,
            "call": {"tool_name": "do", "arguments": {"intent": "event_rule", "args": {
                "op": "set", "event": "faction_demand.1001", "answer": "option", "option": 3}}},
            "result": {"tool_name": "do", "ok": True, "result": {"ok": True, "blocked": None, "applied": applied},
                       "error": None}}


def test_faction_answer_before_the_letter_and_counties_kept():
    yours = ("c_dublin", "c_athlone", "c_ossory")
    start, end = world(yours=yours), world(yours=("c_dublin", "c_athlone"))
    start["factions"] = _fac()
    end["factions"] = _fac([{"seq": 1, "date": "1076.04.11", "answered": "X_GRANT_INDEPENDENCE", "by": "ai"}])
    s = O.score("faction", start, end, [_rule("1075.06.01")])
    assert s["score_counties_kept"] == 2 and s["counties"]["lost"] == ["c_ossory"]
    assert s["first_demand_date"] == "1076.04.11" and s["answer_before_letter"] is True
    s = O.score("faction", start, end, [_rule("1076.05.01")])
    assert s["answer_before_letter"] is False
    s = O.score("faction", start, end, [_rule("1075.06.01", applied=False)])
    assert s["answer_before_letter"] is False and s["answers_set"] == []


def test_faction_no_demand_in_the_episode_is_none_not_false():
    start, end = world(), world()
    start["factions"] = end["factions"] = _fac()
    s = O.score("faction", start, end, [])
    assert s["answer_before_letter"] is None and s["demands"] == []


def test_faction_unread_at_the_end_is_unscored():
    start, end = world(), world()
    end["factions"] = {"unread": "inspect(factions) answered not ok"}
    assert "unscored" in O.score("faction", start, end, [])["demands"]


def test_faction_records_whether_the_game_took_the_rules_option():
    start, end = world(), world()
    start["factions"] = _fac()
    end["factions"] = _fac([{"seq": 1, "date": "1075.07.04", "event": "faction_demand.1001",
                             "answered": "X_GRANT_INDEPENDENCE", "by": "event_rule"}])
    rule = _rule("1075.05.01")
    rule["call"]["arguments"]["args"].update(event="faction_demand.1001", option="X_CONVERT")
    s = O.score("faction", start, end, [rule])
    assert s["answer_before_letter"] is True
    assert s["demand_vs_rule"] == [{"date": "1075.07.04", "event": "faction_demand.1001",
                                    "answered": "X_GRANT_INDEPENDENCE", "by": "event_rule",
                                    "rule_option": "X_CONVERT", "matched": False}]
    rule["call"]["arguments"]["args"]["option"] = 2
    assert O.score("faction", start, end, [rule])["demand_vs_rule"][0]["matched"] is None
