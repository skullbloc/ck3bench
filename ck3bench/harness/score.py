"""python -m ck3bench.harness score RUN_DIR [RUN_DIR ...] [--json]: the success rate of N seated runs.

Per run, read from the run's own files (nothing from prose, nothing defaulted):
  success      the seat's `termination_reason == "goal_reached"` in the trajectory (the step row's
               infos[seat]); never the return, never `end.reason` / end.json's end_reason
  when         success: the game date of that step, and the creation date when the env recorded it;
               otherwise the counties at the last step (metrics[seat].values.dejure_counties)
  created_by   infos[seat].end.created: `seat` (the seat's own applied call), `game_ai_for_seat` (the
               game created it for the seat's ruler and no call of the seat's named it), `other` (someone else),
               or ABSENT with why (an env that writes no infos.end; a run that did not succeed has none)
  steps        the trajectory's end row (`steps`), else the step rows counted, and which it was
  cost_usd     accounting.json (ck3bench.harness.accounting) `cost_usd_reported`; absent says so
  wall_s       run.json (the series driver's) launch -> end, else end.json wall_s (the runner's), and which it was
  valid        run.json `validity` (ck3bench.harness.series end: the start/end identity check and attempt_reasons, below);
               a run without run.json is INVALID (`no_run_json`): nothing witnessed its identity
k/n counts VALID runs only, with a Wilson 95% interval. Invalid runs are listed, never counted.

THE TASK PAID ONCE (ck3bench goal `kingdom_held_once`, e.g. munster1066-kingdom-once-30y-wake): holding
the title does not end the episode, so success is NOT a termination reason. It is read from two places that must
agree, else `unread` (`success_witnesses_disagree`): the trajectory's end row `target_title[seat].held_at_any_step`
(the env's latch), and the step rows (the first step paying `target_title_first_gained`, or the title held in the
header's start metrics). The row adds the first-held date and step, and whether it was held at the horizon.
  creator      who created the title, split: `seat_call` (an applied call of the seat's own turn), `standing_order`
               (the env sent it: the call record carries an order id), `game_ai` (the game for the seat's ruler, no
               call of the seat's named it), `other`, or `unknown` with why (nothing read, or the trajectory and the
               bridge log disagree: bridge_title_witness.json, written by the series' end step). `created.who` reads
               seat, game_ai_for_seat or other.
  checks       per step, the game speed read against the declared one and (aligned checks) each check's date
               recomputed from its rows (`trajectory_validity`: 1 to `check_days` days off is FLAGGED and the run
               stays valid; more is `checks_off_beyond_cadence`; a missing date `witness_absent:check_dates`).

A RUN_DIR that is not a directory is refused (rc 2) before any run is read: a mistyped path is not an attempt.

A SERIES is scored against its LEDGER (`--ledger DIR`, else RATE_LEDGER, else `<series>.ledger` beside the first
RUN_DIR, where the reference driver keeps it under RATE_RUNS): every attempt the ledger names must be among the
RUN_DIRs given (both hosts' run directories in one place, each host's ledger entries copied to the other), and every
RUN_DIR given must be a ledger attempt; and every attempt must have played one expectation (run.json
`expectation.sha256`, the same file for the whole series). Any of these refuses (rc 4, nothing read) whatever the
override below.

THE COMPARISON of two scored series (`score --compare SEATED.json CONTROL.json`, each the `--json` output of one
stopped series, scored without the override; the first a seated series, the second a --policy one, else refused): k_A/n_A - k_B/n_B with its 95% interval by Newcombe's hybrid score method (the square-and-add interval of
the two Wilson intervals), and Fisher's exact test of the 2 x 2 table (successes and failures by series), two-sided
(`compare`). Nothing else is tested.

THE STOPPING RULE (a series: run.json carries `series`, the schedule below). The scorer refuses (rc 4) to score a
series before its stop (the valid target or the attempt cap, by validity alone, every listed run ended). Validity is
ONE reading with three readers: `series end` writes run.json `validity` and the ledger entry from its identity check
plus attempt_reasons (the protected directory and the resource rule below), the slot's stopping rule reads the
ledger, and the scorer reads attempt_reasons again: a ledger entry that disagrees with its run.json validity
(`series_ledger_disagrees`), or a run.json validity that disagrees with the rule read now
(`series_validity_disagrees`), refuses the score (rc 4) whatever the override. An attempt is ended when run.json has
its validity and its end marker (rate_run.done, the driver's last write), or, for a run.json that does not read after
`series start`, when its ledger entry records that damage (ended, invalid) beside its end marker; one with its validity and no marker (its
driver stopped after `series end`) holds the stop until `series close RUN_DIR` writes the marker. A set that mixes
series and non-series run dirs, or two series, is refused (rc 4) whatever the override. The one override is
`--before-stop-for-tests`, which is LOGGED (stderr and SCORE_RATE_OVERRIDE_LOG, default score_rate_overrides.log
beside the first run dir) and is for the harness's own tests only.

THE OUTPUT names each run by its directory's basename, never the path it was read at; `--json` keeps no bridge log
path and reduces any other absolute path to its basename (publishable). A series' gate carries its `identity`: the
task, the start save's sha256, the map's sha256 and the game's build id (IDENTITY_KEYS, one value over its attempts,
as each run.json's start identity read them and `series start` held them to the expectation), with the kit's commit,
wheel members and bridge beside them; `--compare` refuses two series whose IDENTITY_KEYS differ or are not one value.

RESOURCE RULE (a fixed rule of every series, its constants below; decided from recorded evidence only, BEFORE the
trajectory's outcome is read): a run whose runner ended `seat_stopped` or `seat_hung` is INVALID, not a failure, when
its stop window shows (a) an account rate/usage limit (`resource_limited:rate_limit`), (b) an API error, timeout or
overload on the model call (`resource_limited:api_error`) or (c) host pressure over the thresholds in PRESSURE,
MEMAVAIL_FLOOR_KB and D_STATE_RUN (`resource_limited:host_pressure`); evidence that is absent or unreadable is its own
state (`resource_evidence_absent:transcript|pressure|window`), never "no pressure", and also voids the stop: a series
driver that writes no RUN_DIR/pressure.tsv has every stop and hang voided, never counted as a failure
(docs/harness.md, "Absent pressure evidence"). Any other stop/hang stays a valid failure. (a) and (b) are also counted
over the WHOLE run and reported for every run. The printed table says how many runs absent evidence voided.
"""
from __future__ import annotations

import datetime as dt
import gzip
import json
import math
import re
import sys
import zlib
from pathlib import Path
from typing import Any, Optional

Z95 = 1.959963984540054
GOAL = "goal_reached"
#: ck3bench rewards.ONCE_GOALS (the harness's readers do not import the env to read a trajectory)
ONCE_GOALS = ("kingdom_held_once",)
OVERRIDE_FLAG = "--before-stop-for-tests"

# ── the resource rule's constants (fixed for a series: a different value is a different rule) ──
STOP_REASONS = ("seat_stopped", "seat_hung")
#: the agent CLI's rate-limit event statuses that are NOT a limit (plain information a session carries on most turns)
LIMIT_OK = ("allowed", "allowed_warning")
LIMIT_TEXT = re.compile(r"rate.?limit|usage limit|limit reached|too many requests|\b429\b", re.I)
API_TEXT = re.compile(r"api error|overloaded|\b529\b|\b5\d\d\b|timed? ?out|timeout|connection (error|reset|refused)"
                      r"|internal server error|service unavailable", re.I)
#: host pressure: any ONE sample in the window over any ONE of these (pressure.tsv, written by the series driver's host sampler)
PRESSURE = {"mem_full10": 10.0, "io_full10": 25.0, "cpu_some10": 75.0}     # PSI avg10, percent, >=
MEMAVAIL_FLOOR_KB = 1048576                                                # MemAvailable below 1 GiB
D_STATE_RUN = 2                                                            # the game in `D` this many samples in a row
WINDOW_PAD_S = 60                                                          # the window widened by two sample periods


def wilson(k: int, n: int, z: float = Z95) -> Optional[tuple[float, float]]:
    """Wilson score interval for k successes in n; None for n == 0 (no interval, not [0, 1])."""
    if n <= 0:
        return None
    p = k / n
    den = 1 + z * z / n
    mid = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return max(0.0, mid - half), min(1.0, mid + half)


def newcombe(k1: int, n1: int, k0: int, n0: int, z: float = Z95) -> Optional[tuple[float, float, float]]:
    """(d, lower, upper): d = k1/n1 - k0/n0 and its interval by Newcombe's hybrid score method (Newcombe 1998,
    method 10): lower = d - sqrt((p1 - l1)^2 + (u0 - p0)^2), upper = d + sqrt((u1 - p1)^2 + (p0 - l0)^2), from
    the two Wilson intervals (l, u). None when either n is 0."""
    w1, w0 = wilson(k1, n1, z), wilson(k0, n0, z)
    if w1 is None or w0 is None:
        return None
    p1, p0 = k1 / n1, k0 / n0
    d = p1 - p0
    return (d, d - math.sqrt((p1 - w1[0]) ** 2 + (w0[1] - p0) ** 2),
            d + math.sqrt((w1[1] - p1) ** 2 + (p0 - w0[0]) ** 2))


def fisher_two_sided(k1: int, n1: int, k0: int, n0: int) -> Optional[float]:
    """Fisher's exact test of the table [[k1, n1 - k1], [k0, n0 - k0]], two-sided: the sum of the probabilities of
    every table with the same margins that is no more likely than the one seen (the hypergeometric law; a relative
    tolerance of 1e-7 counts ties as R's fisher.test does). None when a margin is empty."""
    if n1 <= 0 or n0 <= 0:
        return None
    k, n = k1 + k0, n1 + n0
    lo, hi = max(0, k - n0), min(k, n1)
    den = math.comb(n, k)
    prob = {x: math.comb(n1, x) * math.comb(n0, k - x) / den for x in range(lo, hi + 1)}
    seen = prob[k1]
    return min(1.0, sum(p for p in prob.values() if p <= seen * (1 + 1e-7)))


def compare(a: dict, b: dict) -> dict:
    """Two scored series (each `score`'s summary: k, n) compared: the difference A - B with its Newcombe 95%
    interval, and Fisher's exact two-sided p, at the 0.05 level."""
    k1, n1, k0, n0 = a["k"], a["n"], b["k"], b["n"]
    nc = newcombe(k1, n1, k0, n0)
    p = fisher_two_sided(k1, n1, k0, n0)
    return {"a": {"k": k1, "n": n1}, "b": {"k": k0, "n": n0},
            "difference": nc[0] if nc else None, "newcombe95": [nc[1], nc[2]] if nc else None,
            "fisher_two_sided_p": p, "fisher_reject_at_0_05": (p < 0.05) if p is not None else None}


def compare_refusals(docs: list[dict]) -> list[str]:
    """Why two `score --json` outputs are not the comparison's inputs: each must be one stopped series scored without
    the override, the first seated (every run played by an agent CLI) and the second the control (every run a
    --policy one), two different series."""
    out = []
    for name, d, want in zip(("first (seated)", "second (control)"), docs, ("seated", "control")):
        g = d.get("gate") if isinstance(d.get("gate"), dict) else {}
        sc = d.get("score") if isinstance(d.get("score"), dict) else {}
        if not all(isinstance(sc.get(k), int) for k in ("k", "n")):
            out.append(f"the {name} file has no score k/n")
        if not g.get("series") or g.get("series") == "mixed":
            out.append(f"the {name} file is not one series' score ({g.get('series')!r})")
        elif g.get("stopped") is not True:
            out.append(f"the {name} file's series had not stopped ({g.get('why')})")
        if g.get("override"):
            out.append(f"the {name} file was scored with {OVERRIDE_FLAG}")
        kinds = {r.get("seated") for r in d.get("runs") or [] if isinstance(r, dict)}
        if kinds != {want == "seated"}:
            out.append(f"the {name} file is not a {want} series (its runs: "
                       f"{sorted({'seated' if k else 'control' if k is False else 'unread' for k in kinds})})")
    ga, gb = ((d.get("gate") or {}).get("series") for d in docs)
    if ga and ga == gb:
        out.append(f"both files score series {ga!r}")
    ids = [(d.get("gate") or {}).get("identity") if isinstance(d.get("gate"), dict) else None for d in docs]
    ids = [x if isinstance(x, dict) else {} for x in ids]
    for k in IDENTITY_KEYS:
        va, vb = ids[0].get(k), ids[1].get(k)
        if not isinstance(va, str) or not isinstance(vb, str):
            bad = [n for n, v in zip(("first", "second"), (va, vb)) if not isinstance(v, str)]
            out.append(f"identity_unread:{k}: the {' and '.join(bad)} file's series has no one {k} "
                       f"({va if not isinstance(va, str) else vb!r})")
        elif va != vb:
            out.append(f"identity_differs:{k}: the two series played different {k} ({va} and {vb}); a comparison is "
                       "of one task from one start")
    return out


def _compare_main(paths: list[str]) -> int:
    if len(paths) != 2:
        print("usage: python -m ck3bench.harness score --compare SEATED.json CONTROL.json (each a `score --json` "
              "output of a stopped series)", file=sys.stderr)
        return 2
    try:
        docs = [json.loads(Path(x).read_text(encoding="utf-8")) for x in paths]
        if not all(isinstance(d, dict) for d in docs):
            raise ValueError("not a JSON object")
    except (OSError, ValueError) as e:
        print(f"REFUSE compare: {type(e).__name__}: {e}", file=sys.stderr)
        return 2
    bad = compare_refusals(docs)
    if bad:
        for x in bad:
            print(f"REFUSE compare: {x}", file=sys.stderr)
        return 2
    print(json.dumps({**compare(docs[0]["score"], docs[1]["score"]),
                      "seated_series": docs[0]["gate"]["series"], "control_series": docs[1]["gate"]["series"],
                      "identity": {k: docs[0]["gate"]["identity"][k] for k in IDENTITY_KEYS}},
                     indent=1))
    return 0


#: the validity reason of a run file a reader could not read whole: `file_unreadable:<file>` (file_reasons)
FILE_UNREADABLE = "file_unreadable"
#: the validity reason of a run file a played-out attempt must hold and does not: `file_absent:<file>`
FILE_ABSENT = "file_absent"


def read_jsonl(path: Path) -> tuple[list[dict], Optional[str]]:
    """The JSON-object lines of a `.jsonl` (or `.jsonl.gz`) run file, and why it did not read whole (None when it
    did): it cannot be opened, the `.gz` does not decompress, it is not UTF-8, a line is not JSON (a truncated last
    line, a NUL-filled tail), or a line is JSON but not an object. Never raises: the lines that read are returned."""
    try:
        raw = gzip.decompress(path.read_bytes()) if path.suffix == ".gz" else path.read_bytes()
    except (OSError, EOFError, zlib.error) as e:
        return [], f"{type(e).__name__}"
    why = []
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text, why = raw.decode("utf-8", "replace"), ["not UTF-8"]
    rows, bad, other = [], 0, 0
    for ln in text.split("\n"):
        if not ln.strip():
            continue
        try:
            x = json.loads(ln)
        except (ValueError, RecursionError):
            bad += 1
            continue
        if isinstance(x, dict):
            rows.append(x)
        else:
            other += 1
    why += [f"{bad} lines not JSON"] if bad else []
    why += [f"{other} lines not JSON objects"] if other else []
    return rows, "; ".join(why) or None


def read_object(p: Path) -> tuple[Optional[dict], Optional[str]]:
    """A run file that holds one JSON object: (the object, None); (None, None) when there is no such file; (None,
    why) when it does not read as one object (truncated, NUL bytes, not UTF-8, another JSON type). Never raises."""
    try:
        raw = p.read_bytes()
    except FileNotFoundError:
        return None, None
    except OSError as e:
        return None, type(e).__name__
    try:
        x = json.loads(raw.decode("utf-8"))
    except UnicodeDecodeError:
        return None, "not UTF-8"
    except (ValueError, RecursionError):
        return None, "not JSON"
    return (x, None) if isinstance(x, dict) else (None, f"a JSON {type(x).__name__}, not an object")


def _d(x: Any) -> dict:
    """X when it is a dict, else an empty one: a field of a run file read as the reader expects it, or not at all."""
    return x if isinstance(x, dict) else {}


def _rows(path: Path) -> list[dict]:
    return read_jsonl(path)[0]


def trajectory_path(d: Path) -> Optional[Path]:
    for n in ("trajectory.jsonl", "trajectory.jsonl.gz"):
        if (d / n).is_file():
            return d / n
    return None


def _json(p: Path) -> Optional[dict]:
    return read_object(p)[0]


def _utc(s: Any) -> Optional[float]:
    """A UTC time as the kit writes it (`2026-01-01T00:00:00Z`, or with an offset), or None: a time with no zone
    names no instant (it would be read in the reading host's own zone), so it does not read."""
    if not isinstance(s, str):
        return None
    try:
        t = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
        return None if t.tzinfo is None else t.timestamp()
    except (ValueError, OverflowError, OSError):
        return None


#: THE fields of run.json and the type each must have when present (`series start` and `series end` write them):
#: a field of another type is read as damage, never as a value (run_json_problem)
RUN_JSON_TYPES = {"schema": str, "run_id": str, "kind": str, "host": str, "launch_utc": str, "plan": dict,
                  "runner_flags": list, "start": dict, "end": dict, "start_reasons": list, "series": dict,
                  "validity": dict, "concurrency": dict, "expectation": (dict, str), "records_flushed": bool,
                  "declared": dict}
#: run.json `declared`: the variables `series start` records as the attempt's own declarations, which `series end`
#: reads in place of its environment (series.as_started); when the block is there every one is text
DECLARED_AT_START = ("RATE_POLICY", "RATE_MODEL", "RATE_SCENARIO", "RATE_GAME_SPEED", "RATE_EXPECT", "RATE_HOST",
                     "RATE_KIND", "RATE_RUN_ID")
#: run.json `series` (a series attempt): the fields and their types (`attempt` is None in a start the slot refused);
#: the first four must be there
RUN_JSON_SERIES_TYPES = {"id": str, "attempt": (int, type(None)), "valid_target": int, "attempt_cap": int,
                         "schedule": str, "schedule_sha256": str, "pattern": list}
RUN_JSON_SERIES_REQUIRED = ("id", "attempt", "valid_target", "attempt_cap")
#: run.json `start` and `end` (the launch and end identity `series start` and `series end` write; series.static_identity):
#: each path's type when present. A part that is present is an object; a value the kit could not read is an object
#: without it ({"unread": why}), never another type. Every leaf a step or the scorer reads is here (run_identity,
#: series._pick, the witnesses).
_TEXT = (str, type(None))
RUN_JSON_IDENTITY_TYPES = {
    ("at",): str, ("host",): str, ("model",): _TEXT, ("full_hold_s",): (int, float, type(None)), ("switches",): dict,
    ("wall_s",): (int, float, type(None)),
    ("git",): dict, ("git", "commit"): _TEXT, ("git", "dirty"): (bool, type(None)),
    ("bridge_so",): dict, ("bridge_so", "sha256"): _TEXT,
    ("wheel",): dict, ("wheel", "sha256"): _TEXT, ("wheel", "compare"): dict,
    ("wheel", "compare", "verdict"): _TEXT, ("wheel", "compare", "wheel_members_sha256"): _TEXT,
    ("map",): dict, ("map", "sha256"): _TEXT,
    ("game_build_id",): dict, ("game_build_id", "build_id"): _TEXT,
    ("claude_version",): dict, ("claude_version", "version"): _TEXT,
    ("start_save",): dict, ("start_save", "sha256"): _TEXT, ("start_save", "none"): _TEXT,
    ("prompt",): dict, ("prompt", "sha256"): _TEXT,
}


def _typed(v: Any, t: Any) -> bool:
    ts = t if isinstance(t, tuple) else (t,)
    return isinstance(v, ts) and not (isinstance(v, bool) and bool not in ts)


def run_json_problem(rj: dict) -> Optional[str]:
    """Why run.json (one JSON object) does not read as the record `series start` and `series end` write, naming the
    field, or None: a field of RUN_JSON_TYPES of another type, `declared` without one of DECLARED_AT_START as text,
    `declared.RATE_EXPECT` empty where `expectation` records a file, `series` without its fields
    (RUN_JSON_SERIES_TYPES),
    `validity` that is not {valid: true or false, reasons: [text]}, `start_reasons` that is not [text], `plan.scenario`
    or `expectation.sha256` that is not text, a `start.*` or `end.*` identity field of another type
    (RUN_JSON_IDENTITY_TYPES)."""
    for k, t in RUN_JSON_TYPES.items():
        if k in rj and not _typed(rj[k], t):
            return f"`{k}` is a {type(rj[k]).__name__}"
    dec = rj.get("declared")
    if dec is not None:
        for k in DECLARED_AT_START:
            if not isinstance(dec.get(k), str):
                return f"`declared.{k}` is {'absent' if k not in dec else 'a ' + type(dec[k]).__name__}"
        if isinstance(rj.get("expectation"), dict) and not dec["RATE_EXPECT"]:
            return "`declared.RATE_EXPECT` is empty and `expectation` records the file `series start` read"
    ser = rj.get("series")
    if ser is not None:
        for k, t in RUN_JSON_SERIES_TYPES.items():
            if (k in ser or k in RUN_JSON_SERIES_REQUIRED) and not _typed(ser.get(k), t):
                return f"`series.{k}` is {'absent' if k not in ser else 'a ' + type(ser[k]).__name__}"
    v = rj.get("validity")
    if v is not None and not (isinstance(v.get("valid"), bool) and isinstance(v.get("reasons"), list)
                              and all(isinstance(x, str) for x in v["reasons"])):
        return "`validity` is not {valid: true or false, reasons: [text]}"
    if "start_reasons" in rj and not all(isinstance(x, str) for x in rj["start_reasons"]):
        return "`start_reasons` is not a list of text"
    for part, key in (("plan", "scenario"), ("expectation", "sha256")):
        x = rj.get(part)
        if isinstance(x, dict) and key in x and not isinstance(x[key], str):
            return f"`{part}.{key}` is a {type(x[key]).__name__}"
    for part in ("start", "end"):
        x = rj.get(part)
        if not isinstance(x, dict):
            continue
        for path, t in RUN_JSON_IDENTITY_TYPES.items():
            v, here = x, True
            for k in path:
                if not isinstance(v, dict) or k not in v:
                    here = False
                    break
                v = v[k]
            if here and not _typed(v, t):
                return f"`{part}.{'.'.join(path)}` is a {type(v).__name__}"
    return None


def read_run_json(d: Path) -> tuple[Optional[dict], str]:
    """THE reading of a run directory's run.json, which every step and the scorer go through: (run.json, "read"),
    (None, "absent"), or (None, "unreadable: why") when it is not one JSON object (read_object) or a field is not of
    its type (run_json_problem). Never raises."""
    rj, why = read_object(Path(d) / "run.json")
    if why:
        return None, f"unreadable: {why}"
    if rj is None:
        return None, "absent"
    bad = run_json_problem(rj)
    return (None, f"unreadable: {bad}") if bad else (rj, "read")


def run_json_damage(state: str) -> str:
    """The validity reason of a run.json that does not read: `file_absent:run.json` or `file_unreadable:run.json`."""
    return f"{FILE_ABSENT if state == 'absent' else FILE_UNREADABLE}:run.json"


def transcript_evidence(d: Path) -> dict:
    """(a) and (b) from the seat's transcript (transcript.jsonl), for the whole run and for its LAST session
    (fresh turns: the stopping turn; a single-session run: the one session). Absent or unreadable is said, never zero."""
    p = d / "transcript.jsonl"
    if not p.is_file():
        return {"state": "absent", "why": "no transcript.jsonl (the seat's private transcript) in the run dir"}
    events, why = read_jsonl(p)
    if why and not events:
        return {"state": "unreadable", "why": why}
    sessions: list[dict] = []
    seen = False

    def new():
        sessions.append({"rate_limit_events": 0, "limit_hits": 0, "limit_warnings": 0, "api_errors": 0,
                         "statuses": {}})
    new()
    for ev in events:
        t, st = ev.get("type"), ev.get("subtype")
        st = st if isinstance(st, str) or st is None else str(st)
        if t == "system" and st == "init":
            if seen:              # every init after the first opens a session (fresh turns: one per turn)
                new()
            seen = True
            continue
        cur = sessions[-1]
        if t == "rate_limit_event":
            info = _d(ev.get("rate_limit_info"))
            status = str(info.get("status"))
            cur["rate_limit_events"] += 1
            cur["statuses"][status] = cur["statuses"].get(status, 0) + 1
            if status == "allowed_warning":
                cur["limit_warnings"] += 1
            elif status not in LIMIT_OK:
                cur["limit_hits"] += 1
        elif t == "result" and (ev.get("is_error") or str(st or "").startswith("error")):
            text = json.dumps(ev.get("result") or ev.get("errors") or st)
            if LIMIT_TEXT.search(text):
                cur["limit_hits"] += 1
            else:
                cur["api_errors"] += 1
        elif t == "system" and st and ("retry" in st or "error" in st):
            text = json.dumps(ev)
            cur["limit_hits" if LIMIT_TEXT.search(text) else "api_errors"] += 1
        elif t == "assistant" and (ev.get("error") or _d(ev.get("message")).get("model") == "<synthetic>"):
            text = json.dumps(ev.get("error") or _d(ev.get("message")).get("content"))
            if LIMIT_TEXT.search(text):
                cur["limit_hits"] += 1
            elif API_TEXT.search(text) or ev.get("error"):
                cur["api_errors"] += 1
    keys = ("rate_limit_events", "limit_hits", "limit_warnings", "api_errors")
    whole = {k: sum(x[k] for x in sessions) for k in keys}
    return {"state": "read" if not why else "partly_unreadable", **({"why": why} if why else {}),
            "sessions": len(sessions), "whole_run": whole, "last_session": {k: sessions[-1][k] for k in keys}}


def stop_window(d: Path, end_reason: str) -> Optional[tuple[float, float]]:
    """The stopping turn: from the last turn row's `sent_utc` (seat_stopped: the runner wrote that turn's row) or
    `ended_utc` (seat_hung: the hung turn has no row), else the run's launch, to end.json `at`."""
    ej = _json(d / "end.json") or {}
    t1 = _utc(ej.get("at"))
    rows: list[dict] = []
    tp = d / "turns.jsonl"
    if tp.is_file():
        rows, why = read_jsonl(tp)
        if why:                    # a turns file that does not read whole gives no stopping turn: no window
            return None
    if rows:                       # every turn row carries both times: one that does not read gives no window
        t0 = _utc(rows[-1].get("sent_utc") if end_reason == "seat_stopped" else rows[-1].get("ended_utc"))
    else:
        t0 = _utc((_json(d / "run.json") or {}).get("launch_utc"))
    return (t0, t1) if t0 is not None and t1 is not None and t1 >= t0 else None


def pressure_evidence(d: Path, window: Optional[tuple[float, float]]) -> dict:
    """(c) from pressure.tsv over the window (padded WINDOW_PAD_S each side)."""
    p = d / "pressure.tsv"
    if not p.is_file():
        return {"state": "absent", "why": "no pressure.tsv (the sampler wrote nothing)"}
    if window is None:
        return {"state": "absent", "why": "no stop window (turns.jsonl / end.json / run.json give no times)"}
    try:
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as e:
        return {"state": "unreadable", "why": f"{type(e).__name__}: {e.strerror}"}
    if not lines:
        return {"state": "unreadable", "why": "pressure.tsv is empty"}
    head = lines[0].split("\t")
    rows = [dict(zip(head, ln.split("\t"))) for ln in lines[1:] if ln.strip()]
    lo, hi = window[0] - WINDOW_PAD_S, window[1] + WINDOW_PAD_S
    inw = [r for r in rows if (_utc(r.get("utc")) or -1) >= lo and (_utc(r.get("utc")) or 1e18) <= hi]
    if not inw:
        return {"state": "absent", "why": f"no sample in the window ({len(rows)} samples outside it)"}

    def num(r, k):
        try:
            return float(r.get(k))
        except (TypeError, ValueError):
            return None
    over, unread, drun, dmax = [], 0, 0, 0
    for r in inw:
        vals = {k: num(r, k) for k in (*PRESSURE, "memavail_kb")}
        if all(v is None for v in vals.values()):
            unread += 1
        for k, lim in PRESSURE.items():
            if vals[k] is not None and vals[k] >= lim:
                over.append(f"{r.get('utc')} {k}={vals[k]}")
        if vals["memavail_kb"] is not None and vals["memavail_kb"] < MEMAVAIL_FLOOR_KB:
            over.append(f"{r.get('utc')} memavail_kb={int(vals['memavail_kb'])}")
        drun = drun + 1 if r.get("game_state") == "D" else 0
        dmax = max(dmax, drun)
    if dmax >= D_STATE_RUN:
        over.append(f"game in D state {dmax} samples in a row")
    if unread == len(inw):
        return {"state": "unreadable", "why": f"all {len(inw)} samples in the window are NA"}
    return {"state": "read", "samples": len(inw), "samples_na": unread, "over": over[:10], "pressure": bool(over)}


def resource_rule(d: Path) -> dict:
    """The resource rule, from recorded evidence only (no trajectory read here)."""
    ej = _json(d / "end.json")
    er = str((ej or {}).get("end_reason") or "").split(":")[0]
    tx = transcript_evidence(d)
    out: dict[str, Any] = {"runner_end": er or None, "transcript": tx}
    if er not in STOP_REASONS:
        out["applies"] = False
        return out
    win = stop_window(d, er)
    pr = pressure_evidence(d, win)
    out.update(applies=True, window=[win[0], win[1]] if win else None, pressure=pr)
    reasons = []
    if tx["state"] != "read":
        reasons.append("resource_evidence_absent:transcript")
    else:
        last = tx["last_session"]
        if last["limit_hits"]:
            reasons.append("resource_limited:rate_limit")
        if last["api_errors"]:
            reasons.append("resource_limited:api_error")
    if win is None:
        reasons.append("resource_evidence_absent:window")
    elif pr["state"] != "read":
        reasons.append("resource_evidence_absent:pressure")
    elif pr["pressure"]:
        reasons.append("resource_limited:host_pressure")
    out["reasons"] = reasons
    out["verdict"] = "void" if reasons else "valid_failure"
    return out


def created_by(end: Any) -> dict:
    """Who created the target title, from infos[seat].end; every absence says why."""
    if not isinstance(end, dict):
        return {"who": "absent", "why": "no infos.end in the step (an env that writes none)"}
    c = end.get("created")
    if not isinstance(c, dict):
        return {"who": "absent", "why": "infos.end carries no `created`"}
    if "unread" in c:
        return {"who": "unread", "why": c["unread"]}
    by = c.get("by")
    if isinstance(by, str) and by.startswith("you:"):
        who = "seat"
    elif isinstance(by, str) and by.startswith("the game for your ruler"):
        who = "game_ai_for_seat"
    elif by is None:
        who = "other"
    else:
        who = "unclassified"
    return {"who": who, "by": by, "date": c.get("date"), "char_id": c.get("char_id")}


def _bridge_witness(d: Path) -> dict:
    w = _json(d / "bridge_title_witness.json")
    if w is None:
        return {"state": "absent", "why": "no bridge_title_witness.json (`series end` writes it from the bridge log)"}
    return w


def creator(created: Any, calls: Any, d: Optional[Path] = None) -> dict:
    """Who created the target title, split four ways (module doc), from the creation as the env read it (`created`,
    the title history with `by`) and the seat's applied calls naming the title (`calls`, each with `order_id` when a
    standing order sent it), corroborated by the bridge log's create_title commands. `unknown` is a state with its
    reason, never a default."""
    out: dict[str, Any] = {}
    if not isinstance(created, dict):
        out = {"who": "unknown", "why": "no creation record in the trajectory"}
    elif "unread" in created:
        out = {"who": "unknown", "why": f"the title history did not read: {created['unread']}"}
    else:
        by = created.get("by")
        last = calls[-1] if isinstance(calls, list) and calls and isinstance(calls[-1], dict) else None
        if isinstance(by, str) and by.startswith("you:"):
            out = ({"who": "standing_order", "order_id": last["order_id"]} if last and last.get("order_id") else
                   {"who": "seat_call"} if last else {"who": "unknown", "why": "`by` names a call of yours, and no "
                                                                               "call record names the title"})
        elif isinstance(by, str) and by.startswith("the game for your ruler"):
            out = {"who": "game_ai"}
        elif by is None:
            out = {"who": "other", "char_id": created.get("char_id")}
        else:
            out = {"who": "unknown", "why": f"`by` not recognised: {by!r}"}
        out.update(date=created.get("date"), by=by)
        if last:
            out["call"] = {k: last.get(k) for k in ("tool", "intent", "step", "game_date", "order_id") if k in last}
    bw = _bridge_witness(d) if d is not None else {"state": "absent", "why": "no run dir"}
    out["from_trajectory"] = out["who"]
    out["bridge"] = bw
    if bw.get("state") == "read" and out["who"] in ("seat_call", "standing_order", "game_ai"):
        sent = bool(bw.get("completed"))
        if out["who"] == "game_ai" and sent:
            out.update(who="unknown", why="witnesses_disagree: the trajectory says the game created it, the bridge "
                                          "log completed a create_title command for it")
        elif out["who"] != "game_ai" and not sent:
            out.update(who="unknown", why="witnesses_disagree: the trajectory names a call, the bridge log completed "
                                          "no create_title command for the title")
        out["corroborated"] = out["who"] != "unknown"
    else:
        out["corroborated"] = None
    return out


def once_outcome(recs: list[dict], seat: str, d: Optional[Path] = None) -> dict:
    """The task paid once: success from the end row's latch and the step rows, which must agree (module doc)."""
    head = next((r for r in recs if r.get("kind") == "header"), {}) or {}
    steps = [r for r in recs if r.get("kind") == "step"]
    endr = next((r for r in recs if r.get("kind") == "end"), None)
    out: dict[str, Any] = {"reader": "once"}
    start_held = (((head.get("start_metrics") or {}).get(seat) or {}).get("values") or {}).get("holds_target_title")
    paid = next((st for st in steps if (((st.get("infos") or {}).get(seat) or {}).get("reward_terms") or {})
                 .get("target_title_first_gained", 0) > 0), None)
    by_steps = True if (start_held is True or paid is not None) else (False if endr is not None else None)
    tt = ((endr or {}).get("target_title") or {}).get(seat) if endr is not None else None
    if endr is None:
        return {**out, "success": None, "success_why": "unread: no end row: the run did not finish"}
    if not isinstance(tt, dict) or not isinstance(tt.get("held_at_any_step"), bool):
        return {**out, "success": None, "success_why": "unread: the end row carries no target_title latch for the seat"}
    if tt["held_at_any_step"] != by_steps:
        return {**out, "success": None, "success_why": f"unread: success_witnesses_disagree (end row "
                                                       f"{tt['held_at_any_step']}, step rows {by_steps})"}
    first = tt.get("first_held") or {}
    out.update(success=tt["held_at_any_step"], success_why=f"end row target_title.held_at_any_step="
                                                            f"{tt['held_at_any_step']}, step rows agree")
    if out["success"]:
        info = ((paid or {}).get("infos") or {}).get(seat) or {}
        fh = info.get("target_title_first_held") or first
        out.update(success_date=first.get("date"), success_step=first.get("step"),
                   held_at_horizon=tt.get("held_at_last_step") if tt.get("horizon_reached") else None,
                   horizon_reached=tt.get("horizon_reached"),
                   created=created_by({"created": fh.get("created")} if fh.get("created") else fh),
                   creator=creator(fh.get("created"), fh.get("your_calls_naming_it"), d),
                   creation_date=creation_date(tt.get("dates"), fh))
    return out


def creation_date(dates: Any, fh: Any) -> dict:
    """The secondary "date of creation": the creation date when known (the seat's applied call
    that named the title, else the title history), else the end date of the first step that read the title held,
    labelled by `from`. `first_step_held` is kept beside it (the primary success date is that step's). An end row
    written before the env recorded `dates` is read from the first holding itself, the same rule."""
    fh = fh if isinstance(fh, dict) else {}
    if not isinstance(dates, dict):
        calls = [c for c in fh.get("your_calls_naming_it") or [] if isinstance(c, dict) and c.get("game_date")]
        hist = fh.get("created") if isinstance(fh.get("created"), dict) else {}
        if calls and str(hist.get("by") or "").startswith("you:"):
            dates = {"created": calls[-1]["game_date"], "created_from": "your_call"}
        elif hist.get("date") and "unread" not in hist:
            dates = {"created": hist["date"], "created_from": "title_history"}
        else:
            dates = {"created": None, "created_from": "unread"}
        dates["first_step_held"] = fh.get("date")
    if dates.get("created"):
        return {"date": dates["created"], "from": dates.get("created_from"), "first_step_held": dates.get("first_step_held")}
    return {"date": dates.get("first_step_held"), "from": "first_step_held",
            "first_step_held": dates.get("first_step_held"), "why": "the creation date was not read"}


def trajectory_validity(recs: list[dict], seat: Optional[str], declared_speed: Optional[int] = None,
                        declared_aligned: Optional[bool] = None) -> dict:
    """The run's clock against its declaration, from the trajectory alone (no outcome read): the header's speed and
    aligned checks against the declared ones; per step, the speed witness, and on an aligned task each check's
    date recomputed from its rows (ck3bench types.check_dates_witness's arithmetic, never the stored status).
    Off by 1..check_days days: FLAGGED (the run stays valid); more: `checks_off_beyond_cadence`; a check with no
    date, or an aligned step with no check rows: `witness_absent:check_dates`."""
    from ck3bench.env import calendar
    head = next((r for r in recs if r.get("kind") == "header"), None)
    if head is None:
        return {"reasons": ["no_trajectory_header"], "flagged": [], "checks": 0}
    sc = head.get("scenario") or {}
    wake = sc.get("wake") or {}
    aligned = wake.get("aligned_checks") is True
    cadence = wake.get("check_days")
    reasons: list[str] = []
    if declared_speed is not None and sc.get("game_speed") != declared_speed:
        reasons.append("expect_differs:game_speed")
    if declared_aligned is not None and aligned != declared_aligned:
        reasons.append("expect_differs:aligned_checks")
    flagged, checks, speed_bad, speed_absent, cd_absent = [], 0, [], [], []
    for st in (r for r in recs if r.get("kind") == "step"):
        n = st.get("step")
        info = ((st.get("infos") or {}).get(seat) or {}) if seat else {}
        if info.get("truncation_reason") not in (None, "horizon"):
            continue                    # a failed step: its own code (technical_truncation) says why
        if declared_speed is not None:
            sp = (st.get("advance") or {}).get("speed") or {}
            if sp.get("status") == "agreed" and sp.get("read") == [declared_speed] and sp.get("declared") == declared_speed:
                pass
            elif sp.get("status") in ("mismatch",) or (sp.get("read") and sp.get("read") != [declared_speed]):
                speed_bad.append(n)
            else:
                speed_absent.append(n)
        if aligned:
            cd = (st.get("wake") or {}).get("check_dates")
            rows = cd.get("rows") if isinstance(cd, dict) else None
            if not isinstance(rows, list) or not rows:
                cd_absent.append(n)
                continue
            for r in rows:
                checks += 1
                a, b = calendar.ordinal(r.get("intended")), calendar.ordinal(r.get("read"))
                if a is None or b is None:
                    cd_absent.append(n)
                    continue
                off = b - a
                if off == 0:
                    continue
                if isinstance(cadence, int) and abs(off) <= cadence:
                    flagged.append({"step": n, "intended": r.get("intended"), "read": r.get("read"), "off": off})
                else:
                    reasons.append("checks_off_beyond_cadence")
                    flagged.append({"step": n, "intended": r.get("intended"), "read": r.get("read"), "off": off,
                                    "beyond": True})
    if speed_bad:
        reasons.append("speed_differs")
    if speed_absent:
        reasons.append("witness_absent:speed")
    if cd_absent:
        reasons.append("witness_absent:check_dates")
    return {"reasons": sorted(set(reasons)), "flagged": flagged, "checks": checks, "aligned": aligned,
            "check_days": cadence, "speed_differs_steps": speed_bad[:10], "speed_absent_steps": speed_absent[:10],
            "check_dates_absent_steps": sorted(set(x for x in cd_absent if x is not None))[:10]}


def read_run(d: Path) -> dict:
    """One run's row. Raises nothing: a file that is missing or does not read is said in the row (absent, or the
    run invalid with `file_unreadable:<file>`), and a reading that fails is the row's `read_failed:<error>`."""
    d = Path(d)
    try:
        row = _read_run(d)
    except Exception as e:  # noqa: BLE001
        row = {"run": d.name, "success": None, "success_why": f"unread: the run's files did not read ({type(e).__name__})",
               "valid": False, "invalid_reasons": [f"read_failed:{type(e).__name__}"]}
    for x in file_reasons(d):
        if x not in row.get("invalid_reasons") or []:
            row["valid"] = False
            row["invalid_reasons"] = [y for y in row.get("invalid_reasons") or [] if y != "validity_absent"] + [x]
    sr = d / START_REFUSED_FILE
    if sr.is_file():           # the driver refused the start: no game ran, not an attempt
        row["valid"] = False
        row["start_refused"] = _text(sr)
        row["invalid_reasons"] = [START_REFUSED] + [x for x in row.get("invalid_reasons") or [] if x != START_REFUSED]
    row["end_row"] = trajectory_end(d)
    row["end_row_budget"] = end_row_budget(d)
    return row


def budget_end(row_s: float, start_s: float, budget_s: float) -> str:
    """THE wall-budget rule for an end row, which the runner (runner.Run.end_by_budget) and the scorer
    (end_row_budget) both apply: `wall_budget` when the row's time is in the second of the deadline,
    floor(start + budget), or later, else `episode_over`."""
    return "wall_budget" if row_s >= math.floor(start_s + budget_s) else "episode_over"


#: the seat runner's start on the wall clock (`start_s`, seconds since the epoch), written by the runner as it starts:
#: the instant its wall budget counts from
RUNNER_START = "runner_start.json"


def end_row_budget(d: Path) -> Optional[str]:
    """The env's end row judged by the wall-budget rule (budget_end) from the attempt's own records: the row's
    `wall_utc`, the runner's own start (RUNNER_START `start_s`, the instant its deadline counts from; for a run
    directory without that file, run.json `launch_utc`, stamped before its runner starts) and the budget its runner
    flags give (run.json `runner_flags`, `--wall-budget-s` as `run-seat` reads it). None when any of them does not
    read (a RUNNER_START that is there and does not read included). Never raises."""
    try:
        row = trajectory_end_row(d)
        rj, _ = read_run_json(d)
        t = _utc((row or {}).get("wall_utc"))
        if (d / RUNNER_START).exists() or (d / RUNNER_START).is_symlink():
            s = (_json(d / RUNNER_START) or {}).get("start_s")
            start = (float(s) if isinstance(s, (int, float)) and not isinstance(s, bool) and math.isfinite(s)
                     else None)
        else:
            start = _utc((rj or {}).get("launch_utc"))
        if t is None or start is None:
            return None
        from ck3bench.harness.series import runner_timings  # series imports this module
        b = runner_timings(rj.get("runner_flags")).get("wall_budget_s")
        if not isinstance(b, (int, float)) or isinstance(b, bool) or not math.isfinite(b):
            return None
        return budget_end(t, start, b)
    except Exception:  # noqa: BLE001
        return None


#: THE class of validity reasons that say a record written AFTER the env's end row is absent or not final: the
#: records the runner, the watcher, the driver and `series end` write once the episode's outcome is fixed, each named
#: with the reason its absence gives (docs/prereg-munster-kingdom.md, sections 4 and 10). Nothing else is in it:
#: * the runner's end.json: absent (`witness_absent:end_json` from `series end`, `file_absent:end.json` from the
#:   scorer), not final (`witness_absent:end_json_final`), or its end the signal that stopped the runner
#:   (`runner_end:stopped_by_signal`);
#: * the watcher's game witness at the end, witness_end.json (`witness_absent:game_end`);
#: * the driver's after digest of the protected directory, own.after.* (`witness_absent:owner_tree`).
#: A --policy run's absent session witnesses are not in it: `series end` reads the arm from run.json (the start's
#: record), so a control attempt whose end.json is absent gets no session reason at all.
STOPPED_AFTER_END = ("witness_absent:end_json", "file_absent:end.json", "witness_absent:end_json_final",
                     "runner_end:stopped_by_signal", "witness_absent:game_end", "witness_absent:owner_tree")


def stopped_after_end(r: dict) -> bool:
    """Stopped after its end row, by evidence: the attempt is invalid, its trajectory holds a readable end row
    (trajectory_end) stamped before its wall-budget deadline (`end_row_budget` `episode_over`, the runner's rule:
    a row at or after the deadline is the runner's `wall_budget`, invalid, and one whose time, start or budget does
    not read is not added), and every one of its validity reasons is in STOPPED_AFTER_END. An attempt with any other
    reason is not."""
    reasons = r.get("invalid_reasons") or []
    return (not r.get("valid") and bool(r.get("end_row")) and r.get("end_row_budget") == "episode_over"
            and bool(reasons) and all(x in STOPPED_AFTER_END for x in reasons))


def _stop_class_only(r: dict) -> bool:
    reasons = r.get("invalid_reasons") or []
    return (not r.get("valid") and bool(r.get("end_row")) and bool(reasons)
            and all(x in STOPPED_AFTER_END for x in reasons))


def sensitivity(rows: list[dict], s: dict, last_scored: Optional[int] = None) -> dict:
    """k'/n': the score's k/n with every attempt stopped after its end row (stopped_after_end) counted with the outcome
    its trajectory shows; the attempts named. An attempt whose outcome does not read is named and not counted. In a
    series whose scored set reached its target (`last_scored`, the attempt number of its last run), an attempt
    numbered after that is left out and named (`after_scored_set`): valid, it would have been played out and not
    scored. An attempt left out because its end row came at or after its wall-budget deadline is named
    (`end_row_after_budget`), and one left out because that time, its start or its budget does not read
    (`end_row_budget_unread`)."""
    late = sorted(r["run"] for r in rows if _stop_class_only(r) and r.get("end_row_budget") == "wall_budget")
    unread = sorted(r["run"] for r in rows if _stop_class_only(r) and r.get("end_row_budget") is None)
    after = sorted(r["run"] for r in rows if stopped_after_end(r) and last_scored is not None
                   and isinstance(r.get("attempt"), int) and r["attempt"] > last_scored)
    hit = sorted((r for r in rows if stopped_after_end(r) and r["run"] not in after),
                 key=lambda r: (r.get("attempt") or 0, r["run"]))
    read = [r for r in hit if r.get("success") is not None]
    k = s["k"] + sum(1 for r in read if r.get("success") is True)
    n = s["n"] + len(read)
    ci = wilson(k, n)
    return {"k": k, "n": n, "wilson95": list(ci) if ci else None, "attempts": [r["run"] for r in hit],
            "outcome_unread": [r["run"] for r in hit if r.get("success") is None],
            "reasons": list(STOPPED_AFTER_END), "scored_k_n": [s["k"], s["n"]],
            "end_row_after_budget": late, "end_row_budget_unread": unread, "after_scored_set": after}


def _text(p: Path) -> str:
    """An operator's or a driver's record (any text), its first 300 characters; one that cannot be read says so."""
    try:
        return p.read_text(encoding="utf-8", errors="replace").strip()[:300]
    except OSError as e:
        return f"unread: {type(e).__name__}"


def _read_run(d: Path) -> dict:
    row: dict[str, Any] = {"run": d.name}
    rr = resource_rule(d)            # FIRST, from recorded evidence only: the outcome is not read yet
    row["resource"] = rr
    tp = trajectory_path(d)
    if tp is None:
        row.update(success=None, success_why="absent: no trajectory.jsonl", valid=False,
                   invalid_reasons=["no_trajectory"])
        return row
    recs = _rows(tp)
    head = next((r for r in recs if r.get("kind") == "header"), None)
    steps = [r for r in recs if r.get("kind") == "step"]
    endr = next((r for r in recs if r.get("kind") == "end"), None)
    hs = (head or {}).get("seats")
    seats = list(hs) if isinstance(hs, dict) else [s.get("name") for s in hs or [] if isinstance(s, dict)]
    if not seats and steps:
        seats = list(steps[-1].get("order") or [])
    seat = seats[0] if len(seats) == 1 else None
    row["seat"] = seat
    if seat is None:
        row.update(success=None, success_why=f"unread: the run has {len(seats)} seats, this scores one")
    goal = _d(_d(head).get("scenario")).get("goal")
    row["goal"] = goal
    term = None
    for st in steps:
        info = _d(_d(st.get("infos")).get(seat)) if seat else {}
        if info.get("termination_reason") or info.get("truncation_reason"):
            term = (st, info)
            break
    last = steps[-1] if steps else None
    if seat is not None and goal in ONCE_GOALS:
        if term is not None:
            row["termination_reason"] = term[1].get("termination_reason")
            row["truncation_reason"] = term[1].get("truncation_reason")
        row.update(once_outcome(recs, seat, d))
    elif seat is not None:
        if term is None:
            row.update(success=None, success_why="unread: no step carries a termination or truncation reason for "
                       "the seat (" + (f"the episode ended {endr.get('reason')!r} first" if endr is not None
                                       else "no end row: the run did not finish") + ")")
        else:
            st, info = term
            tr = info.get("termination_reason")
            row["termination_reason"] = tr
            row["truncation_reason"] = info.get("truncation_reason")
            row["success"] = tr == GOAL
            row["success_why"] = f"termination_reason={tr!r}" if tr else \
                f"truncation_reason={info.get('truncation_reason')!r}"
            if tr == GOAL:
                row["success_date"] = st.get("game_date")
                row["success_step"] = st.get("step")
                row["created"] = created_by(info.get("end"))
                e = info.get("end") if isinstance(info.get("end"), dict) else {}
                row["creator"] = creator(e.get("created"), e.get("your_calls_naming_it"), d)
    if last is not None and seat is not None:
        vals = _d(_d(_d(last.get("metrics")).get(seat)).get("values"))
        row["last_date"] = last.get("game_date")
        row["counties_last"] = vals.get("dejure_counties", "absent: no metrics.values.dejure_counties")
    if endr is not None and isinstance(endr.get("steps"), int):
        row["steps"], row["steps_from"] = endr["steps"], "end row"
    else:
        row["steps"], row["steps_from"] = len(steps), "step rows counted (no end row)"
    row["end_reason"] = (endr or {}).get("reason")                        # shown, never scored
    acc = _json(d / "accounting.json")
    if acc is None:
        row["cost_usd"] = None
        row["cost_why"] = "absent: no accounting.json (`accounting RUN_DIR` did not run on this dir)"
    elif not isinstance(acc.get("cost_usd_reported"), (int, float)):
        row["cost_usd"] = None
        row["cost_why"] = f"absent: accounting.json has no cost_usd_reported ({acc.get('cost_why', 'an older accounting.json')})"
    else:
        row["cost_usd"] = acc["cost_usd_reported"]
    rj, rj_state = read_run_json(d)
    ej = _json(d / "end.json")
    if rj is not None and isinstance((rj.get("end") or {}).get("wall_s"), (int, float)):
        row["wall_s"], row["wall_from"] = rj["end"]["wall_s"], "run.json"
    elif ej is not None and isinstance(ej.get("wall_s"), (int, float)):
        row["wall_s"], row["wall_from"] = ej["wall_s"], "end.json (runner)"
    else:
        row["wall_s"], row["wall_from"] = None, "absent"
    if rj is None:
        row["valid"], row["invalid_reasons"] = False, (["no_run_json"] if rj_state == "absent"
                                                       else [run_json_damage(rj_state)])
    else:
        v = _d(rj.get("validity"))
        row["valid"] = v.get("valid") is True
        rs = v.get("reasons") if isinstance(v.get("reasons"), list) else []
        row["invalid_reasons"] = [str(x) for x in rs] or ([] if row["valid"] else ["validity_absent"])
        row["kind"] = rj.get("kind")
        cv = ((rj.get("start") or {}).get("claude_version") if isinstance(rj.get("start"), dict) else None)
        row["seated"] = None if not isinstance(cv, dict) else "not_applicable" not in cv
        if isinstance(rj.get("series"), dict):
            row.update(series=rj["series"].get("id"), attempt=rj["series"].get("attempt"),
                       valid_target=rj["series"].get("valid_target"), host=rj.get("host"))
        if rj.get("kind") != "rate" and row["valid"]:
            row["valid"], row["invalid_reasons"] = False, [f"kind_{rj.get('kind')}_not_rate"]
    for x in owner_tree_reasons(owner_tree(d)):       # the protected directory, from its own digest files
        if x not in row["invalid_reasons"]:
            row["valid"] = False
            row["invalid_reasons"] = [y for y in row["invalid_reasons"] if y != "validity_absent"] + [x]
    if rr.get("reasons"):
        row["valid"] = False
        have = [x for x in row.get("invalid_reasons") or [] if x != "validity_absent"]
        row["invalid_reasons"] = have + [x for x in rr["reasons"] if x not in have]
    cf = d / CANCELLED_FILE
    if cf.is_file():           # the operator cancelled the series mid-attempt: not a failure, not a void, not an attempt
        row["valid"] = False
        row["cancelled"] = _text(cf)
        row["invalid_reasons"] = [CANCELLED] + [x for x in row.get("invalid_reasons") or [] if x != CANCELLED]
    wd = d / WITHDRAWN_FILE
    if wd.is_file():           # the operator withdrew the host: not a failure, not a resource void
        row["valid"] = False
        row["withdrawn"] = _text(wd)
        row["invalid_reasons"] = [WITHDRAWN] + [x for x in row.get("invalid_reasons") or [] if x != WITHDRAWN]
    return row


#: the operator's record that the run's host was withdrawn mid-attempt (RUN_DIR/operator_withdrawn.txt, any text):
#: the run is invalid with this code and does NOT count toward the attempt cap
WITHDRAWN_FILE = "operator_withdrawn.txt"
WITHDRAWN = "operator_withdrew_host"
#: the operator's record that the series was cancelled while this attempt played (RUN_DIR/operator_stopped.txt, any
#: text): the same standing as a withdrawal (invalid, not an attempt)
CANCELLED_FILE = "operator_stopped.txt"
CANCELLED = "operator_cancelled_series"
NOT_ATTEMPTS = (WITHDRAWN, CANCELLED)
#: the reference driver's record that it refused a start (RUN_DIR/start_refused.txt, the refusal lines): the start
#: step refused (exit 6) or failed before the game, so no game ran and no ledger entry was written. The driver moves
#: such a directory out of the slot's place (REFUSED_DIR under RATE_RUNS), so the slot can be retried; scored, it is
#: invalid with this code and is not an attempt, and the stopping rule never counts it
START_REFUSED_FILE = "start_refused.txt"
START_REFUSED = "start_refused"
REFUSED_DIR = "refused-starts"

#: the protected directory's digests (the reference driver's protected_digest: OUT/own.before.* and OUT/own.after.*)
OWNER_TREE_DIFFERS = "owner_tree_differs"
OWNER_TREE_ABSENT = "witness_absent:owner_tree"


def owner_tree(d: Path) -> dict:
    """The protected directory (RATE_PROTECTED_DIR) as the run's digest files record it: the two summary lines and a
    verdict. `IDENTICAL` (both .list and both .sha byte-equal), `DIFFERS`, `not_configured` (the driver was given no
    directory: own.before.txt says so), `not_taken` (no digest file at all: a driver that takes none), `unwitnessed`
    (a directory was digested before and a file of the pair is missing, or a digest could not be taken)."""
    d = Path(d)
    own: dict[str, Any] = {}
    for tag in ("before", "after"):
        t = d / f"own.{tag}.txt"
        own[tag] = t.read_text(encoding="utf-8", errors="replace").strip() if t.is_file() else "absent"
    lists = [d / f"own.{t}.{x}" for t in ("before", "after") for x in ("list", "sha")]
    if own["before"] == "absent" and own["after"] == "absent" and not any(p.is_file() for p in lists):
        v = "not_taken"
    elif own["before"].startswith("not configured") and not any(p.is_file() for p in lists):
        v = "not_configured"
    elif not all(p.is_file() for p in lists):
        v = "unwitnessed"
    elif lists[0].read_bytes() == lists[2].read_bytes() and lists[1].read_bytes() == lists[3].read_bytes():
        v = "IDENTICAL"
    else:
        v = "DIFFERS"
    return {**own, "verdict": v}


def owner_tree_reasons(ot: dict) -> list[str]:
    """The validity reasons of a protected-directory verdict (owner_tree): a difference is `owner_tree_differs`, a
    digest begun and not completed is `witness_absent:owner_tree`; IDENTICAL, not_configured and not_taken are none."""
    return {"DIFFERS": [OWNER_TREE_DIFFERS], "unwitnessed": [OWNER_TREE_ABSENT]}.get(ot.get("verdict"), [])


# ── a series: the schedule, the stopping rule ────────────────────────────
SERIES_SCHEMA = "ck3bench.series.schedule/1"
SERIES_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,40}$")
#: a host's label in a schedule's pattern (and the driver's --host): one letter, or a letter and a digit (a, b, h1).
#: It stands in every run id, run.json and the published copies, so it is a label, never a machine's name
HOST_LABEL = re.compile(r"^[a-z][0-9]?$")


#: the deepest nesting an expectation or a schedule may have; theirs is a handful of levels. The bound is counted in
#: the text before it is parsed, so it is the same whichever step reads the file and however deep that step's own
#: calls are (the interpreter's recursion limit is not: a file one step reads, a step with more calls above it cannot)
JSON_DEPTH_MAX = 32


class TooDeep(ValueError):
    """A JSON text nested past JSON_DEPTH_MAX."""


def json_depth(text: str) -> int:
    """The deepest nesting of objects and arrays in a JSON text, counted over its brackets outside strings; a string
    is stepped over by the json module's own scanner, which raises (a ValueError) on one that does not end."""
    depth = deepest = i = 0
    n = len(text)
    while i < n:
        c = text[i]
        i += 1
        if c == '"':
            _, i = json.decoder.scanstring(text, i)
        elif c in "[{":
            depth += 1
            deepest = max(deepest, depth)
        elif c in "]}":
            depth -= 1
    return deepest


def read_bounded_json(path: str | Path) -> Any:
    """The JSON value of a file an operator hands a step (an expectation, a schedule): TooDeep (a ValueError) past
    JSON_DEPTH_MAX, before anything is parsed."""
    text = Path(path).read_text(encoding="utf-8")
    d = json_depth(text)
    if d > JSON_DEPTH_MAX:
        raise TooDeep(f"{path}: JSON nested {d} deep (at most {JSON_DEPTH_MAX})")
    return json.loads(text)


def load_series(path: str | Path) -> dict:
    """The frozen schedule: {schema, series, task, pattern [host, ...], valid_target, attempt_cap}; attempt a (from 1)
    is played on pattern[(a - 1) % len(pattern)], so the hosts alternate and a replacement lands on the same host's
    next slot. Refuses (ValueError) anything else."""
    try:
        d = read_bounded_json(path)
    except TooDeep:
        d = None                                     # nested too deeply: not a schedule, refused below
    ok = (isinstance(d, dict) and d.get("schema") == SERIES_SCHEMA and isinstance(d.get("series"), str)
          and SERIES_ID.match(d["series"]) and isinstance(d.get("task"), str)
          and isinstance(d.get("pattern"), list) and d["pattern"]
          and all(isinstance(h, str) and HOST_LABEL.match(h) for h in d["pattern"])
          and all(isinstance(d.get(k), int) and not isinstance(d.get(k), bool) and d[k] > 0
                  for k in ("valid_target", "attempt_cap")) and d["attempt_cap"] >= d["valid_target"])
    if not ok:
        raise ValueError(f"{path}: not a {SERIES_SCHEMA} schedule (series, task, pattern of host labels "
                         f"{HOST_LABEL.pattern}, valid_target <= attempt_cap)")
    return d


def slot_host(series: dict, attempt: int) -> str:
    return series["pattern"][(attempt - 1) % len(series["pattern"])]


def run_id_for(series: dict, attempt: int) -> str:
    return f"{series['series']}-a{attempt:02d}-{slot_host(series, attempt)}"


#: a series attempt's run id (run_id_for), which is its run directory's name
SERIES_RUN_ID = re.compile(r"^(?P<series>[a-z0-9][a-z0-9-]{0,40})-a(?P<attempt>\d{2,})-(?P<host>[a-z][0-9]?)$")


def parse_run_id(name: str) -> Optional[dict]:
    """{series, attempt, host} of a series attempt's run id (a run directory's name), or None for another name."""
    m = SERIES_RUN_ID.match(name)
    return None if m is None else {"series": m["series"], "attempt": int(m["attempt"]), "host": m["host"]}


def standing(e: dict) -> str:
    """THE standing of one attempt, from an entry {ended, valid, not_attempt} (a ledger entry as `series start`,
    `series end`, `series withdraw` and `series close` write it, or validity_only's reading of its run directory):
    `not_attempt` (withdrawn, cancelled, or a refused start: never counted, never valid), `in_flight` (not ended),
    `valid` or `invalid`. The stopping rule (stop_state: the slot and the scorer's gate), the scorer's ledger check and
    `series close` read it, and nothing else decides whether an attempt counts."""
    if e.get("not_attempt"):
        return "not_attempt"
    if not e.get("ended"):
        return "in_flight"
    return "valid" if e.get("valid") is True else "invalid"


def stop_state(series: dict, entries: list[dict]) -> dict:
    """The stopping rule over attempt entries (standing): stopped at `valid_target` valid runs or `attempt_cap`
    counted attempts (an attempt the operator withdrew or the series' cancellation is not one). Validity only: no
    outcome is read here."""
    st = [(e, standing(e)) for e in entries]
    counted = [e for e, s in st if s != "not_attempt"]
    valid = [e for e, s in st if s == "valid"]
    reached = ("valid_target" if len(valid) >= series["valid_target"] else
               "attempt_cap" if len(counted) >= series["attempt_cap"] else None)
    return {"valid": len(valid), "attempts": len(counted), "valid_target": series["valid_target"],
            "attempt_cap": series["attempt_cap"], "stop": reached,
            "in_flight": sorted(e["attempt"] for e, s in st if s == "in_flight")}


#: the end marker the series driver writes last (ck3bench.harness.driver END_MARKER); `series close` writes it for an
#: attempt whose driver stopped after `series end`
END_MARKER = "rate_run.done"


#: the driver's record that the runner returned to it (written before `series end`; docs/harness.md, the driver's steps)
DONE_FILE = "done"
#: the runner's end reason when a signal stopped it (an operator's stop, a host shutting down, or a driver's TERM at
#: its wall limit, which the driver's `done` then follows)
SIGNAL_END = "stopped_by_signal"


#: the end row reasons the env writes when it is closed before its episode's end (env.close, a reset): no end of
#: the episode itself
NOT_PLAYED_END = ("closed", "abandoned_by_reset")


def trajectory_end_row(d: Path) -> Optional[dict]:
    """The env's end row in the trajectory when it records the episode's own end (a `kind: end` row that reads whole,
    whose `reason` is text and not one of NOT_PLAYED_END), else None. A cut last line is not JSON and is no end row
    (read_jsonl); a row that reads is one, whatever follows it. Never raises."""
    tp = trajectory_path(Path(d))
    if tp is None:
        return None
    for r in read_jsonl(tp)[0]:
        if r.get("kind") == "end" and isinstance(r.get("reason"), str) and r["reason"] not in NOT_PLAYED_END:
            return r
    return None


def trajectory_end(d: Path) -> Optional[str]:
    """The reason of the env's end row (trajectory_end_row), else None. Never raises."""
    r = trajectory_end_row(d)
    return r["reason"] if r is not None else None


def played_out(d: Path) -> Optional[str]:
    """What records that an attempt's game was played out, or None when nothing does. Played out: the trajectory
    holds the env's end row (trajectory_end: the episode's outcome is fixed, whatever happened to the runner and the
    driver after it, a kill or a stop in the post-episode hold included), its driver wrote `done` (the runner
    returned to it), its runner wrote `end.json` with an end of its own (any end reason but `stopped_by_signal`; an
    `end.json` that does not read counts, so damage never makes an attempt withdrawable; the runner writes it before
    it closes the session and the game, `final: false` until both are closed, so an end the runner decided counts
    when its host goes down while it closes them and the env's end row reads `closed`), or `series end` has begun
    (bridge_title_witness.json, run.json's validity, result.json, the end marker). An attempt played out is ended by
    `series end` (then `series close` when its driver stopped); one not played out (its runner and its driver stopped
    from outside before the end row: the operator's stop, or its host going down) is withdrawn by `series
    withdraw`. `series end`, `series withdraw` and the driver's message all read this one function."""
    d = Path(d)
    for name in (END_MARKER, "result.json", "bridge_title_witness.json", DONE_FILE):
        if (d / name).is_file():
            return name
    end = trajectory_end(d)
    if end is not None:
        return f"the trajectory's end row (reason {end[:60]})"
    rj, _ = read_run_json(d)
    if "validity" in (rj or {}):
        return "run.json validity"
    if (d / "end.json").exists():
        ej, why = read_object(d / "end.json")
        er = (ej or {}).get("end_reason")
        if why or not isinstance(er, str) or er.split(":")[0] != SIGNAL_END:
            return "end.json" + (f" (it does not read: {why})" if why else f" (end reason {str(er)[:60]})")
    return None


def file_reasons(d: Path) -> list[str]:
    """The run files the kit reads for an attempt's validity or outcome that are absent (`file_absent:<file>`: the
    trajectory, `end.json`) or do not read whole, each `file_unreadable:<file>`: the trajectory (read_jsonl: a line that is not JSON, a NUL-filled tail, a JSON line
    that is not an object, not UTF-8, a `.gz` that does not decompress), `end.json`, `run.json` and `result.json`
    when present and not one JSON object (read_object), `end.json` whose `end_reason` is not text or whose
    `notes_witness` is not an object (`file_unreadable:end.json`), and `run.json`'s `launch_utc` when it is not a UTC
    time (`file_unreadable:run.json:launch_utc`). The transcript and the turns file are read by the resource rule
    only, which says when they do not read (`resource_evidence_absent:transcript|window`). Never raises."""
    d = Path(d)
    out = []
    tp = trajectory_path(d)
    if tp is None:
        out.append(f"{FILE_ABSENT}:trajectory.jsonl")
    else:
        rows, why = read_jsonl(tp)
        if why or not rows:            # a trajectory with no row (an empty file) did not read either
            out.append(f"{FILE_UNREADABLE}:{tp.name}")
    if not (d / "end.json").exists():
        out.append(f"{FILE_ABSENT}:end.json")
    objs = {}
    for name in ("end.json", "run.json", "result.json"):
        objs[name], why = read_object(d / name)
        if not why and name == "run.json" and objs[name] is not None and run_json_problem(objs[name]):
            objs[name], why = None, "a field is not of its type"
        if why:
            out.append(f"{FILE_UNREADABLE}:{name}")
    ej = objs["end.json"]
    if ej is not None and (not isinstance(ej.get("end_reason", ""), (str, type(None)))
                           or not isinstance(ej.get("notes_witness", {}), (dict, type(None)))):
        out.append(f"{FILE_UNREADABLE}:end.json")
    rj = objs["run.json"]
    if rj is not None and "launch_utc" in rj and _utc(rj.get("launch_utc")) is None:
        out.append(f"{FILE_UNREADABLE}:run.json:launch_utc")
    return out


def attempt_reasons(d: Path) -> list[str]:
    """THE validity reasons of a run beyond `series end`'s identity check, from its recorded files only (no outcome):
    a series attempt that is not kind `rate`, the protected directory (owner_tree), the resource rule, and the run
    files that do not read whole (file_reasons). One function, three readers: `series end` adds them to run.json
    `validity` and to the ledger entry it writes (which the slot's stopping rule reads), and the scorer reads them
    again and refuses a series whose record disagrees. Never raises."""
    d = Path(d)
    rj = read_run_json(d)[0] or {}
    out = []
    if isinstance(rj.get("series"), dict) and rj.get("kind") != "rate":
        out.append(f"kind_{rj.get('kind')}_not_rate")
    out += owner_tree_reasons(owner_tree(d))
    out += [x for x in resource_rule(d).get("reasons") or [] if x not in out]
    out += [x for x in file_reasons(d) if x not in out]
    return out


def validity_only(d: Path) -> dict:
    """A run's standing in its series WITHOUT its outcome: run.json's series and validity (as `series end` wrote it),
    attempt_reasons read again, the operator's withdrawal or cancellation records. `disagrees` lists what the record
    and this reading do not share (a reason one has and the other not); a series with any is refused."""
    rj, rj_state = read_run_json(Path(d))
    rj = rj or {}
    ser = rj.get("series")
    if ser is not None and not isinstance(ser.get("attempt"), int) and not (Path(d) / START_REFUSED_FILE).is_file():
        rj, ser, rj_state = {}, None, "unreadable: `series.attempt` is not a whole number"
    v = rj.get("validity")
    refused = (Path(d) / START_REFUSED_FILE).is_file()
    na = refused or (Path(d) / WITHDRAWN_FILE).is_file() or (Path(d) / CANCELLED_FILE).is_file()
    # a refused start, a withdrawal and a cancellation are final as they are: none of them waits for an end step
    ended = na or (v is not None and (Path(d) / END_MARKER).is_file())
    valid, disagrees = None, []
    operator = not refused and na
    if operator and v is not None:
        # `series withdraw` refuses an attempt `series end` has reached: a withdrawal or cancellation record beside
        # its validity was put there by hand, and an ended attempt stands as its end step wrote it
        disagrees.append("a withdrawal or cancellation record in an attempt `series end` ended")
    if na:                     # not an attempt: never a valid run
        valid = False
    elif v is not None:
        now_r = attempt_reasons(Path(d))
        rec = [str(x) for x in v.get("reasons") or []]
        # the record must hold every reason read now, and every reason of the kind read now that it recorded must
        # still be read (evidence that changed after `series end`)
        disagrees = ([f"read now, not recorded: {x}" for x in now_r if x not in rec]
                     + [f"recorded, not read now: {x}" for x in rec if _shared_kind(x) and x not in now_r])
        if (v.get("valid") is True) != (not rec):
            disagrees.append(f"run.json validity says valid={v.get('valid')!r} with reasons {rec[:4]}")
        valid = v.get("valid") is True and rj.get("kind") == "rate" and not now_r
        if not ended:
            valid = None
    ex = rj.get("expectation") if isinstance(rj.get("expectation"), dict) else {}
    return {"series": ser, "attempt": (ser or {}).get("attempt"), "host": rj.get("host"),
            "run_id": rj.get("run_id") or Path(d).name, "expectation_sha256": ex.get("sha256"),
            "ended": ended, "valid": valid, "not_attempt": na, "start_refused": refused,
            "has_validity": v is not None, "recorded_valid": None if v is None else v.get("valid") is True,
            "recorded_reasons": None if v is None else [str(x) for x in v.get("reasons") or []],
            "disagrees": disagrees, "identity": run_identity(rj), "run_json": rj_state,
            "records_flushed": rj.get("records_flushed")}


def _shared_kind(reason: str) -> bool:
    """Whether a validity reason is one attempt_reasons produces (the others are `series end`'s identity check)."""
    return (reason.startswith(("resource_limited:", "resource_evidence_absent:", "kind_", FILE_UNREADABLE + ":", FILE_ABSENT + ":"))
            or reason in (OWNER_TREE_DIFFERS, OWNER_TREE_ABSENT))


#: the identity a scored series carries and a comparison holds equal (run.json: the plan's task, and the start
#: identity `series start` held to the expectation's declared values)
IDENTITY_KEYS = ("task", "start_save_sha256", "map_sha256", "game_build_id")
#: carried beside them, not held by the comparison (a control series starts no agent CLI; the kit is the same release)
IDENTITY_INFO_KEYS = ("git_commit", "wheel_members_sha256", "bridge_so_sha256")


def run_identity(rj: dict) -> dict:
    """The task and start identity of one run, from its run.json (`plan.scenario` and `start`); absent says so."""
    st = rj.get("start") if isinstance(rj.get("start"), dict) else {}

    def g(*path):
        x: Any = st
        for k in path:
            x = x.get(k) if isinstance(x, dict) else None
        return x if isinstance(x, str) else None      # a value of another type is no identity (read_run_json refuses it)
    return {"task": (rj.get("plan") or {}).get("scenario") if isinstance(rj.get("plan"), dict) else None,
            "start_save_sha256": g("start_save", "sha256") or ("none: the task names no start save"
                                                                if g("start_save", "none") else None),
            "map_sha256": g("map", "sha256"),
            "game_build_id": g("game_build_id", "build_id"), "git_commit": g("git", "commit"),
            "wheel_members_sha256": g("wheel", "compare", "wheel_members_sha256"),
            "bridge_so_sha256": g("bridge_so", "sha256")}


def series_identity(ents: list[dict]) -> dict:
    """One value per identity key over a series' attempts, or {"differs": [...]} / {"absent": run ids}."""
    out: dict[str, Any] = {}
    for k in IDENTITY_KEYS + IDENTITY_INFO_KEYS:
        vals = {(e.get("identity") or {}).get(k) for e in ents}
        if None in vals:
            out[k] = {"absent": sorted(e["run_id"] for e in ents if (e.get("identity") or {}).get(k) is None)[:5]}
        elif len(vals) == 1:
            out[k] = vals.pop()
        else:
            out[k] = {"differs": sorted(vals)}
    return out


#: a file a ledger directory holds for an attempt: attempt-N.json in any spelling of the number (ledger_number reads
#: which are the canonical name `series start` writes)
LEDGER_ENTRY = re.compile(r"^attempt-(\d+)\.json$")
#: how far one host's clock may step back: a ledger entry's `started_utc` may be this much earlier than the entry of
#: the same host's attempt before it (its own clock; the hosts' clocks are never compared with each other: across
#: hosts the order is the attempt number, which the slot gives)
LEDGER_CLOCK_SKEW_S = 300
#: the fields of a ledger entry and the type each must have when present (`series start`, `series end`, `series
#: withdraw` and `series close` write them): a field of another type is damage, never a value
LEDGER_TYPES = {"attempt": int, "host": str, "run_id": str, "started_utc": str, "ended": bool, "valid": bool,
                "not_attempt": bool, "reasons": list, "ended_utc": str, "expectation_sha256": str}


def ledger_name(n: int) -> str:
    """THE file name of attempt N's ledger entry, as `series start` writes it."""
    return f"attempt-{n:02d}.json"


def ledger_number(name: str) -> tuple[Optional[int], Optional[str]]:
    """THE reading of a ledger file's name, which the slot (series.ledger) and the scorer (read_ledger) share:
    (N, None) for ledger_name(N); (N, why) for another spelling of attempt N (`attempt-1.json`, `attempt-001.json`),
    which is refused by name, never read as a second entry; (None, None) for a file that is no ledger entry."""
    m = LEDGER_ENTRY.match(name)
    if not m:
        return None, None
    n = int(m.group(1))
    return (n, None) if name == ledger_name(n) else (n, f"not the name `series start` writes ({ledger_name(n)})")


def ledger_entry_problem(e: dict, n: int) -> Optional[str]:
    """Why one ledger entry (one JSON object) does not read as an entry, naming the field, or None: no whole-number
    `attempt` equal to its name's, or a field of LEDGER_TYPES of another type (`reasons` a list of text)."""
    if not _typed(e.get("attempt"), int) or e["attempt"] != n:
        return "no whole-number `attempt` equal to its name's"
    for k, t in LEDGER_TYPES.items():
        if k in e and not _typed(e[k], t):
            return f"`{k}` is a {type(e[k]).__name__}"
    if "reasons" in e and not all(isinstance(x, str) for x in e["reasons"]):
        return "`reasons` is not a list of text"
    return None


def ledger_files(d: Path) -> list[tuple[Path, Optional[dict], Optional[str]]]:
    """THE reading of a ledger directory, which every step and the scorer go through: every attempt file (ledger_number)
    as (path, entry, None), or (path, None, why) when it does not read as an entry (a name that is not the canonical
    one, not one JSON object, ledger_entry_problem). Never raises."""
    out = []
    try:
        files = sorted(f for f in Path(d).iterdir() if ledger_number(f.name)[0] is not None)
    except OSError:
        return []
    for f in files:
        n, why = ledger_number(f.name)
        e = None
        if why is None:
            e, why = read_object(f)
        if why is None:
            why = ledger_entry_problem(e, n) if e is not None else "absent"
        out.append((f, e if why is None else None, why))
    return out


def read_ledger(d: Path) -> list[dict]:
    """A series' ledger directory: every attempt entry (attempt-NN.json), as `series start` and `series end` wrote it;
    an entry that does not read is `{"attempt": NN, "unreadable": why, "file": name}` (the scorer refuses it by name:
    `series_ledger_unreadable`)."""
    return [e if e is not None else {"attempt": ledger_number(f.name)[0], "unreadable": why, "file": f.name}
            for f, e, why in ledger_files(d)]


def ledger_refusals(ents: list[dict], entries: list[dict]) -> list[str]:
    """The run dirs given against the ledger: every ledger attempt has its directory among them, every directory is a
    ledger attempt (by run id), and the directory's attempt number is the ledger's."""
    bad = [le for le in entries if le.get("unreadable")]
    if bad:
        return [f"series_ledger_unreadable: {le.get('file')} ({le['unreadable']}): restore it from the other host's "
                "copy of the ledger" for le in bad]
    have = {e["run_id"]: e for e in ents}
    out = []
    prev: dict = {}                 # per host label: its own clock's last start (hosts' clocks are not compared)
    for le in sorted(entries, key=lambda x: x["attempt"]):
        t = _utc(le.get("started_utc"))
        h = le.get("host")
        if t is None:
            out.append(f"series_ledger_unreadable: {ledger_name(le['attempt'])} has no `started_utc` UTC time "
                       "(`series start` writes one)")
        elif h in prev and t < prev[h][1] - LEDGER_CLOCK_SKEW_S:
            out.append(f"series_ledger_order: attempt {le['attempt']} on host {h} started {le.get('started_utc')}, "
                       f"more than {LEDGER_CLOCK_SKEW_S} s before attempt {prev[h][0]} on the same host started "
                       f"({prev[h][2]}): the ledger's order on that host disagrees with its start times")
        if t is not None:
            prev[h] = (le["attempt"], t, le.get("started_utc"))
        e = have.get(le.get("run_id"))
        if e is None:
            out.append(f"series_attempt_missing: attempt {le['attempt']} ({le.get('run_id')}) is in the ledger and no "
                       "RUN_DIR given is it")
        elif e["attempt"] != le["attempt"]:
            out.append(f"series_attempt_differs: {le.get('run_id')} is attempt {le['attempt']} in the ledger, "
                       f"{e['attempt']} in its run.json")
        elif bool(le.get("not_attempt")) != bool(e["not_attempt"]):
            # one standing (score.standing): the ledger and the run directory say the same of whether it is an attempt
            why = ("its run directory holds a withdrawal, cancellation or refused-start record and the ledger counts it"
                   + ("; `series withdraw RUN_DIR` closes its entry" if not le.get("ended") and not e["has_validity"]
                      else "") if e["not_attempt"] else
                   "the ledger says not an attempt and its run directory holds no withdrawal or cancellation record")
            out.append(f"series_ledger_disagrees: {e['run_id']}: {why}")
        elif not le.get("not_attempt") and (le.get("ended") or e["has_validity"]):
            # the ledger entry `series end` wrote (the slot's stopping rule reads it) against the run.json validity
            # it wrote beside it: one reading of the attempt, or no score
            if not le.get("ended") or not e["has_validity"]:
                out.append(f"series_ledger_disagrees: {e['run_id']}: the ledger says ended={bool(le.get('ended'))}, "
                           f"run.json {'has' if e['has_validity'] else 'has no'} validity"
                           + ("; `series close RUN_DIR` writes the entry from run.json's validity"
                              if e["has_validity"] and not le.get("ended") else ""))
            elif not isinstance(le.get("valid"), bool):
                out.append(f"series_ledger_disagrees: {e['run_id']}: the ledger entry records no validity")
            elif le["valid"] != e["recorded_valid"]:
                out.append(f"series_ledger_disagrees: {e['run_id']}: the ledger says valid={le['valid']}, run.json "
                           f"validity valid={e['recorded_valid']}")
            elif [str(x) for x in le.get("reasons") or []] != e["recorded_reasons"] or not isinstance(
                    le.get("reasons", []), list):
                out.append(f"series_ledger_disagrees: {e['run_id']}: the ledger's reasons are not run.json's validity "
                           "reasons")
    named = {le.get("run_id") for le in entries}
    out += [f"series_attempt_not_in_ledger: {e['run_id']} (attempt {e['attempt']})" for e in ents
            if e["run_id"] not in named]
    return out


def series_gate(dirs: list[Path], ledger_entries: Optional[list[dict]] = None) -> dict:
    """Whether these run dirs are a series and whether it has stopped (the stopping rule, validity only); `refused`
    lists what makes them no series' scorable set: more than one expectation, and (with the ledger's entries) an
    attempt the ledger names that is not among them or one it does not name."""
    # a refused start holds no slot (its retry plays the same attempt number): it is no part of the stopping rule,
    # unless the ledger names it (a driver stopped between the start step's ledger entry and its return): then it is
    # that attempt, not an attempt, and its entry is held to that like any other
    read = [validity_only(d) for d in dirs]
    # a run.json damaged after `series start` (docs/harness.md, "A driver stopped mid-attempt"): the attempt stands as
    # `series end` recorded it in its ledger entry (ended, invalid, its one reason the damage) beside its end marker,
    # which is then the attempt's record; any other entry for it leaves the directory refused by name below
    damaged = {}
    for e, d in zip(read, dirs):
        if e["run_json"] in ("read",) or e["start_refused"]:
            continue
        le = next((x for x in ledger_entries or [] if not x.get("unreadable") and x.get("run_id") == Path(d).name),
                  None)
        if le is not None and le.get("ended") is True and le.get("valid") is False and not le.get("not_attempt") \
                and le.get("reasons") == [run_json_damage(e["run_json"])] and (Path(d) / END_MARKER).is_file():
            e.update(attempt=le["attempt"], host=le.get("host"), expectation_sha256=le.get("expectation_sha256"),
                     ended=True, valid=False, not_attempt=False, has_validity=True, recorded_valid=False,
                     recorded_reasons=list(le["reasons"]), run_json="damaged", disagrees=[])
            damaged[Path(d).name] = {"attempt": le["attempt"], "host": le.get("host"), "reasons": list(le["reasons"])}
    # the ledger names a refused start only when no other directory given is that run id (a slot retried after a
    # refused start plays the same run id, and that retry is the attempt the ledger names)
    played = {e["run_id"] for e in read if not e["start_refused"]}
    named = {le.get("run_id") for le in ledger_entries or []} - played
    ents = [e for e in read if not e["start_refused"] or e["run_id"] in named]
    sers = {json.dumps({k: (e["series"] or {}).get(k) for k in ("id", "schedule_sha256")}, sort_keys=True,
                       default=str)
            for e in ents if e["series"] and e["run_json"] == "read"}
    if not sers:
        # no run.json of the set reads as a series attempt: a set whose run directories are named as series
        # attempts is refused by name, never scored as runs of no series
        named_ = sorted(e["run_id"] for e in ents if e["run_json"] not in ("read", "damaged") and parse_run_id(e["run_id"]))
        if named_:
            return {"series": parse_run_id(named_[0])["series"], "stopped": False,
                    "why": "no run.json of the set reads", "refused": [
                        f"series_run_json_unreadable: {n}: its run.json does not read, and no run.json given reads "
                        "as this series' attempt" for n in named_]}
        return {"series": None}
    s0_ = next(e["series"] for e in ents if e["series"] and e["run_json"] == "read")
    for e in ents:
        if e["run_json"] == "damaged":
            e["series"] = s0_
    # a directory among a series' whose run.json is missing or does not read: named, never "not one series"
    bad = [e for e in ents if e["run_json"] not in ("read", "damaged")]
    if bad:
        return {"series": next(e["series"]["id"] for e in ents if e["series"] and e["run_json"] == "read"),
                "stopped": False, "why": "a run directory's run.json is missing or does not read",
                "refused": [f"series_run_json_absent: {e['run_id']}: no run.json: when the ledger has no entry for it "
                            "(its driver stopped before `series start` wrote it), move the directory to "
                            "refused-starts/RUN_ID-UTC with start_refused.txt; when it has one, `series end RUN_DIR` "
                            "records it ended and invalid (docs/harness.md, \"A driver stopped mid-attempt\")"
                            if e["run_json"] == "absent" else
                            f"series_run_json_unreadable: {e['run_id']}: its run.json {e['run_json']}: `series end "
                            "RUN_DIR` records it ended and invalid when its ledger entry is open (docs/harness.md, "
                            "\"A driver stopped mid-attempt\")" for e in bad]}
    if len(sers) > 1 or any(e["series"] is None for e in ents):
        return {"series": "mixed", "stopped": False, "why": "the run dirs are not all of one series"}
    s0 = next(e["series"] for e in ents)
    refused = []
    shas = {e["expectation_sha256"] for e in ents}
    if None in shas:
        refused.append("series_expectation_unwitnessed: a run.json records no expectation sha256 ("
                       + ", ".join(sorted(e["run_id"] for e in ents if e["expectation_sha256"] is None)[:5]) + ")")
    elif len(shas) > 1:
        refused.append(f"series_expectation_differs: the attempts played {len(shas)} expectation files; a series plays "
                       "one")
    # CK3BENCH_TEST_NO_FSYNC is the kit's test-only switch (ck3bench.env.durable): an attempt started under it wrote
    # its records without flushing them to the disk, so it cannot stand in a measured series
    refused += [f"series_records_not_flushed: {e['run_id']}: its run.json records `records_flushed: false` (`series "
                "start` ran with the test-only switch CK3BENCH_TEST_NO_FSYNC=1 set); a measured series runs with it "
                "unset (docs/harness.md, \"Environment variables\")" for e in ents
                if e["run_json"] == "read" and e.get("records_flushed") is False]
    if ledger_entries is not None:
        refused += ledger_refusals(ents, ledger_entries)
    refused += [f"series_validity_disagrees: {e['run_id']}: {'; '.join(e['disagrees'])} (run.json validity, as "
                "`series end` wrote it, against the same rule read now)" for e in ents if e["disagrees"]]
    st = stop_state({"valid_target": s0["valid_target"], "attempt_cap": s0["attempt_cap"]}, ents)
    nums = [e["attempt"] for e in ents]
    ident = series_identity(ents)
    if len(set(nums)) != len(nums):
        return {"series": s0["id"], "stopped": False, "why": f"an attempt number repeats: {sorted(nums)}",
                "identity": ident, **st}
    stopped = st["stop"] is not None and not st["in_flight"]
    unmarked = sorted(e["attempt"] for e in ents if not e["ended"] and e["has_validity"])
    why = (f"stopped at the {st['stop']}" if stopped else
           f"runs still playing: attempts {st['in_flight']}" if st["stop"] else
           f"not stopped: {st['valid']} valid of {st['valid_target']}, {st['attempts']} attempts of {st['attempt_cap']}")
    if unmarked and not stopped:
        why += (f"; attempts {unmarked} have their validity and no end marker ({END_MARKER}): their driver stopped "
                "after `series end`; `python -m ck3bench.harness series close RUN_DIR` closes each")
    return {"series": s0["id"], "stopped": stopped, "why": why, "refused": refused, "identity": ident,
            "damaged": damaged, **st}


def score(rows: list[dict]) -> dict:
    after, last = [], None
    if rows and all(isinstance(r.get("attempt"), int) for r in rows) and rows[0].get("valid_target"):
        # a series: the scored set is the first `valid_target` valid runs by attempt number; a later valid run
        # (one still playing when the target landed) is reported, not scored
        target, seen = rows[0]["valid_target"], 0
        for r in sorted(rows, key=lambda x: x["attempt"]):
            if r.get("valid"):
                seen += 1
                if seen == target:
                    last = r["attempt"]
                if seen > target:
                    after.append(r["run"])
    rows_in = [r for r in rows if r["run"] not in after]
    out = _score(rows_in)
    out["played_out_not_scored"] = after
    out["attempts"] = _score(rows)["attempts"]         # a played-out run is an attempt, scored or not
    out["sensitivity_stopped_after_end"] = sensitivity(rows, out, last)
    return out


def _score(rows: list[dict]) -> dict:
    valid = [r for r in rows if r.get("valid")]
    unread = [r for r in valid if r.get("success") is None]
    n = len(valid) - len(unread)
    k = sum(1 for r in valid if r.get("success") is True)
    ci = wilson(k, n)
    return {"k": k, "n": n, "valid": len(valid), "valid_but_unread": [r["run"] for r in unread],
            "invalid": [{"run": r["run"], "reasons": r.get("invalid_reasons")} for r in rows if not r.get("valid")],
            "wilson95": list(ci) if ci else None,
            "attempts": sum(1 for r in rows if not set(NOT_ATTEMPTS + (START_REFUSED,)) & set(r.get("invalid_reasons") or [])),
            "withdrawn_not_attempts": sorted(r["run"] for r in rows
                                             if set(NOT_ATTEMPTS) & set(r.get("invalid_reasons") or [])),
            "refused_starts_not_attempts": sorted(r["run"] for r in rows
                                                  if START_REFUSED in (r.get("invalid_reasons") or [])),
            "voided_for_limit_or_api_error": sorted(r["run"] for r in rows if any(
                x in ("resource_limited:rate_limit", "resource_limited:api_error") for x in r.get("invalid_reasons") or []))}


def _fmt(v, nd=2):
    if v is None:
        return "-"
    return f"{v:.{nd}f}" if isinstance(v, float) else str(v)


def voided_for_absent_evidence(rows: list[dict]) -> list[str]:
    """The runs the resource rule voided because its evidence was absent or unreadable (a stop or hang with no
    transcript, no pressure.tsv or no stop window): never counted as failures, so k/n leaves them out."""
    return sorted(r["run"] for r in rows if any(str(x).startswith("resource_evidence_absent:")
                                                for x in r.get("invalid_reasons") or []))


def table(rows: list[dict], s: dict) -> str:
    out = [f"{'run':<44} {'success':<8} {'when / counties':<36} {'created_by':<18} {'steps':>5} {'cost':>8} "
           f"{'wall_s':>8} {'limits/api':>10}  valid"]
    for r in rows:
        if r.get("success") is True:
            c = r.get("created") or {}
            cd = r.get("creation_date") or {}
            when = (f"{r.get('success_date')} (created {cd.get('date')} [{cd.get('from')}])" if cd.get("date") else
                    f"{r.get('success_date')} (created {c.get('date') or 'date absent'})")
            who = (r.get("creator") or {}).get("who") or c.get("who", "-")
        else:
            when = f"{r.get('counties_last', '-')} at {r.get('last_date', '-')}"
            who = "-"
        succ = {True: "yes", False: "no", None: "unread"}[r.get("success")]
        val = "valid" if r.get("valid") else "INVALID " + ",".join(r.get("invalid_reasons") or [])
        tx = (r.get("resource") or {}).get("transcript") or {}
        la = f"{tx['whole_run']['limit_hits']}/{tx['whole_run']['api_errors']}" if "whole_run" in tx else tx.get("state", "-")
        out.append(f"{r['run'][:44]:<44} {succ:<8} {when[:36]:<36} {who:<18} {_fmt(r.get('steps')):>5} "
                   f"{_fmt(r.get('cost_usd')):>8} {_fmt(r.get('wall_s'), 0):>8} {la:>10}  {val}")
    ci = s["wilson95"]
    absent = voided_for_absent_evidence(rows)
    out.append(f"k/n = {s['k']}/{s['n']} valid runs ({s['attempts']} attempts, {len(s['invalid'])} invalid"
               + (f"; {len(s['withdrawn_not_attempts'])} withdrawn or cancelled by the operator, not attempts" if s["withdrawn_not_attempts"] else "")
               + (f"; {len(absent)} voided for absent resource evidence, not failures" if absent else "")
               + (f"; {len(s['refused_starts_not_attempts'])} refused at the start, not attempts"
                  if s.get("refused_starts_not_attempts") else "")
               + (f"; {len(s['played_out_not_scored'])} valid after the target, played out and not scored"
                  if s.get("played_out_not_scored") else "") + ")"
               + (f"; Wilson 95% [{ci[0]:.3f}, {ci[1]:.3f}]" if ci else "; no interval (n = 0)"))
    sv = s.get("sensitivity_stopped_after_end")
    if sv is not None:
        out.append(f"sensitivity: k'/n' = {sv['k']}/{sv['n']} with the {len(sv['attempts'])} attempts stopped after "
                   "their end row counted with their trajectory's outcome"
                   + (f" ({', '.join(sv['attempts'])})" if sv["attempts"] else "")
                   + (f"; outcome unread, not counted: {', '.join(sv['outcome_unread'])}" if sv["outcome_unread"] else "")
                   + (f"; left out, end row at or after the wall budget: {', '.join(sv['end_row_after_budget'])}"
                      if sv.get("end_row_after_budget") else "")
                   + (f"; left out, end row's budget unread: {', '.join(sv['end_row_budget_unread'])}"
                      if sv.get("end_row_budget_unread") else "")
                   + (f"; left out, numbered after the scored set: {', '.join(sv['after_scored_set'])}"
                      if sv.get("after_scored_set") else "")
                   + (f"; Wilson 95% [{sv['wilson95'][0]:.3f}, {sv['wilson95'][1]:.3f}]" if sv["wilson95"] else ""))
    if s["voided_for_limit_or_api_error"]:
        out.append("voided for a rate limit or an API error (not failures): "
                   f"{', '.join(s['voided_for_limit_or_api_error'])}")
    if s["valid_but_unread"]:
        out.append(f"valid but success unread (not counted): {', '.join(s['valid_but_unread'])}")
    return "\n".join(out)


def _log_override(dirs: list[str], gate: dict) -> None:
    import os
    line = (f"{dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')} ck3bench.harness score {OVERRIDE_FLAG}: "
            f"series {gate.get('series')} scored BEFORE its stop ({gate.get('why')}); runs {len(dirs)}")
    print(f"WARNING {line}", file=sys.stderr)
    log = os.environ.get("SCORE_RATE_OVERRIDE_LOG") or str(Path(dirs[0]).resolve().parent / "score_rate_overrides.log")
    with open(log, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def main(argv: list[str]) -> int:
    if any(a in ("-h", "--help") for a in argv):
        print(__doc__)                    # help reads no run directory (a directory named --help is not scored)
        return 0
    if argv[:1] == ["--compare"]:
        return _compare_main(argv[1:])
    as_json = "--json" in argv
    override = OVERRIDE_FLAG in argv
    ledger_arg = None
    if "--ledger" in argv:
        i = argv.index("--ledger")
        if i + 1 >= len(argv):
            print("REFUSE score: --ledger needs a directory", file=sys.stderr)
            return 2
        ledger_arg = argv[i + 1]
        argv = argv[:i] + argv[i + 2:]
    dirs = [a for a in argv if a not in ("--json", OVERRIDE_FLAG)]
    if not dirs:
        print(__doc__)
        return 2
    missing = [d for d in dirs if not Path(d).is_dir()]
    if missing:
        # a mistyped path is not an attempt: counting it would put an invalid run into the series' n
        for d in missing:
            print(f"REFUSE score: RUN_DIR {d!r} is not a directory (a run directory the driver made); nothing read",
                  file=sys.stderr)
        return 2
    gate = series_gate([Path(d) for d in dirs])
    if gate["series"] == "mixed":
        # a set that is part series, part not (or of two series) is no series' score, whatever the override
        print(f"REFUSE series_mixed: {gate['why']}; score one series' run dirs, or runs of no series; nothing read",
              file=sys.stderr)
        return 4
    if gate["series"] is not None:
        import os
        first = next((Path(d) for d in dirs if not (Path(d) / START_REFUSED_FILE).is_file()), Path(dirs[0]))
        led = Path(ledger_arg or os.environ.get("RATE_LEDGER") or first.resolve().parent / f"{gate['series']}.ledger")
        if not led.is_dir():
            print(f"REFUSE series_ledger_absent: series {gate['series']}: no ledger at {led} (--ledger DIR); a series is "
                  "scored against its ledger; nothing read", file=sys.stderr)
            return 4
        gate = series_gate([Path(d) for d in dirs], read_ledger(led))
    if gate.get("refused"):
        for x in gate["refused"]:
            print(f"REFUSE {x}; nothing read", file=sys.stderr)
        return 4
    if gate["series"] is not None and not gate.get("stopped"):
        if not override:
            print(f"REFUSE series_not_stopped: series {gate['series']}: {gate.get('why')}; nothing read", file=sys.stderr)
            return 4
        _log_override(dirs, gate)
    rows = [read_run(Path(d)) for d in dirs]
    for r, d in zip(rows, dirs):
        dm = (gate.get("damaged") or {}).get(Path(d).name)
        if dm is not None:
            # its record is its ledger entry: the attempt, the host, ended and invalid for its damaged run.json
            r.update(series=gate["series"], attempt=dm["attempt"], host=dm["host"], valid=False,
                     invalid_reasons=dm["reasons"], valid_target=gate.get("valid_target"))
    s = score(rows)
    gate["override"] = bool(override and gate["series"] is not None and not gate.get("stopped"))
    if as_json:
        print(json.dumps(publishable({"runs": rows, "score": s, "gate": gate}, names=private_names(dirs)), indent=1))
    else:
        if isinstance(gate.get("identity"), dict):
            print("identity: " + ", ".join(f"{k}={gate['identity'].get(k)}" for k in IDENTITY_KEYS + IDENTITY_INFO_KEYS))
        print(table(rows, s))
    return 0


#: an absolute path in a string (POSIX, two components or more): the score's output names files by basename
_ABS_PATH = re.compile(r"(?<![\w.~:/-])/(?:[^\s/'\"(),;:<>|]+/)+([^\s/'\"(),;:<>|]*)")
#: the bridge witness's keys the score's output keeps (the log's path and where it came from are the host's)
_BRIDGE_KEEP = ("state", "reason_code", "why", "lines", "commands", "completed")
_HELD_KEEP = ("checked", "held", "reason_code", "why", "game_pid", "inode", "held_inodes")


def private_names(dirs: list[str]) -> list[tuple[re.Pattern, str]]:
    """The host and user names `series publish` replaces (ck3bench.harness.publish: the machine names each run.json
    records and this host's, each also without its domain; this account's and the environment's user names, three
    characters or more), as (pattern, placeholder): the same rule for the score's free text (an operator's record,
    a reader's message)."""
    import os
    import socket
    from ck3bench.harness import publish
    hosts = {socket.gethostname()}
    for d in dirs:
        rj = read_run_json(Path(d))[0] or {}
        for part in ("start", "end"):
            h = (rj.get(part) or {}).get("host") if isinstance(rj.get(part), dict) else None
            if isinstance(h, str):
                hosts.add(h)
    names = {n for h in hosts if h and h.strip() for n in (h.strip(), h.strip().split(".")[0]) if len(n) >= 2}
    users = [u for u in publish.user_names(dict(os.environ), []) if len(u) >= 3 and u.lower() not in
             {n.lower() for n in names}]
    return ([(publish.host_rx(n), "<host name>") for n in sorted(names, key=len, reverse=True)]
            + [(publish.host_rx(u), "<user name>") for u in sorted(users, key=len, reverse=True)])


def publishable(doc: Any, names: Optional[list[tuple[re.Pattern, str]]] = None) -> Any:
    """The `--json` output as it can be published: the bridge witness without the log's path, every absolute path
    left in a string (an operator's refusal text, a reader's message) reduced to its basename, and in every string the
    host and user names NAMES gives (private_names: the rule `series publish` applies) replaced. A run is named by its
    directory's basename (`run`) only."""
    if isinstance(doc, dict):
        out = {}
        for k, v in doc.items():
            if k == "bridge" and isinstance(v, dict) and "state" in v:
                v = {kk: vv for kk, vv in v.items() if kk in _BRIDGE_KEEP}
                hb = doc["bridge"].get("held_by_game")
                if isinstance(hb, dict):
                    v["held_by_game"] = {kk: vv for kk, vv in hb.items() if kk in _HELD_KEEP}
            out[k] = publishable(v, names)
        return out
    if isinstance(doc, list):
        return [publishable(x, names) for x in doc]
    if isinstance(doc, str):
        doc = _ABS_PATH.sub(lambda m: m.group(1) or "<dir>", doc)
        for rx, ph in names or []:
            doc = rx.sub(ph, doc)
        return doc
    return doc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
