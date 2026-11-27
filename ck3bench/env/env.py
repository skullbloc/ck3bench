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

Ends. A seat TERMINATES when its played line ends (`line_ended`), when the
line passes to a ruler of another dynasty (`line_lost_to_other_dynasty`: the
seat's dynasty lost it; scored as `line_ended` is, and that ruler is never
scored), or the scenario's goal holds for it. Every seat is TRUNCATED at the horizon
(`horizon`) or when the world fails (`game_exited`, `bridge_unavailable`,
`clock_stalled`, `reward_unreadable`, `script_log_closed`,
`line_dynasty_unread`: a ruler change whose dynasty did not read): the reason is in
`infos[seat]["truncation_reason"]`, and a failure step's reward is NaN, never
a number the world did not give. A done seat leaves `agents`.
"""
from __future__ import annotations

import math
import os
import random
import time
from typing import Any, Mapping, Optional

from . import calendar
from . import comms as _comms
from . import orders as _orders
from . import outcome as _outcome
from . import wake as _wake
from .backend import Backend, backend_for
from .errors import (R_ACTION_SHAPE, R_BACKEND_MISMATCH, R_CLOCK_VERB, R_EPISODE_OVER, R_NOT_RESET, R_SEED_TYPE,
                     R_NOTES_NOT_KEPT, R_NOTES_SHAPE, R_NOTES_TOO_LONG,
                     R_UNKNOWN_SEAT, R_WAKE_SHAPE, R_WAKE_UNDECLARED, R_WAKE_UNWITNESSABLE, TERM_GOAL, TERM_LINE_ENDED, TERM_TEAM_SEAT_DONE, TRUNC_HORIZON,
                     TERM_LINE_LOST_DYNASTY, TRUNC_LINE_DYNASTY_UNREAD, TRUNC_METRIC_UNREADABLE, EnvRefusal)
from .rewards import (HORIZON_GOALS, LINE_KEPT_RULER_CHANGES, ONCE_GOALS, RewardContext, RewardUnavailable, goal_met, line_kept_legs,
                      reward_unavailable_detail)
from .scenario import Scenario, resolve
from .trajectory import TrajectoryWriter
from .types import (CLOCK_TOOL, METRIC_DEFINITIONS, SESSION_CLOCK_ACTIONS, AdvanceDiagnostics, ToolCall, ToolResult, clock_refusal,
                    parse_action)
from .types import METRIC_OK, TARGET_METRICS, Metrics, speed_witness

R_TOOL_NOT_ALLOWED = "tool_not_allowed"
#: Appended to the clock tool's description wherever the env offers its schema.
CLOCK_NOTE = (f"\n\nIn this environment the clock belongs to the step: action in {list(SESSION_CLOCK_ACTIONS)} "
              f"is refused ({R_CLOCK_VERB}); end your turn and the step runs the cycle's game-days.")


def _line_ended(m) -> bool:
    if m.line_status == "line_ended":
        return True
    v, st = m.get("line_alive")
    return st == "ok" and v is False


#: what a line lost to another dynasty scores at its terminal step, as `line_ended` does (ck3.py reads
#: an ended line as 0 counties and no title): never the other dynasty's ruler's reads
_LOST_ZERO = ("realm_counties", "demesne_counties", "dejure_counties")
_LOST_FALSE = ("holds_target_title", "line_alive", "heir_present")


def lost_line_metrics(cur: Metrics, prev: Metrics) -> Metrics:
    """The metrics a seat whose line passed to another dynasty is scored on at that step: its realm, de jure
    counties and title are gone (0, False), the line is not alive, and every other metric (gold, prestige, the
    line's counts) is carried from before the step: what the other dynasty's ruler holds is not the seat's."""
    out = Metrics(cur.game_date, cur.character_id, line_status=cur.line_status, dynasty_id=cur.dynasty_id,
                  dynasty_source=cur.dynasty_source)
    why = "the line passed to another dynasty"
    for n in cur.status:
        if n in _LOST_ZERO or n in _LOST_FALSE:
            out.values[n], out.status[n] = (0 if n in _LOST_ZERO else False), METRIC_OK
            out.source[n] = f"{why}: scored as the line's loss"
        else:
            v, st = prev.get(n)
            out.status[n], out.source[n] = st, f"{why}: carried from before the step, that ruler is not scored"
            if v is not None:
                out.values[n] = v
    return out


def _times(n: int) -> str:
    return {1: "once", 2: "twice"}.get(n, f"{n} times")


class ParallelEnv:
    """PettingZoo-parallel-shaped: `possible_agents`, `agents`, `reset(seed,
    options) -> (observations, infos)`, `step(actions) -> (observations,
    rewards, terminations, truncations, infos)`, `state()`, `close()`.
    Observation and action spaces are JSON documents (`spec()`), not tensor
    spaces: the agents are language models calling typed tools."""

    metadata = {"name": "ck3bench_v0", "is_parallelizable": True, "render_modes": []}

    def __init__(self, scenario: Scenario | str, backend: Optional[Backend] = None,
                 trajectory: Optional[TrajectoryWriter] = None, notes_pad_bytes: Optional[int] = None,
                 seat_surface: Optional[Mapping[str, Any]] = None, **backend_kwargs: Any):
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
        self._last_wake: Optional[dict] = None     # a wake task's last step: days run, why it stopped
        #: an ALIGNED wake step's check dates (types.check_dates_witness): the step record's, not the seat's
        self._last_check_dates: Optional[dict] = None
        self._book: Optional[_orders.Book] = None  # the standing orders (orders.py); None when none stands
        self._order_news: dict[str, list] = {}     # the last step's order records per seat (the next observation)
        self._notes_unwritten: dict[str, dict] = {}  # a turn's notes not written, per seat (the next observation)
        # a decision task's outcome (outcome.py): the start read, and the seat's declare_war / do calls
        self._outcome_start: Optional[dict] = None
        self._outcome_calls: list[dict] = []
        self._last_outcome: Optional[dict] = None   # the last episode's score (the end record's `outcome`)
        #: the target title's LATCH per seat: None until the seat's line is first seen holding it (at the start or at
        #: a step's end), then that first holding {date, step, character_id, ...}; it is never cleared in an episode
        #: (the title lost, a new ruler), so `target_title_first_gained` pays once (rewards.py)
        self._first_held: dict[str, Optional[dict]] = {}
        self._held_now: dict[str, Optional[bool]] = {}
        self._pending_first: dict[str, dict] = {}
        self.comms: Optional[_comms.Comms] = None
        self._bind_comms()
        # CARRIED NOTES (a run option, not the task's: a new seat session every turn): each seat's
        # private pad, kept by comms.py's pad code (replace at the version read, a byte bound refused and
        # never cut), written when the seat ends its turn and shown in its next observation; every step
        # record carries every pad's text. None: no pad (every observation and record as before).
        if notes_pad_bytes is not None and (not isinstance(notes_pad_bytes, int) or isinstance(notes_pad_bytes, bool)
                                            or not 1 <= notes_pad_bytes <= 65536):
            raise EnvRefusal(R_NOTES_SHAPE, f"notes_pad_bytes {notes_pad_bytes!r} must be an int in 1..65536", "env")
        self.notes_pad_bytes = notes_pad_bytes
        #: the seat surface its runner declared (a changed surface is a new measured condition);
        #: written in the header as given, absent when not given
        self.seat_surface = dict(seat_surface) if seat_surface is not None else None
        self.carry: Optional[_comms.Comms] = None
        self._bind_carry()

    def _bind_carry(self) -> None:
        n = self.notes_pad_bytes
        self.carry = None if n is None else _comms.Comms(
            _comms.CommsDecl(None, _comms.NotepadsDecl(n, None, n)), self.scenario.seat_names)

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
            self._bind_carry()
        if self._open:
            self._finish("abandoned_by_reset")
        if self.scenario.wake is not None:
            # a backend that cannot run a wake task, or cannot witness a forced wake, refuses before the game opens
            unseen = [e for e in self.scenario.wake.forced if e not in self.backend.wake_vocabulary()["events"]]
            if unseen:
                raise EnvRefusal(R_WAKE_UNWITNESSABLE, f"{self.scenario.id} forces wakes on {unseen}; backend "
                                                       f"{self.backend.name} does not witness them", "env")
        if self.scenario.outcome is not None and not self.backend.reads_outcomes:
            from .errors import R_OUTCOME_UNSUPPORTED
            raise EnvRefusal(R_OUTCOME_UNSUPPORTED, f"{self.scenario.id} declares outcome "
                                                    f"{self.scenario.outcome!r}; backend {self.backend.name} reads "
                                                    "no outcomes", "env")
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
        self._last_wake = None
        self._last_check_dates = None
        self._book, self._order_news = None, {}
        self._notes_unwritten = {}
        if self.comms is not None:
            self.comms.reset()
        if self.carry is not None:
            self.carry.reset()
        names = self.scenario.metric_names()
        self._start = {s: self.backend.metrics(s, names) for s in self.agents}
        self._prev = dict(self._start)
        self._first_held, self._held_now = {}, {}
        for s in self.agents:
            v, st = self._start[s].get("holds_target_title")
            self._held_now[s] = v if isinstance(v, bool) else None
            self._first_held[s] = ({"date": self._start[s].game_date, "step": 0,
                                    "character_id": self._start[s].character_id, "at_start": True}
                                   if v is True else None)
        # the seat's dynasty is its ruler's at open; the last ruler judged to be of it
        self._judged = {s: self._start[s].character_id for s in self.agents}
        self._outcome_calls = []
        self._title_calls = {s: [] for s in self.agents}
        self._outcome_start = (_outcome.read_standard(self.backend.outcome_reader(self.agents[0]))
                               if self.scenario.outcome is not None else None)
        if self.trajectory is not None:
            self.trajectory.begin(backend=self.backend.name, fake=self.backend.fake)
            self.trajectory.write("header", scenario=self.scenario.to_dict(), seed=self._seed,
                                  seed_drawn=drawn, seats=self._characters, provenance=opened.provenance,
                                  start_metrics={s: m.to_dict() for s, m in self._start.items()},
                                  orders=self.orders_setting(),
                                  **({"notes_pad_bytes": self.notes_pad_bytes} if self.carry is not None else {}),
                                  **({"seat_surface": self.seat_surface} if self.seat_surface is not None else {}),
                                  **({"outcome_start": self._outcome_start} if self._outcome_start is not None
                                     else {}))
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
             turns: Optional[Mapping[str, Mapping[str, Any]]] = None,
             wake: Optional[Mapping[str, Any]] = None,
             orders: Optional[Mapping[str, Any]] = None) -> tuple[dict, dict, dict, dict, dict]:
        """`usage`: per seat, the tokens its policy reported (recorded as
        given). `turns`: per seat, how its turn ended (an agent loop's typed
        turn end, a runner's timeout record); recorded as given. `wake`: on a
        wake task only (wake.py), per seat, {max_days, when}; a seat left out
        takes the scenario's cycle. On a fixed task any wake is refused. `orders`: on a wake task with orders on
        (orders.py), per seat, the standing orders for this step; refused anywhere else."""
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
        wake_req = self._wake_requests(wake)       # refused here, before any call runs
        order_req = self._order_requests(orders)   # likewise
        assert self._rng is not None
        order = list(self.agents)
        self._rng.shuffle(order)
        step_no = self._step + 1
        for s in order:
            for c in parsed[s]:
                self._exec(s, c, "action")
        if order_req:
            # STANDING ORDERS stand across steps (orders.py Book): this turn's are added to the ones standing
            if self._book is None:
                self._book = _orders.Book()
            for s, os_ in order_req.items():
                prev = self._prev.get(s)
                tp = getattr(self.backend, "title_price", None)   # the price read for a title an order mints
                self._book.add(s, os_, self._date, getattr(prev, "character_id", None),
                               price_of=(lambda k, _s=s, _tp=tp: _tp(_s, k)) if callable(tp) else None)
        adv = self.backend.advance(self.scenario.cycle_days) if wake_req is None else self._advance_wake(wake_req)
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
        lost: dict[str, dict] = {}           # seats whose line passed to another dynasty this step
        self._pending_first = {}             # this step's first holdings, committed with the step's rewards
        if failure is None:
            names = self.scenario.metric_names()
            metrics = {s: self.backend.metrics(s, names) for s in live}
            horizon = step_no >= self.scenario.n_steps if wake_req is None else \
                calendar.ordinal(adv.game_date) >= calendar.ordinal(self.scenario.end_date)
            for s in live:
                verdict = self._judge_dynasty(s, metrics[s])
                if verdict is not None:
                    infos[s]["line_dynasty"] = verdict
                    if verdict["verdict"] == "unread":
                        failure = TRUNC_LINE_DYNASTY_UNREAD
                    elif verdict["verdict"] == "other_dynasty":
                        lost[s] = verdict
        if failure is None:
            try:
                for s in live:
                    m = metrics[s]
                    if s in lost:
                        # the seat's dynasty lost the line; a failure, scored on
                        # the line's loss and never on the other dynasty's ruler
                        m = lost_line_metrics(m, self._prev[s])
                        infos[s]["line_dynasty"]["scored_metrics"] = m.to_dict()
                    self._latch(s, m, step_no, adv.game_date)
                    ended = _line_ended(m)
                    before = self._first_held.get(s) is not None
                    # a horizon goal says what held over the whole episode, and a once goal whether the title was
                    # ever held: both judged at the end date only (holding the title does not end a once goal)
                    goal = not ended and (horizon or self.scenario.goal not in HORIZON_GOALS + ONCE_GOALS) \
                        and goal_met(self.scenario.goal, self._start[s], m,
                                     **({"held_before": before} if self.scenario.goal in ONCE_GOALS else {}))
                    ctx = RewardContext(self._start[s], self._prev[s], m, final=ended or goal or horizon,
                                        line_ended=ended, title_held_before=before)
                    own[s], parts = self.scenario.reward.seat_reward(ctx)
                    infos[s]["reward_terms"] = parts
                    if s in lost:
                        v = lost[s]
                        terms[s], infos[s]["termination_reason"] = True, TERM_LINE_LOST_DYNASTY
                        infos[s]["end"] = {
                            "reason": TERM_LINE_LOST_DYNASTY, "date": adv.game_date, "step": step_no,
                            "why": f"your ruler's titles passed to {v['ruler_id']} of dynasty {v['dynasty_id']}, not "
                                   f"your dynasty {v['seat_dynasty_id']}: your dynasty lost the line, so the task "
                                   "ends here as a failure; nothing that ruler does is scored",
                            "ruler_id": v["ruler_id"], "dynasty_id": v["dynasty_id"],
                            "your_dynasty_id": v["seat_dynasty_id"]}
                    elif ended:
                        terms[s], infos[s]["termination_reason"] = True, TERM_LINE_ENDED
                        infos[s]["end"] = {"reason": TERM_LINE_ENDED, "date": adv.game_date, "step": step_no,
                                           "why": "your ruler's line has no successor to play"}
                    elif goal:
                        terms[s], infos[s]["termination_reason"] = True, TERM_GOAL
                        infos[s]["end"] = self._goal_end(s, step_no, adv.game_date, m)
                    elif horizon:
                        truncs[s], infos[s]["truncation_reason"] = True, TRUNC_HORIZON
                        infos[s]["end"] = {"reason": TRUNC_HORIZON, "date": adv.game_date, "step": step_no,
                                           "end_date": self.scenario.end_date,
                                           "why": f"the task's end date {self.scenario.end_date} was reached; "
                                                  "the goal was not met"}
                        if self.scenario.goal == "line_kept_peacefully":
                            infos[s]["end"]["legs"] = line_kept_legs(self._start[s], m)
            except RewardUnavailable as e:
                failure = TRUNC_METRIC_UNREADABLE
                for s in live:
                    infos[s]["reward_unavailable"] = reward_unavailable_detail(e)
        if failure is None:
            key = self.scenario.reward.team_seat
            if key is not None and key in live and terms[key]:
                # the team is paid by this seat alone: when it ends, the task ends for every seat
                for s in live:
                    if not (terms[s] or truncs[s]):
                        terms[s], infos[s]["termination_reason"] = True, TERM_TEAM_SEAT_DONE
                        infos[s]["team_seat_reason"] = infos[key]["termination_reason"]
        if failure is None:
            for s in live:
                if isinstance(infos[s].get("end"), dict) and s in lost:
                    # the end as read: the other dynasty's ruler; and what the seat was scored on
                    infos[s]["end"]["final"] = {"date": adv.game_date, "character_id": metrics[s].character_id,
                                                "dynasty_id": metrics[s].dynasty_id,
                                                "line_status": metrics[s].line_status,
                                                "scored": dict(infos[s]["line_dynasty"]["scored_metrics"]["values"])}
                elif isinstance(infos[s].get("end"), dict):
                    # the state the episode ended in, read at the end date (a seat's last read can be the
                    # step's start: 293.68 gold, where the end date read 365.76)
                    infos[s]["end"]["final"] = self._final_state(s, metrics[s], adv.game_date)
        if failure is not None:
            for s in live:
                rewards[s] = math.nan
                terms[s], truncs[s] = False, True
                infos[s].update(truncation_reason=failure, reward_status="unavailable",
                                advance_detail=adv.detail,
                                end={"reason": failure, "date": adv.game_date, "step": step_no,
                                     "why": ("a ruler changed and a dynasty did not read, so whether your dynasty "
                                             "still holds the line cannot be judged; the episode stops unscored"
                                             if failure == TRUNC_LINE_DYNASTY_UNREAD else
                                             "the world failed (the game or its bridge); not your play")})
                infos[s].pop("termination_reason", None)
        else:
            rewards = self.scenario.reward.team_rewards(own)
            for s in live:
                infos[s].update(reward_status="ok", reward_own=own[s])
                first = self._pending_first.pop(s, None)
                if first is not None:
                    self._first_held[s] = first
                    if self.scenario.goal in ONCE_GOALS:
                        infos[s]["target_title_first_held"] = dict(first)
                if s in metrics:
                    v = (lost_line_metrics(metrics[s], self._prev[s]) if s in lost else metrics[s]).get(
                        "holds_target_title")[0]
                    self._held_now[s] = v if isinstance(v, bool) else None
                if self.scenario.goal in ONCE_GOALS and isinstance(infos[s].get("end"), dict):
                    infos[s]["end"]["target_title"] = self._once_record(s, infos[s]["end"])
            self._prev.update(metrics)
        for s in live:
            r = rewards[s]
            cur = self._returns.get(s)
            self._returns[s] = None if (cur is None or math.isnan(r)) else cur + r
        diag = self.backend.advance_diagnostics(adv)
        if not isinstance(diag, AdvanceDiagnostics):
            raise TypeError(f"backend {self.backend.name}: advance_diagnostics returned {type(diag).__name__}")
        live_after = [s for s in live if not (terms[s] or truncs[s])]
        order_recs: dict[str, list] = {}
        if self._book is not None:
            ended = {s: str(infos[s].get("termination_reason") or infos[s].get("truncation_reason"))
                     for s in live if s not in live_after}
            order_recs = self._book.close(adv.game_date, ended)
            if not self._book:
                self._book = None          # nothing stands: the next step runs as one without orders
        comms_rec = self.comms.end_step(step_no, order, live_after) if self.comms is not None else None
        # the seat's own orders' records, in every observation until the next step (only when it set any)
        self._order_news = order_recs
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
                         "wall_s": adv.wall_s, "diagnostics": diag.to_dict(),
                         "speed": speed_witness(self.scenario.game_speed, self.backend.clock_speeds())},
                usage={s: dict(u) for s, u in (usage or {}).items()},
                **({"turns": {s: dict(t) for s, t in turns.items()}} if turns else {}),
                **({"comms": comms_rec} if comms_rec is not None else {}),
                **({"wake": {"requests": wake_req, **self._last_wake,
                             **({"check_dates": self._last_check_dates} if self._last_check_dates is not None
                                else {})}} if wake_req is not None else {}),
                **({"notes_pads": {s: self._pad(s) for s in live}} if self.carry is not None else {}),
                **({"orders": order_recs} if order_recs else {}))
        if not self.agents:
            first = next((infos[s].get("truncation_reason") for s in live if truncs[s]), None)
            self._finish(failure or first or "all_seats_done")
        return obs, rewards, terms, truncs, infos

    def _latch(self, seat: str, m: Any, step_no: int, date: Optional[str]) -> Optional[dict]:
        """The target title's latch at a scored step: when the line's character holds the title (as scored: a line
        lost to another dynasty holds nothing) and the latch is not set, this step's first holding, with who
        created the title as read now (the title history beside the seat's applied calls naming it), is PENDING
        until the step's rewards stand (a step that fails sets nothing). The latch is never cleared in an
        episode."""
        if not self.scenario.target_title:
            return None
        v, _st = m.get("holds_target_title")
        if v is not True or self._first_held.get(seat) is not None:
            return None
        rec: dict[str, Any] = {"date": date, "step": step_no, "character_id": m.character_id, "at_start": False}
        if self.scenario.goal in ONCE_GOALS:
            rec.update(self._creation(seat, m.character_id))
            rec["why"] = (f"your ruler {m.character_id} holds {self.scenario.target_title} for the first time in this "
                          "episode: its reward is paid now, once (never again if the title is lost and regained); "
                          f"the episode goes on to the end date {self.scenario.end_date}")
        self._pending_first[seat] = rec
        return dict(rec)

    @staticmethod
    def creation_dates(first: Optional[Mapping[str, Any]]) -> Optional[dict[str, Any]]:
        """Both dates of a first holding (a title minted 1096.02.19 is first read held at the step's end,
        1096.04.20). `created`: the date of the seat's applied call that named the title
        (`created_from` "your_call"), else the title history's creation date ("title_history"), else None
        ("unread"); `first_step_held`: the end date of the first step that read the title held."""
        if not isinstance(first, Mapping):
            return None
        calls = [c for c in first.get("your_calls_naming_it") or [] if isinstance(c, Mapping) and c.get("game_date")]
        hist = first.get("created") if isinstance(first.get("created"), Mapping) else {}
        if calls and str(hist.get("by") or "").startswith("you:"):
            created, src = calls[-1]["game_date"], "your_call"
        elif hist.get("date") and "unread" not in hist:
            created, src = hist["date"], "title_history"
        else:
            created, src = None, "unread"
        return {"created": created, "created_from": src, "first_step_held": first.get("date"),
                "first_step": first.get("step")}

    def _once_record(self, seat: str, end: Mapping[str, Any]) -> dict[str, Any]:
        """A once goal's primary outcome, in the step's end and the trajectory's end record: whether the seat's line
        held the target title at any step by the end date, the first holding (date, step, ruler, who created it),
        and whether it was held at the end (None when the step did not read it)."""
        first = self._first_held.get(seat)
        return {"title": self.scenario.target_title, "held_at_any_step": first is not None,
                "first_held": dict(first) if first is not None else None,
                "dates": self.creation_dates(first),
                "held_at_end": self._held_now.get(seat), "end_reason": end.get("reason"),
                "end_date": end.get("date"), "horizon_reached": end.get("reason") in (TRUNC_HORIZON, TERM_GOAL)}

    def _creation(self, seat: str, cid: Optional[int]) -> dict[str, Any]:
        """Who created the target title, from the game's title history and the seat's own applied
        calls that named it (each with its phase: a standing order's call carries its order id)."""
        out: dict[str, Any] = {}
        origin = self.backend.title_origin(seat)
        mine = list(self._title_calls.get(seat) or [])
        if isinstance(origin, Mapping) and "unread" not in origin:
            out["created"] = dict(origin)
            if origin.get("char_id") == cid:
                out["created"]["by"] = (f"you: {mine[-1]['tool']}" + (f" {mine[-1]['intent']}" if mine[-1].get('intent')
                                                                       else "") + f" at step {mine[-1]['step']}"
                                        if mine else "the game for your ruler (his AI, a decision or an event): no "
                                                     "call of yours that names the title was applied")
        else:
            out["created"] = dict(origin) if isinstance(origin, Mapping) else {"unread": "no title history read"}
        if mine:
            out["your_calls_naming_it"] = mine[-3:]
        return out

    def _judge_dynasty(self, seat: str, m: Metrics) -> Optional[dict]:
        """At a ruler change (the line's character is not the last one judged), whether the new ruler
        is of the seat's dynasty (its ruler's at open). None: no change to judge (or the line ended, which ends
        the seat on its own). Else {verdict: same | other_dynasty | unread, ...}. A dynasty that did not read, the
        new ruler's or the seat's at open, is `unread`, never a match and never a loss: the game is paused at the
        read, so a re-read reads the same memory, and going on would score a ruler nobody judged."""
        cid = m.character_id
        if cid is None or cid == self._judged.get(seat) or m.line_status == "line_ended":
            return None
        st = self._start[seat]
        out = {"ruler_id": cid, "previous_ruler_id": self._judged.get(seat), "dynasty_id": m.dynasty_id,
               "dynasty_source": m.dynasty_source, "seat_dynasty_id": st.dynasty_id,
               "seat_dynasty_source": st.dynasty_source}
        if st.dynasty_id is None or m.dynasty_id is None:
            return {**out, "verdict": "unread"}
        if m.dynasty_id != st.dynasty_id:
            return {**out, "verdict": "other_dynasty"}
        self._judged[seat] = cid
        return {**out, "verdict": "same"}

    def _final_state(self, seat: str, m: Any, date: Optional[str]) -> dict[str, Any]:
        """The seat's ruler, gold, counties and target title as read at the episode's end (the step's own
        metric reads, and one more read for any of these the task does not score); a value not read is
        `unread` with its reason, never a number."""
        names = ["gold", "realm_counties"] + (list(TARGET_METRICS) if self.scenario.target_title else [])
        out: dict[str, Any] = {"date": date, "character_id": m.character_id, "line_status": m.line_status}
        missing = [n for n in names if m.get(n)[1] != METRIC_OK]
        more = self.backend.metrics(seat, tuple(missing)) if missing else None
        for n in names:
            src = m if n not in missing else more
            v, st = src.get(n)
            out[n] = v if st == METRIC_OK else f"unread: {src.source.get(n) or st}"
        return out

    def _goal_end(self, seat: str, step_no: int, date: Optional[str], cur: Any = None) -> dict[str, Any]:
        """Why the seat's episode ended at its goal (a bare `episode_over: true` reads as a failure to a seat). The goal is the line's character HOLDING the target
        title, however it came to him; who created it is the game's title history (the backend's read) beside the
        seat's own applied calls that named the title."""
        t = self.scenario.target_title
        cid = self._prev[seat].character_id if seat in self._prev else self._characters.get(seat)
        if self.scenario.goal == "line_kept_peacefully":
            # judged at the end date (HORIZON_GOALS): the legs as read at this step, every one held
            return {"reason": TERM_GOAL, "goal": self.scenario.goal, "date": date, "step": step_no,
                    "why": f"the task's end date {self.scenario.end_date} was reached and every leg of the goal "
                           "held: your line has a ruler of your dynasty, it passed from ruler to ruler at least "
                           f"{_times(LINE_KEPT_RULER_CHANGES)}, no offensive war was declared, and your realm is no "
                           "smaller than at the start",
                    "legs": line_kept_legs(self._start[seat], cur)}
        if self.scenario.goal in ONCE_GOALS:
            # judged at the end date on the latch: the title was held at some step; the creation as read then
            first = self._first_held.get(seat) or self._pending_first.get(seat) or {}
            out = {"reason": TERM_GOAL, "goal": self.scenario.goal, "date": date, "step": step_no, "title": t,
                   "why": f"the task's end date {self.scenario.end_date} was reached, and your line held {t} at a step "
                          f"of this episode (first on {first.get('date')}, step {first.get('step')}): the task's goal "
                          "is met",
                   "scored": f"the task scores your line holding {t} at any step by the end date, however it came to "
                             "it; its reward was paid once, at the first holding"}
            for k in ("created", "your_calls_naming_it"):
                if k in first:
                    out[k] = first[k]
            return out
        out: dict[str, Any] = {"reason": TERM_GOAL, "goal": self.scenario.goal, "date": date, "step": step_no,
                               "why": f"your ruler {cid} holds {t}: the task's goal is met",
                               "title": t, "holder_id": cid,
                               "scored": f"the task scores your ruler holding {t}, however it came to him"}
        out.update(self._creation(seat, cid))
        return out

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
                                        + (["comms"] if self.comms is not None else [])
                                        + (["wake"] if self.scenario.wake is not None else [])
                                        + (["notes_pad"] if self.carry is not None else [])},
                "metrics": {n: METRIC_DEFINITIONS[n] for n in self.scenario.metric_names()},
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

    def _exec(self, seat: str, c: ToolCall, phase: str, date: Optional[str] = None,
              order_id: Optional[str] = None) -> ToolResult:
        """One call by every route; an order's (phase `order`) carries the check's date and its order's id."""
        t0 = time.monotonic()
        date = self._date if date is None else date
        tag = {"order_id": order_id} if order_id is not None else {}
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
        self._pending[seat].append({"phase": phase, "call": c.to_dict(), "result": res.to_dict(), **tag})
        if self.scenario.target_title and _applied_naming(c, res, self.scenario.target_title):
            # a call of the seat's that the game applied and that names the target title
            self._title_calls.setdefault(seat, []).append({"step": self._step + 1, "game_date": date,
                                                          "tool": c.tool_name, **tag,
                                                          **({"intent": c.arguments.get("intent")}
                                                             if c.arguments.get("intent") else {})})
        witness = None
        if self.scenario.outcome is not None and c.tool_name in ("declare_war", "do"):
            rec = {"step": self._step + 1, "game_date": date, "call": c.to_dict(), "result": res.to_dict(),
                   **({"phase": phase, **tag} if order_id is not None else {})}
            if c.tool_name == "declare_war" and _outcome._applied(rec):
                # the strength read AT the declaration (outcome.py), on the seat's own server, the game paused
                da = c.arguments.get("declare_args") if isinstance(c.arguments.get("declare_args"), Mapping) \
                    else c.arguments
                tid = da.get("target_id") if isinstance(da, Mapping) else None
                witness = (_outcome.power(self.backend.outcome_reader(seat), tid) if isinstance(tid, int)
                           else {"unread": f"declare_war named no int target_id: {tid!r}"})
                rec["witness"] = witness
            self._outcome_calls.append(rec)
        if self.trajectory is not None:
            self.trajectory.write("call", step=self._step + 1, seat=seat, phase=phase, call=c.to_dict(),
                                  result=res.to_dict(), game_date=date,
                                  wall_s=round(time.monotonic() - t0, 6), **tag,
                                  **({"witness": witness} if witness is not None else {}))
        return res

    def _observe(self, seat: str, m=None) -> dict[str, Any]:
        m = m if m is not None else self._prev.get(seat)
        return {"seat": seat, "character_id": m.character_id if m is not None else self._characters.get(seat),
                "game_date": self._date, "step": self._step,
                # a wake task's step count is not known in advance: its steps end when the world says
                "steps_total": self.scenario.n_steps if self.scenario.wake is None else None,
                "cycle_days": self.scenario.cycle_days, "end_date": self.scenario.end_date,
                "metrics": {k: m.get(k)[0] for k in m.status} if m is not None else {},
                "metric_status": dict(m.status) if m is not None else {},
                "line_status": m.line_status if m is not None else None,
                "last_results": list(self._pending.get(seat, [])), "tools": list(self.scenario.tools),
                "backend": self.backend.name, "fake": self.backend.fake,
                "world": self._world(seat) if self._open else {},
                **({"comms": self.comms.observation(seat)} if self.comms is not None else {}),
                **({"wake": self._wake_observation()} if self.scenario.wake is not None else {}),
                **({"notes_pad": {**self._pad(seat), "max_bytes": self.notes_pad_bytes, **self._unwritten(seat)}}
                   if self.carry is not None else {}),
                **({"orders": list(self._order_news[seat])} if seat in self._order_news else {}),
                **({"orders_standing": self._book.standing(seat)}
                   if self._book is not None and self._book.live(seat) else {})}

    def _world(self, seat: str) -> dict[str, Any]:
        """The backend's per-seat observation; under a once goal, after the first holding, its `goal` line is the
        task's own (the title held, its reward paid once, play going on), never a line about creating it. When
        the news says the ruler changed and that change cancelled the seat's standing orders, the ruler_changed row
        names them beside what carried (a copy: the backend's read is kept)."""
        world = self.backend.observation(seat)
        gone = _orders.cancelled_by_ruler_change(self._order_news.get(seat))
        news = world.get("news") if isinstance(world, dict) else None
        rc = news.get("ruler_changed") if isinstance(news, dict) else None
        if gone and isinstance(rc, dict):
            tp = getattr(self.backend, "title_price", None)     # the price read for the new ruler at this date
            again = _orders.set_again_of(self._order_news.get(seat),
                                         (lambda k: tp(seat, k)) if callable(tp) else None)
            world = {**world, "news": {**news, "ruler_changed": {**rc, "orders_cancelled": {
                "orders": gone, "why": "an order dies with the ruler who set it (orders names each)",
                **({"set_again": again} if again else {})}}}}
        first = self._first_held.get(seat) if self.scenario.goal in ONCE_GOALS else None
        if first is None or not isinstance(world, dict):
            return world
        t, now = self.scenario.target_title, self._held_now.get(seat)
        held = {True: "your ruler holds it now", False: "your line does not hold it now",
                None: "whether your line holds it now did not read"}[now]
        return {**world, "goal": f"{t} was first held on {first.get('date')} (step {first.get('step')}): its reward "
                                 f"was paid then, once, and is not paid again; {held}. Play goes on to the end date "
                                 f"{self.scenario.end_date}; counties gained or lost still count."}

    # ── carried notes (notes_pad_bytes) ────────────────────────────────────
    def _unwritten(self, seat: str) -> dict[str, Any]:
        """`unwritten` in the observation after the step whose turn's notes were not written, else nothing."""
        u = self._notes_unwritten.get(seat)
        return {"unwritten": u["line"]} if u is not None and u["step"] == self._step else {}

    def _pad(self, seat: str) -> dict[str, Any]:
        assert self.carry is not None
        p = self.carry.private.get(seat) or {"text": "", "version": 0}
        return {"text": p["text"], "version": p["version"], "bytes": len(p["text"].encode("utf-8"))}

    def note_unwritten(self, seat: str, code: str, why: str, recorded: bool = False) -> str:
        """The seat's notes for this turn were not written (`code`, `why`) and its turn ends anyway; the
        observation after the step says so in `notes_pad.unwritten`. Recorded as a refused notes call unless
        write_notes already recorded it. Returns the line the seat will read."""
        self._live(seat)
        if self.carry is None:
            raise EnvRefusal(R_NOTES_NOT_KEPT, "this run keeps no notes pad (notes_pad_bytes)", "env")
        line = (f"your last turn's notes were NOT written ({why}); the pad below is the one before them, and that "
                "turn still ended")
        self._notes_unwritten[seat] = {"step": self._step + 1, "line": line}
        if self.trajectory is not None and not recorded:
            call = ToolCall("note_write", {"pad": _comms.PAD_PRIVATE, "mode": "edit"})
            self.trajectory.write("call", step=self._step + 1, seat=seat, phase="notes", call=call.to_dict(),
                                  result=ToolResult("note_write", False, error=f"{code}: {why}").to_dict(),
                                  game_date=self._date, wall_s=0.0)
        return line

    def write_notes(self, seat: str, text: Any) -> dict[str, Any]:
        """Replace `seat`'s carried notes pad with `text` (in its turn). Refused,
        never cut: no pad kept by this run, a non-string, or over the bytes."""
        self._live(seat)
        if self.carry is None:
            raise EnvRefusal(R_NOTES_NOT_KEPT, "this run keeps no notes pad (notes_pad_bytes)", "env")
        if not isinstance(text, str):
            raise EnvRefusal(R_NOTES_SHAPE, f"notes are a string, got {type(text).__name__}", "env")
        cur = self.carry.private[seat]["version"]
        call = ToolCall("note_write", {"pad": _comms.PAD_PRIVATE, "text": text, "mode": "replace", "version": cur})
        res = self.carry.handle(seat, call, self._step + 1, self._date)
        if self.trajectory is not None:       # every write, applied or refused, is a record (phase `notes`)
            self.trajectory.write("call", step=self._step + 1, seat=seat, phase="notes", call=call.to_dict(),
                                  result=res.to_dict(), game_date=self._date, wall_s=0.0)
        if not res.ok:
            # three turns running, a one-line replace left a pad 6-51 bytes over, each told only the total: the
            # bytes to cut, and that an edit keeps the rest of the pad
            n = len(text.encode("utf-8"))
            raise EnvRefusal(R_NOTES_TOO_LONG, f"{n} bytes > {self.notes_pad_bytes}: nothing written, nothing cut; "
                                               f"the next pad must be at least {n - self.notes_pad_bytes} bytes "
                                               "shorter (an edit keeps every line it does not touch)", "env")
        return self._pad(seat)

    # ── wake tasks (wake.py) ───────────────────────────────────────────────
    def check_wake(self, seat: str, request: Any) -> dict[str, Any]:
        """One live seat's wake request, checked as `step` will check it (the
        server's /submit asks this, so a bad request is refused to its sender)."""
        self._live(seat)
        if self.scenario.wake is None:
            raise EnvRefusal(R_WAKE_UNDECLARED, f"{self.scenario.id} is a fixed-cadence task (every step "
                                                f"{self.scenario.cycle_days} days); it declares no [wake]", "env")
        return _wake.parse_request(seat, request, cap_days=self.scenario.wake.max_days,
                                   default_days=self.scenario.cycle_days, vocabulary=self.backend.wake_vocabulary())

    def _wake_requests(self, wake: Optional[Mapping[str, Any]]) -> Optional[dict[str, dict]]:
        """None on a fixed task (a wake given there is refused); else every live
        seat's request record, the defaults included (declared false)."""
        w = self.scenario.wake
        if w is None:
            if wake:
                raise EnvRefusal(R_WAKE_UNDECLARED, f"{self.scenario.id} is a fixed-cadence task (every step "
                                                    f"{self.scenario.cycle_days} days); it declares no [wake]", "env")
            return None
        wake = {} if wake is None else wake
        if not isinstance(wake, Mapping):
            raise EnvRefusal(R_WAKE_SHAPE, "wake is a mapping seat -> {max_days, when}", "env")
        extra = [s for s in wake if s not in self.agents]
        if extra:
            raise EnvRefusal(R_UNKNOWN_SEAT, f"wake for {extra}: not live seats {self.agents}", "env")
        vocab = self.backend.wake_vocabulary()
        return {s: _wake.parse_request(s, wake.get(s), cap_days=w.max_days, default_days=self.scenario.cycle_days,
                                       vocabulary=vocab) for s in self.agents}

    def _advance_wake(self, req: dict[str, dict]) -> Any:
        """Run the clock to the first check where any seat's condition holds, the
        earliest max_days among the seats, or the horizon."""
        assert self.scenario.wake is not None and self._date is not None
        left = calendar.days_between(self._date, self.scenario.end_date)
        days = min([left] + [r["max_days"] for r in req.values()])
        forced = [{"event": e, "forced": True} for e in self.scenario.wake.forced]
        watched = {s: {"when": list(r["when"]) + forced} for s, r in req.items() if r["when"] or forced}
        # what each seat's conditions read: a backend reads only these at a check (a cheap probe)
        need = {s: frozenset(c.get("event") or c.get("metric") for c in w["when"]) for s, w in watched.items()}
        book = self._book
        if book:
            # STANDING ORDERS (orders.py): their seats are read at every check too, with their conditions' reads
            for s in book.seats():
                need[s] = need.get(s, frozenset()) | book.need(s)
        read = list(need)
        start = {s: self._wake_facts(s, need[s]) for s in read}

        def probe(date: str) -> list:
            now = {s: self._wake_facts(s, need[s]) for s in read}
            out = _wake.fired(watched, start, {s: now[s] for s in watched}, date)
            if book is not None:
                for s in book.seats():
                    book.check(s, start[s], now[s], date, self._fire_order)
                out += list(book.wakes)      # a refused fire wakes its seat, at this check and every later one
            return out
        # ALIGNED checks: the backend stops the clock on each check date; asked only when declared, so
        # an undeclared run calls the backend exactly as before
        aligned = {"aligned": True} if self.scenario.wake.aligned else {}
        adv = self.backend.advance_until(days, self.scenario.wake.check_days, probe if read else None, **aligned)
        ex = adv.extra if isinstance(adv.extra, dict) else {}
        # the witness goes in the step record only (not the seat's observation): one row per check
        cd = ex.get("check_dates")
        self._last_check_dates = dict(cd) if aligned and isinstance(cd, dict) else None
        ran, fired = ex.get("days"), list(ex.get("fired") or [])
        if not adv.reached:
            stop = "failure"
        elif fired:
            stop = "condition"
        elif ran == left:
            stop = "horizon"
        else:
            stop = "max_days"
        self._last_wake = {"days": ran, "checks": ex.get("checks"), "stop": stop, "fired": fired,
                           "planned_days": days,
                           **({"forced": list(self.scenario.wake.forced)} if self.scenario.wake.forced else {}),
                           # the backend's own account of its checks (the real game: probe cost, stop lag)
                           **({"probe": ex["wake_diag"]} if isinstance(ex.get("wake_diag"), dict) else {})}
        return adv

    # ── standing orders (orders.py) ────────────────────────────────────────
    def orders_on(self) -> bool:
        """Whether this task takes standing orders: a wake task (orders fire at its checks) not switched off."""
        return self.scenario.wake is not None and self.scenario.wake.orders

    def orders_setting(self) -> dict[str, Any]:
        """The trajectory header's record of the setting, on or off, and why."""
        if self.scenario.wake is None:
            return {"enabled": False, "why": "a fixed-cadence task has no checks to fire an order at"}
        if not self.scenario.wake.orders:
            return {"enabled": False, "why": "the task declares wake.orders = false"}
        return {"enabled": True, "max_orders": _orders.MAX_ORDERS, "tools": list(_orders.ORDER_TOOLS)}

    def check_orders(self, seat: str, orders: Any) -> list[dict]:
        """One live seat's orders, checked as `step` will check them (the server's /submit asks this)."""
        self._live(seat)
        if not self.orders_on():
            raise EnvRefusal(_orders.R_ORDERS_UNDECLARED, f"{self.scenario.id} takes no standing orders ("
                                                          f"{self.orders_setting()['why']})", "env")
        return _orders.parse_orders(seat, orders, vocabulary=self.backend.wake_vocabulary(),
                                    tools=tuple(self.scenario.tools),
                                    schema_of=lambda t: self.backend.tool_specs(seat, [t])[0].get("inputSchema"),
                                    standing=self._book.ids(seat) if self._book is not None else ())

    def order_notes(self, seat: str, parsed: list) -> list:
        """For each order that mints a title on a gold threshold, the price the tools read for it
        (orders.price_note), as end_turn's reply says it; [] when none."""
        tp = getattr(self.backend, "title_price", None)
        if not callable(tp):
            return []
        out = []
        for o in parsed or []:
            n = _orders.price_note(o, lambda k: tp(seat, k)) if not o.get("cancel") else None
            if n:
                out.append({"order": o["id"], **n})
        return out

    def _order_requests(self, orders: Optional[Mapping[str, Any]]) -> dict[str, list[dict]]:
        if not orders:
            return {}
        if not isinstance(orders, Mapping):
            raise EnvRefusal(_orders.R_ORDER_SHAPE, "orders is a mapping seat -> list of orders", "env")
        extra = [s for s in orders if s not in self.agents]
        if extra:
            raise EnvRefusal(R_UNKNOWN_SEAT, f"orders for {extra}: not live seats {self.agents}", "env")
        return {s: self.check_orders(s, o) for s, o in orders.items() if o is not None}

    def _fire_order(self, seat: str, order: dict, date: Optional[str]) -> ToolResult:
        do = order["do"]
        return self._exec(seat, ToolCall(do["tool"], dict(do["args"])), "order", date=date, order_id=order["id"])

    def _wake_facts(self, seat: str, need: frozenset) -> dict[str, Any]:
        """The backend's reading for `seat`'s conditions (`need`: the events and metrics they name)."""
        return self.backend.wake_facts(seat, need=need)

    def _wake_observation(self) -> dict[str, Any]:
        w = self.scenario.wake
        assert w is not None
        left = calendar.days_between(self._date, self.scenario.end_date) if self._date else None
        return {"check_days": w.check_days, "max_days": w.max_days, "default_days": self.scenario.cycle_days,
                "days_left": left, "last_step": dict(self._last_wake) if self._last_wake is not None else None,
                "conditions": {"events": {e: _wake.EVENTS[e] for e in self.backend.wake_vocabulary()["events"]},
                               "metrics": list(self.backend.wake_vocabulary()["metrics"]),
                               "ops": list(_wake.OPS), "max_conditions": _wake.MAX_CONDITIONS}}

    def _finish(self, reason: str) -> None:
        extra: dict[str, Any] = {}
        if self.scenario.outcome is not None and self._outcome_start is not None:
            # the end read, before the game closes (a game already gone answers each read `unread`)
            try:
                end = _outcome.read_standard(self.backend.outcome_reader(self.agents[0] if self.agents
                                                                         else self.scenario.seat_names[0]))
            except EnvRefusal as e:
                end = {"unread": f"{e.code}: {e.detail}"}
            extra = {"outcome_end": end,
                     "outcome": _outcome.score(self.scenario.outcome, self._outcome_start, end,
                                               self._outcome_calls)}
        if self.scenario.goal in ONCE_GOALS and self._first_held:
            # the primary outcome of a once goal, per seat, in the episode's end record (never read from the return)
            extra["target_title"] = {s: {"title": self.scenario.target_title,
                                         "held_at_any_step": self._first_held.get(s) is not None,
                                         "first_held": (dict(self._first_held[s]) if self._first_held.get(s) is not None
                                                        else None),
                                         "dates": self.creation_dates(self._first_held.get(s)),
                                         "held_at_last_step": self._held_now.get(s),
                                         "last_step_date": self._date, "end_date": self.scenario.end_date,
                                         "horizon_reached": (self._date is not None and calendar.ordinal(self._date)
                                                             >= calendar.ordinal(self.scenario.end_date))}
                                     for s in self.possible_agents if s in self._first_held}
        if self.trajectory is not None:
            self.trajectory.write("end", reason=reason, steps=self._step, returns=dict(self._returns), **extra)
        self._last_outcome = extra.get("outcome")
        self._open = False
        self._end_reason = reason
        self.agents = []

    @property
    def last_outcome(self) -> Optional[dict]:
        """The last episode's decision-task score (outcome.py), None when the scenario declares none."""
        return self._last_outcome

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


def _applied_naming(c: ToolCall, res: ToolResult, title: str) -> bool:
    """The game applied this call and its arguments name `title` (a mint, a decision)."""
    import json as _json
    if not res.ok or title not in _json.dumps(c.arguments, default=str):
        return False
    r = res.result if isinstance(res.result, Mapping) else {}
    inner = r.get("result") if isinstance(r.get("result"), Mapping) else r
    if r.get("ok") is False or r.get("blocked") not in (None, {}) or inner.get("blocked") not in (None, {}):
        return False
    return "applied" in (r.get("verdict"), inner.get("verdict"))
