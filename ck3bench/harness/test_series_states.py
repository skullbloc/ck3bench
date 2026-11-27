"""Every reachable state of a series under its documented commands, and the one reading of an attempt's standing.

A stand-in series of three attempts (valid target 2, attempt cap 3, hosts `a` and `b`) is explored breadth first from its empty ledger. The commands are the kit's own, run in-process on the
run directories: the driver's `series run` (the slot, the run directory, `series start`, the game played, then
the env's end row, `done`, `series end`, `series result` and, when both exited 0, the end marker; also with `series
end`'s reading raising an error of its own), stopping a driver (before the end row; after it, in the post-episode
hold, killed with no end.json or stopped by a signal with end.json `stopped_by_signal`; and with the end row cut
short), a trajectory cut after an attempt's end, `series end` run by hand, `series withdraw`
(with and without `--cancelled`), `series close`, and the documented hand step for a run directory whose start never
reached the ledger (moved to refused-starts/ with start_refused.txt). Each command is also stopped before each of its
writes (a driver or a command killed between two writes), so every state a crash can leave is reached too.

In every reachable state:

  * the slot (score.stop_state over the ledger) and the scorer (score.series_gate over every run directory, held to
    the ledger) agree on the valid count, the attempt count and whether the stop is reached, unless the scorer
    refuses the series by name;
  * the scorer never calls the series stopped while the slot would start another attempt;
  * a withdrawal, a cancellation or a close refuses while the attempt's driver runs, and writes nothing;
  * some sequence of the documented commands, none of them stopped, leads from it to a series the scorer scores as
    stopped, or to a clean continuation (nothing running, nothing in flight, nothing refused, the stop not reached),
    or the scorer refuses it naming a damaged file (a record damaged after its end, which no command repairs).

The game is the fake backend's finished episode; the identity witnesses `series end` reads are stood in (each is
tested on its own in test_series.py), so an attempt is valid or not by its runner's end reason alone."""
import hashlib
import json
import os
import shutil
from collections import deque
from pathlib import Path

import pytest

from ck3bench.harness import runner
from ck3bench.harness import score as S
from ck3bench.harness import series as SR

TD = Path(__file__).with_name("testdata")
TARGET, CAP, LAST = 2, 3, 3
PROMPT = b"prompt"


class Stopped(BaseException):
    """The process running a command was killed before a write (a kill, which no `except Exception` sees)."""


#: what a host that went down mid-write leaves at the end of a file: a line cut inside a string
TRUNCATED = b'{"kind": "step", "t'


def split_end(traj: bytes) -> tuple[bytes, bytes]:
    """(the trajectory before the env's end row, the end row's line): the game plays the first, and the env writes
    the second when the episode ends, before its post-episode hold."""
    lines = traj.splitlines(keepends=True)
    i = next(n for n, ln in enumerate(lines) if b'"kind": "end"' in ln)
    return b"".join(lines[:i]), b"".join(lines[i:])


def trajectory_state(traj: bytes) -> str:
    """What a state key records of a trajectory: damaged after its end, its end row whole, cut, or absent."""
    if traj.endswith(TRUNCATED):
        return "damaged"
    if b'"kind": "end"' in traj:
        return "end row" if traj.endswith(b"\n") else "end row cut"
    return "no end row"


def end_raises(*a, **k):
    """`series end`'s reading failing with an error of its own (a file shape no reader expected)."""
    raise RuntimeError("a reading that fails")


@pytest.fixture(autouse=True)
def no_fsync(monkeypatch):
    """The records' flushes to the disk skipped (series.NO_FSYNC_FOR_TESTS): thousands of states, each written."""
    monkeypatch.setenv(SR.NO_FSYNC_FOR_TESTS, "1")


@pytest.fixture(scope="module")
def episode(tmp_path_factory):
    out = tmp_path_factory.mktemp("ep") / "run"
    assert runner.main(["--out", str(out), "--scenario", str(TD / "standin-ireland-fake-wake-once.toml"),
                        "--policy", "noop"]) == 0
    return {f.name: f.read_bytes() for f in out.iterdir() if f.is_file() and f.name != "end.json"}


def _ident() -> dict:
    return {"git": {"commit": "a" * 40, "dirty": False}, "bridge_so": {"sha256": "b" * 64},
            "wheel": {"sha256": "c" * 64, "compare": {"verdict": "identical", "wheel_members_sha256": "d" * 64}},
            "map": {"sha256": "e" * 64}, "game_build_id": {"build_id": "f" * 40},
            "claude_version": {"version": "2.1.0 (Claude Code)"}, "start_save": {"sha256": "1" * 64},
            "prompt": {"sha256": hashlib.sha256(PROMPT).hexdigest()}, "model": "m", "full_hold_s": 600,
            "switches": {}, "host": "h"}


class World:
    """The series' files under one directory, restored from a snapshot before each command and read after it."""

    def __init__(self, root: Path, episode: dict, monkeypatch):
        self.w = root / "w"
        self.runs = self.w / "runs"
        self.sched = root / "s.json"
        self.sched.write_text(json.dumps({"schema": S.SERIES_SCHEMA, "series": "st", "task": "t", "pattern": ["a", "b"],
                                          "valid_target": TARGET, "attempt_cap": CAP}))
        self.series = S.load_series(self.sched)
        self.episode = episode
        self.alive: frozenset = frozenset()
        self.budget = None
        for k, v in (("RATE_LEDGER", str(self.runs / "st.ledger")), ("RATE_RUNS", str(self.runs)),
                     ("RATE_SERIES", str(self.sched)), ("RATE_PROTECTED_DIR", str(root / "protected")),
                     ("RATE_EXPECT", str(root / "e.json"))):     # what series start records (load_expect stands in)
            monkeypatch.setenv(k, v)
        for k in ("RATE_SCENARIO", "RATE_HOST", "RATE_RUN_ID"):
            monkeypatch.delenv(k, raising=False)
        exp = {"_path": "e.json", "_sha256": "9" * 64, "_unfrozen": [], "timings": {}}
        monkeypatch.setattr(SR, "load_expect", lambda: exp)
        monkeypatch.setattr(SR, "plan", lambda: {"scenario": "t", "seat": None})
        monkeypatch.setattr(SR, "static_identity", lambda p: _ident())
        monkeypatch.setattr(SR, "check_static", lambda *a: [])
        monkeypatch.setattr(SR, "check_task", lambda *a: [])
        monkeypatch.setattr(SR, "timing_reasons", lambda *a: [])
        monkeypatch.setattr(SR, "witness_reasons", lambda *a: [])
        monkeypatch.setattr(SR, "header_reasons", lambda *a: ([], {}))
        monkeypatch.setattr(SR, "session_witnesses", lambda *a, **k: ([], {}))
        monkeypatch.setattr(SR, "alive", lambda out: [f"its driver ({out.name})"] if out.name in self.alive else [])
        real = SR._write

        def write(p, text):
            if self.budget is not None:
                if self.budget == 0:
                    raise Stopped()
                self.budget -= 1
            real(p, text)
        monkeypatch.setattr(SR, "_write", write)
        self.runs.mkdir(parents=True)

    # ── snapshots ──
    def snap(self) -> tuple:
        files, dirs = {}, []
        for p in sorted(self.w.rglob("*")):
            rel = str(p.relative_to(self.w))
            if p.is_dir():
                dirs.append(rel)
            else:
                files[rel] = p.read_bytes()
        return files, tuple(dirs), self.alive

    def load(self, snap: tuple) -> None:
        files, dirs, alive = snap
        shutil.rmtree(self.w)
        for d in dirs:
            (self.w / d).mkdir(parents=True, exist_ok=True)
        for rel, b in files.items():
            (self.w / rel).write_bytes(b)
        self.alive = alive

    def key(self, snap: tuple) -> tuple:
        """What a state is: per run directory, which records it holds (run.json's validity by its verdict), and the
        ledger entries' standing fields, and which drivers run; timestamps are not part of it."""
        files, dirs, alive = snap
        rec = []
        entries = [json.loads(b) for r, b in files.items() if ".ledger/attempt-" in r and r.endswith(".json")]
        by_run = {e.get("run_id"): e for e in entries}
        played = {Path(d).name for d in dirs if d.count("/") == 1}
        for d in dirs:
            if d.endswith(".ledger") or d == "runs" or d == "runs/" + S.REFUSED_DIR:
                continue
            shown = d.rsplit("-", 1)[0] + "-*" if d.startswith("runs/" + S.REFUSED_DIR + "/") else d
            names = {Path(r).name: b for r, b in files.items() if str(Path(r).parent) == d}
            rid = json.loads(names["run.json"]).get("run_id") if "run.json" in names else None
            if shown != d and not (S.WITHDRAWN_FILE in names or (rid in by_run and rid not in played)):
                # a refused start the ledger does not name holds no slot and no count: one with the same run id is
                # all of them (the run id its run.json carries, if any, is what a later ledger entry can name)
                rec.append(("runs/refused-starts/*", "set aside", rid or ""))
                continue
            rj = json.loads(names["run.json"]) if "run.json" in names else None
            v = (rj or {}).get("validity")
            if "trajectory.jsonl" in names:
                shown = shown + f" ({trajectory_state(names['trajectory.jsonl'])})"
            ej = json.loads(names["end.json"]).get("end_reason") if "end.json" in names else None
            e = by_run.get(Path(d).name) if not d.startswith("runs/" + S.REFUSED_DIR + "/") else None
            if e is not None and e.get("ended") and S.standing(e) == "not_attempt" and v is None and (
                    S.WITHDRAWN_FILE in names or S.CANCELLED_FILE in names):
                rec.append((shown, "finished", "not_attempt"))     # a finished attempt is its standing alone
                continue
            if e is not None and e.get("ended") and v is not None and e.get("valid") is v["valid"] and not \
                    e.get("not_attempt") and S.END_MARKER in names and "result.json" in names and not (
                    S.WITHDRAWN_FILE in names or S.CANCELLED_FILE in names):
                rec.append((shown, "finished", S.standing(e)))
                continue
            rec.append((shown, rj is not None, None if v is None else v["valid"], ej,
                        tuple(sorted(n for n in names if n in (S.END_MARKER, "result.json", S.WITHDRAWN_FILE,
                                                               S.DONE_FILE, S.CANCELLED_FILE, S.START_REFUSED_FILE,
                                                               "trajectory.jsonl", "bridge_title_witness.json")))))
        led = tuple(sorted((e["attempt"], e.get("run_id"), e.get("ended"), e.get("valid"), e.get("not_attempt"))
                           for e in entries))
        return tuple(sorted(set(rec))), led, alive

    # ── readings ──
    def run_dirs(self) -> list[Path]:
        top = [p for p in self.runs.iterdir() if p.is_dir() and not p.name.endswith(".ledger")
               and p.name != S.REFUSED_DIR]
        aside = sorted((self.runs / S.REFUSED_DIR).iterdir()) if (self.runs / S.REFUSED_DIR).is_dir() else []
        return sorted(top) + aside

    def readings(self) -> tuple[dict, dict, bool]:
        led = S.stop_state(self.series, SR.ledger(self.series))
        dirs = self.run_dirs()
        gate = S.series_gate(dirs)
        if gate["series"] not in (None, "mixed"):
            gate = S.series_gate(dirs, S.read_ledger(self.runs / "st.ledger"))
        if gate["series"] is None:                      # no run directory reads as an attempt yet
            gate = {"series": None, "stopped": False, "refused": [], **S.stop_state(self.series, [])}
        # this world runs under the test-only switch (no_fsync), so every attempt records `records_flushed: false`
        # and the scorer refuses the series for it; that refusal has its own test, and is left out of the readings
        gate["refused"] = [x for x in gate.get("refused") or [] if not x.startswith("series_records_not_flushed")]
        refused = gate["series"] == "mixed" or bool(gate.get("refused")) or "repeats" in str(gate.get("why"))
        return led, gate, refused

    def closable(self) -> list[dict]:
        """The scorer's reading of the run directories with every attempt whose `series end` wrote its validity and
        whose driver stopped before the marker read as `series close` will close it (its recorded validity)."""
        out = []
        for d in self.run_dirs():
            e = S.validity_only(d)
            if e["series"] is None:                 # a directory no `series start` wrote: no attempt of any series
                continue
            played = {x.name for x in self.run_dirs() if x.parent == self.runs}
            if e["start_refused"] and (e["run_id"] not in {x.get("run_id") for x in SR.ledger(self.series)}
                                       or e["run_id"] in played):
                continue
            if not e["ended"] and e["has_validity"] and not e["not_attempt"] and d.name not in self.alive:
                e = {**e, "ended": True, "valid": e["recorded_valid"]}
            out.append(e)
        return out

    # ── the commands ──
    def run(self, fn, *args, budget=None):
        self.budget = budget
        try:
            fn(*args)
            return True
        except Stopped:
            return False
        finally:
            self.budget = None

    def start(self, host: str) -> None:
        got, _ = SR.slot(self.series, host, "")
        rid = got["run_id"]
        out = self.runs / rid
        if self.budget == 0:
            raise Stopped()
        out.mkdir()
        if self.budget is not None:
            self.budget -= 1
        os.environ["RATE_HOST"], os.environ["RATE_RUN_ID"] = host, rid
        try:
            assert SR.cmd_start(out) == 0
        finally:
            os.environ.pop("RATE_HOST")
            os.environ.pop("RATE_RUN_ID")
        self.alive = self.alive | {rid}
        for n, b in self.episode.items():           # the game plays (the runner's files, not the kit's records)
            (out / n).write_bytes(split_end(b)[0] if n == "trajectory.jsonl" else b)
        (out / "seat_prompt.rendered.md").write_bytes(PROMPT)
        (out / "turns.jsonl").write_text("")
        (out / "transcript.jsonl").write_text(json.dumps({"type": "system", "subtype": "init"}) + "\n")

    def finish(self, rid: str, end_reason: str, raises: bool = False) -> None:
        """The driver's tail: the runner's end.json, the driver's `done`, `series end`, `series result`, and the end
        marker only when both exited 0 (driver.Driver.attempt); then it exits. RAISES: `series end`'s reading fails
        with an error of its own."""
        out = self.runs / rid
        self.end_row(rid)
        (out / "end.json").write_text(json.dumps({"end_reason": end_reason, "at": "2026-01-01T00:10:00Z",
                                                  "notes_witness": {"verdict": "agreed"}}))
        (out / S.DONE_FILE).write_text("rc=0\n")
        real = SR.end_reading
        try:
            if raises:
                SR.end_reading = end_raises
            if SR.cmd_end(out) == 0:
                SR.end_reading = real
                if SR.cmd_result(out) == 0:
                    SR._write(out / S.END_MARKER, "rc=0\n")
        finally:
            SR.end_reading = real
            self.alive = self.alive - {rid}

    def end_row(self, rid: str, cut: bool = False, reason: str = "") -> None:
        """The env writes its end row (CUT: a host that went down while writing it leaves half of it; REASON: the
        row's reason in place of the episode's own, `closed` when the runner closed the env)."""
        row = split_end(self.episode["trajectory.jsonl"])[1]
        if reason:
            r = json.loads(row)
            r["reason"] = reason
            row = json.dumps(r, sort_keys=True).encode() + b"\n"
        with open(self.runs / rid / "trajectory.jsonl", "ab") as f:
            f.write(row[:len(row) // 2] if cut else row)

    def stopped_after_end_row(self, rid: str, how: str) -> None:
        """The driver and its runner stopped in the post-episode hold, the end row written: KILL (a host down: no
        end.json, no `done`), SIGNAL (Ctrl-C or a shutdown: end.json `stopped_by_signal`, no `done`), or CUT (a host
        down while the end row was being written). DECIDED: the runner decided the end (the seat exited) and its host
        went down while it closed the game: its end.json `final: false` (runner.Run.finish writes it first), the env's
        end row `closed`, no `done`."""
        if how == "decided":
            (self.runs / rid / "end.json").write_text(json.dumps({"end_reason": "seat_exited", "final": False,
                                                                  "at": "2026-01-01T00:10:00Z",
                                                                  "notes_witness": {"verdict": "agreed"}}))
            self.end_row(rid, reason="closed")
            self.alive = self.alive - {rid}
            return
        self.end_row(rid, cut=how == "cut")
        if how == "signal":
            (self.runs / rid / "end.json").write_text(json.dumps({"end_reason": "stopped_by_signal: SIGINT",
                                                                  "at": "2026-01-01T00:10:00Z"}))
        self.alive = self.alive - {rid}

    def damage(self, rid: str) -> None:
        """A file damaged after the attempt's end: its trajectory's last line cut, as a host that went down mid-write
        leaves it."""
        with open(self.runs / rid / "trajectory.jsonl", "ab") as f:
            f.write(TRUNCATED)

    def aside(self, rid: str) -> None:
        """The documented hand step for a run directory whose start never reached the ledger."""
        out = self.runs / rid
        (self.runs / S.REFUSED_DIR).mkdir(exist_ok=True)
        n = len(list((self.runs / S.REFUSED_DIR).glob(f"{rid}-*")))
        dst = self.runs / S.REFUSED_DIR / f"{rid}-{n}"
        SR._write(out / S.START_REFUSED_FILE, "the driver stopped during series start; moved by hand\n")
        out.rename(dst)


def unfinished(world: World) -> set:
    """The attempts in the slot's place that are not finished: a driver runs, or the ledger entry is missing, open,
    or ended without the end marker (a stopped process's residue)."""
    led = {e.get("run_id"): e for e in SR.ledger(world.series)}
    out = set(world.alive)
    for d in world.run_dirs():
        if d.parent != world.runs:
            continue
        e = led.get(d.name)
        if e is None or not e.get("ended") or (not e.get("not_attempt") and not (d / S.END_MARKER).is_file()):
            out.add(d.name)
    return out


def actions(world: World) -> list[tuple[str, object, tuple, bool]]:
    """(name, command, args, explored stopped too) for every documented command that applies here; `series end` by
    hand runs on an attempt whose driver has stopped. A command is stopped before each of its writes, and a driver stopped, while every other attempt is finished or
    not begun: each stop point is reached, and the residue it leaves then meets every later command; two
    attempts' residues at once multiply the states and add no reading the per-attempt standing does not."""
    acts = []
    damaged = [x for x in world.run_dirs() if (x / "trajectory.jsonl").is_file()
               and (x / "trajectory.jsonl").read_bytes().endswith(TRUNCATED)]
    if damaged:
        return []                # a record damaged after its end: the scorer's named refusal is where it ends
    hosts_busy = {rid.rsplit("-", 1)[1] for rid in world.alive}
    named = {e.get("run_id") for e in SR.ledger(world.series)}
    left = unfinished(world)
    for h in ("a", "b"):
        got, refuse = SR.slot(world.series, h, "")
        if got is None or got["attempt"] > LAST or h in hosts_busy or (world.runs / got["run_id"]).exists():
            continue
        if not [x for x in SR.slot(world.series, h, got["run_id"])[1]]:
            acts.append((f"start {h}", world.start, (h,), not left))
    for d in world.run_dirs():
        if d.parent != world.runs:
            continue
        rid = d.name
        others = left - {rid}
        if rid in world.alive:
            acts.append((f"finish {rid} valid", world.finish, (rid, "episode_over"), not others))
            acts.append((f"finish {rid} invalid", world.finish, (rid, "wall_budget"), not others))
            acts.append((f"finish {rid} end raises", world.finish, (rid, "episode_over", True), not others))
            if not others:
                acts.append((f"stop driver {rid}", lambda r=rid: setattr(world, "alive", world.alive - {r}), (),
                             False))
                for how in ("kill", "signal", "cut", "decided"):
                    acts.append((f"stop driver {rid} after the end row ({how})", world.stopped_after_end_row,
                                 (rid, how), False))
        elif (d / "run.json").is_file():
            acts.append((f"end {rid}", SR.cmd_end, (d,), not others))
        if (d / S.END_MARKER).is_file() and not others:
            acts.append((f"damage {rid}", world.damage, (rid,), False))
        live = rid in world.alive          # refused while the driver runs: run whole, it must write nothing
        acts.append((f"withdraw {rid}", SR.cmd_withdraw, (d,), not others and not live))
        acts.append((f"cancel {rid}", SR.cmd_withdraw, (d, True), not others and not live))
        acts.append((f"close {rid}", SR.cmd_close, (d,), not others and not live))
        if not live and rid not in named:
            acts.append((f"aside {rid}", world.aside, (rid,), False))
    return acts


def test_every_reachable_state_reads_one_standing_and_has_a_way_on(tmp_path, monkeypatch, episode, capsys):
    world = World(tmp_path, episode, monkeypatch)
    first = world.snap()
    seen = {world.key(first): first}
    edges: dict = {}
    queue = deque([world.key(first)])
    problems = []
    good = set()
    named_damage, end_failed = [], []
    while queue:
        k = queue.popleft()
        world.load(seen[k])
        led, gate, refused = world.readings()
        if not refused:
            # the slot and the scorer read one count; an attempt the scorer holds in flight only for its missing end
            # marker is the one `series close` finishes: it is counted as recorded, and the scorer names the command
            after = S.stop_state(world.series, world.closable())
            if (after["valid"], after["attempts"], after["stop"]) != (led["valid"], led["attempts"], led["stop"]):
                problems.append(("readers disagree", k, led, gate))
            if (gate["valid"], gate["attempts"]) != (after["valid"], after["attempts"]) and \
                    "series close" not in str(gate.get("why")):
                problems.append(("an attempt to close is not named", k, gate))
        if gate.get("stopped") and not refused and not led["stop"]:
            problems.append(("scored stopped, the slot would start", k))
        if not refused and (gate.get("stopped") or (not world.alive and not led["in_flight"]
                                                    and not gate.get("in_flight") and led["stop"] is None)):
            good.add(k)
        damaged = [x for x in gate.get("refused") or [] if S.FILE_UNREADABLE + ":" in x]
        if refused and damaged and len(damaged) == len(gate.get("refused") or []):
            # a file damaged after its attempt's end: the score refuses the series by name, and nothing documented
            # repairs a frozen record; that refusal is the outcome
            good.add(k)
            named_damage.append(k)
        for d in world.run_dirs():
            v = (S.read_object(d / "run.json")[0] or {}).get("validity") or {}
            if any(str(x).startswith("end_failed:") for x in v.get("reasons") or []):
                end_failed.append(k)
        edges[k] = []
        set_aside = {d.name.rsplit("-", 1)[0] for d in world.run_dirs() if d.parent.name == S.REFUSED_DIR}
        for name, fn, args, stoppable in actions(world):
            # the whole command, then the command stopped before each of its writes (a slot's start is stopped
            # once: the start set aside and stopped again leaves the same states)
            budgets, n = [None], 0 if stoppable else None
            if n is not None and name.startswith("start ") and any(r.endswith("-" + args[0]) and r in set_aside for r in
                                                 [SR.slot(world.series, args[0], "")[0]["run_id"]]):
                n = None
            while n is not None:
                world.load(seen[k])
                done = world.run(fn, *args, budget=None if n is None else n)
                if done:
                    break
                budgets.append(n)
                n += 1
            for b in budgets:
                world.load(seen[k])
                alive_before = world.alive
                world.run(fn, *args, budget=b)
                if name.split()[0] in ("withdraw", "cancel", "close") and args and Path(args[0]).name in alive_before:
                    if world.key(world.snap()) != k:
                        problems.append((f"{name} wrote while its driver runs", k))
                nk = world.key(world.snap())
                if nk not in seen:
                    seen[nk] = world.snap()
                    queue.append(nk)
                if b is None:
                    edges[k].append(nk)
        capsys.readouterr()
        assert len(seen) < int(os.environ.get("STATES_CAP", "20000")), (len(seen), "the exploration does not close")
    # every state reaches a good one through whole documented commands
    can = set(good)
    changed = True
    while changed:
        changed = False
        for k, outs in edges.items():
            if k not in can and any(o in can for o in outs):
                can.add(k)
                changed = True
    stuck = [k for k in seen if k not in can]
    assert not problems, (len(problems), problems[:3])
    assert not stuck, (len(stuck), stuck[:2])
    # CONTROL: the exploration reached the states the rules are about
    recs = [r for k in seen for r in k[0]]
    files = lambda r: r[4] if len(r) == 5 else ()                                   # noqa: E731
    assert len(seen) > 500
    assert any(len(r) == 5 and r[3] == "wall_budget" for r in recs)                 # an invalid end
    assert any(r[1:] == ("finished", "invalid") for r in recs) and any(r[1:] == ("finished", "valid") for r in recs)
    assert any(r[1:] == ("finished", "not_attempt") for r in recs)                  # a withdrawal closed
    assert any(S.WITHDRAWN_FILE in files(r) for r in recs) and any(S.CANCELLED_FILE in files(r) for r in recs)
    assert any(r[1:] == ("set aside", "") for r in recs)                              # a start set aside
    assert any(r[1:2] == ("set aside",) and r[2] for r in recs)                     # one with its run.json
    assert any(len(r) == 5 and r[2] is not None and S.END_MARKER not in files(r) for r in recs)   # one to close
    assert any(len([e for e in k[1] if e[2]]) >= 3 for k in seen)                   # the cap reached
    assert any(len(k[2]) == 2 for k in seen)                                         # both hosts playing at once
    assert named_damage and end_failed                     # a file damaged after an end; a `series end` that raised
    # the post-episode hold: an end row written and its driver stopped (no `done`), ended by `series end` by hand
    held = [r for r in recs if "(end row)" in r[0] and len(r) == 5 and S.DONE_FILE not in files(r)]
    assert any(r[2] is not None for r in held)                                      # ended by hand
    assert any("(end row cut)" in r[0] for r in recs)                               # a cut end row
    # the runner's own end, its host down while it closed the game: counted, ended by `series end` by hand
    decided = [r for r in recs if len(r) == 5 and r[3] == "seat_exited" and S.DONE_FILE not in files(r)]
    assert any(r[2] is False for r in decided)


@pytest.fixture
def world(tmp_path, monkeypatch, episode):
    return World(tmp_path, episode, monkeypatch)


def _played(world: World, host: str, end_reason: str = "episode_over") -> Path:
    assert world.run(world.start, host)
    rid = sorted(world.alive)[-1] if len(world.alive) == 1 else next(r for r in world.alive if r.endswith("-" + host))
    return world.runs / rid


def test_CONTROL_the_standing_of_a_withdrawn_attempt_is_one_reading(world, capsys):
    """The slot, the scorer and `series close` read one standing: a withdrawal while the driver runs is refused
    (and writes nothing); once the driver has stopped it is withdrawn, and the driver's `series end` can no longer
    make it valid (refused, `end_ended`). An ended ledger entry that says valid and not an attempt counts as no
    attempt and no valid run (score.standing). A withdrawal record put by hand into an ended attempt refuses the
    score; without it the same series scores."""
    a1 = _played(world, "a")
    world.finish(a1.name, "episode_over")
    a2 = _played(world, "b")
    assert SR.cmd_withdraw(a2) == 6 and "withdraw_alive" in capsys.readouterr().out
    assert not (a2 / S.WITHDRAWN_FILE).exists()
    world.alive = frozenset()                               # the operator stops the driver
    assert SR.cmd_withdraw(a2) == 0
    assert SR.cmd_end(a2) == 6 and "end_ended" in capsys.readouterr().out
    led = S.stop_state(world.series, SR.ledger(world.series))
    assert (led["valid"], led["attempts"]) == (1, 1)
    assert S.standing({"ended": True, "valid": True, "not_attempt": True}) == "not_attempt"
    assert S.stop_state(world.series, [{"attempt": 1, "ended": True, "valid": True, "not_attempt": True}])["valid"] == 0
    a3 = _played(world, "a")
    world.finish(a3.name, "episode_over")
    _, gate, refused = world.readings()
    assert not refused and gate["stopped"] is True
    (a3 / S.WITHDRAWN_FILE).write_text("by hand\n")
    _, gate, refused = world.readings()
    assert refused and any("series_ledger_disagrees: st-a03-a" in x for x in gate["refused"])
    assert any("series_validity_disagrees: st-a03-a" in x for x in gate["refused"])
    (a3 / S.WITHDRAWN_FILE).unlink()
    assert not world.readings()[2]


def test_CONTROL_series_end_writes_the_validity_once(world, capsys):
    """`series end` on an attempt whose validity is written refuses and leaves run.json and the ledger as they are,
    whatever it would read now; a `series end` stopped between run.json and the ledger leaves an entry that
    `series close` writes from run.json's validity, never a second reading."""
    a1 = _played(world, "a")
    world.finish(a1.name, "episode_over")
    before = ((a1 / "run.json").read_bytes(), (world.runs / "st.ledger" / "attempt-01.json").read_bytes())
    assert SR.cmd_end(a1) == 6 and "end_ended" in capsys.readouterr().out
    assert before == ((a1 / "run.json").read_bytes(), (world.runs / "st.ledger" / "attempt-01.json").read_bytes())
    a2 = _played(world, "b")
    world.end_row(a2.name)
    (a2 / "end.json").write_text(json.dumps({"end_reason": "episode_over", "at": "2026-01-01T00:10:00Z",
                                             "notes_witness": {"verdict": "agreed"}}))
    assert not world.run(SR.cmd_end, a2, budget=2)          # bridge witness and run.json written, the ledger not
    world.alive = frozenset()
    _, gate, refused = world.readings()
    assert refused and any("series close" in x for x in gate["refused"])
    assert SR.cmd_end(a2) == 6
    assert SR.cmd_close(a2) == 0
    e = json.loads((world.runs / "st.ledger" / "attempt-02.json").read_text())
    assert (e["ended"], e["valid"]) == (True, json.loads((a2 / "run.json").read_text())["validity"]["valid"])
    _, gate, refused = world.readings()
    assert not refused and gate["stopped"] is True


def test_CONTROL_a_start_set_aside_by_hand_frees_the_slot(world):
    """A driver stopped during `series start` before the ledger entry: the run directory blocks the slot's run id
    until it is moved to refused-starts/ with start_refused.txt (the documented hand step); then the same slot
    plays. One stopped after the entry is withdrawn instead, wherever its directory is: the scorer counts it as
    that attempt, not an attempt, once withdrawn."""
    assert not world.run(world.start, "a", budget=2)        # the directory and run.json, no ledger entry
    assert (world.runs / "st-a01-a").is_dir() and not SR.ledger(world.series)
    assert world.readings()[2]                               # refused until it is put right
    world.aside("st-a01-a")
    assert not world.readings()[2]
    a1 = _played(world, "a")
    assert a1.name == "st-a01-a"
    world.alive = frozenset()
    # stopped after the ledger entry and moved aside: the ledger names it, so it is that attempt
    assert world.run(world.start, "b")                     # entered in the ledger; its driver then stopped
    world.alive = frozenset()
    moved = world.runs / S.REFUSED_DIR / "st-a02-b-0"
    (world.runs / "st-a02-b").rename(moved)
    (moved / S.START_REFUSED_FILE).write_text("moved\n")
    _, gate, refused = world.readings()
    assert refused and any("st-a02-b" in x and "series withdraw" in x for x in gate["refused"])
    assert SR.cmd_withdraw(moved) == 0
    assert not world.readings()[2]


def test_a_start_on_a_host_whose_clock_is_set_back_is_refused_in_its_first_second(world, capsys):
    """Host a's clock reads an hour before its own previous start in the ledger (set back): `series start` refuses
    `start_clock_behind` and enters no ledger entry, where the scorer would refuse the series later
    (`series_ledger_order`). CONTROL: the same start with the clock right is entered."""
    a1 = _played(world, "a")
    world.finish(a1.name, "episode_over")
    a2 = _played(world, "b")
    world.finish(a2.name, "wall_budget")
    f = world.runs / "st.ledger" / "attempt-01.json"
    e = json.loads(f.read_text())
    keep = e["started_utc"]
    e["started_utc"] = "2999-01-01T00:00:00Z"
    f.write_text(json.dumps(e))
    rid = SR.slot(world.series, "a", "")[0]["run_id"]
    os.environ["RATE_HOST"], os.environ["RATE_RUN_ID"] = "a", rid
    try:
        (world.runs / rid).mkdir()
        assert SR.cmd_start(world.runs / rid) == 6
        said = capsys.readouterr().out
        # both times and both recoveries: the clock behind now, or the earlier stamp from a clock that was ahead
        assert "REFUSE start_clock_behind: this host's clock reads 20" in said, said
        assert "is stamped 2999-01-01T00:00:00Z" in said and "wait until 2998-12-31T23:55:00Z" in said, said
        assert "set it right" in said and "as a deviation" in said
        assert not (world.runs / "st.ledger" / "attempt-03.json").exists()
        shutil.rmtree(world.runs / rid)
        e["started_utc"] = keep
        f.write_text(json.dumps(e))
        (world.runs / rid).mkdir()
        assert SR.cmd_start(world.runs / rid) == 0
        assert json.loads((world.runs / rid / "run.json").read_text())["records_flushed"] is False
    finally:
        os.environ.pop("RATE_HOST")
        os.environ.pop("RATE_RUN_ID")


def test_a_series_with_an_attempt_started_under_the_test_only_switch_is_refused_by_the_scorer(world):
    """CK3BENCH_TEST_NO_FSYNC=1 (this file's fixture) makes `series start` record `records_flushed: false`; the
    scorer refuses a series holding such an attempt, by name (`series_records_not_flushed`), so the switch cannot
    stand in a measured series. CONTROL: the same attempt recorded as flushed is not refused for it."""
    a1 = _played(world, "a")
    world.finish(a1.name, "episode_over")
    rj = json.loads((a1 / "run.json").read_text())
    assert rj["records_flushed"] is False
    dirs = world.run_dirs()
    refused = S.series_gate(dirs, S.read_ledger(world.runs / "st.ledger")).get("refused") or []
    assert any(x.startswith(f"series_records_not_flushed: {a1.name}:") for x in refused), refused
    (a1 / "run.json").write_text(json.dumps(dict(rj, records_flushed=True)))
    refused = S.series_gate(dirs, S.read_ledger(world.runs / "st.ledger")).get("refused") or []
    assert not any(x.startswith("series_records_not_flushed") for x in refused), refused
