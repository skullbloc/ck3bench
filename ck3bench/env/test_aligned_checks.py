"""Aligned checks: a wake task's checks land ON their dates at any declared speed.

A run that declares its game speed (`env run/serve/play --game-speed N`) runs its wake checks ALIGNED: the
backend arms the bridge's game-day pause alarm (pause_alarm.h, `unpause_game {pause_at}`) at each check date
(start + k * check_days, the step's end last), waits for the pause, makes the check paused, and runs on. The step
record carries the witness `wake.check_dates`: each check's INTENDED date beside the date the bridge READ when the
clock stopped (declared beside observed); a difference is `mismatch`, a problem the trajectory checker names.

A run that declares no speed keeps the wall-polled checks (the clock read about once a wall second, a check whenever
the date has moved `check_days`). The fakes prove the route as written; only a live game proves the game stops where
they do.
"""
from __future__ import annotations

import copy
import io
import json

import pytest

from ck3bench.env import ParallelEnv, calendar, cli
from ck3bench.env.errors import EnvRefusal
from ck3bench.env.scenario import BUILTIN_DICTS, scenario_from_dict
from ck3bench.env.test_wake_ck3 import NEVER, TASK, WakeBridge, _claim, _env
from ck3bench.env.trajectory import TrajectoryWriter, check_trajectory, read_trajectory
from ck3bench.env.types import check_dates_witness

START = "1066.09.15"


def _fast(speed=5, aligned=True, **wake):
    d = copy.deepcopy(BUILTIN_DICTS[TASK])
    d["game_speed"] = speed
    if aligned:
        d["wake"]["aligned_checks"] = True
    d["wake"].update(wake)
    return scenario_from_dict(d)


def _steps(buf):
    return [json.loads(x) for x in buf.getvalue().splitlines() if json.loads(x)["kind"] == "step"]


def _sched(n, every=7, start=START):
    return [calendar.add_days(start, every * k) for k in range(1, n + 1)]


class LateBridge(WakeBridge):
    """WakeBridge whose alarm fires `late[target]` days after the target it was armed for (a frame that ran one
    more day before the pause took hold)."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.late: dict[str, int] = {}

    def _move(self, days):
        real = self.target
        extra = self.late.get(real, 0)
        if extra:
            self.target = calendar.add_days(real, extra)
        try:
            super()._move(days)
        finally:
            if self.alarm != "fired":
                self.target = real


# ── the declaration ─────────────────────────────────────────────────────────────────────────────────────────────

def test_aligned_checks_is_a_wake_declaration_written_only_when_on():
    on, off = _fast(), _fast(speed=3, aligned=False)
    assert on.wake.aligned is True and off.wake.aligned is False
    assert on.to_dict()["wake"]["aligned_checks"] is True
    assert "aligned_checks" not in off.to_dict()["wake"]                   # the default is not written
    assert scenario_from_dict(on.to_dict()).wake == on.wake


def test_a_wake_task_faster_than_3_without_aligned_checks_is_refused():
    for speed in (4, 5):
        with pytest.raises(EnvRefusal) as e:
            _fast(speed=speed, aligned=False)
        assert e.value.code == "scenario_bad_value" and "aligned_checks" in e.value.detail
    # control: the same at 3 loads (polled, as before), and a fixed-cadence task at 5 loads (it has no checks)
    assert _fast(speed=3, aligned=False).wake.aligned is False
    assert scenario_from_dict(dict(BUILTIN_DICTS["france1066-treasury-2y"], game_speed=5)).wake is None


def test_a_speed_declared_on_the_command_line_aligns_the_checks(tmp_path, capsys):
    for speed in (5, 3):
        out = tmp_path / f"run{speed}.jsonl"
        assert cli.main(["run", "fake-kingdom-wake", "--seed", "1", "--policy", "noop", "--out", str(out),
                         "--game-speed", str(speed)]) == cli.EXIT_OK
        recs = read_trajectory(out)
        assert recs[0]["scenario"]["wake"]["aligned_checks"] is True and check_trajectory(recs) == []
        # a noop seat asks no condition and the task forces none: no check is made, none is witnessed
        steps = [r for r in recs if r["kind"] == "step"]
        assert steps and all(r["wake"]["checks"] == 0 and "check_dates" not in r["wake"] for r in steps)
        sc = cli._scenario(type("A", (), {"scenario": "fake-kingdom-wake", "game_speed": speed})())
        assert sc.game_speed == speed and sc.wake.aligned is True
    out = tmp_path / "undeclared.jsonl"
    assert cli.main(["run", "fake-kingdom-wake", "--seed", "1", "--policy", "noop", "--out", str(out)]) == cli.EXIT_OK
    recs = read_trajectory(out)
    assert "aligned_checks" not in recs[0]["scenario"]["wake"]
    assert not any("check_dates" in r["wake"] for r in recs if r["kind"] == "step")
    capsys.readouterr()


# ── the witness ─────────────────────────────────────────────────────────────────────────────────────────────────

def test_the_witness_is_declared_beside_observed_and_never_agreed_without_a_read():
    a = check_dates_witness([("1066.09.22", "1066.09.22"), ("1066.09.29", "1066.09.29")])
    assert a["status"] == "agreed" and a["checks"] == 2 and a["off"] == {"0": 2}
    assert a["rows"][0] == {"intended": "1066.09.22", "read": "1066.09.22", "off": 0}
    m = check_dates_witness([("1066.09.22", "1066.09.23"), ("1066.09.29", "1066.09.29")])
    assert m["status"] == "mismatch" and m["off"] == {"1": 1, "0": 1}
    u = check_dates_witness([("1066.09.22", None)])
    assert u["status"] == "unread" and u["rows"][0]["off"] is None
    assert check_dates_witness([])["status"] == "unwitnessed"


# ── the real backend's clock loop (fake bridge) ────────────────────────────────────────────────────────────────

def test_aligned_every_check_is_made_paused_on_its_date(tmp_path):
    br = WakeBridge()
    br.rate = 10                                  # a speed-5 clock: 10 game days between two reads
    buf = io.StringIO()
    env, br, _ = _env(tmp_path, bridge=br, task=_fast(), buf=buf)
    n0 = len(br.calls)
    _, w = _step(env, {"max_days": 70, "when": [NEVER]})
    rec = _steps(buf)[-1]["wake"]
    cd = rec["check_dates"]
    assert cd["status"] == "agreed" and cd["checks"] == 10
    assert [r["intended"] for r in cd["rows"]] == _sched(10) == [r["read"] for r in cd["rows"]]
    assert w["probe"]["checks_running"] == 0 and w["probe"]["checks_paused"] == 10 and w["days"] == 70
    # the alarm was armed at each check date in turn, and nowhere else
    arms = [p["pause_at"] for a, p in br.calls[n0:] if a == "unpause_game"]
    assert arms == _sched(10)


class SlowPauseBridge(WakeBridge):
    """WakeBridge whose pause takes hold one read after the date reached the alarm's day: that read shows the
    date there with the clock still running. Logs whether the clock was paused at every wake read."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self._hold = False
        self.read_paused: list[bool] = []

    def call(self, action, params=None, timeout=8.0):
        if action == "get_tick_info" and self._hold:
            self._hold, self.paused, self.alarm = False, True, "fired"
            return {"game_date": self.date, "paused": True, "game_speed": self.speed, "pause_alarm": {"state": "fired"}}
        if action in ("query_claims", "get_active_wars", "query_title_hierarchy"):
            self.read_paused.append(self.paused)
        r = super().call(action, params, timeout)
        if action == "get_tick_info" and r.get("paused") is True and self.alarm == "fired":
            self.paused, self.alarm, self._hold = False, "armed", True
            return dict(r, paused=False, pause_alarm={"state": "armed"})
        return r


def test_aligned_a_check_waits_for_the_pause_not_just_the_date(tmp_path):
    br = SlowPauseBridge()
    br.rate = 10
    env, br, _ = _env(tmp_path, bridge=br, task=_fast())
    n = len(br.read_paused)
    _, w = _step(env, {"max_days": 70, "when": [{"event": "claim_gained"}]})
    # two wake reads (claims, wars) at the step's start reading and at each of the 10 checks: every one paused
    assert w["probe"]["checks_paused"] == 10 and len(br.read_paused) - n == 2 * 11
    assert all(br.read_paused[n:]), br.read_paused[n:]


def test_CONTROL_polled_at_the_same_clock_lands_off_the_dates(tmp_path):
    """Non-discriminating on the aligned code: the same clock with the checks polled (speed 3, no declaration)
    checks every ~10 days, not every 7, and records no check_dates: the aligned test above can fail."""
    br = WakeBridge()
    br.rate = 10
    buf = io.StringIO()
    env, br, _ = _env(tmp_path, bridge=br, task=_fast(speed=3, aligned=False), buf=buf)
    _, w = _step(env, {"max_days": 70, "when": [NEVER]})
    assert "check_dates" not in _steps(buf)[-1]["wake"]
    assert w["probe"]["checks_running"] < 9


def test_aligned_a_condition_is_caught_at_the_first_check_date_after_it_holds(tmp_path):
    br = WakeBridge()
    br.rate = 10
    br.at["1066.10.16"] = _claim(77)              # between the checks of 10.13 and 10.20
    buf = io.StringIO()
    env, br, _ = _env(tmp_path, bridge=br, task=_fast(), buf=buf)
    obs, w = _step(env, {"max_days": 365, "when": [{"event": "claim_gained"}]})
    p = w["probe"]
    assert w["stop"] == "condition" and obs["game_date"] == "1066.10.20" and w["days"] == 35
    assert p["fire_read_date"] == p["stop_date"] == "1066.10.20" and p["lag_days"] == 0 and p["torn"] == 0
    assert _steps(buf)[-1]["wake"]["check_dates"]["rows"][-1] == {"intended": "1066.10.20", "read": "1066.10.20",
                                                                  "off": 0}


def test_aligned_a_late_stop_is_a_mismatch_the_checker_names_and_the_schedule_holds(tmp_path):
    br = LateBridge()
    br.rate = 10
    br.late["1066.10.06"] = 1                      # the third check's alarm fires a day late
    buf = io.StringIO()
    env, br, _ = _env(tmp_path, bridge=br, task=_fast(), buf=buf)
    _, w = _step(env, {"max_days": 70, "when": [NEVER]})
    cd = _steps(buf)[-1]["wake"]["check_dates"]
    assert cd["status"] == "mismatch" and cd["off"] == {"0": 9, "1": 1}
    assert cd["rows"][2] == {"intended": "1066.10.06", "read": "1066.10.07", "off": 1}
    assert [r["intended"] for r in cd["rows"]] == _sched(10)       # the next check is back on its date
    env.close()
    probs = check_trajectory([json.loads(x) for x in buf.getvalue().splitlines()])
    assert any("check" in p and "1066.10.06" in p and "1066.10.07" in p for p in probs), probs


class TimedBridge(WakeBridge):
    """WakeBridge whose clock runs `gdps` game days per (fake host) wall second while unpaused."""

    def __init__(self, gdps, **kw):
        super().__init__(**kw)
        self.gdps, self.host, self._t, self._acc = gdps, None, 0.0, 0.0

    def call(self, action, params=None, timeout=8.0):
        if self.host is not None and action == "unpause_game":
            self._t, self._acc = self.host.t, 0.0
        if self.host is not None and action == "get_tick_info":
            run = self._acc + (self.host.t - self._t) * self.gdps
            self.rate, self._acc, self._t = int(run), run - int(run), self.host.t
        return super().call(action, params, timeout)


def test_aligned_the_wait_shortens_to_the_measured_clock(tmp_path):
    """A stop is not left unread for a whole poll: once the loop has seen the clock run, the wait before a read
    is just under the time the clock needs to reach the alarm. 70 days at 20 gd/s are 3.5 s of clock; ten
    stops each read a whole poll late would make it 10 s."""
    from ck3bench.env.ck3 import ALIGNED_NAP_MIN_S
    br = TimedBridge(20.0)
    env, br, _ = _env(tmp_path, bridge=br, task=_fast())
    br.host = env.backend.host
    t0 = env.backend.host.monotonic()
    _, w = _step(env, {"max_days": 70, "when": [NEVER]})
    wall = env.backend.host.monotonic() - t0
    assert w["probe"]["checks_paused"] == 10 and 3.5 <= wall < 5.5, wall
    b = env.backend
    b._aligned_rate = 20.0
    assert b._aligned_nap(0, 7) == pytest.approx(0.28) and b._aligned_nap(0, 0) == ALIGNED_NAP_MIN_S
    assert b._aligned_nap(0, 365) == b.poll_s


def test_aligned_the_realm_read_keeps_its_sub_cadence_and_is_read_at_the_end(tmp_path):
    br = WakeBridge()
    br.rate = 10
    env, br, _ = _env(tmp_path, bridge=br, task=_fast())
    before = sum(1 for a, _ in br.calls if a == "query_realm")
    _, w = _step(env, {"max_days": 70, "when": [{"event": "realm_changed"}]})
    realm = sum(1 for a, _ in br.calls if a == "query_realm") - before
    # the step's start reading, then of 10 checks the 1st, 5th and 9th before the end, and the end (the 10th)
    assert w["probe"]["checks_paused"] == 10 and realm == 1 + 4


def test_aligned_a_late_stop_at_the_step_end_is_a_truncation_as_before(tmp_path):
    br = LateBridge()
    br.rate = 10
    br.late[calendar.add_days(START, 70)] = 1
    buf = io.StringIO()
    env, br, _ = _env(tmp_path, bridge=br, task=_fast(), buf=buf)
    env.step({"dublin": []}, wake={"dublin": {"max_days": 70, "when": [NEVER]}})
    rec = _steps(buf)[-1]
    assert rec["advance"]["truncation"] == "clock_stalled" and rec["wake"]["check_dates"]["status"] == "mismatch"


# ── the trajectory checker recomputes; it does not trust the status ────────────────────────────────────────────

def _aligned_records():
    """The fake wake task with the speed declared as the command line declares it, seats asking a condition."""
    buf = io.StringIO()
    env = ParallelEnv(cli._scenario(type("A", (), {"scenario": "fake-kingdom-wake", "game_speed": 5})()),
                      trajectory=TrajectoryWriter(stream=buf))
    env.reset(seed=5)
    for _ in range(3):
        env.step({s: [] for s in env.agents}, wake={"count": {"max_days": 40, "when": [NEVER]}})
    env.close()
    return [json.loads(x) for x in buf.getvalue().splitlines()]


def test_the_checker_names_a_forged_agreed_and_an_absent_witness():
    recs = _aligned_records()
    assert check_trajectory(recs) == []
    assert [r["wake"]["check_dates"]["checks"] for r in recs if r["kind"] == "step"] == [40, 40, 40]
    forged = copy.deepcopy(recs)
    s = next(r for r in forged if r["kind"] == "step")
    s["wake"]["check_dates"]["rows"][0]["read"] = calendar.add_days(s["wake"]["check_dates"]["rows"][0]["read"], 1)
    assert any("check" in p for p in check_trajectory(forged))       # the status still says agreed
    told = copy.deepcopy(recs)            # rows untouched, the status rewritten: only the recomputation sees it
    next(r for r in told if r["kind"] == "step")["wake"]["check_dates"]["status"] = "unwitnessed"
    assert any("says 'unwitnessed' and its rows read 'agreed'" in p for p in check_trajectory(told))
    absent = copy.deepcopy(recs)
    next(r for r in absent if r["kind"] == "step")["wake"].pop("check_dates")
    assert any("check_dates" in p for p in check_trajectory(absent))
    # and the witness on a task whose checks are not aligned is named too
    stray = copy.deepcopy(recs)
    stray[0]["scenario"]["wake"].pop("aligned_checks")
    assert any("not aligned" in p for p in check_trajectory(stray))


# ── the fake backend: aligned differs from polled only by the declaration and the witness ───────────────────────

def _fake_run(aligned):
    d = copy.deepcopy(BUILTIN_DICTS["fake-kingdom-wake"])
    if aligned:
        d["wake"]["aligned_checks"] = True
    buf = io.StringIO()
    env = ParallelEnv(scenario_from_dict(d), trajectory=TrajectoryWriter(stream=buf))
    env.reset(seed=5)
    while env.agents:
        env.step({s: [] for s in env.agents}, wake={"count": {"max_days": 40, "when": [
            {"metric": "gold", "op": ">=", "value": 104}]}})
    env.close()
    out = []
    for x in buf.getvalue().splitlines():
        r = json.loads(x)
        for k in ("episode_id", "wall_utc", "wall_s"):
            r.pop(k, None)
        if isinstance(r.get("advance"), dict):
            r["advance"].pop("wall_s", None)
        out.append(r)
    return out


def test_the_fake_aligned_run_is_the_polled_run_plus_the_witness():
    a, p = _fake_run(True), _fake_run(False)
    assert len(a) == len(p)
    for ra, rp in zip(a, p):
        if ra["kind"] == "header":
            assert ra["scenario"]["wake"].pop("aligned_checks") is True
        if ra["kind"] == "step":
            assert ra["wake"].pop("check_dates")["status"] == "agreed"
        assert ra == rp


# ── off = the checks without the rule, byte for byte ──────────────────────────────────────────────────────────

def _step(env, req):
    obs, *_ = env.step({"dublin": []}, wake={"dublin": req})
    return obs["dublin"], env._last_wake
