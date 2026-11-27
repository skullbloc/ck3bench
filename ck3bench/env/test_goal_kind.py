"""The seat's toolkit server is told the task's goal kind (one source for what its observations
offer); the line tasks say exactly what ends the episode, the other tasks' text is unchanged. No game."""
from __future__ import annotations

import copy
import dataclasses
import hashlib
import io

from ck3bench.env.ck3 import GOAL_KIND_ENV, Ck3Backend
from ck3bench.env.env import ParallelEnv
from ck3bench.env.scenario import BUILTIN_DICTS, LINE_LOST_TEXT, LINE_TASK_LOST_TEXT, scenario_from_dict
from ck3bench.env.test_holder_starts import FakeHost, NorseBridge, Seat, _save
from ck3bench.env.test_task_notes_sets import LOR
from ck3bench.env.trajectory import TrajectoryWriter


def _open(tmp_path, goal=None):
    d = copy.deepcopy(BUILTIN_DICTS[LOR])
    d["start"]["save_sha256"] = hashlib.sha256(b"SAVE").hexdigest()
    sc = scenario_from_dict(d)
    if goal is not None:
        sc = dataclasses.replace(sc, goal=goal)
    seen = {}

    def tf(seat, cid, env):
        seen[seat] = dict(env)
        return Seat()
    b = Ck3Backend(_save(tmp_path, sc.start_save), host=FakeHost(), link_factory=lambda s, c, m: NorseBridge(),
                   socket_resolver=lambda i: "/run/fake.sock", transport_factory=tf, work_dir=tmp_path,
                   poll_s=1.0, retry_s=0.5, socket_probe=lambda s: False)
    ParallelEnv(sc, backend=b, trajectory=TrajectoryWriter(stream=io.StringIO())).reset(seed=1)
    return seen


def test_the_seat_server_is_told_the_goal_kind(tmp_path, monkeypatch):
    monkeypatch.setenv(GOAL_KIND_ENV, "kingdom_held")          # a stray value in the env's own environment
    seen = _open(tmp_path)
    assert seen["lorraine"][GOAL_KIND_ENV] == "line_kept_peacefully"


def test_the_line_tasks_say_what_ends_the_episode():
    for t in ("lorraine1066-line-25y-wake", "carinthia1066-line-25y-wake"):
        desc = BUILTIN_DICTS[t]["description"]
        assert LINE_TASK_LOST_TEXT in desc and LINE_LOST_TEXT not in desc, t
    assert "at your ruler's death" in LINE_TASK_LOST_TEXT and "does not end it" in LINE_TASK_LOST_TEXT


def test_CONTROL_every_other_task_keeps_its_text():
    """The Munster tasks (the once task among them) and every task that carried the sentence keep it."""
    for k, d in BUILTIN_DICTS.items():
        if d.get("goal") == "line_kept_peacefully":
            continue
        assert LINE_TASK_LOST_TEXT not in str(d.get("description") or ""), k
    for k in ("munster1066-kingdom-30y-wake", "munster1066-kingdom-once-30y-wake"):
        assert LINE_LOST_TEXT in BUILTIN_DICTS[k]["description"], k
