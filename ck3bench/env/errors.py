"""Typed refusals and the closed vocabularies of the environment.

Every way the environment can decline to do something is an `EnvRefusal` with
a `code` from one of the tuples below and a `detail` naming what was looked
at. Nothing is defaulted: an unknown key, a missing declaration or a route
that has not been proven is a refusal, never a guess.
"""
from __future__ import annotations

from typing import Any


class EnvRefusal(Exception):
    """The environment declined. `code` is machine-readable, `detail` is prose,
    `leg` names the part of the environment that refused."""

    def __init__(self, code: str, detail: str = "", leg: str = "env"):
        super().__init__(f"{leg}: {code}: {detail}")
        self.code, self.detail, self.leg = code, detail, leg

    def as_dict(self) -> dict[str, Any]:
        return {"ok": False, "code": self.code, "detail": self.detail, "leg": self.leg}


# ── API misuse (the caller's error) ─────────────────────────────────────────
R_NOT_RESET = "not_reset"                       # step/call before reset
R_EPISODE_OVER = "episode_over"                 # step/call after every seat is done
R_UNKNOWN_SEAT = "unknown_seat"                 # an action for a seat that is not live
R_ACTION_SHAPE = "action_malformed"             # an action that is not a list of tool calls
R_SEED_TYPE = "seed_not_an_int"

# ── declarations ────────────────────────────────────────────────────────────
R_SCENARIO_UNKNOWN = "scenario_unknown"
R_SCENARIO_KEY = "scenario_unknown_key"
R_SCENARIO_MISSING = "scenario_missing_key"
R_SCENARIO_TYPE = "scenario_wrong_type"
R_SCENARIO_VALUE = "scenario_bad_value"
R_SCENARIO_SPEED = "scenario_speed_unreachable"  # a game speed above the fastest the bridge reaches
R_REWARD_UNKNOWN = "reward_unknown"
R_BACKEND_UNKNOWN = "backend_unknown"
R_BACKEND_MISMATCH = "scenario_needs_other_backend"
R_POLICY_UNKNOWN = "policy_unknown"
R_POLICY_NOT_APPLICABLE = "policy_not_applicable"   # a shipped policy that has no valid call for this task

# ── the real backend ────────────────────────────────────────────────────────
R_CHECKPOINT_UNSUPPORTED = "checkpoint_unsupported"   # no backend saves or restores at a step boundary
R_SAVE_MISSING = "start_save_missing"
R_SAVE_SHA = "start_save_sha256_mismatch"
R_LAUNCH = "launch_failed"
R_TOOLKIT_MISSING = "toolkit_not_installed"
R_START_DATE = "start_not_established"
R_SPEED = "speed_not_established"
R_SEAT_TRANSPORT = "seat_transport_failed"

# ── reading the world ──────────────────────────────────────────────────────
R_TOOL_SPECS = "tool_specs_unavailable"         # the backend cannot describe its tools' input schemas

# ── seats chosen from the world (census.py) ────────────────────────────────
R_CENSUS_READ = "census_read_unavailable"       # a census read was refused or malformed
R_CENSUS_TOO_FEW = "census_too_few_dukes"       # fewer qualifying vassals than the highest declared rank

# ── turns collected over the server (server.py, episode.py) ────────────────
R_TURN_TIMEOUT_UNDECLARED = "turn_timeout_not_declared"   # /submit needs a declared per-step deadline
R_ALREADY_SUBMITTED = "already_submitted"       # a second submission (or a call) after a seat's turn closed
R_SUBMISSIONS_PENDING = "submissions_pending"   # a direct /step while seats have submitted through /submit
R_STEP_PENDING = "step_pending"                 # /await reached its wait bound before the step ran
R_STOPPED_BY_SIGNAL = "stopped_by_signal"       # `env play` closed on SIGTERM/SIGINT before its episode ended

# ── the clock belongs to the step (types.clock_refusal) ────────────────────
# Both are RESULTS (`ok: false`, the code first in `error` and in `result.code`),
# like `tool_not_allowed`: the seat reads them and its turn goes on.
R_CLOCK_VERB = "clock_verb_refused"             # a call that would pause, unpause, set the speed or wait
R_SESSION_ACTION_UNVETTED = "session_action_unvetted"   # a session action the env has not classified

# ── the server ─────────────────────────────────────────────────────────────
R_REMOTE_BIND = "remote_bind_refused"
R_BAD_REQUEST = "bad_request"

#: Why an episode was TRUNCATED (ended from outside the task). The first is the
#: time limit; every other one is the game or the instrument failing, typed so a
#: trainer can drop or keep the episode on purpose. Never reported as success.
TRUNC_HORIZON = "horizon"
TRUNC_GAME_EXITED = "game_exited"               # the game process is gone
TRUNC_BRIDGE_UNAVAILABLE = "bridge_unavailable" # the bridge answered none of the retries
TRUNC_CLOCK_STALLED = "clock_stalled"           # the clock did not reach the cycle's end in its wall bound
TRUNC_METRIC_UNREADABLE = "reward_unreadable"   # a declared reward could not be read
TRUNC_SCRIPT_LOG_CLOSED = "script_log_closed"   # the game stopped writing the log action outcomes are read from
TRUNCATION_REASONS = (TRUNC_HORIZON, TRUNC_GAME_EXITED, TRUNC_BRIDGE_UNAVAILABLE, TRUNC_CLOCK_STALLED,
                      TRUNC_METRIC_UNREADABLE, TRUNC_SCRIPT_LOG_CLOSED)

#: Why ONE seat TERMINATED (ended on the task's own terms).
TERM_LINE_ENDED = "line_ended"                  # the bridge decided the played line has no successor
TERM_GOAL = "goal_reached"                      # a scenario's declared goal predicate held
TERM_TEAM_SEAT_DONE = "team_seat_done"          # reward.team = "seat": the seat the team is keyed on ended
TERMINATION_REASONS = (TERM_LINE_ENDED, TERM_GOAL, TERM_TEAM_SEAT_DONE)
