"""A series run's validity rules (ck3bench.harness.series): the launch identity, the start and end witnesses, the
task, the save readback, and the operator hooks that are not configured. No game, no model."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from ck3bench.harness import game_identity
from ck3bench.harness import series as SR


def _ident(**over):
    base = {"git": {"commit": "a" * 40, "dirty": False}, "bridge_so": {"sha256": "b" * 64},
            "wheel": {"sha256": "c" * 64, "compare": {"verdict": "identical", "wheel_members_sha256": "d" * 64}},
            "map": {"sha256": "e" * 64}, "game_build_id": {"build_id": "f" * 40},
            "claude_version": {"version": "2.1.0 (Claude Code)"}, "start_save": {"sha256": "1" * 64},
            "prompt": {"sha256": "2" * 64}, "model": "opus", "full_hold_s": 600, "switches": {}}
    base.update(over)
    return base


def test_the_launch_identity_names_each_failure():
    exp = {"kind": "rate", "git_commit": "a" * 40, "bridge_so_sha256": "b" * 64}
    assert SR.check_static(_ident(), exp, "rate") == []
    assert SR.check_static(_ident(), None, "rate") == ["expectation_not_declared"]
    assert "tree_dirty" in SR.check_static(_ident(git={"commit": "a" * 40, "dirty": True}), exp, "rate")
    assert "expect_differs:bridge_so_sha256" in SR.check_static(_ident(bridge_so={"sha256": "9" * 64}), exp, "rate")
    assert "unread:map" in SR.check_static(_ident(map={"unread": "no such file"}), exp, "rate")
    assert "wheel_install_differs" in SR.check_static(
        _ident(wheel={"sha256": "c" * 64, "compare": {"verdict": "DIFFERS"}}), exp, "rate")
    # a check plays the expectation an attempt will (it reads the values that one freezes); an attempt never plays
    # an expectation declared for checks
    assert SR.check_static(_ident(), exp, "check") == []
    assert "expect_kind:check!=rate" in SR.check_static(_ident(), {**exp, "kind": "check"}, "rate")
    assert SR.check_static(_ident(), {**exp, "kind": "check"}, "check") == []
    # a --policy run records no agent CLI: a declared version is not held (the hosts' CLIs may differ)
    na = _ident(claude_version={"not_applicable": SR.NO_AGENT_CLI})
    assert SR.check_static(na, {**exp, "claude_version": "9.9.9 (Claude Code)"}, "rate") == []
    assert "expect_differs:claude_version" in SR.check_static(_ident(), {**exp, "claude_version": "9.9.9"}, "rate")
    assert SR.check_task({"scenario": "a"}, {"task": "b"}) == ["expect_differs:task"]
    assert SR.check_task({"scenario": "a"}, {"task": "a"}) == SR.check_task({"scenario": "a"}, None) == []


def _w(sha="b" * 64, bid="f" * 40, env=None, servers=True):
    e = env if env is not None else {"CK3_BRIDGE_ARMY_ORDERS": "1"}
    return {"pid": 7, "bridge_mapped": [{"path": "/x/libck3_bridge.so", "sha256": sha}], "build_id": {"build_id": bid},
            "environ": e, "servers": [{"pid": 8, "environ": e}] if servers else []}


def test_the_witnesses_hold_the_game_to_its_launch_identity():
    rj = {"start": _ident()}
    exp = {"switches": {"CK3_BRIDGE_ARMY_ORDERS": "1", "CK3_BRIDGE_AI_VETO": None}}
    assert SR.witness_reasons(rj, _w(), _w(), exp) == []
    assert SR.witness_reasons(rj, {"unread": "x"}, _w(), exp) == ["witness_absent:game_start"]
    assert "bridge_mapped_differs_end" in SR.witness_reasons(rj, _w(), _w(sha="0" * 64), exp)
    assert "game_build_id_differs_start" in SR.witness_reasons(rj, _w(bid="0" * 40), _w(), exp)
    r = SR.witness_reasons(rj, _w(env={"CK3_BRIDGE_ARMY_ORDERS": "1", "CK3_BRIDGE_AI_VETO": "0"}), _w(), exp)
    assert "game_switch_differs:CK3_BRIDGE_AI_VETO_start" in r
    assert "witness_absent:toolkit_server_end" in SR.witness_reasons(rj, _w(), _w(servers=False), exp)
    w2 = _w()
    w2["pid"] = 9
    assert "game_pid_changed" in SR.witness_reasons(rj, _w(), w2, exp)


def test_the_save_header_date_reads_back(tmp_path):
    f = tmp_path / "x.ck3"
    f.write_bytes(b"SAV0103\nmeta_data={\n\tsave_game_version=3\n\tmeta_date=1086.3.14\n}\n" + b"\0" * 100)
    assert SR.save_header_date(f) == {"meta_date": "1086.3.14"}
    assert SR.date_key("1086.03.14") == SR.date_key("1086.3.14")
    g = tmp_path / "y.ck3"
    g.write_bytes(b"PK\3\4 binary")
    assert "unread" in SR.save_header_date(g)


def test_build_id_is_the_elf_note():
    r = subprocess.run(["readelf", "-n", os.path.realpath(sys.executable)], capture_output=True, text=True) \
        if Path("/usr/bin/readelf").exists() else None
    if r is None or r.returncode != 0 or "Build ID" not in r.stdout:
        pytest.skip("readelf gives no build id here")
    want = r.stdout.split("Build ID:")[1].split()[0]
    assert SR.build_id(os.path.realpath(sys.executable)) == {"build_id": want}
    assert "unread" in SR.build_id(__file__)


def test_hooks_not_configured_are_said_never_ok(tmp_path, monkeypatch):
    for k in ("RATE_HOLD_CHECK", "RATE_UI"):
        monkeypatch.delenv(k, raising=False)
    assert SR.hold_framecheck(tmp_path) == {"checked": False, "reason": "no hold check configured (RATE_HOLD_CHECK)"}
    fs = SR.final_save(tmp_path, os.getpid(), 0.0, 0.0, None)
    assert fs["saved"] is False and "no UI driver configured" in fs["reason"]


def test_a_watch_that_saw_no_game_records_the_hold_check_as_not_checked(tmp_path, monkeypatch):
    """With no game witnessed the hold check never runs, and framecheck_hold.json says so (`checked: false` with
    why), the shape docs/harness.md gives; it is never left absent for result.json to fill."""
    monkeypatch.setenv("RATE_WATCH_S", "5")
    (tmp_path / "done").write_text("rc=0\n")
    assert SR.cmd_watch(tmp_path) == 0
    fc = json.loads((tmp_path / "framecheck_hold.json").read_text())
    assert fc["checked"] is False and "no game process witnessed" in fc["reason"]


def test_a_hold_check_hook_is_recorded_as_it_printed(tmp_path, monkeypatch):
    hook = tmp_path / "hook.py"
    hook.write_text('import json, sys\nprint("noise")\nprint(json.dumps({"checked": True, "verdict": "ok", '
                    '"dir": sys.argv[1]}))\n')
    monkeypatch.setenv("RATE_HOLD_CHECK", f"{sys.executable} {hook}")
    assert SR.hold_framecheck(tmp_path) == {"checked": True, "verdict": "ok", "dir": str(tmp_path)}
    hook.write_text("print('no json')\n")
    assert SR.hold_framecheck(tmp_path)["checked"] is False


def test_the_plan_refuses_a_turn_mode_or_prompt_the_harness_does_not_have(monkeypatch):
    monkeypatch.setenv("RATE_SCENARIO", "munster1066-kingdom-once-30y-wake")
    monkeypatch.delenv("RATE_GAME_SPEED", raising=False)
    monkeypatch.delenv("TURN_MODE", raising=False)
    monkeypatch.delenv("SEAT_PROMPT", raising=False)
    p = SR.plan()
    assert p["seat"] == "munster" and p["goal"] == "kingdom_held_once"
    from ck3bench.harness import prompt
    import hashlib
    sc = SR._scenario()
    want = hashlib.sha256(prompt.render_prompt(sc, sc.n_steps).encode()).hexdigest()
    assert SR.rendered_prompt(p) == {"template": "seat_prompt.md", "sha256": want}
    for k, v in (("TURN_MODE", "continuous"), ("SEAT_PROMPT", "v2")):
        monkeypatch.setenv(k, v)
        with pytest.raises(SystemExit, match="REFUSE"):
            SR.plan()
        monkeypatch.delenv(k)


def test_the_game_is_found_from_the_run_s_own_record_never_a_name(tmp_path):
    assert game_identity.owned(tmp_path)[0] is None
    (tmp_path / "trajectory.jsonl").write_text(json.dumps({"kind": "header", "provenance": {"pid": os.getpid()}}) + "\n")
    pid, why = game_identity.owned(tmp_path)
    assert pid is None and "not a CK3 binary" in why


_LOG = ("[CK3-Bridge][instance] id=t\n"
        "[x] Action: create_title params: {\"params\": {\"title_key\": \"k_ireland\", \"actor_id\": 36303}}\n"
        "[x] create_title intent COMPLETED: actor=36303 title_id=461\n")


def _holder(path: Path):
    """A process holding PATH open, as the game holds its bridge log."""
    return subprocess.Popen([sys.executable, "-c", f"import sys,time; f=open({str(path)!r},'a'); "
                             "sys.stdout.write('ok\\n'); sys.stdout.flush(); time.sleep(60)"],
                            stdout=subprocess.PIPE, text=True)


def test_the_bridge_log_is_read_only_when_it_is_the_one_this_run_s_game_held(tmp_path):
    log = tmp_path / "bridge.log"
    log.write_text(_LOG)
    p = _holder(log)
    try:
        assert p.stdout.readline() == "ok\n"
        w = {"pid": p.pid, "bridge_log_held": SR.held_log(p.pid, str(log))}
        assert w["bridge_log_held"]["held"] == [{"dev": log.stat().st_dev, "ino": log.stat().st_ino}]
        got = SR.bridge_title_witness("k_ireland", str(log), witnesses=[w])
        assert got["state"] == "read" and got["held_by_game"]["held"] is True
        assert got["completed"] == [{"line": 3, "actor_id": 36303, "title_id": 461}]
        # another run's log at the same path (a fresh inode, as the bridge writes it): not read
        log.unlink()
        log.write_text(_LOG)
        got = SR.bridge_title_witness("k_ireland", str(log), witnesses=[w])
        assert got["state"] == "unwitnessed" and got["reason_code"] == "log_replaced" and "commands" not in got
        # the game seen, holding no file at the path
        other = {"pid": p.pid, "bridge_log_held": SR.held_log(p.pid, str(tmp_path / "elsewhere.log"))}
        assert other["bridge_log_held"]["held"] == []
        assert SR.bridge_title_witness("k_ireland", str(log), witnesses=[other])["reason_code"] == "log_not_held"
    finally:
        p.kill()
        p.wait(10)
    # no game witnessed (nobody looked): unwitnessed, never read; an empty witness list is the same state
    for ws in ([], [{"unread": "no game process witnessed"}], [{"pid": 1}]):
        got = SR.bridge_title_witness("k_ireland", str(log), witnesses=ws)
        assert (got["state"], got["reason_code"]) == ("unwitnessed", "no_game_witness"), got
    # a caller that names the log vouches for it: read, and the record says it was not checked
    got = SR.bridge_title_witness("k_ireland", str(log))
    assert got["state"] == "read" and got["held_by_game"] == {"checked": False, "why": "the caller named the log"}
    assert SR.bridge_title_witness("k_ireland", str(tmp_path / "none.log"), witnesses=[])["state"] == "absent"


def test_CONTROL_the_scorer_corroborates_only_from_a_read_log(tmp_path):
    """An unwitnessed log leaves the creator uncorroborated (null), as an absent one does; only `read` counts."""
    from ck3bench.harness import score as S
    created, calls = {"by": "you: create_title k_ireland", "date": "1080.1.1"}, [{"tool": "do", "step": 3}]
    for state, want in (("read", True), ("unwitnessed", None), ("absent", None)):
        (tmp_path / "bridge_title_witness.json").write_text(json.dumps(
            {"state": state, "completed": [{"line": 3}], "commands": [{"line": 2}]}))
        assert S.creator(created, calls, tmp_path)["corroborated"] is want, state


def test_every_series_step_reads_the_host_config(tmp_path):
    """`--config FILE` reaches `series declare` (and every other step): a variable only the file sets is read; one
    the environment sets wins."""
    cfg = tmp_path / "host.toml"
    task = Path(__file__).with_name("testdata") / "standin-ireland-fake.toml"
    cfg.write_text(f'[env]\nRATE_SCENARIO = "{task}"\nRATE_MODEL = "from-file"\n')
    env = {k: v for k, v in os.environ.items() if not k.startswith(("RATE_", "CK3_BRIDGE_"))}
    env["RATE_MODEL"] = "from-env"
    r = subprocess.run([sys.executable, "-m", "ck3bench.harness", "series", "declare", "--config", str(cfg)],
                       env=env, capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stdout + r.stderr
    d = json.loads(r.stdout)
    assert d["task"] == "standin-ireland-fake" and d["model"] == "from-env"
    r = subprocess.run([sys.executable, "-m", "ck3bench.harness", "series", "declare"], env=env,
                       capture_output=True, text=True, timeout=300)
    assert r.returncode != 0 or json.loads(r.stdout)["task"] != "standin-ireland-fake"
    bad = tmp_path / "bad.toml"
    bad.write_text('[other]\nX = "1"\n')
    r = subprocess.run([sys.executable, "-m", "ck3bench.harness", "series", "plan", "--config", str(bad)], env=env,
                       capture_output=True, text=True, timeout=300)
    assert r.returncode == 2 and "REFUSE config" in r.stdout


def test_a_policy_run_has_no_session_witnesses_and_that_is_not_a_missing_one(tmp_path):
    """A --policy run starts no agent session, so it writes no tool listing and no init event: `series end` records
    that state and gives no reason, unless the expectation declares a value for one (then it is held and absent).
    CONTROL: a seated run without the two files reds on both; a seated run with them passes."""
    policy, seated = {"turn_mode": "policy", "policy": "noop"}, {"turn_mode": "fresh"}
    reasons, seen = SR.session_witnesses(tmp_path, policy, {"task": "t"})
    assert reasons == [] and seen == {"tool_listing_sha256": SR.NO_SESSION, "seat_model_reported": SR.NO_SESSION}
    reasons, _ = SR.session_witnesses(tmp_path, policy, {"tool_listing_sha256": "a" * 64, "seat_model_reported": "m"})
    assert reasons == ["witness_absent:tool_listing", "witness_absent:session_init"]
    reasons, _ = SR.session_witnesses(tmp_path, seated, {"task": "t"})
    assert reasons == ["witness_absent:tool_listing", "witness_absent:session_init"]
    assert SR.session_witnesses(tmp_path, None, None)[0] == reasons          # no end.json: not a policy run
    (tmp_path / "tool_listing.json").write_text("[]")
    (tmp_path / "session_init.json").write_text(json.dumps({"model": "m"}))
    reasons, seen = SR.session_witnesses(tmp_path, seated, {"seat_model_reported": "m"})
    assert reasons == [] and seen["seat_model_reported"] == "m" and len(seen["tool_listing_sha256"]) == 64
    assert SR.session_witnesses(tmp_path, seated, {"seat_model_reported": "x"})[0] == \
        ["expect_differs:seat_model_reported"]


def test_a_withdrawn_attempt_s_ledger_entry_is_closed_and_the_slot_moves_on(tmp_path, monkeypatch):
    """`series withdraw OUT`: the record written, the ledger entry ended, invalid and not an attempt, so the stopping
    rule counts it as no attempt and the scorer reads it as ended (never in flight) though no end step ran."""
    from ck3bench.harness import score as S
    sched = tmp_path / "s.json"
    sched.write_text(json.dumps({"schema": S.SERIES_SCHEMA, "series": "w", "task": "t", "pattern": ["a", "b"],
                                 "valid_target": 1, "attempt_cap": 2}))
    out = tmp_path / "runs" / "w-a01-a"
    out.mkdir(parents=True)
    (out / "run.json").write_text(json.dumps({"run_id": "w-a01-a", "host": "a", "kind": "rate",
                                              "series": {"id": "w", "schedule": str(sched), "attempt": 1, "valid_target": 1, "attempt_cap": 2}}))
    monkeypatch.setenv("RATE_RUNS", str(tmp_path / "runs"))
    monkeypatch.delenv("RATE_LEDGER", raising=False)
    SR.write_ledger(S.load_series(sched), {"attempt": 1, "host": "a", "run_id": "w-a01-a", "ended": False})
    assert S.validity_only(out)["ended"] is False                       # CONTROL: no end step, no record: in flight
    assert SR.main(["withdraw", str(out)]) == 0
    e = json.loads((tmp_path / "runs" / "w.ledger" / "attempt-01.json").read_text())
    assert (e["ended"], e["valid"], e["not_attempt"], e["reasons"]) == (True, False, True, [S.WITHDRAWN])
    assert (out / S.WITHDRAWN_FILE).is_file()
    v = S.validity_only(out)
    assert (v["ended"], v["valid"], v["not_attempt"]) == (True, False, True)
    st = S.stop_state(S.load_series(sched), SR.ledger(S.load_series(sched)))
    assert (st["attempts"], st["in_flight"], st["stop"]) == (0, [], None)
    got, refuse = SR.slot(S.load_series(sched), "b", "w-a02-b")
    assert refuse == [] and got["attempt"] == 2
    assert SR.main(["withdraw", str(tmp_path)]) == 6                    # not a series attempt


# ── one expectation per series, the hosts table, an ended attempt, unread values at declare ──
def _sched(tmp_path) -> Path:
    from ck3bench.harness import score as S
    p = tmp_path / "s.json"
    p.write_text(json.dumps({"schema": S.SERIES_SCHEMA, "series": "w", "task": "t", "pattern": ["a", "b"],
                             "valid_target": 1, "attempt_cap": 3}))
    return p


def test_a_series_attempt_plays_the_one_expectation_its_ledger_started_with(tmp_path, monkeypatch):
    """Attempt 2 with another expectation file than attempt 1's is refused (`series_expectation_differs`); an entry
    that records none is unwitnessed, never agreed; no expectation at all is refused. CONTROL: the same file plays."""
    from ck3bench.harness import score as S
    monkeypatch.setenv("RATE_RUNS", str(tmp_path / "runs"))
    monkeypatch.delenv("RATE_LEDGER", raising=False)
    ser = S.load_series(_sched(tmp_path))
    assert SR.series_expectation_reasons(ser, {"_sha256": "1" * 64}) == []             # the first attempt
    SR.write_ledger(ser, {"attempt": 1, "host": "a", "run_id": "w-a01-a", "ended": True,
                          "expectation_sha256": "1" * 64})
    assert SR.series_expectation_reasons(ser, {"_sha256": "1" * 64}) == []
    assert SR.series_expectation_reasons(ser, {"_sha256": "2" * 64}) == ["series_expectation_differs"]
    assert SR.series_expectation_reasons(ser, None) == ["series_expectation_absent"]
    SR.write_ledger(ser, {"attempt": 2, "host": "b", "run_id": "w-a02-b", "ended": True})
    assert SR.series_expectation_reasons(ser, {"_sha256": "1" * 64}) == ["series_expectation_unwitnessed"]


def test_the_hosts_table_holds_the_agent_cli_version_and_nothing_else(tmp_path, monkeypatch):
    p = tmp_path / "e.json"
    base = {"schema": SR.EXPECT_SCHEMA, "kind": "rate", "model": "m", "bridge_so_sha256": "b" * 64}
    monkeypatch.setenv("RATE_EXPECT", str(p))
    monkeypatch.setenv("RATE_HOST", "b")
    monkeypatch.setenv("RATE_KIND", "rate")
    p.write_text(json.dumps({**base, "hosts": {"a": {"claude_version": "1"}, "b": {"claude_version": "2"}}}))
    got = SR.load_expect()
    assert got["claude_version"] == "2" and got["bridge_so_sha256"] == "b" * 64           # CONTROL
    for bad in ({"b": {"bridge_so_sha256": "x" * 64}}, {"b": {"claude_version": "2", "model": "other"}}, {"b": "2"}):
        p.write_text(json.dumps({**base, "hosts": bad}))
        with pytest.raises(SystemExit, match="expect_hosts_key"):
            SR.load_expect()


def test_withdraw_refuses_an_attempt_that_ended(tmp_path, monkeypatch):
    """An ended attempt (its ledger entry ended, or its run directory holding its validity and end marker) is not
    withdrawn: its valid ledger entry stays as it is. CONTROL: a started, unended attempt is withdrawn."""
    from ck3bench.harness import score as S
    sched = _sched(tmp_path)
    monkeypatch.setenv("RATE_RUNS", str(tmp_path / "runs"))
    monkeypatch.delenv("RATE_LEDGER", raising=False)
    ser = S.load_series(sched)
    outs = {}
    for i, host in ((1, "a"), (2, "b"), (3, "a")):
        out = tmp_path / "runs" / f"w-a0{i}-{host}"
        out.mkdir(parents=True)
        (out / "run.json").write_text(json.dumps({"run_id": out.name, "host": host, "kind": "rate",
                                                  "series": {"id": "w", "schedule": str(sched), "attempt": i,
                                                             "valid_target": 1, "attempt_cap": 2}}))
        outs[i] = out
    SR.write_ledger(ser, {"attempt": 1, "run_id": "w-a01-a", "ended": True, "valid": True})
    assert SR.main(["withdraw", str(outs[1])]) == 6 and not (outs[1] / S.WITHDRAWN_FILE).exists()
    assert json.loads((tmp_path / "runs" / "w.ledger" / "attempt-01.json").read_text())["valid"] is True
    SR.write_ledger(ser, {"attempt": 2, "run_id": "w-a02-b", "ended": False})
    rj = json.loads((outs[2] / "run.json").read_text())
    (outs[2] / "run.json").write_text(json.dumps({**rj, "validity": {"valid": True, "reasons": []}}))
    (outs[2] / "rate_run.done").write_text("rc=0\n")
    assert SR.main(["withdraw", str(outs[2])]) == 6                 # ended in its directory, its ledger entry still open
    assert SR.main(["withdraw", str(outs[3])]) == 6                 # no ledger entry: never started
    SR.write_ledger(ser, {"attempt": 3, "run_id": "w-a03-a", "ended": False})
    assert SR.main(["withdraw", str(outs[3])]) == 0 and (outs[3] / S.WITHDRAWN_FILE).is_file()


def test_declare_writes_a_placeholder_for_a_value_it_cannot_read(tmp_path):
    """A bridge library that is not there: `series declare` writes a `<FREEZE: ...>` placeholder for its sha256
    (naming why and where a check run reads it), never leaves the key out; an attempt refuses the file
    (`expect_not_frozen`). CONTROL: the values it can read are written."""
    task = Path(__file__).with_name("testdata") / "standin-ireland-fake.toml"
    env = {k: v for k, v in os.environ.items() if not k.startswith(("RATE_", "CK3_BRIDGE_"))}
    env.update(RATE_SCENARIO=str(task), RATE_MODEL="m", RATE_SO=str(tmp_path / "missing.so"), RATE_POLICY="noop")
    r = subprocess.run([sys.executable, "-m", "ck3bench.harness", "series", "declare"], env=env,
                       capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stdout + r.stderr
    d = json.loads(r.stdout)
    assert d["bridge_so_sha256"].startswith("<FREEZE: unread at declare") and "start.bridge_so.sha256" in \
        d["bridge_so_sha256"]
    assert "claude_version" not in d and d["model"] == "m" and "timings" in d       # a --policy series: no CLI
    p = tmp_path / "e.json"
    p.write_text(json.dumps({**d, "notes_version": "n"}))
    env.update(RATE_EXPECT=str(p), RATE_KIND="rate", RATE_HOST="a")
    r = subprocess.run([sys.executable, "-c", "from ck3bench.harness import series as SR; SR.load_expect()"], env=env,
                       capture_output=True, text=True, timeout=300)
    assert r.returncode != 0 and "expect_not_frozen" in r.stderr and "bridge_so_sha256" in r.stderr


def test_series_start_holds_the_runner_s_timings_to_the_expectation_whatever_the_driver():
    """The runner flags a driver passed (run.json `runner_flags`) against the expectation's `timings`: another
    value, or a flag missing, is a reason (a driver of your own cannot run other timings and stay valid). CONTROL:
    the declared values are none."""
    t = {"turn_timeout_s": 1200.0, "step_wait_s": 900.0, "wall_budget_s": 27900.0, "wall_s": 28800.0}
    flags = ["--scenario", "x", "--turn-timeout-s", "1200.0", "--step-wait-s", "900.0", "--wall-budget-s", "27900.0"]
    assert SR.timing_reasons(flags, {"timings": t}) == []
    assert SR.timing_reasons([x if x != "900.0" else "60" for x in flags], {"timings": t}) == \
        ["expect_differs:timings.step_wait_s"]
    assert SR.timing_reasons(flags[:-2], {"timings": t}) == ["witness_absent:runner_flag:wall_budget_s"]
    assert SR.timing_reasons(flags, {}) == ["expect_timings_absent"]


def test_the_timings_are_read_as_the_runner_reads_its_flags():
    """A repeated flag (the runner plays the last), an abbreviation and `--flag=value` are read as `run-seat` reads
    them: another value than the declared one is a reason. CONTROL: the same values spelled those ways agree."""
    t = {"turn_timeout_s": 1200.0, "step_wait_s": 900.0, "wall_budget_s": 27900.0, "wall_s": 28800.0}
    base = ["--scenario", "x", "--turn-timeout-s", "1200", "--step-wait-s", "900", "--wall-budget-s", "27900"]
    assert SR.timing_reasons(base + ["--turn-timeout-s", "60"], {"timings": t}) == ["expect_differs:timings.turn_timeout_s"]
    assert SR.timing_reasons(base + ["--wall-budget", "99"], {"timings": t}) == ["expect_differs:timings.wall_budget_s"]
    assert SR.timing_reasons(base[:-2] + ["--wall-budget-s=27900"], {"timings": t}) == []
    assert SR.timing_reasons(base + ["--turn-timeout-s", "1200.0"], {"timings": t}) == []
    assert SR.timing_reasons(["--step-wait-s", "not-a-number"], {"timings": t}) == [
        f"witness_absent:runner_flag:{k}" for k in SR.TIMING_FLAGS]


def test_a_schedule_that_does_not_read_is_a_refuse_line_not_a_traceback(tmp_path, monkeypatch, capsys):
    from ck3bench.harness import score as S
    p = tmp_path / "s.json"
    p.write_text(json.dumps({"schema": S.SERIES_SCHEMA, "series": "w", "task": "t", "pattern": ["rackbox"],
                             "valid_target": 1, "attempt_cap": 1}))
    monkeypatch.setenv("RATE_SERIES", str(p))
    monkeypatch.setenv("RATE_LEDGER", str(tmp_path / "led"))
    assert SR.main(["slot", "a", "w-a01-a"]) == 2
    out = capsys.readouterr().out
    assert out.startswith("REFUSE slot: ValueError") and "Traceback" not in out


def test_an_expectation_reads_the_same_from_a_step_with_many_calls_above_it(tmp_path, monkeypatch):
    """The nesting bound is counted in the text, so an expectation at the bound reads, and one a level past it is
    refused (`expect_schema`), alike from the top of a step and from far down its calls: a file `series start` reads,
    `series end` reads too."""
    from ck3bench.harness import score as SC
    p = tmp_path / "e.json"
    monkeypatch.setenv("RATE_EXPECT", str(p))
    monkeypatch.setenv("RATE_HOST", "b")
    monkeypatch.setenv("RATE_KIND", "rate")

    def member(levels):
        return ('{"schema": "%s", "kind": "rate", "notes_for_reader": ' % SR.EXPECT_SCHEMA
                + '{"a": ' * levels + "1" + "}" * levels + "}")

    def below(n):
        return SR.load_expect() if n == 0 else below(n - 1)

    for calls in (0, 800):
        p.write_text(member(SC.JSON_DEPTH_MAX - 1))                  # the document's own level and the member's
        assert below(calls)["schema"] == SR.EXPECT_SCHEMA
        p.write_text(member(SC.JSON_DEPTH_MAX))
        with pytest.raises(SystemExit, match="expect_schema"):
            below(calls)
