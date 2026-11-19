"""The ways the game dies are TYPED truncations, never a hang and never a
success: each injected on the fake backend. Then the two task-side ends
(line ended, goal reached), and two mutants that show the contract checks bite."""
from __future__ import annotations

import io
import json
import math

import pytest

from ck3bench.env import ParallelEnv, ToolCall, TrajectoryWriter, check_trajectory, contract
from ck3bench.env.errors import (TERM_GOAL, TERM_LINE_ENDED, TRUNC_BRIDGE_UNAVAILABLE, TRUNC_CLOCK_STALLED,
                                 TRUNC_GAME_EXITED, TRUNC_METRIC_UNREADABLE, TRUNC_SCRIPT_LOG_CLOSED)
from ck3bench.env.fake import FakeBackend
from ck3bench.env.scenario import BUILTIN_DICTS, scenario_from_dict


def _duel(**fake):
    d = json.loads(json.dumps(BUILTIN_DICTS["fake-duel"]))
    d["fake"].update(fake)
    return scenario_from_dict(d)


@pytest.mark.parametrize("kind,reason", [
    (TRUNC_GAME_EXITED, TRUNC_GAME_EXITED), (TRUNC_CLOCK_STALLED, TRUNC_CLOCK_STALLED),
    (TRUNC_BRIDGE_UNAVAILABLE, TRUNC_BRIDGE_UNAVAILABLE), (TRUNC_SCRIPT_LOG_CLOSED, TRUNC_SCRIPT_LOG_CLOSED),
    ("metric_unreadable", TRUNC_METRIC_UNREADABLE)])
def test_each_failure_truncates_every_seat_with_its_reason_and_a_nan_reward(kind, reason):
    w = TrajectoryWriter(stream=io.StringIO())
    env = ParallelEnv(_duel(fail={"at_step": 3, "kind": kind}), trajectory=w)
    got = contract.drive(env, seed=1)
    assert got["steps"] == 3 and got["end_reason"] == reason
    assert all(math.isnan(r) for r in got["rewards"][-1].values())
    assert all(i["truncation_reason"] == reason for i in got["last_infos"].values())
    recs = [json.loads(x) for x in w._fh.getvalue().splitlines()]
    assert check_trajectory(recs) == []
    last = [r for r in recs if r["kind"] == "step"][-1]
    assert set(last["rewards"].values()) == {None}, "JSON carries no NaN: the reward is null"
    assert recs[-1]["returns"] == {"east": None, "west": None}


def test_a_line_that_ends_terminates_its_seat_and_the_other_plays_on():
    # east is old, alone and attacked every step: its line ends before the horizon
    env = ParallelEnv(_duel(counties=[1, 30], ages=[90, 20]))
    env.reset(seed=3)
    ended, steps = None, 0
    while env.agents:
        acts = {s: [] for s in env.agents}
        if "west" in env.agents:
            acts["west"] = [ToolCall("declare_war", {"target_id": 1})] if "east" in env.agents else []
        _, rew, te, tr, infos = env.step(acts)
        steps += 1
        if te.get("east"):
            ended = (steps, infos["east"]["termination_reason"], rew["east"])
    assert ended is not None and ended[1] == TERM_LINE_ENDED
    assert ended[2] <= -10.0, "line_survival (weight 10) is paid on the step the line ends"
    assert steps == 8 and ended[0] < 8


def test_the_county_goal_terminates_the_seat_that_reaches_it():
    d = json.loads(json.dumps(BUILTIN_DICTS["fake-duel"]))
    d.update(goal="county_gain", reward={"terms": [{"name": "county_gain_goal", "weight": 1.0}]})
    d["fake"].update(counties=[50, 1], ages=[20, 20])
    env = ParallelEnv(scenario_from_dict(d))
    env.reset(seed=5)
    hit = None
    for _ in range(8):
        if "east" not in env.agents:
            break
        _, rew, te, _, infos = env.step({s: ([ToolCall("declare_war", {"target_id": 100})] if s == "east" else [])
                                         for s in env.agents})
        if te.get("east"):
            hit = (infos["east"]["termination_reason"], rew["east"])
    assert hit == (TERM_GOAL, 1.0)


# ── mutants: the checks are not vacuous ──────────────────────────────────

class _ZeroOnFailure(ParallelEnv):
    """MUTANT: reports a failure step's reward as 0.0, the silent default."""
    def step(self, actions, usage=None):
        obs, r, te, tr, infos = super().step(actions, usage)
        return obs, {k: (0.0 if math.isnan(v) else v) for k, v in r.items()}, te, tr, infos


class _HorizonOnFailure(ParallelEnv):
    """MUTANT: reports a world failure as the horizon (a success)."""
    def step(self, actions, usage=None):
        obs, r, te, tr, infos = super().step(actions, usage)
        for i in infos.values():
            if i.get("truncation_reason"):
                i["truncation_reason"] = "horizon"
        return obs, r, te, tr, infos


@pytest.mark.parametrize("mutant", [_ZeroOnFailure, _HorizonOnFailure])
def test_mutant_the_contract_check_reds_on_a_failure_reported_as_a_number_or_a_success(mutant):
    env = mutant(_duel(fail={"at_step": 2, "kind": TRUNC_GAME_EXITED}))
    with pytest.raises(AssertionError):
        contract.drive(env, seed=1)


def test_control_the_unmutated_env_passes_the_same_check():
    contract.drive(ParallelEnv(_duel(fail={"at_step": 2, "kind": TRUNC_GAME_EXITED})), seed=1)


class _SeedlessFake(FakeBackend):
    """MUTANT: a backend whose world ignores the seed."""
    def open(self, scenario, seed):
        return super().open(scenario, 0)


def test_mutant_a_backend_that_ignores_the_seed_is_caught_by_the_determinism_check():
    def rewards(seed, backend):
        env = ParallelEnv("fake-duel", backend=backend)
        return contract.drive(env, seed, lambda env, obs: {
            s: [ToolCall("declare_war", {"target_id": 100})] for s in env.agents})["rewards"]
    assert rewards(11, FakeBackend()) != rewards(12, FakeBackend())          # control
    assert rewards(11, _SeedlessFake()) == rewards(12, _SeedlessFake())      # the mutant's seeds do nothing
