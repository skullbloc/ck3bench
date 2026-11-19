"""Contract checks any ParallelEnv-shaped environment must pass, as plain
functions (raising AssertionError), so the tests run them on the real classes
AND on deliberately broken ones (the mutants) to show they bite.
"""
from __future__ import annotations

import math
from typing import Any, Mapping

from .errors import TERMINATION_REASONS, TRUNC_HORIZON, TRUNCATION_REASONS


def check_reset(env, out: Any) -> None:
    assert isinstance(out, tuple) and len(out) == 2, "reset returns (observations, infos)"
    obs, infos = out
    assert set(obs) == set(env.possible_agents) == set(infos), "reset keys every possible agent"
    assert env.agents == list(env.possible_agents), "every seat is live after reset"
    for s, o in obs.items():
        assert o["seat"] == s and o["step"] == 0
        assert isinstance(infos[s]["seed"], int), "the seed is always known"
        assert infos[s]["fake"] is env.backend.fake


def check_step(env, live_before: list, out: Any) -> None:
    assert isinstance(out, tuple) and len(out) == 5, "step returns five dicts"
    obs, rew, te, tr, infos = out
    for d in out:
        assert set(d) == set(live_before), "each dict is keyed by the seats live before the step"
    for s in live_before:
        assert isinstance(te[s], bool) and isinstance(tr[s], bool)
        assert not (te[s] and tr[s]), "a seat is terminated or truncated, not both"
        assert isinstance(rew[s], float)
        status = infos[s].get("reward_status")
        assert status in ("ok", "unavailable"), "every reward carries its status"
        if status == "unavailable":
            assert math.isnan(rew[s]), "an unavailable reward is NaN, never a number"
            assert tr[s] and infos[s].get("truncation_reason") in TRUNCATION_REASONS
            assert infos[s]["truncation_reason"] != TRUNC_HORIZON, "a failure is never reported as the horizon"
        else:
            assert math.isfinite(rew[s])
        if te[s]:
            assert infos[s].get("termination_reason") in TERMINATION_REASONS
        if tr[s]:
            assert infos[s].get("truncation_reason") in TRUNCATION_REASONS
    assert env.agents == [s for s in live_before if not (te[s] or tr[s])], "a done seat leaves agents"


def check_horizon(env, steps: int, last_infos: Mapping[str, dict]) -> None:
    """An episode that ran to its horizon stopped at exactly n_steps, truncated `horizon`."""
    assert steps == env.scenario.n_steps, f"{steps} steps, the scenario declares {env.scenario.n_steps}"
    assert all(i.get("truncation_reason") == TRUNC_HORIZON or i.get("termination_reason")
               for i in last_infos.values())


def drive(env, seed: int, actions_for=lambda env, obs: {s: [] for s in env.agents}) -> dict:
    """Run one episode under the contract checks; returns what happened."""
    out = env.reset(seed=seed)
    check_reset(env, out)
    obs, steps, last = out[0], 0, {}
    rewards: list = []
    while env.agents:
        live = list(env.agents)
        res = env.step(actions_for(env, obs))
        check_step(env, live, res)
        obs, last = res[0], res[4]
        rewards.append(res[1])
        steps += 1
    return {"steps": steps, "last_infos": last, "rewards": rewards, "end_reason": env.end_reason}
