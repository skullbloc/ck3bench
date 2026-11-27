"""The target-title task (`ireland1066-kingdom-15y`): its declaration, its
reward terms and goal, a whole episode on the fake backend, and the real
backend's two target reads on the public tools' recorded live replies
(`recorded_ireland1066.json`) and on replies built from them."""
from __future__ import annotations

import copy
import json
import math
from pathlib import Path

import pytest

from ck3bench.env import ParallelEnv, ToolCall, contract
from ck3bench.env.errors import TERM_GOAL, TRUNC_METRIC_UNREADABLE, EnvRefusal
from ck3bench.env.rewards import RewardContext, RewardUnavailable, goal_met, spec_from
from ck3bench.env.scenario import BUILTIN_DICTS, builtin, scenario_from_dict
from ck3bench.env.types import METRIC_OK, METRIC_UNREADABLE, Metrics

from .test_ck3_backend import FakeBridge, FakeTransport, _backend

RECORDED = json.loads((Path(__file__).with_name("recorded_ireland1066.json")).read_text(encoding="utf-8"))
ME = 31506


def _rec(subject):
    for r in RECORDED["replies"]:
        if r["args"]["subject"] == subject:
            return copy.deepcopy(r["reply"])
    raise KeyError(subject)


# ── the declaration ─────────────────────────────────────────────────────────

def test_the_ireland_task_is_declared_as_asked():
    sc = builtin("ireland1066-kingdom-15y")
    assert (sc.start_date, sc.end_date, sc.n_steps, sc.cycle_days) == ("1066.09.15", "1081.09.15", 75, 73)
    assert [(s.name, s.character_id) for s in sc.seats] == [("dublin", ME)]
    assert sc.target_title == "k_ireland" and sc.goal == "kingdom_held" and sc.game_speed == 3
    assert dict(sc.reward.terms) == {"dejure_counties_delta": 1.0, "target_title_gained": 10.0, "line_survival": 5.0}
    assert sc.metric_names() == ("dejure_counties", "holds_target_title", "line_alive")
    assert scenario_from_dict(sc.to_dict()) == sc


def _ire(**over):
    d = copy.deepcopy(BUILTIN_DICTS["ireland1066-kingdom-15y"])
    d.update(over)
    return d


@pytest.mark.parametrize("change,code", [
    (lambda d: d.pop("target_title"), "scenario_missing_key"),
    (lambda d: d.update(target_title="Ireland"), "scenario_bad_value"),
    (lambda d: d.update(target_title="d_meath"), "scenario_bad_value"),      # kingdom_held needs a kingdom
    (lambda d: d.update(goal="none", reward={"terms": [{"name": "gold_delta", "weight": 1.0}]}),
     "scenario_bad_value"),                                                  # declared, nothing reads it
])
def test_a_target_title_is_declared_exactly_when_something_reads_it(change, code):
    d = _ire()
    change(d)
    with pytest.raises(EnvRefusal) as e:
        scenario_from_dict(d)
    assert e.value.code == code


def test_a_goal_alone_makes_its_metric_read():
    d = _ire(reward={"terms": [{"name": "gold_delta", "weight": 1.0}], "team": "none"})
    assert scenario_from_dict(d).metric_names() == ("gold", "holds_target_title")


# ── the reward terms and the goal ──────────────────────────────────────────

def _m(held=None, dejure=None, status="ok"):
    m = Metrics("1066.09.15", ME)
    for name, v in (("holds_target_title", held), ("dejure_counties", dejure)):
        if v is not None or status != "ok":
            m.values[name], m.status[name] = v, status
    return m


def test_the_title_bonus_pays_once_on_the_step_the_title_is_gained():
    spec = spec_from([("target_title_gained", 10.0)])
    pay = lambda prev, cur: spec.seat_reward(RewardContext(_m(False), _m(prev), _m(cur), False, False))[0]
    assert (pay(False, True), pay(True, True), pay(False, False), pay(True, False)) == (10.0, 0.0, 0.0, 0.0)


def test_the_dejure_delta_is_the_change_in_held_counties():
    spec = spec_from([("dejure_counties_delta", 1.0)])
    r = spec.seat_reward(RewardContext(_m(dejure=1), _m(dejure=1), _m(dejure=3), False, False))[0]
    assert r == 2.0


def test_the_kingdom_goal_holds_only_when_the_title_is_held():
    assert goal_met("kingdom_held", _m(False), _m(True)) is True
    assert goal_met("kingdom_held", _m(False), _m(False)) is False


@pytest.mark.parametrize("cur", [_m(status=METRIC_UNREADABLE), Metrics("1066.09.15", ME)])
def test_an_unread_title_is_no_answer(cur):
    with pytest.raises(RewardUnavailable):
        goal_met("kingdom_held", _m(False), cur)
    with pytest.raises(RewardUnavailable):
        spec_from([("target_title_gained", 1.0)]).seat_reward(RewardContext(_m(False), _m(False), cur, False, False))


# ── a whole episode on the fake backend ────────────────────────────────────

def _conqueror(env, obs):
    """Wars on the neighbours until more than half the de jure land is held, then the title."""
    acts = {}
    for s in env.agents:
        if s != "count":
            acts[s] = []
            continue
        view = env.call(s, ToolCall("observe")).result
        tt = view["target_title"]
        if 2 * tt["dejure_held"] > tt["dejure_total"] and view["gold"] >= 60:
            acts[s] = [ToolCall("do", {"intent": "mint_title", "args": {"title_key": tt["key"]}})]
        else:
            acts[s] = [ToolCall("declare_war", {"target_id": n}) for n in list(view["neighbours"])[:1]]
    return acts


def test_a_count_that_conquers_reaches_the_kingdom_and_terminates_goal_reached():
    for seed in range(40):
        env = ParallelEnv("fake-kingdom")
        out = contract.drive(env, seed=seed, actions_for=_conqueror)
        last = out["last_infos"].get("count") or {}
        if last.get("termination_reason") == TERM_GOAL:
            break
    else:
        pytest.fail("no seed of 40 reached the kingdom on the fake map")
    total = sum(r["count"] for r in out["rewards"] if "count" in r)
    m = env.state()["metrics"]["count"]
    assert m["values"]["holds_target_title"] is True
    # the return is the de jure gain plus the bonus (the line lived)
    assert total == pytest.approx((m["values"]["dejure_counties"] - 1) + 10.0)


def test_the_noop_count_never_reaches_the_goal_on_the_fake_map():
    env = ParallelEnv("fake-kingdom")
    out = contract.drive(env, seed=3)
    assert out["end_reason"] != TERM_GOAL
    assert all(not i.get("termination_reason") == TERM_GOAL for i in out["last_infos"].values())


def test_mint_title_is_refused_short_of_a_majority_on_the_fake_map():
    env = ParallelEnv("fake-kingdom")
    env.reset(seed=1)
    r = env.call("count", ToolCall("do", {"intent": "mint_title", "args": {"title_key": "k_fake"}}))
    assert not r.ok and r.error.startswith("insufficient_de_jure_control")


# ── the real backend, on recorded replies ──────────────────────────────────

class RecordedTransport(FakeTransport):
    """Answers `inspect` from the recorded live replies (title, title_hierarchy),
    after an optional edit; every other tool as FakeTransport does."""

    def __init__(self, edit=None):
        super().__init__()
        self.edit = edit or (lambda subject, reply: reply)

    def call(self, tool_name, arguments):
        self.calls.append((tool_name, dict(arguments)))
        if tool_name == "inspect" and arguments.get("subject") in ("title", "title_hierarchy"):
            return self.edit(arguments["subject"], _rec(arguments["subject"]))
        return {"ok": True, "tool": tool_name}


def _make(tmp_path, edit=None, line_char=ME):
    br = FakeBridge(speed=0)
    br.watch = {"status": "following", "line_character_id": line_char}
    br.profile = {**br.profile, "id": line_char}
    br.realm = {line_char: 1}
    trs: dict = {}
    b, _, _, _ = _backend(tmp_path, bridge=br)
    b.transport_factory = lambda seat, cid, env: trs.setdefault(seat, RecordedTransport(edit))
    return b, br, trs


def _open(tmp_path, edit=None, line_char=ME):
    b, br, trs = _make(tmp_path, edit, line_char)
    b.open(builtin("ireland1066-kingdom-15y"), seed=1)
    return b, br, trs["dublin"]


def test_the_recorded_start_reads_none_of_ireland_held_and_no_king(tmp_path):
    b, _, tr = _open(tmp_path)
    m = b.metrics("dublin", builtin("ireland1066-kingdom-15y").metric_names())
    assert m.get("dejure_counties") == (0, METRIC_OK)
    assert m.get("holds_target_title") == (False, METRIC_OK)
    assert "summary.held (of 14)" in m.source["dejure_counties"]
    assert "the title has no holder" in m.source["holds_target_title"]
    assert ("inspect", {"subject": "title", "title_key": "k_ireland", "character_id": ME}) in tr.calls
    assert ("inspect", {"subject": "title_hierarchy", "title_id": 461, "character_id": ME}) in tr.calls


def _held_by(holder, held=9):
    def edit(subject, reply):
        if subject == "title":
            reply["result"]["summary"]["held"] = held
        else:
            reply["result"]["title"]["holder_id"] = holder
        return reply
    return edit


def test_a_king_of_ireland_in_the_line_reads_held_and_counts_his_share(tmp_path):
    b, *_ = _open(tmp_path, edit=_held_by(ME))
    m = b.metrics("dublin", ["dejure_counties", "holds_target_title"])
    assert m.get("dejure_counties") == (9, METRIC_OK) and m.get("holds_target_title") == (True, METRIC_OK)


def test_another_king_is_not_the_seats_kingdom(tmp_path):
    b, *_ = _open(tmp_path, edit=_held_by(29703))
    assert b.metrics("dublin", ["holds_target_title"]).get("holds_target_title") == (False, METRIC_OK)


def test_the_reads_follow_the_line_to_the_heir(tmp_path):
    heir = 36124
    b, _, tr = _open(tmp_path, edit=_held_by(heir), line_char=heir)
    m = b.metrics("dublin", ["dejure_counties", "holds_target_title"])
    assert m.character_id == heir and m.get("holds_target_title") == (True, METRIC_OK)
    assert all(a.get("character_id") == heir for t, a in tr.calls if t == "inspect")


def _broken(what):
    def edit(subject, reply):
        if what == "title_not_ok" and subject == "title":
            return {"ok": False, "tool": "inspect", "blocked": {"gate": "title_key_not_found"}}
        if what == "empty_tree" and subject == "title":
            reply["result"]["summary"].update(held=0, total=0)
        if what == "other_title" and subject == "title_hierarchy":
            reply["result"]["title"]["key"] = "k_england"
        if what == "hierarchy_not_ok" and subject == "title_hierarchy":
            return {"ok": False, "tool": "inspect", "blocked": {"gate": "title_not_found"}}
        return reply
    return edit


@pytest.mark.parametrize("what,unread", [
    ("title_not_ok", {"dejure_counties", "holds_target_title"}),   # no title id either
    ("empty_tree", {"dejure_counties"}),                           # the bridge's titular_or_transient
    ("other_title", {"holds_target_title"}),
    ("hierarchy_not_ok", {"holds_target_title"}),
])
def test_a_read_that_does_not_answer_is_unreadable_never_a_number(tmp_path, what, unread):
    b, *_ = _open(tmp_path, edit=_broken(what))
    m = b.metrics("dublin", ["dejure_counties", "holds_target_title"])
    assert {n for n, st in m.status.items() if st == METRIC_UNREADABLE} == unread
    for n in unread:
        assert m.values.get(n) is None and m.source[n]


def test_an_unreadable_target_truncates_the_episode_with_a_nan_reward(tmp_path):
    b, *_ = _make(tmp_path, edit=_broken("empty_tree"))
    env = ParallelEnv(builtin("ireland1066-kingdom-15y"), backend=b)
    env.reset(seed=1)
    _, rew, _, trunc, info = env.step({"dublin": []})
    assert math.isnan(rew["dublin"]) and trunc["dublin"] and info["dublin"]["truncation_reason"] == TRUNC_METRIC_UNREADABLE


def test_an_ended_line_reads_no_ireland_and_makes_no_target_call(tmp_path):
    b, br, tr = _open(tmp_path)
    br.watch = {"status": "line_ended", "line_character_id": ME}
    n = len(tr.calls)
    m = b.metrics("dublin", ["dejure_counties", "holds_target_title"])
    assert m.get("dejure_counties") == (0, METRIC_OK) and m.get("holds_target_title") == (False, METRIC_OK)
    assert len(tr.calls) == n
