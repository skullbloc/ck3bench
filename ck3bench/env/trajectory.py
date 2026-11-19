"""The trajectory record: one JSONL file per episode.

Record kinds, in order: one `header`; then, per step, zero or more `call`
records (each tool call a seat made, in its turn or as its action) and one
`step` record; then one `end`. Every record carries `schema`, `kind`,
`episode_id`, `backend` and `fake` (True for any record of a backend that is
not the game), and `wall_utc`.

    header  scenario (the full declaration), seed, seats (name -> character id
            at the start), provenance (the backend's: versions, save sha, ...)
    call    step, seat, phase (`turn`: before the step, `action`: the step's
            own calls), call {tool_name, arguments}, result {tool_name, ok,
            result, error}, game_date, wall_s
    step    step, game_date (after the advance), order (the seats' execution
            order), actions, observations, rewards, terminations, truncations,
            infos, metrics, advance {reached, truncation, detail, wall_s,
            diagnostics {status, alarm_rearms, dropped_frames, detail}: the
            backend's own account of the advance, `reported`, `not_applicable`
            (the fake) or `not_reported`; optional},
            usage (per seat, only what a policy reported; absent is absent),
            turns (per seat, how its turn ended, when the driver reported it:
            an agent loop's typed turn end, a runner's timeout record),
            comms (when the scenario declares [comms]: the messages delivered
            and dropped at this step's end, the shared-pad writes applied and
            refused; comms.py states the rule)
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

from .types import DIAG_STATUSES

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
    record, so a run that dies leaves every record written before it."""

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
        self.records += 1
        return rec

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


def check_trajectory(records: Iterable[dict]) -> list[str]:
    """Every problem found, in order; [] when the file is well formed. Episodes
    may follow one another in one file (header ... end, header ... end)."""
    problems: list[str] = []
    state = "want_header"
    episode = None
    last_step = 0
    has_comms = False
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
            if has_comms and "comms" not in r:
                problems.append(f"record {i}: the scenario declares comms and step {r.get('step')} records none")
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
