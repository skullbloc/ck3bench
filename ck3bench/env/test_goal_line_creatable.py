"""The goal line says when the target title can be created now; the seat's toolkit server is told the task's
target title (its create_title veto never drops the game's AI creating it for the seat). No game."""
from __future__ import annotations

import copy
import dataclasses
import hashlib
import io

from ck3bench.env.ck3 import GOAL_TITLE_ENV, Ck3Backend
from ck3bench.env.env import ParallelEnv
from ck3bench.env.scenario import BUILTIN_DICTS, scenario_from_dict
from ck3bench.env.test_holder_starts import FakeHost, NorseBridge, Seat, _save
from ck3bench.env.test_task_notes_sets import LOR
from ck3bench.env.trajectory import TrajectoryWriter


def _open(tmp_path, target):
    """The Lorraine task (its seat found on the fake), with the target title set on the built task when given."""
    d = copy.deepcopy(BUILTIN_DICTS[LOR])
    d["start"]["save_sha256"] = hashlib.sha256(b"SAVE").hexdigest()
    sc = scenario_from_dict(d)
    if target:
        sc = dataclasses.replace(sc, target_title=target)
    seen = {}

    def tf(seat, cid, env):
        seen[seat] = dict(env)
        return Seat()
    b = Ck3Backend(_save(tmp_path, sc.start_save), host=FakeHost(), link_factory=lambda s, c, m: NorseBridge(),
                   socket_resolver=lambda i: "/run/fake.sock", transport_factory=tf, work_dir=tmp_path,
                   poll_s=1.0, retry_s=0.5, socket_probe=lambda s: False)
    ParallelEnv(sc, backend=b, trajectory=TrajectoryWriter(stream=io.StringIO())).reset(seed=1)
    return seen


def test_the_seat_server_is_told_the_tasks_target_title(tmp_path, monkeypatch):
    monkeypatch.delenv(GOAL_TITLE_ENV, raising=False)
    seen = _open(tmp_path, "d_lower_lorraine")
    assert seen["lorraine"][GOAL_TITLE_ENV] == "d_lower_lorraine"


def test_CONTROL_a_task_with_no_target_passes_none_on(tmp_path, monkeypatch):
    """A stray CK3_GOAL_TITLE in the env's own environment never reaches a seat of a task with no target title."""
    monkeypatch.setenv(GOAL_TITLE_ENV, "k_ireland")
    assert not BUILTIN_DICTS[LOR].get("target_title")
    seen = _open(tmp_path, None)
    assert GOAL_TITLE_ENV not in seen["lorraine"]
