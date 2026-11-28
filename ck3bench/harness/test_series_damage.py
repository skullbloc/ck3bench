"""The run files below, in the eight damaged shapes below (shapes a host that went down, or a hand, can leave), against
every series command: nothing raises, and every outcome is a named refusal or a named invalid reason. Not covered
here: the witness files, `done`, accounting.json, pressure.tsv, post.tsv, the protected directory's digests,
driver.log, and field types beyond those named below.

The files: the trajectory, the transcript, the turns file, end.json (whole, and its notes witness), run.json (whole,
and its launch time), result.json, and the attempt's ledger entry. The shapes: absent, empty, cut mid-line, cut
inside a string, cut inside a number, a NUL-filled tail, another JSON type, bytes that are not UTF-8. Each is laid on
attempt 2 of a stand-in series (target 2, cap 3, attempt 1 ended valid) at two moments:

  * BEFORE ITS END: the game was played out (end.json and `done` written) and the driver stopped before `series
    end`; then `series end`, `series close`, `series withdraw`, `series result`, `series slot`, `score` and `series
    publish` run, in that order, as an operator would;
  * AFTER ITS END: `series end` and `series close` completed, then the file is damaged; the same commands run.

For each: no command raises; every REFUSE line names its code; `series end` leaves no played attempt without a
validity unless it refused by name; a damaged file the attempt's validity reads is in that validity
(`file_unreadable:<file>`) or the score refuses by name; and the slot (the ledger) and the scorer (the run
directories) count the same valid runs and attempts unless the scorer refuses by name.

Further tests below: an attempt stopped after the env's end row (killed, interrupted, TERMed in the hold) and a cut
end row; run.json damaged after its start in ten shapes (its recovery row followed as written); `series result` on a
run.json that does not read; a second ledger file for one number; ledger fields of the wrong type; a host clock an
hour behind and ahead.

The identity witnesses `series end` reads are stood in (each is tested in test_series.py), as in
test_series_stop.py. No game, no model."""
import io
import json
import re
import shutil
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest

from ck3bench.harness import runner
from ck3bench.harness import score as S
from ck3bench.harness import series as SR

TD = Path(__file__).with_name("testdata")
END_AT = "2026-01-01T00:10:00Z"
#: a REFUSE line names its code first: `REFUSE <code>:` (or `REFUSE <code> ...`)
REFUSE_LINE = re.compile(r"^REFUSE [a-z][a-z_]*[:; ]")
#: (file, where it lives): the run files the kit reads, and the ledger entry
FILES = ("trajectory.jsonl", "transcript.jsonl", "turns.jsonl", "end.json", "end.json:notes_witness", "run.json",
         "run.json:launch_utc", "result.json", "ledger")
SHAPES = ("absent", "empty", "mid_line", "mid_string", "mid_number", "nul_tail", "wrong_type", "not_utf8")
#: the files whose damage the attempt's validity reads (file_reasons); the transcript and the turns are the resource
#: rule's, read only for a stop or a hang (this attempt ended `episode_over`)
VALIDITY_FILES = ("trajectory.jsonl", "end.json", "end.json:notes_witness", "run.json:launch_utc")


@pytest.fixture(autouse=True)
def no_fsync(monkeypatch):
    """The records' flushes to the disk skipped (series.NO_FSYNC_FOR_TESTS): every shape, each written."""
    monkeypatch.setenv(SR.NO_FSYNC_FOR_TESTS, "1")


@pytest.fixture(scope="module")
def episode(tmp_path_factory):
    out = tmp_path_factory.mktemp("ep") / "run"
    assert runner.main(["--out", str(out), "--scenario", str(TD / "standin-ireland-fake-wake-once.toml"),
                        "--policy", "noop"]) == 0
    return {f.name: f.read_bytes() for f in out.iterdir() if f.is_file() and f.name != "end.json"}


PROMPT = b"prompt"


def expectation(d: Path) -> tuple[str, str]:
    """An expectation file in D as `series start` records one: (its absolute path, its sha256)."""
    import hashlib
    p = d / "e.json"
    if not p.exists():
        p.write_text(json.dumps({"schema": SR.EXPECT_SCHEMA}))
    return str(p.resolve()), hashlib.sha256(p.read_bytes()).hexdigest()


def _ident() -> dict:
    import hashlib
    return {"git": {"commit": "a" * 40, "dirty": False}, "bridge_so": {"sha256": "b" * 64},
            "wheel": {"sha256": "c" * 64, "compare": {"verdict": "identical", "wheel_members_sha256": "d" * 64}},
            "map": {"sha256": "e" * 64}, "game_build_id": {"build_id": "f" * 40},
            "claude_version": {"version": "2.1.0 (Claude Code)"}, "start_save": {"sha256": "1" * 64},
            "prompt": {"sha256": hashlib.sha256(PROMPT).hexdigest()}, "model": "m", "full_hold_s": 600,
            "switches": {}, "host": "h"}


class Series:
    def __init__(self, root: Path, episode: dict, monkeypatch):
        self.root = root
        self.sched = root / "s.json"
        self.sched.write_text(json.dumps({"schema": S.SERIES_SCHEMA, "series": "st", "task": "t",
                                          "pattern": ["a", "b"], "valid_target": 2, "attempt_cap": 3}))
        self.ser = S.load_series(self.sched)
        self.episode = episode
        for k, v in (("RATE_LEDGER", str(root / "st.ledger")), ("RATE_SERIES", str(self.sched)),
                     ("SCORE_RATE_OVERRIDE_LOG", str(root / "override.log"))):
            monkeypatch.setenv(k, v)
        for k in ("RATE_EXPECT", "RATE_SCENARIO", "RATE_HOST", "RATE_RUN_ID"):
            monkeypatch.delenv(k, raising=False)
        monkeypatch.setattr(SR, "static_identity", lambda p: _ident())
        monkeypatch.setattr(SR, "witness_reasons", lambda *a: [])
        monkeypatch.setattr(SR, "header_reasons", lambda *a: ([], {}))
        monkeypatch.setattr(SR, "session_witnesses", lambda *a, **k: ([], {}))
        monkeypatch.setattr(SR, "settle_resource_window", lambda out, cap_s=0: 0.0)

    def attempt(self, a: int) -> Path:
        """Attempt A played out: run.json's start, the ledger entry, the game's files, end.json and `done`."""
        host = "a" if a % 2 else "b"
        ep, es = expectation(self.root)
        out = self.root / f"st-a{a:02d}-{host}"
        out.mkdir()
        for n, b in self.episode.items():
            (out / n).write_bytes(b)
        (out / "end.json").write_text(json.dumps({"end_reason": "episode_over", "at": END_AT,
                                                  "notes_witness": {"verdict": "agreed"}}))
        (out / "turns.jsonl").write_text(json.dumps({"turn": 1, "sent_utc": "2026-01-01T00:05:00Z",
                                                     "ended_utc": "2026-01-01T00:06:00Z"}) + "\n")
        (out / "transcript.jsonl").write_text(json.dumps({"type": "system", "subtype": "init"}) + "\n"
                                              + json.dumps({"type": "result", "result": "ok"}) + "\n")
        (out / S.DONE_FILE).write_text("rc=0\n")
        (out / "seat_prompt.rendered.md").write_bytes(PROMPT)
        (out / "run.json").write_text(json.dumps({
            "schema": SR.SCHEMA, "run_id": out.name, "kind": "rate", "host": host, "launch_utc": "2026-01-01T00:00:00Z",
            "plan": {"scenario": "t", "seat": None}, "start": {"at": "2026-01-01T00:00:00Z", **_ident()},
            "start_reasons": [], "expectation": {"path": ep, "sha256": es},
            "declared": {**{k: "" for k in S.DECLARED_AT_START}, "RATE_EXPECT": ep},
            "series": {"id": "st", "schedule": str(self.sched), "schedule_sha256": "x", "attempt": a,
                       "valid_target": 2, "attempt_cap": 3, "pattern": ["a", "b"]}}, indent=1))
        SR.write_ledger(self.ser, {"attempt": a, "host": host, "run_id": out.name, "ended": False,
                                   "started_utc": f"2026-01-01T00:0{a}:00Z", "expectation_sha256": es})
        return out

    def ended(self, out: Path) -> None:
        with redirect_stdout(io.StringIO()):
            assert SR.cmd_end(out) == 0 and SR.cmd_close(out) == 0


def _cut(b: bytes, shape: str, lines: bool = True) -> bytes:
    """B damaged in SHAPE: a `.jsonl` file's last line (LINES), or a one-object file as a whole; `absent` is the
    caller's."""
    last = b.rstrip(b"\n").rfind(b"\n") + 1 if lines else 0   # where the last record starts
    tail = b[last:]
    if shape == "empty":
        return b""
    if shape == "mid_line":
        return b[:last + max(1, len(tail) // 2)]
    if shape == "mid_string":
        q = tail.find(b'"', 1)
        return b[:last + (q + 2 if q >= 0 else len(tail) // 2)]
    if shape == "mid_number":
        m = re.search(rb"\d{2,}", tail) or re.search(rb"\d", tail)
        return b[:last + (m.start() + 1 if m else len(tail) // 2)]
    if shape == "nul_tail":
        n = min(64, len(tail))
        return b[:len(b) - n] + b"\0" * n
    if shape == "wrong_type":
        return b"[1, 2]\nnull\n" if b.lstrip().startswith(b"{") and b"\n{" in b else b"[1, 2]\n"
    if shape == "not_utf8":
        return b[:last] + b"\xff\xfe" + tail
    raise AssertionError(shape)


def damage(out: Path, ledger: Path, what: str, shape: str) -> bool:
    """Damage WHAT of the attempt in OUT in SHAPE; False when the file is not there to damage."""
    if what == "ledger":
        p = ledger / f"attempt-{SR._read(out / 'run.json')['series']['attempt']:02d}.json" \
            if SR._read(out / "run.json") else None
    elif ":" in what:
        # a field: absent, another JSON type, or a value of the right type that does not read
        name, field = what.split(":")
        p = out / name
        rj = json.loads(p.read_text())
        if shape == "absent":
            rj.pop(field, None)
        else:
            rj[field] = [1, 2] if shape == "wrong_type" else ("agreed" if field == "notes_witness" else
                                                              "2026-13-45T99:99:99Z" if shape != "empty" else "")
        p.write_text(json.dumps(rj))
        return True
    else:
        p = out / what
    if p is None or not p.is_file():
        return False
    if shape == "absent":
        p.unlink()
    else:
        p.write_bytes(_cut(p.read_bytes(), shape, lines=p.suffix == ".jsonl"))
    return True


def run(fn, *args) -> tuple[object, list[str]]:
    o, e = io.StringIO(), io.StringIO()
    with redirect_stdout(o), redirect_stderr(e):
        rc = fn(*args)
    return rc, (o.getvalue() + e.getvalue()).splitlines()


def readings(s: Series, dirs: list[Path]) -> tuple[dict, dict, bool]:
    try:
        led = S.stop_state(s.ser, SR.ledger(s.ser))
    except SR.LedgerUnreadable:
        led = None
    gate = S.series_gate(dirs, S.read_ledger(s.root / "st.ledger"))
    refused = gate["series"] == "mixed" or bool(gate.get("refused"))
    return led, gate, refused


@pytest.mark.parametrize("moment", ["before_end", "after_end"])
@pytest.mark.parametrize("what", FILES)
def test_a_damaged_run_file_never_raises_and_every_outcome_is_named(tmp_path, monkeypatch, episode, moment, what):
    problems = []
    for shape in SHAPES:
        root = tmp_path / shape
        root.mkdir()
        s = Series(root, episode, monkeypatch)
        a1 = s.attempt(1)
        s.ended(a1)
        a2 = s.attempt(2)
        if moment == "after_end":
            s.ended(a2)
        elif what == "result.json":
            continue                                  # written only after the end
        if not damage(a2, root / "st.ledger", what, shape):
            continue
        out = []
        try:
            for name, argv in (("end", ["end", str(a2)]), ("close", ["close", str(a2)]),
                               ("withdraw", ["withdraw", str(a2)]), ("result", ["result", str(a2)]),
                               ("slot", ["slot", "a", "st-a03-a"])):
                rc, lines = run(SR.main, argv)
                out.append((name, rc, lines))
            rc, lines = run(S.main, [str(a1), str(a2), "--json", S.OVERRIDE_FLAG])
            out.append(("score", rc, lines))
            rc, lines = run(SR.main, ["publish", str(a2), str(root / "pub")])
            out.append(("publish", rc, lines))
            led, gate, refused = readings(s, [a1, a2])
            row = S.read_run(a2)
        except Exception as e:  # noqa: BLE001 - the property: nothing raises
            problems.append((shape, "RAISES", type(e).__name__, str(e)[:120]))
            continue
        for name, rc, lines in out:
            bad = [ln for ln in lines if ln.startswith("REFUSE") and not REFUSE_LINE.match(ln)]
            if bad:
                problems.append((shape, name, "an unnamed refusal", bad[:1]))
        end_rc, end_lines = out[0][1], out[0][2]
        # a field left out is not damage of the file (a record of an older writer); its value read as absent is
        # the identity check's (notes_witness:None) or nothing (launch_utc: no wall time)
        field_absent = ":" in what and shape == "absent"
        rj, rj_state = S.read_run_json(a2)
        if moment == "before_end" and end_rc == 0 and rj is None:
            # a run.json that does not read: the ledger entry is the attempt's record (ended, invalid, its damage)
            le = S.read_object(root / "st.ledger" / "attempt-02.json")[0] or {}
            if (le.get("ended"), le.get("valid"), le.get("reasons")) != (True, False, [S.run_json_damage(rj_state)]):
                problems.append((shape, "end exited 0 on a run.json that does not read and the ledger entry does "
                                 "not record it", le))
        elif moment == "before_end" and end_rc == 0 and not isinstance(rj.get("validity"), dict):
            problems.append((shape, "end exited 0 and wrote no validity"))
        if moment == "before_end" and end_rc != 0 and not any(REFUSE_LINE.match(x) for x in end_lines):
            problems.append((shape, "end failed without a named refusal", end_lines[-2:]))
        if moment == "before_end" and what in VALIDITY_FILES and end_rc == 0 and rj is not None:
            reasons = rj["validity"]["reasons"]
            if (rj["validity"]["valid"] and not field_absent) or not field_absent and not any(x.startswith((
                    S.FILE_UNREADABLE, S.FILE_ABSENT, "witness_absent:end_json",
                                                                 "notes_witness:", "no_trajectory"))
                                                  for x in reasons):
                problems.append((shape, "the damaged file is not in the validity", reasons))
        if moment == "after_end" and what in VALIDITY_FILES + ("run.json", "ledger") and not refused \
                and not field_absent:
            problems.append((shape, "the score did not refuse a damaged record of an ended attempt", gate.get("why")))
        if not refused and led is not None:
            # an attempt `series end` reached and `series close` did not finish counts as recorded (the scorer names
            # `series close` for it)
            if (gate["valid"], gate["attempts"]) != (led["valid"], led["attempts"]) and \
                    "series close" not in str(gate.get("why")):
                problems.append((shape, "the slot and the scorer disagree", led, gate))
        if not row.get("valid") is False and what in VALIDITY_FILES and not field_absent:
            problems.append((shape, "the scorer's row is valid with a damaged file", row.get("invalid_reasons")))
    assert not problems, problems


def test_CONTROL_the_damage_shapes_are_damage(tmp_path):
    """Each shape of each text file is one the readers see: read_jsonl or read_object says why, and the undamaged
    bytes read whole."""
    line = json.dumps({"kind": "step", "game_date": "1066.9.15", "n": 123456}).encode() + b"\n"
    jl = line * 3
    ob = json.dumps({"end_reason": "episode_over", "n": 123456}).encode()
    for shape in SHAPES[1:]:
        p = tmp_path / f"{shape}.jsonl"
        p.write_bytes(_cut(jl, shape))
        rows, why = S.read_jsonl(p)
        assert why or (shape == "empty" and rows == []), shape
        q = tmp_path / f"{shape}.json"
        q.write_bytes(_cut(ob, shape, lines=False))
        assert S.read_object(q)[1], shape
    p = tmp_path / "whole.jsonl"
    p.write_bytes(jl)
    assert S.read_jsonl(p) == ([json.loads(line)] * 3, None)


def test_CONTROL_series_end_writes_a_validity_when_its_reading_fails(tmp_path, monkeypatch, episode):
    """`series end` whose reading raises an error of its own writes an invalid validity naming it (`end_failed`), in
    run.json and the ledger: a played-out attempt never stays without one. MUTANT: the reading's guard removed
    (cmd_end calls end_reading bare), the error escapes and nothing is written."""
    s = Series(tmp_path, episode, monkeypatch)
    a1 = s.attempt(1)

    def boom(*a):
        raise KeyError("start")
    monkeypatch.setattr(SR, "end_reading", boom)
    rc, lines = run(SR.cmd_end, a1)
    v = json.loads((a1 / "run.json").read_text())["validity"]
    assert rc == 0 and v["valid"] is False and "end_failed:KeyError" in v["reasons"]
    e = json.loads((tmp_path / "st.ledger" / "attempt-01.json").read_text())
    assert (e["ended"], e["valid"], e["reasons"]) == (True, False, v["reasons"])
    b = s.attempt(3)
    import inspect
    src = inspect.getsource(SR.cmd_end)
    assert "end, reasons = end_reading(out, rj)" in src and "except Exception" in src
    with pytest.raises(KeyError):
        SR.end_reading(b, json.loads((b / "run.json").read_text()))


def test_CONTROL_the_one_line_between_withdraw_and_end(tmp_path, monkeypatch, episode):
    """scorer.played_out: the trajectory's end row, `done`, an end.json of the runner's own end (or one that does not
    read) and the end marker make an attempt played out (`series withdraw` refuses `withdraw_played`; `series end`
    runs); an end.json of `stopped_by_signal`, no `done` and no end row (the operator's stop, a host shutting down,
    before the episode's end) leaves it withdrawable (`series end` refuses `end_not_played`)."""
    s = Series(tmp_path, episode, monkeypatch)
    a1 = s.attempt(1)
    (a1 / S.DONE_FILE).unlink()
    (a1 / "end.json").write_text(json.dumps({"end_reason": "stopped_by_signal", "at": END_AT}))
    (a1 / "trajectory.jsonl").write_bytes(before_end_row(episode["trajectory.jsonl"]))   # stopped before the end row
    assert S.played_out(a1) is None
    rc, lines = run(SR.cmd_end, a1)
    assert rc == 6 and lines[0].startswith("REFUSE end_not_played")
    for make in (lambda d: (d / S.DONE_FILE).write_text("rc=0\n"),
                 lambda d: (d / "end.json").write_text(json.dumps({"end_reason": "wall_budget"})),
                 lambda d: (d / "end.json").write_bytes(b'{"end_reason": "stopp'),
                 lambda d: (d / S.END_MARKER).write_text("rc=1\n"),
                 lambda d: (d / "trajectory.jsonl").write_bytes(episode["trajectory.jsonl"])):
        b = tmp_path / "copy"
        import shutil
        shutil.copytree(a1, b)
        make(b)
        assert S.played_out(b), make
        shutil.rmtree(b)
    (a1 / "end.json").write_text(json.dumps({"end_reason": "episode_over", "at": END_AT}))
    rc, lines = run(SR.cmd_withdraw, a1)
    assert rc == 6 and lines[0].startswith("REFUSE withdraw_played") and "series end" in lines[0] \
        and "series close" in lines[0]
    assert not (a1 / S.WITHDRAWN_FILE).exists()


def before_end_row(traj: bytes) -> bytes:
    """The trajectory as the game wrote it before the env's end row."""
    lines = traj.splitlines(keepends=True)
    return b"".join(lines[:next(i for i, ln in enumerate(lines) if b'"kind": "end"' in ln)])


def counts(s: Series, dirs: list[Path]) -> tuple:
    """(the slot's (valid, attempts), the scorer's (valid, attempts), the scorer's refusals)."""
    led = S.stop_state(s.ser, SR.ledger(s.ser))
    gate = S.series_gate(dirs, S.read_ledger(s.root / "st.ledger"))
    return (led["valid"], led["attempts"]), (gate.get("valid"), gate.get("attempts")), gate.get("refused")


#: how the post-episode hold was cut: KILL (a host down: no end.json), INT (Ctrl-C) and TERM (a shutdown): the
#: runner's end.json `stopped_by_signal`; none of them leaves `done`
HOLD_STOPS = {"kill": None, "int": "stopped_by_signal: SIGINT", "term": "stopped_by_signal: SIGTERM"}


def held(s: Series, how: str) -> Path:
    """Attempt 2 stopped in its post-episode hold, its end row written (the trajectory whole), in HOW."""
    a2 = s.attempt(2)
    (a2 / S.DONE_FILE).unlink()
    if HOLD_STOPS[how] is None:
        (a2 / "end.json").unlink()
    else:
        (a2 / "end.json").write_text(json.dumps({"end_reason": HOLD_STOPS[how], "at": END_AT}))
    return a2


@pytest.mark.parametrize("how", sorted(HOLD_STOPS))
def test_an_attempt_stopped_after_its_end_row_is_played_out_and_counted(tmp_path, monkeypatch, episode, how):
    """The env wrote its end row (the outcome is fixed) and the driver and runner were stopped in the hold, before
    `done` and before the runner's own end.json: the attempt is played out. `series withdraw` refuses
    (`withdraw_played`), the driver's message names `series end`, `series end` by hand writes it invalid, `series
    close` closes it, and the slot and the scorer count it as an attempt."""
    from ck3bench.harness import driver
    s = Series(tmp_path, episode, monkeypatch)
    a1 = s.attempt(1)
    s.ended(a1)
    a2 = held(s, how)
    assert S.played_out(a2).startswith("the trajectory's end row")
    rc, lines = run(SR.cmd_withdraw, a2)
    assert rc == 6 and lines[0].startswith("REFUSE withdraw_played") and not (a2 / S.WITHDRAWN_FILE).exists()
    d = driver.Driver.__new__(driver.Driver)
    d.out, d.log, d.post = a2, lambda m: None, lambda *a: None
    rc_, lines = run(d.unfinished)
    assert any("series end" in x and "series withdraw" not in x for x in lines), lines
    rc, lines = run(SR.cmd_end, a2)
    v = json.loads((a2 / "run.json").read_text())["validity"]
    assert rc == 0 and v["valid"] is False
    assert run(SR.cmd_close, a2)[0] == 0
    slot, scorer, refused = counts(s, [a1, a2])
    assert slot == scorer == (1, 2) and not refused


def test_MUTANT_without_the_end_row_rule_a_played_attempt_is_withdrawn(tmp_path, monkeypatch, episode):
    """A narrower rule, played_out reading `done` and end.json only and no end row: the attempt killed in its hold is
    withdrawn and leaves the count, (1, 2) becoming (1, 1). The test above is red on that rule."""
    s = Series(tmp_path, episode, monkeypatch)
    a1 = s.attempt(1)
    s.ended(a1)
    a2 = held(s, "kill")
    monkeypatch.setattr(S, "trajectory_end", lambda d: None)
    assert S.played_out(a2) is None
    assert run(SR.cmd_withdraw, a2)[0] == 0
    slot, scorer, _ = counts(s, [a1, a2])
    assert slot == scorer == (1, 1)


def test_a_cut_end_row_is_no_end_row(tmp_path, monkeypatch, episode):
    """A host down while the env wrote its end row leaves half a line: that is no end row (the rule in the series'
    document, section 4), the attempt was not played out, and it is withdrawn; the same row whole is one."""
    s = Series(tmp_path, episode, monkeypatch)
    a1 = s.attempt(1)
    s.ended(a1)
    a2 = held(s, "kill")
    head = before_end_row(episode["trajectory.jsonl"])
    row = episode["trajectory.jsonl"][len(head):]
    (a2 / "trajectory.jsonl").write_bytes(head + row[:len(row) // 2])
    assert S.played_out(a2) is None
    (a2 / "trajectory.jsonl").write_bytes(head + row.rstrip(b"\n"))       # whole, before its newline
    assert S.played_out(a2)
    (a2 / "trajectory.jsonl").write_bytes(head + row[:len(row) // 2])
    assert run(SR.cmd_withdraw, a2)[0] == 0
    slot, scorer, refused = counts(s, [a1, a2])
    assert slot == scorer == (1, 1) and not refused


#: run.json damaged after `series start` (the ledger entry open, the game played or not): the shapes
RUN_JSON_DAMAGE = {"nul_tail": lambda b: b[:-32] + b"\0" * 32, "cut": lambda b: b[:len(b) // 2], "list": lambda b: b"[]",
                   "absent": None, "series_text": "series", "attempt_text": "attempt", "schedule_int": "schedule",
                   "run_id_list": "run_id", "start_list": "start", "expectation_sha_list": "expectation"}


def damage_run_json(out: Path, shape: str) -> None:
    how = RUN_JSON_DAMAGE[shape]
    p = out / "run.json"
    if how is None:
        p.unlink()
    elif callable(how):
        p.write_bytes(how(p.read_bytes()))
    else:
        rj = json.loads(p.read_text())
        if how == "series":
            rj["series"] = "st"
        elif how in ("attempt", "schedule"):
            rj["series"][how] = "2" if how == "attempt" else 5
        elif how == "expectation":
            rj["expectation"]["sha256"] = ["9" * 64]
        else:
            rj[how] = [1, 2]
        p.write_text(json.dumps(rj))


@pytest.mark.parametrize("shape", sorted(RUN_JSON_DAMAGE))
def test_a_run_json_damaged_after_its_start_is_counted_invalid_by_the_documented_route(tmp_path, monkeypatch, episode,
                                                                                         shape):
    """The recovery table's row, followed literally: `series end OUT` records the attempt in its ledger entry
    (ended, invalid, `file_unreadable:run.json` or `file_absent:run.json`, counted) and writes the end marker;
    `series result` refuses and prints no outcome, `series withdraw` and `series close` refuse by name; the next
    attempt plays, and the slot and the scorer count the same, the scorer scoring the stopped series. Nothing
    raises."""
    s = Series(tmp_path, episode, monkeypatch)
    a1 = s.attempt(1)
    s.ended(a1)
    a2 = s.attempt(2)
    damage_run_json(a2, shape)
    for fn in (SR.cmd_withdraw, SR.cmd_close, SR.cmd_result):
        rc, lines = run(fn, a2)
        assert rc == 6 and REFUSE_LINE.match(lines[0]) and "success" not in " ".join(lines), (fn.__name__, lines)
    assert not (a2 / "result.json").exists() and not (a2 / S.WITHDRAWN_FILE).exists()
    rc, lines = run(SR.cmd_end, a2)
    want = "file_absent:run.json" if shape == "absent" else "file_unreadable:run.json"
    assert rc == 0 and json.loads(lines[-1]) == {"valid": False, "reasons": [want], "recorded_in": "attempt-02.json"}
    e = json.loads((tmp_path / "st.ledger" / "attempt-02.json").read_text())
    assert (e["ended"], e["valid"], e["not_attempt"], e["reasons"]) == (True, False, False, [want])
    assert (a2 / S.END_MARKER).is_file()
    rc, lines = run(SR.cmd_end, a2)
    assert rc == 6 and REFUSE_LINE.match(lines[0])                       # once
    a3 = s.attempt(3)
    s.ended(a3)
    slot, scorer, refused = counts(s, [a1, a2, a3])
    assert slot == scorer == (2, 3) and not refused
    rc, lines = run(S.main, [str(a1), str(a2), str(a3), "--json"])
    assert rc == 0, lines
    doc = json.loads("\n".join(lines))
    row = next(r for r in doc["runs"] if r["run"] == a2.name)
    assert (row["attempt"], row["valid"], row["invalid_reasons"]) == (2, False, [want])
    assert doc["score"]["attempts"] == 3
    assert run(SR.main, ["publish", str(a2), str(tmp_path / "pub")])[0] in (0, 5)


@pytest.mark.parametrize("shape", ["nul_tail", "absent", "series_text", "series_null"])
def test_series_result_writes_and_prints_no_outcome_without_a_readable_series_run_json(tmp_path, monkeypatch, episode,
                                                                                       shape):
    """A series attempt in flight whose run.json does not read, is gone, or whose `series` is text or null: `series
    result` refuses by name, writes no result.json and prints no success value. CONTROL: the intact attempt, once
    ended, is sealed."""
    s = Series(tmp_path, episode, monkeypatch)
    a1 = s.attempt(1)
    if shape == "series_null":
        rj = json.loads((a1 / "run.json").read_text())
        rj["series"] = None
        (a1 / "run.json").write_text(json.dumps(rj))
    else:
        damage_run_json(a1, shape)
    rc, lines = run(SR.cmd_result, a1)
    assert rc == 6 and lines[0].startswith("REFUSE result_") and not (a1 / "result.json").exists()
    assert not any(k in " ".join(lines) for k in ('"success"', "goal", "counties_last", "last_date")), lines
    b = s.attempt(3)
    assert run(SR.cmd_end, b)[0] == 0
    rc, lines = run(SR.cmd_result, b)
    assert rc == 0 and json.loads((b / "result.json").read_text())["success"] == SR.SEALED


def test_a_second_file_for_one_ledger_number_is_refused_by_name_by_slot_and_scorer(tmp_path, monkeypatch, episode):
    """attempt-1.json beside attempt-01.json (a hand rebuild with the wrong name): the slot, every step and the
    scorer refuse naming the file; they never read two entries for one attempt."""
    s = Series(tmp_path, episode, monkeypatch)
    a1 = s.attempt(1)
    s.ended(a1)
    led = tmp_path / "st.ledger"
    (led / "attempt-1.json").write_bytes((led / "attempt-01.json").read_bytes())
    rc, lines = run(SR.main, ["slot", "b", "st-a02-b"])
    assert rc == 7 and "series_ledger_unreadable: attempt-1.json" in lines[0]
    gate = S.series_gate([a1], S.read_ledger(led))
    assert any(x.startswith("series_ledger_unreadable: attempt-1.json") for x in gate["refused"])
    assert S.ledger_number("attempt-1.json") == (1, "not the name `series start` writes (attempt-01.json)")
    assert S.ledger_number("attempt-01.json") == (1, None) and S.ledger_number("attempt-100.json") == (100, None)


@pytest.mark.parametrize("field,value", [("run_id", [1]), ("ended", "false"), ("valid", "true"),
                                         ("reasons", {"a": 1}), ("host", 1), ("started_utc", "2026-01-01T00:02:00")])
def test_a_ledger_entry_of_the_wrong_type_is_a_named_refusal_everywhere(tmp_path, monkeypatch, episode, field, value):
    """A hand-edited ledger entry whose field is of another type (or a start time with no zone): every step and the
    scorer refuse by name (`series_ledger_unreadable`); nothing raises."""
    s = Series(tmp_path, episode, monkeypatch)
    a1 = s.attempt(1)
    s.ended(a1)
    a2 = s.attempt(2)
    f = tmp_path / "st.ledger" / "attempt-02.json"
    e = json.loads(f.read_text())
    e[field] = value
    f.write_text(json.dumps(e))
    steps = [] if field == "started_utc" else [
        run(SR.main, argv) for argv in (["end", str(a2)], ["withdraw", str(a2)], ["close", str(a2)],
                                        ["slot", "a", "st-a03-a"])]
    scored = run(S.main, [str(a1), str(a2), "--json", S.OVERRIDE_FLAG])
    for rc, lines in steps + [scored]:
        assert rc != 0 and any(REFUSE_LINE.match(x) for x in lines), lines
    assert "series_ledger_unreadable" in " ".join(scored[1])
    assert all("series_ledger_unreadable" in " ".join(lines) for _, lines in steps[:1] + steps[3:])


@pytest.mark.parametrize("skew_s", [-3600, 3600])
def test_a_host_clock_an_hour_off_does_not_wedge_the_series(tmp_path, monkeypatch, episode, skew_s):
    """Host b's clock an hour behind or ahead of host a's: the ledger's start times interleave out of order across
    the hosts, and the series scores (across hosts the order is the attempt number). On one host, a start more than
    the allowance before that host's earlier start refuses, saying what it read."""
    s = Series(tmp_path, episode, monkeypatch)
    dirs = []
    for a in (1, 2, 3):
        out = s.attempt(a)
        f = tmp_path / "st.ledger" / f"attempt-{a:02d}.json"
        e = json.loads(f.read_text())
        t = 10 * a + (skew_s if a == 2 else 0)
        e["started_utc"] = S.dt.datetime.fromtimestamp(1767225600 + 3600 + t, S.dt.timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ")
        f.write_text(json.dumps(e))
        s.ended(out)
        dirs.append(out)
    gate = S.series_gate(dirs, S.read_ledger(tmp_path / "st.ledger"))
    assert not gate["refused"] and gate["stopped"], gate
    f = tmp_path / "st.ledger" / "attempt-03.json"
    e = json.loads(f.read_text())
    e["started_utc"] = "2026-01-01T00:00:00Z"                            # host a's own clock set back an hour
    f.write_text(json.dumps(e))
    gate = S.series_gate(dirs, S.read_ledger(tmp_path / "st.ledger"))
    assert any(x.startswith("series_ledger_order: attempt 3 on host a started") and "same host" in x
               for x in gate["refused"]), gate["refused"]


#: the values a field of another type takes (each JSON type, and a boolean where a number or text is read)
OTHER_TYPES = {"null": None, "true": True, "int": 7, "float": 1.5, "text": "x", "list": [1], "object": {"a": 1}}
#: the records whose every field the fuzz below sets to each of OTHER_TYPES: run.json (to three levels: its `start`
#: and `end` identity), the runner's end.json, and the attempt's ledger entry
FUZZED = ("run.json", "end.json", "ledger")


def field_paths(x, pre=(), depth=0):
    """Every field of a JSON value, to three levels (a list's first item stands for its items)."""
    if depth >= 3:
        return
    items = x.items() if isinstance(x, dict) else ([(0, x[0])] if isinstance(x, list) and x else [])
    for k, v in items:
        yield pre + (k,)
        yield from field_paths(v, pre + (k,), depth + 1)


def _set(x, path, v):
    for k in path[:-1]:
        x = x[k]
    x[path[-1]] = v


@pytest.mark.parametrize("moment", ["before_end", "after_end"])
@pytest.mark.parametrize("what", FUZZED)
def test_every_field_the_kit_writes_of_another_type_is_a_named_outcome_for_every_command(tmp_path, monkeypatch,
                                                                                          episode, moment, what):
    """Every field of `what` (run.json with its `start` and `end` identity, end.json, the ledger entry) set to each
    value of OTHER_TYPES, at two moments (played out and not ended; ended and closed), against every command an
    operator runs on it (end, close, withdraw, result, slot, score with and without --json, publish): none raises,
    and a refusal names its code. CONTROL: the fields are found (the records are not empty), and a field the
    scorer reads in a set (`start.git.commit`) is among them."""
    base = tmp_path / "base"
    base.mkdir()
    s = Series(base, episode, monkeypatch)
    a1 = s.attempt(1)
    s.ended(a1)
    a2 = s.attempt(2)
    if moment == "after_end":
        s.ended(a2)
    rel = Path("st.ledger/attempt-02.json") if what == "ledger" else a2.relative_to(base) / what
    obj = json.loads((base / rel).read_text())
    paths = list(field_paths(obj))
    assert paths and (what != "run.json" or ("start", "git", "commit") in paths), paths
    problems = []
    for path in paths:
        for name, v in OTHER_TYPES.items():
            w = tmp_path / "w"
            if w.exists():
                shutil.rmtree(w)
            shutil.copytree(base, w)
            monkeypatch.setenv("RATE_LEDGER", str(w / "st.ledger"))
            o = json.loads((w / rel).read_text())
            _set(o, path, v)
            (w / rel).write_text(json.dumps(o))
            b1, b2 = w / a1.name, w / a2.name
            for cmd, fn, argv in (("end", SR.main, ["end", str(b2)]), ("close", SR.main, ["close", str(b2)]),
                                  ("withdraw", SR.main, ["withdraw", str(b2)]), ("result", SR.main, ["result", str(b2)]),
                                  ("slot", SR.main, ["slot", "a", "st-a03-a"]), ("score", S.main, [str(b1), str(b2)]),
                                  ("score json", S.main, [str(b1), str(b2), "--json", S.OVERRIDE_FLAG]),
                                  ("publish", SR.main, ["publish", str(b2), str(w / "pub")])):
                try:
                    _, lines = run(fn, argv)
                except Exception as e:  # noqa: BLE001 - the property: nothing raises
                    problems.append((".".join(map(str, path)), name, cmd, f"{type(e).__name__}: {str(e)[:80]}"))
                    continue
                bad = [ln for ln in lines if ln.startswith("REFUSE") and not REFUSE_LINE.match(ln)]
                if bad:
                    problems.append((".".join(map(str, path)), name, cmd, bad[:1]))
    assert not problems, (len(problems), problems[:6])
    monkeypatch.setenv("RATE_LEDGER", str(base / "st.ledger"))


def test_MUTANT_a_nested_identity_field_unchecked_raises_in_the_score(tmp_path, monkeypatch, episode):
    """The rule above is what holds it: with run.json's `start` and `end` identity unchecked and read as found,
    `start.git.commit` as a list makes the score raise; with the check, it is a named refusal."""
    s = Series(tmp_path, episode, monkeypatch)
    a1 = s.attempt(1)
    s.ended(a1)
    a2 = s.attempt(2)
    s.ended(a2)
    rj = json.loads((a2 / "run.json").read_text())
    rj["start"]["git"]["commit"] = [1]
    (a2 / "run.json").write_text(json.dumps(rj))
    rc, lines = run(S.main, [str(a1), str(a2)])
    assert rc != 0 and any("start.git.commit" in x for x in lines), lines
    monkeypatch.setattr(S, "RUN_JSON_IDENTITY_TYPES", {})
    monkeypatch.setattr(S, "run_identity", lambda rj: {k: ((rj.get("start") or {}).get("git") or {}).get("commit")
                                                       for k in S.IDENTITY_KEYS + S.IDENTITY_INFO_KEYS})
    with pytest.raises(TypeError):
        run(S.main, [str(a1), str(a2)])


#: the ends the runner decides itself (runner.Run.run_fresh / run_policy): the seat exited, stopped or hung, the wall
#: budget ran out. The runner then closes the session and the game (env play writes its end row `closed`).
RUNNER_ENDS = ("seat_exited", "seat_stopped", "seat_hung", "wall_budget")


class Killed(BaseException):
    """The runner's host went down (a kill, which no `except Exception` sees)."""


class _Closing:
    """`env play` still running when the runner's finish closes it; the kill lands as the runner signals it."""
    returncode = None

    def poll(self):
        return None

    @property
    def pid(self):
        raise Killed()


def killed_while_closing(out: Path, end_reason: str) -> None:
    """The runner's own finish (runner.Run.finish) with END_REASON decided, killed as it closes `env play`: what it
    leaves on the disk is what a host that went down in that window leaves. The env's end row is then `closed` (the
    runner had begun to close it)."""
    r = runner.Run.__new__(runner.Run)
    r.out, r.end_reason, r.sess, r.play, r.sessions = out, end_reason, None, _Closing(), 1
    r.t0 = 0.0
    r.a = type("A", (), {"turn_mode": "fresh", "policy": None, "notes": "on", "hold_before_close_s": 0.0})()
    r.L = lambda m: None
    with pytest.raises(Killed):
        r.finish()
    rows = (out / "trajectory.jsonl").read_bytes().splitlines(keepends=True)
    i = next(n for n, ln in enumerate(rows) if b'"kind": "end"' in ln)
    end = json.loads(rows[i])
    end["reason"] = "closed"
    (out / "trajectory.jsonl").write_bytes(b"".join(rows[:i]) + json.dumps(end).encode() + b"\n")


@pytest.mark.parametrize("end_reason", RUNNER_ENDS)
def test_an_end_the_runner_decided_is_played_out_when_its_host_goes_down_while_closing(tmp_path, monkeypatch, episode,
                                                                                       end_reason):
    """The runner decided the end (the seat exited, stopped or hung; the wall budget) and its host went down while it
    closed the session and the game, before `done` and before its end record was whole: the end reason is on the
    disk first (end.json `final: false`), so the attempt is played out. `series withdraw` refuses, `series end`
    writes it invalid (`witness_absent:end_json_final`), and the slot and the scorer count it as an attempt."""
    s = Series(tmp_path, episode, monkeypatch)
    a1 = s.attempt(1)
    s.ended(a1)
    a2 = s.attempt(2)
    (a2 / S.DONE_FILE).unlink()
    (a2 / "end.json").unlink()
    killed_while_closing(a2, end_reason)
    ej = json.loads((a2 / "end.json").read_text())
    assert (ej["end_reason"], ej["final"]) == (end_reason, False)
    assert S.trajectory_end(a2) is None and S.played_out(a2).startswith("end.json")
    rc, lines = run(SR.cmd_withdraw, a2)
    assert rc == 6 and lines[0].startswith("REFUSE withdraw_played") and not (a2 / S.WITHDRAWN_FILE).exists()
    assert run(SR.cmd_end, a2)[0] == 0
    v = json.loads((a2 / "run.json").read_text())["validity"]
    assert v["valid"] is False and "witness_absent:end_json_final" in v["reasons"], v
    assert run(SR.cmd_close, a2)[0] == 0
    slot, scorer, refused = counts(s, [a1, a2])
    assert slot == scorer == (1, 2) and not refused


def test_MUTANT_an_end_recorded_only_after_the_close_is_withdrawn(tmp_path, monkeypatch, episode):
    """The other order, the runner's end.json written only once the session and the game are closed: the same kill
    leaves no end.json and a `closed` end row: nothing records the end, `series withdraw` takes the attempt out and
    (1, 2) becomes (1, 1). The test above is red on that order."""
    s = Series(tmp_path, episode, monkeypatch)
    a1 = s.attempt(1)
    s.ended(a1)
    a2 = s.attempt(2)
    (a2 / S.DONE_FILE).unlink()
    (a2 / "end.json").unlink()
    real = runner.write_whole
    monkeypatch.setattr(runner, "write_whole", lambda p, text: None if '"final": false' in text else real(p, text))
    killed_while_closing(a2, "seat_exited")
    assert not (a2 / "end.json").exists() and S.played_out(a2) is None
    assert run(SR.cmd_withdraw, a2)[0] == 0
    slot, scorer, _ = counts(s, [a1, a2])
    assert slot == scorer == (1, 1)


@pytest.mark.parametrize("arg", [".", "./"])
def test_a_step_given_the_run_directory_as_dot_reads_it_by_its_own_name(tmp_path, monkeypatch, episode, arg):
    """`series end .` from inside a run directory whose run.json does not read: the step reads the directory by its
    own name (its ledger entry), as with the absolute path, never as a run id that no attempt has."""
    s = Series(tmp_path, episode, monkeypatch)
    a1 = s.attempt(1)
    s.ended(a1)
    a2 = s.attempt(2)
    (a2 / "run.json").write_bytes((a2 / "run.json").read_bytes()[:40])
    monkeypatch.chdir(a2)
    rc, lines = run(SR.main, ["end", arg])
    assert rc == 0 and not any("end_no_start" in x for x in lines), lines
    e = json.loads((tmp_path / "st.ledger" / "attempt-02.json").read_text())
    assert (e["ended"], e["valid"], e["reasons"]) == (True, False, ["file_unreadable:run.json"])


@pytest.mark.parametrize("holder", [b"\xff\xfe ck3bench-series x", b"\0" * 40])
def test_a_lock_file_that_is_not_text_is_read_without_a_traceback(tmp_path, monkeypatch, episode, holder):
    """A lock's holder (and its game list) that is not UTF-8 or is NULs, read by `series withdraw` and `series
    close`: each answers with its own outcome (a refusal by name, or the withdrawal), never a decoding error."""
    s = Series(tmp_path, episode, monkeypatch)
    a1 = s.attempt(1)
    s.ended(a1)
    a2 = s.attempt(2)
    (a2 / S.DONE_FILE).unlink()
    (a2 / "end.json").unlink()
    (a2 / "trajectory.jsonl").write_bytes(before_end_row((a2 / "trajectory.jsonl").read_bytes()))
    lock = tmp_path / "lk"
    lock.mkdir()
    (lock / "holder").write_bytes(holder)
    (lock / "game.list").write_bytes(b"\xff pid 1 exe /x run r\n")
    (a2 / "driver.log").write_text(f"2026-01-01T00:00:00Z lock taken: {lock}: ck3bench-series "
                                   f"2026-01-01T00:00:00Z st-a02-b pid {__import__('os').getpid()}\n")
    for cmd in ("close", "withdraw"):
        rc, lines = run(SR.main, [cmd, str(a2)])
        assert not any("UnicodeDecodeError" in x for x in lines), (cmd, lines)
        assert rc == 0 or (lines and REFUSE_LINE.match(lines[0])), (cmd, rc, lines)


# ── analyze on the shapes a crash leaves: a named line, never a traceback ──────────────────────────────────────

_HEAD = {"kind": "header", "wall_utc": "2026-01-01T00:00:00Z", "scenario": {"cycle_days": 30}}
_STEP = {"kind": "step", "step": 1, "wall_utc": "2026-01-01T00:01:00Z"}
_EV = [{"type": "system", "subtype": "init"},
       {"type": "assistant", "message": {"id": "m1", "content": [{"type": "tool_use", "id": "t1", "name": "x__end_turn",
                                                                   "input": {}}], "usage": {"input_tokens": 3}}},
       {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t1", "content": "ok"}]}}]
ANALYZE_SHAPES = {
    "trajectory_empty": ("trajectory.jsonl", b""),
    "trajectory_cut": ("trajectory.jsonl", (json.dumps(_HEAD) + "\n" + json.dumps(_STEP)[:20]).encode()),
    "trajectory_nul": ("trajectory.jsonl", (json.dumps(_HEAD) + "\n").encode() + b"\0" * 64),
    "trajectory_no_kind": ("trajectory.jsonl", (json.dumps({"wall_utc": "x"}) + "\n").encode()),
    "wall_utc_a_number": ("trajectory.jsonl", (json.dumps(dict(_HEAD, wall_utc=5)) + "\n" + json.dumps(_STEP)).encode()),
    "scenario_text": ("trajectory.jsonl", (json.dumps(dict(_HEAD, scenario="s")) + "\n" + json.dumps(_STEP)).encode()),
    "transcript_absent": ("transcript.jsonl", None),
    "transcript_cut": ("transcript.jsonl", ("\n".join(json.dumps(e) for e in _EV))[:-30].encode()),
    "transcript_message_text": ("transcript.jsonl", (json.dumps({"type": "assistant", "message": "text"}) + "\n").encode()),
}


@pytest.mark.parametrize("shape", sorted(ANALYZE_SHAPES))
def test_analyze_says_what_did_not_read_instead_of_a_traceback(tmp_path, capsys, shape):
    """`analyze` (the driver's post step) on a trajectory or transcript left empty, cut, NUL-tailed, absent or with
    a field of another type: exit 0, analysis.json written, and a line `analyze: <file>: ...` naming what did not
    read (it raised StopIteration, JSONDecodeError, AttributeError, TypeError or KeyError before)."""
    from ck3bench.harness import analyze as A
    run = tmp_path / "run"
    run.mkdir()
    (run / "trajectory.jsonl").write_text(json.dumps(_HEAD) + "\n" + json.dumps(_STEP) + "\n")
    (run / "transcript.jsonl").write_text("\n".join(json.dumps(e) for e in _EV) + "\n")
    name, raw = ANALYZE_SHAPES[shape]
    if raw is None:
        (run / name).unlink()
    else:
        (run / name).write_bytes(raw)
    assert A.main(str(run)) == 0
    said = capsys.readouterr().out
    assert f"analyze: {name}:" in said, said
    assert json.loads((run / "analysis.json").read_text())["summary"]["not_read"]


def test_CONTROL_analyze_on_whole_files_says_nothing_did_not_read(tmp_path, capsys):
    from ck3bench.harness import analyze as A
    run = tmp_path / "run"
    run.mkdir()
    (run / "trajectory.jsonl").write_text(json.dumps(_HEAD) + "\n" + json.dumps(_STEP) + "\n")
    (run / "transcript.jsonl").write_text("\n".join(json.dumps(e) for e in _EV) + "\n")
    assert A.main(str(run)) == 0
    assert "analyze:" not in capsys.readouterr().out
    summ = json.loads((run / "analysis.json").read_text())["summary"]
    assert "not_read" not in summ and summ["turns"] == 1 and summ["game_days"] == 30


# ── run.json `declared`: every member there and text, or a named refusal; never the step's environment ─────────────

DECLARED_SHAPES = {"block_text": "x", "block_list": [], "block_null": None,
                   "member_number": {"RATE_POLICY": 1}, "member_absent": {"RATE_MODEL": None},
                   "member_list": {"RATE_EXPECT": []}, "member_expect_empty": {"RATE_EXPECT": ""}}


@pytest.mark.parametrize("shape", sorted(DECLARED_SHAPES))
def test_a_declared_block_of_another_shape_is_damage_and_the_ledger_records_it(tmp_path, monkeypatch, episode, shape):
    """`declared` that is not an object, or one of its members (score.DECLARED_AT_START) absent or not text, or its
    RATE_EXPECT empty while `expectation` records the file `series start` read: run.json
    does not read (score.run_json_problem names the member), so `series end` records the attempt ended, invalid,
    `file_unreadable:run.json`, in its ledger entry, and reads nothing from its own environment."""
    s = Series(tmp_path, episode, monkeypatch)
    a1 = s.attempt(1)
    rj = json.loads((a1 / "run.json").read_text())
    v = DECLARED_SHAPES[shape]
    if isinstance(v, dict):
        for k, x in v.items():
            if x is None:
                rj["declared"].pop(k)
            else:
                rj["declared"][k] = x
    else:
        rj["declared"] = v
    (a1 / "run.json").write_text(json.dumps(rj))
    got, state = S.read_run_json(a1)
    assert got is None and "declared" in state, state
    rc, lines = run(SR.cmd_end, a1)
    assert rc == 0 and json.loads(lines[-1])["reasons"] == ["file_unreadable:run.json"], lines
    e = json.loads((tmp_path / "st.ledger" / "attempt-01.json").read_text())
    assert (e["ended"], e["valid"], e["reasons"]) == (True, False, ["file_unreadable:run.json"])


def test_CONTROL_an_extra_declared_member_is_read_past_and_the_attempt_ends_as_recorded(tmp_path, monkeypatch, episode):
    s = Series(tmp_path, episode, monkeypatch)
    a1 = s.attempt(1)
    rj = json.loads((a1 / "run.json").read_text())
    rj["declared"]["RATE_SOMETHING_ELSE"] = "x"
    (a1 / "run.json").write_text(json.dumps(rj))
    assert S.read_run_json(a1)[1] == "read"
    rc, lines = run(SR.cmd_end, a1)
    assert rc == 0 and json.loads((a1 / "run.json").read_text())["validity"]["valid"] is True, lines


def test_a_run_json_with_no_declared_block_is_refused_and_nothing_is_written(tmp_path, monkeypatch, episode):
    """`series start` of this kit always records `declared`, so a run.json without the block is not one it wrote:
    `series end` refuses by name (`end_declared_absent`) and writes nothing; the step's environment is never read in
    its place. Put back, the same step ends the attempt."""
    s = Series(tmp_path, episode, monkeypatch)
    a1 = s.attempt(1)
    whole = (a1 / "run.json").read_text()
    rj = json.loads(whole)
    rj.pop("declared")
    (a1 / "run.json").write_text(json.dumps(rj))
    monkeypatch.setenv("RATE_POLICY", "noop")
    files = {p.name: p.read_bytes() for p in a1.iterdir()}
    ledger = (tmp_path / "st.ledger" / "attempt-01.json").read_bytes()
    rc, lines = run(SR.cmd_end, a1)
    assert rc == 6 and lines[0].startswith(f"REFUSE end_declared_absent: {a1.name}:") and "nothing written" in lines[0]
    assert {p.name: p.read_bytes() for p in a1.iterdir()} == files
    assert (tmp_path / "st.ledger" / "attempt-01.json").read_bytes() == ledger
    (a1 / "run.json").write_text(whole)
    rc, lines = run(SR.cmd_end, a1)
    assert rc == 0 and json.loads((a1 / "run.json").read_text())["validity"]["valid"] is True, lines


def test_an_expectation_that_is_not_the_file_series_start_read_is_refused_and_nothing_is_written(tmp_path, monkeypatch,
                                                                                                    episode):
    """`series end` holds the expectation at run.json's recorded path to the sha256 `series start` recorded
    (`expectation.sha256`): another file there is refused by name (`end_expect_differs`) and nothing is written;
    the frozen file put back, the same step ends the attempt."""
    s = Series(tmp_path, episode, monkeypatch)
    a1 = s.attempt(1)
    exp = Path(json.loads((a1 / "run.json").read_text())["declared"]["RATE_EXPECT"])
    frozen = exp.read_bytes()
    exp.write_bytes(frozen + b"\n")                       # still the schema, another file
    monkeypatch.setenv("RATE_POLICY", "noop")
    files = {p.name: p.read_bytes() for p in a1.iterdir()}
    ledger = (tmp_path / "st.ledger" / "attempt-01.json").read_bytes()
    rc, lines = run(SR.cmd_end, a1)
    assert rc == 6 and lines[0].startswith(f"REFUSE end_expect_differs: {a1.name}:") and "nothing written" in lines[0]
    assert {p.name: p.read_bytes() for p in a1.iterdir()} == files
    assert (tmp_path / "st.ledger" / "attempt-01.json").read_bytes() == ledger
    exp.write_bytes(frozen)
    rc, lines = run(SR.cmd_end, a1)
    assert rc == 0 and json.loads((a1 / "run.json").read_text())["validity"]["valid"] is True, lines
