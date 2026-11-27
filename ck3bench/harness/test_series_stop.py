"""The stopping rule read one way by its three readers: `series end` (run.json validity and the ledger entry), the
slot (the ledger) and the scorer (the run dirs). A seated stop or hang that the resource rule voids is invalid in all
three; a ledger entry or a run.json validity that disagrees with the rule read again refuses the score; an attempt
whose driver stopped after `series end` is closed by `series close` and then counts as ended.

`series end` runs here in-process on a run directory whose identity witnesses are stood in (each is tested on its
own in test_series.py), so the only thing that can make an attempt invalid is the rule under test. No game, no
model."""
import json
from pathlib import Path

import pytest

from ck3bench.harness import runner
from ck3bench.harness import score as S
from ck3bench.harness import series as SR

TD = Path(__file__).with_name("testdata")
SEAT_TURN_AT = "2026-01-01T00:05:00Z"
END_AT = "2026-01-01T00:10:00Z"


@pytest.fixture(scope="module")
def episode(tmp_path_factory):
    """One finished episode of the paid-once stand-in (its trajectory reads; its outcome is a failure)."""
    out = tmp_path_factory.mktemp("ep") / "run"
    assert runner.main(["--out", str(out), "--scenario", str(TD / "standin-ireland-fake-wake-once.toml"),
                        "--policy", "noop"]) == 0
    return out


def _ident() -> dict:
    return {"git": {"commit": "a" * 40, "dirty": False}, "bridge_so": {"sha256": "b" * 64},
            "wheel": {"sha256": "c" * 64, "compare": {"verdict": "identical", "wheel_members_sha256": "d" * 64}},
            "map": {"sha256": "e" * 64}, "game_build_id": {"build_id": "f" * 40},
            "claude_version": {"version": "2.1.0 (Claude Code)"}, "start_save": {"sha256": "1" * 64},
            "prompt": {"sha256": "2" * 64}, "model": "m", "full_hold_s": 600, "switches": {}, "host": "h"}


@pytest.fixture
def series(tmp_path, monkeypatch):
    """A schedule (target 2, cap 3), its ledger directory, and `series end`'s identity witnesses stood in."""
    sched = tmp_path / "s.json"
    sched.write_text(json.dumps({"schema": S.SERIES_SCHEMA, "series": "st", "task": "t", "pattern": ["a", "b"],
                                 "valid_target": 2, "attempt_cap": 3}))
    monkeypatch.setenv("RATE_LEDGER", str(tmp_path / "st.ledger"))
    monkeypatch.delenv("RATE_EXPECT", raising=False)
    monkeypatch.setattr(SR, "static_identity", lambda p: _ident())
    monkeypatch.setattr(SR, "witness_reasons", lambda *a: [])
    monkeypatch.setattr(SR, "header_reasons", lambda *a: ([], {}))
    monkeypatch.setattr(SR, "session_witnesses", lambda *a, **k: ([], {}))
    return sched


def expectation(d: Path) -> tuple[str, str]:
    """An expectation file in D as `series start` records one: (its absolute path, its sha256)."""
    import hashlib
    p = d / "e.json"
    if not p.exists():
        p.write_text(json.dumps({"schema": SR.EXPECT_SCHEMA}))
    return str(p.resolve()), hashlib.sha256(p.read_bytes()).hexdigest()


PRESSURE_HEAD = ("utc", "mem_full10", "io_full10", "cpu_some10", "memavail_kb")


def _attempt(tmp: Path, episode: Path, sched: Path, a: int, end_reason: str, pressure: bool = True,
             limit: bool = False, marker: bool = True) -> Path:
    """Attempt A's run dir as a driver leaves it before `series end`: run.json `start`, the episode's files, the
    runner's end reason, the seat's transcript and (PRESSURE) the host sampler's rows over the stop window."""
    host = "a" if a % 2 else "b"
    ep, es = expectation(sched.parent)
    out = tmp / f"st-a{a:02d}-{host}"
    out.mkdir()
    for f in episode.iterdir():
        if f.is_file():
            (out / f.name).write_bytes(f.read_bytes())
    end = json.loads((out / "end.json").read_text())
    (out / "end.json").write_text(json.dumps({**end, "end_reason": end_reason, "at": END_AT,
                                              "notes_witness": {"verdict": "agreed"}}))
    (out / "turns.jsonl").write_text(json.dumps({"turn": 1, "sent_utc": SEAT_TURN_AT, "ended_utc": SEAT_TURN_AT}) + "\n")
    ev = [{"type": "system", "subtype": "init"}]
    if limit:
        ev.append({"type": "rate_limit_event", "rate_limit_info": {"status": "rejected"}})
    (out / "transcript.jsonl").write_text("".join(json.dumps(e) + "\n" for e in ev))
    if pressure:
        (out / "pressure.tsv").write_text("\t".join(PRESSURE_HEAD) + "\n" + "".join(
            f"2026-01-01T00:{m:02d}:00Z\t0\t0\t1\t9000000\n" for m in range(4, 12)))
    (out / "seat_prompt.rendered.md").write_text("prompt")
    import hashlib
    ident = {**_ident(), "prompt": {"sha256": hashlib.sha256(b"prompt").hexdigest()}}
    (out / "run.json").write_text(json.dumps({
        "schema": SR.SCHEMA, "run_id": out.name, "kind": "rate", "host": host, "launch_utc": "2026-01-01T00:00:00Z",
        "plan": {"scenario": "t", "seat": None}, "start": {"at": "x", **ident}, "start_reasons": [],
        "expectation": {"path": ep, "sha256": es},
        "declared": {**{k: "" for k in S.DECLARED_AT_START}, "RATE_EXPECT": ep},
        "series": {"id": "st", "schedule": str(sched), "schedule_sha256": "x", "attempt": a, "valid_target": 2,
                   "attempt_cap": 3, "pattern": ["a", "b"]}}))
    SR.write_ledger(S.load_series(sched), {"attempt": a, "host": host, "run_id": out.name, "ended": False,
                                           "started_utc": SR.now(), "expectation_sha256": es})
    return out


def _end(out: Path, monkeypatch, marker: bool = True) -> dict:
    """`series end OUT` as the driver calls it (the start identity read again: the stand-in), then its marker."""
    rj = json.loads((out / "run.json").read_text())
    monkeypatch.setattr(SR, "static_identity", lambda p: {k: v for k, v in rj["start"].items() if k != "at"})
    assert SR.cmd_end(out) == 0
    if marker:
        (out / S.END_MARKER).write_text("rc=0\n")
    return json.loads((out / "run.json").read_text())["validity"]


def _ledger_entry(sched: Path, a: int) -> dict:
    return next(e for e in SR.ledger(S.load_series(sched)) if e["attempt"] == a)


def _score(dirs, *extra) -> int:
    return S.main([*map(str, dirs), *extra])


def test_a_resource_voided_stop_is_invalid_in_run_json_the_ledger_and_the_score(tmp_path, episode, series,
                                                                                monkeypatch, capsys):
    """Attempt 1 played out (valid); attempt 2 stopped with no pressure samples (the rule voids it); the target is
    2 valid. `series end` records the void in run.json AND the ledger, so the slot does NOT stop the series at 2
    attempts (a ledger that counted it valid would refuse `series_stopped` while the scorer says not stopped), and
    the scorer reads the same: not stopped, nothing refused."""
    a1 = _attempt(tmp_path, episode, series, 1, "episode_over")
    assert _end(a1, monkeypatch) == {"valid": True, "reasons": []}           # CONTROL: the stand-in can be valid
    a2 = _attempt(tmp_path, episode, series, 2, "seat_stopped", pressure=False)
    v2 = _end(a2, monkeypatch)
    assert v2["valid"] is False and v2["reasons"] == ["resource_evidence_absent:pressure"], v2
    e2 = _ledger_entry(series, 2)
    assert (e2["ended"], e2["valid"], e2["reasons"]) == (True, False, ["resource_evidence_absent:pressure"])
    got, refuse = SR.slot(S.load_series(series), "a", "st-a03-a")
    assert refuse == [] and got["attempt"] == 3 and (got["valid"], got["attempts"]) == (1, 2)
    assert _score([a1, a2]) == 4
    err = capsys.readouterr().err
    assert "series_not_stopped" in err and "1 valid of 2, 2 attempts of 3" in err and "disagrees" not in err


def test_CONTROL_a_stop_with_its_evidence_read_and_no_pressure_stays_a_valid_failure(tmp_path, episode, series,
                                                                                    monkeypatch):
    a2 = _attempt(tmp_path, episode, series, 2, "seat_stopped")
    assert _end(a2, monkeypatch) == {"valid": True, "reasons": []}
    assert _ledger_entry(series, 2)["valid"] is True and S.validity_only(a2)["valid"] is True


def test_a_hang_with_a_limit_is_void_and_the_cap_stops_the_series_for_both_readers(tmp_path, episode, series,
                                                                                   monkeypatch, capsys):
    """A hung seat whose last session hit a rate limit (pressure read, none): void in all three readers; with a
    second void the cap of 3 is reached at 1 valid: the slot and the scorer both say stopped."""
    a1 = _attempt(tmp_path, episode, series, 1, "episode_over")
    _end(a1, monkeypatch)
    a2 = _attempt(tmp_path, episode, series, 2, "seat_hung", limit=True)
    assert _end(a2, monkeypatch)["reasons"] == ["resource_limited:rate_limit"]
    a3 = _attempt(tmp_path, episode, series, 3, "seat_stopped", pressure=False)
    _end(a3, monkeypatch)
    got, refuse = SR.slot(S.load_series(series), "b", "st-a04-b")
    assert any("series_stopped: the attempt_cap" in x for x in refuse), refuse
    capsys.readouterr()
    assert _score([a1, a2, a3], "--json") == 0
    out = json.loads(capsys.readouterr().out)
    assert out["gate"]["stopped"] is True and out["gate"]["stop"] == "attempt_cap" and out["score"]["n"] == 1


def test_an_attempt_without_its_end_marker_holds_the_stop_until_series_close(tmp_path, episode, series,
                                                                              monkeypatch, capsys):
    """The driver stopped after `series end` (the ledger entry ended, run.json valid) and before its marker: the
    scorer says why and names the command; `series close` writes result.json and the marker, and the series scores.
    CONTROL: close refuses an attempt `series end` never reached, and a second close."""
    a1 = _attempt(tmp_path, episode, series, 1, "episode_over")
    _end(a1, monkeypatch)
    a2 = _attempt(tmp_path, episode, series, 2, "episode_over")
    _end(a2, monkeypatch, marker=False)
    assert _ledger_entry(series, 2)["ended"] is True
    assert _score([a1, a2]) == 4
    err = capsys.readouterr().err
    assert "series_not_stopped" in err and "no end marker" in err and "series close" in err
    assert SR.main(["close", str(a2)]) == 0
    assert (a2 / S.END_MARKER).is_file() and (a2 / "result.json").is_file()
    assert SR.main(["close", str(a2)]) == 6 and "already" in capsys.readouterr().out
    assert _score([a1, a2]) == 0
    a3 = _attempt(tmp_path, episode, series, 3, "episode_over")
    assert SR.main(["close", str(a3)]) == 6 and "has not run" in capsys.readouterr().out
    assert not (a3 / S.END_MARKER).exists()


def test_a_ledger_that_disagrees_with_run_json_refuses_the_score(tmp_path, episode, series, monkeypatch, capsys):
    """The ledger says attempt 2 invalid, its run.json valid (a ledger written by another rule, or edited): refused
    rc 4, whatever the override. CONTROL: the agreeing pair scores."""
    a1 = _attempt(tmp_path, episode, series, 1, "episode_over")
    _end(a1, monkeypatch)
    a2 = _attempt(tmp_path, episode, series, 2, "episode_over")
    _end(a2, monkeypatch)
    assert _score([a1, a2]) == 0
    capsys.readouterr()
    f = Path(SR.ledger_dir(S.load_series(series))) / "attempt-02.json"
    e = json.loads(f.read_text())
    f.write_text(json.dumps({**e, "valid": False, "reasons": ["tree_dirty"]}))
    for extra in ((), (S.OVERRIDE_FLAG,)):
        assert _score([a1, a2], *extra) == 4
        assert "series_ledger_disagrees: st-a02-b: the ledger says valid=False" in capsys.readouterr().err


def test_a_run_json_validity_the_rule_does_not_read_again_refuses_the_score(tmp_path, episode, series, monkeypatch,
                                                                         capsys):
    """A run.json validity written without the resource rule (a driver of its own that leaves the rule out): a
    stop with no pressure samples recorded valid, its ledger entry agreeing. The scorer reads the rule again and
    refuses (`series_validity_disagrees`) instead of counting it either way. CONTROL: the same attempt ended by
    this `series end` agrees and is not refused."""
    a1 = _attempt(tmp_path, episode, series, 1, "episode_over")
    _end(a1, monkeypatch)
    a2 = _attempt(tmp_path, episode, series, 2, "seat_stopped", pressure=False)
    _end(a2, monkeypatch)
    a3 = _attempt(tmp_path, episode, series, 3, "episode_over")
    _end(a3, monkeypatch)
    assert _score([a1, a2, a3]) == 0
    capsys.readouterr()
    # a record of the identity check's verdict alone, in run.json and the ledger
    rj = json.loads((a2 / "run.json").read_text())
    rj["validity"] = {"valid": True, "reasons": []}
    (a2 / "run.json").write_text(json.dumps(rj))
    f = Path(SR.ledger_dir(S.load_series(series))) / "attempt-02.json"
    f.write_text(json.dumps({**json.loads(f.read_text()), "valid": True, "reasons": []}))
    assert _score([a1, a2, a3]) == 4
    err = capsys.readouterr().err
    assert "series_validity_disagrees: st-a02-b: read now, not recorded: resource_evidence_absent:pressure" in err


def test_series_end_waits_for_the_stop_window_to_close(tmp_path, monkeypatch):
    """The window's last sample is written after its end: `series end` waits (bounded) until the padded window has
    passed, so the verdict it records is the one the scorer reads later. CONTROL: a window long past waits 0."""
    import time
    d = tmp_path / "r"
    d.mkdir()
    slept = []
    monkeypatch.setattr(SR.time, "sleep", lambda s: slept.append(s))
    monkeypatch.setattr(S, "resource_rule", lambda out: {"window": [time.time() - 30, time.time() - 10]})
    assert 0 < SR.settle_resource_window(d) <= S.WINDOW_PAD_S + 5 and slept
    monkeypatch.setattr(S, "resource_rule", lambda out: {"window": [0, 1]})
    assert SR.settle_resource_window(d) == 0.0
