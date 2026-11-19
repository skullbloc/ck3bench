"""`ck3bench env serve` and `env play` close the environment when they are
stopped by SIGTERM or SIGINT, in the foreground and as a background job.

Closing is what kills the game the real backend launched and removes the save
copy it staged, so a server stopped without it would leave a game running.
Each case runs the real CLI in a real child process on the fake backend, whose
close (patched in the child only) writes a witness file; the child is signalled
by the pid recorded when it started, after its /proc exe is checked. A
background job of a non-interactive shell starts with SIGINT ignored (POSIX),
and a Python started that way installs no KeyboardInterrupt handler: the
background cases check that SIGINT is in the child's ignored set before they
signal it, so they test the case they name.
"""
from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from ck3bench.env.trajectory import read_trajectory

pytestmark = pytest.mark.skipif(not Path("/proc/self/status").is_file() or shutil.which("sh") is None,
                                reason="needs /proc and a POSIX sh")

REPO = Path(__file__).resolve().parents[2]
WAIT_S = 30.0

#: the child: the real CLI, with FakeBackend.close writing a witness after it closes
CHILD = """
import os, sys
from pathlib import Path
witness = Path(sys.argv[1])
# the ignored-signal mask this process started with, before the CLI sets its handlers
status = Path("/proc/self/status").read_text().splitlines()
witness.with_name("sigign").write_text([l for l in status if l.startswith("SigIgn:")][0].split()[1])
from ck3bench.env import cli, fake
_close = fake.FakeBackend.close
def close(self):
    _close(self)
    with witness.open("a") as f:
        f.write(f"closed {os.getpid()}\\n")
fake.FakeBackend.close = close
sys.exit(cli.main(sys.argv[2:]))
"""

COMMANDS = {
    "serve": ["serve", "fake-solo", "--port", "0", "--turn-timeout-s", "600"],
    # an external seat nobody drives: the episode waits in its first step until stopped
    "play": ["play", "fake-council", "--seed", "1", "--seat", "duke1=noop", "--seat", "duke2=noop",
             "--external", "king", "--turn-timeout-s", "600"],
}


def _wait(pred, what, timeout=WAIT_S):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        v = pred()
        if v:
            return v
        time.sleep(0.05)
    raise AssertionError(f"timed out after {timeout:g} s waiting for {what}")


def _ignored_at_start(tmp_path: Path) -> int:
    """The child's SigIgn mask as it started (it writes it before importing the CLI)."""
    return int((tmp_path / "sigign").read_text(), 16)


def _start(tmp_path: Path, cmd: str, background: bool):
    script = tmp_path / "child.py"
    script.write_text(CHILD)
    witness, out, pidfile = tmp_path / "witness", tmp_path / "out.jsonl", tmp_path / "child.pid"
    traj = tmp_path / "t.jsonl"
    argv = [sys.executable, str(script), str(witness), *COMMANDS[cmd], "--out", str(traj)]
    env = {**os.environ, "PYTHONPATH": str(REPO) + os.pathsep + os.environ.get("PYTHONPATH", "")}
    with out.open("w") as fh:
        if background:
            # `CMD &` in a non-interactive sh: the job starts with SIGINT and SIGQUIT ignored;
            # sh records its pid and waits for it
            shell = 'pidfile="$1"; shift; "$@" & echo "$!" > "$pidfile"; wait "$!"'
            p = subprocess.Popen(["sh", "-c", shell, "sh", str(pidfile), *argv], stdout=fh,
                                 stderr=subprocess.STDOUT, env=env, cwd=tmp_path, start_new_session=True)
            pid = int(_wait(lambda: pidfile.is_file() and pidfile.read_text().strip(), "the job's pid"))
        else:
            p = subprocess.Popen(argv, stdout=fh, stderr=subprocess.STDOUT, env=env, cwd=tmp_path,
                                 start_new_session=True)
            pid = p.pid
    return p, pid, witness, out, traj


def _first_line(out: Path):
    def ready():
        lines = [ln for ln in out.read_text().splitlines() if ln.startswith("{")]
        return json.loads(lines[0]) if lines else None
    return _wait(ready, "the ready line")


def _kill_leftover(p, pid: int) -> None:
    exe = Path(f"/proc/{pid}/exe")
    try:
        same = os.path.realpath(exe) == os.path.realpath(sys.executable)
    except OSError:
        same = False
    if same:
        os.kill(pid, signal.SIGKILL)
    if p.poll() is None:
        p.kill()
        p.wait(10)


@pytest.mark.parametrize("background", [False, True], ids=["foreground", "background"])
@pytest.mark.parametrize("signum", [signal.SIGTERM, signal.SIGINT], ids=["SIGTERM", "SIGINT"])
@pytest.mark.parametrize("cmd", sorted(COMMANDS))
def test_a_signal_closes_the_environment(tmp_path, cmd, signum, background):
    p, pid, witness, out, traj = _start(tmp_path, cmd, background)
    try:
        ready = _first_line(out)
        assert ready["ok"] is True, ready
        if cmd == "play":
            # the episode is open (the runner reset it) before the signal
            _wait(lambda: traj.is_file() and '"kind": "header"' in traj.read_text(), "the trajectory header")
        exe = os.path.realpath(f"/proc/{pid}/exe")
        assert exe == os.path.realpath(sys.executable), f"pid {pid} is {exe}, not the child"
        if background:
            assert _ignored_at_start(tmp_path) & (1 << (signal.SIGINT - 1)), "the job did not start with SIGINT ignored"
        else:
            assert not _ignored_at_start(tmp_path) & (1 << (signal.SIGINT - 1))
        assert not witness.exists()
        os.kill(pid, signum)
        _wait(lambda: witness.is_file(), "the close witness", timeout=20)
        rc = p.wait(WAIT_S)
    finally:
        _kill_leftover(p, pid)
    assert witness.read_text() == f"closed {pid}\n", "closed exactly once, by the signalled process"
    last = json.loads([ln for ln in out.read_text().splitlines() if ln.startswith("{")][-1])
    assert last["stopped_by"] == signal.Signals(signum).name and last["closed"] is True
    assert rc == (0 if cmd == "serve" else 1)
    if cmd == "play":
        recs = read_trajectory(traj)
        assert recs[-1]["kind"] == "end" and recs[-1]["reason"] == "closed"
