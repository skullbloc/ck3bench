"""The environment: a parallel multi-agent env (PettingZoo's parallel shape)
whose agents are SEATS, and a single-seat wrapper (Gymnasium's shape).

    env = ParallelEnv(scenario)                  # or ParallelEnv("france1066-hold-10y", saves_dir=...)
    obs, infos = env.reset(seed=7)
    while env.agents:
        res = env.call("king", ToolCall("observe"))          # optional, in the seat's turn, game paused
        obs, rewards, terms, truncs, infos = env.step({s: [ToolCall("pass_turn")] for s in env.agents})
    env.close()

One STEP is one decision cycle: every live seat's action (a list of tool
calls, possibly empty: the game's own AI then plays the seat) is executed with
the game paused, in a seeded order, then the clock runs `cycle_days`
game-days and stops paused. Between steps a seat may make any number of
in-turn calls (`call`); they are part of the trajectory like any other.

Ends. A seat TERMINATES when its played line ends (`line_ended`) or the
scenario's goal holds for it. Every seat is TRUNCATED at the horizon
(`horizon`) or when the world fails (`game_exited`, `bridge_unavailable`,
`clock_stalled`, `reward_unreadable`, `script_log_closed`): the reason is in
`infos[seat]["truncation_reason"]`, and a failure step's reward is NaN, never
a number the world did not give. A done seat leaves `agents`.
"""
from __future__ import annotations

import math
import os
import random
import time
from typing import Any, Mapping, Optional

from . import comms as _comms
from .backend import Backend, backend_for
from .errors import (R_ACTION_SHAPE, R_BACKEND_MISMATCH, R_CLOCK_VERB, R_EPISODE_OVER, R_NOT_RESET, R_SEED_TYPE,
                     R_UNKNOWN_SEAT, TERM_GOAL, TERM_LINE_ENDED, TERM_TEAM_SEAT_DONE, TRUNC_HORIZON,
                     TRUNC_METRIC_UNREADABLE, EnvRefusal)
from .rewards import RewardContext, RewardUnavailable, county_goal_met, reward_unavailable_detail
from .scenario import Scenario, resolve
from .trajectory import TrajectoryWriter
from .types import (CLOCK_TOOL, METRIC_DEFINITIONS, SESSION_CLOCK_ACTIONS, AdvanceDiagnostics, ToolCall, ToolResult, clock_refusal,
                    parse_action)

R_TOOL_NOT_ALLOWED = "tool_not_allowed"
#: Appended to the clock tool's description wherever the env offers its schema.
CLOCK_NOTE = (f"\n\nIn this environment the clock belongs to the step: action in {list(SESSION_CLOCK_ACTIONS)} "
              f"is refused ({R_CLOCK_VERB}); end your turn and the step runs the cycle's game-days.")


def _line_ended(m) -> bool:
    if m.line_status == "line_ended":
        return True
    v, st = m.get("line_alive")
    return st == "ok" and v is False


class ParallelEnv:
    """PettingZoo-parallel-shaped: `possible_agents`, `agents`, `reset(seed,
    options) -> (observations, infos)`, `step(actions) -> (observations,
    rewards, terminations, truncations, infos)`, `state()`, `close()`.
    Observation and action spaces are JSON documents (`spec()`), not tensor
    spaces: the agents are language models calling typed tools."""

    metadata = {"name": "ck3bench_v0", "is_parallelizable": True, "render_modes": []}

    def __init__(self, scenario: Scenario | str, backend: Optional[Backend] = None,
                 trajectory: Optional[TrajectoryWriter] = None, **backend_kwargs: Any):
        self.scenario = resolve(scenario) if isinstance(scenario, str) else scenario
        self.backend = backend if backend is not None else backend_for(self.scenario, **backend_kwargs)
        if (self.backend.name == "fake") != (self.scenario.backend == "fake"):
            raise EnvRefusal(R_BACKEND_MISMATCH, f"scenario {self.scenario.id} declares backend "
                                                 f"{self.scenario.backend}, given {self.backend.name}", "env")
        self.trajectory = trajectory
        self.possible_agents: list[str] = list(self.scenario.seat_names)
        self.agents: list[str] = []
        self._open = False
        self._step = 0
        self._seed: Optional[int] = None
        self._rng: Optional[random.Random] = None
        self._characters: dict[str, int] = {}
        self._start: dict[str, Any] = {}
        self._prev: dict[str, Any] = {}
        self._date: Optional[str] = None
        self._pending: dict[str, list[dict]] = {}
        self._returns: dict[str, Optional[float]] = {}
        self._end_reason: Optional[str] = None
        self.comms: Optional[_comms.Comms] = None
        self._bind_comms()

    def _bind_comms(self) -> None:
        sc = self.scenario
        self.comms = _comms.Comms(sc.comms, sc.seat_names) if sc.comms is not None else None

    # ── the contract ──────────────────────────────────────────────────────
    def reset(self, seed: Optional[int] = None, options: Optional[Mapping[str, Any]] = None,
              *, scenario: Scenario | str | None = None) -> tuple[dict, dict]:
        """Open the world at the scenario's start. `seed` None draws one from
        the OS and records it (a seed is never unknown); `options` may carry
        `scenario` (an id or a Scenario) to switch task on the same backend."""
        if seed is not None and (not isinstance(seed, int) or isinstance(seed, bool)):
            raise EnvRefusal(R_SEED_TYPE, f"seed is {type(seed).__name__}", "env")
        new = scenario if scenario is not None else (options or {}).get("scenario")
        if new is not None:
            sc = resolve(new) if isinstance(new, str) else new
            if sc.backend != self.scenario.backend:
                raise EnvRefusal(R_BACKEND_MISMATCH, f"{sc.id} needs backend {sc.backend}", "env")
            self.scenario = sc
            self.possible_agents = list(sc.seat_names)
            self._bind_comms()
        if self._open:
            self._finish("abandoned_by_reset")
        drawn = seed is None
        self._seed = int.from_bytes(os.urandom(4), "big") if drawn else seed
        self._rng = random.Random(self._seed)
        opened = self.backend.open(self.scenario, self._seed)
        self._characters = dict(opened.characters)
        self._date = opened.game_date
        self._step = 0
        self.agents = list(self.possible_agents)
        self._open = True
        self._pending = {s: [] for s in self.agents}
        self._returns = {s: 0.0 for s in self.agents}
        self._end_reason = None
        if self.comms is not None:
            self.comms.reset()
        names = self.scenario.reward.metrics()
        self._start = {s: self.backend.metrics(s, names) for s in self.agents}
        self._prev = dict(self._start)
        if self.trajectory is not None:
            self.trajectory.begin(backend=self.backend.name, fake=self.backend.fake)
            self.trajectory.write("header", scenario=self.scenario.to_dict(), seed=self._seed,
                                  seed_drawn=drawn, seats=self._characters, provenance=opened.provenance,
                                  start_metrics={s: m.to_dict() for s, m in self._start.items()})
        obs = {s: self._observe(s) for s in self.agents}
        infos = {s: {"seed": self._seed, "seed_drawn": drawn, "backend": self.backend.name,
                     "fake": self.backend.fake, "start_metrics": self._start[s].to_dict()} for s in self.agents}
        return obs, infos

    def call(self, seat: str, call: ToolCall | Mapping[str, Any]) -> ToolResult:
        """One tool call in `seat`'s turn (the game paused); the clock does not move."""
        self._live(seat)
        c = call if isinstance(call, ToolCall) else ToolCall.from_dict(call)
        return self._exec(seat, c, "turn")

    def step(self, actions: Mapping[str, Any], usage: Optional[Mapping[str, Mapping[str, Any]]] = None,
             turns: Optional[Mapping[str, Mapping[str, Any]]] = None) -> tuple[dict, dict, dict, dict, dict]:
        """`usage`: per seat, the tokens its policy reported (recorded as
        given). `turns`: per seat, how its turn ended (an agent loop's typed
        turn end, a runner's timeout record); recorded as given."""
        if not self._open:
            raise EnvRefusal(R_NOT_RESET if self._seed is None else R_EPISODE_OVER, "reset first", "env")
        if not isinstance(actions, Mapping):
            raise EnvRefusal(R_ACTION_SHAPE, "actions is a mapping seat -> list of tool calls", "env")
        extra = [s for s in actions if s not in self.agents]
        if extra:
            raise EnvRefusal(R_UNKNOWN_SEAT, f"{extra} are not live seats {self.agents}", "env")
        missing = [s for s in self.agents if s not in actions]
        if missing:
            raise EnvRefusal(R_ACTION_SHAPE, f"no action for live seats {missing}; send [] for no action", "env")
        parsed = {s: parse_action(actions[s]) for s in self.agents}
        assert self._rng is not None
        order = list(self.agents)
        self._rng.shuffle(order)
        step_no = self._step + 1
        for s in order:
            for c in parsed[s]:
                self._exec(s, c, "action")
        adv = self.backend.advance(self.scenario.cycle_days)
        self._step = step_no
        self._date = adv.game_date
        live = list(self.agents)
        rewards: dict[str, float] = {}
        terms = {s: False for s in live}
        truncs = {s: False for s in live}
        infos: dict[str, dict] = {s: {"game_date": adv.game_date, "order": order} for s in live}
        metrics: dict[str, Any] = {}
        failure = None if adv.reached else adv.truncation or "advance_not_reached"
        own: dict[str, float] = {}
        if failure is None:
            names = self.scenario.reward.metrics()
            metrics = {s: self.backend.metrics(s, names) for s in live}
            horizon = step_no >= self.scenario.n_steps
            try:
                for s in live:
                    m = metrics[s]
                    ended = _line_ended(m)
                    goal = (self.scenario.goal == "county_gain" and not ended
                            and county_goal_met(self._start[s], m))
                    ctx = RewardContext(self._start[s], self._prev[s], m, final=ended or goal or horizon,
                                        line_ended=ended)
                    own[s], parts = self.scenario.reward.seat_reward(ctx)
                    infos[s]["reward_terms"] = parts
                    if ended:
                        terms[s], infos[s]["termination_reason"] = True, TERM_LINE_ENDED
                    elif goal:
                        terms[s], infos[s]["termination_reason"] = True, TERM_GOAL
                    elif horizon:
                        truncs[s], infos[s]["truncation_reason"] = True, TRUNC_HORIZON
            except RewardUnavailable as e:
                failure = TRUNC_METRIC_UNREADABLE
                for s in live:
                    infos[s]["reward_unavailable"] = reward_unavailable_detail(e)
            key = self.scenario.reward.team_seat
            if failure is None and key is not None and key in live and terms[key]:
                # the team is paid by this seat alone: when it ends, the task ends for every seat
                for s in live:
                    if not (terms[s] or truncs[s]):
                        terms[s], infos[s]["termination_reason"] = True, TERM_TEAM_SEAT_DONE
                        infos[s]["team_seat_reason"] = infos[key]["termination_reason"]
        if failure is not None:
            for s in live:
                rewards[s] = math.nan
                terms[s], truncs[s] = False, True
                infos[s].update(truncation_reason=failure, reward_status="unavailable",
                                advance_detail=adv.detail)
                infos[s].pop("termination_reason", None)
        else:
            rewards = self.scenario.reward.team_rewards(own)
            for s in live:
                infos[s].update(reward_status="ok", reward_own=own[s])
            self._prev.update(metrics)
        for s in live:
            r = rewards[s]
            cur = self._returns.get(s)
            self._returns[s] = None if (cur is None or math.isnan(r)) else cur + r
        diag = self.backend.advance_diagnostics(adv)
        if not isinstance(diag, AdvanceDiagnostics):
            raise TypeError(f"backend {self.backend.name}: advance_diagnostics returned {type(diag).__name__}")
        live_after = [s for s in live if not (terms[s] or truncs[s])]
        comms_rec = self.comms.end_step(step_no, order, live_after) if self.comms is not None else None
        obs = {s: self._observe(s, metrics.get(s)) for s in live}
        for s in live:
            self._pending[s] = []
        self.agents = live_after
        if self.trajectory is not None:
            self.trajectory.write(
                "step", step=step_no, game_date=adv.game_date, order=order,
                actions={s: [c.to_dict() for c in parsed[s]] for s in live}, observations=obs,
                rewards=rewards, terminations=terms, truncations=truncs, infos=infos,
                metrics={s: m.to_dict() for s, m in metrics.items()},
                advance={"reached": adv.reached, "truncation": adv.truncation, "detail": adv.detail,
                         "wall_s": adv.wall_s, "diagnostics": diag.to_dict()},
                usage={s: dict(u) for s, u in (usage or {}).items()},
                **({"turns": {s: dict(t) for s, t in turns.items()}} if turns else {}),
                **({"comms": comms_rec} if comms_rec is not None else {}))
        if not self.agents:
            first = next((infos[s].get("truncation_reason") for s in live if truncs[s]), None)
            self._finish(failure or first or "all_seats_done")
        return obs, rewards, terms, truncs, infos

    def state(self) -> dict[str, Any]:
        """A global view (every seat's last metrics), for centralised critics."""
        return {"scenario": self.scenario.id, "step": self._step, "game_date": self._date,
                "agents": list(self.agents), "metrics": {s: m.to_dict() for s, m in self._prev.items()},
                "open": self._open, "end_reason": self._end_reason, "fake": self.backend.fake}

    def spec(self) -> dict[str, Any]:
        """The observation and action spaces, as documents."""
        return {"env": self.metadata["name"], "scenario": self.scenario.to_dict(),
                "possible_agents": list(self.possible_agents),
                "action": {"type": "list", "items": {"tool_name": {"enum": list(self.scenario.tools)},
                                                     "arguments": "object"},
                           "empty": "no action; the game's own AI plays the seat this cycle"},
                "observation": {"keys": ["seat", "character_id", "game_date", "step", "steps_total",
                                         "cycle_days", "end_date", "metrics", "line_status",
                                         "last_results", "tools", "backend", "fake", "world"]
                                        + (["comms"] if self.comms is not None else [])},
                "metrics": {n: METRIC_DEFINITIONS[n] for n in self.scenario.reward.metrics()},
                "backend": self.backend.name, "fake": self.backend.fake}

    def observe(self, seat: str) -> dict[str, Any]:
        """`seat`'s current observation (as the last reset/step returned it, with
        this turn's results so far), for a seat that did not make that call."""
        self._live(seat)
        return self._observe(seat)

    def tool_specs(self, seat: str) -> list[dict[str, Any]]:
        """The input schemas of the tools `seat` may call (name, description,
        inputSchema: the MCP tool shape), in the scenario's order: the env-owned
        ones from comms.py, the game's from the backend."""
        if seat not in self.possible_agents:
            raise EnvRefusal(R_UNKNOWN_SEAT, f"{seat!r} is not a seat {self.possible_agents}", "env")
        own = {t["name"]: t for t in _comms.tool_specs(self.scenario.comms)}
        game = [t for t in self.scenario.tools if t not in own]
        by_name = {t["name"]: t for t in self.backend.tool_specs(seat, game)} if game else {}
        out = [own.get(t) or by_name[t] for t in self.scenario.tools]
        return [dict(t, description=(t.get("description") or "") + CLOCK_NOTE) if t["name"] == CLOCK_TOOL else t
                for t in out]

    def observation_space(self, agent: str) -> dict[str, Any]:
        return self.spec()["observation"]

    def action_space(self, agent: str) -> dict[str, Any]:
        return self.spec()["action"]

    def close(self) -> None:
        if self._open:
            self._finish("closed")
        self.backend.close()

    # ── inside ────────────────────────────────────────────────────────────
    def _live(self, seat: str) -> None:
        if not self._open:
            raise EnvRefusal(R_NOT_RESET if self._seed is None else R_EPISODE_OVER, "reset first", "env")
        if seat not in self.agents:
            raise EnvRefusal(R_UNKNOWN_SEAT, f"{seat!r} is not a live seat {self.agents}", "env")

    def _exec(self, seat: str, c: ToolCall, phase: str) -> ToolResult:
        t0 = time.monotonic()
        clock = clock_refusal(c)
        if clock is not None:
            # every route (call, step action, server, shim, agent, runner) comes through here
            code, detail = clock
            res = ToolResult(c.tool_name, False, result={"code": code, "leg": "env", "detail": detail},
                             error=f"{code}: {detail}")
        elif c.tool_name not in self.scenario.tools:
            res = ToolResult(c.tool_name, False, error=f"{R_TOOL_NOT_ALLOWED}: {c.tool_name!r} is not in this "
                                                       f"scenario's tools {list(self.scenario.tools)}")
        elif self.comms is not None and c.tool_name in _comms.COMMS_TOOLS:
            res = self.comms.handle(seat, c, self._step + 1, self._date)
        else:
            res = self.backend.call(seat, c)
        self._pending[seat].append({"phase": phase, "call": c.to_dict(), "result": res.to_dict()})
        if self.trajectory is not None:
            self.trajectory.write("call", step=self._step + 1, seat=seat, phase=phase, call=c.to_dict(),
                                  result=res.to_dict(), game_date=self._date,
                                  wall_s=round(time.monotonic() - t0, 6))
        return res

    def _observe(self, seat: str, m=None) -> dict[str, Any]:
        m = m if m is not None else self._prev.get(seat)
        return {"seat": seat, "character_id": m.character_id if m is not None else self._characters.get(seat),
                "game_date": self._date, "step": self._step, "steps_total": self.scenario.n_steps,
                "cycle_days": self.scenario.cycle_days, "end_date": self.scenario.end_date,
                "metrics": {k: m.get(k)[0] for k in m.status} if m is not None else {},
                "metric_status": dict(m.status) if m is not None else {},
                "line_status": m.line_status if m is not None else None,
                "last_results": list(self._pending.get(seat, [])), "tools": list(self.scenario.tools),
                "backend": self.backend.name, "fake": self.backend.fake,
                "world": self.backend.observation(seat) if self._open else {},
                **({"comms": self.comms.observation(seat)} if self.comms is not None else {})}

    def _finish(self, reason: str) -> None:
        if self.trajectory is not None:
            self.trajectory.write("end", reason=reason, steps=self._step, returns=dict(self._returns))
        self._open = False
        self._end_reason = reason
        self.agents = []

    @property
    def end_reason(self) -> Optional[str]:
        return self._end_reason

    @property
    def steps_done(self) -> int:
        """Steps completed in the current (or last) episode; the open step is steps_done + 1."""
        return self._step

    @property
    def is_open(self) -> bool:
        return self._open


class Env:
    """The single-seat, Gymnasium-shaped view of a one-seat scenario:
    `reset(seed, options) -> (obs, info)`, `step(action) -> (obs, reward,
    terminated, truncated, info)`, and `call(tool_call)` in the seat's turn."""

    def __init__(self, scenario: Scenario | str, **kwargs: Any):
        self.penv = ParallelEnv(scenario, **kwargs)
        if len(self.penv.possible_agents) != 1:
            raise EnvRefusal(R_ACTION_SHAPE, f"Env needs a one-seat scenario; "
                                             f"{self.penv.scenario.id} has {self.penv.possible_agents}", "env")
        self.seat = self.penv.possible_agents[0]

    def reset(self, *, seed: Optional[int] = None, options: Optional[Mapping[str, Any]] = None):
        obs, infos = self.penv.reset(seed=seed, options=options)
        return obs[self.seat], infos[self.seat]

    def call(self, call: ToolCall | Mapping[str, Any]) -> ToolResult:
        return self.penv.call(self.seat, call)

    def tool_specs(self) -> list[dict[str, Any]]:
        return self.penv.tool_specs(self.seat)

    def step(self, action: Any, usage: Optional[Mapping[str, Any]] = None,
             turn: Optional[Mapping[str, Any]] = None):
        obs, r, te, tr, info = self.penv.step({self.seat: action},
                                              usage={self.seat: usage} if usage else None,
                                              turns={self.seat: turn} if turn else None)
        return obs[self.seat], r[self.seat], te[self.seat], tr[self.seat], info[self.seat]

    def close(self) -> None:
        self.penv.close()
