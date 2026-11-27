"""The reference series driver (`series run`, ck3bench.harness.driver) end to end on the fake backend: a whole series
of two attempts through the lock, the start identity, the sampler, the watcher, the runner, the end witnesses, the
sealed result, the ledger and the end marker, then the stop and the score; and a third attempt refused at the stop.
No game and no model: every game witness is absent and says so, so every attempt is invalid with its reasons, and
the series stops at its attempt cap.

The host is a stand-in a stranger can build: a clean git checkout holding the bench, a stand-in installed toolkit
distribution with the wheel of the same members, a file in place of the bridge and of the map, the python binary as
the game binary (an ELF with a build id) and as the agent CLI whose version is recorded.
"""
import json
import os
import subprocess
import stat
import sys
import time
import uuid
import zipfile
from pathlib import Path

import pytest

from ck3bench.harness import driver as D
from ck3bench.harness import score as S
from ck3bench.harness import series as SR

TD = Path(__file__).with_name("testdata")
BENCH = Path(__file__).resolve().parents[1]
STANDIN = TD / "standin-ireland-fake-wake-once.toml"
needs_mcp = pytest.mark.skipif(__import__("importlib").util.find_spec("mcp") is None,
                               reason="the mcp library is not installed (pip install mcp)")


def _host(tmp: Path) -> dict:
    if "build_id" not in SR.build_id(os.path.realpath(sys.executable)):
        pytest.skip("this python binary carries no ELF build id to stand in for the game's")
    clone = tmp / "clone"
    clone.mkdir()
    (clone / "ck3bench").symlink_to(BENCH)
    (clone / "standin_member").mkdir()
    (clone / "standin_member" / "data.txt").write_text("member\n")
    di = clone / "ck3agent-0.0.0.dist-info"
    di.mkdir()
    (di / "METADATA").write_text("Metadata-Version: 2.1\nName: ck3agent\nVersion: 0.0.0\n")
    (di / "RECORD").write_text("standin_member/data.txt,,\nck3agent-0.0.0.dist-info/METADATA,,\n")
    g = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
         "GIT_COMMITTER_EMAIL": "t@t"}
    for c in (["init", "-q"], ["add", "-A"], ["commit", "-qm", "frozen"]):
        subprocess.run(["git", "-C", str(clone), *c], check=True, env=g, capture_output=True, timeout=60)
    stage = tmp / "stage"
    stage.mkdir()
    (stage / "libck3_bridge.so").write_bytes(b"\x7fELF stand-in bridge")
    (stage / "ck3map.bin").write_bytes(b"CK3MAP\0\0")
    with zipfile.ZipFile(stage / "ck3agent-0.0.0-py3-none-any.whl", "w") as z:
        z.writestr("standin_member/data.txt", "member\n")
        z.writestr("ck3agent-0.0.0.dist-info/RECORD", "x")
    prot = tmp / "protected"
    prot.mkdir()
    (prot / "settings.txt").write_text("unchanged\n")
    env = {k: v for k, v in os.environ.items() if not k.startswith(("CK3_BRIDGE_", "RATE_", "CK3_", "FAKE_CLAUDE"))
           and k not in ("TURN_MODE", "NOTES", "SEAT_PROMPT", "RECORD", "VIEW", "CLEANMAP", "FULL_HOLD",
                         "KIT_REPO", "MAP_BIN", "KIT_INSTANCE")}
    env.update(PYTHONPATH=str(clone), KIT_REPO=str(clone), RATE_RUNS=str(tmp / "runs"), RATE_SCENARIO=str(STANDIN),
               RATE_SO=str(stage / "libck3_bridge.so"), MAP_BIN=str(stage / "ck3map.bin"),
               RATE_WHEEL=str(stage / "ck3agent-0.0.0-py3-none-any.whl"), RATE_GAME_BIN=os.path.realpath(sys.executable),
               RATE_CLAUDE_BIN=sys.executable, RATE_MODEL="stand-in", RATE_INSTANCE="demo", RATE_SAMPLE_S="1",
               RATE_PROTECTED_DIR=str(prot), TURN_MODE="fresh", NOTES="on", SEAT_PROMPT="default", RECORD="0", VIEW="",
               CLEANMAP="0", FULL_HOLD="600", XDG_STATE_HOME=str(tmp / "state"), RATE_LOCK_DIR=str(tmp / "lock"))
    return {"clone": clone, "env": env, "runs": tmp / "runs", "lock": tmp / "lock"}


def _harness(h: dict, *args, timeout=600):
    return subprocess.run([sys.executable, "-m", "ck3bench.harness", *args], env=h["env"], cwd=h["clone"],
                          capture_output=True, text=True, timeout=timeout)


def _declare(h: dict, tmp: Path) -> Path:
    """`series declare` on the staged stand-in, the three pre-flight values left out (they would be frozen from a
    check run; left out, they are not held)."""
    r = subprocess.run([sys.executable, "-m", "ck3bench.harness", "series", "declare"],
                       env={**h["env"], "RATE_GAME_SPEED": "3", "RATE_WALL_BUDGET_S": "15"}, cwd=h["clone"],
                       capture_output=True, text=True, timeout=600)
    # the speed the run declares, which aligns a wake task's checks; the stand-in's short wall budget, which a
    # series then holds
    assert r.returncode == 0, r.stdout + r.stderr
    d = json.loads(r.stdout)
    for k in ("notes_version", "tool_listing_sha256", "seat_model_reported"):
        assert "FREEZE" in d.pop(k)
    assert d["kit"] == {"TURN_MODE": "fresh", "NOTES": "on", "SEAT_PROMPT": "default", "RECORD": "0", "VIEW": "",
                        "CLEANMAP": "0"} and d["switches"] == {}
    assert (d["game_speed"], d["aligned_checks"]) == (3, True)
    assert d["timings"] == {**D.DEFAULTS, "wall_budget_s": 15.0}
    assert not [k for k, v in d.items() if isinstance(v, str) and "FREEZE" in v]     # every identity value read
    p = tmp / "expect.json"
    p.write_text(json.dumps(d, indent=1))
    return p


def unique_id(base: str) -> str:
    """A series or run id of this test alone: the stand-in game is a python process, which a driver reads as its
    run's game by the run id it carries, so two suites on one host (or a re-run while a game from an earlier one is
    still alive) never share one."""
    return f"{base}-{uuid.uuid4().hex[:10]}"


def _schedule(tmp: Path, sid: str) -> Path:
    p = tmp / "series.json"
    p.write_text(json.dumps({"schema": S.SERIES_SCHEMA, "series": sid, "task": str(STANDIN), "pattern": ["h1", "h2"],
                             "valid_target": 1, "attempt_cap": 2}))
    return p


def reap(out: Path) -> list[int]:
    """Kill what a run's runner started in sessions of their own (`env play`, each agent session) and is still alive:
    each by the pid the runner recorded in OUT/runner.log, and only while that process carries this run's
    RATE_RUN_ID (never by a name); waits, 30 s at most in all, until each has exited or is a zombie. The pids it
    killed."""
    import re
    import signal
    from ck3bench.harness.game_identity import environ
    try:
        log = (out / "runner.log").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    rid, killed = out.name, []
    for pid in sorted({int(x) for x in re.findall(r"(?:env play|session \d+) pid (\d+)", log)}):
        try:
            if environ(pid).get("RATE_RUN_ID") != rid:
                continue
            os.killpg(pid, signal.SIGKILL)
        except (OSError, ProcessLookupError):
            continue
        killed.append(pid)
    t = time.monotonic()
    for pid in killed:
        while Path(f"/proc/{pid}").exists() and "zombie" not in Path(f"/proc/{pid}/status").read_text(
                errors="replace").lower() and time.monotonic() - t < 30:
            time.sleep(0.05)
    return killed


def test_a_whole_series_on_the_fake_backend_runs_stops_and_scores(tmp_path):
    h = _host(tmp_path)
    sid = unique_id("demo")
    exp, ser = _declare(h, tmp_path), _schedule(tmp_path, sid)
    common = ["series", "run", "--series", str(ser), "--expect", str(exp), "--policy", "noop", "--wall-budget-s", "15"]
    r = _harness(h, *common, "--host", "h1", "--dry-run")
    assert r.returncode == 0 and f"{sid}-a01-h1" in r.stdout and not h["runs"].exists(), r.stdout + r.stderr
    dirs = []
    for host, rid in (("h1", f"{sid}-a01-h1"), ("h2", f"{sid}-a02-h2")):
        r = _harness(h, *common, "--host", host)
        out = h["runs"] / rid
        assert not reap(out)                     # a run played whole leaves nothing of its own alive
        assert r.returncode == 0, r.stdout + r.stderr
        dirs.append(out)
        files = {p.name for p in out.iterdir()}
        assert {"run.json", "trajectory.jsonl", "end.json", "done", D.END_MARKER, "pressure.tsv", "post.tsv",
                "own.before.txt", "own.before.list", "own.after.sha", "ck3.before", "witness_start.json",
                "final_save.json", "result.json", "accounting.json",
                "seat_prompt.rendered.md"} <= files, sorted(files)
        rj = json.loads((out / "run.json").read_text())
        assert rj["start_reasons"] == [] and rj["series"]["attempt"] == S.parse_run_id(rid)["attempt"]
        assert rj["start"]["wheel"]["compare"]["verdict"] == "identical"
        reasons = rj["validity"]["reasons"]
        assert rj["validity"]["valid"] is False and "witness_absent:game_start" in reasons
        # the hold after the episode is cut by the budget, and the end stays the episode's own (no runner_end)
        assert not [x for x in reasons if x.startswith("runner_end:")], reasons
        assert json.loads((out / "end.json").read_text())["hold_cut_by_wall_budget"] is True
        # a --policy run starts no agent session: neither session witness is a reason, and each is said as such
        assert not [x for x in reasons if x in ("witness_absent:tool_listing", "witness_absent:session_init")], reasons
        assert rj["end"]["seen"]["tool_listing_sha256"] == rj["end"]["seen"]["seat_model_reported"] == SR.NO_SESSION
        res = json.loads((out / "result.json").read_text())
        assert res["success"] == SR.SEALED and res["owner_tree"]["verdict"] == "IDENTICAL"
        assert not set(res["row"]) & set(SR.OUTCOME_KEYS) and "end_row" in SR.OUTCOME_KEYS    # how it ended is one
        assert res["final_save"]["saved"] is False and "no final save" in res["final_save"]["reason"]
        steps = [ln.split("\t")[0] for ln in (out / "post.tsv").read_text().splitlines()]
        # the protected directory's after digest precedes `series end`, which holds the run to it
        assert steps == ["own_before", "run-seat", "accounting", "watcher", "analyze", "own_after", "end", "result"]
        assert rj["end"]["seen"]["owner_tree"] == "IDENTICAL"
        ps = (out / "pressure.tsv").read_text().splitlines()
        assert ps[0].split("\t") == list(D.PRESSURE_COLUMNS) and len(ps) >= 3
        assert (out / D.END_MARKER).read_text().startswith("rc=0 ")
        assert not h["lock"].exists()
    led = sorted((h["runs"] / f"{sid}.ledger").glob("attempt-*.json"))
    assert [json.loads(p.read_text())["ended"] for p in led] == [True, True]
    # the stop: the attempt cap; a third attempt is refused before the lock
    r = _harness(h, *common, "--host", "h1")
    assert r.returncode == 7 and "series_stopped" in r.stderr and not (h["runs"] / f"{sid}-a03-h1").exists()
    r = _harness(h, "score", *map(str, dirs), "--json")
    assert r.returncode == 0, r.stderr
    sc = json.loads(r.stdout)["score"]
    assert (sc["k"], sc["n"], sc["attempts"]) == (0, 0, 2) and len(sc["invalid"]) == 2
    r = _harness(h, "score", *map(str, dirs))
    lines = r.stdout.splitlines()
    assert lines[-2].startswith("k/n = 0/0 valid runs (2 attempts, 2 invalid)"), lines[-3:]
    assert lines[-1].startswith("sensitivity: k'/n' = 0/0 with the 0 attempts stopped after their end row")


def test_a_held_lock_and_an_existing_run_dir_are_refused_before_anything_starts(tmp_path):
    h = _host(tmp_path)
    exp = _declare(h, tmp_path)
    h["lock"].mkdir(parents=True)
    (h["lock"] / "holder").write_text("someone else\n")
    r = _harness(h, "series", "run", "--host", "h1", "--run-id", "x1", "--expect", str(exp), "--policy", "noop")
    assert r.returncode == 3 and "held: someone else" in r.stderr and not (h["runs"] / "x1").exists()
    assert "never taken over" in r.stderr and (h["lock"] / "holder").read_text() == "someone else\n"
    (h["lock"] / "holder").unlink()
    h["lock"].rmdir()
    h["runs"].mkdir()
    (h["runs"] / "x1").mkdir()
    r = _harness(h, "series", "run", "--host", "h1", "--run-id", "x1", "--expect", str(exp), "--policy", "noop")
    assert r.returncode == 3 and "exists" in r.stderr and not h["lock"].exists()


def test_a_kit_switch_the_driver_cannot_honour_is_refused(tmp_path):
    h = _host(tmp_path)
    exp = _declare(h, tmp_path)
    d = json.loads(exp.read_text())
    d["kit"]["RECORD"] = "1"
    exp.write_text(json.dumps(d))
    h["env"].pop("RECORD")
    r = _harness(h, "series", "run", "--host", "h1", "--run-id", "x2", "--expect", str(exp), "--policy", "noop")
    assert r.returncode == 5 and "kit_switch_unhonoured" in r.stderr and not h["runs"].exists()


@needs_mcp
def test_a_check_run_with_the_stand_in_agent_cli(tmp_path):
    """A seat played by an agent CLI: the runner's fresh sessions on the stand-in agent CLI (two turns, then it exits)."""
    h = _host(tmp_path)
    exp = _declare(h, tmp_path)
    d = json.loads(exp.read_text())
    d["kind"] = "check"
    exp.write_text(json.dumps(d))
    h["env"]["FAKE_CLAUDE_MODE"] = "exit_at:3"
    rid = unique_id("chk")
    r = _harness(h, "series", "run", "--host", "h1", "--run-id", rid, "--expect", str(exp), "--check",
                 "--agent-cli", f"{sys.executable} {TD / 'fake_cli.py'}", "--session-env", "FAKE_CLAUDE_MODE")
    out = h["runs"] / rid
    assert not reap(out)
    assert r.returncode == 0, r.stdout + r.stderr
    assert json.loads((out / "end.json").read_text())["end_reason"] == "seat_exited"
    res = json.loads((out / "result.json").read_text())
    assert res["kind"] == "check" and "runner_end:seat_exited" in res["invalid_reasons"]
    assert len((out / "turns.jsonl").read_text().splitlines()) == 2
    sent = [json.loads(x) for x in (out / "session_stdin.jsonl").read_text().splitlines()]
    assert len(sent) >= 2 and sent[0]["message"]["content"].startswith("Turn 1:")
    # the three values a check gives the expectation, where the docs say to read them
    seen = json.loads((out / "run.json").read_text())["end"]["seen"]
    assert {"notes_version", "tool_listing_sha256", "seat_model_reported"} <= set(seen)
    assert seen["tool_listing_sha256"] and seen["seat_model_reported"]


def test_the_sampler_writes_what_the_resource_rule_reads(tmp_path):
    """CONTROL: the built-in sampler's file is read by the scorer (state `read`), so a stop or hang is judged, not
    voided for absence; without the file the same window reads `absent`."""
    s = D.Sampler(tmp_path, 0.2)
    s.start()
    import time
    time.sleep(1.0)
    s.stop.set()
    s.join(5)
    rows = (tmp_path / "pressure.tsv").read_text().splitlines()
    assert len(rows) >= 3
    t = S._utc(rows[1].split("\t")[0])
    pr = S.pressure_evidence(tmp_path, (t, t + 1))
    any_psi = any(Path(f"/proc/pressure/{k}").is_file() for k in ("cpu", "memory", "io"))
    assert pr["state"] == ("read" if any_psi or Path("/proc/meminfo").is_file() else "unreadable"), pr
    (tmp_path / "pressure.tsv").unlink()
    assert S.pressure_evidence(tmp_path, (t, t + 1))["state"] == "absent"


def test_the_protected_digest_says_not_configured_and_sees_a_change(tmp_path):
    out = tmp_path / "run"
    out.mkdir()
    assert D.protected_digest(None, out / "own.before") == (0, "not configured")
    assert not (out / "own.before.list").exists()
    prot = tmp_path / "p"
    prot.mkdir()
    (prot / "a.txt").write_text("x")
    D.protected_digest(str(prot), out / "own.before")
    D.protected_digest(str(prot), out / "own.after")
    assert (out / "own.before.sha").read_bytes() == (out / "own.after.sha").read_bytes()
    (prot / "a.txt").write_text("y")
    D.protected_digest(str(prot), out / "own.after")
    assert (out / "own.before.sha").read_bytes() != (out / "own.after.sha").read_bytes()
    assert D.protected_digest(str(tmp_path), out / "own.x")[0] == 2     # the run dir inside it: refused


def test_the_default_lock_is_one_per_user_on_the_host_whatever_the_runs_directory(tmp_path):
    """Two drivers with different RATE_RUNS and no RATE_LOCK_DIR name one lock; the second is refused while the
    first holds it, and the first's holder line is left as it was."""
    import argparse
    a = argparse.Namespace()
    d1 = D.Driver.__new__(D.Driver)
    d2 = D.Driver.__new__(D.Driver)
    d1.env, d2.env = {"RATE_RUNS": str(tmp_path / "r1")}, {"RATE_RUNS": str(tmp_path / "r2")}
    d1.a = d2.a = a
    assert d1.lockdir() == d2.lockdir() == D.default_lock_dir()
    assert D.default_lock_dir().name == D.LOCK_NAME and str(os.getuid()) in str(D.default_lock_dir())
    # the same rule, taken for real on a stand-in location
    lock = tmp_path / "host.lock"
    for d in (d1, d2):
        d.env["RATE_LOCK_DIR"] = str(lock)
        d.lock, d.holder = None, ""
    d1.take_lock("a01")
    with pytest.raises(D.Refused) as e:
        d2.take_lock("a02")
    assert e.value.rc == 3 and "a01" in str(e.value) and "running" in str(e.value)
    assert d2.release_lock() == "no lock taken" and (lock / "holder").read_text().strip() == d1.holder
    assert d1.release_lock() == "lock released" and not lock.exists()


def test_the_default_lock_path_does_not_depend_on_a_session_and_its_parent_is_private(tmp_path, monkeypatch):
    """The default lock is one fixed path from the uid: the same whatever XDG_RUNTIME_DIR says and whether the user's
    runtime directory exists (a logout removes that directory, and a lock inside it with it). Its parent is made 0700,
    narrowed to 0700 when ours with wider bits, and refused when it is a link; taking the default lock goes through
    that parent (the real code path, on a stand-in root)."""
    import argparse
    uid = os.getuid()
    for xdg in ("/nonexistent-runtime", "", f"/run/user/{uid}"):
        monkeypatch.setenv("XDG_RUNTIME_DIR", xdg)
        assert D.default_lock_dir() == D.LOCK_ROOT / f"ck3bench-{uid}" / D.LOCK_NAME
    assert D.default_lock_dir(uid) == D.default_lock_dir(uid)
    assert D.LOCK_ROOT == Path("/tmp") and "/run/" not in str(D.default_lock_dir())
    monkeypatch.setattr(D, "LOCK_ROOT", tmp_path)
    drivers = []
    for i in (1, 2):
        d = D.Driver.__new__(D.Driver)
        d.env, d.a, d.lock, d.holder = {"RATE_RUNS": str(tmp_path / f"r{i}")}, argparse.Namespace(), None, ""
        drivers.append(d)
    d1, d2 = drivers
    d1.take_lock("a01")
    parent = tmp_path / f"ck3bench-{uid}"
    assert d1.lock == parent / D.LOCK_NAME and stat.S_IMODE(parent.stat().st_mode) == 0o700
    with pytest.raises(D.Refused) as e:
        d2.take_lock("a02")
    assert e.value.rc == 3 and "a01" in str(e.value)
    assert d1.release_lock() == "lock released" and parent.is_dir() and not (parent / D.LOCK_NAME).exists()
    parent.chmod(0o755)
    d2.take_lock("a02")
    assert stat.S_IMODE(parent.stat().st_mode) == 0o700 and d2.release_lock() == "lock released"
    # CONTROL: a parent that is a link is refused, and nothing is made through it
    parent.rmdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    parent.symlink_to(elsewhere)
    with pytest.raises(D.Refused) as e:
        d1.take_lock("a03")
    assert e.value.rc == 3 and "RATE_LOCK_DIR" in str(e.value) and not any(elsewhere.iterdir())


def test_a_task_on_the_game_without_a_display_is_refused_first(tmp_path):
    """The game inherits the driver's environment: a game task with no DISPLAY is refused (rc 2) naming it, before
    any step runs (no run dir, no lock); the fake backend needs none."""
    h = _host(tmp_path)
    env = {k: v for k, v in h["env"].items() if k not in ("DISPLAY", "WAYLAND_DISPLAY", "SDL_VIDEODRIVER",
                                                            "XAUTHORITY")}
    env["RATE_SCENARIO"] = "munster1066-kingdom-once-30y-wake"
    import time
    t = time.monotonic()
    r = subprocess.run([sys.executable, "-m", "ck3bench.harness", "series", "run", "--host", "h1", "--run-id", "g1",
                        "--policy", "noop"], env=env, cwd=h["clone"], capture_output=True, text=True, timeout=120)
    assert r.returncode == 2 and "game_env:DISPLAY is not set" in r.stderr, r.stderr
    assert time.monotonic() - t < 10 and not h["runs"].exists() and not h["lock"].exists()
    assert D.game_env_refusals({"DISPLAY": ":5"}) == []
    assert D.game_env_refusals({"SDL_VIDEODRIVER": "wayland", "DISPLAY": ":5"})[0].startswith(
        "REFUSE game_env:WAYLAND_DISPLAY")
    bad = D.game_env_refusals({"DISPLAY": ":5", "XAUTHORITY": str(tmp_path / "none"), "XDG_STATE_HOME": "rel",
                               "SDL_VIDEODRIVER": "kmsdrm"})
    assert [x.split(":")[1].split("=")[0].split(" ")[0] for x in bad] == ["SDL_VIDEODRIVER", "XAUTHORITY",
                                                                          "XDG_STATE_HOME"]
    env["DISPLAY"] = ":5"
    exp = _declare(h, tmp_path)
    r = subprocess.run([sys.executable, "-m", "ck3bench.harness", "series", "run", "--host", "h1", "--run-id", "g1",
                        "--policy", "noop", "--expect", str(exp), "--dry-run"], env=env, cwd=h["clone"], capture_output=True, text=True,
                       timeout=300)
    assert r.returncode == 0 and "game env: DISPLAY=:5 " in r.stdout, r.stdout + r.stderr


def test_the_protected_digest_is_the_find_pipeline_byte_for_byte(tmp_path):
    """CONTROL against the shell pipeline the docstring names: the same two files, so a digest taken that way
    compares with the driver's."""
    import shutil
    if not shutil.which("find") or subprocess.run(["find", "--version"], capture_output=True,
                                                  text=True).stdout.find("GNU") < 0:
        pytest.skip("GNU find is not installed")
    prot = tmp_path / "p"
    (prot / "sub dir").mkdir(parents=True)
    (prot / "a.txt").write_text("x")
    (prot / "sub dir" / "B.bin").write_bytes(b"\0\1")
    (prot / "Z").write_text("")
    (prot / "link").symlink_to("a.txt")
    out = tmp_path / "out"
    out.mkdir()
    rc, line = D.protected_digest(str(prot), out / "own")
    sh = ("find . -printf '%y %p %s %T@\\n' | LC_ALL=C sort > \"$1.list\" && "
          "find . -type f -print0 | LC_ALL=C sort -z | xargs -0 sha256sum > \"$1.sha\"")
    subprocess.run(["bash", "-c", sh, "x", str(out / "sh")], cwd=prot, check=True, timeout=60)
    assert (out / "own.list").read_bytes() == (out / "sh.list").read_bytes()
    assert (out / "own.sha").read_bytes() == (out / "sh.sha").read_bytes()
    import hashlib
    assert rc == 0 and f"files=3 list_sha={hashlib.sha256((out / 'sh.list').read_bytes()).hexdigest()[:16]}" in line


@pytest.mark.parametrize("verb,module", [("accounting", "accounting"), ("analyze", "analyze"), ("steps", "steps"),
                                         ("game-pid", "game_identity")])
def test_each_reader_verb_answers_help_and_refuses_no_argument(verb, module, tmp_path):
    """`--help` prints the reader's statement (rc 0) through the harness verb and the module alike, never a traceback
    reading `--help` as a run directory; no argument is a usage error (rc 2)."""
    pkg_root = str(Path(D.__file__).resolve().parents[2])          # the tree this ck3bench was imported from
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1",
           "PYTHONPATH": os.pathsep.join(x for x in (pkg_root, os.environ.get("PYTHONPATH")) if x)}
    first = __import__(f"ck3bench.harness.{module}", fromlist=["x"]).__doc__.strip().splitlines()[0]
    for argv in (["-m", "ck3bench.harness", verb, "--help"], ["-m", f"ck3bench.harness.{module}", "--help"]):
        r = subprocess.run([sys.executable, *argv], capture_output=True, text=True, timeout=60, cwd=tmp_path, env=env)
        assert r.returncode == 0 and "Traceback" not in r.stderr and first in r.stdout, (argv, r.stderr[-500:])
    r = subprocess.run([sys.executable, "-m", "ck3bench.harness", verb], capture_output=True, text=True, timeout=60,
                       cwd=tmp_path, env=env)
    assert r.returncode == 2 and "Traceback" not in r.stderr


#: a stand-in runner that ignores TERM (so the driver kills it) and starts a stand-in game in a session of its own,
#: which outlives it; it records the game as `series watch` does: its pid and its /proc/<pid>/exe
STANDIN_RUNNER = r"""
import json, os, signal, subprocess, sys, time
signal.signal(signal.SIGTERM, signal.SIG_IGN)
game = subprocess.Popen([sys.executable, "-c", "import sys, time; time.sleep(float(sys.argv[1]))", sys.argv[2]],
                        start_new_session=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL)
time.sleep(0.3)
w = {"pid": game.pid, "exe": os.readlink(f"/proc/{game.pid}/exe")}
open(os.path.join(sys.argv[1], "witness_start.json"), "w").write(json.dumps(w))
time.sleep(600)
"""


def _killed_runner(tmp_path, monkeypatch, game_s: float, exit_wait_s: float):
    """A driver whose runner is killed at its wall limit while its game lives on: (the driver, the lock, the game's
    recorded (pid, exe))."""
    import argparse
    import time
    monkeypatch.setattr(D, "TERM_WAIT_S", 1.0)
    monkeypatch.setattr(D, "GAME_EXIT_WAIT_S", exit_wait_s)
    out, lock = tmp_path / "run-a01", tmp_path / "lock"
    out.mkdir()
    d = D.Driver.__new__(D.Driver)
    d.a, d.env = argparse.Namespace(wall_s=1.0), {"RATE_LOCK_DIR": str(lock)}
    d.lock, d.holder, d.out, d.sampler, d.watcher, d.started = None, "", out, None, None, True
    d.ended = True                                        # its end steps completed: the lock is what is read
    d.take_lock("a01")
    script = tmp_path / "runner.py"
    script.write_text(STANDIN_RUNNER)
    runner = subprocess.Popen([sys.executable, str(script), str(out), str(game_s)], stdin=subprocess.DEVNULL)
    t = time.monotonic()
    while not D.recorded_games(out) and time.monotonic() - t < 20:
        time.sleep(0.1)
    games = D.recorded_games(out)
    assert len(games) == 1 and D.alive_as(*games[0])
    rc = d.wait_runner(runner)
    assert rc == -9, rc                                   # TERM ignored, then killed
    return d, lock, games[0], rc


def _end_game(pid: int, exe: str) -> None:
    import signal
    import time
    if D.alive_as(pid, exe):
        os.kill(pid, signal.SIGKILL)
    t = time.monotonic()
    while D.alive_as(pid, exe) and time.monotonic() - t < 10:
        time.sleep(0.1)


def test_a_game_that_outlives_its_killed_runner_keeps_the_lock_and_the_next_driver_is_told(tmp_path, monkeypatch):
    """The runner is killed at the wall limit and its game lives on: the end keeps the lock (naming the game by the
    pid and exe the run recorded) instead of releasing it, so a second driver cannot start a second game; its
    refusal names the game and says whether it still runs."""
    import argparse
    d, lock, (pid, exe), rc = _killed_runner(tmp_path, monkeypatch, game_s=60, exit_wait_s=1.0)
    try:
        d.finish(rc)
        assert lock.is_dir() and (lock / D.LOCK_GAME).read_text() == f"pid {pid} exe {exe} run run-a01\n"
        assert "lock\t1\t" in (d.out / "post.tsv").read_text() and (d.out / D.END_MARKER).is_file()
        d2 = D.Driver.__new__(D.Driver)
        d2.a, d2.env, d2.lock, d2.holder = argparse.Namespace(), {"RATE_LOCK_DIR": str(lock)}, None, ""
        with pytest.raises(D.Refused) as e:
            d2.take_lock("a02")
        assert e.value.rc == 3 and f"pid {pid} ({exe}), still running" in str(e.value), str(e.value)
        _end_game(pid, exe)
        with pytest.raises(D.Refused) as e:
            d2.take_lock("a02")
        assert f"pid {pid} ({exe}), no longer running" in str(e.value), str(e.value)
    finally:
        _end_game(pid, exe)


def test_a_game_that_outlives_its_killed_runner_is_waited_for_then_the_lock_is_released(tmp_path, monkeypatch):
    """The same killed runner, with a game that exits a few seconds later: the end holds the lock while the game
    lives and releases it once the game has exited."""
    import time
    d, lock, (pid, exe), rc = _killed_runner(tmp_path, monkeypatch, game_s=4, exit_wait_s=60.0)
    try:
        assert D.alive_as(pid, exe) and lock.is_dir()
        t = time.monotonic()
        d.finish(rc)
        assert not D.alive_as(pid, exe) and not lock.exists() and time.monotonic() - t < 60
        assert "still alive" in (d.out / "driver.log").read_text()
    finally:
        _end_game(pid, exe)


def test_a_recorded_pid_now_running_another_program_is_not_the_game(tmp_path):
    """CONTROL: a live pid whose /proc/<pid>/exe is not the recorded exe (a reused pid) is not the run's game, so it
    does not keep the lock; the recorded exe of a live pid is."""
    out = tmp_path / "run"
    out.mkdir()
    me = os.readlink(f"/proc/{os.getpid()}/exe")
    (out / "witness_start.json").write_text(json.dumps({"pid": os.getpid(), "exe": "/x/binaries/ck3"}))
    (out / "witness_end.json").write_text(json.dumps({"unread": "gone"}))
    assert D.recorded_games(out) == [(os.getpid(), "/x/binaries/ck3")] and not D.alive_as(os.getpid(), "/x/binaries/ck3")
    assert D.alive_as(os.getpid(), me)


#: a stand-in runner that writes NO witness (as when `series watch` never sees its game) and starts a stand-in game,
#: in a session of its own, that outlives it; the game inherits the runner's environment
UNWITNESSED_RUNNER = r"""
import os, signal, subprocess, sys, time
signal.signal(signal.SIGTERM, signal.SIG_IGN)
game = subprocess.Popen([sys.executable, "-c", "import sys, time; time.sleep(float(sys.argv[1]))", sys.argv[2]],
                        start_new_session=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL)
open(os.path.join(sys.argv[1], "game.pid"), "w").write(str(game.pid))
time.sleep(600)
"""


def _unwitnessed(tmp_path, monkeypatch, run_id_in_game: bool):
    """A driver whose runner wrote no witness and is killed at its wall limit while its game lives on; the configured
    game binary is this python's (as in the stand-in series). (the driver, the lock, the game's pid, rc)."""
    import argparse
    import time
    monkeypatch.setattr(D, "TERM_WAIT_S", 1.0)
    monkeypatch.setattr(D, "GAME_EXIT_WAIT_S", 1.0)
    out, lock = tmp_path / "run-a01", tmp_path / "lock"
    out.mkdir()
    d = D.Driver.__new__(D.Driver)
    d.a = argparse.Namespace(wall_s=1.0)
    # a run id of this test alone: another suite running at the same time has games carrying its own
    rid = f"demo-a01-{os.getpid()}-{uuid.uuid4().hex[:12]}"
    d.env = {"RATE_LOCK_DIR": str(lock), "RATE_GAME_BIN": sys.executable, "RATE_RUN_ID": rid}
    d.lock, d.holder, d.out, d.sampler, d.watcher, d.started = None, "", out, None, None, True
    d.ended = True                                        # its end steps completed: the lock is what is read
    d.take_lock("a01")
    (out / "ck3.before").write_text("".join(x + "\n" for x in D.ck3_pids(d.game_bin())))
    script = tmp_path / "runner.py"
    script.write_text(UNWITNESSED_RUNNER)
    env = {k: v for k, v in os.environ.items() if k != "RATE_RUN_ID"}
    if run_id_in_game:
        env["RATE_RUN_ID"] = rid
    runner = subprocess.Popen([sys.executable, str(script), str(out), "60"], stdin=subprocess.DEVNULL, env=env)
    t = time.monotonic()
    while not (out / "game.pid").is_file() and time.monotonic() - t < 20:
        time.sleep(0.1)
    pid = int((out / "game.pid").read_text())
    assert D.recorded_games(out) == []                    # no witness: the recorded rule alone would see nothing
    rc = d.wait_runner(runner)
    assert rc == -9, rc
    return d, lock, pid, rc


def test_a_game_no_witness_recorded_keeps_the_lock_when_it_carries_the_runs_id(tmp_path, monkeypatch):
    """The runner wrote no witness and its game (a process of the configured game binary, not alive when the run
    began, carrying this run's RATE_RUN_ID) lives on: the end keeps the lock and names that game."""
    d, lock, pid, rc = _unwitnessed(tmp_path, monkeypatch, run_id_in_game=True)
    exe = os.readlink(f"/proc/{pid}/exe")
    try:
        d.finish(rc)
        assert lock.is_dir() and (lock / D.LOCK_GAME).read_text() == f"pid {pid} exe {exe} run run-a01\n"
        assert "lock\t1\t" in (d.out / "post.tsv").read_text()
    finally:
        _end_game(pid, exe)


def test_CONTROL_a_process_of_the_game_binary_that_is_not_the_runs_does_not_keep_the_lock(tmp_path, monkeypatch):
    """The same binary, started during the run but without this run's id (another program of a shared interpreter,
    as the stand-in's game binary is): not this run's game, and the lock is released."""
    d, lock, pid, rc = _unwitnessed(tmp_path, monkeypatch, run_id_in_game=False)
    exe = os.readlink(f"/proc/{pid}/exe")
    try:
        d.finish(rc)
        assert not lock.exists()
    finally:
        _end_game(pid, exe)


def test_a_series_run_s_default_slot_reads_the_host_config(tmp_path):
    """With RATE_RUNS only in --config, a series attempt with no --run-id gets this host's next slot (the default
    run id is read with the configured environment, as every child step is). CONTROL: with RATE_RUNS in neither,
    the driver refuses it as unset (rc 2) before any step."""
    h = _host(tmp_path)
    exp, ser = _declare(h, tmp_path), _schedule(tmp_path, "demo")   # dry runs: nothing starts
    env = {k: v for k, v in h["env"].items() if k != "RATE_RUNS"}
    cfg = tmp_path / "host.toml"
    cfg.write_text(f'[env]\nRATE_RUNS = "{h["runs"]}"\n')
    args = [sys.executable, "-m", "ck3bench.harness", "series", "run", "--host", "h1", "--series", str(ser),
            "--expect", str(exp), "--policy", "noop", "--dry-run"]
    r = subprocess.run(args + ["--config", str(cfg)], env=env, cwd=h["clone"], capture_output=True, text=True,
                       timeout=300)
    assert r.returncode == 0 and "demo-a01-h1" in r.stdout and not h["runs"].exists(), r.stdout + r.stderr
    r = subprocess.run(args, env=env, cwd=h["clone"], capture_output=True, text=True, timeout=300)
    assert r.returncode == 2 and "RATE_RUNS is not set" in r.stderr, r.stdout + r.stderr


def test_a_series_attempt_holds_its_timings_its_agent_cli_and_its_protected_directory(tmp_path):
    """In a series: a timing flag other than the expectation's `timings` is refused (rc 5), and so is an expectation
    that declares none (rc 2); --agent-cli and --session-env are refused (the identity records RATE_CLAUDE_BIN's
    version); RATE_PROTECTED_DIR unset is refused. Each before the lock and the run directory. CONTROL: the dry run
    with the declared timings (or no timing flag) is accepted, and a check (no series) takes any timings."""
    h = _host(tmp_path)
    exp, ser = _declare(h, tmp_path), _schedule(tmp_path, "demo")   # dry runs: nothing starts
    common = ["series", "run", "--host", "h1", "--series", str(ser), "--expect", str(exp), "--policy", "noop"]
    r = _harness(h, *common, "--wall-budget-s", "16", "--dry-run")
    assert r.returncode == 5 and "expect_differs:timings: --wall-budget-s 16 (declared 15)" in r.stderr, r.stderr
    r = _harness(h, *common, "--agent-cli", "x", "--dry-run")
    assert r.returncode == 2 and "series_agent_cli" in r.stderr
    r = _harness(h, *common, "--session-env", "X", "--dry-run")
    assert r.returncode == 2 and "series_agent_cli" in r.stderr
    hp = {**h, "env": {k: v for k, v in h["env"].items() if k != "RATE_PROTECTED_DIR"}}
    r = _harness(hp, *common, "--dry-run")
    assert r.returncode == 2 and "protected_dir_not_configured" in r.stderr
    d = json.loads(exp.read_text())
    no_t = tmp_path / "expect-no-timings.json"
    no_t.write_text(json.dumps({k: v for k, v in d.items() if k != "timings"}))
    r = _harness(h, "series", "run", "--host", "h1", "--series", str(ser), "--expect", str(no_t), "--policy", "noop",
                 "--dry-run")
    assert r.returncode == 2 and "expect_timings" in r.stderr
    assert "REFUSE: REFUSE" not in r.stderr and r.stderr.startswith("REFUSE expect_timings")   # one prefix
    assert not h["runs"].exists() and not h["lock"].exists()
    for extra in ([], ["--wall-budget-s", "15"]):
        r = _harness(h, *common, *extra, "--dry-run")
        assert r.returncode == 0 and "--wall-budget-s 15.0" in r.stdout, r.stdout + r.stderr
    r = _harness(h, "series", "run", "--host", "h1", "--run-id", "c1", "--expect", str(exp), "--policy", "noop",
                 "--wall-budget-s", "900", "--dry-run")
    assert r.returncode == 0 and "--wall-budget-s 900.0" in r.stdout, r.stdout + r.stderr


#: JSON nested far past the bound: refused before it is parsed, as any other value that is not an object
DEEP = "[" * 200000 + "]" * 200000
#: the schema with one member nested one level past the bound (the member's own levels and the document's)
DEEP_MEMBER = ('{"schema": "%s", "x": ' % SR.EXPECT_SCHEMA + '{"a": ' * S.JSON_DEPTH_MAX + "1"
               + "}" * S.JSON_DEPTH_MAX + "}")


@pytest.mark.parametrize("shape", ("[]", '"x"', "null", DEEP, DEEP_MEMBER),
                         ids=("list", "string", "null", "deep", "deep_member"))
def test_an_expectation_or_schedule_that_is_not_a_json_object_is_refused_by_name_with_nothing_written(tmp_path, shape):
    """`series start` and the driver refuse an expectation that is a JSON value other than an object
    (`expect_schema`), and the driver a schedule that is one (the schedule's refusal): exit 2 (the driver) or 5
    (`series start`), no traceback, no run directory, no lock, nothing in OUT."""
    h = _host(tmp_path)
    bad = tmp_path / "bad.json"
    bad.write_text(shape)
    r = _harness(h, "series", "run", "--host", "h1", "--run-id", "x3", "--expect", str(bad), "--policy", "noop")
    assert r.returncode == 2 and r.stderr.startswith("REFUSE expect_schema") and "Traceback" not in r.stderr, r.stderr
    exp = _declare(h, tmp_path)
    r = _harness(h, "series", "run", "--host", "h1", "--series", str(bad), "--expect", str(exp), "--policy", "noop")
    assert r.returncode == 2 and f"not a {S.SERIES_SCHEMA} schedule" in r.stderr and "Traceback" not in r.stderr, \
        r.stderr
    assert not h["runs"].exists() and not h["lock"].exists()
    out = tmp_path / "out"
    out.mkdir()
    r = subprocess.run([sys.executable, "-m", "ck3bench.harness", "series", "start", str(out)],
                       env={**h["env"], "RATE_EXPECT": str(bad), "RATE_RUN_ID": "x4", "RATE_HOST": "h1"},
                       cwd=h["clone"], capture_output=True, text=True, timeout=600)
    assert r.returncode == 5 and r.stdout.startswith("REFUSE expect_schema") and "Traceback" not in r.stderr, \
        r.stdout + r.stderr
    assert not list(out.iterdir())


# ── a stop at the env's end row, in both arms: the reasons it adds are the class the sensitivity line reads ─────────

def _seated_cli(tmp: Path) -> Path:
    """A stand-in agent CLI binary: `--version` prints a version; anything else plays the stand-in seat."""
    p = tmp / "claude-standin"
    p.write_text(f"#!/bin/sh\nif [ \"$1\" = \"--version\" ]; then echo '1.0.0 (Claude Code)'; exit 0; fi\n"
                 f"exec {sys.executable} {TD / 'fake_cli.py'} \"$@\"\n")
    p.chmod(0o755)
    return p


def _attempt(tmp: Path, arm: str, kill_at_end_row: bool, relative: bool = False) -> tuple[dict, Path, dict]:
    """One series attempt through the driver (cap 1, a series id of its own); with `kill_at_end_row`, the driver's
    whole process group (the driver, its runner, its watcher) is killed the moment the env's end row reads, then what
    the runner started in sessions of their own (`env play`, an agent session) by the pids it recorded (reap), and
    the documented recovery runs by hand (the lock removed, as a host reboot does; `series end OUT`, `series close
    OUT`). With `relative`, the driver runs in the directory holding the schedule and the expectation and is given
    them by their names, as the series' document writes its commands. (the scorer's row, OUT, the host)"""
    import signal
    tmp.mkdir(parents=True, exist_ok=True)
    h = _host(tmp)
    h["env"]["RATE_CLAUDE_BIN"] = str(_seated_cli(tmp))
    exp = _declare(h, tmp)
    sid = unique_id("demo")
    ser = tmp / "series.json"
    ser.write_text(json.dumps({"schema": S.SERIES_SCHEMA, "series": sid, "task": str(STANDIN), "pattern": ["h1"],
                               "valid_target": 1, "attempt_cap": 1}))
    names = (ser.name, exp.name) if relative else (str(ser), str(exp))
    args = [sys.executable, "-m", "ck3bench.harness", "series", "run", "--host", "h1", "--series", names[0], "--expect",
            names[1], "--wall-budget-s", "15"] + (["--policy", "noop"] if arm == "control" else [])
    out = h["runs"] / f"{sid}-a01-h1"
    p = subprocess.Popen(args, env=h["env"], cwd=tmp if relative else h["clone"], stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT, text=True, start_new_session=True)
    try:
        if kill_at_end_row:
            t = time.monotonic()
            while S.trajectory_end(out) is None and p.poll() is None and time.monotonic() - t < 300:
                time.sleep(0.02)
            assert S.trajectory_end(out) is not None, "no end row"
            os.killpg(p.pid, signal.SIGKILL)
        p.communicate(timeout=600)
    finally:
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        killed = reap(out)
    assert bool(killed) == kill_at_end_row, killed      # only a kill leaves `env play` holding the game
    if kill_at_end_row:
        import shutil
        shutil.rmtree(h["lock"], ignore_errors=True)
        assert "validity" not in json.loads((out / "run.json").read_text())
        for step in ("end", "close"):
            r = _harness(h, "series", step, str(out))
            assert r.returncode == 0, (step, r.stdout + r.stderr)
    return S.read_run(out), out, h


def _unended_copy(out: Path, runs: Path) -> tuple[Path, dict]:
    """A copy of attempt OUT and its ledger under RUNS as its host left it had it gone down right after the driver's
    after digest (before `series end`): run.json without its end and validity, no end marker, the ledger entry open.
    (the copy, the validity the driver wrote)"""
    import shutil
    rj = json.loads((out / "run.json").read_text())
    led = out.parent / (rj["series"]["id"] + ".ledger")
    shutil.copytree(out, runs / out.name)
    shutil.copytree(led, runs / led.name)
    o = runs / out.name
    driver = rj.pop("validity")
    rj.pop("end")
    (o / "run.json").write_text(json.dumps(rj, indent=1))
    for f in ("bridge_title_witness.json", "result.json", S.END_MARKER):
        (o / f).unlink(missing_ok=True)
    e = runs / led.name / f"attempt-{rj['series']['attempt']:02d}.json"
    ent = {k: v for k, v in json.loads(e.read_text()).items() if k not in ("valid", "reasons", "ended_utc", "not_attempt")}
    e.write_text(json.dumps(dict(ent, ended=False), indent=1))
    return o, driver


def _tree(d: Path) -> dict:
    return {str(p.relative_to(d)): p.read_bytes() for p in sorted(d.rglob("*")) if p.is_file()}


@pytest.mark.parametrize("arm", ("control", pytest.param("seated", marks=needs_mcp)))
def test_a_hand_recovery_from_another_directory_reads_the_paths_the_driver_was_given_by_name(tmp_path, arm):
    """The driver given the schedule and the expectation by their names, in their directory, as the series' document
    writes its commands: `series start` records both absolute, so the documented recovery by hand from another
    directory, in the host configuration only (none of the attempt's declarations), writes the driver's validity.
    With the expectation moved away, or a JSON value there that is not an object (`[]`, `"x"`, `null`), `series end`
    refuses by name and writes nothing (run.json, the ledger and the run directory as they were, no `end_failed`);
    put back, the same step ends it as the driver did."""
    _, out, h = _attempt(tmp_path / "rel", arm, False, relative=True)
    rj = json.loads((out / "run.json").read_text())
    paths = (rj["declared"]["RATE_EXPECT"], rj["expectation"]["path"], rj["series"]["schedule"])
    assert all(os.path.isabs(x) and Path(x).is_file() for x in paths), paths
    runs = tmp_path / "hand" / "runs"
    o, driver = _unended_copy(out, runs)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    hand = {k: v for k, v in h["env"].items() if k not in SR.DECLARED_AT_START and k != "RATE_SERIES"}
    hand["RATE_RUNS"] = str(runs)

    def step(name):
        return subprocess.run([sys.executable, "-m", "ck3bench.harness", "series", name, str(o)], env=hand,
                              cwd=elsewhere, capture_output=True, text=True, timeout=600)
    exp = Path(rj["declared"]["RATE_EXPECT"])
    away = exp.with_name(exp.name + ".away")
    exp.rename(away)
    before = _tree(runs)
    r = step("end")
    assert r.returncode == 6 and "REFUSE end_expect_unreadable" in r.stdout and "nothing written" in r.stdout, \
        r.stdout + r.stderr
    assert _tree(runs) == before and "end_failed" not in r.stdout
    away.rename(exp)
    frozen = exp.read_bytes()
    for shape in ("[]", '"x"', "null", DEEP, DEEP_MEMBER):   # JSON, not an object: the same refusal, no end_failed
        exp.write_text(shape)
        r = step("end")
        assert r.returncode == 6 and "REFUSE end_expect_unreadable" in r.stdout and "expect_schema" in r.stdout \
            and "nothing written" in r.stdout and "Traceback" not in r.stderr, (shape[:8], r.stdout + r.stderr)
        assert _tree(runs) == before and "end_failed" not in r.stdout
    exp.write_bytes(frozen)
    for name in ("end", "close"):
        r = step(name)
        assert r.returncode == 0, (name, r.stdout + r.stderr)
    assert json.loads((o / "run.json").read_text())["validity"] == driver


@pytest.mark.parametrize("arm", ("control", pytest.param("seated", marks=needs_mcp)))
def test_a_kill_at_the_end_row_adds_only_reasons_of_the_stopped_after_end_class_in_either_arm(tmp_path, arm):
    """The same attempt played whole and killed at its end row: the reasons the kill adds are all in
    score.STOPPED_AFTER_END (records written after the end row), so with the stand-in's own reasons (no game) taken
    out, the killed attempt is in k'/n' in both arms alike, a --policy run's ended by hand included."""
    whole, _, _ = _attempt(tmp_path / "whole", arm, False)
    killed, _, _ = _attempt(tmp_path / "killed", arm, True)
    assert killed["end_row"] and not killed["valid"]
    added = [x for x in killed["invalid_reasons"] if x not in whole["invalid_reasons"]]
    assert added and all(x in S.STOPPED_AFTER_END for x in added), (added, whole["invalid_reasons"])
    row = dict(killed, invalid_reasons=added)
    assert S.stopped_after_end(row) and S.score([row])["sensitivity_stopped_after_end"]["attempts"] == [killed["run"]]
