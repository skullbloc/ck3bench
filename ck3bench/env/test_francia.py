"""The empire goal (empire_held), read the way kingdom_held is (the seat's line holding the target
title), and the 867 Francia task built on it."""
import copy

import pytest

from ck3bench.env.errors import EnvRefusal
from ck3bench.env.rewards import RewardUnavailable, goal_met
from ck3bench.env.scenario import BUILTIN_DICTS, scenario_from_dict
from ck3bench.env.types import METRIC_UNREADABLE, Metrics

ME = 31762


def _m(held=None, status="ok"):
    m = Metrics("0867.01.01", ME)
    if held is not None or status != "ok":
        m.values["holds_target_title"], m.status["holds_target_title"] = held, status
    return m


def test_the_empire_goal_holds_only_when_the_title_is_held():
    assert goal_met("empire_held", _m(False), _m(True)) is True
    assert goal_met("empire_held", _m(False), _m(False)) is False
    with pytest.raises(RewardUnavailable):
        goal_met("empire_held", _m(False), _m(status=METRIC_UNREADABLE))


@pytest.mark.parametrize("goal,title,ok", [("empire_held", "e_france", True), ("empire_held", "k_ireland", False),
                                           ("kingdom_held", "e_france", False), ("kingdom_held", "k_ireland", True)])
def test_each_title_goal_takes_only_its_own_tier(goal, title, ok):
    d = copy.deepcopy(BUILTIN_DICTS["munster1066-kingdom-30y-wake"])
    d.update(goal=goal, target_title=title)
    if ok:
        assert scenario_from_dict(d).goal == goal
    else:
        with pytest.raises(EnvRefusal) as e:
            scenario_from_dict(d)
        assert e.value.code == "scenario_bad_value" and goal in e.value.detail


# ── the 867 task ──
TASKF = "francia867-empire-40y-wake"
BASE = "ireland1066-kingdom-30y-wake"


def test_francia_is_the_wake_task_with_its_own_start_seat_goal_and_horizon():
    from ck3bench.env.scenario import builtin
    a, b = BUILTIN_DICTS[BASE], BUILTIN_DICTS[TASKF]
    assert {k for k in set(a) | set(b) if a.get(k) != b.get(k)} == \
        {"id", "description", "start", "seats", "horizon_days", "goal", "target_title", "status_reason", "notes_set"}
    sc = builtin(TASKF)
    assert sc.start_save == "_867_01_01.ck3" and sc.start_date == "0867.01.01" and sc.end_date == "0907.01.01"
    assert sc.start_save_sha256 == "1a08927d4515ebaeb20f528f56430084348556359606ac54d2ba4b47a82bc5be"
    assert [(s.name, s.character_id) for s in sc.seats] == [("francia", 12015)]
    assert sc.goal == "empire_held" and sc.target_title == "e_france" and sc.n_steps == 200
    assert sc.wake.max_days == 365 and sc.wake.check_days == 7 and sc.cycle_days == 73
    assert dict(sc.reward.terms) == dict(builtin(BASE).reward.terms)
    assert sc.metric_names() == ("dejure_counties", "holds_target_title", "line_alive")
    assert scenario_from_dict(sc.to_dict()) == sc
    # the base task is untouched
    assert BUILTIN_DICTS[BASE]["goal"] == "kingdom_held" and BUILTIN_DICTS[BASE]["target_title"] == "k_ireland"


def test_francia_text_is_public_and_names_its_seat():
    import json
    from ck3bench.env.scenario import builtin
    sc = builtin(TASKF)
    assert "King of West Francia" in sc.description and "e_france" in sc.description
    assert "Ireland" not in sc.description and "Murchad" not in sc.description
    assert "run live with a model" in sc.status_reason and sc.status_reason.count(". Time: ") == 1
    blob = json.dumps(BUILTIN_DICTS[TASKF]).lower()
    for w in ("/home/", "/mnt/", "own" + "er", "no" + "de", "la" + "ne "):
        assert w not in blob, w


def test_francia_refuses_a_start_save_that_is_not_the_declared_one(tmp_path):
    """The declared sha256 guards the start: a file of the same name with other bytes is refused before any launch
    (the backend's host is never asked to run anything)."""
    from ck3bench.env.ck3 import Ck3Backend
    from ck3bench.env.scenario import builtin

    class NoHost:
        def run(self, *a, **k):
            raise AssertionError("launched")
    (tmp_path / "_867_01_01.ck3").write_bytes(b"SAV0 not the 867 start")
    b = Ck3Backend(tmp_path, host=NoHost(), work_dir=tmp_path)
    with pytest.raises(EnvRefusal) as e:
        b.open(builtin(TASKF), 1)
    assert e.value.code == "start_save_sha" or "sha" in e.value.code, e.value.code
    assert "1a08927d" in e.value.detail
