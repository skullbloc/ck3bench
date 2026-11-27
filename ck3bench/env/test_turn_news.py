"""The seat's toolkit news in every observation (the real-game backend).

A seat reads the environment's observation every turn and the toolkit's `observe` only
when it calls it, so news the toolkit reports (a chaplain moved off fabricating a claim)
reaches the seat only through `observe` unless the observation carries it. The ck3 backend
reads observe(depth="news") on the seat's own server once per game date and puts it in the
observation's `world.news`; that read diffs against its own last news read, so the seat's
own observe news is not moved.

The news replies below are the toolkit's compact news (news.compact) of blocks recorded
on a live 1066 game (toolkit 2026.12.12, Murchad of Dublin), as the seat's server
answers observe(depth="news").
"""
from __future__ import annotations

import io
import json

from ck3bench.env import ParallelEnv, ToolCall
from ck3bench.env.mcp_shim import compact_step
from ck3bench.env.test_ck3_backend import FakeBridge, _backend, _scn
from ck3bench.env.trajectory import TrajectoryWriter

#: news.compact of the recorded 1068.09.15 block (the chaplain moved off fabricating)
RECORDED_1068_09_15 = {
    "council_changed": [{"seat": "councillor_court_chaplain", "before": "task_fabricate_claim",
                         "now": "task_religious_relations",
                         "fabrication_left": {"since": "1067.11.27", "title_id": 508, "title_key": None},
                         "next": {"tool": "do", "args": {"action_class": "fabricate_targets",
                                                         "args": {"within": "d_meath"}}}}],
    "since": "1067.04.22",
    "titles_gained": [{"title_id": 469, "key": "c_athlone", "how": None},
                      {"title_id": 470, "key": "b_athlone", "how": None}]}
#: what the toolkit answers on a turn when nothing changed, and on its first news read
QUIET = {"since": "1066.11.27", "nothing_new": True}
FIRST = {"first_observe": True, "nothing_new": True}


class NewsTransport:
    """The seat's toolkit server: observe(depth="news") answers the next news block."""

    def __init__(self, blocks):
        self.blocks = list(blocks)
        self.calls: list = []

    def call(self, tool_name, arguments):
        self.calls.append((tool_name, dict(arguments)))
        if tool_name == "observe" and arguments.get("depth") == "news":
            news = self.blocks.pop(0) if self.blocks else QUIET
            return {"ok": True, "tool": "observe", "result": {"depth": "news", "news": news}}
        return {"ok": True, "tool": tool_name, "result": {}}

    def close(self):
        pass


def _news_backend(tmp_path, blocks):
    tr = NewsTransport(blocks)
    b, br, host, _ = _backend(tmp_path, bridge=FakeBridge(speed=0, rate=7))
    b.transport_factory = lambda seat, cid, env: tr
    return b, tr


def _news_calls(tr):
    return [a for t, a in tr.calls if t == "observe"]


def test_each_observation_carries_the_seats_news_read_once_per_date(tmp_path):
    b, tr = _news_backend(tmp_path, [FIRST, RECORDED_1068_09_15])
    buf = io.StringIO()
    env = ParallelEnv(_scn(), backend=b, trajectory=TrajectoryWriter(stream=buf))
    obs, _ = env.reset(seed=1)
    assert obs["king"]["world"]["news"] == FIRST
    assert env.observe("king")["world"]["news"] == FIRST          # seat_observation: no second read
    assert _news_calls(tr) == [{"depth": "news"}]
    obs, *_ = env.step({"king": []})
    assert obs["king"]["world"]["news"] == RECORDED_1068_09_15
    assert env.observe("king")["world"]["news"] == RECORDED_1068_09_15
    assert _news_calls(tr) == [{"depth": "news"}, {"depth": "news"}], "one read per turn, never a plain observe"
    obs, *_ = env.step({"king": []})
    assert obs["king"]["world"]["news"] == QUIET and len(json.dumps(QUIET)) < 60
    steps = [json.loads(x) for x in buf.getvalue().splitlines() if json.loads(x)["kind"] == "step"]
    assert steps[0]["observations"]["king"]["world"]["news"] == RECORDED_1068_09_15, "the trajectory records it"
    env.close()


def test_CONTROL_the_end_turn_reply_keeps_the_news(tmp_path):
    """end_turn / await_step hand the session compact_step(step result): the news stays whole."""
    out = {"observation": {"world": {"news": RECORDED_1068_09_15}, "last_results": [
        {"phase": "turn", "call": {"tool_name": "observe"}, "result": {"ok": True, "result": {"x": 1}}}]}}
    got = compact_step(out)
    assert got["observation"]["world"]["news"] == RECORDED_1068_09_15
    assert len(json.dumps(RECORDED_1068_09_15)) < 500


def test_a_news_read_that_failed_says_so(tmp_path):
    b, tr = _news_backend(tmp_path, [])
    tr.call = lambda tool, args: {"ok": False, "blocked": {"gate": "read_timeout"}}
    b.open(_scn(), seed=1)
    news = b.observation("king")["news"]
    assert "unread" in news and "observe(depth=news)" in news["unread"], news


def test_CONTROL_the_fake_backend_observation_is_unchanged():
    env = ParallelEnv("fake-council")
    obs, _ = env.reset(seed=3)
    for o in obs.values():
        assert o["world"]["fake"] is True and "news" not in o["world"]
    env.close()
