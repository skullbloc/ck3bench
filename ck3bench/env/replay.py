"""Re-judge a recorded episode under a task: the recorded metric reads, served back step by step through the
environment's own step (env.py), so the rewards and ends it gives are the env's, not a copy of its rules.

    rows = recorded_rows(read_trajectory("trajectory.jsonl"))     # or a compact rows file
    out = rejudge(rows, "munster1066-kingdom-once-30y-wake")

`recorded_rows` keeps of a trajectory only what a judge reads: the seat, its start metrics, and per step the date,
the metrics as read, the reward and the end as recorded, and how the target title was created (the title history
and the seat's applied calls naming it) when the step's end recorded it. `ReplayBackend` serves them: each advance lands on the next recorded step's date; nothing is called
in the game. A task that would run past the last recorded step is reported as still live there
(`live_after_last_step`), never extended with invented reads.
"""
from __future__ import annotations

import io
import json
from typing import Any, Callable, Mapping, Optional, Sequence

from .backend import Backend, OpenedWorld
from .scenario import Scenario, resolve
from .types import AdvanceResult, Metrics, ToolCall, ToolResult

R_RECORDING_ENDED = "recording_ended"


def recorded_rows(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """The judge's inputs from one recorded single-seat trajectory."""
    head = next(r for r in records if r.get("kind") == "header")
    seats = head.get("seats") or {}
    if not isinstance(seats, Mapping) or len(seats) != 1:
        raise ValueError(f"a recorded episode of one seat is re-judged; the header names {seats!r}")
    seat, cid = next(iter(seats.items()))
    end = next((r for r in records if r.get("kind") == "end"), None)
    steps = []
    for r in records:
        if r.get("kind") != "step":
            continue
        info = (r.get("infos") or {}).get(seat) or {}
        e = info.get("end") if isinstance(info.get("end"), Mapping) else {}
        steps.append({"step": r.get("step"), "game_date": r.get("game_date"),
                      "metrics": (r.get("metrics") or {}).get(seat),
                      "reward": (r.get("rewards") or {}).get(seat),
                      "reward_terms": info.get("reward_terms"),
                      "termination_reason": info.get("termination_reason"),
                      "truncation_reason": info.get("truncation_reason"),
                      **({"created": e["created"]} if isinstance(e.get("created"), Mapping) else {}),
                      **({"calls_naming_title": list(e["your_calls_naming_it"])}
                         if isinstance(e.get("your_calls_naming_it"), list) else {})})
    return {"task": (head.get("scenario") or {}).get("id"), "seat": seat, "character_id": cid,
            "start_date": ((head.get("scenario") or {}).get("start") or {}).get("date"),
            "start_metrics": (head.get("start_metrics") or {}).get(seat), "steps": steps,
            "end": {k: end.get(k) for k in ("reason", "steps", "returns")} if end else None}


def _metrics(d: Mapping[str, Any]) -> Metrics:
    return Metrics(game_date=d.get("game_date"), character_id=d.get("character_id"), values=dict(d.get("values") or {}),
                   status=dict(d.get("status") or {}), line_status=d.get("line_status"),
                   source=dict(d.get("source") or {}), dynasty_id=d.get("dynasty_id"),
                   dynasty_source=d.get("dynasty_source"))


class ReplayBackend(Backend):
    """A recorded episode's reads, served back (see the module doc). Not the game: `fake` is True."""
    name = "replay"
    fake = True

    def __init__(self, rows: Mapping[str, Any]):
        self.rows = rows
        self.i = -1                  # the recorded step last advanced to; -1: the start

    def open(self, scenario: Scenario, seed: int) -> OpenedWorld:
        if [s.name for s in scenario.seats] != [self.rows["seat"]]:
            raise ValueError(f"task {scenario.id} plays {[s.name for s in scenario.seats]}, the recording "
                             f"{self.rows['seat']!r}")
        self.i = -1
        return OpenedWorld(self.rows.get("start_date") or scenario.start_date,
                           {self.rows["seat"]: self.rows["character_id"]},
                           {"replay": f"recorded {self.rows.get('task')}, {len(self.rows['steps'])} steps"})

    def call(self, seat: str, call: ToolCall) -> ToolResult:
        return ToolResult(call.tool_name, False, error="replay: a recorded episode takes no calls")

    def _next(self) -> AdvanceResult:
        if self.i + 1 >= len(self.rows["steps"]):
            return AdvanceResult(None, False, truncation=R_RECORDING_ENDED, detail="no recorded step is left")
        self.i += 1
        return AdvanceResult(self.rows["steps"][self.i]["game_date"], True,
                             extra={"days": None, "checks": 0, "fired": []})

    def advance(self, days: int) -> AdvanceResult:
        return self._next()

    def advance_until(self, max_days: int, check_days: int, probe: Optional[Callable[[str], list]],
                      aligned: bool = False) -> AdvanceResult:
        return self._next()

    def metrics(self, seat: str, names: Sequence[str]) -> Metrics:
        d = self.rows["start_metrics"] if self.i < 0 else self.rows["steps"][self.i]["metrics"]
        return _metrics(d)

    def wake_vocabulary(self) -> dict[str, tuple]:
        from .wake import EVENTS, METRICS
        return {"events": tuple(EVENTS), "metrics": tuple(METRICS)}

    def wake_facts(self, seat: str, need: Optional[frozenset] = None) -> dict[str, Any]:
        return {}

    def title_origin(self, seat: str) -> Mapping[str, Any]:
        """The creation as the recorded run read it at its goal step (the newest entry then), else unread."""
        later = [s["created"] for s in self.rows["steps"][self.i:] if isinstance(s.get("created"), Mapping)] \
            if self.i >= 0 else []
        if not later:
            return {"unread": "the recording read no title history at or after this step"}
        c = {k: v for k, v in later[0].items() if k != "by"}
        return c

    def close(self) -> None:
        return None


def rejudge(rows: Mapping[str, Any], task: str | Scenario, seed: int = 1) -> dict[str, Any]:
    """Step the env under `task` over the recorded reads; per step the reward, its terms and any end; and the end
    record the env writes when the task ends inside the recording."""
    from .env import ParallelEnv
    from .trajectory import TrajectoryWriter
    sc = resolve(task) if isinstance(task, str) else task
    buf = io.StringIO()
    env = ParallelEnv(sc, backend=ReplayBackend(rows), trajectory=TrajectoryWriter(stream=buf))
    env.reset(seed=seed)
    seat = rows["seat"]
    out: list[dict] = []
    for rec in rows["steps"]:
        if seat not in env.agents:
            break
        # the seat's applied calls naming the title, as the recording's end listed them (no call is replayed): the
        # env attributes the creation from these, as it did live
        for c in rec.get("calls_naming_title") or []:
            if c.get("step") == rec["step"] and c not in env._title_calls[seat]:
                env._title_calls[seat].append(dict(c))
        _obs, r, te, tr, info = env.step({seat: []})
        i = info[seat]
        out.append({"step": rec["step"], "game_date": rec["game_date"], "reward": r[seat],
                    "reward_terms": i.get("reward_terms"), "termination_reason": i.get("termination_reason"),
                    "truncation_reason": i.get("truncation_reason"),
                    **({"target_title_first_held": i["target_title_first_held"]}
                       if "target_title_first_held" in i else {}),
                    **({"end": i["end"]} if "end" in i else {})})
    live = seat in env.agents
    if live:
        env.close()          # the recording ended before the task did: closed, the end record says so
    recs = [json.loads(x) for x in buf.getvalue().splitlines() if x.strip()]
    end = next((x for x in recs if x.get("kind") == "end"), None)
    return {"task": sc.id, "recorded_task": rows.get("task"), "steps": out,
            "return": sum(s["reward"] for s in out), "live_after_last_step": live,
            "end_record": {k: v for k, v in (end or {}).items() if k not in ("wall_utc", "episode_id", "schema")}}


__all__ = ["ReplayBackend", "recorded_rows", "rejudge", "R_RECORDING_ENDED"]
