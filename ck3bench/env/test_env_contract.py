"""The API contract on the fake backend: shapes, lifecycle refusals,
determinism, and the single-seat wrapper."""
from __future__ import annotations

import io
import json
import math

import pytest

from ck3bench.env import Env, ParallelEnv, ToolCall, TrajectoryWriter, check_trajectory
from ck3bench.env import contract
from ck3bench.env.errors import (R_ACTION_SHAPE, R_EPISODE_OVER, R_NOT_RESET, R_SEED_TYPE, R_UNKNOWN_SEAT,
                                 TRUNC_HORIZON, EnvRefusal)
from ck3bench.env.fake import FakeBackend
from ck3bench.env.scenario import BUILTIN_DICTS, scenario_from_dict


def _taxes(env, obs):
    return {s: [ToolCall("do", {"intent": "raise_taxes"})] for s in env.agents}


def _records(writer):
    return [json.loads(x) for x in writer._fh.getvalue().splitlines()]


def _strip_wall(recs):
    out = []
    for r in recs:
        r = {k: v for k, v in r.items() if k not in ("wall_utc", "wall_s", "episode_id")}
        if r["kind"] == "step":
            r["advance"] = {k: v for k, v in r["advance"].items() if k != "wall_s"}
        out.append(r)
    return out


def test_a_whole_episode_keeps_the_contract_and_ends_at_the_horizon():
    env = ParallelEnv("fake-solo")
    got = contract.drive(env, seed=1, actions_for=_taxes)
    contract.check_horizon(env, got["steps"], got["last_infos"])
    assert got["end_reason"] == TRUNC_HORIZON and got["steps"] == 12


def test_two_seats_step_together_and_the_order_is_recorded():
    w = TrajectoryWriter(stream=io.StringIO())
    env = ParallelEnv("fake-duel", trajectory=w)
    env.reset(seed=4)
    _, _, _, _, infos = env.step({"east": [], "west": [ToolCall("pass_turn", {"reason": "x"})]})
    assert sorted(infos["east"]["order"]) == ["east", "west"]
    steps = [r for r in _records(w) if r["kind"] == "step"]
    assert steps[0]["order"] == infos["east"]["order"]


def test_step_before_reset_is_refused_not_reset():
    env = ParallelEnv("fake-solo")
    with pytest.raises(EnvRefusal) as e:
        env.step({"king": []})
    assert e.value.code == R_NOT_RESET


def test_step_after_the_end_is_refused_episode_over():
    env = ParallelEnv("fake-solo")
    contract.drive(env, seed=2)
    with pytest.raises(EnvRefusal) as e:
        env.step({"king": []})
    assert e.value.code == R_EPISODE_OVER
    with pytest.raises(EnvRefusal) as e:
        env.call("king", ToolCall("observe"))
    assert e.value.code == R_EPISODE_OVER


def test_a_missing_seat_action_is_refused_never_defaulted_to_no_action():
    env = ParallelEnv("fake-duel")
    env.reset(seed=1)
    with pytest.raises(EnvRefusal) as e:
        env.step({"east": []})
    assert e.value.code == R_ACTION_SHAPE and "west" in e.value.detail


def test_an_action_for_an_unknown_seat_is_refused():
    env = ParallelEnv("fake-solo")
    env.reset(seed=1)
    with pytest.raises(EnvRefusal) as e:
        env.step({"king": [], "pretender": []})
    assert e.value.code == R_UNKNOWN_SEAT


@pytest.mark.parametrize("bad", ["observe", [{"tool": "observe"}], [{"tool_name": ""}],
                                 [{"tool_name": "do", "arguments": []}], {"tool_name": "do"}])
def test_a_malformed_action_is_refused(bad):
    env = ParallelEnv("fake-solo")
    env.reset(seed=1)
    with pytest.raises(EnvRefusal) as e:
        env.step({"king": bad})
    assert e.value.code == R_ACTION_SHAPE


def test_a_bad_seed_is_refused():
    env = ParallelEnv("fake-solo")
    for seed in ("7", 1.5, True):
        with pytest.raises(EnvRefusal) as e:
            env.reset(seed=seed)
        assert e.value.code == R_SEED_TYPE


def test_a_tool_outside_the_scenario_is_an_answer_the_policy_reads_not_an_exception():
    env = ParallelEnv("fake-solo")
    env.reset(seed=1)
    res = env.call("king", ToolCall("declare_war", {"target_id": 100}))
    assert res.ok is False and res.error.startswith("tool_not_allowed")
    obs, *_ = env.step({"king": [ToolCall("steer")]})
    assert [r["result"]["ok"] for r in obs["king"]["last_results"]] == [False, False]


def test_an_unknown_public_tool_on_the_fake_says_it_is_not_modelled():
    sc = dict(BUILTIN_DICTS["fake-solo"], tools=["observe", "steer"])
    env = ParallelEnv(scenario_from_dict(sc))
    env.reset(seed=1)
    res = env.call("king", ToolCall("steer"))
    assert res.ok is False and "not_modelled_by_fake" in res.error


def test_in_turn_calls_do_not_move_the_clock_and_reach_the_next_observation():
    env = ParallelEnv("fake-solo")
    obs, _ = env.reset(seed=1)
    d0 = obs["king"]["game_date"]
    r = env.call("king", ToolCall("observe"))
    assert r.ok and r.result["date"] == d0
    obs, *_ = env.step({"king": []})
    assert [x["phase"] for x in obs["king"]["last_results"]] == ["turn"]
    assert obs["king"]["game_date"] == "1066.10.15"


def test_same_seed_same_actions_same_trajectory_and_another_seed_differs():
    def run(seed):
        w = TrajectoryWriter(stream=io.StringIO())
        env = ParallelEnv("fake-duel", trajectory=w)
        contract.drive(env, seed, lambda env, obs: {
            s: [ToolCall("declare_war", {"target_id": obs[s]["world"]["neighbours"][0]})] for s in env.agents})
        return _strip_wall(_records(w))
    a, b, c = run(11), run(11), run(12)
    assert a == b
    assert a != c


def test_a_drawn_seed_is_recorded_so_the_episode_can_be_replayed():
    w = TrajectoryWriter(stream=io.StringIO())
    env = ParallelEnv("fake-solo", trajectory=w)
    _, infos = env.reset()
    assert infos["king"]["seed_drawn"] is True and isinstance(infos["king"]["seed"], int)
    assert _records(w)[0]["seed"] == infos["king"]["seed"]


def test_reset_mid_episode_closes_the_old_episode_in_the_trajectory():
    w = TrajectoryWriter(stream=io.StringIO())
    env = ParallelEnv("fake-solo", trajectory=w)
    env.reset(seed=1)
    env.step({"king": []})
    env.reset(seed=2)
    env.close()
    recs = _records(w)
    assert [r["reason"] for r in recs if r["kind"] == "end"] == ["abandoned_by_reset", "closed"]
    assert check_trajectory(recs) == []


def test_reset_can_switch_scenario_on_the_same_backend():
    env = ParallelEnv("fake-solo")
    obs, _ = env.reset(seed=1, options={"scenario": "fake-duel"})
    assert set(obs) == {"east", "west"} and env.possible_agents == ["east", "west"]


def test_every_record_and_observation_says_fake():
    w = TrajectoryWriter(stream=io.StringIO())
    env = ParallelEnv("fake-duel", trajectory=w)
    contract.drive(env, 3)
    recs = _records(w)
    assert recs and all(r["fake"] is True and r["backend"] == "fake" for r in recs)
    assert all(o["fake"] is True for r in recs if r["kind"] == "step" for o in r["observations"].values())


def test_the_single_seat_env_has_the_gymnasium_shape():
    env = Env("fake-solo")
    obs, info = env.reset(seed=5)
    assert obs["seat"] == "king" and info["seed"] == 5
    total, n = 0.0, 0
    while True:
        obs, r, term, trunc, info = env.step([ToolCall("do", {"intent": "raise_taxes"})])
        total, n = total + r, n + 1
        if term or trunc:
            break
    assert n == 12 and trunc and info["truncation_reason"] == TRUNC_HORIZON and math.isfinite(total)


def test_the_single_seat_env_refuses_a_multi_seat_scenario():
    with pytest.raises(EnvRefusal):
        Env("fake-duel")


def test_usage_a_policy_reports_is_recorded_as_reported():
    w = TrajectoryWriter(stream=io.StringIO())
    env = Env("fake-solo", trajectory=w)
    env.reset(seed=1)
    env.step([], usage={"input_tokens": 10, "output_tokens": 3})
    env.step([])
    steps = [r for r in _records(w) if r["kind"] == "step"]
    assert steps[0]["usage"] == {"king": {"input_tokens": 10, "output_tokens": 3}}
    assert steps[1]["usage"] == {}


def test_a_fake_backend_on_a_ck3_scenario_is_refused():
    with pytest.raises(EnvRefusal) as e:
        ParallelEnv("france1066-treasury-2y", backend=FakeBackend())
    assert e.value.code == "scenario_needs_other_backend"

