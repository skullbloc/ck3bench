"""A line that passes to another dynasty ends the episode as a failure.

The rule: when the seat's titles pass to a ruler of another dynasty (a conquest, a lost house-eradication war), the
seat's dynasty has lost the line. The episode ends there, terminated `line_lost_to_other_dynasty`, scored as a line
that ended is (`line_survival`'s penalty; the realm, the de jure counties and the title read 0 / False; gold and the
line's counts carried from before the step), and nothing that other ruler does or holds is scored: no goal of any
kind is met by him. A ruler change whose dynasty (or the seat's, read at open) did not read truncates the episode
`line_dynasty_unread` with a NaN reward, never a match and never a loss.

The dynasty compared is the DYNASTY, not the house: the bridge's query_character `dynasty_id` is the house's dynasty
validated against the dynasty database, so a cadet house of the seat's dynasty is the seat's dynasty.

These tests use stand-in backends only; no recorded game is replayed here.
"""
from __future__ import annotations

import io
import json
import math

import pytest

from ck3bench.env import ParallelEnv, contract
from ck3bench.env.errors import (TERM_GOAL, TERM_LINE_LOST_DYNASTY, TERMINATION_REASONS, TRUNC_HORIZON,
                                 TRUNC_LINE_DYNASTY_UNREAD, TRUNCATION_REASONS)
from ck3bench.env.fake import FakeBackend
from ck3bench.env.rewards import GOAL_METRICS
from ck3bench.env.scenario import BUILTIN_DICTS, scenario_from_dict
from ck3bench.env.trajectory import TrajectoryWriter

OTHER = 9001          # the other dynasty's ruler


class Conquest(FakeBackend):
    """The fake world, and at advance `at` the seat's line passes to ruler `who` of dynasty `dynasty` (the seat's
    own: a same-dynasty succession; another: a conquest; 0: a dynasty that does not read), who brings `gain`
    counties (de jure too), the target title when `title`, and `passes` more ruler changes on the line's watch."""

    def __init__(self, at: int, seat: str = "count", dynasty=None, who: int = OTHER, gain: int = 3,
                 title: bool = True, passes: int = 2, start_dynasty: int | None = None):
        super().__init__()
        self.at, self.seat, self.dyn, self.who = at, seat, dynasty, who
        self.gain, self.title, self.passes, self.start_dynasty = gain, title, passes, start_dynasty

    def open(self, scenario, seed):
        out = super().open(scenario, seed)
        if self.start_dynasty is not None:
            self.realms[self.seat].dynasty = self.start_dynasty
        return out

    def _pass(self):
        r = self.realms[self.seat]
        if r.line == "line_ended":
            return
        r.character_id = self.who
        r.dynasty = getattr(r, "dynasty", 0) if self.dyn == "same" else self.dyn
        r.counties += self.gain
        if self.target is not None:
            r.dejure = min(self.dejure_total, r.dejure + self.gain)
            if self.title:
                self.title_holder = self.seat
        r.successions += self.passes

    def advance(self, days):
        adv = super().advance(days)
        if self.advances == self.at:
            self._pass()
        return adv


def _sc(base: str = "fake-kingdom", **over):
    return scenario_from_dict({**BUILTIN_DICTS[base], **over})


def _run(env, seed=3):
    """contract.drive, keeping each seat's own last step: the step it ended at, its info and its reward."""
    out = env.reset(seed=seed)
    contract.check_reset(env, out)
    ended, last, reward = {}, {}, {}
    n = 0
    while env.agents:
        live = list(env.agents)
        res = env.step({s: [] for s in live})
        contract.check_step(env, live, res)
        n += 1
        for s in live:
            ended[s], last[s], reward[s] = n, res[4][s], res[1][s]
    return {"ended": ended, "last_infos": last, "reward": reward, "end_reason": env.end_reason}


# ── the rule, on the fake world ─────────────────────────────────────────────

def test_a_conquest_by_another_dynasty_ends_the_episode_as_a_failure_and_his_title_is_not_scored():
    buf = io.StringIO()
    env = ParallelEnv(_sc(), backend=Conquest(at=2, dynasty=77), trajectory=TrajectoryWriter(stream=buf))
    out = _run(env)
    recs = [json.loads(l) for l in buf.getvalue().splitlines()]
    steps = [r for r in recs if r["kind"] == "step"]
    last = next(r for r in steps if "count" in r["terminations"] and r["terminations"]["count"])
    assert last["step"] == 2, "the episode ends on the step the line passed"
    info = last["infos"]["count"]
    assert info["termination_reason"] == TERM_LINE_LOST_DYNASTY
    end = info["end"]
    assert end["reason"] == TERM_LINE_LOST_DYNASTY and end["ruler_id"] == OTHER and end["dynasty_id"] == 77
    assert end["your_dynasty_id"] == 1 and "nothing that ruler does is scored" in end["why"]
    # the failure reward: line_survival's penalty, the de jure land lost, no title (he holds k_fake in the world)
    parts = info["reward_terms"]
    assert parts["line_survival"] == -5.0 and parts["target_title_gained"] == 0.0
    assert parts["dejure_counties_delta"] <= 0
    assert env.backend.title_holder == "count", "the other dynasty's ruler does hold the title in the world"
    assert end["final"]["character_id"] == OTHER and end["final"]["scored"]["holds_target_title"] is False
    # the raw read is recorded beside what was scored
    assert last["metrics"]["count"]["dynasty_id"] == 77 and last["metrics"]["count"]["values"]["holds_target_title"]
    assert info["line_dynasty"]["verdict"] == "other_dynasty"
    # the header carries the seat's dynasty as read at open
    assert recs[0]["start_metrics"]["count"]["dynasty_id"] == 1
    # nothing after: the seat left agents at that step
    assert all("count" not in r["terminations"] for r in steps if r["step"] > 2)


@pytest.mark.parametrize("who", ["son", "cadet"])
def test_CONTROL_a_same_dynasty_succession_continues_and_its_title_is_scored(who):
    """A son, a brother or a cadet house: the dynasty id is the same, so the line goes on and the goal is met."""
    env = ParallelEnv(_sc(), backend=Conquest(at=2, dynasty="same", who=OTHER if who == "son" else OTHER + 1))
    out = _run(env)
    reasons = [i.get("termination_reason") for i in [out["last_infos"].get("count", {})]]
    assert reasons == [TERM_GOAL], "the same dynasty's ruler holding the title meets the goal"


# ── every goal kind goes through it (enumerated from rewards.GOAL_METRICS) ──
_GOAL_CASES = {
    "kingdom_held": dict(),
    "empire_held": dict(target_title="e_fake"),
    "county_gain": dict(goal="county_gain", target_title=None,
                        reward={"terms": [{"name": "county_gain_goal", "weight": 1.0},
                                          {"name": "line_survival", "weight": 5.0}], "team": "none"}),
    "line_kept_peacefully": dict(goal="line_kept_peacefully", target_title=None,
                                 reward={"terms": [{"name": "realm_counties_delta", "weight": 1.0},
                                                   {"name": "line_survival", "weight": 5.0}], "team": "none"}),
    "kingdom_held_once": dict(reward={"terms": [{"name": "dejure_counties_delta", "weight": 1.0},
                                                {"name": "target_title_first_gained", "weight": 10.0},
                                                {"name": "line_survival", "weight": 5.0}], "team": "none"}),
    "none": dict(goal="none", target_title=None,
                 reward={"terms": [{"name": "realm_counties_delta", "weight": 1.0},
                                   {"name": "line_survival", "weight": 5.0}], "team": "none"}),
}


def test_every_goal_kind_has_a_case():
    assert set(_GOAL_CASES) == set(GOAL_METRICS), "a goal kind with no case here is not proven to end at the loss"


def _goal_sc(goal):
    over = dict(_GOAL_CASES[goal])
    if "goal" not in over:
        over["goal"] = goal
    d = {**BUILTIN_DICTS["fake-kingdom"], **over}
    if d.get("target_title") is None:
        d.pop("target_title")
        d["fake"] = {k: v for k, v in d["fake"].items() if k not in ("dejure", "dejure_total")}
    return scenario_from_dict(d)


@pytest.mark.parametrize("goal", sorted(_GOAL_CASES))
@pytest.mark.parametrize("dyn", ["same", 77])
def test_each_goal_kind_ends_at_the_loss_and_is_met_only_by_the_seats_dynasty(goal, dyn):
    sc = _goal_sc(goal)
    # a horizon goal is judged at the end date, and so is a once goal (holding the title does not end it): the line
    # passes on the last step
    at = sc.n_steps if goal in ("line_kept_peacefully", "kingdom_held_once") else 2
    env = ParallelEnv(sc, backend=Conquest(at=at, dynasty=dyn))
    out = _run(env)
    info = out["last_infos"]["count"]
    if dyn == 77:
        assert info.get("termination_reason") == TERM_LINE_LOST_DYNASTY, info
        assert out["ended"]["count"] == at
        assert info["reward_terms"]["line_survival"] == -5.0
    elif goal == "none":
        assert info.get("truncation_reason") == TRUNC_HORIZON
    else:
        assert info.get("termination_reason") == TERM_GOAL, info
        assert out["ended"]["count"] == at


# ── unread: refused, never a match, never a loss ─────────────────────────────

def test_a_ruler_change_whose_dynasty_did_not_read_truncates_unscored():
    env = ParallelEnv(_sc(), backend=Conquest(at=2, dynasty=0))
    out = _run(env)
    info = out["last_infos"]["count"]
    assert out["ended"] == {"count": 2, "rival": 2} and info["truncation_reason"] == TRUNC_LINE_DYNASTY_UNREAD
    assert math.isnan(out["reward"]["count"]) and math.isnan(out["reward"]["rival"])
    assert info["line_dynasty"]["verdict"] == "unread" and "cannot be judged" in info["end"]["why"]
    assert out["end_reason"] == TRUNC_LINE_DYNASTY_UNREAD


def test_the_seats_dynasty_unread_at_open_truncates_at_the_first_ruler_change_and_not_before():
    env = ParallelEnv(_sc(), backend=Conquest(at=3, dynasty="same", start_dynasty=0))
    out = _run(env)
    assert out["ended"]["count"] == 3 and out["last_infos"]["count"]["truncation_reason"] == TRUNC_LINE_DYNASTY_UNREAD


def test_the_reasons_are_typed():
    assert TERM_LINE_LOST_DYNASTY in TERMINATION_REASONS and TRUNC_LINE_DYNASTY_UNREAD in TRUNCATION_REASONS


def test_the_seat_is_told_why_in_its_episode_end_reply():
    from ck3bench.env.mcp_shim import SeatShim
    from ck3bench.env.server import EnvClient, EnvServer
    solo = _sc(seats=[{"name": "count", "character_id": 1}],
               fake={"counties": [1], "gold": [100.0], "prestige": [50.0], "ages": [30], "dejure": [1],
                     "dejure_total": 7})
    env = ParallelEnv(solo, backend=Conquest(at=1, dynasty=77))
    srv = EnvServer(env, turn_timeout_s=30).start()
    cl = EnvClient(srv.url, timeout=30)
    cl.reset(seed=3)
    try:
        out, err = SeatShim(cl, "count", 30, ack=True).call("end_turn", {"summary": "pass"})
        assert not err and out["episode_over"]
        assert out["your_episode_ended"]["reason"] == TERM_LINE_LOST_DYNASTY
        assert "your dynasty lost the line" in out["your_episode_ended"]["why"]
    finally:
        srv.stop()


# ── the real backend's read ──────────────────────────────────────────────────

def test_the_ck3_backend_reads_the_dynasty_from_query_character_and_never_the_house(tmp_path):
    from .test_ck3_backend import FakeBridge, _backend, _scn

    class Br(FakeBridge):
        rows = {37067: {"dynasty_id": 500, "house_id": 10}, 56019: {"dynasty_id": 500, "house_id": 11},
                60000: {"dynasty_id": 600, "house_id": 10}, 70000: {"dynasty_id": None, "house_id": 12}}

        def call(self, action, params=None, *a, **kw):
            out = super().call(action, params, *a, **kw)
            if action == "query_character" and isinstance(out, dict):
                cid = self.watch["line_character_id"] if (params or {}).get("played_line") else params["character_id"]
                out = {**out, **self.rows.get(cid, {})}
            return out
    br = Br(speed=0)
    b, *_ = _backend(tmp_path, bridge=br)
    b.open(_scn(), seed=1)
    assert b.metrics("king", ("gold",)).dynasty_id == 500
    br.watch = {"status": "following", "line_character_id": 56019}    # a cadet house of the same dynasty
    assert b.metrics("king", ("gold",)).dynasty_id == 500
    br.watch = {"status": "following", "line_character_id": 60000}    # the same house id, another dynasty
    assert b.metrics("king", ("gold",)).dynasty_id == 600
    br.watch = {"status": "following", "line_character_id": 70000}    # null: the house's dynasty did not resolve
    m = b.metrics("king", ("gold",))
    assert m.dynasty_id is None and "gave no dynasty_id" in m.dynasty_source


# ── line_kept_peacefully needs ONE ruler change ─────────────────────────────

@pytest.mark.parametrize("passes,met", [(1, True), (0, False)])
def test_one_same_dynasty_ruler_change_keeps_the_line(passes, met):
    """The Lorraine goal: one pass of the line in the seat's
    dynasty, no offensive war, counties >= start, judged at the end date; zero passes is not enough."""
    sc = _goal_sc("line_kept_peacefully")
    env = ParallelEnv(sc, backend=Conquest(at=sc.n_steps, dynasty="same", passes=passes))
    out = _run(env)
    info = out["last_infos"]["count"]
    assert out["ended"]["count"] == sc.n_steps
    if met:
        assert info.get("termination_reason") == TERM_GOAL and info["end"]["legs"]["ruler_changes"]["value"] >= 1
        assert "at least once" in info["end"]["why"]
    else:
        assert info.get("truncation_reason") == TRUNC_HORIZON
        assert info["end"]["legs"]["ruler_changes"] == {"value": 0, "status": "ok", "holds": False}
