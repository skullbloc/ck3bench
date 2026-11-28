"""The runner end to end on the fake backend: the real `env play`, the real shim, a stand-in agent CLI (no model).

Needs the `mcp` library (the runner lists the shim's tools with a real MCP client); skipped without it.
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from ck3bench.harness import runner as R

TD = Path(__file__).with_name("testdata")
FAKE_CLI = TD / "fake_cli.py"
STANDIN = TD / "standin-ireland-fake.toml"
STANDIN_ONCE = TD / "standin-ireland-fake-wake-once.toml"
needs_mcp = pytest.mark.skipif(__import__("importlib").util.find_spec("mcp") is None,
                               reason="the mcp library is not installed (pip install mcp)")


def play_seat(out: Path, *extra, env=None, scenario=STANDIN):
    argv = [sys.executable, "-m", "ck3bench.harness", "run-seat", "--out", str(out), "--scenario", str(scenario),
            "--claude", f"{sys.executable} {FAKE_CLI}", "--turn-timeout-s", "60", "--open-wait-s", "60",
            "--session-env", "FAKE_CLAUDE_MODE", *extra]
    r = subprocess.run(argv, env={**os.environ, **(env or {})}, capture_output=True, text=True, timeout=300)
    return r, json.loads((out / "end.json").read_text())


def rows(p: Path):
    return [json.loads(x) for x in p.read_text().splitlines() if x.strip()]


@needs_mcp
def test_an_episode_plays_to_its_horizon_one_fresh_session_per_turn(tmp_path):
    r, end = play_seat(tmp_path / "run")
    assert r.returncode == 0 and end["end_reason"] == "episode_over", (end, r.stderr[-400:])
    turns = rows(tmp_path / "run" / "turns.jsonl")
    assert len(turns) == 15 and end["sessions"] == 15 and all(t["end_turn_called"] for t in turns)
    assert [t["session"] for t in turns] == list(range(1, 16))
    files = {p.name for p in (tmp_path / "run").iterdir()}
    assert {"trajectory.jsonl", "transcript.jsonl", "turns.jsonl", "end.json", "mcp.json", "tool_listing.json",
            "session_init.json", "session_argv.json", "seat_prompt.rendered.md", "runner.log"} <= files
    init = json.loads((tmp_path / "run" / "session_init.json").read_text())
    assert init["ok"] and all(t.startswith(R.TOOL_PREFIX) for t in init["tools"])


@needs_mcp
def test_a_session_that_exits_mid_turn_ends_the_episode(tmp_path):
    r, end = play_seat(tmp_path / "run", env={"FAKE_CLAUDE_MODE": "exit_at:2"})
    assert end["end_reason"] == "seat_exited" and r.returncode == 1


@needs_mcp
def test_a_reply_without_end_turn_gets_one_reminder_in_its_session(tmp_path):
    r, end = play_seat(tmp_path / "run", env={"FAKE_CLAUDE_MODE": "stop_at:3"})
    turns = rows(tmp_path / "run" / "turns.jsonl")
    assert [t["reminded"] for t in turns[:4]] == [False, False, True, True]
    assert all(t["end_turn_called"] for t in turns)      # this stand-in answers the reminder


def test_the_noop_policy_plays_the_game_s_ai_alone_with_no_session(tmp_path):
    r, end = play_seat(tmp_path / "run", "--policy", "noop", scenario=STANDIN_ONCE)
    assert r.returncode == 0 and end["end_reason"] == "episode_over", (end, r.stderr[-400:])
    assert end["turn_mode"] == "policy" and end["policy"] == "noop" and end["sessions"] == 0
    assert end["play_last_line"]["seats"] == {"dublin": "noop"}
    assert (tmp_path / "run" / "seat_prompt.rendered.md").read_text().startswith("You are playing one seat")


def test_a_task_the_prompt_cannot_describe_costs_no_game(tmp_path):
    r, end = play_seat(tmp_path / "run", "--seat", "count", scenario="fake-kingdom")
    assert end["end_reason"].startswith("setup_failed: seat prompt:") and end["play_rc"] is None


def test_seat_surface_declares_every_switch():
    a = SimpleNamespace(turn_mode="fresh", **{**R.SURFACE_OFF, "obs_delta": "on"})
    assert R.seat_surface(a) == {**R.SURFACE_OFF, "obs_delta": "on", "turn_mode": "fresh"}


def test_the_delta_keeps_what_the_turn_is_for_and_lists_what_went():
    prev = {"character_id": 1, "game_date": "1066.10.01", "world": {"news": {"a": 1, "b": 2}}, "metrics": {"x": 1}}
    cur = {"character_id": 1, "game_date": "1066.11.01", "world": {"news": {"a": 1}}, "metrics": {"x": 1}}
    sent, same = R.obs_delta(prev, cur)
    assert sent["game_date"] == "1066.11.01" and sent["metrics"] == {"x": 1} and "world.news.a" in same
    assert sent["gone"] == ["world.news.b"]
    assert R.obs_delta(prev, {**cur, "character_id": 2}) == ({**cur, "character_id": 2}, [])


def test_the_turn_lines_take_the_goal_and_orders_out_of_the_observation():
    obs = {"world": {"goal": "create k_x", "news": {}}, "orders_standing": ["o1"]}
    lines = R.turn_lines(obs, {"unwritten": "your notes were over the bound"})
    assert lines.startswith("GOAL: create k_x\n\nSTANDING ORDERS") and "NOTE: your notes were over" in lines
    assert "goal" not in obs["world"] and "orders_standing" not in obs
    assert R.numbered_pad("a\nb") == "1|a\n2|b" and R.pad_size({"bytes": 10, "max_bytes": 4096}) == \
        "10 of 4096 bytes, 4086 left"


def test_an_in_process_run_leaves_the_caller_s_signals_and_umask_as_they_were(tmp_path):
    """A run in the caller's process must not leave SIGTERM ignored: every child the caller starts later would
    inherit the ignored signal and could not be stopped."""
    import signal
    before = {s: signal.getsignal(s) for s in (signal.SIGTERM, signal.SIGINT)}
    um = os.umask(0o022)
    os.umask(um)
    assert R.main(["--out", str(tmp_path / "run"), "--scenario", str(STANDIN_ONCE), "--policy", "noop"]) == 0
    assert {s: signal.getsignal(s) for s in before} == before
    assert os.umask(um) == um


@needs_mcp
def test_every_line_sent_to_a_session_is_recorded_as_the_session_read_it(tmp_path):
    """session_stdin.jsonl against an independent witness: a wrapper in front of the stand-in CLI copies the bytes the
    session read on its stdin. They must be byte-equal (each turn's message and each reminder, in order), every line
    a `user` message, and a value planted in the runner's environment (a credential's place) must not be in it."""
    seen = tmp_path / "seen.jsonl"
    wrap = tmp_path / "cli-wrap"
    wrap.write_text(f"#!/bin/bash\nexec {sys.executable} {FAKE_CLI} \"$@\" < <(tee -a {seen})\n")
    wrap.chmod(0o755)
    out = tmp_path / "run"
    argv = [sys.executable, "-m", "ck3bench.harness", "run-seat", "--out", str(out), "--scenario", str(STANDIN),
            "--claude", str(wrap), "--turn-timeout-s", "60", "--open-wait-s", "60", "--session-env", "FAKE_CLAUDE_MODE"]
    planted = "planted-credential-6f1d2c"
    subprocess.run(argv, env={**os.environ, "FAKE_CLAUDE_MODE": "stop_at:3", "ANTHROPIC_API_KEY": planted},
                   capture_output=True, text=True, timeout=300)
    rec = (out / "session_stdin.jsonl").read_bytes()
    assert rec and rec == seen.read_bytes()
    msgs = [json.loads(x) for x in rec.decode().splitlines()]
    turns = rows(out / "turns.jsonl")
    assert len(msgs) == len(turns) + sum(t["reminded"] for t in turns)
    assert all(m["type"] == "user" and set(m) == {"type", "message"} for m in msgs)
    firsts = [m["message"]["content"] for m in msgs if m["message"]["content"].startswith("Turn ")]
    assert [int(c.split(":")[0].split()[1]) for c in firsts] == [t["turn"] for t in turns]
    assert any(m["message"]["content"].startswith("Your turn is still open") for m in msgs)
    assert planted.encode() not in rec
    assert (out / "session_stdin.jsonl").stat().st_mode & 0o077 == 0


def test_a_long_line_goes_into_the_stdin_record_whole_beside_another_appender(tmp_path):
    """The session's stdin record has one writer, the runner, and each line is one write(2): two processes appending
    lines far over a pipe buffer (8,192 bytes) to the same file leave every line whole, none inside another."""
    rec = tmp_path / "session_stdin.jsonl"
    prog = ("import json,os,sys; from ck3bench.harness import runner as R\n"
            "fd = os.open(sys.argv[1], os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)\n"
            "for i in range(40):\n"
            "    R.append_line(fd, (json.dumps({'w': sys.argv[2], 'i': i, 'pad': sys.argv[2] * 70000}) + '\\n').encode())\n")
    env = {**os.environ, "PYTHONPATH": str(Path(R.__file__).resolve().parents[2])}
    ps = [subprocess.Popen([sys.executable, "-c", prog, str(rec), w], env=env) for w in "ab"]
    assert [p.wait(timeout=120) for p in ps] == [0, 0]
    rows = [json.loads(x) for x in rec.read_text().splitlines()]
    assert sorted((r["w"], r["i"]) for r in rows) == sorted((w, i) for w in "ab" for i in range(40))
    assert all(r["pad"] == r["w"] * 70000 for r in rows)


def test_a_short_write_is_finished_not_dropped(monkeypatch):
    got, calls = bytearray(), []

    def short(fd, data):
        n = min(len(data), 5)
        calls.append(n)
        got.extend(data[:n])
        return n
    monkeypatch.setattr(R.os, "write", short)
    R.append_line(3, b"a short line.\n")
    assert bytes(got) == b"a short line.\n" and calls == [5, 5, 4]


@needs_mcp
def test_two_signals_at_once_mid_episode_still_close_the_game_and_write_the_end(tmp_path):
    """A driver stopped while its runner plays sends the runner's process group a TERM and then the runner its own:
    two signals with no gap. The first stops the run, the second is ignored, so the clean end runs whole: end.json is
    final (`stopped_by_signal`), and `env play` (which runs in a session of its own) has exited."""
    import re
    import signal
    import time
    out = tmp_path / "run"
    argv = [sys.executable, "-m", "ck3bench.harness", "run-seat", "--out", str(out), "--scenario", str(STANDIN),
            "--claude", f"{sys.executable} {FAKE_CLI}", "--turn-timeout-s", "60", "--open-wait-s", "60"]
    p = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)
    try:
        t = time.monotonic()
        while time.monotonic() - t < 240 and p.poll() is None:
            if (out / "turns.jsonl").is_file() and len(rows(out / "turns.jsonl")) >= 2:
                break
            time.sleep(0.2)
        assert p.poll() is None, "the episode ended before the signals"
        os.kill(p.pid, signal.SIGTERM)
        os.kill(p.pid, signal.SIGTERM)
        p.wait(240)
    finally:
        if p.poll() is None:
            os.killpg(p.pid, signal.SIGKILL)
            p.wait(30)
    end = json.loads((out / "end.json").read_text())
    assert end["final"] is True and end["end_reason"].startswith("stopped_by_signal"), end
    assert end["play_rc"] is not None
    play = int(re.search(r"env play pid (\d+)", (out / "runner.log").read_text()).group(1))
    assert not Path(f"/proc/{play}").exists() or "zombie" in Path(f"/proc/{play}/status").read_text().lower()


def test_MUTANT_a_handler_that_raises_again_is_interrupted_in_its_own_clean_end():
    """A handler that raises on every signal: a second signal landing while the first one's interrupt is handled
    raises again, out of the runner's `finally`, before its clean end. runner.stop ignores every signal after the
    first, so the same second signal raises nothing."""
    import signal
    old = {s: signal.getsignal(s) for s in (signal.SIGTERM, signal.SIGINT)}

    def raises_again(signum, _frame):
        raise KeyboardInterrupt(signal.Signals(signum).name)

    def twice() -> str:
        try:
            os.kill(os.getpid(), signal.SIGTERM)      # the first signal, mid-run
        except KeyboardInterrupt:
            try:
                os.kill(os.getpid(), signal.SIGTERM)  # the second, while the first is handled
            except KeyboardInterrupt:
                return "interrupted again"
            return "clean end"
        return "no interrupt"
    try:
        signal.signal(signal.SIGTERM, raises_again)
        assert twice() == "interrupted again"
        signal.signal(signal.SIGTERM, R.stop)
        assert twice() == "clean end"
        assert signal.getsignal(signal.SIGTERM) is signal.SIG_IGN and signal.getsignal(signal.SIGINT) is signal.SIG_IGN
    finally:
        for sig, h in old.items():
            signal.signal(sig, h)


# ── the episode's end row against the wall budget, the same in both arms ─────────────────────────────────────────

def _stamp(t: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))


def _run_in(tmp_path, *, end_row, policy):
    """A Run whose wall-budget deadline (its start + 100 s) passed 100 s ago, with the env's end row in its trajectory
    stamped 50 s before the deadline (`in_time`), 50 s after it (`late`), or no end row (None); `env play` a process
    that is still alive (its post-episode hold)."""
    a = SimpleNamespace(out=str(tmp_path / "run"), wall_budget_s=100.0, policy=policy, prompt=None, seat="dublin")
    run = R.Run(a)
    run.t0 -= 200.0
    run.t0_wall -= 200.0
    deadline = run.t0_wall + 100.0
    row = {"kind": "end", "reason": "horizon", "steps": 15,
           "wall_utc": _stamp(deadline + {"in_time": -50.0, "late": 50.0}.get(end_row, 0.0))}
    lines = [json.dumps({"kind": "header"})] + ([json.dumps(row)] if end_row else [])
    (run.out / "trajectory.jsonl").write_text("\n".join(lines) + "\n")
    run.play = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    run.prompt = ""
    return run


@pytest.mark.parametrize("arm", ("seated", "policy"))
@pytest.mark.parametrize("end_row", ("in_time", "late", None))
def test_the_end_row_s_own_time_decides_the_wall_budget_in_either_arm(tmp_path, arm, end_row):
    """A policy run's `env play` lives on through the post-episode hold; a seated run's runner reads the end row at
    the top of its next turn, however long the last turn took. Both judge the end row by its own time against the
    deadline: one stamped before it ends `episode_over` in both arms, the budget spent in the hold; one stamped after
    it (a seated turn begun before the deadline and played past it, say) ends `wall_budget` in both; with no end row
    both end `wall_budget` (the control)."""
    run = _run_in(tmp_path, end_row=end_row, policy="noop" if arm == "policy" else None)
    try:
        if arm == "policy":
            run.start_play = lambda: None
            run.run_policy()
        else:
            run.client = SimpleNamespace(state=lambda: {"open": True, "agents": ["dublin"]})
            run.run_fresh(Path("unused"), [])
        assert run.end_reason == ("episode_over" if end_row == "in_time" else "wall_budget"), (arm, run.end_reason)
        if end_row:
            assert run.budget_judged["by"] == "end_row" and run.budget_judged["end_row_wall_utc"]
    finally:
        run.play.kill()
        run.play.wait(30)


def test_CONTROL_the_end_row_s_second_and_the_deadline_s_second_judge_as_after():
    """The env stamps its rows to the second: a row in the deadline's own second is judged after it, in both arms (a
    policy checked each second sees no row before the deadline and ends `wall_budget` then)."""
    a = SimpleNamespace(out="/nonexistent-run-dir", wall_budget_s=100.0)
    run = R.Run.__new__(R.Run)
    run.a, run.out, run.t0_wall = a, Path("/nonexistent-run-dir"), 1_000_000.5
    import ck3bench.harness.score as S
    for stamp, want in ((1_000_099.0, "episode_over"), (1_000_100.0, "wall_budget"), (1_000_101.0, "wall_budget")):
        orig = S.trajectory_end_row
        S.trajectory_end_row = lambda d, st=stamp: {"kind": "end", "reason": "horizon", "wall_utc": _stamp(st)}
        try:
            assert run.end_by_budget() == want, (stamp, want)
        finally:
            S.trajectory_end_row = orig


def test_the_control_arm_with_a_hold_longer_than_its_budget_is_episode_over_end_to_end(tmp_path):
    """The real `env play` on the stand-in, noop policy, a 40 s hold and a 15 s wall budget: the end row lands in
    the first seconds, the budget runs out inside the hold, and the end is `episode_over`; the
    budget still bounds the run, so the hold is cut (recorded) and `env play` closed. CONTROL: with a budget past the
    hold, the hold is whole and `env play` closes by itself."""
    r, end = play_seat(tmp_path / "run", "--policy", "noop", "--hold-before-close-s", "40", "--wall-budget-s", "15",
                       scenario=STANDIN_ONCE)
    assert end["end_reason"] == "episode_over" and r.returncode == 0, (end, r.stderr[-400:])
    assert end["wall_s"] < 15, "the end row came after the budget: the case is not the one under test"
    assert end["hold_cut_by_wall_budget"] is True and "play_killed" not in end
    r, end = play_seat(tmp_path / "run2", "--policy", "noop", "--hold-before-close-s", "5", "--wall-budget-s", "300",
                       scenario=STANDIN_ONCE)
    assert end["end_reason"] == "episode_over" and "hold_cut_by_wall_budget" not in end and end["play_rc"] == 0
