"""The data that crosses the environment's boundary: tool calls, their
results, the world metrics rewards are computed from, and what one clock
advance reports.

All of it is plain JSON (`to_dict` / `from_dict`), so a policy in any
language sees the same shapes over the env server as a Python policy sees
in-process.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Mapping, Optional, Sequence

from .errors import R_ACTION_SHAPE, EnvRefusal

#: The ck3agent toolkit's public game tools, as its MCP server registers them
#: (read from the server's own list_tools; a test pins it there when the
#: toolkit is importable). The environment does not add or remove any.
PUBLIC_TOOLS = ("query", "discover", "steer", "pass_turn", "observe", "plan_war", "declare_war",
                "succession_plan", "set_phase", "do", "inspect", "cbs", "session")
#: The tools a multi-agent seat may also have when its scenario declares them.
SEAT_TOOLS = ("msg_send", "note_read", "note_write", "guide_read")

# ── the clock belongs to the step ───────────────────────────────────────────
#: The toolkit tool that owns the game clock, and its actions, split by whether
#: they move it. Read from the toolkit's code, not its docs: `session` is the only
#: tool whose implementation sends a clock verb to the bridge (`pause_game`,
#: `unpause_game`, `set_speed`), through its `pause`, `unpause`, `speed` and
#: `wait` branches; every other action of it, and every other tool, is clock-free
#: (the toolkit's own clock-neutral test drives each one against a recorder).
#: test_mirrors.py checks this split against the toolkit's SESSION_ACTIONS
#: whenever the toolkit imports.
CLOCK_TOOL = "session"
SESSION_CLOCK_ACTIONS = ("speed", "pause", "unpause", "wait")
SESSION_CLOCK_FREE_ACTIONS = ("control", "add", "release", "whoami", "health", "experimental_flags",
                              "set_experimental")


def clock_refusal(call: "ToolCall") -> Optional[tuple[str, str]]:
    """(code, detail) when `call` would run or change the game clock inside a
    seat's turn, else None. In this environment the clock belongs to the step:
    it runs `cycle_days` when every seat has acted, and nowhere else. A session
    action the env has not classified is refused too (a new clock verb in a later
    toolkit must not pass through unseen)."""
    if call.tool_name != CLOCK_TOOL:
        return None
    from .errors import R_CLOCK_VERB, R_SESSION_ACTION_UNVETTED
    action = call.arguments.get("action", "")
    if action in SESSION_CLOCK_ACTIONS:
        return R_CLOCK_VERB, (f"session(action={action!r}) runs or changes the game clock; in this environment "
                              f"the clock belongs to the step (end your turn and the step runs the cycle)")
    if not isinstance(action, str) or action not in SESSION_CLOCK_FREE_ACTIONS:
        return R_SESSION_ACTION_UNVETTED, (f"session(action={action!r}) is not one the environment has "
                                           f"classified as clock-free {list(SESSION_CLOCK_FREE_ACTIONS)}")
    return None


@dataclass(frozen=True)
class ToolCall:
    """One tool call: the shape OpenEnv's CallToolAction uses (tool_name,
    arguments), so a policy written against that convention needs no adapter."""
    tool_name: str
    arguments: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"tool_name": self.tool_name, "arguments": dict(self.arguments)}

    @classmethod
    def from_dict(cls, d: Any) -> "ToolCall":
        if not isinstance(d, Mapping):
            raise EnvRefusal(R_ACTION_SHAPE, f"a tool call is an object, got {type(d).__name__}", "action")
        extra = set(d) - {"tool_name", "arguments"}
        if extra:
            raise EnvRefusal(R_ACTION_SHAPE, f"unknown keys in a tool call: {sorted(extra)}", "action")
        name, args = d.get("tool_name"), d.get("arguments", {})
        if not isinstance(name, str) or not name:
            raise EnvRefusal(R_ACTION_SHAPE, "tool_name must be a non-empty string", "action")
        if not isinstance(args, Mapping):
            raise EnvRefusal(R_ACTION_SHAPE, f"arguments must be an object, got {type(args).__name__}", "action")
        return cls(name, dict(args))


def parse_action(a: Any) -> list[ToolCall]:
    """An action is a LIST of tool calls (possibly empty: the seat does nothing
    this cycle and the game's own AI plays it). Anything else is refused."""
    if isinstance(a, ToolCall):
        return [a]
    if not isinstance(a, (list, tuple)):
        raise EnvRefusal(R_ACTION_SHAPE, f"an action is a list of tool calls, got {type(a).__name__}", "action")
    return [c if isinstance(c, ToolCall) else ToolCall.from_dict(c) for c in a]


@dataclass
class ToolResult:
    """What a tool call returned. `ok` False is an answer the policy reads
    (a refused tool, an unknown tool), never an exception: a model that sends
    a bad call learns from the reply, as it would on the MCP server."""
    tool_name: str
    ok: bool
    result: Any = None
    error: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


#: A metric's read status. Only `ok` carries a number; anything else is a
#: labelled absence (a partial read is never a number).
METRIC_OK = "ok"
METRIC_UNREADABLE = "unreadable"
METRIC_NOT_DECLARED = "not_declared"

#: The metrics a reward may name, each defined in one sentence. All are read for
#: the played LINE's current character (the seat's ruler at the start, then his
#: successors), the game paused.
METRIC_DEFINITIONS = {
    "realm_counties": "counties in the realm the character rules: the ones he holds himself plus every vassal's, "
                      "recursively (0 once the line has ended)",
    "demesne_counties": "counties the character holds himself, his vassals' not counted (0 once the line has "
                        "ended)",
    "gold": "the character's gold",
    "prestige": "the character's prestige",
    "line_alive": "whether the played line still has a ruler (the bridge's line watch)",
    "dejure_counties": "counties of the scenario's target title's de jure territory that lie in the realm the "
                       "character rules: held by him or by a vassal at any depth (0 once the line has ended)",
    "holds_target_title": "whether the character himself holds the scenario's target title (false once the line "
                          "has ended)",
    "ruler_changes": "how many times the played line has passed from one ruler to the next since the episode "
                     "began (the line watch counts each pass as the game makes it, not only at step ends)",
    "offensive_wars_declared": "wars declared since the episode began whose primary attacker was a ruler of the "
                               "played line at the time, whoever chose to declare (the seat's call or the game's "
                               "AI); a faction's war counts when he leads the faction (he is its attacker); "
                               "wars already open at the start are not counted",
    "heir_present": "whether the character's primary title has at least one heir in the game's succession line "
                    "(false once the line has ended)",
}
#: The metrics that read the scenario's `target_title`; a scenario that names
#: one of them, by a reward term or its goal, declares the title.
TARGET_METRICS = ("dejure_counties", "holds_target_title")
METRIC_NAMES = tuple(METRIC_DEFINITIONS)


@dataclass
class Metrics:
    """The world as one seat's reward sees it at one moment. Each value sits
    beside its own status; a value with a status other than `ok` is None."""
    game_date: Optional[str]
    character_id: Optional[int]
    values: dict[str, Any] = field(default_factory=dict)
    status: dict[str, str] = field(default_factory=dict)
    line_status: Optional[str] = None        # following | successor_pending | line_ended | None (unread)
    #: per metric, the read it came from (the backend's own words), or why it is unreadable
    source: dict[str, str] = field(default_factory=dict)
    #: the DYNASTY (not the house: a cadet house is the same dynasty) of the line's character as read,
    #: None when it did not read; `dynasty_source` is the read, or why it did not read
    dynasty_id: Optional[int] = None
    dynasty_source: Optional[str] = None

    def get(self, name: str) -> tuple[Any, str]:
        st = self.status.get(name, METRIC_NOT_DECLARED)
        return (self.values.get(name) if st == METRIC_OK else None), st

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


#: What a backend says about how one advance went, beside the step record's
#: `advance`: `reported` (the numbers it read), `not_applicable` (the world has
#: no such thing: the fake backend), `not_reported` (the backend does not say).
DIAG_REPORTED = "reported"
DIAG_NOT_APPLICABLE = "not_applicable"
DIAG_NOT_REPORTED = "not_reported"
DIAG_STATUSES = (DIAG_REPORTED, DIAG_NOT_APPLICABLE, DIAG_NOT_REPORTED)
DIAG_FIELDS = ("alarm_rearms", "dropped_frames")


@dataclass(frozen=True)
class AdvanceDiagnostics:
    """One step's advance diagnostics (Backend.advance_diagnostics).

    alarm_rearms    how many times the pause alarm was armed again during the
                    advance (the game read paused before the target, or running
                    with no alarm)
    dropped_frames  bridge replies dropped during the step because they named
                    another session's request (the reply was asked for again)

    Under `reported` at least one field is a non-negative int; a field left
    None there was not read this step (`detail` says why). Under any other
    status both are None: absence is never written as 0."""
    status: str
    alarm_rearms: Optional[int] = None
    dropped_frames: Optional[int] = None
    detail: str = ""

    def __post_init__(self):
        if self.status not in DIAG_STATUSES:
            raise ValueError(f"diagnostics status {self.status!r} not in {DIAG_STATUSES}")
        vals = [getattr(self, f) for f in DIAG_FIELDS]
        for f, v in zip(DIAG_FIELDS, vals):
            if v is not None and (not isinstance(v, int) or isinstance(v, bool) or v < 0):
                raise ValueError(f"{f} is {v!r}: a non-negative int or None")
        if self.status == DIAG_REPORTED and all(v is None for v in vals):
            raise ValueError("`reported` diagnostics carry at least one number")
        if self.status != DIAG_REPORTED and any(v is not None for v in vals):
            raise ValueError(f"`{self.status}` diagnostics carry no numbers")

    def to_dict(self) -> dict[str, Any]:
        return {"status": self.status, "alarm_rearms": self.alarm_rearms, "dropped_frames": self.dropped_frames,
                "detail": self.detail}


#: A step's game speed, declared beside read: `agreed` (every read was the declared speed),
#: `mismatch` (a read was another speed: the world did not run as declared), `unwitnessed` (the backend
#: read the clock and no read gave a speed), `not_reported` (the backend does not read it).
SPEED_AGREED, SPEED_MISMATCH, SPEED_UNWITNESSED, SPEED_NOT_REPORTED = "agreed", "mismatch", "unwitnessed", "not_reported"
SPEED_STATUSES = (SPEED_AGREED, SPEED_MISMATCH, SPEED_UNWITNESSED, SPEED_NOT_REPORTED)


def speed_witness(declared: int, reads: Optional[Sequence[Any]]) -> dict[str, Any]:
    """The step's speed record: the scenario's declared speed beside what the clock loop read while the
    step's clock ran (`reads`: each get_tick_info `game_speed`, None where a read gave none; None when the
    backend does not report reads). `read` is the distinct speeds read; never a default, never `agreed`
    without a read."""
    if reads is None:
        return {"declared": declared, "read": None, "reads": None, "unread": None, "status": SPEED_NOT_REPORTED}
    vals = [r for r in reads if isinstance(r, int) and not isinstance(r, bool)]
    distinct = sorted(set(vals))
    status = SPEED_UNWITNESSED if not vals else (SPEED_AGREED if distinct == [declared] else SPEED_MISMATCH)
    return {"declared": declared, "read": distinct, "reads": len(vals), "unread": len(reads) - len(vals),
            "status": status}


#: An ALIGNED wake step's check dates: each check's intended date beside the date the bridge read
#: when the clock stopped for it. `agreed` (every check read its own date), `mismatch` (a check read another
#: date: it was made off its date, `off` days late or early), `unread` (a stop gave no date, none read another),
#: `unwitnessed` (no check was made). Never a default, never `agreed` without a read.
CHECK_AGREED, CHECK_MISMATCH, CHECK_UNREAD, CHECK_UNWITNESSED = "agreed", "mismatch", "unread", "unwitnessed"
CHECK_STATUSES = (CHECK_AGREED, CHECK_MISMATCH, CHECK_UNREAD, CHECK_UNWITNESSED)


def check_dates_witness(rows: Sequence[tuple[str, Optional[str]]]) -> dict[str, Any]:
    """The step's `wake.check_dates`: (intended, read) per check, in order. `off` per row is read minus
    intended in game days (None when the stop gave no date); the record's `off` counts the rows per offset
    (the distribution); `status` as CHECK_*. One definition: the backends build it and the trajectory checker
    recomputes it from the rows (it never trusts the stored status)."""
    from . import calendar
    out, dist = [], {}
    for intended, read in rows:
        ok = calendar.ordinal(read) is not None and calendar.ordinal(intended) is not None
        off = calendar.ordinal(read) - calendar.ordinal(intended) if ok else None
        out.append({"intended": intended, "read": read, "off": off})
        if off is not None:
            dist[str(off)] = dist.get(str(off), 0) + 1
    offs = [r["off"] for r in out]
    if not out:
        status = CHECK_UNWITNESSED
    elif any(o not in (None, 0) for o in offs):
        status = CHECK_MISMATCH
    elif any(o is None for o in offs):
        status = CHECK_UNREAD
    else:
        status = CHECK_AGREED
    return {"mode": "aligned", "checks": len(out), "status": status, "off": dist, "rows": out}


@dataclass
class AdvanceResult:
    """One clock advance. `reached` is True only when the game was read paused
    AT the target date; otherwise `truncation` names why (errors.TRUNC_*)."""
    game_date: Optional[str]
    reached: bool
    truncation: Optional[str] = None
    detail: str = ""
    wall_s: float = 0.0
    extra: dict[str, Any] = field(default_factory=dict)
