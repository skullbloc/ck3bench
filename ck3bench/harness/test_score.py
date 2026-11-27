"""The scorer and the series' stopping rule on synthetic run directories (a no-model episode on the fake backend,
then the identity record a series' end step writes). No game, no model."""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from ck3bench.harness import runner
from ck3bench.harness import score as S

TD = Path(__file__).with_name("testdata")


@pytest.fixture(scope="module")
def noop_run(tmp_path_factory):
    """One finished episode of the paid-once stand-in, the seat played by the game's AI alone."""
    out = tmp_path_factory.mktemp("runs") / "base-a01-h"
    assert runner.main(["--out", str(out), "--scenario", str(TD / "standin-ireland-fake-wake-once.toml"),
                        "--policy", "noop"]) == 0
    return out


def _copy(src: Path, dst: Path, run_json=None, done=True) -> Path:
    dst.mkdir(parents=True)
    for f in src.iterdir():
        if f.is_file():
            (dst / f.name).write_bytes(f.read_bytes())
    if run_json is not None:
        (dst / "run.json").write_text(json.dumps(run_json))
    if done:
        (dst / "rate_run.done").write_text("done\n")
    return dst


EXPECT_SHA = "e" * 64


def _series(attempt, valid=True, target=2, cap=3, expect=EXPECT_SHA):
    return {"kind": "rate", "validity": {"valid": valid, "reasons": [] if valid else ["tree_dirty"]},
            "series": {"id": "base", "attempt": attempt, "valid_target": target, "attempt_cap": cap,
                       "schedule_sha256": "x"}, "host": "h", "expectation": {"path": "expect.json", "sha256": expect}}


def _ledger(tmp: Path, dirs, name="base") -> Path:
    """The series' ledger beside the run dirs (where the reference driver keeps it): one entry per attempt."""
    d = tmp / f"{name}.ledger"
    d.mkdir(exist_ok=True)
    for x in dirs:
        rj = json.loads((x / "run.json").read_text())
        a, v = rj["series"]["attempt"], rj.get("validity") or {}
        (d / f"attempt-{a:02d}.json").write_text(json.dumps({"attempt": a, "host": "h", "run_id": x.name,
                                                             "ended": True, "valid": v.get("valid") is True,
                                                             "reasons": v.get("reasons") or [],
                                                             "started_utc": f"2026-01-01T00:{a:02d}:00Z",
                                                             "expectation_sha256": EXPECT_SHA}))
    return d


def test_wilson_interval():
    assert S.wilson(0, 0) is None
    lo, hi = S.wilson(7, 20)
    assert round(lo, 3) == 0.181 and round(hi, 3) == 0.567
    assert S.wilson(0, 5)[0] == 0.0 and S.wilson(5, 5)[1] == pytest.approx(1.0)


def test_a_run_without_its_identity_record_is_invalid_and_never_counted(noop_run):
    r = S.read_run(noop_run)
    assert r["valid"] is False and r["invalid_reasons"] == ["no_run_json"]
    s = S.score([r])
    assert s["n"] == 0 and s["k"] == 0


def test_a_valid_no_model_run_is_read_from_its_own_files(noop_run, tmp_path):
    d = _copy(noop_run, tmp_path / "v", {"kind": "rate", "validity": {"valid": True, "reasons": []}})
    r = S.read_run(d)
    assert r["valid"] is True and r["success"] is False
    assert r["steps"] == 15 and "held_at_any_step=False" in r["success_why"]
    s = S.score([r])
    assert (s["k"], s["n"]) == (0, 1)
    assert "valid" in S.table([r], s)


def test_a_series_is_refused_before_its_stop_and_scored_after(noop_run, tmp_path):
    a1 = _copy(noop_run, tmp_path / "base-a01-h", _series(1))
    gate = S.series_gate([a1])
    assert gate["series"] == "base" and gate["stopped"] is False
    _ledger(tmp_path, [a1])
    r = subprocess.run([sys.executable, "-m", "ck3bench.harness", "score", str(a1)], capture_output=True, text=True,
                       timeout=120)
    assert r.returncode == 4 and "series_not_stopped" in r.stderr and r.stdout == ""
    a2 = _copy(noop_run, tmp_path / "base-a02-h", _series(2))
    _ledger(tmp_path, [a2])
    gate = S.series_gate([a1, a2])
    assert gate["stopped"] is True and gate["stop"] == "valid_target"
    r = subprocess.run([sys.executable, "-m", "ck3bench.harness", "score", str(a1), str(a2), "--json"],
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert (out["score"]["k"], out["score"]["n"]) == (0, 2)


def test_an_invalid_attempt_counts_toward_the_cap_not_the_target(noop_run, tmp_path):
    dirs = [_copy(noop_run, tmp_path / f"base-a0{i}-h", _series(i, valid=(i != 2))) for i in (1, 2, 3)]
    gate = S.series_gate(dirs)
    assert gate["valid"] == 2 and gate["attempts"] == 3 and gate["stopped"] is True


def test_a_run_still_playing_holds_the_stop(noop_run, tmp_path):
    a1 = _copy(noop_run, tmp_path / "base-a01-h", _series(1))
    a2 = _copy(noop_run, tmp_path / "base-a02-h", _series(2), done=False)
    a3 = _copy(noop_run, tmp_path / "base-a03-h", _series(3))
    gate = S.series_gate([a1, a2, a3])
    assert gate["stopped"] is False and gate["in_flight"] == [2]


def test_the_schedule_alternates_hosts_and_names_its_runs(tmp_path):
    p = tmp_path / "s.json"
    p.write_text(json.dumps({"schema": S.SERIES_SCHEMA, "series": "base", "task": "t", "pattern": ["h1", "h2"],
                             "valid_target": 2, "attempt_cap": 3}))
    s = S.load_series(p)
    assert [S.run_id_for(s, a) for a in (1, 2, 3)] == ["base-a01-h1", "base-a02-h2", "base-a03-h1"]
    p.write_text(json.dumps({**json.loads(p.read_text()), "attempt_cap": 1}))
    with pytest.raises(ValueError):
        S.load_series(p)


def test_a_stop_with_a_rate_limit_in_its_window_is_void_not_a_failure(noop_run, tmp_path):
    d = _copy(noop_run, tmp_path / "rl", {"kind": "rate", "validity": {"valid": True, "reasons": []}})
    end = json.loads((d / "end.json").read_text())
    (d / "end.json").write_text(json.dumps({**end, "end_reason": "seat_stopped"}))
    (d / "turns.jsonl").write_text(json.dumps({"turn": 1, "sent_utc": "2026-01-01T00:00:00.000Z",
                                               "ended_utc": "2026-01-01T00:01:00Z"}) + "\n")
    (d / "transcript.jsonl").write_text(
        json.dumps({"type": "system", "subtype": "init"}) + "\n"
        + json.dumps({"type": "rate_limit_event", "rate_limit_info": {"status": "rejected"}}) + "\n")
    r = S.read_run(d)
    assert r["valid"] is False and any(x.startswith("resource_limited") or x.startswith("resource_evidence_absent")
                                       for x in r["invalid_reasons"]), r["invalid_reasons"]


def test_a_run_dir_that_does_not_exist_is_refused_not_counted(tmp_path, capsys):
    """A mistyped RUN_DIR is refused rc 2 and nothing is scored; a real (empty) run directory beside it is not
    enough to score the call (every path named must be a directory)."""
    real = tmp_path / "a01"
    real.mkdir()
    assert S.main([str(tmp_path / "no-such")]) == 2
    err = capsys.readouterr()
    assert "REFUSE score" in err.err and "no-such" in err.err and "k/n" not in err.out
    assert S.main([str(real), str(tmp_path / "no-such")]) == 2
    assert "k/n" not in capsys.readouterr().out


def test_CONTROL_an_existing_run_dir_is_still_scored(tmp_path, capsys):
    """The refusal is keyed on the path, not on the run: an empty run directory is read (and counted invalid)."""
    real = tmp_path / "a01"
    real.mkdir()
    assert S.main([str(real)]) == 0
    assert "k/n" in capsys.readouterr().out


def _digests(d: Path, before: bytes, after: bytes) -> None:
    for tag, body in (("before", before), ("after", after)):
        (d / f"own.{tag}.list").write_bytes(b"d . 0 1.0\n")
        (d / f"own.{tag}.sha").write_bytes(body)
        (d / f"own.{tag}.txt").write_text(f"root=/p files=1 list_sha=0 content_sha={body[:4].hex()}\n")


def test_a_protected_directory_that_differs_is_invalid_for_the_scorer_and_the_stopping_rule(noop_run, tmp_path):
    """run.json says valid (its end step took no after digest, or an older one did not hold it); the digests differ:
    the row is invalid `owner_tree_differs` and the run is not a valid one of the series. CONTROL: the same run with
    identical digests stays valid in both."""
    same = _copy(noop_run, tmp_path / "base-a01-h", _series(1))
    _digests(same, b"aa  ./x\n", b"aa  ./x\n")
    differs = _copy(noop_run, tmp_path / "base-a02-h", _series(2))
    _digests(differs, b"aa  ./x\n", b"bb  ./x\n")
    assert S.validity_only(same)["valid"] is True and S.validity_only(differs)["valid"] is False
    assert "owner_tree_differs" not in S.read_run(same)["invalid_reasons"]
    r = S.read_run(differs)
    assert r["valid"] is False and "owner_tree_differs" in r["invalid_reasons"]
    assert S.owner_tree(same)["verdict"] == "IDENTICAL" and S.owner_tree(differs)["verdict"] == "DIFFERS"
    st = S.stop_state({"valid_target": 2, "attempt_cap": 3}, [S.validity_only(x) for x in (same, differs)])
    assert (st["valid"], st["attempts"], st["stop"]) == (1, 2, None)


def test_the_protected_directory_verdicts(tmp_path):
    d = tmp_path / "r"
    d.mkdir()
    assert S.owner_tree(d)["verdict"] == "not_taken" and S.owner_tree_reasons(S.owner_tree(d)) == []
    (d / "own.before.txt").write_text("not configured (RATE_PROTECTED_DIR unset)\n")
    (d / "own.after.txt").write_text("not configured (RATE_PROTECTED_DIR unset)\n")
    assert S.owner_tree(d)["verdict"] == "not_configured" and S.owner_tree_reasons(S.owner_tree(d)) == []
    _digests(d, b"aa\n", b"aa\n")
    (d / "own.after.sha").unlink()
    assert S.owner_tree(d)["verdict"] == "unwitnessed"
    assert S.owner_tree_reasons(S.owner_tree(d)) == [S.OWNER_TREE_ABSENT]


def test_a_refused_start_is_no_attempt_and_holds_no_slot(noop_run, tmp_path):
    """A start the driver refused (start_refused.txt, the directory moved aside): scored invalid `start_refused`,
    not an attempt, ended (never in flight); beside the series' runs it changes neither the stop nor the attempt
    numbers (its retry has the same one)."""
    ref = tmp_path / "refused" / "base-a01-h-20260101T000000Z"
    ref.mkdir(parents=True)
    (ref / "run.json").write_text(json.dumps({**_series(1), "validity": None}))
    (ref / S.START_REFUSED_FILE).write_text("rc=6: REFUSE expect_differs:bridge_so_sha256\n")
    a1 = _copy(noop_run, tmp_path / "base-a01-h", _series(1))
    a2 = _copy(noop_run, tmp_path / "base-a02-h", _series(2))
    v = S.validity_only(ref)
    assert (v["ended"], v["valid"], v["not_attempt"], v["start_refused"]) == (True, False, True, True)
    gate = S.series_gate([ref, a1, a2])
    assert gate["stopped"] is True and gate["attempts"] == 2 and gate["valid"] == 2
    rows = [S.read_run(x) for x in (ref, a1, a2)]
    assert rows[0]["invalid_reasons"][0] == S.START_REFUSED
    s = S.score(rows)
    assert s["attempts"] == 2 and s["refused_starts_not_attempts"] == [ref.name]
    assert "1 refused at the start, not attempts" in S.table(rows, s)


def test_the_comparison_is_newcombe_and_fisher_on_known_tables(tmp_path, capsys):
    """Newcombe (1998), method 10, its worked example 56/70 against 48/80: d 0.2000, interval 0.0524 to 0.3339.
    Fisher's exact test, two-sided, of [[1, 9], [11, 3]]: p 0.002759 (R's fisher.test); [[3, 1], [1, 3]]: 0.4857."""
    d, lo, hi = S.newcombe(56, 70, 48, 80)
    assert (round(d, 4), round(lo, 4), round(hi, 4)) == (0.2, 0.0524, 0.3339)
    assert round(S.fisher_two_sided(1, 10, 11, 14), 6) == 0.002759
    assert round(S.fisher_two_sided(3, 4, 1, 4), 4) == 0.4857
    assert S.newcombe(0, 0, 1, 2) is None and S.fisher_two_sided(1, 0, 1, 2) is None
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    a.write_text(json.dumps(_scored("seated", 56, 70, True)))
    b.write_text(json.dumps(_scored("control", 48, 80, False)))
    assert S.main(["--compare", str(a), str(b)]) == 0
    got = json.loads(capsys.readouterr().out)
    assert round(got["difference"], 4) == 0.2 and got["fisher_reject_at_0_05"] is True
    assert (got["seated_series"], got["control_series"]) == ("seated", "control")


IDENTITY = {"task": "munster1066-kingdom-once-30y-wake", "start_save_sha256": "5" * 64, "map_sha256": "6" * 64,
            "game_build_id": "7" * 40}


def _scored(series, k, n, seated, stopped=True, override=False, **identity) -> dict:
    return {"score": {"k": k, "n": n}, "runs": [{"run": f"{series}-a01-a", "seated": seated}],
            "gate": {"series": series, "stopped": stopped, "override": override, "why": "x",
                     "identity": {**IDENTITY, **identity}}}


def test_the_comparison_refuses_what_is_not_two_stopped_series_seated_then_control(tmp_path, capsys):
    """Each refusal on its own planted defect; CONTROL: the well-formed pair compares (rc 0)."""
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    cases = [(_scored("seated", 5, 10, True, stopped=False), _scored("control", 2, 10, False), "had not stopped"),
             (_scored("seated", 5, 10, True, override=True), _scored("control", 2, 10, False), "scored with"),
             (_scored("control", 2, 10, False), _scored("seated", 5, 10, True), "is not a seated series"),
             (_scored("seated", 5, 10, True), _scored("seated", 2, 10, True), "both files score"),
             ({"score": {"k": 1, "n": 2}}, _scored("control", 2, 10, False), "not one series"),
             (_scored("seated", 5, 10, True), {"anything": 1}, "no score k/n")]
    for da, db, why in cases:
        a.write_text(json.dumps(da))
        b.write_text(json.dumps(db))
        assert S.main(["--compare", str(a), str(b)]) == 2, why
        assert why in capsys.readouterr().err, why
    a.write_text(json.dumps(_scored("seated", 5, 10, True)))
    b.write_text(json.dumps(_scored("control", 2, 10, False)))
    assert S.main(["--compare", str(a), str(b)]) == 0


def test_a_series_is_scored_against_its_ledger_and_one_expectation(noop_run, tmp_path, capsys):
    """An attempt the ledger names whose directory is not given refuses the score (rc 4, whatever the override), so
    does a directory the ledger does not name and an absent ledger; two expectation files in one series refuse. CONTROL: the complete set against its ledger scores."""
    dirs = [_copy(noop_run, tmp_path / f"base-a0{i}-h", _series(i)) for i in (1, 2)]
    assert S.main([str(dirs[1])]) == 4 and "series_ledger_absent" in capsys.readouterr().err
    _ledger(tmp_path, dirs)
    for argv in ([str(dirs[1])], [str(dirs[1]), S.OVERRIDE_FLAG]):
        assert S.main(argv) == 4
        err = capsys.readouterr().err
        assert "series_attempt_missing: attempt 1 (base-a01-h)" in err and "k/n" not in err
    extra = _copy(noop_run, tmp_path / "base-a03-h", _series(3))
    assert S.main([*map(str, dirs), str(extra)]) == 4
    assert "series_attempt_not_in_ledger: base-a03-h" in capsys.readouterr().err
    other = _copy(noop_run, tmp_path / "x" / "base-a02-h", _series(2, expect="f" * 64))
    assert S.main([str(dirs[0]), str(other), "--ledger", str(tmp_path / "base.ledger")]) == 4
    assert "series_expectation_differs" in capsys.readouterr().err
    assert S.main([*map(str, dirs), "--json"]) == 0
    got = json.loads(capsys.readouterr().out)
    assert got["score"]["n"] == 2 and got["gate"]["stopped"] is True and got["gate"]["override"] is False


def test_a_valid_run_played_out_after_the_target_is_an_attempt_not_scored(noop_run, tmp_path):
    dirs = [_copy(noop_run, tmp_path / f"base-a0{i}-h", _series(i)) for i in (1, 2, 3)]
    rows = [S.read_run(x) for x in dirs]
    s = S.score(rows)
    assert s["played_out_not_scored"] == ["base-a03-h"] and s["attempts"] == 3 and s["n"] == 2
    assert "1 valid after the target, played out and not scored" in S.table(rows, s)


def test_the_comparison_refuses_two_series_of_another_task_or_start(tmp_path, capsys):
    """Each identity key that differs between the two series, or is not one value within one, refuses the
    comparison by its name. CONTROL: the same identity compares (rc 0) and the output carries it."""
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    a.write_text(json.dumps(_scored("seated", 5, 10, True)))
    for k in S.IDENTITY_KEYS:
        for other in ("x" * 64, {"differs": ["1", "2"]}):
            b.write_text(json.dumps(_scored("control", 2, 10, False, **{k: other})))
            assert S.main(["--compare", str(a), str(b)]) == 2, k
            err = capsys.readouterr().err
            assert (f"identity_differs:{k}" if isinstance(other, str) else f"identity_unread:{k}") in err, (k, err)
    b.write_text(json.dumps(_scored("control", 2, 10, False)))
    assert S.main(["--compare", str(a), str(b)]) == 0
    assert json.loads(capsys.readouterr().out)["identity"] == IDENTITY


def test_a_scored_series_carries_its_identity(noop_run, tmp_path, capsys):
    rj = {**_series(1), "plan": {"scenario": "munster1066-kingdom-once-30y-wake"},
          "start": {"start_save": {"sha256": "5" * 64}, "map": {"sha256": "6" * 64},
                    "game_build_id": {"build_id": "7" * 40}}}
    dirs = [_copy(noop_run, tmp_path / f"base-a0{i}-h", {**rj, "series": {**rj["series"], "attempt": i}})
            for i in (1, 2)]
    _ledger(tmp_path, dirs)
    assert S.main([*map(str, dirs), "--json"]) == 0
    got = json.loads(capsys.readouterr().out)["gate"]["identity"]
    assert {k: got[k] for k in S.IDENTITY_KEYS} == IDENTITY
    assert got["git_commit"] == {"absent": ["base-a01-h", "base-a02-h"]}
    assert S.main([*map(str, dirs)]) == 0 and "identity: task=munster1066-kingdom-once-30y-wake" in capsys.readouterr().out


def test_a_set_mixing_series_and_other_run_dirs_is_refused_whatever_the_override(noop_run, tmp_path, capsys):
    """Two of a series' attempts and one run of no series: refused rc 4 with and without the override, nothing
    read. CONTROL: the series' own dirs alone score."""
    dirs = [_copy(noop_run, tmp_path / f"base-a0{i}-h", _series(i)) for i in (1, 2)]
    _ledger(tmp_path, dirs)
    other = _copy(noop_run, tmp_path / "solo", {"kind": "rate", "validity": {"valid": True, "reasons": []}})
    for extra in ([], [S.OVERRIDE_FLAG]):
        assert S.main([*map(str, dirs), str(other), *extra]) == 4
        err = capsys.readouterr()
        assert "series_mixed" in err.err and "k/n" not in err.out
    assert S.main([*map(str, dirs)]) == 0


def test_the_json_output_names_runs_by_basename_and_no_bridge_log_path(noop_run, tmp_path, capsys, monkeypatch):
    """`score --json` as published: no absolute path anywhere in it (the run's directory, a refused start's text
    naming the ledger's path, the bridge witness's log path). CONTROL: without the publishable pass the same output
    carries them."""
    dirs = [_copy(noop_run, tmp_path / f"base-a0{i}-h", _series(i)) for i in (1, 2)]
    _ledger(tmp_path, dirs)
    ref = tmp_path / "refused" / "base-a01-h-20260101T000000Z"
    ref.mkdir(parents=True)
    (ref / "run.json").write_text(json.dumps({**_series(1), "validity": None}))
    (ref / S.START_REFUSED_FILE).write_text(f"rc=6: REFUSE series_alternation (ledger {tmp_path}/base.ledger)\n")
    absolute = re.compile(r"(?<![\w.:/-])/(?:[\w.-]+/)+[\w.-]*")

    def run() -> str:
        assert S.main([*map(str, dirs), str(ref), "--json", "--ledger", str(tmp_path / "base.ledger")]) == 0
        return capsys.readouterr().out
    out = run()
    assert str(tmp_path) not in out and not absolute.search(out), absolute.search(out)
    rows = {r["run"]: r for r in json.loads(out)["runs"]}
    assert set(rows) == {"base-a01-h", "base-a02-h", ref.name} and "dir" not in rows["base-a01-h"]
    assert "base.ledger" in rows[ref.name]["start_refused"]
    bw = {"state": "unwitnessed", "path": str(tmp_path / "state" / "bridge.log"), "path_from": "RATE_BRIDGE_LOG",
          "reason_code": "log_replaced", "held_by_game": {"checked": True, "held": False, "reason_code": "log_replaced",
                                                          "why": f"this run's game held no file at {tmp_path}/b.log"}}
    pub = S.publishable({"runs": [{"creator": {"who": "seat_call", "bridge": bw}}]})
    assert str(tmp_path) not in json.dumps(pub) and pub["runs"][0]["creator"]["bridge"]["reason_code"] == "log_replaced"
    monkeypatch.setattr(S, "publishable", lambda d, names=None: d)
    assert str(tmp_path) in run()


def test_the_cli_s_limit_reached_line_is_a_rate_limit_not_an_api_error(tmp_path):
    """The agent CLI's "5-hour limit reached" result is read as the limit it is, by the scorer and the runner."""
    from ck3bench.harness import runner as R
    line = "5-hour " + "limit reached \u2219 resets 3pm"
    (tmp_path / "transcript.jsonl").write_text(json.dumps({"type": "system", "subtype": "init"}) + "\n" + json.dumps(
        {"type": "result", "subtype": "success", "is_error": True, "result": line}) + "\n")
    w = S.transcript_evidence(tmp_path)["whole_run"]
    assert (w["limit_hits"], w["api_errors"]) == (1, 0) and R.RATE_RE.search(line)


def test_a_reason_the_resource_rule_and_run_json_both_hold_is_listed_once(tmp_path, monkeypatch):
    """run.json's validity records the resource rule's reason (series end adds it) and the scorer reads the rule
    again: the row lists the reason once."""
    d = tmp_path / "r"
    d.mkdir()
    (d / "trajectory.jsonl").write_text(json.dumps({"kind": "header", "seats": {"s": {}}}) + "\n")
    (d / "run.json").write_text(json.dumps({"kind": "rate", "validity": {"valid": False,
                                                                         "reasons": ["resource_limited:rate_limit"]}}))
    (d / "end.json").write_text(json.dumps({"end_reason": "seat_stopped"}))
    monkeypatch.setattr(S, "resource_rule", lambda d: {"reasons": ["resource_limited:rate_limit"]})
    assert S.read_run(d)["invalid_reasons"] == ["resource_limited:rate_limit"]


def test_the_json_output_carries_no_host_or_user_name_in_its_free_text(tmp_path, monkeypatch):
    """An operator's record (a withdrawal text, a refused start's lines) is free text in the rows: the host names the
    run.json records and the account's user names are replaced there as `series publish` replaces them. CONTROL:
    with no names, the text is kept."""
    d = tmp_path / "r"
    d.mkdir()
    (d / "run.json").write_text(json.dumps({"start": {"host": "rackbox-7.lab"}, "end": {"host": "rackbox-7"}}))
    monkeypatch.setenv("USER", "someone")
    names = S.private_names([str(d)])
    doc = {"withdrawn": "withdrawn on rackbox-7 by someone, rackbox-7.lab", "reasons": ["operator_withdrew_host"]}
    got = S.publishable(doc, names)
    assert got["withdrawn"] == "withdrawn on <host name> by <user name>, <host name>" and got["reasons"] == doc["reasons"]
    assert S.publishable(doc) == doc


# ── the sensitivity line: attempts stopped after their end row, counted with their trajectory's outcome ─────────

def _row(run, attempt, valid, success, reasons=(), end_row="horizon", budget="episode_over", target=10):
    return {"run": run, "attempt": attempt, "valid": valid, "success": success, "invalid_reasons": list(reasons),
            "end_row": end_row, "end_row_budget": budget, "valid_target": target}


def test_the_score_gives_k_n_and_beside_it_k_n_with_the_attempts_stopped_after_their_end_row():
    """An attempt invalid only for the reasons a stop after its end row gives (score.STOPPED_AFTER_END) and with an
    end row is counted in k'/n' with its trajectory's outcome and named; one invalid for any other reason too, or
    with no end row, is not. Printed beside k/n and in `score --json`."""
    from ck3bench.harness import score as SC
    rows = [_row("s-a01-a", 1, True, True), _row("s-a02-b", 2, True, False),
            _row("s-a03-a", 3, False, True, ["witness_absent:end_json_final", "witness_absent:owner_tree"]),
            _row("s-a04-b", 4, False, False, ["runner_end:stopped_by_signal"]),
            _row("s-a05-a", 5, False, None, ["witness_absent:owner_tree"]),
            _row("s-a06-b", 6, False, True, ["witness_absent:end_json", "prompt_differs_from_launch"]),
            _row("s-a07-a", 7, False, True, ["runner_end:stopped_by_signal"], end_row=None)]
    s = SC.score(rows)
    assert (s["k"], s["n"]) == (1, 2)
    sv = s["sensitivity_stopped_after_end"]
    assert (sv["k"], sv["n"]) == (2, 4) and sv["scored_k_n"] == [1, 2]
    assert sv["attempts"] == ["s-a03-a", "s-a04-b", "s-a05-a"] and sv["outcome_unread"] == ["s-a05-a"]
    line = next(x for x in SC.table(rows, s).splitlines() if x.startswith("sensitivity:"))
    assert "k'/n' = 2/4" in line and "s-a03-a, s-a04-b, s-a05-a" in line and "outcome unread" in line


def test_a_stop_at_the_end_row_itself_is_in_k_n_prime_by_its_evidence_and_any_other_reason_keeps_it_out():
    """The class is the records written after the end row (score.STOPPED_AFTER_END): a KILL at the end row leaves
    end.json absent (`witness_absent:end_json` beside the scorer's own `file_absent:end.json`) and the watcher's end
    witness absent; a Ctrl-C there leaves `runner_end:stopped_by_signal` and the same witness absent. Each is in
    k'/n'. A reason outside the class (a start witness, a prompt that differs) keeps an attempt out, and so does no
    end row. CONTROL: a class without `file_absent:end.json` and `witness_absent:game_end` leaves both stops out."""
    from ck3bench.harness import score as SC
    kill0 = ["witness_absent:game_end", "witness_absent:end_json", "witness_absent:owner_tree", "file_absent:end.json"]
    int0 = ["witness_absent:game_end", "runner_end:stopped_by_signal", "witness_absent:owner_tree"]
    rows = [_row("s-a01-a", 1, False, True, kill0), _row("s-a02-b", 2, False, False, int0),
            _row("s-a03-a", 3, False, True, [*kill0, "witness_absent:game_start"]),
            _row("s-a04-b", 4, False, True, ["witness_absent:game_end", "prompt_differs_from_launch"]),
            _row("s-a05-a", 5, False, True, kill0, end_row=None)]
    sv = SC.score(rows)["sensitivity_stopped_after_end"]
    assert sv["attempts"] == ["s-a01-a", "s-a02-b"] and (sv["k"], sv["n"]) == (1, 2)
    assert set(sv["reasons"]) == set(SC.STOPPED_AFTER_END) >= {*kill0, *int0}
    old = ("witness_absent:end_json", "witness_absent:end_json_final", "runner_end:stopped_by_signal",
           "witness_absent:owner_tree")
    assert not [r["run"] for r in rows if r["end_row"] and all(x in old for x in r["invalid_reasons"])]


def test_CONTROL_with_no_attempt_stopped_after_its_end_row_k_n_and_k_n_prime_are_equal():
    from ck3bench.harness import score as SC
    rows = [_row("s-a01-a", 1, True, True), _row("s-a02-b", 2, False, True, ["prompt_differs_from_launch"])]
    s = SC.score(rows)
    sv = s["sensitivity_stopped_after_end"]
    assert (sv["k"], sv["n"]) == (s["k"], s["n"]) == (1, 1) and sv["attempts"] == []
    assert "k'/n' = 1/1 with the 0 attempts" in SC.table(rows, s)


def _budget_dir(tmp: Path, arm: str, row_at: str, flags=("--wall-budget-s", "100")) -> Path:
    """A run directory with what end_row_budget reads: run.json's launch (2026-01-01T00:00:00Z) and runner flags, and
    the trajectory's end row stamped `row_at`; the arm in `declared` (the scorer reads neither arm differently)."""
    from ck3bench.harness import score as SC
    d = tmp / f"s-a01-a-{arm}-{row_at.replace(':', '')}"
    d.mkdir(parents=True)
    dec = {k: "" for k in SC.DECLARED_AT_START}
    dec["RATE_POLICY"] = "noop" if arm == "control" else ""
    (d / "run.json").write_text(json.dumps({"schema": "ck3bench.series.run/1", "launch_utc": "2026-01-01T00:00:00Z",
                                            "runner_flags": list(flags), "declared": dec}))
    (d / "trajectory.jsonl").write_text(json.dumps({"kind": "header"}) + "\n" + json.dumps(
        {"kind": "end", "reason": "horizon", "wall_utc": row_at}) + "\n")
    return d


@pytest.mark.parametrize("arm", ("control", "seated"))
def test_a_stop_after_an_end_row_stamped_at_or_after_the_wall_budget_is_not_in_k_n_prime_in_either_arm(tmp_path, arm):
    """The scorer judges a stopped attempt's end row by the runner's wall-budget rule (score.budget_end): from the
    attempt's launch and its runner's --wall-budget-s (100 s), a row stamped in the second before the deadline is
    `episode_over` (in k'/n'), one in the deadline's second or later is `wall_budget`, invalid as the runner would have
    made it: left out and named. One whose times do not read is left out and named too. The same in both arms."""
    from ck3bench.harness import score as SC
    assert SC.end_row_budget(_budget_dir(tmp_path, arm, "2026-01-01T00:01:39Z")) == "episode_over"
    assert SC.end_row_budget(_budget_dir(tmp_path, arm, "2026-01-01T00:01:40Z")) == "wall_budget"
    assert SC.end_row_budget(_budget_dir(tmp_path, arm, "2026-01-01T00:20:00Z")) == "wall_budget"
    assert SC.end_row_budget(_budget_dir(tmp_path, arm, "2026-01-01T00:00:30Z", flags=())) is None
    assert SC.end_row_budget(_budget_dir(tmp_path, arm, "no time")) is None
    kill0 = ["witness_absent:game_end", "witness_absent:end_json", "witness_absent:owner_tree", "file_absent:end.json"]
    rows = [_row("s-a01-a", 1, False, True, kill0), _row("s-a02-b", 2, False, True, kill0, budget="wall_budget"),
            _row("s-a03-a", 3, False, False, kill0, budget=None)]
    sv = SC.score(rows)["sensitivity_stopped_after_end"]
    assert sv["attempts"] == ["s-a01-a"] and (sv["k"], sv["n"]) == (1, 1)
    assert sv["end_row_after_budget"] == ["s-a02-b"] and sv["end_row_budget_unread"] == ["s-a03-a"]
    assert not SC.stopped_after_end(rows[1]) and not SC.stopped_after_end(rows[2])
    line = next(x for x in SC.table(rows, SC.score(rows)).splitlines() if x.startswith("sensitivity:"))
    assert "end row at or after the wall budget: s-a02-b" in line and "budget unread: s-a03-a" in line


#: the runner's start after the attempt's launch (the start steps between them; the seated arm's take longer)
START_GAP_S = {"control": 5.0, "seated": 40.0}


@pytest.mark.parametrize("arm", ("control", "seated"))
def test_a_stopped_attempt_s_end_row_is_judged_from_the_start_its_runner_recorded_in_either_arm(tmp_path, arm):
    """The runner records the instant it counts its wall budget from as it starts (runner_start.json), and the scorer
    counts from that one value, not from run.json's earlier launch: with the runner started START_GAP_S after the
    launch, a row stamped in the second before the runner's deadline (100 s) is in k'/n', one in that second is
    left out and named, in both arms. A run directory without the file is judged from its launch; one whose file
    does not read is named unread."""
    import types
    from ck3bench.harness import score as SC
    r = runner.Run(types.SimpleNamespace(out=str(tmp_path / "runner")))
    for f in (r.tx, r.turns, r.log):
        f.close()
    os.close(r.stdin_rec)
    assert json.loads((tmp_path / "runner" / SC.RUNNER_START).read_text())["start_s"] == r.t0_wall
    gap, launch = START_GAP_S[arm], SC._utc("2026-01-01T00:00:00Z")

    def row_at(after_start_s: float, start: object = "gap") -> Path:
        t = int(gap + after_start_s)                     # seconds after the launch, 2026-01-01T00:00:00Z
        d = _budget_dir(tmp_path / f"d{len(list(tmp_path.iterdir()))}", arm,
                        f"2026-01-01T00:{t // 60:02d}:{t % 60:02d}Z")
        if start == "gap":
            (d / SC.RUNNER_START).write_text(json.dumps({"start_s": launch + gap, "start_utc": "x"}))
        elif start is not None:
            (d / SC.RUNNER_START).write_text(start)
        return d
    in_time, late = row_at(99), row_at(100)
    assert SC.end_row_budget(in_time) == "episode_over" and SC.end_row_budget(late) == "wall_budget"
    assert SC.end_row_budget(row_at(99, start=None)) == "wall_budget"       # from the launch: 99 + gap s, late
    assert SC.end_row_budget(row_at(-gap + 99, start=None)) == "episode_over"
    for bad in ("[]", '"x"', "null", '{"start_s": "1"}', "{"):
        assert SC.end_row_budget(row_at(99, start=bad)) is None, bad
    kill0 = ["witness_absent:game_end", "witness_absent:end_json", "witness_absent:owner_tree", "file_absent:end.json"]
    rows = [_row("s-a01-a", 1, False, True, kill0, budget=SC.end_row_budget(in_time)),
            _row("s-a02-b", 2, False, True, kill0, budget=SC.end_row_budget(late))]
    sv = SC.score(rows)["sensitivity_stopped_after_end"]
    assert sv["attempts"] == ["s-a01-a"] and sv["end_row_after_budget"] == ["s-a02-b"], sv


def test_the_runner_and_the_scorer_judge_an_end_row_by_one_rule():
    """runner.Run.end_by_budget returns score.budget_end for the row's time: one function, two readers."""
    import inspect
    from ck3bench.harness import runner
    from ck3bench.harness import score as SC
    assert "budget_end(at, self.t0_wall, budget)" in inspect.getsource(runner.Run.end_by_budget)
    assert SC.budget_end(99.0, 0.5, 99.0) == "wall_budget" and SC.budget_end(98.9, 0.5, 99.0) == "episode_over"


def test_k_n_prime_leaves_out_an_attempt_numbered_after_a_scored_set_that_reached_its_target():
    """The scored set is the first valid_target valid runs by attempt number (a later valid one is played out and not
    scored); so a stopped attempt numbered after that set's last run is left out of k'/n' and named, and one numbered
    inside it is added. CONTROL: a series that reached its cap before its target scores every valid run, and adds
    every stopped one."""
    from ck3bench.harness import score as SC
    kill0 = ["witness_absent:game_end", "witness_absent:end_json", "witness_absent:owner_tree", "file_absent:end.json"]
    rows = [_row("s-a01-a", 1, True, True, target=2), _row("s-a02-b", 2, False, False, kill0, target=2),
            _row("s-a03-a", 3, True, False, target=2), _row("s-a04-b", 4, False, True, kill0, target=2),
            _row("s-a05-a", 5, True, True, target=2)]
    s = SC.score(rows)
    assert (s["k"], s["n"], s["played_out_not_scored"]) == (1, 2, ["s-a05-a"])
    sv = s["sensitivity_stopped_after_end"]
    assert sv["attempts"] == ["s-a02-b"] and sv["after_scored_set"] == ["s-a04-b"] and (sv["k"], sv["n"]) == (1, 3)
    short = [dict(r, valid_target=10) for r in rows]
    sv = SC.score(short)["sensitivity_stopped_after_end"]
    assert sv["attempts"] == ["s-a02-b", "s-a04-b"] and sv["after_scored_set"] == []


def test_the_depth_of_a_json_text_is_counted_outside_its_strings_and_bounds_what_a_step_reads(tmp_path):
    """json_depth counts brackets outside strings (an escaped quote does not end one); read_bounded_json reads a file
    at the bound and refuses one a level past it (TooDeep, a ValueError) before parsing, so the answer does not
    depend on which step reads the file."""
    assert S.json_depth('{"a": [1, {"b": "]]]}}}[[[ \\\\\\" {{{"}]}') == 3
    assert S.json_depth('"[[[["') == 0 and S.json_depth("7") == 0
    at, past = tmp_path / "at.json", tmp_path / "past.json"
    at.write_text("[" * S.JSON_DEPTH_MAX + "]" * S.JSON_DEPTH_MAX)
    past.write_text("[" * (S.JSON_DEPTH_MAX + 1) + "]" * (S.JSON_DEPTH_MAX + 1))
    assert isinstance(S.read_bounded_json(at), list)
    with pytest.raises(S.TooDeep):
        S.read_bounded_json(past)
    huge = tmp_path / "huge.json"
    huge.write_text("[" * 300000 + "]" * 300000)
    with pytest.raises(ValueError):
        S.read_bounded_json(huge)
