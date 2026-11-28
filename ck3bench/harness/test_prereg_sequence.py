"""The declared series' command sequence (docs/prereg-munster-kingdom.md, "Commands") on the fake backend, in the
document's order and with its commands: `series declare` for the control and the seated series from a host
configuration (`--config`, speed 5), one check run per series from the declared file itself, the frozen expectation
(the seated one with its `hosts` table before the first attempt, and never edited after it), the dry run, the
attempts on two hosts whose agent CLIs report different versions, a refused start and its retry of the same slot, an
attempt whose protected directory changes while it plays, `series withdraw` on an ended attempt (refused), the stop,
`score` (and its refusal when an attempt's directory is left out), `score --compare` and `series publish`.

What differs from the document, each because the stand-in is not the game: the task is the stand-in task file; the
series declare a 15 s (control) or 60 s (seated) wall budget (`RATE_WALL_BUDGET_S`), which their attempts then hold,
because a stand-in episode is over in seconds and the document's 27,900 s budget would hold its game for the whole
600 s FULL_HOLD; the checks run with that budget, not 900 s; the schedules' target and cap are 2 and 3 (control) and
2 and 2 (seated), not 40 and 56; each host's `RATE_CLAUDE_BIN` is a stand-in agent CLI (it answers `--version` and
plays the stand-in seat). The seated half needs the `mcp` package and is skipped without it. No game and no model:
every attempt is invalid on its game witnesses, and each series stops at its attempt cap.
"""
import hashlib
import json
import subprocess
import sys
import time
import tomllib
from pathlib import Path

import pytest

from ck3bench.harness import score as S
from ck3bench.harness import series as SR
from ck3bench.harness import test_driver as TD

needs_mcp = TD.needs_mcp
SPEED = "5"
#: this module's series and check run ids, its own (test_driver.unique_id): two suites on one host never share one
CTRL, SEATED = TD.unique_id("demo-control"), TD.unique_id("demo-seated")
CHECK_CTRL, CHECK_SEATED = TD.unique_id("check-control"), TD.unique_id("check-seated")


def _cli(tmp: Path, name: str, version: str) -> Path:
    """One host's versioned agent CLI binary, a stand-in: `--version` prints its version; anything else plays the
    stand-in seat (testdata/fake_cli.py), three turns then it exits."""
    p = tmp / f"claude-{name}"
    p.write_text(f"#!/bin/sh\nif [ \"$1\" = \"--version\" ]; then echo '{version} (Claude Code)'; exit 0; fi\n"
                 f"FAKE_CLAUDE_MODE=exit_at:3 exec {sys.executable} {TD.TD / 'fake_cli.py'} \"$@\"\n")
    p.chmod(0o755)
    return p


def _hosts(tmp: Path) -> tuple[dict, dict]:
    """The stand-in host (test_driver._host) and one host configuration per host, as the document writes them: the
    environment keeps only what a configuration does not set (a variable already in the environment wins)."""
    h = TD._host(tmp)
    moved = ("RATE_RUNS", "RATE_SO", "MAP_BIN", "RATE_WHEEL", "RATE_GAME_BIN", "RATE_INSTANCE", "RATE_PROTECTED_DIR",
             "KIT_REPO", "TURN_MODE", "SEAT_PROMPT", "NOTES", "RECORD", "VIEW", "CLEANMAP", "FULL_HOLD")
    shared = {k: h["env"][k] for k in moved}
    # RATE_SCENARIO stays: the stand-in task is a file, which an expectation names by its id
    for k in (*moved, "RATE_CLAUDE_BIN", "RATE_MODEL"):
        h["env"].pop(k, None)
    cfg = {}
    for name, version in (("a", "1.0.0"), ("b", "1.0.1")):
        env = {**shared, "RATE_INSTANCE": f"demo-{name}", "RATE_CLAUDE_BIN": str(_cli(tmp, name, version))}
        p = tmp / f"host-{name}.toml"
        p.write_text("[env]\n" + "".join(f"{k} = {json.dumps(v)}\n" for k, v in env.items()))
        cfg[name] = p
    return h, cfg


def _run(h: dict, *args, env: dict | None = None, timeout=600) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "ck3bench.harness", *args], env={**h["env"], **(env or {})},
                          cwd=h["clone"], capture_output=True, text=True, timeout=timeout)


def _declare(h: dict, cfg: Path, out: Path, budget: str, **env) -> dict:
    r = _run(h, "series", "declare", "--config", str(cfg),
             env={"RATE_GAME_SPEED": SPEED, "RATE_WALL_BUDGET_S": budget, **env})
    assert r.returncode == 0, r.stdout + r.stderr
    out.write_text(r.stdout)
    return json.loads(r.stdout)


def _freeze(path: Path, seen: dict, keys: tuple[str, ...], **extra) -> None:
    d = json.loads(path.read_text())
    for k in keys:
        assert "FREEZE" in d[k]
        d[k] = seen[k]
    d.update(extra)
    assert not [k for k, v in d.items() if isinstance(v, str) and "FREEZE" in v]
    path.write_text(json.dumps(d, indent=1))


@pytest.fixture(scope="module")
def control(tmp_path_factory):
    """The control series, every step of the document in order; what the test below checks is recorded here."""
    tmp = tmp_path_factory.mktemp("prereg")
    h, cfg = _hosts(tmp)
    runs = h["runs"]
    seen: dict = {"h": h, "cfg": cfg, "tmp": tmp}
    exp = tmp / "expect-control.json"
    seen["declared"] = _declare(h, cfg["a"], exp, "15", RATE_MODEL="none", RATE_POLICY="noop")
    # the check run, from the declared file as it is (a check leaves the values it reads out)
    r = _run(h, "series", "run", "--host", "a", "--config", str(cfg["a"]), "--expect", str(exp), "--policy", "noop",
             "--check", "--run-id", CHECK_CTRL, "--wall-budget-s", "15")
    assert r.returncode == 0, r.stdout + r.stderr
    seen["check"] = rj = json.loads((runs / CHECK_CTRL / "run.json").read_text())
    sched = tmp / "control.json"
    sched.write_text(json.dumps({"schema": S.SERIES_SCHEMA, "series": CTRL, "task": str(TD.STANDIN),
                                 "pattern": ["a", "b"], "valid_target": 2, "attempt_cap": 3}))
    attempt = ["series", "run", "--series", str(sched), "--expect", str(exp), "--policy", "noop"]
    # CONTROL: an attempt refuses the same file until it is frozen
    seen["unfrozen"] = _run(h, *attempt, "--host", "a", "--config", str(cfg["a"]), "--dry-run")
    seen["a01_after_unfrozen"] = (runs / f"{CTRL}-a01-a").exists()
    _freeze(exp, rj["end"]["seen"], ("notes_version",))
    seen["expect_sha"] = hashlib.sha256(exp.read_bytes()).hexdigest()
    seen["dry"] = _run(h, *attempt, "--host", "a", "--config", str(cfg["a"]), "--dry-run")
    # a start refused on its identity (another bridge library than the declared one): exit 6, no game, and nothing
    # left in the slot's place (no ledger entry, the directory moved aside), so the slot is retried as it is
    env_a = tomllib.loads(cfg["a"].read_text())["env"]
    other = tmp / "other-bridge.so"
    other.write_bytes(b"\x7fELF another bridge")
    wrong = tmp / "host-a-wrong.toml"
    wrong.write_text(cfg["a"].read_text().replace(json.dumps(env_a["RATE_SO"]), json.dumps(str(other))))
    seen["refused"] = _run(h, *attempt, "--host", "a", "--config", str(wrong))
    seen["after_refused"] = ((runs / f"{CTRL}-a01-a").exists(),
                             list((runs / f"{CTRL}.ledger").glob("*.json")))
    seen["aside"] = list((runs / S.REFUSED_DIR).glob(f"{CTRL}-a01-a-*"))
    # attempt 1 on host a, the same slot; its protected directory changes while it plays
    prot = Path(env_a["RATE_PROTECTED_DIR"])
    p = subprocess.Popen([sys.executable, "-m", "ck3bench.harness", *attempt, "--host", "a", "--config", str(cfg["a"])],
                         env=h["env"], cwd=h["clone"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    a01 = runs / f"{CTRL}-a01-a"
    t = time.monotonic()
    while not (a01 / "own.before.txt").is_file() and p.poll() is None and time.monotonic() - t < 300:
        time.sleep(0.2)
    assert (a01 / "own.before.txt").is_file()
    (prot / "settings.txt").write_text("changed by the run\n")
    out, _ = p.communicate(timeout=600)
    assert p.returncode == 0, out
    (prot / "settings.txt").write_text("unchanged\n")
    seen["ledger_01"] = (runs / f"{CTRL}.ledger" / "attempt-01.json").read_text()
    # an ended attempt is not withdrawn (its ledger entry stays as the end step wrote it)
    seen["withdraw_ended"] = _run(h, "series", "withdraw", str(a01), "--config", str(cfg["a"]))
    seen["ledger_01_after_withdraw"] = (runs / f"{CTRL}.ledger" / "attempt-01.json").read_text()
    # attempt 2 on host b, whose agent CLI reports another version: a control run starts none, so it plays
    seen["a02"] = _run(h, *attempt, "--host", "b", "--config", str(cfg["b"]))
    seen["a03"] = _run(h, *attempt, "--host", "a", "--config", str(cfg["a"]))
    seen["after_stop"] = _run(h, *attempt, "--host", "b", "--config", str(cfg["b"]))
    dirs = [runs / x for x in (f"{CTRL}-a01-a", f"{CTRL}-a02-b", f"{CTRL}-a03-a")]
    seen["dirs"] = dirs
    seen["score_missing"] = _run(h, "score", *map(str, dirs[1:]), "--json")
    seen["score"] = _run(h, "score", *map(str, dirs), "--json")
    seen["score_path"] = tmp / "control-score.json"
    seen["score_path"].write_text(seen["score"].stdout)
    seen["score_aside"] = _run(h, "score", str(seen["aside"][0]), "--json") if seen["aside"] else None
    dest = tmp / "published" / f"{CTRL}-a02-b"
    seen["publish"] = _run(h, "series", "publish", "--config", str(cfg["b"]), str(runs / f"{CTRL}-a02-b"),
                           str(dest))
    seen["dest"] = dest
    return seen


def test_the_control_series_runs_as_declared_on_two_hosts_and_a_protected_difference_is_invalid(control):
    c, tmp = control, control["tmp"]
    d = c["declared"]
    assert d["model"] == "none" and "claude_version" not in d and d["game_speed"] == 5
    assert not {"tool_listing_sha256", "seat_model_reported"} & set(d) and "FREEZE" in d["notes_version"]
    assert d["timings"]["wall_budget_s"] == 15.0
    rj = c["check"]
    assert rj["kind"] == "check" and rj["start_reasons"] == [] and rj["expectation"]["unfrozen"] == ["notes_version"]
    assert "notes_version" in rj["end"]["seen"]
    r = c["unfrozen"]
    assert r.returncode == 5 and "expect_not_frozen" in r.stdout + r.stderr and not c["a01_after_unfrozen"]
    r = c["dry"]
    assert r.returncode == 0 and f"{CTRL}-a01-a" in r.stdout and "--wall-budget-s 15.0" in r.stdout, r.stderr
    r = c["refused"]
    assert r.returncode == 6 and "expect_differs:bridge_so_sha256" in r.stdout + r.stderr, r.stdout + r.stderr
    assert c["after_refused"] == (False, [])
    assert len(c["aside"]) == 1 and "expect_differs:bridge_so_sha256" in (c["aside"][0] / S.START_REFUSED_FILE).read_text()
    a01, a02, a03 = c["dirs"]
    v = json.loads((a01 / "run.json").read_text())["validity"]
    assert v["valid"] is False and S.OWNER_TREE_DIFFERS in v["reasons"]
    led = json.loads(c["ledger_01"])
    assert led["valid"] is False and S.OWNER_TREE_DIFFERS in led["reasons"]
    assert led["expectation_sha256"] == c["expect_sha"] and S.validity_only(a01)["valid"] is False
    r = c["withdraw_ended"]
    assert r.returncode == 6 and "withdraw_ended" in r.stdout and c["ledger_01_after_withdraw"] == c["ledger_01"]
    assert not (a01 / S.WITHDRAWN_FILE).exists()
    assert c["a02"].returncode == 0, c["a02"].stdout + c["a02"].stderr
    rj2 = json.loads((a02 / "run.json").read_text())
    assert rj2["start_reasons"] == [] and rj2["start"]["claude_version"] == {"not_applicable": SR.NO_AGENT_CLI}
    assert S.OWNER_TREE_DIFFERS not in rj2["validity"]["reasons"]
    assert c["a03"].returncode == 0, c["a03"].stdout + c["a03"].stderr
    assert c["after_stop"].returncode == 7 and "series_stopped" in c["after_stop"].stderr
    r = c["score_missing"]
    assert r.returncode == 4 and f"series_attempt_missing: attempt 1 ({CTRL}-a01-a)" in r.stderr and not r.stdout
    r = c["score"]
    assert r.returncode == 0, r.stderr
    got = json.loads(r.stdout)
    rows = {x["run"]: x for x in got["runs"]}
    assert S.OWNER_TREE_DIFFERS in rows[f"{CTRL}-a01-a"]["invalid_reasons"]
    assert S.OWNER_TREE_DIFFERS not in rows[f"{CTRL}-a02-b"]["invalid_reasons"]
    assert got["score"]["attempts"] == 3 and got["gate"]["stopped"] is True and got["gate"]["override"] is False
    # the refused start scores as what it is, and blocks nothing: never in flight, never an attempt
    r = c["score_aside"]
    assert r.returncode == 0, r.stderr
    row = json.loads(r.stdout)["runs"][0]
    assert row["invalid_reasons"][0] == S.START_REFUSED and json.loads(r.stdout)["score"]["attempts"] == 0
    # what is published of an attempt: the copy names no host and no configured path
    assert c["publish"].returncode == 0, c["publish"].stdout + c["publish"].stderr
    text = "".join(f.read_text() for f in c["dest"].iterdir())
    # (the scorer's row names the run by its basename, so result.json carries no run-directory path to rewrite)
    assert str(tmp) not in text and "<RATE_PROTECTED_DIR>" in text
    assert json.loads((c["dest"] / "PUBLISHED.json").read_text())["placeholders"]


def _ended_by_hand(h: dict, cfg: Path, out: Path, tmp: Path, **env) -> tuple[dict, dict]:
    """The attempt `out`, as its host left it had it gone down right after the driver's after digest (before its
    `series end`), ended by the documented recovery: `series end OUT`, then `series close OUT`, by hand with the host
    configuration only (none of the variables the driver sets for its own steps: the arm, the model, the task, the
    speed, the expectation), plus `env`. On a copy; (the driver's validity, the hand recovery's validity)."""
    import shutil
    runs = tmp / f"hand-{out.name}" / "runs"
    shutil.copytree(out, runs / out.name)
    led = out.parent / (json.loads((out / "run.json").read_text())["series"]["id"] + ".ledger")
    shutil.copytree(led, runs / led.name)
    o = runs / out.name
    rj = json.loads((o / "run.json").read_text())
    driver = rj.pop("validity")
    rj.pop("end")
    (o / "run.json").write_text(json.dumps(rj, indent=1))
    for f in ("bridge_title_witness.json", "result.json", S.END_MARKER):
        (o / f).unlink(missing_ok=True)
    e = runs / led.name / f"attempt-{rj['series']['attempt']:02d}.json"
    ent = {k: v for k, v in json.loads(e.read_text()).items()
           if k not in ("valid", "reasons", "ended_utc", "not_attempt")}
    e.write_text(json.dumps(dict(ent, ended=False), indent=1))
    hand = {k: v for k, v in h["env"].items() if k not in SR.DECLARED_AT_START and k != "RATE_SERIES"}
    hand.update(RATE_RUNS=str(runs), **env)
    for step in ("end", "close"):
        r = subprocess.run([sys.executable, "-m", "ck3bench.harness", "series", step, str(o), "--config", str(cfg)],
                           env=hand, cwd=h["clone"], capture_output=True, text=True, timeout=600)
        assert r.returncode == 0, (step, r.stdout + r.stderr)
    return driver, json.loads((o / "run.json").read_text())["validity"]


def test_a_control_attempt_ended_by_hand_after_its_host_went_down_reads_as_its_driver_would(control):
    """A control attempt whose host went down after the driver's after digest and before its `series end`: the
    recovery by hand, in the host configuration (no RATE_POLICY, RATE_MODEL, RATE_SCENARIO or RATE_EXPECT), writes the
    validity the driver wrote: `series end` takes the arm, the model, the task and the expectation from what `series
    start` recorded. CONTROL: a seated arm's RATE_POLICY set in that environment does not make it a seated run, nor a
    stray RATE_MODEL another model."""
    a02 = control["dirs"][1]
    driver, hand = _ended_by_hand(control["h"], control["cfg"]["b"], a02, control["tmp"])
    assert hand == driver, (driver, hand)
    assert not [x for x in hand["reasons"] if x.startswith("start_end_differs:")], hand
    driver, stray = _ended_by_hand(control["h"], control["cfg"]["b"], a02, control["tmp"] / "stray",
                                   RATE_POLICY="", RATE_MODEL="another-model", RATE_SCENARIO="no-such-task")
    assert stray == driver, (driver, stray)


@needs_mcp
def test_the_seated_series_runs_from_its_declared_file_and_is_compared_with_the_control(control):
    """The seated series: the check from the declared file (the three values it reads left out), the expectation
    frozen with its `hosts` table of the two agent CLI versions before the first attempt, both hosts' attempts on
    that one file, the stop, the score and the comparison. CONTROL: a copy without the table refuses host b's start
    on the CLI version, and, a different file from attempt 1's, on the one expectation per series."""
    h, cfg, tmp = control["h"], control["cfg"], control["tmp"]
    runs = h["runs"]
    exp = tmp / "expect-seated.json"
    d = _declare(h, cfg["a"], exp, "60", RATE_MODEL="stand-in")
    assert d["claude_version"] == "1.0.0 (Claude Code)" and d["timings"]["wall_budget_s"] == 60.0
    r = _run(h, "series", "run", "--host", "a", "--config", str(cfg["a"]), "--expect", str(exp), "--check",
             "--run-id", CHECK_SEATED, "--wall-budget-s", "60")
    assert r.returncode == 0, r.stdout + r.stderr
    rj = json.loads((runs / CHECK_SEATED / "run.json").read_text())
    assert rj["start_reasons"] == [] and rj["expectation"]["unfrozen"] == [
        "notes_version", "seat_model_reported", "tool_listing_sha256"]
    seen = rj["end"]["seen"]
    assert seen["tool_listing_sha256"] and seen["seat_model_reported"]
    sched = tmp / "seated.json"
    sched.write_text(json.dumps({"schema": S.SERIES_SCHEMA, "series": SEATED, "task": str(TD.STANDIN),
                                 "pattern": ["a", "b"], "valid_target": 2, "attempt_cap": 2}))
    no_table = tmp / "expect-seated-no-hosts.json"
    _freeze(exp, seen, ("notes_version", "tool_listing_sha256", "seat_model_reported"))
    no_table.write_text(exp.read_text())
    frozen = json.loads(exp.read_text())
    frozen["hosts"] = {"a": {"claude_version": "1.0.0 (Claude Code)"}, "b": {"claude_version": "1.0.1 (Claude Code)"}}
    exp.write_text(json.dumps(frozen, indent=1))
    before = exp.read_bytes()
    attempt = ["series", "run", "--series", str(sched)]
    r = _run(h, *attempt, "--expect", str(exp), "--host", "a", "--config", str(cfg["a"]), "--dry-run")
    assert r.returncode == 0 and f"{SEATED}-a01-a" in r.stdout, r.stdout + r.stderr
    r = _run(h, *attempt, "--expect", str(exp), "--host", "a", "--config", str(cfg["a"]))
    assert r.returncode == 0, r.stdout + r.stderr
    r = _run(h, *attempt, "--expect", str(no_table), "--host", "b", "--config", str(cfg["b"]))
    out = r.stdout + r.stderr
    assert r.returncode == 6 and "expect_differs:claude_version" in out and "series_expectation_differs" in out, out
    assert not (runs / f"{SEATED}-a02-b").exists()
    r = _run(h, *attempt, "--expect", str(exp), "--host", "b", "--config", str(cfg["b"]))
    assert r.returncode == 0, r.stdout + r.stderr
    assert exp.read_bytes() == before                     # the expectation is never edited after attempt 1
    rj = json.loads((runs / f"{SEATED}-a02-b" / "run.json").read_text())
    assert rj["start_reasons"] == [] and rj["start"]["claude_version"]["version"] == "1.0.1 (Claude Code)"
    dirs = [runs / x for x in (f"{SEATED}-a01-a", f"{SEATED}-a02-b")]
    # the documented recovery by hand of a seated attempt reads it as its driver did, whatever the environment says
    driver, hand = _ended_by_hand(h, cfg["b"], dirs[1], tmp, RATE_POLICY="noop", RATE_MODEL="none")
    assert hand == driver and not [x for x in hand["reasons"] if x.startswith("start_end_differs:")], (driver, hand)
    r = _run(h, "score", *map(str, dirs), "--json")
    assert r.returncode == 0, r.stderr
    (tmp / "seated-score.json").write_text(r.stdout)
    assert all(x["seated"] is True for x in json.loads(r.stdout)["runs"])
    r = _run(h, "score", "--compare", str(tmp / "seated-score.json"), str(control["score_path"]))
    assert r.returncode == 0, r.stderr
    got = json.loads(r.stdout)
    assert (got["seated_series"], got["control_series"]) == (SEATED, CTRL)
    r = _run(h, "score", "--compare", str(control["score_path"]), str(tmp / "seated-score.json"))
    assert r.returncode == 2 and "is not a seated series" in r.stderr
