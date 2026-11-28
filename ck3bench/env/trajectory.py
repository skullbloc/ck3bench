"""The trajectory record: one JSONL file per episode.

Record kinds, in order: one `header`; then, per step, zero or more `call`
records (each tool call a seat made, in its turn or as its action) and one
`step` record; then one `end`. Every record carries `schema`, `kind`,
`episode_id`, `backend` and `fake` (True for any record of a backend that is
not the game), and `wall_utc`.

    header  scenario (the full declaration), seed, seats (name -> character id
            at the start), provenance (the backend's: versions, save sha, ...;
            the game's: `toolkit` version and `notes`, each seat's shared notes
            as its own server's session(action="health") reported them,
            {state, version, notes} or {state: off} or {unread: why}, beside
            `notes_switch`, the CK3_NOTES the env passed its seats' servers),
            orders (the standing-orders setting, on or off: {enabled, why |
            max_orders, tools}; orders.py)
    call    step, seat, phase (`turn`: before the step, `action`: the step's
            own calls, `order`: a standing order the env sent at a check while
            the clock ran, with `order_id` and the check's game_date), call
            {tool_name, arguments}, result {tool_name, ok, result, error},
            game_date, wall_s
    step    step, game_date (after the advance), order (the seats' execution
            order), actions, observations, rewards, terminations, truncations,
            infos, metrics, advance {reached, truncation, detail, wall_s,
            diagnostics {status, alarm_rearms, dropped_frames, detail}: the
            backend's own account of the advance, `reported`, `not_applicable`
            (the fake) or `not_reported`; optional, speed {declared, read,
            reads, unread, status}: the scenario's game speed beside the
            speeds the clock loop read while the step's clock ran
            (types.speed_witness; `mismatch` is a problem the checker names);
            optional},
            usage (per seat, only what a policy reported; absent is absent),
            turns (per seat, how its turn ended, when the driver reported it:
            an agent loop's typed turn end, a runner's timeout record),
            comms (when the scenario declares [comms]: the messages delivered
            and dropped at this step's end, the shared-pad writes applied and
            refused; comms.py states the rule),
            wake (when the scenario declares [wake], a wake task: requests
            {seat: {declared, max_days, when}}, days (run this step),
            planned_days, checks, stop (condition | max_days | horizon |
            failure) and fired [{seat, index, condition, read, date, forced?}],
            forced (the task's forced wakes), probe (the real game's account of
            its checks: checks_running/paused, probe wall, unread, torn,
            stop_arms, fire_read_date, stop_date, lag_days), check_dates (when
            the scenario's wake declares aligned_checks and the step made
            checks: types.check_dates_witness, {mode, checks, status, off,
            rows [{intended, read, off}]}, recomputed from the rows by the
            checker, which names every row read off its date); a wake task's
            steps are of different lengths, so a reader takes a step's length
            from `wake.days` (`step_days`), never from cycle_days, and the
            checker holds each wake step's dates to its days)
            orders (when a seat set standing orders for the step: per seat,
            the records {order, outcome, date, ...}, outcome set | fired |
            blocked | expired | cancelled | dropped; orders.py)
            notes_pads (when the run keeps carried notes pads, env.py
            `notes_pad_bytes`: each seat's pad {text, version, bytes} at the
            step's end)
    end     reason (the first truncation or `all_seats_done`), steps, returns
            (per seat, the sum of its rewards; null if any was NaN)

FORMAT. Every record's `schema` is `ck3bench.trajectory/2`; a file of any
other schema is a problem `check_trajectory` reports. Each step's
`metrics[seat].source` names the read every value came from
(types.METRIC_DEFINITIONS defines each metric).

A NaN reward is written as null with `reward_status: unavailable` beside it in
the step's infos: JSON has no NaN, and a reader must not see a number.

`read_trajectory` reads a file back and `check_trajectory` checks the order
and the required keys; the CLI exposes it as `ck3bench env check`.
"""
from __future__ import annotations

import json
import math
import time
import uuid
from pathlib import Path
from typing import Any, Iterable, Optional, TextIO

from . import durable
from .types import DIAG_STATUSES, SPEED_MISMATCH, SPEED_STATUSES, check_dates_witness

SCHEMA = "ck3bench.trajectory/2"
KINDS = ("header", "call", "step", "end")
COMMON = ("schema", "kind", "episode_id", "backend", "fake", "wall_utc")
REQUIRED = {
    "header": ("scenario", "seed", "seats", "provenance"),
    "call": ("step", "seat", "phase", "call", "result", "game_date", "wall_s"),
    "step": ("step", "game_date", "order", "actions", "observations", "rewards", "terminations",
             "truncations", "infos", "metrics", "advance"),
    "end": ("reason", "steps", "returns"),
}


def _utc() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def json_safe(v: Any) -> Any:
    """NaN/inf -> None (JSON has none); everything else as is, recursively."""
    if isinstance(v, float) and not math.isfinite(v):
        return None
    if isinstance(v, dict):
        return {str(k): json_safe(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [json_safe(x) for x in v]
    return v


class TrajectoryWriter:
    """Appends records to one JSONL file (or any text stream). Flushed per
    record, so a run that dies leaves every record written before it. The end
    row is also flushed to the disk (with the file's directory) before write
    returns (durable.sync_file, unless the test-only switch is set): a power loss
    after it cannot lose it. The rows before it reach the disk in the kernel's
    own time."""

    def __init__(self, path: Optional[str | Path] = None, stream: Optional[TextIO] = None):
        if (path is None) == (stream is None):
            raise ValueError("give exactly one of path or stream")
        self.path = Path(path) if path is not None else None
        self._fh = stream if stream is not None else self.path.open("w", encoding="utf-8")
        self._own = stream is None
        self.episode_id: Optional[str] = None
        self.backend = "unknown"
        self.fake = True
        self.records = 0

    def begin(self, *, backend: str, fake: bool, episode_id: Optional[str] = None) -> str:
        self.episode_id = episode_id or uuid.uuid4().hex
        self.backend, self.fake = backend, bool(fake)
        return self.episode_id

    def write(self, kind: str, **fields: Any) -> dict:
        if kind not in KINDS:
            raise ValueError(f"unknown record kind {kind!r}")
        rec = {"schema": SCHEMA, "kind": kind, "episode_id": self.episode_id, "backend": self.backend,
               "fake": self.fake, "wall_utc": _utc(), **fields}
        self._fh.write(json.dumps(json_safe(rec), sort_keys=True, allow_nan=False) + "\n")
        self._fh.flush()
        if kind == "end":
            self.sync()
        self.records += 1
        return rec

    def sync(self) -> None:
        """Flush what is written to the disk: the file, and its directory (a stream with no file is left)."""
        try:
            durable.sync_file(self._fh)
        except (OSError, ValueError):       # a stream with no file descriptor (io.UnsupportedOperation is both)
            return
        if self.path is not None:
            durable.sync_dir(self.path.parent)

    def close(self) -> None:
        if self._own:
            self._fh.close()


def read_trajectory(path: str | Path) -> list[dict]:
    out = []
    for n, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        if line.strip():
            try:
                out.append(json.loads(line))
            except ValueError as e:
                raise ValueError(f"line {n}: {e}") from None
    return out


def _days(a: Any, b: Any) -> Optional[int]:
    from .calendar import ordinal
    oa, ob = ordinal(a), ordinal(b)
    return None if oa is None or ob is None else ob - oa


def step_days(header: dict, step: dict) -> int:
    """How many game-days one step record's clock ran: `wake.days` on a wake
    task (its steps are of different lengths), the declared `cycle_days` on a
    fixed one. A wake task's step without `wake.days` raises: no reader may
    take a wake step's length from cycle_days."""
    sc = header.get("scenario") or {}
    if "wake" in sc:
        w = step.get("wake")
        if not (isinstance(w, dict) and isinstance(w.get("days"), int)):
            raise ValueError(f"step {step.get('step')} of a wake task records no wake.days")
        return w["days"]
    return int(sc["cycle_days"])


def summary(records: Iterable[dict]) -> list[dict]:
    """Per episode: the scenario, its cadence, what the toolkit and its shared
    notes were (the header's provenance), and the game-days its steps ran
    (step_days: a wake task's from each step's wake.days)."""
    out: list[dict] = []
    head = None
    for r in records:
        if r.get("kind") == "header":
            head = r
            prov = r.get("provenance") or {}
            sc = r.get("scenario") or {}
            out.append({"scenario": sc.get("id"), "cadence": "wake" if "wake" in sc else "fixed",
                        "toolkit": prov.get("toolkit", "not recorded"),
                        "toolkit_identity": prov.get("toolkit_identity", "not recorded"),
                        "notes": prov.get("notes", "not recorded"),
                        "notes_switch": prov.get("notes_switch", "not recorded"),
                        "steps": 0, "days": 0, "days_by": "wake.days" if "wake" in sc else "cycle_days"})
        elif r.get("kind") == "step" and head is not None and out:
            out[-1]["steps"] += 1
            try:
                out[-1]["days"] += step_days(head, r)
            except (ValueError, KeyError, TypeError) as e:
                out[-1]["days"] = None
                out[-1]["days_problem"] = str(e)
                head = None
    return out


def _check_dates_problems(i: int, step: Any, w: dict, aligned: bool) -> list[str]:
    """An aligned wake step's `check_dates`, recomputed from its rows (types.check_dates_witness; the
    stored status is not trusted). Absent where checks were made is a problem; so is a row read off its date."""
    cd = w.get("check_dates")
    if not aligned:
        return [f"record {i}: step {step} records check_dates on a task whose checks are not aligned"] \
            if cd is not None else []
    if cd is None:
        n = w.get("checks")
        return [f"record {i}: an aligned task's step {step} made {n} checks and records no check_dates"] \
            if not (isinstance(n, int) and n == 0) else []
    rows = cd.get("rows") if isinstance(cd, dict) else None
    if not isinstance(rows, list) or not all(isinstance(x, dict) for x in rows):
        return [f"record {i}: step {step} check_dates carries no rows"]
    out = []
    again = check_dates_witness([(x.get("intended"), x.get("read")) for x in rows])
    if any(cd.get(k) != again[k] for k in ("status", "off", "checks", "rows")):
        out.append(f"record {i}: step {step} check_dates says {cd.get('status')!r} and its rows read "
                   f"{again['status']!r}")
    if w.get("checks") != len(rows):
        out.append(f"record {i}: step {step} made {w.get('checks')} checks and check_dates has {len(rows)} rows")
    for x in again["rows"]:
        if x["off"] != 0:
            how = "no date read" if x["off"] is None else f"{x['off']:+d} days"
            out.append(f"record {i}: step {step} check intended {x['intended']} was made at {x['read']} ({how})")
    return out


def check_trajectory(records: Iterable[dict]) -> list[str]:
    """Every problem found, in order; [] when the file is well formed. Episodes
    may follow one another in one file (header ... end, header ... end)."""
    problems: list[str] = []
    state = "want_header"
    episode = None
    last_step = 0
    has_comms = False
    has_wake = False
    aligned = False
    prev_date = None
    for i, r in enumerate(records, 1):
        missing = [k for k in COMMON if k not in r]
        if missing:
            problems.append(f"record {i}: missing {missing}")
            continue
        if r["schema"] != SCHEMA:
            problems.append(f"record {i}: schema {r['schema']!r}, want {SCHEMA!r}")
        kind = r["kind"]
        if kind not in KINDS:
            problems.append(f"record {i}: kind {kind!r}")
            continue
        miss = [k for k in REQUIRED[kind] if k not in r]
        if miss:
            problems.append(f"record {i} ({kind}): missing {miss}")
        if state == "want_header":
            if kind != "header":
                problems.append(f"record {i}: {kind} before a header")
            episode, last_step, state = r["episode_id"], 0, "in_episode"
            has_comms = isinstance(r.get("scenario"), dict) and "comms" in r["scenario"]
            has_wake = isinstance(r.get("scenario"), dict) and "wake" in r["scenario"]
            aligned = has_wake and isinstance(r["scenario"]["wake"], dict) \
                and r["scenario"]["wake"].get("aligned_checks") is True
            prev_date = ((r.get("scenario") or {}).get("start") or {}).get("date") if has_wake else None
            continue
        if r["episode_id"] != episode:
            problems.append(f"record {i}: episode_id changed inside an episode")
        if kind == "header":
            problems.append(f"record {i}: header inside an episode (no end)")
        elif kind == "step":
            if r.get("step") != last_step + 1:
                problems.append(f"record {i}: step {r.get('step')} after {last_step}")
            last_step = r.get("step", last_step)
            diag = (r.get("advance") or {}).get("diagnostics") if isinstance(r.get("advance"), dict) else None
            if diag is not None and (not isinstance(diag, dict) or diag.get("status") not in DIAG_STATUSES):
                problems.append(f"record {i}: step {r.get('step')} advance.diagnostics status "
                                f"{(diag or {}).get('status') if isinstance(diag, dict) else diag!r} not in {DIAG_STATUSES}")
            spd = (r.get("advance") or {}).get("speed") if isinstance(r.get("advance"), dict) else None
            if isinstance(spd, dict) and spd.get("status") == SPEED_MISMATCH:
                problems.append(f"record {i}: step {r.get('step')} ran at speed {spd.get('read')}, "
                                f"declared {spd.get('declared')}")
            elif spd is not None and (not isinstance(spd, dict) or spd.get("status") not in SPEED_STATUSES):
                problems.append(f"record {i}: step {r.get('step')} advance.speed status not in {SPEED_STATUSES}")
            if has_comms and "comms" not in r:
                problems.append(f"record {i}: the scenario declares comms and step {r.get('step')} records none")
            w = r.get("wake")
            if has_wake and not (isinstance(w, dict) and isinstance(w.get("days"), int)
                                 and w.get("stop") in ("condition", "max_days", "horizon", "failure")):
                problems.append(f"record {i}: a wake task's step {r.get('step')} records no wake days and stop")
            if not has_wake and "wake" in r:
                problems.append(f"record {i}: step {r.get('step')} records wake on a fixed-cadence task")
            if has_wake and isinstance(w, dict):
                problems += _check_dates_problems(i, r.get("step"), w, aligned)
            if has_wake and isinstance(w, dict) and isinstance(w.get("days"), int):
                # a wake step's length is its own: the date it ended on is the last one plus its days
                adv = r.get("advance") if isinstance(r.get("advance"), dict) else {}
                if adv.get("reached") and prev_date is not None and _days(prev_date, r.get("game_date")) != w["days"]:
                    problems.append(f"record {i}: step {r.get('step')} wake.days {w['days']} but {prev_date} -> "
                                    f"{r.get('game_date')} is {_days(prev_date, r.get('game_date'))} days")
                prev_date = r.get("game_date")
        elif kind == "call":
            if r.get("step") != last_step + 1:
                problems.append(f"record {i}: call for step {r.get('step')} while step {last_step + 1} is open")
        elif kind == "end":
            if r.get("steps") != last_step:
                problems.append(f"record {i}: end says {r.get('steps')} steps, {last_step} written")
            state = "want_header"
    if state != "want_header":
        problems.append("the file ends inside an episode (no end record)")
    return problems
