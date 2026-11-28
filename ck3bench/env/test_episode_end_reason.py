"""The seat is told why its episode ended.

When the game's AI creates the target title for the seat's ruler (k_ireland, munster1066-kingdom-30y-wake) and the
env sees him hold it at the step's end (`holds_target_title: true`, termination goal_reached), an `end_turn` reply of
`{"ok": true, "turn_ended": true, "stepped": true, "waiting_for": [], "episode_over": true, ...}` with no reason
reads to the seat as a failure to create it. The step's `info` carries `end` (the goal with the title, its holder, the date and who created it; or
the horizon) and the shim's ack reply carries it as `your_episode_ended`.

What the task scores is unchanged and stated in its text: the ruler HOLDING the title, however it came to him
(rewards.py goal kingdom_held and target_title_gained read `holds_target_title`, the title's holder_id against the
seat's character, ck3.py `_target_reads`; outcome.py scores the decision tasks only and has no kind for this one).
"""
from __future__ import annotations

import pytest

from ck3bench.env import ParallelEnv, ToolCall, contract
from ck3bench.env.errors import TERM_GOAL, TRUNC_HORIZON
from ck3bench.env.fake import FakeBackend
from ck3bench.env.mcp_shim import SeatShim
from ck3bench.env.scenario import builtin
from ck3bench.env.server import EnvClient, EnvServer

from .test_ireland import _conqueror


def _goal_run(monkeypatch, origin):
    if origin is not None:
        monkeypatch.setattr(FakeBackend, "title_origin", lambda self, seat: origin(self, seat), raising=False)
    for seed in range(40):
        env = ParallelEnv("fake-kingdom")
        out = contract.drive(env, seed=seed, actions_for=_conqueror)
        last = out["last_infos"].get("count") or {}
        if last.get("termination_reason") == TERM_GOAL:
            return env, last
    pytest.fail("no seed of 40 reached the kingdom on the fake map")


def test_the_goal_end_names_the_title_its_holder_the_date_and_that_holding_is_scored(monkeypatch):
    env, info = _goal_run(monkeypatch, None)
    end = info["end"]
    assert end["reason"] == TERM_GOAL and end["title"] == "k_fake" and end["date"] == info["game_date"]
    assert end["holder_id"] == env.state()["metrics"]["count"]["character_id"]
    assert "however it came to him" in end["scored"]
    # the fake world keeps no title history: said, never guessed
    assert "unread" in end["created"]
    assert end["your_calls_naming_it"][-1]["intent"] == "mint_title"


def test_created_by_the_seats_own_call_says_so(monkeypatch):
    def origin(self, seat):
        return {"date": "1067.01.01", "event": "created", "char_id": self.realms[seat].character_id}
    env, info = _goal_run(monkeypatch, origin)
    assert info["end"]["created"]["by"].startswith("you: do mint_title at step ")


def test_CONTROL_with_no_call_of_the_seats_it_names_the_game(monkeypatch):
    def origin(self, seat):
        return {"date": "1080.05.25", "event": "created", "char_id": self.realms[seat].character_id}
    monkeypatch.setattr(FakeBackend, "title_origin", origin, raising=False)
    env = ParallelEnv("fake-kingdom")
    env.reset(seed=1)
    env._title_calls["count"] = []                 # the AI's creation: no call of the seat's named the title
    end = env._goal_end("count", 31, "1080.12.26")
    assert end["created"]["by"].startswith("the game for your ruler") and end["created"]["date"] == "1080.05.25"
    env.close()


def test_the_horizon_end_says_the_end_date_was_reached():
    env = ParallelEnv("fake-solo")
    out = contract.drive(env, seed=1)
    end = out["last_infos"]["king"]["end"]
    assert end["reason"] == TRUNC_HORIZON and end["end_date"] == builtin("fake-solo").end_date
    assert "not met" in end["why"]


def test_the_ack_reply_of_the_last_step_carries_why_and_earlier_ones_do_not():
    env = ParallelEnv("fake-solo")
    srv = EnvServer(env, turn_timeout_s=30).start()
    cl = EnvClient(srv.url, timeout=30)
    cl.reset(seed=1)
    try:
        replies = []
        while True:
            out, err = SeatShim(cl, "king", 30, ack=True).call("end_turn", {"summary": "pass"})
            assert not err
            replies.append(out)
            if out["episode_over"]:
                break
        assert all("your_episode_ended" not in r for r in replies[:-1])
        why = replies[-1]["your_episode_ended"]
        assert why["reason"] == TRUNC_HORIZON and why["date"] == builtin("fake-solo").end_date
    finally:
        srv.stop()
        env.close()


def test_the_munster_task_text_says_what_is_scored():
    d = builtin("munster1066-kingdom-30y-wake").description
    assert "What is scored is your ruler HOLDING k_ireland, however it comes to him" in d
    assert "created for him by the game's AI" in d


def test_an_applied_call_naming_the_title_is_recorded_and_a_refused_one_is_not():
    env = ParallelEnv("fake-kingdom")
    env.reset(seed=1)
    env.call("count", ToolCall("do", {"intent": "mint_title", "args": {"title_key": "k_fake"}}))   # refused: short
    assert env._title_calls["count"] == []
    env.close()
