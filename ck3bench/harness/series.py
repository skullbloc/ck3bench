"""python -m ck3bench.harness series STEP ...: the identity, witness and result steps of one seated run of a series.

A series driver calls these steps in order for each attempt: the reference driver `series run`
(ck3bench.harness.driver, which takes a simple lock, samples host pressure and writes the files the scorer needs),
or your own. Every input comes from the environment the driver exports (RATE_*, KIT_REPO, MAP_BIN, the switches;
docs/harness.md lists each variable, its meaning and its default), never from a scratch path.

    series switches            the declared switches against this environment: `export`/`unset` lines on
                               stdout (rc 0) or one REFUSE line per difference (rc 5)
    series plan                the episode's plan as JSON (scenario, seat, steps, start save, template)
    series start OUT           OUT/run.json `start`: the identity read at launch; rc 6 and REFUSE lines when
                               a value differs from the expectation file (the run must not start)
    series watch OUT           in the background for the whole run: the game's identity when it appears
                               (witness_start.json) and at the trajectory's end row (witness_end.json), the
                               hold check of the game's display (RATE_HOLD_CHECK), then the final save through the
                               game's menu (RATE_UI) with a readback of the save's header date (final_save.json);
                               never signals a process
    series end OUT             run.json `end` (the same reads again) and `validity`: valid only when every end
                               value equals its start value and every declared expectation, and the scorer's
                               attempt_reasons (protected directory, resource rule, run files that do not read)
                               find none; each failure is a reason code, never a default, and a reading of the
                               attempt's own run files that fails is one too (`end_failed:<error>`); an input of the
                               step that does not read (the expectation or the schedule run.json names, this host's
                               identity) is a refusal and nothing is written; the ledger entry carries the same
                               validity.
                               Written once, for a played-out attempt only (score.played_out): refused (rc 6,
                               `end_ended`) when run.json has its validity, result.json exists, the ledger entry
                               has ended or carries an end's fields, the ledger has no entry for it, or a
                               withdrawal or cancellation record is there; `end_not_played` when nothing records
                               that its game was played out (it is withdrawn instead). A run.json that does not
                               read, with the attempt's open ledger entry: the end marker, then the entry ended,
                               invalid (`file_unreadable:run.json`, `file_absent:run.json`), counted
    series result OUT          OUT/result.json: the run's one summary (the scorer's row, validity, final
                               save, protected directory, lock window, post steps); a series attempt's is sealed,
                               and refused (rc 6, nothing written or printed but the refusal) before `series end`
                               (`result_before_end`) or when run.json does not read (`result_run_json_unreadable`)
    series declare             an expectation file read from what is staged now (to review and freeze)
    series slot HOST RUN_ID    (a series, RATE_SERIES) the schedule's next attempt for HOST: rc 0 and the attempt
                               as JSON, or rc 7 and REFUSE: the run id is not that slot's, an earlier slot has
                               not started (the hosts alternate), or the series has stopped
    series run ...             the reference driver: every step above, in order, for one attempt
                               (`series run --help`)
    series withdraw OUT        (a series) the operator's record that OUT's attempt was not played out (no end row in
                               its trajectory, no `done`, no end.json of its own end): its host was
                               withdrawn (operator_withdrawn.txt; --cancelled: the series was cancelled,
                               operator_stopped.txt), and its ledger entry closed: ended, invalid, not an attempt.
                               Only once its driver and its game have exited, and only for an attempt not played
                               out (refused otherwise, rc 6: `withdraw_alive`, `withdraw_ended`, `withdraw_played`:
                               a played-out attempt is ended by `series end` by hand, then `series close`)
    series close OUT           (a series) an attempt whose `series end` wrote run.json's validity and whose end marker
                               is missing: the ledger entry from that validity when it was not written, result.json
                               when absent, then the marker (rate_run.done), so the scorer counts it ended; refused
                               when `series end` has not run, when it is withdrawn, or while its driver or game lives
    series publish RUN DEST [--host-name NAME ...]
                               a publishable copy of run directory RUN in the new directory DEST: host names,
                               configured paths, the agent CLI's account and rate-limit fields and session ids
                               replaced (ck3bench.harness.publish); rc 5 and nothing written when one survives

Every step but `run` also takes `--config FILE` (the driver's host configuration: a TOML file with one [env] table of
strings; a variable already in the environment wins), so `series declare` reads the same host as `series run`.

The expectation may declare `game_speed` (the driver passes --game-speed, which aligns the checks)
and `aligned_checks`; `end` holds the trajectory to them (ck3bench.harness.score.trajectory_validity: each step's
speed read-back, each check's date; 1..check_days days off is flagged and stays valid). A SERIES (the driver's
--series, the scorer's schedule) numbers its attempts, alternates the hosts, keeps a LEDGER of each attempt's start
and validity (RATE_LEDGER, default RATE_RUNS/<series>.ledger; the other host's entries are copied in by hand: they
carry no outcome) and stops at the valid target or the attempt cap. A series run's result.json is SEALED: no outcome
is written or printed until the scorer (`python -m ck3bench.harness score`) reads the stopped series.

Absence is a state: a value nobody could read is `{"unread": why}` and makes the run invalid
(`witness_absent:<key>` or `unread:<key>`), never `agreed`.
"""
from __future__ import annotations

import contextlib
import datetime as dt
import hashlib
import json
import os
import re
import socket
import struct
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Optional

from ck3bench.env import durable
from ck3bench.harness import game_identity, prompt
from ck3bench.harness import score as scorer  # the one reading of a run's success; result.json and the table share it

SCHEMA = "ck3bench.series.run/1"
EXPECT_SCHEMA = "ck3bench.series.expect/1"
RESULT_SCHEMA = "ck3bench.series.result/1"
FULL_HOLD_S = 600
#: the switches the series driver exports to its seat launcher; the defaults when nothing is declared. RECORD, VIEW
#: and CLEANMAP default to off: those are the values the reference driver honours (driver.UNHONOURED), so an
#: expectation that declares none of them runs; a driver with screen recording declares RECORD=1 and its VIEW
KIT_SWITCHES = {"TURN_MODE": "fresh", "NOTES": "on", "SEAT_PROMPT": "default", "RECORD": "0", "VIEW": "",
                "CLEANMAP": "0"}
BRIDGE_PREFIX = "CK3_BRIDGE_"
#: the environ keys read from the game's and the toolkit server's /proc/<pid>/environ (those keys only)
WITNESS_KEYS = re.compile(r"^(CK3_INSTANCE_ID|CK3_BRIDGE_[A-Z0-9_]+|CK3MAP_BIN|CK3AGENT_BRIDGE|CK3_NOTES|"
                          r"CK3BENCH_TOOLKIT_WHEEL_SHA256)=")
SAFE_VALUE = re.compile(r"^[A-Za-z0-9._:/@+=-]*$")
#: the runner's end reasons that are the harness's, not the seat's: the run is invalid. seat_stopped / seat_hung are
#: the seat's own behaviour and stay valid failures unless the scorer's resource rule (ck3bench.harness.score) voids
#: them; seat_exited cannot be told from a CLI crash and is invalid.
RUNNER_INVALID = ("rate_limited", "env_unreachable", "setup_failed", "runner_error", "stopped_by_signal",
                  "no_init_event", "surface_mismatch", "wall_budget", "seat_exited")
#: env truncations that are a technical failure, not the horizon (ck3bench errors.TRUNCATION_REASONS minus horizon)
TECHNICAL_TRUNCATIONS = ("game_exited", "bridge_unavailable", "clock_stalled", "reward_unreadable",
                         "script_log_closed", "line_dynasty_unread")
UI_CAP_S = 420           # inputs only this long after the end row (the env holds the game FULL_HOLD 600 s)
#: the game's own menu path to a named save: (what, the UI driver's args, wait). The click points are for a
#: 1920x1080 game window (docs/harness.md, "Requirements of the hooks"); RATE_UI receives them as given
SAVE_INPUTS = (("menu button", ("click", "1372", "1062"), 3), ("Save Game", ("click", "374", "458"), 3),
               ("name field", ("click", "960", "278"), 3), ("select all", ("key", "ctrl+a"), 1),
               ("type name", ("type", "{name}"), 1), ("Save", ("click", "1046", "872"), 10),
               ("Resume", ("click", "374", "370"), 3))


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def E(k: str, default: str = "") -> str:
    return os.environ.get(k, default)


def sha256_file(p: str | Path) -> dict:
    p = Path(p)
    if not str(p) or not p.is_file():
        return {"path": str(p), "unread": "no such file"}
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return {"path": str(p), "sha256": h.hexdigest()}


def build_id(path: str | Path) -> dict:
    """The ELF NT_GNU_BUILD_ID of a binary (program headers' PT_NOTE segments); never a guess."""
    try:
        with open(path, "rb") as f:
            hdr = f.read(64)
            if hdr[:4] != b"\x7fELF" or hdr[4] != 2:
                return {"unread": "not a 64-bit ELF"}
            end = "<" if hdr[5] == 1 else ">"
            phoff, = struct.unpack_from(end + "Q", hdr, 32)
            phentsize, phnum = struct.unpack_from(end + "HH", hdr, 54)
            f.seek(phoff)
            ph = f.read(phentsize * phnum)
            for i in range(phnum):
                p_type, = struct.unpack_from(end + "I", ph, i * phentsize)
                if p_type != 4:          # PT_NOTE
                    continue
                off, = struct.unpack_from(end + "Q", ph, i * phentsize + 8)
                size, = struct.unpack_from(end + "Q", ph, i * phentsize + 32)
                f.seek(off)
                notes = f.read(size)
                j = 0
                while j + 12 <= len(notes):
                    nsz, dsz, typ = struct.unpack_from(end + "III", notes, j)
                    name = notes[j + 12:j + 12 + nsz]
                    d0 = j + 12 + ((nsz + 3) & ~3)
                    if typ == 3 and name.rstrip(b"\0") == b"GNU":
                        return {"build_id": notes[d0:d0 + dsz].hex()}
                    j = d0 + ((dsz + 3) & ~3)
        return {"unread": "no NT_GNU_BUILD_ID note"}
    except OSError as e:
        return {"unread": f"{type(e).__name__}: {e}"}


def git_state(repo: str) -> dict:
    try:
        c = subprocess.run(["git", "-C", repo, "rev-parse", "HEAD"], capture_output=True, text=True, timeout=30)
        s = subprocess.run(["git", "-C", repo, "status", "--porcelain"], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as e:
        return {"unread": f"{type(e).__name__}: {e}"}
    if c.returncode != 0 or s.returncode != 0:
        return {"unread": (c.stderr or s.stderr).strip()[:200]}
    lines = [x for x in s.stdout.splitlines() if x.strip()]
    return {"commit": c.stdout.strip(), "dirty": bool(lines), "dirty_paths": lines[:10]}


#: what a --policy run records for the agent CLI's version: it starts no agent CLI (RATE_POLICY is set)
NO_AGENT_CLI = "not_applicable: a --policy run starts no agent CLI"


def claude_version() -> dict:
    """The agent CLI's version, or not applicable for a --policy run (RATE_POLICY: at `series start` the
    environment's, after it the start's own record, which end_reading puts in place)."""
    if E("RATE_POLICY"):
        return {"not_applicable": NO_AGENT_CLI}
    exe = E("RATE_CLAUDE_BIN", "claude")
    try:
        r = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as e:
        return {"unread": f"{type(e).__name__}: {e}"}
    return {"version": r.stdout.strip()} if r.returncode == 0 and r.stdout.strip() else \
        {"unread": f"rc {r.returncode}: {r.stderr.strip()[:200]}"}


def wheel_identity() -> dict:
    whl = E("RATE_WHEEL")
    out = sha256_file(whl)
    if "unread" in out:
        return out
    try:
        from ck3bench.env import toolkit_id
        c = toolkit_id.compare(whl)
    except Exception as e:  # noqa: BLE001
        return {**out, "compare": {"unread": f"{type(e).__name__}: {e}"}}
    return {**out, "compare": {k: c[k] for k in ("verdict", "members", "identical", "differ", "missing",
                                                   "extra_installed", "wheel_members_sha256")}}


def _scenario():
    """The task as the run plays it: RATE_GAME_SPEED declared in place of its own speed, which aligns a wake
    task's checks (ck3bench cli._scenario, the env play reader the runner calls)."""
    from ck3bench.env.scenario import resolve, scenario_from_dict
    sc = resolve(E("RATE_SCENARIO"))
    sp = E("RATE_GAME_SPEED")
    if not sp:
        return sc
    d = dict(sc.to_dict(), game_speed=int(sp))
    if "wake" in d:
        d["wake"] = dict(d["wake"], aligned_checks=True)
    return scenario_from_dict(d)


def plan() -> dict:
    sc = _scenario()
    if len(sc.seats) != 1:
        raise SystemExit(f"REFUSE: scenario {sc.id} has {len(sc.seats)} seats; a series run plays one")
    tm, sp = E("TURN_MODE", "fresh"), E("SEAT_PROMPT", "default")
    if tm != "fresh" or sp != "default":
        raise SystemExit(f"REFUSE: TURN_MODE={tm} SEAT_PROMPT={sp}: the harness plays fresh turns with its one "
                         "seat prompt (TURN_MODE=fresh, SEAT_PROMPT=default)")
    tpl = prompt.TEMPLATE_PATH
    return {"scenario": sc.id, "seat": sc.seats[0].name, "character_id": sc.seats[0].character_id,
            "n_steps": sc.n_steps, "start_save": sc.start_save, "start_save_sha256_declared": sc.start_save_sha256,
            "start_date": sc.start_date, "end_date": sc.end_date, "prompt_template": str(tpl),
            "game_speed": sc.game_speed, "aligned_checks": bool(sc.wake is not None and sc.wake.aligned),
            "goal": sc.goal, "target_title": sc.target_title}


def rendered_prompt(p: dict) -> dict:
    """The seat prompt rendered exactly as the runner will (the one render_prompt), hashed."""
    try:
        text = prompt.render_prompt(_scenario(), p["n_steps"], 4096,
                                    Path(p["prompt_template"]).read_text(encoding="utf-8"))
    except (SystemExit, Exception) as e:  # noqa: BLE001
        return {"unread": f"{type(e).__name__}: {e}"}
    return {"template": Path(p["prompt_template"]).name, "sha256": hashlib.sha256(text.encode()).hexdigest()}


def switches_in_env() -> dict:
    out = {k: v for k, v in os.environ.items() if k.startswith(BRIDGE_PREFIX)}
    for k in KIT_SWITCHES:
        out[k] = os.environ.get(k)
    out["FULL_HOLD"] = os.environ.get("FULL_HOLD")
    return dict(sorted(out.items()))


# ── the expectation file ───────────────────────────────────────────────────
#: what the expectation's `hosts` table may set per host: the agent CLI version (each host runs its own binary)
HOST_KEYS = ("claude_version",)
#: the timings a series holds (the reference driver's flags; `series declare` writes them, the driver refuses another)
TIMING_KEYS = ("turn_timeout_s", "step_wait_s", "wall_budget_s", "wall_s")
#: the variable `series declare` reads for each timing
TIMING_ENV = {"turn_timeout_s": "RATE_TURN_TIMEOUT_S", "step_wait_s": "RATE_STEP_WAIT_S",
              "wall_budget_s": "RATE_WALL_BUDGET_S", "wall_s": "RATE_WALL_S"}
#: where a check run's run.json has each identity value `series declare` could not read (its placeholder says so)
FROM_CHECK = {"git_commit": "start.git.commit", "bridge_so_sha256": "start.bridge_so.sha256",
              "wheel_sha256": "start.wheel.sha256", "wheel_members_sha256": "start.wheel.compare.wheel_members_sha256",
              "map_sha256": "start.map.sha256", "game_build_id": "start.game_build_id.build_id",
              "claude_version": "start.claude_version.version", "start_save_sha256": "start.start_save.sha256",
              "prompt_sha256": "start.prompt.sha256", "model": "the model id (RATE_MODEL)"}


def _placeholder(v: Any) -> bool:
    return isinstance(v, str) and ("<" in v or "FREEZE" in v)


def load_expect() -> Optional[dict]:
    """The expectation file RATE_EXPECT names, with this host's `hosts` entry laid over it. A placeholder left in it
    (`series declare` writes `<FREEZE: ...>` for the values a check run reads) refuses an attempt
    (`expect_not_frozen`); a check run (RATE_KIND check) holds every other value and leaves a top-level placeholder
    out, listed in `_unfrozen` (its run.json records them): reading those values is what a check is for."""
    p = E("RATE_EXPECT")
    if not p:
        return None
    try:
        d = scorer.read_bounded_json(p)
    except scorer.TooDeep as e:
        raise SystemExit(f"REFUSE expect_schema: {p} is not {EXPECT_SCHEMA} ({e})") from None
    if not isinstance(d, dict):
        raise SystemExit(f"REFUSE expect_schema: {p} is not {EXPECT_SCHEMA} (a JSON {type(d).__name__}, not an object)")
    if d.get("schema") != EXPECT_SCHEMA:
        raise SystemExit(f"REFUSE expect_schema: {p} is not {EXPECT_SCHEMA} ({d.get('schema')!r})")
    host = E("RATE_HOST")
    hosts = d.get("hosts") or {}
    bad_hosts = ([f"{h}.{k}" for h, t in hosts.items() if isinstance(t, dict) for k in t if k not in HOST_KEYS]
                 + [str(h) for h, t in hosts.items() if not isinstance(t, dict)]) if isinstance(hosts, dict) else ["hosts"]
    if bad_hosts:
        raise SystemExit(f"REFUSE expect_hosts_key: {p}: the `hosts` table holds {list(HOST_KEYS)} per host and nothing "
                         f"else ({bad_hosts[:8]}); every other value is one for the whole series")
    merged = {k: v for k, v in d.items() if k not in ("hosts", "notes_for_reader")}
    for k, v in (hosts.get(host) or {}).items():
        merged[k] = v
    unfrozen = []
    if E("RATE_KIND", "rate") == "check":
        unfrozen = sorted(k for k, v in merged.items() if _placeholder(v))
        merged = {k: v for k, v in merged.items() if k not in unfrozen}
    bad = [k for k, v in _flat(merged) if _placeholder(v)]
    if bad:
        raise SystemExit(f"REFUSE expect_not_frozen: {p} still has placeholders at {bad[:8]}")
    merged["_unfrozen"] = unfrozen
    merged["_path"] = os.path.abspath(p)       # recorded absolute: a step run by hand from another directory reads it
    merged["_sha256"] = hashlib.sha256(Path(p).read_bytes()).hexdigest()
    return merged


def _flat(d: Any, pre: str = ""):
    if isinstance(d, dict):
        for k, v in d.items():
            yield from _flat(v, f"{pre}{k}.")
    else:
        yield pre.rstrip("."), d


def cmd_switches() -> int:
    """Refuse on any switch that differs from the declaration; else print the exports for the driver."""
    exp = load_expect()
    sw = (exp or {}).get("switches") or {}
    kit = {**KIT_SWITCHES, **((exp or {}).get("kit") or {})}
    refuse, lines = [], []
    for k, v in sw.items():
        if not k.startswith(BRIDGE_PREFIX):
            refuse.append(f"REFUSE switch_not_bridge: {k} in `switches` (bridge switches only; kit switches in `kit`)")
            continue
        have = os.environ.get(k)
        if v is not None and not SAFE_VALUE.match(str(v)):
            refuse.append(f"REFUSE switch_value: {k}={v!r} has characters this kit does not export")
        elif have is not None and (v is None or have != str(v)):
            refuse.append(f"REFUSE switch_differs: {k} is {have!r} here, declared {v!r}")
        else:
            lines.append(f"export {k}={v}" if v is not None else f"unset {k}")
    if exp is not None:
        for k in os.environ:
            if k.startswith(BRIDGE_PREFIX) and k not in sw:
                refuse.append(f"REFUSE switch_undeclared: {k}={os.environ[k]!r} is set and the expectation does "
                              "not declare it")
    for k, v in kit.items():
        if k not in KIT_SWITCHES:
            refuse.append(f"REFUSE kit_switch_unknown: {k}")
            continue
        have = os.environ.get(k)
        if not SAFE_VALUE.match(str(v)):
            refuse.append(f"REFUSE switch_value: {k}={v!r}")
        elif have is not None and have != str(v):
            refuse.append(f"REFUSE switch_differs: {k} is {have!r} here, declared {v!r}")
        else:
            lines.append(f"export {k}={v}")
    hold = (exp or {}).get("full_hold_s", FULL_HOLD_S)
    if hold != FULL_HOLD_S:
        refuse.append(f"REFUSE full_hold: the expectation declares {hold}, a series run holds {FULL_HOLD_S}")
    if os.environ.get("FULL_HOLD") not in (None, str(FULL_HOLD_S)):
        refuse.append(f"REFUSE switch_differs: FULL_HOLD is {os.environ['FULL_HOLD']!r} here, a series run holds {FULL_HOLD_S}")
    lines.append(f"export FULL_HOLD={FULL_HOLD_S}")
    if refuse:
        print("\n".join(refuse))
        return 5
    print("\n".join(lines))
    return 0


# ── start ──────────────────────────────────────────────────────────────────
def static_identity(p: dict) -> dict:
    saves = E("RATE_SAVES")
    ss = sha256_file(Path(saves) / p["start_save"]) if p.get("start_save") else \
        {"none": "the scenario names no start save (a fake-backend scenario)"}
    return {"git": git_state(E("KIT_REPO")), "bridge_so": sha256_file(E("RATE_SO")), "wheel": wheel_identity(),
            "map": sha256_file(E("MAP_BIN")), "game_build_id": build_id(E("RATE_GAME_BIN")),
            "claude_version": claude_version(), "host": socket.gethostname(), "start_save": ss,
            "prompt": rendered_prompt(p), "switches": switches_in_env(), "model": E("RATE_MODEL"),
            "full_hold_s": int(E("FULL_HOLD", "0") or 0)}


#: run.json value -> the expectation key it is held to
def _pick(ident: dict) -> dict[str, Any]:
    g, w = ident.get("git") or {}, ident.get("wheel") or {}
    return {"git_commit": g.get("commit"), "bridge_so_sha256": (ident.get("bridge_so") or {}).get("sha256"),
            "wheel_sha256": w.get("sha256"), "wheel_members_sha256": (w.get("compare") or {}).get("wheel_members_sha256"),
            "map_sha256": (ident.get("map") or {}).get("sha256"),
            "game_build_id": (ident.get("game_build_id") or {}).get("build_id"),
            "claude_version": (ident.get("claude_version") or {}).get("version"),
            "start_save_sha256": (ident.get("start_save") or {}).get("sha256"),
            "prompt_sha256": (ident.get("prompt") or {}).get("sha256"), "model": ident.get("model"),
            "full_hold_s": ident.get("full_hold_s")}


def check_static(ident: dict, exp: Optional[dict], kind: str) -> list[str]:
    """Reason codes for a launch identity: unread values, a dirty tree, an install that is not the wheel, and
    every declared expectation that differs."""
    r = []
    for k in ("git", "bridge_so", "map", "game_build_id", "claude_version", "start_save", "prompt", "wheel"):
        if isinstance(ident.get(k), dict) and "unread" in ident[k]:
            r.append(f"unread:{k}")
    if (ident.get("git") or {}).get("dirty"):
        r.append("tree_dirty")
    cmp_ = (ident.get("wheel") or {}).get("compare") or {}
    if "unread" in cmp_:
        r.append("unread:wheel_compare")
    elif cmp_ and cmp_.get("verdict") != "identical":
        r.append("wheel_install_differs")
    if exp is None:
        r.append("expectation_not_declared")
        return r
    # an attempt plays only an expectation declared for attempts; a check may play one (it reads what that
    # expectation will freeze) or one declared for checks
    ek = exp.get("kind", "rate")
    if ek != kind and not (kind == "check" and ek == "rate"):
        r.append(f"expect_kind:{ek}!={kind}")
    # a --policy run starts no agent CLI: a declared CLI version (a seated expectation's, or one declared without
    # RATE_POLICY) is not held, on any host
    na = {"claude_version"} if "not_applicable" in (ident.get("claude_version") or {}) else set()
    for k, have in _pick(ident).items():
        if k in exp and k not in na and exp[k] != have:
            r.append(f"expect_differs:{k}")
    return r


def check_task(p: dict, exp: Optional[dict]) -> list[str]:
    """The task the run plays (the scenario id, the speed and aligned checks the run declares) against the declared
    ones."""
    want = exp.get("task") if exp else None
    if isinstance(want, str) and want.endswith(".toml"):
        try:                       # a declared task FILE is the task its id names (a test stand-in's case)
            from ck3bench.env.scenario import resolve
            want = resolve(want).id
        except Exception:  # noqa: BLE001
            pass
    r = ["expect_differs:task"] if exp and "task" in exp and want != p.get("scenario") else []
    for k in ("game_speed", "aligned_checks"):
        if exp and k in exp and exp[k] != p.get(k):
            r.append(f"expect_differs:{k}")
    return r


# ── a series: the schedule, the ledger, the slot ───────────────────────────
def _series() -> Optional[dict]:
    p = E("RATE_SERIES")
    if not p:
        return None
    d = scorer.load_series(p)
    d["_path"], d["_sha256"] = os.path.abspath(p), hashlib.sha256(Path(p).read_bytes()).hexdigest()
    return d


def ledger_dir(series: dict, out: Optional[Path] = None) -> Path:
    """RATE_LEDGER, else RATE_RUNS/<series>.ledger, else (for a step given a run directory OUT) the ledger beside
    OUT, where the reference driver keeps both; none of them is a refusal (never the working directory)."""
    if E("RATE_LEDGER"):
        return Path(E("RATE_LEDGER"))
    if E("RATE_RUNS"):
        return Path(E("RATE_RUNS")) / f"{series['series']}.ledger"
    if out is not None:
        return Path(out).resolve().parent / f"{series['series']}.ledger"
    raise SystemExit("REFUSE series_ledger: neither RATE_LEDGER nor RATE_RUNS is set")


class LedgerUnreadable(ValueError):
    """A ledger entry that does not read: every step that reads the ledger refuses (`series_ledger_unreadable`)."""


def ledger(series: dict, out_dir: Optional[Path] = None) -> list[dict]:
    """Every attempt entry in the ledger (attempt-NN.json): {attempt, host, run_id, started_utc, ended, valid,
    not_attempt}; validity only, never an outcome. An entry that does not read (scorer.ledger_files: a file name that is
    not the canonical one, a field of another type) is never skipped, which would free its slot: LedgerUnreadable,
    which each step prints as its REFUSE line."""
    out = []
    for f, e, why in scorer.ledger_files(ledger_dir(series, out_dir)):
        if e is None:
            raise LedgerUnreadable(f"series_ledger_unreadable: {f.name} ({why}) in {f.parent}: restore it from the "
                                   "other host's copy of the ledger")
        out.append(e)
    return out


def slot(series: dict, host: str, run_id: str) -> tuple[Optional[dict], list[str]]:
    """The attempt this host plays next, or the refusals: the stop reached, the run id not the slot's, an earlier
    slot not started (the hosts alternate), the task not the schedule's."""
    ents = ledger(series)
    st = scorer.stop_state(series, ents)
    used = {e["attempt"] for e in ents}
    a = 1
    while a in used or scorer.slot_host(series, a) != host:
        a += 1
        if a > 10 * series["attempt_cap"]:
            return None, [f"REFUSE series_host_unknown: {host!r} is not in the schedule's pattern {series['pattern']}"]
    r = []
    if st["stop"]:
        r.append(f"REFUSE series_stopped: the {st['stop']} is reached ({st['valid']} valid, {st['attempts']} "
                 "attempts); no attempt starts")
    want = scorer.run_id_for(series, a)
    if run_id != want:
        r.append(f"REFUSE series_slot: this host's next attempt is {a}, run id {want!r}, not {run_id!r}")
    missing = [b for b in range(1, a) if b not in used]
    if missing:
        r.append(f"REFUSE series_alternation: attempt {a} on {host} waits for attempt {missing[0]} on "
                 f"{scorer.slot_host(series, missing[0])} to start (ledger {ledger_dir(series)})")
    if E("RATE_SCENARIO") and series["task"] != E("RATE_SCENARIO"):
        r.append(f"REFUSE series_task: the schedule's task is {series['task']}, this run plays {E('RATE_SCENARIO')}")
    return {"attempt": a, "host": host, "run_id": want, **st}, r


#: the runner flag carrying each timing a series holds; `wall_s` is the driver's own limit on the runner process,
#: which no runner flag carries (a driver of your own holds it as the reference driver does)
TIMING_FLAGS = {"turn_timeout_s": "--turn-timeout-s", "step_wait_s": "--step-wait-s",
                "wall_budget_s": "--wall-budget-s"}


def timing_reasons(flags: Any, exp: dict) -> list[str]:
    """A series attempt's runner flags (RATE_RUNNER_FLAGS, recorded in run.json) against the expectation's
    `timings`: whatever driver started the run, the runner plays the declared ones. A flag that is missing or does not
    read is unwitnessed, never agreed."""
    t = exp.get("timings")
    if not isinstance(t, dict):
        return ["expect_timings_absent"]
    parsed = runner_timings(flags)
    out = []
    for k in TIMING_FLAGS:
        have = parsed.get(k)
        if have is None:
            out.append(f"witness_absent:runner_flag:{k}")
        elif not isinstance(t.get(k), (int, float)) or isinstance(t.get(k), bool) or float(t[k]) != have:
            out.append(f"expect_differs:timings.{k}")
    return out


def runner_timings(flags: Any) -> dict[str, Optional[float]]:
    """The timings the runner plays with these flags, read by the runner's own parser (ck3bench.harness.runner.parser,
    as `run-seat` reads them: the last of a repeated flag, an abbreviation, `--flag=value`); None for one the flags
    do not give, and for all when the flags do not parse."""
    import contextlib
    import io
    from ck3bench.harness import runner
    p = runner.parser()
    p.set_defaults(**{k: None for k in TIMING_FLAGS})
    try:
        with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
            ns, _ = p.parse_known_args(["--out", "-", "--scenario", "-",
                                        *[str(x) for x in (flags if isinstance(flags, list) else [])]])
    except SystemExit:
        return {k: None for k in TIMING_FLAGS}
    return {k: getattr(ns, k) for k in TIMING_FLAGS}


def series_expectation_reasons(series: dict, exp: Optional[dict]) -> list[str]:
    """One expectation per series: an attempt plays the file (its sha256) every ledger entry started with; an entry
    that records none is unwitnessed, never agreed."""
    if exp is None:
        return ["series_expectation_absent"]
    shas = {e.get("expectation_sha256") for e in ledger(series)}
    if None in shas:
        return ["series_expectation_unwitnessed"]
    return ["series_expectation_differs"] if shas - {exp["_sha256"]} else []


def write_ledger(series: dict, entry: dict, out_dir: Optional[Path] = None) -> None:
    d = ledger_dir(series, out_dir)
    d.mkdir(parents=True, exist_ok=True)
    f = d / scorer.ledger_name(entry["attempt"])
    cur, why = scorer.read_object(f)
    if why:
        raise LedgerUnreadable(f"series_ledger_unreadable: {f.name} ({why}) in {d}: restore it from the other host's "
                               "copy of the ledger")
    _write(f, json.dumps({**(cur or {}), **entry}, indent=1))


def cmd_slot(host: str, run_id: str) -> int:
    ser = _series()
    if ser is None:
        print("REFUSE series_absent: RATE_SERIES is not set")
        return 7
    try:
        got, refuse = slot(ser, host, run_id)
    except LedgerUnreadable as e:
        got, refuse = None, [f"REFUSE {e}"]
    if refuse:
        print("\n".join(refuse))
        return 7
    print(json.dumps(got))
    return 0


def cmd_plan() -> int:
    print(json.dumps(plan()))
    return 0


#: What the refusal line says beside a start reason (cmd_start), filled by the check that found it
REFUSE_DETAIL: dict = {}


def clock_reasons(ser: dict, host: Optional[str], at: str) -> list[str]:
    """`start_clock_behind` when this host's clock reads more than scorer.LEDGER_CLOCK_SKEW_S before the latest start
    this host label has in the ledger (its own clock set back, after a power loss for example, or that earlier start
    stamped by a clock that was ahead): refused in the first second, as the scorer would refuse the series for it
    (`series_ledger_order`). The refusal line prints both times (REFUSE_DETAIL). A ledger that does not read is the
    slot's refusal, not this one."""
    t = scorer._utc(at)
    try:
        mine = [(scorer._utc(e.get("started_utc")), e) for e in ledger(ser) if e.get("host") == host]
    except LedgerUnreadable:
        return []
    last, ent = max(((x, e) for x, e in mine if x is not None), default=(None, None), key=lambda p: p[0] or 0)
    if t is None or last is None or not t < last - scorer.LEDGER_CLOCK_SKEW_S:
        return []
    until = dt.datetime.fromtimestamp(last - scorer.LEDGER_CLOCK_SKEW_S, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    REFUSE_DETAIL["start_clock_behind"] = (
        f"this host's clock reads {at}; host {host}'s latest start in the ledger (attempt {ent.get('attempt')}) is "
        f"stamped {ent.get('started_utc')}, more than {scorer.LEDGER_CLOCK_SKEW_S} s later. If this clock is behind, "
        f"set it right and start again; if it is right, that stamp came from a clock that was ahead: wait until "
        f"{until}, or correct that entry's started_utc by hand and record it as a deviation (docs/harness.md, "
        "\"Further refusal codes\")")
    return ["start_clock_behind"]


def cmd_start(out: Path) -> int:
    REFUSE_DETAIL.clear()
    exp = load_expect()
    p = plan()
    ident = static_identity(p)
    kind = E("RATE_KIND", "rate")
    reasons = check_static(ident, exp, kind) + check_task(p, exp)
    rj = {"schema": SCHEMA, "run_id": E("RATE_RUN_ID"), "kind": kind, "host": E("RATE_HOST"),
          "launch_utc": E("RATE_LAUNCH_UTC") or now(), "plan": p,
          "runner_flags": json.loads(E("RATE_RUNNER_FLAGS", "[]")),
          "expectation": ({"path": exp["_path"], "sha256": exp["_sha256"], "unfrozen": exp["_unfrozen"]}
                          if exp else "not_declared"),
          "concurrency": {"paired": E("RATE_PAIRED") or None,
                          "source": "declared by the operator (--paired); not observed from this host"
                          if E("RATE_PAIRED") else "none declared (--paired not given)"},
          "start": {"at": now(), **ident}, "start_reasons": reasons,
          # what the steps after this one read as the attempt's own declarations, whoever runs them (as_started)
          "declared": {k: (os.path.abspath(E(k)) if k == "RATE_EXPECT" and E(k) else E(k)) for k in DECLARED_AT_START},
          # whether the records reach the disk (false only under the kit's test-only switch, NO_FSYNC_FOR_TESTS)
          "records_flushed": durable.sync_wanted()}
    ser = _series()
    if ser is not None:
        got, refuse = slot(ser, E("RATE_HOST"), E("RATE_RUN_ID"))
        if refuse:
            reasons += [x.split(" ", 2)[1].rstrip(":") for x in refuse]
        reasons += series_expectation_reasons(ser, exp)
        reasons += clock_reasons(ser, E("RATE_HOST"), rj["start"]["at"])
        if exp is not None:
            reasons += timing_reasons(rj["runner_flags"], exp)
        if not E("RATE_PROTECTED_DIR"):
            reasons.append("protected_dir_not_configured")
        rj["series"] = {"id": ser["series"], "schedule": ser["_path"], "schedule_sha256": ser["_sha256"],
                        "attempt": (got or {}).get("attempt"), "valid_target": ser["valid_target"],
                        "attempt_cap": ser["attempt_cap"], "pattern": ser["pattern"]}
    _write(out / "run.json", json.dumps(rj, indent=1))
    hard = [x for x in reasons if x != "expectation_not_declared" or ser is not None]
    if hard:
        print("\n".join(f"REFUSE {x}" + (f": {REFUSE_DETAIL[x]}" if x in REFUSE_DETAIL else "") for x in hard))
        return 6
    if ser is not None:
        write_ledger(ser, {"attempt": rj["series"]["attempt"], "host": E("RATE_HOST"), "run_id": E("RATE_RUN_ID"),
                           "started_utc": rj["start"]["at"], "ended": False, "expectation_sha256": exp["_sha256"]})
    print(f"run.json start written; reasons: {reasons or 'none'}")
    return 0


# ── the run's witness: the game process, the end window, the final save ───
def _environ_keys(pid: int) -> dict:
    try:
        raw = Path(f"/proc/{pid}/environ").read_bytes().split(b"\0")
    except OSError as e:
        return {"unread": f"{type(e).__name__}"}
    out = {}
    for x in raw:
        s = x.decode("utf-8", "replace")
        if WITNESS_KEYS.match(s):
            k, _, v = s.partition("=")
            out[k] = v
    return dict(sorted(out.items()))


def game_witness(pid: int) -> dict:
    w: dict[str, Any] = {"at": now(), "pid": pid}
    try:
        exe = os.readlink(f"/proc/{pid}/exe")
    except OSError as e:
        return {**w, "unread": f"the process is gone ({type(e).__name__})"}
    w["exe"] = exe
    w["build_id"] = build_id(f"/proc/{pid}/exe")
    try:
        maps = Path(f"/proc/{pid}/maps").read_text()
    except OSError as e:
        maps = ""
        w["maps_unread"] = type(e).__name__
    so = sorted({ln.split(None, 5)[5] for ln in maps.splitlines()
                 if len(ln.split(None, 5)) == 6 and "libck3_bridge" in ln.split(None, 5)[5]})
    # the file the game mapped, read through the process (map_files survives a replaced path)
    w["bridge_mapped"] = [{"path": s, **{k: v for k, v in sha256_file(s.replace(" (deleted)", "")).items()
                                         if k != "path"}} for s in so]
    w["environ"] = _environ_keys(pid)
    w["servers"] = toolkit_servers(w["environ"].get("CK3_INSTANCE_ID", ""))
    w["bridge_log_held"] = held_log(pid, bridge_log_path()[0])
    return w


def held_log(pid: int, path: Optional[str]) -> dict:
    """The bridge log as the game holds it: the device and inode of every open file of the process whose path is
    PATH (the bridge opens a fresh log at load and keeps it open), read through /proc/PID/fd. `held` empty is a
    reading (the game holds no file at that path); `unread` says why nobody could look."""
    if not path:
        return {"unread": "no bridge log path"}
    want = os.path.realpath(path)
    fd_dir = Path(f"/proc/{pid}/fd")
    held = []
    try:
        fds = list(fd_dir.iterdir())
    except OSError as e:
        return {"path": want, "unread": f"{type(e).__name__}: {fd_dir}"}
    for fd in fds:
        try:
            if os.readlink(fd) != want:
                continue
            st = os.stat(fd)
        except OSError:
            continue
        if (st.st_dev, st.st_ino) not in [(h["dev"], h["ino"]) for h in held]:
            held.append({"dev": st.st_dev, "ino": st.st_ino})
    return {"path": want, "held": held}


def toolkit_servers(inst: str) -> list[dict]:
    """The toolkit server processes of this instance: argv carries `serve`, environ CK3_INSTANCE_ID is ours, the
    exe is a python. Read-only; recorded with their python's real path and the switch keys they inherited."""
    out = []
    for p in Path("/proc").iterdir():
        if not p.name.isdigit():
            continue
        try:
            argv = (p / "cmdline").read_bytes().split(b"\0")
            exe = os.readlink(p / "exe")
        except OSError:
            continue
        if b"serve" not in argv or "python" not in Path(exe).name:
            continue
        env = _environ_keys(int(p.name))
        if inst and env.get("CK3_INSTANCE_ID") != inst:
            continue
        if not any(a.endswith(b"ck3agent") or b"ck3agent" in a for a in argv):
            continue
        out.append({"pid": int(p.name), "exe": exe, "argv": [a.decode("utf-8", "replace") for a in argv[:3]],
                    "environ": env})
    return out


def _end_row(out: Path) -> Optional[dict]:
    tp = out / "trajectory.jsonl"
    if not tp.is_file():
        return None
    last = None
    for line in tp.read_text(encoding="utf-8", errors="replace").splitlines():
        if '"kind": "end"' in line or '"kind":"end"' in line:
            try:
                return json.loads(line)
            except ValueError:
                return None
        if '"kind": "step"' in line:
            last = line
    return None


def _last_step_date(out: Path) -> Optional[str]:
    d = None
    tp = out / "trajectory.jsonl"
    if tp.is_file():
        for line in tp.read_text(encoding="utf-8", errors="replace").splitlines():
            if '"kind": "step"' in line:
                try:
                    d = json.loads(line).get("game_date")
                except ValueError:
                    pass
    return d


def _ours(out: Path) -> tuple[Optional[int], str]:
    """The run's game pid (game_identity.owned), or (None, `REFUSED why`)."""
    try:
        pid, why = game_identity.owned(out)
    except Exception as e:  # noqa: BLE001
        return None, f"REFUSED {type(e).__name__}: {e}"[:200]
    return (pid, "") if pid is not None else (None, f"REFUSED {why}")


def _log(out: Path, msg: str) -> None:
    with open(out / "watch.log", "a", encoding="utf-8") as f:
        f.write(f"{now()} {msg}\n")


def date_key(s: Optional[str]) -> Optional[tuple]:
    try:
        return tuple(int(x) for x in str(s).split("."))
    except ValueError:
        return None


def save_header_date(p: Path) -> dict:
    """The save's own header date (CK3 writes `meta_date=Y.M.D` in its plaintext head)."""
    try:
        with open(p, "rb") as f:
            head = f.read(1 << 16)
    except OSError as e:
        return {"unread": f"{type(e).__name__}: {e}"}
    m = re.search(rb"meta_date=([0-9]+\.[0-9]+\.[0-9]+)", head)
    return {"meta_date": m.group(1).decode()} if m else {"unread": "no meta_date in the save's first 64 KiB"}


def final_save(out: Path, pid: int, t_end: float, t_end_wall: float, end_date: Optional[str]) -> dict:
    name = f"{E('RATE_RUN_ID')}_final"
    saves = Path(E("RATE_GAME_SAVES"))
    rec: dict[str, Any] = {"name": name, "dir": str(saves), "inputs": []}
    if not os.path.exists(f"/proc/{pid}"):
        return {**rec, "saved": False, "reason": "no final save: the game is gone at the end row"}
    if not E("RATE_UI"):
        return {**rec, "saved": False, "reason": "no final save: no UI driver configured (RATE_UI)"}
    ui = [E("RATE_UI")]
    for what, args, wait in SAVE_INPUTS:
        if time.monotonic() - t_end > UI_CAP_S:
            return {**rec, "saved": False, "reason": f"no final save: time cap {UI_CAP_S} s reached before {what!r}"}
        a = [x.format(name=name) for x in args]
        r = subprocess.run(["bash", *ui, *a], capture_output=True, text=True, timeout=60)
        rec["inputs"].append({"at": now(), "what": what, "args": a, "rc": r.returncode, "out": r.stdout.strip()[-200:]})
        if r.returncode != 0:
            return {**rec, "saved": False, "reason": f"no final save: input {what!r} failed rc {r.returncode}: "
                                                     f"{(r.stdout + r.stderr).strip()[-200:]}"}
        time.sleep(wait)
    f = saves / f"{name}.ck3"
    t, size = time.monotonic(), -1
    while time.monotonic() - t < 120:            # the file, written after the end row, its size settled
        if f.is_file() and f.stat().st_mtime >= t_end_wall - 5:
            s = f.stat().st_size
            if s == size and s > 0:
                break
            size = s
        time.sleep(2)
    if not (f.is_file() and f.stat().st_mtime >= t_end_wall - 5):
        return {**rec, "saved": False, "reason": f"no final save: no {f.name} written after the end row in {saves} "
                                                 "within 120 s of the last input"}
    hd = save_header_date(f)
    rec.update(saved=True, file=sha256_file(f), bytes=f.stat().st_size, header=hd, end_row_date=end_date)
    want, got = date_key(end_date), date_key(hd.get("meta_date"))
    if want is None or got is None:
        rec["readback"] = "unwitnessed: " + ("the trajectory has no step date" if want is None else hd.get("unread", ""))
    else:
        rec["readback"] = "agreed" if want == got else f"DIFFERS: header {hd['meta_date']} vs last step {end_date}"
    try:
        dst = out / "final_save"
        dst.mkdir(exist_ok=True)
        (dst / f.name).write_bytes(f.read_bytes())
        rec["copied_to"] = str(dst / f.name)
    except OSError as e:
        rec["copy_unread"] = f"{type(e).__name__}: {e}"
    return rec


def hold_framecheck(out: Path) -> dict:
    """A check of the game's display in the hold (RATE_HOLD_CHECK: a command, RUN_DIR appended, that
    prints one JSON object), recorded as framecheck_hold.json; none configured is said, never `ok`."""
    cmd = E("RATE_HOLD_CHECK")
    if not cmd:
        return {"checked": False, "reason": "no hold check configured (RATE_HOLD_CHECK)"}
    r = subprocess.run([*cmd.split(), str(out)], capture_output=True, text=True, timeout=300)
    try:
        got = json.loads(r.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return {"checked": False, "reason": f"the hold check printed no JSON (rc {r.returncode}): "
                                            f"{(r.stdout + r.stderr).strip()[-200:]}"}
    return got if isinstance(got, dict) else {"checked": False, "reason": f"the hold check printed {got!r}"[:200]}


def cmd_watch(out: Path) -> int:
    t0 = time.monotonic()
    limit = float(E("RATE_WATCH_S", "36000"))
    pid = None
    why = ""
    while time.monotonic() - t0 < limit and not (out / "done").exists():
        if (out / "trajectory.jsonl").is_file():
            pid, why = _ours(out)
            if pid is not None:
                break
        time.sleep(3)
    if pid is None:
        msg = f"no game process witnessed ({why or 'no trajectory header'})"
        _log(out, msg)
        _write(out / "witness_start.json", json.dumps({"unread": msg}, indent=1))
        _write(out / "framecheck_hold.json", json.dumps({"checked": False, "reason": f"no hold check: {msg}"},
                                                             indent=1))
        _write(out / "final_save.json", json.dumps({"saved": False, "reason": f"no final save: {msg}"}, indent=1))
        return 0
    _write(out / "witness_start.json", json.dumps(game_witness(pid), indent=1))
    _log(out, f"game pid {pid} witnessed at start")
    end = None
    while time.monotonic() - t0 < limit:
        end = _end_row(out)
        if end is not None or (out / "done").exists() or not os.path.exists(f"/proc/{pid}"):
            break
        time.sleep(2)
    t_end, t_end_wall = time.monotonic(), time.time()
    if end is None:
        why = "the run ended" if (out / "done").exists() else "the game exited" if not os.path.exists(f"/proc/{pid}") \
            else "the watch limit was reached"
        _write(out / "witness_end.json", json.dumps({"unread": f"no end row: {why}"}, indent=1))
        _write(out / "framecheck_hold.json", json.dumps(
            {"checked": False, "reason": f"no hold check: no end row ({why}); the hold follows an end row"}, indent=1))
        _write(out / "final_save.json", json.dumps(
            {"saved": False, "reason": f"no final save: no end row ({why}); env play holds the game only after an "
                                       "end row"}, indent=1))
        _log(out, f"no end row: {why}")
        return 0
    _log(out, f"end row: {json.dumps(end)[:200]}")
    _write(out / "witness_end.json", json.dumps(game_witness(pid), indent=1))
    try:
        fc = hold_framecheck(out)
    except Exception as e:  # noqa: BLE001
        fc = {"checked": False, "reason": f"{type(e).__name__}: {e}"}
    _write(out / "framecheck_hold.json", json.dumps(fc, indent=1))
    try:
        fs = final_save(out, pid, t_end, t_end_wall, _last_step_date(out))
    except Exception as e:  # noqa: BLE001
        fs = {"saved": False, "reason": f"no final save: {type(e).__name__}: {e}"}
    _write(out / "final_save.json", json.dumps(fs, indent=1))
    _log(out, f"final save: {fs.get('saved')} {fs.get('reason') or fs.get('readback')}")
    if os.path.exists(f"/proc/{pid}"):
        _write(out / "witness_end2.json", json.dumps(game_witness(pid), indent=1))
    return 0


# ── end ────────────────────────────────────────────────────────────────────
def _read(p: Path) -> Optional[dict]:
    """A JSON-object file, or None when it is absent or does not read as one object (scorer.read_object)."""
    return scorer.read_object(p)[0]


#: TEST ONLY: when this variable is "1", the records' flushes to the disk are skipped (the rename stays), so the kit's
#: own tests, which write thousands of records, run in minutes; `series start` records whether it was set (run.json
#: `records_flushed`) and the scorer refuses a series with an attempt started under it (docs/harness.md,
#: "Environment variables"). Declared once, in ck3bench.env.durable.
NO_FSYNC_FOR_TESTS = durable.NO_FSYNC_FOR_TESTS


def _write(p: Path, text: str) -> None:
    """Every record a series step writes (run.json, a ledger entry, result.json, the operator's records, the end
    marker, the watcher's witness files): written whole or not at all (durable.write_whole, which the driver's `done`
    and end marker and the runner's end.json go through too:
    a temporary file, flushed to the disk, renamed over it, and the directory flushed), so a step stopped between two
    writes leaves each record as it was before or after, never half written."""
    durable.write_whole(p, text)


def witness_reasons(rj: dict, ws: Optional[dict], we: Optional[dict], exp: Optional[dict]) -> list[str]:
    r = []
    so = ((rj.get("start") or {}).get("bridge_so") or {}).get("sha256")
    for tag, w in (("start", ws), ("end", we)):
        if not isinstance(w, dict) or "unread" in w:
            r.append(f"witness_absent:game_{tag}")
            continue
        mapped = w.get("bridge_mapped") or []
        if len(mapped) != 1 or mapped[0].get("sha256") is None:
            r.append(f"witness_absent:bridge_mapped_{tag}" if not mapped or mapped[0].get("sha256") is None
                     else f"bridge_mapped_many_{tag}")
        elif mapped[0]["sha256"] != so:
            r.append(f"bridge_mapped_differs_{tag}")
        bid = (w.get("build_id") or {}).get("build_id")
        want = ((rj.get("start") or {}).get("game_build_id") or {}).get("build_id")
        if bid is None:
            r.append(f"witness_absent:game_build_id_{tag}")
        elif bid != want:
            r.append(f"game_build_id_differs_{tag}")
        env = w.get("environ") or {}
        for k, v in ((exp or {}).get("switches") or {}).items():
            if env.get(k) != (None if v is None else str(v)):
                r.append(f"game_switch_differs:{k}_{tag}")
        servers = w.get("servers") or []
        if not servers:
            r.append(f"witness_absent:toolkit_server_{tag}")
        for s in servers:
            for k, v in ((exp or {}).get("switches") or {}).items():
                if (s.get("environ") or {}).get(k) != (None if v is None else str(v)):
                    r.append(f"server_switch_differs:{k}_{tag}")
    if isinstance(ws, dict) and isinstance(we, dict) and "unread" not in ws and "unread" not in we \
            and ws.get("pid") != we.get("pid"):
        r.append("game_pid_changed")
    return r


def header_reasons(out: Path, rj: dict, exp: Optional[dict]) -> tuple[list[str], dict]:
    r, seen = [], {}
    tp = out / "trajectory.jsonl"
    head = None
    if tp.is_file():
        with open(tp, encoding="utf-8") as f:
            first = f.readline()
        try:
            head = json.loads(first)
        except ValueError:
            head = None
    if not isinstance(head, dict) or head.get("kind") != "header":
        return ["no_trajectory_header"], seen
    prov = head.get("provenance") or {}
    seen["header_toolkit"] = prov.get("toolkit")
    tid = prov.get("toolkit_identity")
    seen["header_toolkit_identity"] = tid
    want = (((rj.get("start") or {}).get("wheel") or {}).get("compare") or {}).get("wheel_members_sha256")
    if not isinstance(tid, dict) or not tid.get("installed_members_sha256"):
        r.append("witness_absent:header_toolkit_identity")
    elif tid["installed_members_sha256"] != want:
        r.append("header_toolkit_identity_differs")
    elif tid.get("wheel_sha256_declared") != ((rj.get("start") or {}).get("wheel") or {}).get("sha256"):
        r.append("header_wheel_sha_differs")
    ss = prov.get("start_save_sha256")
    seen["header_start_save_sha256"] = ss
    if ss != ((rj.get("start") or {}).get("start_save") or {}).get("sha256"):
        r.append("header_start_save_differs")
    seat = (rj.get("plan") or {}).get("seat")
    notes = ((prov.get("notes") or {}).get(seat) or {}) if seat else {}
    seen["notes_version"] = notes.get("version")
    if exp and "notes_version" in exp and notes.get("version") != exp["notes_version"]:
        r.append("expect_differs:notes_version")
    return r, seen


#: what `series end` records for a session witness a --policy run has none of (it starts no agent session)
NO_SESSION = "not_applicable: a --policy run starts no agent session"


def session_witnesses(out: Path, ej: Optional[dict], exp: Optional[dict],
                      policy: Optional[bool] = None) -> tuple[list[str], dict]:
    """(reasons, seen) for the agent session's two witnesses: the tool listing a real MCP client read
    (`tool_listing.json`) and the session's init event (`session_init.json`), each held to the expectation when it
    declares a value. A --policy run (the runner's end.json says turn_mode `policy`) starts no agent session and
    writes neither: their absence is that state (NO_SESSION), not a missing witness, unless the expectation declares
    a value for one, which is then held and reads absent. `policy` is the attempt's arm as `series start` recorded it
    (declared_at_start), so an end.json the stop left unwritten does not turn a --policy run into a seated one."""
    reasons, seen = [], {}
    if policy is None:
        policy = isinstance(ej, dict) and ej.get("turn_mode") == "policy"
    tl = sha256_file(out / "tool_listing.json")
    seen["tool_listing_sha256"] = tl.get("sha256")
    if "sha256" not in tl:
        if policy and not (exp and "tool_listing_sha256" in exp):
            seen["tool_listing_sha256"] = NO_SESSION
        else:
            reasons.append("witness_absent:tool_listing")
    elif exp and "tool_listing_sha256" in exp and tl["sha256"] != exp["tool_listing_sha256"]:
        reasons.append("expect_differs:tool_listing_sha256")
    si = _read(out / "session_init.json")
    seen["seat_model_reported"] = (si or {}).get("model")
    if si is None:
        if policy and not (exp and "seat_model_reported" in exp):
            seen["seat_model_reported"] = NO_SESSION
        else:
            reasons.append("witness_absent:session_init")
    elif exp and "seat_model_reported" in exp and si.get("model") != exp["seat_model_reported"]:
        reasons.append("expect_differs:seat_model_reported")
    return reasons, seen


#: the recovery of a played-out attempt with no validity, and of one with its validity and no end marker (the
#: refusals, the reference driver and docs/harness.md, "A driver stopped mid-attempt", all say these)
END_BY_HAND = "`series end {out}` by hand (once what it names is put right), then `series close {out}`"
CLOSE_BY_HAND = "`series close {out}`"
WITHDRAW_BY_HAND = "`series withdraw {out}` (`--cancelled` when the series is cancelled)"
END_DAMAGED_BY_HAND = ("`series end {out}` (its run.json does not read: the step records the attempt ended and invalid "
                       "in its ledger entry)")


def end_refusal(out: Path, rj: dict) -> Optional[tuple[str, str]]:
    """(code, why) when `series end` must not run on OUT, or None: it writes an attempt's validity once, and only for
    an attempt played out (scorer.played_out). `end_ended`: its validity already in run.json (a driver that stopped
    after it: `series close`), result.json written, a withdrawal or cancellation record, a series
    attempt with no ledger entry (its start was never entered), a ledger entry that has ended or carries an end's
    fields (`ended_utc`, `valid`, `reasons`). `end_not_played`: nothing records that its game was played out (its
    runner and driver were stopped from outside: `series withdraw`)."""
    if isinstance(rj.get("validity"), dict):
        return "end_ended", ("run.json has its validity already (written once; a driver that stopped after it: "
                             f"{CLOSE_BY_HAND.format(out=out.name)})")
    if (out / "result.json").is_file():
        return "end_ended", "result.json is written and run.json has no validity (`series result` runs after the end)"
    rec = [f for f in (scorer.WITHDRAWN_FILE, scorer.CANCELLED_FILE) if (out / f).is_file()]
    if rec:
        return "end_ended", f"the attempt is withdrawn ({rec[0]})"
    ser = rj.get("series")
    if isinstance(ser, dict) and isinstance(ser.get("attempt"), int):
        try:
            sched = scorer.load_series(ser["schedule"])
        except (OSError, ValueError, KeyError, TypeError) as e:
            return "end_schedule_unreadable", (f"the schedule run.json names ({ser.get('schedule')}) does not read "
                                               f"({type(e).__name__}); put the frozen schedule back at that path")
        cur = next((e for e in ledger(sched, out) if e.get("attempt") == ser["attempt"]), None)
        if cur is None:
            return "end_ended", ("the ledger has no entry for its attempt (its start was never entered: a run "
                                 "directory left so is moved to refused-starts/ by hand)")
        if cur.get("ended"):
            return "end_ended", f"its ledger entry has ended (standing {scorer.standing(cur)})"
        done = [k for k in ("ended_utc", "valid", "reasons") if k in cur]
        if done:
            return "end_ended", f"its ledger entry carries an end's fields ({', '.join(done)}) and is not ended"
    if scorer.played_out(out) is None:
        return "end_not_played", ("nothing records that its game was played out (no `done`, no end.json other than "
                                  f"`{scorer.SIGNAL_END}`): it was stopped from outside and is withdrawn, "
                                  f"{WITHDRAW_BY_HAND.format(out=out.name)}")
    return None


def damaged_entry(out: Path) -> tuple[Optional[Path], Optional[dict], str]:
    """For a run directory whose run.json does not read: (its ledger directory, its open ledger entry, "") when the
    directory is a series attempt by its name (scorer.parse_run_id) and the ledger beside it (ledger_dir) holds an
    entry for that run id and attempt that has not ended; else (ledger, None, why)."""
    rid = scorer.parse_run_id(out.name)
    if rid is None:
        return None, None, "its name is no series attempt's run id"
    try:
        led = ledger_dir({"series": rid["series"]}, out)
    except SystemExit as e:
        return None, None, str(e.code)
    try:
        cur = next((e for e in ledger({"series": rid["series"]}, out) if e.get("attempt") == rid["attempt"]), None)
    except LedgerUnreadable as e:
        return led, None, str(e)
    if cur is None or cur.get("run_id") != out.name:
        return led, None, f"the ledger {led} has no entry for {out.name}"
    if cur.get("ended") or any(k in cur for k in ("ended_utc", "valid", "reasons")):
        return led, None, f"its ledger entry has ended (standing {scorer.standing(cur)})"
    return led, cur, ""


def end_damaged(out: Path, state: str) -> int:
    """`series end` on an attempt whose run.json does not read (absent, cut, NUL-filled, a field of another type) after
    `series start` entered it in the ledger: the ledger entry is the attempt's record. It is ended, invalid, counted,
    its one reason the damage (`file_unreadable:run.json` or `file_absent:run.json`), after the end marker is
    written, and nothing else (run.json is left as found; `series result` refuses it). The scorer reads the attempt from that entry
    (score.series_gate)."""
    led, cur, why = damaged_entry(out)
    if cur is None:
        print(f"REFUSE end_no_start: {out.name}: run.json {state.replace('unreadable: ', 'does not read: ')}, and "
              f"{why}: `series start` entered no attempt to end; nothing written")
        return 6
    reason = scorer.run_json_damage(state)
    rid = scorer.parse_run_id(out.name)
    # the marker first: a step stopped between the two leaves the entry open, and this step runs again
    _write(out / scorer.END_MARKER, f"written by `series end` {now()}: run.json {state}; the ledger entry is the "
                                    "attempt's record\n")
    write_ledger({"series": rid["series"]}, {"attempt": cur["attempt"], "host": cur.get("host"), "run_id": out.name,
                                             "ended": True, "valid": False, "reasons": [reason], "ended_utc": now(),
                                             "not_attempt": False}, out)
    print(json.dumps({"valid": False, "reasons": [reason], "recorded_in": scorer.ledger_name(cur["attempt"])}))
    return 0


def cmd_end(out: Path) -> int:
    out = Path(out).resolve()          # `.` or `./` names the run directory by its own name
    rj, state = scorer.read_run_json(out)
    if rj is None:
        return end_damaged(out, state)
    try:
        refusal = end_refusal(out, rj)
    except LedgerUnreadable as e:
        print(f"REFUSE {e}; nothing written")
        return 6
    if refusal:
        print(f"REFUSE {refusal[0]}: {out.name}: {refusal[1]}; nothing written")
        return 6
    if not isinstance(rj.get("declared"), dict):
        print(f"REFUSE end_declared_absent: {out.name}: run.json has no `declared` block (`series start` records the "
              "attempt's declarations there), so this step cannot read the attempt as it started; nothing written")
        return 6
    try:
        end, reasons = end_reading(out, rj)
    except EndInputUnreadable as e:
        print(f"REFUSE {e.code}: {out.name}: {e.why}; nothing written; once it is put right, "
              f"{END_BY_HAND.format(out=out.name)}")
        return 6
    except Exception as e:  # noqa: BLE001
        # every reading of the attempt's own run files that fails is a reason: a played-out attempt never stays
        # without its validity
        end = {"at": now(), "unread": f"{type(e).__name__}: {str(e)[:200]}"}
        reasons = list(rj.get("start_reasons") or [])
        reasons.append(f"end_failed:{type(e).__name__}")
        try:
            reasons += [x for x in scorer.attempt_reasons(out) if x not in reasons]
        except Exception:  # noqa: BLE001
            pass
    rj["end"] = end
    rj["validity"] = {"valid": not reasons, "reasons": reasons}
    # run.json first, then the ledger entry from it: a step stopped between the two leaves run.json's validity and an
    # open entry, which `series close` writes from run.json (never a second reading)
    try:
        _write(out / "run.json", json.dumps(rj, indent=1, default=str))
    except OSError as e:
        print(f"REFUSE end_unwritten: {out.name}: run.json is not written ({type(e).__name__}: {e}); nothing "
              f"written; once the disk is put right, {END_BY_HAND.format(out=out.name)}")
        return 6
    ser = rj.get("series")
    if isinstance(ser, dict) and isinstance(ser.get("attempt"), int):
        try:
            ledger_end(scorer.load_series(ser["schedule"]), rj, out)
        except (OSError, ValueError) as e:
            print(f"REFUSE end_ledger: the ledger entry is not written ({type(e).__name__}: {e}); "
                  f"{CLOSE_BY_HAND.format(out=out.name)} writes it from run.json's validity")
            print(json.dumps(rj["validity"]))
            return 6
    print(json.dumps(rj["validity"]))
    return 0


#: the variables `series start` reads as the attempt's declarations (the arm, the model, the task and its speed, the
#: expectation, the host label and the run's kind), recorded in run.json `declared`; every step after it reads them
#: from there (as_started), never from the environment it runs in: the reference driver sets them for its own
#: steps, the host configuration does not, so a step run by hand reads the same attempt the driver would. Declared
#: once, in the scorer (score.DECLARED_AT_START), whose run.json reading holds every member present and text.
DECLARED_AT_START = scorer.DECLARED_AT_START


def declared_at_start(rj: dict) -> dict:
    """run.json `declared` (DECLARED_AT_START). A run.json that reads holds every member as text
    (score.run_json_problem); one with no `declared` block is refused by `series end` before it reads
    (`end_declared_absent`)."""
    return {k: rj["declared"][k] for k in DECLARED_AT_START}


@contextlib.contextmanager
def as_started(rj: dict):
    """The environment with the attempt's own declarations (declared_at_start) in place of this step's, restored
    after: an empty one is unset."""
    keep = {k: os.environ.get(k) for k in DECLARED_AT_START}
    try:
        for k, v in declared_at_start(rj).items():
            if v:
                os.environ[k] = v
            else:
                os.environ.pop(k, None)
        yield
    finally:
        for k, v in keep.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


class EndInputUnreadable(Exception):
    """An input of `series end` itself that does not read: the expectation run.json `declared` names (or one that is
    not the file `series start` read), or this host's identity. Not the attempt's own run files: cmd_end refuses (the
    code) and writes nothing."""

    def __init__(self, code: str, why: str):
        super().__init__(why)
        self.code, self.why = code, why


def end_reading(out: Path, rj: dict) -> tuple[dict, list[str]]:
    """run.json's `end` and the validity reasons of `series end` (the module statement), read with the attempt's own
    declarations (as_started); raises EndInputUnreadable for an input of the step that does not read, which cmd_end
    refuses, and on the attempt's own run files it cannot read, which cmd_end records as `end_failed:<error>`."""
    with as_started(rj):
        return _end_reading(out, rj)


def _end_reading(out: Path, rj: dict) -> tuple[dict, list[str]]:
    try:
        exp = load_expect() if E("RATE_EXPECT") else None
    except (Exception, SystemExit) as e:  # noqa: BLE001  (whatever the reading of this input raises is a refusal)
        why = str(e.code) if isinstance(e, SystemExit) else f"{type(e).__name__}"
        raise EndInputUnreadable("end_expect_unreadable", f"the expectation run.json names ({E('RATE_EXPECT')}) does "
                                 f"not read ({why[:200]}); put the frozen expectation back at that path") from None
    started = rj.get("expectation")
    if isinstance(started, dict) and (exp or {}).get("_sha256") != started.get("sha256"):
        raise EndInputUnreadable("end_expect_differs", f"the expectation at {E('RATE_EXPECT')} is not the file "
                                 f"`series start` read (sha256 {(exp or {}).get('_sha256')}, run.json records "
                                 f"{started.get('sha256')}); put the frozen expectation back at that path")
    plan = rj["plan"]
    try:
        ident = static_identity(plan)
    except Exception as e:  # noqa: BLE001
        raise EndInputUnreadable("end_host_unreadable", f"this host's identity does not read ({type(e).__name__}: "
                                 f"{str(e)[:200]})") from None
    reasons = list(rj.get("start_reasons") or [])
    a, b = _pick(rj["start"]), _pick(ident)
    for k in a:
        if a[k] != b[k]:
            reasons.append(f"start_end_differs:{k}")
    if rj["start"].get("switches") != ident.get("switches"):
        reasons.append("start_end_differs:switches")
    if (ident.get("git") or {}).get("dirty"):
        reasons.append("tree_dirty_at_end")
    cmp_ = (ident.get("wheel") or {}).get("compare") or {}
    if cmp_.get("verdict") != "identical":
        reasons.append("wheel_install_differs_at_end")
    ws, we = _read(out / "witness_start.json"), _read(out / "witness_end.json")
    reasons += witness_reasons(rj, ws, we, exp)
    hr, seen = header_reasons(out, rj, exp)
    reasons += hr
    # the rendered prompt the runner wrote, against the one rendered at launch
    pr = sha256_file(out / "seat_prompt.rendered.md")
    seen["prompt_sha256_run"] = pr.get("sha256")
    if pr.get("sha256") != (rj["start"].get("prompt") or {}).get("sha256"):
        reasons.append("prompt_differs_from_launch" if "sha256" in pr else "witness_absent:seat_prompt_rendered")
    ej = _read(out / "end.json")
    sr, ss = session_witnesses(out, ej, exp, policy=bool(E("RATE_POLICY")))
    reasons += sr
    seen.update(ss)
    if ej is None:
        reasons.append("witness_absent:end_json")
    else:
        er = str(ej.get("end_reason") or "")
        seen["runner_end_reason"] = er
        if er.split(":")[0] in RUNNER_INVALID:
            reasons.append(f"runner_end:{er.split(':')[0]}")
        if ej.get("final") is False:
            # the runner recorded its end and was stopped before it closed the session and the game and wrote its
            # end record whole: the attempt counts (score.played_out), on evidence that is not complete
            reasons.append("witness_absent:end_json_final")
        nw = scorer._d(ej.get("notes_witness")).get("verdict")
        seen["notes_witness"] = nw
        if nw != "agreed":
            reasons.append(f"notes_witness:{nw}")
    tp = scorer.trajectory_path(out)
    recs = scorer._rows(tp) if tp is not None else []
    tv = scorer.trajectory_validity(recs, (rj.get("plan") or {}).get("seat"),
                                    (exp or {}).get("game_speed"), (exp or {}).get("aligned_checks"))
    if tp is not None:
        reasons += tv["reasons"]
    seen["clock"] = {k: tv.get(k) for k in ("checks", "aligned", "check_days", "flagged", "speed_differs_steps",
                                            "speed_absent_steps", "check_dates_absent_steps")}
    bw = bridge_title_witness((rj.get("plan") or {}).get("target_title"),
                              witnesses=[w for w in (ws, we, _read(out / "witness_end2.json")) if isinstance(w, dict)])
    _write(out / "bridge_title_witness.json", json.dumps(bw, indent=1))
    # the protected directory (the driver digests it before the run and again before this step) and the resource
    # rule: the scorer's own attempt_reasons, so run.json, the ledger (the slot's stopping rule) and the score read
    # one validity
    seen["owner_tree"] = scorer.owner_tree(out)["verdict"]
    settle_resource_window(out)
    reasons += [x for x in scorer.attempt_reasons(out) if x not in reasons]
    row = scorer.read_run(out)
    if row.get("truncation_reason") in TECHNICAL_TRUNCATIONS:
        reasons.append(f"technical_truncation:{row['truncation_reason']}")
    if row.get("success") is None:
        reasons.append("unread:success")
    t0 = scorer._utc(rj.get("launch_utc"))
    return ({"at": now(), **ident, "seen": seen,
             "wall_s": None if t0 is None else round(time.time() - t0, 1)}, reasons)


def ledger_end(series: dict, rj: dict, out: Optional[Path] = None) -> None:
    """The attempt's ledger entry ended with run.json's validity (as `series end` wrote it): one record, two places."""
    v = rj["validity"]
    write_ledger(series, {"attempt": rj["series"]["attempt"], "host": rj.get("host"), "run_id": rj.get("run_id"),
                          "ended": True, "valid": v.get("valid") is True, "reasons": list(v.get("reasons") or []),
                          "ended_utc": now(), "not_attempt": False}, out)


def settle_resource_window(out: Path, cap_s: float = scorer.WINDOW_PAD_S + 5) -> float:
    """Wait until the resource rule's stop window, padded, has passed (at most CAP_S seconds), so the host sampler
    has written every row the rule reads and the verdict `series end` records is the one the scorer reads later. The
    seconds waited."""
    win = (scorer.resource_rule(out) or {}).get("window")
    if not win:
        return 0.0
    wait = win[1] + scorer.WINDOW_PAD_S + 2 - time.time()
    if wait <= 0:
        return 0.0
    wait = min(wait, cap_s)
    time.sleep(wait)
    return wait


#: the bridge log's lines for a create_title command (dispatch_router's Action line, the intent queue's completion)
_BRIDGE_ACTION = re.compile(r"\] Action: create_title params: (\{.*\})\s*$")
_BRIDGE_DONE = re.compile(r"create_title intent COMPLETED: actor=(\d+) title_id=(\d+)")


def bridge_log_path() -> tuple[Optional[str], str]:
    if E("RATE_BRIDGE_LOG"):
        return E("RATE_BRIDGE_LOG"), "RATE_BRIDGE_LOG"
    try:
        from ck3agent.server import instance
        return instance.resolve(E("RATE_INSTANCE") or None).bridge_log, "ck3agent instance paths for RATE_INSTANCE"
    except Exception as e:  # noqa: BLE001
        return None, f"no bridge log path: {type(e).__name__}: {e}"


def log_held_by_game(path: str, witnesses: list[dict]) -> dict:
    """Whether the file at PATH now is the bridge log THIS run's game held: its device and inode against those the
    game witnesses recorded (`bridge_log_held`). `held` true, or false with a reason code: `no_game_witness` (no
    witness saw this run's game, so nobody could look), `log_not_held` (the game was seen and held no file at the
    path), `log_replaced` (the file at the path is not the one the game held: another game's, or a later one)."""
    try:
        st = os.stat(path)
    except OSError as e:
        return {"held": False, "reason_code": "log_unreadable", "why": f"{type(e).__name__}: {e}"}
    seen = [w for w in witnesses if "unread" not in w and isinstance(w.get("bridge_log_held"), dict)]
    looked = [w["bridge_log_held"] for w in seen if "held" in w["bridge_log_held"]]
    if not looked:
        why = "no game witness of this run recorded the files it held" if not seen else \
            "; ".join(str(w["bridge_log_held"].get("unread")) for w in seen)
        return {"held": False, "reason_code": "no_game_witness", "why": why}
    held = [(h["dev"], h["ino"]) for x in looked for h in x["held"]]
    pids = sorted({w.get("pid") for w in seen})
    if (st.st_dev, st.st_ino) in held:
        return {"held": True, "game_pid": pids, "inode": st.st_ino}
    if not held:
        return {"held": False, "reason_code": "log_not_held", "game_pid": pids,
                "why": f"this run's game held no file at {path}"}
    return {"held": False, "reason_code": "log_replaced", "game_pid": pids, "inode": st.st_ino,
            "held_inodes": sorted({i for _, i in held}),
            "why": "the file at the path is not the one this run's game held"}


def bridge_title_witness(title: Optional[str], path: Optional[str] = None,
                         witnesses: Optional[list[dict]] = None) -> dict:
    """The bridge log's create_title commands for the target title and their completions (who sent a mint is the
    trajectory's; whether a command reached the game at all is the bridge's). Absent is a state, never `none`.

    WITNESSES: the run's game witnesses. With them the log is read only when it is the file this run's game held
    (log_held_by_game); otherwise the state is `unwitnessed` with its reason code and no command is read, since the
    file may be another run's. Without them (None: a caller that names a log it vouches for) the log is read as
    given and `held_by_game` says it was not checked."""
    if not title:
        return {"state": "absent", "why": "the task names no target title"}
    src = "given"
    if path is None:
        path, src = bridge_log_path()
    if not path:
        return {"state": "absent", "why": src}
    if not Path(path).is_file():
        return {"state": "absent", "path": path, "path_from": src, "why": f"no file at {path}"}
    own: dict = {"checked": False, "why": "the caller named the log"} if witnesses is None else \
        {"checked": True, **log_held_by_game(path, witnesses)}
    if witnesses is not None and not own["held"]:
        return {"state": "unwitnessed", "path": path, "path_from": src, "reason_code": own["reason_code"],
                "held_by_game": own}
    try:
        lines = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as e:
        return {"state": "absent", "path": path, "why": f"{type(e).__name__}: {e}"}
    cmds, done = [], []
    actors = set()
    for n, ln in enumerate(lines, 1):
        m = _BRIDGE_ACTION.search(ln)
        if m:
            try:
                prm = (json.loads(m.group(1)).get("params") or {})
            except ValueError:
                continue
            if prm.get("title_key") == title:
                cmds.append({"line": n, "actor_id": prm.get("actor_id")})
                actors.add(prm.get("actor_id"))
            continue
        m = _BRIDGE_DONE.search(ln)
        if m and int(m.group(1)) in actors:
            done.append({"line": n, "actor_id": int(m.group(1)), "title_id": int(m.group(2))})
    return {"state": "read", "path": path, "path_from": src, "held_by_game": own, "lines": len(lines), "commands": cmds,
            "completed": done}


# ── result ─────────────────────────────────────────────────────────────────
_TSV_UNESCAPE = {"\\": "\\", "t": "\t", "n": "\n", "r": "\r"}


def _tsv_note(v: str) -> str:
    """A post.tsv note as the driver's post step escaped it (backslash, tab, newline, carriage return)."""
    return re.sub(r"\\(.)", lambda m: _TSV_UNESCAPE.get(m.group(1), "\\" + m.group(1)), v)


def _tsv(p: Path) -> list[dict]:
    try:
        rows = [dict(zip(("step", "rc", "at", "note"), ln.split("\t", 3)))
                for ln in p.read_text(encoding="utf-8", errors="replace").splitlines() if ln]
    except OSError:
        return []
    for r in rows:
        if "note" in r:
            r["note"] = _tsv_note(r["note"])
    return rows


#: a series run's result.json carries none of these (the outcome is read once, after the stop, by the scorer)
OUTCOME_KEYS = ("success", "success_why", "success_date", "success_step", "termination_reason", "truncation_reason",
                "created", "creator", "counties_last", "last_date", "held_at_horizon", "horizon_reached",
                "end_reason", "end_row", "steps", "steps_from", "reader", "creation_date")
SEALED = "sealed: a series run's outcome is read by the scorer (ck3bench.harness score) once the series has stopped"


def cmd_result(out: Path) -> int:
    """OUT/result.json. Writes and prints an outcome only for a run of no series; a series attempt's (a run.json with
    `series`) is sealed and written only once `series end` has written its validity. A run.json that does not read
    (read_run_json), or one with no `series` in a directory a series names (RATE_SERIES set, or its name a series
    attempt's run id with an entry in the ledger beside it), refuses: nothing is written or printed but the refusal."""
    out = Path(out).resolve()          # `.` or `./` names the run directory by its own name
    rj, state = scorer.read_run_json(out)
    if rj is None:
        print(f"REFUSE result_run_json_unreadable: {out.name}: run.json {state}; nothing written (a series attempt's "
              "record is then its ledger entry, which `series end` writes)")
        return 6
    named = scorer.parse_run_id(out.name)
    if "series" not in rj and (E("RATE_SERIES") or (named is not None and any(
            e.get("run_id") == out.name for e in _ledger_or_empty(named["series"], out)))):
        print(f"REFUSE result_run_json_unreadable: {out.name}: run.json has no `series` and a series names this "
              "directory (RATE_SERIES, or its ledger); nothing written")
        return 6
    if isinstance(rj.get("series"), dict) and not isinstance(rj.get("validity"), dict):
        print(f"REFUSE result_before_end: {out.name}: run.json has no validity (`series result` follows `series end`); "
              "nothing written")
        return 6
    row = scorer.read_run(out)
    own = scorer.owner_tree(out)
    sealed = isinstance(rj.get("series"), dict)
    if sealed:
        row = {k: v for k, v in row.items() if k not in OUTCOME_KEYS}
    res = {"schema": RESULT_SCHEMA, "run_id": rj.get("run_id"), "kind": rj.get("kind"), "host": rj.get("host"),
           "task": scorer._d(rj.get("plan")).get("scenario"), "valid": row.get("valid"),
           "invalid_reasons": row.get("invalid_reasons"),
           "success": SEALED if sealed else row.get("success"),
           "success_why": SEALED if sealed else row.get("success_why"), "row": row,
           **({"series": rj["series"]} if sealed else {}),
           "final_save": _read(out / "final_save.json") or {"saved": False, "reason": "no final save: final_save.json absent"},
           "framecheck_hold": _read(out / "framecheck_hold.json") or {"checked": False,
                                                                       "reason": "framecheck_hold.json absent"},
           "owner_tree": own, "lock": {"taken": E("RATE_LOCK_TAKEN"), "released": "by the series driver, after this file"},
           "post_steps": _tsv(out / "post.tsv"), "written_utc": now()}
    for x in scorer.owner_tree_reasons(own):         # the scorer's row carries it already; said here too
        res["valid"] = False
        if x not in (res["invalid_reasons"] or []):
            res["invalid_reasons"] = list(res["invalid_reasons"] or []) + [x]
    _write(out / "result.json", json.dumps(res, indent=1))
    print(json.dumps({k: res[k] for k in ("run_id", "kind", "valid", "invalid_reasons", "success")}))
    return 0


def _ledger_or_empty(series_id: str, out: Path) -> list[dict]:
    """The entries of the ledger beside OUT (ledger_dir), or [] when there is none or one does not read."""
    try:
        return ledger({"series": series_id}, out)
    except (LedgerUnreadable, SystemExit):
        return []


def alive(out: Path) -> list[str]:
    """What of OUT's run is still running: each game its witnesses recorded that is alive as recorded
    (driver.recorded_games, driver.alive_as), and its driver while it holds the lock driver.log names (the lock's
    holder line is the one the driver wrote and its pid is running). `series withdraw` and `series close` refuse while
    anything is; the record of a stopped driver is then final."""
    from ck3bench.harness import driver
    out_ = [f"game pid {p} ({e})" for p, e in driver.recorded_games(out) if driver.alive_as(p, e)]
    try:
        lines = (out / "driver.log").read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        lines = []
    for ln in lines:
        m = LOCK_LINE.search(ln)
        if not m:
            continue
        try:
            held = (Path(m.group(1)) / "holder").read_text(encoding="utf-8", errors="replace").strip() == m.group(2)
        except OSError:
            held = False
        if held and Path(f"/proc/{m.group(3)}").exists():
            out_.append(f"its driver, pid {m.group(3)}, holding {m.group(1)} (if pid {m.group(3)} is not that "
                        f"driver, the lock is stale: check with `ps -p {m.group(3)} -o args=`, then remove "
                        f"{m.group(1)} by hand)")
    return out_


#: driver.log's lock line (driver.Driver.run): `lock taken: LOCK: ck3bench-series UTC RUN_ID pid PID`; the lock's path
#: is everything before the holder (a path may hold ": "), the holder's fields hold no space
LOCK_LINE = re.compile(r" lock taken: (.+): (ck3bench-series \S+ \S+ pid (\d+))$")


def cmd_withdraw(out: Path, cancelled: bool = False) -> int:
    """An attempt that was not played out (scorer.played_out: its runner and driver were stopped from outside), its
    host withdrawn (or the series cancelled): the operator's record in OUT (scorer.WITHDRAWN_FILE, or with
    `cancelled` scorer.CANCELLED_FILE; written when absent) and the attempt's ledger entry closed: ended, not valid,
    its code, not an attempt (the stopping rule then counts neither it nor its validity, and the host's next slot
    takes its place). The schedule is the one run.json names; nothing is read from the game. Refused (rc 6, nothing
    written): OUT is not a series attempt or has no ledger entry; `series end` has reached it (`withdraw_ended`: it
    stands as its end step wrote it; one whose driver stopped after that step is closed by `series close`); it was
    played out (`withdraw_played`: `series end` by hand, then `series close`); its driver or a game it recorded is
    still running (`withdraw_alive`: stop the driver first, and run this once both have exited); it is withdrawn
    already."""
    out = Path(out).resolve()          # `.` or `./` names the run directory by its own name
    rj, state = scorer.read_run_json(out)
    if rj is None and damaged_entry(out)[1] is not None:
        print(f"REFUSE withdraw_run_json_unreadable: {out.name}: run.json {state} and the ledger holds its open "
              f"entry: the attempt stays counted; `series end {out.name}` records it ended and invalid; nothing written")
        return 6
    rj = rj or {}
    ser = rj.get("series")
    if not isinstance(ser, dict) or not isinstance(ser.get("attempt"), int):
        print(f"REFUSE withdraw: {out}/run.json names no series attempt (it is absent or does not read)")
        return 6
    try:
        s = scorer.load_series(ser["schedule"])
    except (OSError, ValueError, KeyError, TypeError) as e:
        print(f"REFUSE withdraw: the schedule run.json names does not read ({type(e).__name__}: {e})")
        return 6
    a, rid = ser["attempt"], rj.get("run_id")
    try:
        cur = next((e for e in ledger(s, out) if e.get("attempt") == a), None)
    except LedgerUnreadable as e:
        print(f"REFUSE {e}; nothing written")
        return 6
    mine, other = ((scorer.CANCELLED_FILE, scorer.WITHDRAWN_FILE) if cancelled
                   else (scorer.WITHDRAWN_FILE, scorer.CANCELLED_FILE))
    if cur is None:
        print(f"REFUSE withdraw_ended: attempt {a} ({rid}) has no ledger entry (ledger {ledger_dir(s, out)}): its start "
              "was never entered; a run directory left in the slot's place is moved to refused-starts/ by hand "
              "(docs/harness.md, \"A driver stopped mid-attempt\")")
        return 6
    if "validity" in rj or (cur.get("ended") and not cur.get("not_attempt")):
        print(f"REFUSE withdraw_ended: attempt {a} ({rid}) has ended (`series end` wrote its validity; ledger "
              f"standing {scorer.standing(cur)}): an ended attempt stands as its end step wrote it; one whose driver "
              f"stopped after that step is closed by {CLOSE_BY_HAND.format(out=out.name)}")
        return 6
    if (out / other).is_file():
        print(f"REFUSE withdraw: {out.name} holds {other} already: run the same command again (with "
              f"{'--cancelled' if other == scorer.CANCELLED_FILE else 'no --cancelled'})")
        return 6
    if cur.get("ended") and (out / mine).is_file():
        print(f"REFUSE withdraw: attempt {a} ({rid}) is withdrawn already")
        return 6
    played = scorer.played_out(out)
    if played:
        print(f"REFUSE withdraw_played: attempt {a} ({rid}) was played out ({played} records it): a played-out "
              f"attempt is ended, never withdrawn: {END_BY_HAND.format(out=out.name)}; nothing written")
        return 6
    live = alive(out)
    if live:
        print(f"REFUSE withdraw_alive: attempt {a} ({rid}) is still running ({'; '.join(live)}): stop its driver, "
              "wait until its game has exited, then withdraw it; nothing written")
        return 6
    f = out / mine
    if not f.is_file():
        _write(f, f"{'the series cancelled' if cancelled else 'the host withdrawn'} by the operator, {now()}\n")
    code = scorer.CANCELLED if cancelled else scorer.WITHDRAWN
    write_ledger(s, {"attempt": a, "host": rj.get("host"), "run_id": rid, "ended": True,
                     "valid": False, "reasons": [code], "ended_utc": now(), "not_attempt": True}, out)
    print(json.dumps({"attempt": a, "run_id": rid, "ledger": str(ledger_dir(s, out)),
                      "reasons": [code], "not_attempt": True}))
    return 0


def cmd_close(out: Path) -> int:
    """An attempt whose `series end` wrote run.json's validity and whose end marker is missing (its driver stopped
    after that step, or `series end` was run by hand): the ledger entry ended from that validity when `series end`
    stopped before writing it, `series result` when result.json is absent, then the end marker, so the scorer counts
    it ended. Refused (rc 6, nothing written) when the marker, result.json and the ended entry all exist (closed
    already), when `series end` has not run (a played-out attempt: `series end` by hand first; one not played out:
    `series withdraw`), when it is withdrawn, when an ended ledger entry disagrees with run.json's validity, or while
    its driver or a game its witnesses recorded is still running."""
    out = Path(out).resolve()          # `.` or `./` names the run directory by its own name
    rj, state = scorer.read_run_json(out)
    if rj is None and damaged_entry(out)[1] is not None:
        print(f"REFUSE close: {out.name}: run.json {state} and the ledger holds its open entry: `series end "
              f"{out.name}` records it ended and invalid (and writes the end marker); nothing written")
        return 6
    rj = rj or {}
    ser = rj.get("series")
    v = rj.get("validity")
    if not isinstance(ser, dict) or not isinstance(ser.get("attempt"), int):
        print(f"REFUSE close: {out.name}/run.json names no series attempt (it is absent or does not read)")
        return 6
    if not isinstance(v, dict):
        how = (f"it was played out: {END_BY_HAND.format(out=out.name)}" if scorer.played_out(out) else
               f"it was not played out: {WITHDRAW_BY_HAND.format(out=out.name)}")
        print(f"REFUSE close: {out.name}: `series end` has not run (run.json has no validity); {how}")
        return 6
    try:
        s = scorer.load_series(ser["schedule"])
    except (OSError, ValueError, KeyError, TypeError) as e:
        print(f"REFUSE close: the schedule run.json names does not read ({type(e).__name__})")
        return 6
    try:
        cur = next((e for e in ledger(s, out) if e.get("attempt") == ser["attempt"]), None)
    except LedgerUnreadable as e:
        print(f"REFUSE {e}; nothing written")
        return 6
    rec = [f for f in (scorer.WITHDRAWN_FILE, scorer.CANCELLED_FILE) if (out / f).is_file()]
    if cur is None or cur.get("not_attempt") or rec:
        print(f"REFUSE close: attempt {ser['attempt']}: "
              + ("no ledger entry" if cur is None else "it is withdrawn (not an attempt)") + "; nothing written")
        return 6
    marked = (out / scorer.END_MARKER).is_file()
    if cur.get("ended") and marked and (out / "result.json").is_file():
        print(f"REFUSE close: {out.name} has its end marker already")
        return 6
    if cur.get("ended") and (cur.get("valid") is not (v.get("valid") is True)
                             or list(cur.get("reasons") or []) != list(v.get("reasons") or [])):
        print(f"REFUSE close: attempt {ser['attempt']}: the ledger entry (valid={cur.get('valid')}) is not the one "
              f"`series end` wrote beside run.json's validity (valid={v.get('valid')}); nothing written")
        return 6
    live = alive(out)
    if live:
        print(f"REFUSE close: attempt {ser['attempt']} is still running ({'; '.join(live)}); close it once its driver "
              "and its game have exited")
        return 6
    if not cur.get("ended"):
        ledger_end(s, rj, out)
    if not (out / "result.json").is_file():
        cmd_result(out)
    if not marked:
        _write(out / scorer.END_MARKER, f"closed by `series close` {now()}: the end marker after `series end`\n")
    print(json.dumps({"attempt": ser["attempt"], "run_id": rj.get("run_id"), "closed": True,
                      "valid": v.get("valid") is True}))
    return 0


def cmd_declare() -> int:
    """The expectation read from what is staged now. With RATE_POLICY set (a --policy series: no agent CLI, no
    session) it declares no agent CLI version and no session witness; the values a check run reads are
    `<FREEZE: ...>` placeholders, which an attempt refuses and a check run leaves out. An identity value that cannot
    be read here (a file that is missing, a binary that does not answer) is a placeholder too, naming why and where
    a check run's run.json has it. `timings` are the driver's flags a series holds (RATE_TURN_TIMEOUT_S,
    RATE_STEP_WAIT_S, RATE_WALL_BUDGET_S, RATE_WALL_S, else the reference driver's defaults)."""
    p = plan()
    ident = static_identity(p)
    session = {} if E("RATE_POLICY") else {
        "tool_listing_sha256": "<FREEZE: run.json end.seen.tool_listing_sha256 of a check run>",
        "seat_model_reported": "<FREEZE: run.json end.seen.seat_model_reported of a check run>"}
    picked = _pick(ident)
    # a value that could not be read here is a placeholder (an attempt refuses it), never left out: left out, an
    # attempt would hold nothing for it. Not applicable: the CLI version of a --policy series, a task with no save
    na = {k for k, src in (("claude_version", "claude_version"), ("start_save_sha256", "start_save"))
          if "not_applicable" in (ident.get(src) or {}) or "none" in (ident.get(src) or {})}
    for k, v in picked.items():
        if (v is None or v == "") and k not in na and k in FROM_CHECK:
            src = {"git_commit": "git", "bridge_so_sha256": "bridge_so", "wheel_sha256": "wheel",
                   "wheel_members_sha256": "wheel", "map_sha256": "map", "game_build_id": "game_build_id",
                   "claude_version": "claude_version", "start_save_sha256": "start_save",
                   "prompt_sha256": "prompt"}.get(k)
            why = (ident.get(src) or {}).get("unread", "not read") if src else "not set"
            picked[k] = f"<FREEZE: unread at declare ({str(why)[:120]}); {FROM_CHECK[k]} of a check run>"
    from ck3bench.harness.driver import DEFAULTS
    timings = {}
    for k in TIMING_KEYS:
        v = E(TIMING_ENV[k]) or DEFAULTS[k]
        try:
            timings[k] = float(v)
        except ValueError:
            raise SystemExit(f"REFUSE declare_timing: {TIMING_ENV[k]}={v!r} is not a number of seconds")
    d = {"schema": EXPECT_SCHEMA, "kind": E("RATE_KIND", "rate"), "task": p["scenario"],
         **{k: v for k, v in picked.items() if v is not None}, "timings": timings,
         "switches": {k: v for k, v in ident["switches"].items() if k.startswith(BRIDGE_PREFIX)},
         "kit": {k: ident["switches"].get(k) for k in KIT_SWITCHES},
         "game_speed": p["game_speed"], "aligned_checks": p["aligned_checks"],
         "notes_version": "<FREEZE: run.json end.seen.notes_version of a check run>", **session}
    print(json.dumps(d, indent=1))
    return 0


def apply_config(argv: list[str]) -> list[str]:
    """`--config FILE` anywhere in a step's arguments (not `run`'s, which reads it itself): FILE's [env] table
    (ck3bench.harness.driver.load_config) sets the variables this environment does not, as the driver does; the
    arguments come back without it."""
    if "--config" not in argv or argv[:1] == ["run"]:
        return argv
    i = argv.index("--config")
    if i + 1 >= len(argv):
        raise SystemExit("REFUSE config: --config needs a file")
    from ck3bench.harness.driver import Refused, load_config
    try:
        env = load_config(argv[i + 1])
    except (Refused, OSError, ValueError) as e:
        raise SystemExit(f"REFUSE config: {e}")
    for k, v in env.items():
        os.environ.setdefault(k, v)
    return argv[:i] + argv[i + 2:]


HELP = ("-h", "--help")
STEPS = ("switches", "plan", "start", "watch", "end", "result", "declare", "slot", "run", "publish", "withdraw",
         "close")


def step_help(step: str) -> str:
    """A step's own lines of this module's statement (its usage and what it does), and where its inputs come from."""
    lines, out = __doc__.splitlines(), []
    for i, ln in enumerate(lines):
        if ln.split()[:2] == ["series", step]:
            out.append(ln)
            for x in lines[i + 1:]:
                if not (x.startswith(" " * 31) and x.strip()):
                    break
                out.append(x)
    arg = {"start": " OUT", "watch": " OUT", "end": " OUT", "result": " OUT", "slot": " HOST RUN_ID",
           "publish": " RUN DEST", "withdraw": " OUT [--cancelled]", "close": " OUT"}.get(step, "")
    return (f"usage: python -m ck3bench.harness series {step}{arg} [--config FILE]\n\n" + "\n".join(out)
            + "\n\nEvery input comes from the environment (docs/harness.md, \"Environment variables\"); "
              "`python -m ck3bench.harness series` prints every step.")


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    if argv[0] in HELP:
        print(__doc__)
        return 0
    if argv[0] != "run" and any(x in HELP for x in argv[1:]):
        # a step's help reads nothing and writes nothing (a directory named --help is never watched or scored)
        print(step_help(argv[0]) if argv[0] in STEPS else __doc__)
        return 0 if argv[0] in STEPS else 2
    try:
        argv = apply_config(argv)
    except SystemExit as e:
        print(e.code)
        return 2
    c = argv[0]
    try:
        if c == "switches":
            return cmd_switches()
        if c == "plan":
            return cmd_plan()
        if c == "declare":
            return cmd_declare()
        if c == "slot" and len(argv) == 3:
            return cmd_slot(argv[1], argv[2])
        if c == "run":
            from ck3bench.harness import driver
            return driver.main(argv[1:])
        if c == "withdraw" and len(argv) in (2, 3) and argv[2:] in ([], ["--cancelled"]):
            if not Path(argv[1]).is_dir():
                print(f"REFUSE withdraw: OUT {argv[1]!r} is not a directory")
                return 2
            return cmd_withdraw(Path(argv[1]), cancelled=argv[2:] == ["--cancelled"])
        if c == "publish" and len(argv) >= 3:
            from ck3bench.harness import publish
            return publish.main(argv[1:])
        if c in ("start", "watch", "end", "result", "close") and len(argv) == 2:
            if not Path(argv[1]).is_dir():
                print(f"REFUSE {c}: OUT {argv[1]!r} is not a directory (the run directory the driver made)")
                return 2
            return {"start": cmd_start, "watch": cmd_watch, "end": cmd_end, "result": cmd_result,
                    "close": cmd_close}[c](Path(argv[1]))
    except SystemExit as e:
        if isinstance(e.code, str):
            print(e.code)
            return 5
        raise
    except (ValueError, OSError) as e:
        # an input that does not read (a schedule that is no schedule, a file that cannot be opened): one REFUSE line
        print(f"REFUSE {c}: {type(e).__name__}: {e}")
        return 2
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
