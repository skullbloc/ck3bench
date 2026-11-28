"""The kingdom paid once (goal kingdom_held_once, term target_title_first_gained) and the task that uses it,
munster1066-kingdom-once-30y-wake: holding the title does not end the episode, its +10 is paid once in an episode
(a latch that survives the title's loss and a change of ruler), the outcome is in the end records, and the line
rules still end it. The task it varies, munster1066-kingdom-30y-wake, is unchanged: the replay fixtures
(recorded_munster_rows.json, the step rows of two of its episodes as the env wrote them: test inputs, not results)
re-judge to the rewards and ends they carry."""
import hashlib
import json
from pathlib import Path

import pytest

from ck3bench.env.errors import TERM_GOAL, TERM_LINE_ENDED, TERM_LINE_LOST_DYNASTY, TRUNC_HORIZON, EnvRefusal
from ck3bench.env.replay import rejudge
from ck3bench.env.rewards import ONCE_GOALS, RewardContext, goal_met, spec_from
from ck3bench.env.scenario import (BUILTIN_DICTS, TITLE_ENDS, TITLE_ONCE, builtin, scenario_from_dict,
                                   title_rule)
from ck3bench.env.types import Metrics

NEW, OLD = "munster1066-kingdom-once-30y-wake", "munster1066-kingdom-30y-wake"
ROWS = json.loads((Path(__file__).with_name("recorded_munster_rows.json")).read_text(encoding="utf-8"))
RECORDED = ("munster_1086", "munster_1096")
#: munster1066-kingdom-30y-wake's declaration as it was before the once task was added: sha256 of its sorted JSON
#: without `status_reason` (the task's prose on what has run of it, which changes as it runs; everything an episode
#: reads is pinned)
OLD_DECL_SHA = "4c3a00f0b0b9e217567d26d54843ceea88d8326835536cb52773c0afe4bef181"


def _decl_sha(d):
    """Without its prose on what has run and its notes set (the old task declares the Ireland set since the toolkit's
    default set became general advice: the notes it was served before, under that set's name)."""
    d = {k: v for k, v in d.items() if k not in ("status_reason", "notes_set")}
    return hashlib.sha256(json.dumps(d, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


# ── the task as declared ──────────────────────────────────────────────────
def test_the_old_task_declaration_is_unchanged():
    assert _decl_sha(BUILTIN_DICTS[OLD]) == OLD_DECL_SHA
    assert builtin(OLD).goal == "kingdom_held" and dict(builtin(OLD).reward.terms)["target_title_gained"] == 10


def test_the_new_task_is_the_old_one_with_the_reward_changed_only():
    old, new = BUILTIN_DICTS[OLD], BUILTIN_DICTS[NEW]
    differ = sorted(k for k in set(old) | set(new) if old.get(k) != new.get(k))
    assert differ == ["description", "goal", "id", "notes_set", "reward", "status_reason"]
    sc = builtin(NEW)
    assert sc.goal == "kingdom_held_once" and sc.goal in ONCE_GOALS and sc.target_title == "k_ireland"
    assert dict(sc.reward.terms) == {"dejure_counties_delta": 1.0, "target_title_first_gained": 10.0,
                                     "line_survival": 5.0}
    assert sc.notes_set == "munster1066once" and sc.end_date == "1096.09.15"


def test_the_new_task_text_states_the_reward_truthfully():
    d = builtin(NEW).description
    for words in ("+1 for each de jure Irish county gained", "+10 once", "never again if it is lost and regained",
                  "does not end the episode", "play goes on to the end date (1096.09.15)", "-5 if the line ends",
                  "another dynasty, the episode ends there as a loss"):
        assert words in d, words
    assert "ends for the seat on the first step" not in d and TITLE_ENDS not in d
    assert title_rule(builtin(NEW)) == TITLE_ONCE and title_rule(builtin(OLD)) == TITLE_ENDS
    assert title_rule(builtin("lorraine1066-line-25y-wake")) is None


@pytest.mark.parametrize("change, why", [
    ({"reward": {"terms": [{"name": "target_title_gained", "weight": 10.0}], "team": "none"}}, "pays it again"),
    ({"goal": "kingdom_held"}, "is the reward of a once goal"),
    ({"target_title": "d_munster"}, "needs a kingdom target_title"),
])
def test_a_once_goal_declared_wrong_is_refused(change, why):
    with pytest.raises(EnvRefusal) as e:
        scenario_from_dict({**BUILTIN_DICTS[NEW], **change})
    assert why in e.value.detail


def test_control_the_new_declaration_itself_passes_the_reader():
    assert scenario_from_dict(dict(BUILTIN_DICTS[NEW])).id == NEW


# ── the term and the goal predicate ───────────────────────────────────────
def _m(held, cid=1):
    return Metrics("1070.01.01", cid, values={"holds_target_title": held}, status={"holds_target_title": "ok"})


def test_the_term_pays_on_the_first_holding_only():
    spec = spec_from([("target_title_first_gained", 10.0)])
    pay = lambda prev, cur, before: spec.seat_reward(RewardContext(_m(False), _m(prev), _m(cur), False, False,
                                                                   title_held_before=before))[0]
    assert pay(False, True, False) == 10.0
    assert pay(False, True, True) == 0.0            # regained: the latch is set
    assert pay(True, True, True) == 0.0 and pay(False, False, False) == 0.0
    # control: the old term pays the regain (why the once task does not use it)
    old = spec_from([("target_title_gained", 10.0)])
    assert old.seat_reward(RewardContext(_m(False), _m(False), _m(True), False, False, title_held_before=True))[0] == 10


def test_the_once_goal_reads_the_latch_and_refuses_without_it():
    assert goal_met("kingdom_held_once", _m(False), _m(False), held_before=True) is True
    assert goal_met("kingdom_held_once", _m(False), _m(True), held_before=False) is True
    assert goal_met("kingdom_held_once", _m(False), _m(False), held_before=False) is False
    with pytest.raises(EnvRefusal):
        goal_met("kingdom_held_once", _m(False), _m(True))


# ── the env, on scripted reads (replay.ReplayBackend) ─────────────────────
def _row(step, date, held, cid=31762, dyn=2044, line="following", alive=True, dejure=3):
    return {"step": step, "game_date": date, "metrics": {
        "game_date": date, "character_id": cid, "dynasty_id": dyn, "dynasty_source": "test",
        "line_status": line, "values": {"dejure_counties": dejure, "holds_target_title": held, "line_alive": alive},
        "status": {"dejure_counties": "ok", "holds_target_title": "ok", "line_alive": "ok"}, "source": {}}}


def _rows(steps):
    start = _row(0, "1066.09.15", False)["metrics"]
    return {"task": NEW, "seat": "munster", "character_id": 31762, "start_date": "1066.09.15",
            "start_metrics": start, "steps": steps}


def test_lose_and_regain_pays_once_and_the_episode_runs_on():
    out = rejudge(_rows([_row(1, "1070.01.01", False, dejure=8), _row(2, "1071.01.01", True, dejure=8),
                         _row(3, "1072.01.01", False, dejure=7), _row(4, "1073.01.01", True, dejure=8)]), NEW)
    assert [s["reward"] for s in out["steps"]] == [5.0, 10.0, -1.0, 1.0]
    assert [s["reward_terms"]["target_title_first_gained"] for s in out["steps"]] == [0, 10, 0, 0]
    assert all(s["termination_reason"] is None and s["truncation_reason"] is None for s in out["steps"])
    assert out["live_after_last_step"] is True
    firsts = [s for s in out["steps"] if "target_title_first_held" in s]
    assert len(firsts) == 1 and firsts[0]["step"] == 2 and firsts[0]["target_title_first_held"]["date"] == "1071.01.01"
    tt = out["end_record"]["target_title"]["munster"]
    assert tt["held_at_any_step"] is True and tt["first_held"]["step"] == 2 and tt["held_at_last_step"] is True
    assert tt["horizon_reached"] is False


def test_an_heir_regaining_is_not_paid_again():
    out = rejudge(_rows([_row(1, "1070.01.01", True), _row(2, "1075.01.01", False, cid=40001),
                         _row(3, "1076.01.01", True, cid=40001)]), NEW)
    assert [s["reward_terms"]["target_title_first_gained"] for s in out["steps"]] == [10, 0, 0]
    assert out["end_record"]["target_title"]["munster"]["first_held"]["character_id"] == 31762


def test_an_heir_who_first_holds_it_is_paid_once():
    out = rejudge(_rows([_row(1, "1070.01.01", False), _row(2, "1075.01.01", False, cid=40001),
                         _row(3, "1076.01.01", True, cid=40001), _row(4, "1077.01.01", True, cid=40001)]), NEW)
    assert [s["reward_terms"]["target_title_first_gained"] for s in out["steps"]] == [0, 0, 10, 0]


def test_at_the_end_date_the_goal_is_met_on_the_latch_with_the_outcome_in_both_end_records():
    out = rejudge(_rows([_row(1, "1080.01.01", True), _row(2, "1090.01.01", False),
                         _row(3, "1096.09.15", False)]), NEW)
    last = out["steps"][-1]
    assert last["termination_reason"] == TERM_GOAL and out["live_after_last_step"] is False
    assert last["end"]["target_title"]["held_at_any_step"] is True
    assert last["end"]["target_title"]["held_at_end"] is False and last["end"]["target_title"]["horizon_reached"]
    assert "first on 1080.01.01" in last["end"]["why"]
    tt = out["end_record"]["target_title"]["munster"]
    assert tt == {**tt, "held_at_any_step": True, "held_at_last_step": False, "horizon_reached": True}
    assert tt["first_held"]["date"] == "1080.01.01"


def test_at_the_end_date_never_held_is_the_horizon_with_the_outcome_recorded():
    out = rejudge(_rows([_row(1, "1080.01.01", False), _row(2, "1096.09.15", False)]), NEW)
    last = out["steps"][-1]
    assert last["truncation_reason"] == TRUNC_HORIZON and last["termination_reason"] is None
    assert last["end"]["target_title"] == {**last["end"]["target_title"], "held_at_any_step": False,
                                           "first_held": None}
    assert out["end_record"]["target_title"]["munster"]["held_at_any_step"] is False


def test_the_line_lost_to_another_dynasty_still_ends_the_episode_and_keeps_the_latch():
    out = rejudge(_rows([_row(1, "1070.01.01", True), _row(2, "1075.01.01", True, cid=50001, dyn=999)]), NEW)
    last = out["steps"][-1]
    assert last["termination_reason"] == TERM_LINE_LOST_DYNASTY and out["live_after_last_step"] is False
    assert last["reward_terms"]["target_title_first_gained"] == 0       # the other dynasty's holding is never paid
    assert last["end"]["target_title"]["held_at_any_step"] is True
    assert last["end"]["target_title"]["held_at_end"] is False           # scored as the line's loss: nothing held


def test_a_line_that_ends_still_ends_the_episode():
    out = rejudge(_rows([_row(1, "1070.01.01", False), _row(2, "1072.01.01", False, line="line_ended",
                                                           alive=False, dejure=0)]), NEW)
    assert out["steps"][-1]["termination_reason"] == TERM_LINE_ENDED
    assert out["end_record"]["target_title"]["munster"]["held_at_any_step"] is False


def test_after_the_first_holding_the_goal_line_says_paid_once_and_play_goes_on():
    from ck3bench.env.env import ParallelEnv
    from ck3bench.env.replay import ReplayBackend
    env = ParallelEnv(NEW, backend=ReplayBackend(_rows([_row(1, "1070.01.01", False), _row(2, "1071.01.01", True),
                                                        _row(3, "1072.01.01", False)])))
    obs, _ = env.reset(seed=1)
    assert "goal" not in obs["munster"]["world"]
    obs, *_ = env.step({"munster": []})
    assert "goal" not in obs["munster"]["world"]
    obs, *_ = env.step({"munster": []})
    g = obs["munster"]["world"]["goal"]
    assert "first held on 1071.01.01" in g and "paid then, once" in g and "Play goes on" in g and "holds it now" in g
    assert "end" not in g.replace("end date", "")
    obs, *_ = env.step({"munster": []})
    assert "does not hold it now" in obs["munster"]["world"]["goal"]


def test_control_the_old_task_ends_at_the_first_holding_on_the_same_reads():
    out = rejudge(_rows([_row(1, "1070.01.01", False), _row(2, "1071.01.01", True), _row(3, "1072.01.01", False)]),
                  OLD)
    assert len(out["steps"]) == 2 and out["steps"][-1]["termination_reason"] == TERM_GOAL
    assert "target_title" not in out["end_record"] and "target_title" not in out["steps"][-1]["end"]


# ── the replay fixtures: two episodes' step rows of the old task ──────────
@pytest.mark.parametrize("run", RECORDED)
def test_recorded_runs_rejudge_under_the_old_task_exactly_as_recorded(run):
    rows = ROWS[run]
    out = rejudge(rows, OLD)
    assert [s["reward"] for s in out["steps"]] == [s["reward"] for s in rows["steps"]]
    assert [s["reward_terms"] for s in out["steps"]] == [s["reward_terms"] for s in rows["steps"]]
    assert [s["termination_reason"] for s in out["steps"]] == [s["termination_reason"] for s in rows["steps"]]
    assert [s["truncation_reason"] for s in out["steps"]] == [s["truncation_reason"] for s in rows["steps"]]
    assert out["return"] == rows["end"]["returns"]["munster"] and len(out["steps"]) == rows["end"]["steps"]
    assert out["live_after_last_step"] is False


@pytest.mark.parametrize("run, step, date, by_order", [("munster_1086", 55, "1086.03.14", False),
                                                       ("munster_1096", 45, "1096.02.16", True)])
def test_recorded_runs_under_the_new_task_pay_the_same_and_play_on(run, step, date, by_order):
    rows = ROWS[run]
    out = rejudge(rows, NEW)
    assert out["return"] == rows["end"]["returns"]["munster"] == 16.0
    assert all(s["termination_reason"] is None and s["truncation_reason"] is None for s in out["steps"])
    assert out["live_after_last_step"] is True                  # the episode would have continued
    first = [s for s in out["steps"] if "target_title_first_held" in s]
    assert [(s["step"], s["game_date"]) for s in first] == [(step, date)]
    calls = first[0]["target_title_first_held"]["your_calls_naming_it"]
    assert ("order_id" in calls[-1]) is by_order
