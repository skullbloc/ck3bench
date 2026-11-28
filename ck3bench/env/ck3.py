"""The REAL backend: Crusader Kings III through the ck3agent toolkit.

Run live (CK3 1.18.3.1, ck3agent 2026.12.11, no model): one-seat and six-seat
episodes to their horizon, writes applied while paused, a killed game typed
`game_exited`, a restore from the game's own autosave, two instances at once.
Every route here is one the toolkit's bridge already serves (named per
method). What it does not do it refuses by name rather than guessing:

  * the standing-policy / proposal mods     -> mods_not_wired (the toolkit
                                               does not install them, and
                                               neither does this backend)
  * checkpoint / restore at a step boundary -> checkpoint_unsupported (the
                                               Backend default: no bridge
                                               verb writes or loads a save)

THE ROUTE
  open     the user's own start save (a file name in `saves_dir`; its sha256
           checked when the scenario declares one) is copied and launched with
           `ck3agent launch --save COPY`, which waits for the game to load and
           leaves it paused; the game's pid is read from the launcher's
           `CK3 pid N` line and its /proc exe checked. The bridge socket is the
           toolkit's (`ck3agent._bridge.socket_path`, per CK3_INSTANCE_ID). One
           CONTROL session (`_bridge.Link`, mode control) writes `set_speed`
           and reads `get_tick_info` back; the clock is run to the scenario's
           start on the pause alarm and the date read back. Seats declared by
           census rank are chosen there (census.py: the first seat's vassals
           under the duke rule, read on the control session). Each seat gets its
           own toolkit MCP server (`ck3agent serve --transport stdio`) with
           CK3_AGENT_CHARACTER_ID set to its character, so a seat's tool calls
           are exactly the public tools' replies. Each seat's played-line watch
           is armed (`query_character {played_line: 1}`).
  call     the seat's MCP server, tools/call; the reply envelope is the result.
  advance  `unpause_game {pause_at: date+N}`, then `get_tick_info` polled
           until the game reads paused BY the alarm at the target. A pause
           that disarmed the alarm re-arms it (counted). The wall bound is
           N / gd_per_s x wall_margin + wall_slack_s; past it: clock_stalled.
           A bridge that answers none of `call_tries` calls: bridge_unavailable.
           The game's pid gone: game_exited. A tool reply this cycle that read
           the script log channel `closed`: script_log_closed (the game stopped
           writing the log action outcomes are read from).
  metrics  paused reads on the control session: the watch
           (`query_character {character_id: start, played_line: 1}`) names the
           line's current character and its state; `query_character` of that
           character gives gold, prestige and `top_liege_id`; realm and
           demesne counties as `metrics` documents. A read that fails or lacks
           its key is `unreadable`, never a number.
  close    the seats' servers stopped, the session closed, the game WE launched
           killed (SIGKILL: the game does not honour TERM), identity re-read
           from /proc first.
"""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence

from . import calendar
from .backend import Backend, OpenedWorld
from . import census as _census
from . import goal_line as _goal
from . import toolkit_id as _toolkit_id
from .errors import (R_CENSUS_READ, R_LAUNCH, R_SAVE_MISSING, R_SAVE_SHA, R_SEAT_TRANSPORT, R_SPEED,
                     R_START_DATE, R_TOOL_SPECS, R_TOOLKIT_MISSING, TRUNC_BRIDGE_UNAVAILABLE, TRUNC_CLOCK_STALLED,
                     TRUNC_GAME_EXITED, TRUNC_SCRIPT_LOG_CLOSED, EnvRefusal)
from .scenario import Scenario
from .types import (DIAG_REPORTED, METRIC_OK, METRIC_UNREADABLE, TARGET_METRICS, AdvanceDiagnostics,
                    AdvanceResult, Metrics, ToolCall, ToolResult, check_dates_witness)

R_MODS = "mods_not_wired"
R_NOT_OPEN = "backend_not_open"
CK3_EXE_SUFFIX = "/binaries/ck3"
PID_LINE = re.compile(r"CK3 pid (\d+)")
GAME_SPEED_ENV = "CK3_EPISODE_GAME_SPEED"
CHARACTER_ENV = "CK3_AGENT_CHARACTER_ID"
INSTANCE_ENV = "CK3_INSTANCE_ID"
#: Tells a seat's toolkit server that the environment owns the clock: it then lists no
#: clock action in `session` and refuses one (`clock_owned_by_env`).
CLOCK_OWNER_ENV = "CK3_CLOCK_OWNER"
#: The toolkit's shared-notes switch (`off` turns them off); seats' servers inherit it from the env's environment.
NOTES_ENV = "CK3_NOTES"
#: the toolkit's reply-pages switch (reply_fit.PAGES_ENV; `off` restores the cuts)
REPLY_PAGES_ENV = "CK3_REPLY_PAGES"
#: The notes set a task declares (Scenario.notes_set), passed to its seats' servers
NOTES_SET_ENV = "CK3_NOTES_SET"
#: the task's target title, for the seat's toolkit server: its create_title veto never drops the game's AI
#: creating that title for the seat (the task counts the title when the AI creates it)
GOAL_TITLE_ENV = "CK3_GOAL_TITLE"
#: the task's goal kind, for the seat's toolkit server: a goal that forbids offensive war (line_kept_peacefully)
#: gets observations without the conquest material (ck3agent/server/task_goal.py)
GOAL_KIND_ENV = "CK3_GOAL_KIND"
#: the `_meta` key of a call the bench makes for itself on the seat's server (ck3agent mcp_server
#: BENCH_READ_META, the same string): its paged reply is not kept for the seat's `inspect(subject="more")`
BENCH_READ_META = "ck3bench_own_read"
#: The measured unsteered clock rate (eight 100-year games: 0.87 to 0.99 game-days per second).
GD_PER_S = 0.9
#: The most rows a liege's court listing is asked for (a court read live held 26 to 32 ruler rows).
COURT_MAX = 500

#: WAKE tasks (wake.py): what this backend witnesses, every one a CONTROL-session read (never the seat's
#: own server: an observe(depth="news") probe would move the seat's news baseline):
#:   ruler_changed      the played-line watch (query_character {played_line: 1}) names another character
#:   gold, prestige,    query_character of the line's character
#:     piety
#:   war_started,       get_active_wars {character_id}: the war ids; war_declared_on keeps the wars in
#:   war_ended,           which he is not the attacker (`is_attacker` false, else `defender_id` == him)
#:   war_declared_on
#:   child_born         query_character of the line's character: his children's ids
#:   heir_other_dynasty query_title_succession_map {character_id}: the primary heir (the bridge's line with the
#:                        holder's children first, heir_line.h) when his dynasty_id is not the character's
#:   claim_gained       query_claims {character_id}: the claimed title ids
#:   fabrication_ended  query_council_tasks {character_id}: the targets of seats on task_fabricate_claim
#:   faction_over_      query_faction_threat {character_id}: the ids of factions against him, not at war,
#:     threshold          whose power_ratio is at or over FACTION_POWER_THRESHOLD
#:   title_gained,      query_title_hierarchy {character_id}: held_titles keys; title_gained is the
#:   title_lost           scenario's target title among them, title_lost a county-or-higher key gone
#:   realm_counties,    as metrics() reads it (query_realm for an independent ruler, the court listing
#:   realm_changed        for a vassal: 0.7-0.8 s), so read at every PAUSED check and only every
#:                        WAKE_REALM_EVERY-th check while the clock runs (or, with ALIGNED checks, every
#:                        WAKE_REALM_EVERY-th check before the step's end, and at the end)
#: Not witnessed: dejure_counties (read through the seat's own server's inspect). A read that does not
#: answer at a check is None there: it fires nothing, and the step's `wake.probe.unread` counts it.
WAKE_EVENTS = ("ruler_changed", "realm_changed", "title_gained", "title_lost", "war_started", "war_ended",
               "war_declared_on", "claim_gained", "fabrication_ended", "child_born", "faction_over_threshold",
               "heir_other_dynasty")
#: the game's demand power threshold (00_defines.txt NFaction DEFAULT_POWER_THRESHOLD; the same constant as
#: ck3agent/server/faction_letters.py POWER_THRESHOLD)
FACTION_POWER_THRESHOLD = 80
WAKE_METRICS = ("gold", "prestige", "piety", "realm_counties")
WAKE_REALM_EVERY = 4
FABRICATE_TASK = "task_fabricate_claim"
LANDED_TIERS = ("c_", "d_", "k_", "e_")
#: How many times a stop's alarm is armed again when the date has already reached the day asked for.
WAKE_STOP_ARMS = 5
#: The shortest wait between two clock reads in the aligned loop (_aligned_nap): a get_tick_info is a
#: control-session round trip of a few ms; 50 ms bounds the time a fast clock sits paused unread.
ALIGNED_NAP_MIN_S = 0.05



#: offensive_wars_declared reads the bridge's war-declaration ring (query_war_declarations): the newest WAR_RING
#: declarations the game made, anyone's, each with the tick it was seen and its primary attacker. The ring is
#: read at every metric read (the game paused) and at every wake check (the clock running; a read the bridge does
#: not serve there is counted in the check's `unread`). A read that counts nothing new keeps the ledger; a read
#: that shows the ring may have dropped declarations since the last read (it is full and every row is new) makes
#: the count unreadable for the rest of the episode, and so does a war monitor the bridge reports inactive after
#: the clock has run. Never a number the ring could not vouch for.
WAR_RING = 64


class _WarLedger:
    """One seat's war declarations since the episode began: the ring rows read (by war id and tick: an ended
    war's id may be used again), the line's rulers known, the declarations they made, and why the count can no
    longer be told (None while it can)."""

    def __init__(self) -> None:
        self.since: Optional[int] = None         # the ring's current_tick at the last read (None: no baseline)
        self.seen: set[tuple[Any, Any]] = set()
        self.line: set[int] = set()
        self.declared: list[dict] = []
        self.broken: Optional[str] = None


#: How long `ck3agent launch --save` waits for the game to go active. Its own default, 120 s, lost a start-up
#: race on a measured host (the game went active 119.5 s after the bridge loaded; another load there took
#: 101.1 s); 300 s is 2.5 times the slowest load measured. launch_timeout bounds the call.
LAUNCH_ACTIVE_WAIT_S = 300

class Host:
    """Every side effect on the machine; tests replace it."""

    def run(self, argv: Sequence[str], env: Mapping[str, str], timeout: float) -> tuple[int, str]:
        with tempfile.TemporaryFile(mode="w+") as out:
            # a file, never a pipe: the launcher leaves the game holding its stdout
            try:
                p = subprocess.run(list(argv), env=dict(env), stdout=out, stderr=subprocess.STDOUT,
                                   timeout=timeout, check=False)
                rc = p.returncode
            except subprocess.TimeoutExpired:
                rc = 124
            out.seek(0)
            return rc, out.read()

    def exe(self, pid: int) -> Optional[str]:
        try:
            return os.readlink(f"/proc/{pid}/exe")
        except OSError:
            return None

    def alive(self, pid: int) -> bool:
        return self.exe(pid) is not None

    def exists(self, pid: int) -> bool:
        """/proc/PID is there at all. A SIGKILLed game's exe link stops resolving
        as soon as its memory is torn down, seconds before it has closed its files
        (its bridge socket among them) and left /proc (read live)."""
        return os.path.exists(f"/proc/{pid}")

    def kill(self, pid: int) -> None:
        with contextlib.suppress(ProcessLookupError):
            os.kill(pid, signal.SIGKILL)

    def sleep(self, s: float) -> None:
        time.sleep(s)

    def monotonic(self) -> float:
        return time.monotonic()


class McpStdioTransport:
    """One seat's toolkit MCP server over stdio. One asyncio loop in a thread
    owns the client for its whole life (the MCP client's contexts must be
    entered and left in one task); calls are queued to it."""

    def __init__(self, argv: Sequence[str], env: Mapping[str, str], timeout: float = 120.0):
        self.argv, self.env, self.timeout = list(argv), dict(env), timeout
        self._loop = asyncio.new_event_loop()
        self._ready = threading.Event()
        self._error: Optional[BaseException] = None
        self._queue: Optional[asyncio.Queue] = None
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        if not self._ready.wait(timeout):
            raise EnvRefusal(R_SEAT_TRANSPORT, f"the MCP server did not initialise in {timeout:.0f} s", "ck3")
        if self._error is not None:
            e = self._error
            while isinstance(e, BaseExceptionGroup) and e.exceptions:
                e = e.exceptions[0]
            raise EnvRefusal(R_SEAT_TRANSPORT, f"{type(e).__name__}: {e}", "ck3")

    def _run(self) -> None:
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._main())
        except BaseException as e:  # noqa: BLE001
            self._error = self._error or e
            self._ready.set()

    async def _main(self) -> None:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
        self._queue = asyncio.Queue()
        params = StdioServerParameters(command=self.argv[0], args=self.argv[1:], env=self.env)
        async with stdio_client(params) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                self._ready.set()
                while True:
                    item = await self._queue.get()
                    if item is None:
                        return
                    fn, fut = item
                    try:
                        res = await fn(s)
                        self._loop.call_soon(fut.set_result, res) if not fut.done() else None
                    except Exception as e:  # noqa: BLE001
                        if not fut.done():
                            fut.set_exception(e)

    def _submit(self, fn) -> Any:
        if self._queue is None or not self._thread.is_alive():
            raise EnvRefusal(R_SEAT_TRANSPORT, "the seat's MCP server is not running", "ck3")

        async def put():
            fut = self._loop.create_future()
            await self._queue.put((fn, fut))
            return await fut
        return asyncio.run_coroutine_threadsafe(put(), self._loop).result(self.timeout)

    def list_tools(self) -> list[str]:
        async def f(s):
            return [t.name for t in (await s.list_tools()).tools]
        return self._submit(f)

    def list_tool_specs(self) -> list[dict[str, Any]]:
        """Every tool the server registers: {name, description, inputSchema}."""
        async def f(s):
            return [{"name": t.name, "description": t.description or "", "inputSchema": t.inputSchema}
                    for t in (await s.list_tools()).tools]
        return self._submit(f)

    def call(self, tool_name: str, arguments: Mapping[str, Any], meta: Optional[dict] = None) -> Any:
        async def f(s):
            try:
                res = await (s.call_tool(tool_name, dict(arguments), meta=meta) if meta else
                             s.call_tool(tool_name, dict(arguments)))
            except TypeError:
                if not meta:
                    raise
                res = await s.call_tool(tool_name, dict(arguments))     # an MCP client without `meta`: a plain call
            if getattr(res, "structuredContent", None):
                sc = res.structuredContent
                return sc.get("result", sc) if isinstance(sc, dict) and set(sc) == {"result"} else sc
            texts = [c.text for c in res.content if getattr(c, "type", "") == "text"]
            joined = "\n".join(texts)
            try:
                return json.loads(joined)
            except ValueError:
                return {"ok": not res.isError, "text": joined}
        return self._submit(f)

    def call_own(self, tool_name: str, arguments: Mapping[str, Any]) -> Any:
        """A call the bench makes for itself (a metric, a witness), marked in its `_meta` so the toolkit leaves
        the seat's kept reply pages as they were (a paged read of the bench's own must not reset them)."""
        return self.call(tool_name, arguments, meta={BENCH_READ_META: True})

    def health(self) -> Any:
        """session(action="health") on this server: the env's own provenance read
        (the notes the seat is served), never one of the seat's calls."""
        return self.call("session", {"action": "health"})

    def close(self) -> None:
        if self._queue is not None and self._thread.is_alive():
            asyncio.run_coroutine_threadsafe(self._queue.put(None), self._loop)
            self._thread.join(15)


def _default_link(sock: str, client: str, mode: str):
    try:
        from ck3agent import _bridge
    except ImportError as e:
        raise EnvRefusal(R_TOOLKIT_MISSING, f"ck3agent._bridge: {e}", "ck3") from None
    return _bridge.Link(sock, client=client, mode=mode)


#: When set, a file every control-session request and reply is appended to (one JSON line each: monotonic and
#: UTC time, action, params, reply, seconds the call took). Off by default; a live measurement's transcript.
CONTROL_LOG_ENV = "CK3BENCH_CONTROL_LOG"


class LoggedLink:
    """A control session that writes every request and its reply to `path` (CONTROL_LOG_ENV)."""

    def __init__(self, link: Any, path: str):
        self._link, self._f = link, open(path, "a", encoding="utf-8")

    def call(self, action: str, params: Optional[dict] = None, *a: Any, **kw: Any) -> Any:
        t0, utc = time.monotonic(), time.time()
        reply = self._link.call(action, params, *a, **kw)
        self._f.write(json.dumps({"t": round(t0, 4), "utc": round(utc, 3), "action": action, "params": params,
                                  "dt": round(time.monotonic() - t0, 4), "reply": reply}, default=str) + "\n")
        self._f.flush()
        return reply

    @property
    def error(self) -> Any:
        return getattr(self._link, "error", "")

    def close(self) -> None:
        try:
            self._link.close()
        finally:
            self._f.close()


def _default_socket(instance_id: Optional[str]) -> str:
    try:
        from ck3agent import _bridge
    except ImportError as e:
        raise EnvRefusal(R_TOOLKIT_MISSING, f"ck3agent._bridge: {e}", "ck3") from None
    old = os.environ.get(INSTANCE_ENV)
    try:
        if instance_id:
            os.environ[INSTANCE_ENV] = instance_id
        return _bridge.socket_path()
    finally:
        if instance_id:
            if old is None:
                os.environ.pop(INSTANCE_ENV, None)
            else:
                os.environ[INSTANCE_ENV] = old


def _default_game_saves(instance_id: Optional[str]) -> Optional[Path]:
    """The launched game's own `save games/` (the toolkit's game_user_dir: keyed on
    XDG_STATE_HOME, not on the instance id), where `ck3agent launch --save` stages
    its copy. None when the toolkit does not import (nothing is then removed)."""
    try:
        from ck3agent import _config
    except ImportError:
        return None
    return _config.game_user_dir() / "save games"


def _default_socket_alive(sock: str) -> bool:
    try:
        from ck3agent import _bridge
    except ImportError:
        return False
    return bool(_bridge.alive(sock))


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _foreign(reply: Any, action: str) -> bool:
    """A frame that answers some OTHER request. Read live on ck3agent 2026.12.11: the control
    session's first read after a seat's MCP server bound its character got that
    bind's result (`action: session_control`, `caller_request_id`, no
    `request_id`), broadcast by the bridge; the toolkit's correlation takes any
    frame without a request_id that is not a named push as the reply. Ours
    carry our request_id, or name our action."""
    if not isinstance(reply, dict):
        return False
    named = reply.get("action")
    if isinstance(named, str) and named != action:
        return True
    return "caller_request_id" in reply and "request_id" not in reply


def _id(v: Any) -> Optional[int]:
    return v & 0xFFFFFF if isinstance(v, int) and not isinstance(v, bool) else None


def _channel_closed(reply: Any) -> bool:
    """True when any nested `script_log_channel` in a tool reply reads closed."""
    if isinstance(reply, dict):
        ch = reply.get("script_log_channel")
        if isinstance(ch, dict) and ch.get("state") == "closed":
            return True
        return any(_channel_closed(v) for v in reply.values())
    if isinstance(reply, list):
        return any(_channel_closed(v) for v in reply)
    return False


class Ck3Backend(Backend):
    name = "ck3"
    fake = False

    def __init__(self, saves_dir: Optional[str | Path] = None, *, ck3agent: str = "ck3agent",
                 instance_id: Optional[str] = None, work_dir: Optional[str | Path] = None,
                 gd_per_s: float = GD_PER_S, wall_margin: float = 3.0, wall_slack_s: float = 120.0,
                 call_tries: int = 5, retry_s: float = 2.0, poll_s: float = 1.0, launch_timeout: float = 900.0,
                 exit_wait_s: float = 120.0, socket_probe: Optional[Callable[[str], bool]] = None,
                 host: Optional[Host] = None,
                 link_factory: Optional[Callable[[str, str, str], Any]] = None,
                 socket_resolver: Optional[Callable[[Optional[str]], str]] = None,
                 game_saves_resolver: Optional[Callable[[Optional[str]], Optional[Path]]] = None,
                 transport_factory: Optional[Callable[[str, int, Mapping[str, str]], Any]] = None):
        self.saves_dir = Path(saves_dir) if saves_dir is not None else None
        self.ck3agent, self.instance_id = ck3agent, instance_id
        self.work_dir = Path(work_dir) if work_dir is not None else None
        self.gd_per_s, self.wall_margin, self.wall_slack_s = gd_per_s, wall_margin, wall_slack_s
        self.call_tries, self.retry_s, self.poll_s = call_tries, retry_s, poll_s
        self.launch_timeout = launch_timeout
        self.exit_wait_s = exit_wait_s
        self.socket_probe = socket_probe or _default_socket_alive
        self.close_leftover: Optional[int] = None   # a pid killed at close that had not left /proc in exit_wait_s
        self.host = host or Host()
        self.link_factory = link_factory or _default_link
        self.socket_resolver = socket_resolver or _default_socket
        self.game_saves_resolver = game_saves_resolver or _default_game_saves
        self.staged: Optional[tuple[Path, str]] = None   # (the copy the launch staged in the game dir, its sha256)
        self.close_removed: list[str] = []               # what the last close removed from the game dir
        self.transport_factory = transport_factory or self._mcp_transport
        self.pid: Optional[int] = None
        self.link = None
        self.sock: Optional[str] = None
        self.seats: dict[str, int] = {}          # seat -> start character id (the line's start)
        self.transports: dict[str, Any] = {}
        self.date: Optional[str] = None
        self.channel_closed_at: Optional[str] = None
        self.foreign_frames = 0                  # replies dropped as another request's (see _foreign)
        self._frames_reported = 0                # foreign_frames already handed to a step's diagnostics
        self._speeds: Optional[list] = None      # the speeds the last advance's clock loop read (clock_speeds)
        self._listing: Optional[tuple[Optional[str], Any]] = None   # (date, the census's ruler listing)
        self._courts: dict[tuple[Optional[str], int], Any] = {}      # (date, liege) -> his court's ruler rows
        self._tmp: Optional[Path] = None
        self.target_title: Optional[str] = None     # the scenario's target title (TARGET_METRICS read it)
        self._target_id: Optional[int] = None       # its title id, as the seat's inspect(title) named it
        self._news: dict[str, tuple[Optional[str], Any]] = {}   # seat -> (date read, its toolkit news)
        self._wake_running = False                  # a check made while the clock runs (advance_until)
        self._wake_sub = False                      # an aligned check before the step's end (realm sub-cadence)
        self._aligned_rate: Optional[float] = None  # game days per wall second the aligned loop last saw
        self._wake_checks = 0                       # checks made in this advance (the realm read's sub-cadence)
        self._wake_unread: list[str] = []           # reads that did not answer at a check, this advance
        self._wars: dict[str, _WarLedger] = {}      # seat -> its war-declaration ledger (offensive_wars_declared)

    # ── open ──────────────────────────────────────────────────────────────
    def _env(self, **extra: str) -> dict[str, str]:
        env = dict(os.environ)
        if self.instance_id:
            env[INSTANCE_ENV] = self.instance_id
        env.update(extra)
        return env

    def _mcp_transport(self, seat: str, character_id: int, env: Mapping[str, str]):
        return McpStdioTransport([self.ck3agent, "serve", "--transport", "stdio"], env)

    def open(self, scenario: Scenario, seed: int) -> OpenedWorld:
        if self.pid is not None:
            self.close()
        self._listing = None
        self._news = {}
        self.target_title, self._target_id = scenario.target_title, None
        if scenario.policy_mod or scenario.proposal_mod:
            raise EnvRefusal(R_MODS, "the standing-policy and proposal mods are not installed by the toolkit, "
                                     "and this backend does not install them", "ck3")
        if self.saves_dir is None:
            raise EnvRefusal(R_SAVE_MISSING, "no saves_dir: the ck3 backend launches a save of your own", "ck3")
        save = self.saves_dir / str(scenario.start_save)
        if not save.is_file():
            raise EnvRefusal(R_SAVE_MISSING, f"{scenario.start_save} is not in the saves dir", "ck3")
        sha = sha256_file(save)
        if scenario.start_save_sha256 and sha != scenario.start_save_sha256:
            raise EnvRefusal(R_SAVE_SHA, f"declared {scenario.start_save_sha256}, read {sha}", "ck3")
        self._tmp = Path(tempfile.mkdtemp(prefix="ck3bench-env-", dir=self.work_dir))
        copy = self._tmp / save.name
        shutil.copy2(save, copy)
        env = self._env(**{GAME_SPEED_ENV: str(scenario.game_speed)})
        # the launcher copies the save into the game's own `save games/`; that copy is
        # ours to remove at close, but only if this launch put it there (a same-named file already
        # there is kept by the launcher, and kept by us)
        gsaves = self.game_saves_resolver(self.instance_id)
        staged = gsaves / copy.name if gsaves is not None else None
        staged_here = staged is not None and not staged.exists()
        rc, out = self.host.run([self.ck3agent, "launch", "--save", str(copy), "--wait", str(LAUNCH_ACTIVE_WAIT_S)],
                                env, self.launch_timeout + 30)
        m = PID_LINE.findall(out)
        pid = int(m[-1]) if m else None
        if rc != 0 or pid is None or not (self.host.exe(pid) or "").endswith(CK3_EXE_SUFFIX) \
                or "game paused" not in out:
            if pid is not None and (self.host.exe(pid) or "").endswith(CK3_EXE_SUFFIX):
                self.host.kill(pid)
            raise EnvRefusal(R_LAUNCH, f"rc {rc}, pid {pid}: `ck3agent launch` waited up to {LAUNCH_ACTIVE_WAIT_S}s "
                                       f"for the save to load and the game to go active: {out[-300:]}", "ck3")
        self.pid = pid
        if staged_here and staged is not None and staged.is_file():
            self.staged = (staged, sha)
        self.sock = self.socket_resolver(self.instance_id)
        self.link = self.link_factory(self.sock, "ck3bench-env", "control")
        if os.environ.get(CONTROL_LOG_ENV):
            self.link = LoggedLink(self.link, os.environ[CONTROL_LOG_ENV])
        sp = self._call("set_speed", {"speed": scenario.game_speed})
        t = self._tick()
        self._speed_at_open = (t or {}).get("game_speed")
        if sp is None or t is None or t.get("game_speed") != scenario.game_speed:
            raise EnvRefusal(R_SPEED, f"set_speed {scenario.game_speed} reads back "
                                      f"{(t or {}).get('game_speed')!r}", "ck3")
        now = self._date(t)
        s_ord = calendar.ordinal(scenario.start_date)
        if calendar.ordinal(now) is not None and calendar.ordinal(now) < s_ord:
            adv = self._run_clock(scenario.start_date, calendar.days_between(now, scenario.start_date))
            now = adv.game_date
        if now != scenario.start_date:
            raise EnvRefusal(R_START_DATE, f"the game reads {now!r}, the scenario starts {scenario.start_date}",
                             "ck3")
        self.date = now
        ranks = {s.name: int(s.census_rank) for s in scenario.seats if s.census_rank is not None}
        census = None
        chosen: dict[str, int] = {}
        seat_census: dict[str, dict] = {}
        for s in scenario.seats:
            if s.holder_of is not None:
                # a seat named by the title its ruler holds: resolved here, on this save, never written in the task
                chosen[s.name], seat_census[s.name] = _census.select_by_holder(
                    self._call, s.name, s.holder_of, str(s.holder_name), s.holder_name_key)
        if ranks:
            chosen_r, census = _census.select_by_rank(self._read_cached, int(scenario.seats[0].character_id), ranks)
            chosen.update(chosen_r)
        self.seats = {s.name: int(s.character_id) if s.character_id is not None else chosen[s.name]
                      for s in scenario.seats}
        self._wars = {s: _WarLedger() for s in self.seats} \
            if "offensive_wars_declared" in scenario.metric_names() else {}
        for s, cid in self.seats.items():
            self._call("query_character", {"character_id": cid, "played_line": 1})
            try:
                senv = self._env(**{CHARACTER_ENV: str(cid), CLOCK_OWNER_ENV: "env"})
                senv.pop(NOTES_SET_ENV, None)          # only the task's declaration chooses the set
                if scenario.notes_set:
                    senv[NOTES_SET_ENV] = scenario.notes_set
                senv.pop(GOAL_TITLE_ENV, None)         # only the task names the goal title
                if scenario.target_title:
                    senv[GOAL_TITLE_ENV] = scenario.target_title
                senv.pop(GOAL_KIND_ENV, None)          # only the task names its goal kind
                if scenario.goal:
                    senv[GOAL_KIND_ENV] = str(scenario.goal)
                self.transports[s] = self.transport_factory(s, cid, senv)
            except EnvRefusal:
                raise
            except Exception as e:  # noqa: BLE001
                raise EnvRefusal(R_SEAT_TRANSPORT, f"seat {s}: {type(e).__name__}: {e}", "ck3") from None
        self._frames_reported = self.foreign_frames   # the open's own drops go in the header, not step 1
        return OpenedWorld(now, dict(self.seats),
                           {"backend": self.name, "fake": False, "start_save": scenario.start_save,
                            "start_save_sha256": sha, "declared_sha256": scenario.start_save_sha256,
                            "pid": pid, "instance_id": self.instance_id, "seed_controls": "seat order only",
                            "toolkit": self._toolkit_version(), "toolkit_identity": _toolkit_id.identity(),
                            "notes": self._notes_read(),
                            "notes_switch": os.environ.get(NOTES_ENV) or "unset",
                            # the reply-pages switch the seats' servers inherit, when one is set
                            # (unset: the toolkit's default, on; the header then has no such key, as before)
                            **({"reply_pages_switch": os.environ[REPLY_PAGES_ENV]}
                               if os.environ.get(REPLY_PAGES_ENV) else {}),
                            # the declared speed beside the speed read back at open
                            "game_speed": {"declared": scenario.game_speed, "read_at_open": self._speed_at_open},
                            # the set declared beside each server's own report (`notes`: its `set`)
                            "notes_set_declared": scenario.notes_set or "default",
                            "dropped_frames_at_open": self.foreign_frames,
                            **({"census": census} if census is not None else {}),
                            **({"seat_census": seat_census} if seat_census else {})})

    def _notes_read(self) -> dict[str, Any]:
        """Each seat's toolkit shared notes as its OWN server reports them
        (`session(action="health")` result.notes: {state, version, notes} or
        {state: off, switch}); a server that did not answer, or whose health
        carries no notes (a toolkit from before them), is {"unread": why}."""
        out: dict[str, Any] = {}
        for seat in self.seats:
            health = getattr(self.transports.get(seat), "health", None)
            if health is None:
                out[seat] = {"unread": "the seat's transport has no health read"}
                continue
            what = 'session(action="health")'
            try:
                reply = health()
            except Exception as e:  # noqa: BLE001
                out[seat] = {"unread": f"{what}: {type(e).__name__}: {e}"}
                continue
            res = reply.get("result") if isinstance(reply, dict) and reply.get("ok") is True else None
            n = res.get("notes") if isinstance(res, dict) else None
            out[seat] = dict(n) if isinstance(n, dict) else \
                {"unread": f"{what} answered not ok" if res is None else f"{what} result carries no notes"}
        return out

    def _toolkit_version(self) -> Optional[str]:
        try:
            import importlib.metadata as md
            return md.version("ck3agent")
        except Exception:  # noqa: BLE001
            return None

    def tool_specs(self, seat: str, names: Sequence[str]) -> list[dict[str, Any]]:
        """The seat's own toolkit server's list_tools, the named tools only."""
        tr = self.transports.get(seat)
        lister = getattr(tr, "list_tool_specs", None)
        if lister is None:
            raise EnvRefusal(R_TOOL_SPECS, f"seat {seat} has no transport that lists its tools", "ck3")
        specs = {t["name"]: t for t in lister()}
        missing = [n for n in names if n not in specs]
        if missing:
            raise EnvRefusal(R_TOOL_SPECS, f"seat {seat}'s server does not register {missing}", "ck3")
        return [specs[n] for n in names]

    # ── bridge reads ──────────────────────────────────────────────────────
    def _call(self, action: str, params: Optional[dict] = None) -> Optional[dict]:
        if self.link is None:
            raise EnvRefusal(R_NOT_OPEN, action, "ck3")
        for n in range(1, self.call_tries + 1):
            r = self.link.call(action, params)
            if r is not None and _foreign(r, action):
                self.foreign_frames += 1     # another session's result, not our answer: ask again
                continue
            if r is not None:
                return r
            if n < self.call_tries:
                self.host.sleep(self.retry_s)
        return None

    def _tick(self) -> Optional[dict]:
        r = self._call("get_tick_info")
        return r if isinstance(r, dict) else None

    @staticmethod
    def _date(t: Optional[dict]) -> Optional[str]:
        if not t:
            return None
        d = t.get("game_date") or t.get("date")
        return d if isinstance(d, str) else None

    @staticmethod
    def _alarm(t: dict) -> Optional[str]:
        a = t.get("pause_alarm")
        return a.get("state") if isinstance(a, dict) else None

    # ── the step ──────────────────────────────────────────────────────────
    def call(self, seat: str, call: ToolCall) -> ToolResult:
        tr = self.transports.get(seat)
        if tr is None:
            return ToolResult(call.tool_name, False, error=f"{R_NOT_OPEN}: no transport for seat {seat}")
        try:
            reply = tr.call(call.tool_name, dict(call.arguments))
        except EnvRefusal as e:
            return ToolResult(call.tool_name, False, error=f"{e.code}: {e.detail}")
        except Exception as e:  # noqa: BLE001
            return ToolResult(call.tool_name, False, error=f"{R_SEAT_TRANSPORT}: {type(e).__name__}: {e}")
        if _channel_closed(reply) and self.channel_closed_at is None:
            self.channel_closed_at = self.date
        ok = reply.get("ok") is True if isinstance(reply, dict) else False
        err = None if ok else (reply.get("error") if isinstance(reply, dict) else None) or "not ok"
        return ToolResult(call.tool_name, ok, reply, None if ok else str(err))

    def advance(self, days: int) -> AdvanceResult:
        if self.date is None:
            raise EnvRefusal(R_NOT_OPEN, "advance before open", "ck3")
        if self.channel_closed_at is not None:
            return AdvanceResult(self.date, False, TRUNC_SCRIPT_LOG_CLOSED,
                                 f"a tool reply read the script log channel closed at {self.channel_closed_at}")
        return self._run_clock(calendar.add_days(self.date, days), days)

    def _run_clock(self, target: str, days: int) -> AdvanceResult:
        self._listing = None                 # the world moves: a listing read before is stale
        self._courts = {}
        self._speeds = []                    # every speed the loop reads (clock_speeds)
        t0 = self.host.monotonic()
        bound = days / self.gd_per_s * self.wall_margin + self.wall_slack_s
        rearms = 0
        if self.pid is not None and not self.host.alive(self.pid):
            return AdvanceResult(self.date, False, TRUNC_GAME_EXITED, f"pid {self.pid} is gone")
        if self._call("unpause_game", {"pause_at": target}) is None:
            return AdvanceResult(self.date, False, TRUNC_BRIDGE_UNAVAILABLE,
                                 f"unpause_game: no reply in {self.call_tries} tries: {self.link.error}")
        while True:
            if self.pid is not None and not self.host.alive(self.pid):
                return AdvanceResult(self.date, False, TRUNC_GAME_EXITED, f"pid {self.pid} is gone",
                                     self.host.monotonic() - t0)
            t = self._tick()
            if t is None:
                return AdvanceResult(self.date, False, TRUNC_BRIDGE_UNAVAILABLE,
                                     f"get_tick_info: no reply in {self.call_tries} tries",
                                     self.host.monotonic() - t0)
            now, paused, alarm = self._date(t), t.get("paused"), self._alarm(t)
            self._speeds.append(t.get("game_speed"))
            if now is not None:
                self.date = now
            if paused is True and alarm == "fired":
                if now == target:
                    return AdvanceResult(now, True, wall_s=self.host.monotonic() - t0, extra={"rearms": rearms})
                return AdvanceResult(now, False, TRUNC_CLOCK_STALLED,
                                     f"the alarm fired at {now}, the target was {target}",
                                     self.host.monotonic() - t0, {"rearms": rearms})
            if self.host.monotonic() - t0 > bound:
                return AdvanceResult(now, False, TRUNC_CLOCK_STALLED,
                                     f"not at {target} after {bound:.0f} s (read {now}, paused {paused}, "
                                     f"alarm {alarm})", self.host.monotonic() - t0, {"rearms": rearms})
            if (paused is True and alarm != "fired") or (paused is False and alarm in ("cleared", "none")):
                rearms += 1
                self._call("unpause_game", {"pause_at": target})
            self.host.sleep(self.poll_s)

    # ── wake tasks (wake.py) ─────────────────────────────────────────────
    def wake_vocabulary(self) -> dict[str, tuple]:
        """WAKE_EVENTS and WAKE_METRICS; once open, `title_gained` only when the
        scenario names a target title (there is nothing else it could mean)."""
        ev = WAKE_EVENTS if (self.date is None or self.target_title is not None) else \
            tuple(e for e in WAKE_EVENTS if e != "title_gained")
        return {"events": ev, "metrics": WAKE_METRICS}

    def wake_facts(self, seat: str, need: Optional[frozenset] = None) -> dict[str, Any]:
        """One check's reading for `seat` on the control session (WAKE_EVENTS
        says which read gives what), only the reads `need` names. Set-valued
        events are sorted id lists; a read that did not answer is None."""
        want = set(WAKE_EVENTS) | set(WAKE_METRICS) if need is None else set(need)
        facts: dict[str, Any] = {"character_id": None, "metrics": {}, "events": {}}

        def miss(what: str) -> None:
            self._wake_unread.append(what)
        w = self._call("query_character", {"character_id": self.seats[seat], "played_line": 1})
        watch = w.get("played_line") if isinstance(w, dict) else None
        line = watch.get("line_character_id") if isinstance(watch, dict) else None
        if not (isinstance(watch, dict) and watch.get("status") in ("following", "successor_pending")
                and isinstance(line, int) and not isinstance(line, bool)):
            miss("played_line")
            return facts
        cid = facts["character_id"] = line & 0xFFFFFF
        prof = None
        if want & {"gold", "prestige", "piety", "child_born", "heir_other_dynasty"}:
            prof = self._call("query_character", {"character_id": cid})
        if "heir_other_dynasty" in want:
            m = self._call("query_title_succession_map", {"character_id": cid})
            mine = prof.get("dynasty_id") if isinstance(prof, dict) else None
            hid = m.get("primary_heir_id") if isinstance(m, dict) else None
            hd = m.get("primary_heir_dynasty_id") if isinstance(m, dict) else None
            if not (isinstance(hid, int) and isinstance(mine, int)):
                miss("query_title_succession_map primary heir / query_character dynasty_id")
                facts["events"]["heir_other_dynasty"] = None
            else:
                facts["events"]["heir_other_dynasty"] = (
                    [str(hid)] if hid > 0 and isinstance(hd, int) and hd > 0 and hd != mine else [])
        if "child_born" in want:
            kids = prof.get("children") if isinstance(prof, dict) else None
            if not isinstance(kids, list):
                miss("query_character children")
            facts["events"]["child_born"] = None if not isinstance(kids, list) else sorted(
                {str(k) for k in kids if isinstance(k, int) and not isinstance(k, bool)})
        if want & {"gold", "prestige", "piety"}:
            for k in ("gold", "prestige", "piety"):
                v = prof.get(k) if isinstance(prof, dict) else None
                ok = isinstance(v, (int, float)) and not isinstance(v, bool)
                facts["metrics"][k] = float(v) if ok else None
                if k in want and not ok:
                    miss(f"query_character {k}")
        if want & {"war_started", "war_ended", "war_declared_on"}:
            rows = self._rows("get_active_wars", {"character_id": cid}, "wars")
            if rows is None:
                miss("get_active_wars")
            ids = None if rows is None else sorted({str(r["war_id"]) for r in rows if r.get("war_id") is not None})
            dfn = None if rows is None else sorted({str(r["war_id"]) for r in rows if r.get("war_id") is not None
                                                    and (r.get("is_attacker") is False if "is_attacker" in r
                                                         else _id(r.get("defender_id")) == cid)})
            facts["events"].update(war_started=ids, war_ended=ids, war_declared_on=dfn)
        if "claim_gained" in want:
            rows = self._rows("query_claims", {"character_id": cid}, "claims")
            if rows is None:
                miss("query_claims")
            facts["events"]["claim_gained"] = None if rows is None else \
                sorted({str(r["title_id"]) for r in rows if r.get("title_id") is not None})
        if "fabrication_ended" in want:
            rows = self._rows("query_council_tasks", {"character_id": cid}, "seats")
            if rows is None:
                miss("query_council_tasks")
            facts["events"]["fabrication_ended"] = None if rows is None else sorted(
                {str(r.get("target_title_id", r.get("target_province_id"))) for r in rows
                 if r.get("task_name") == FABRICATE_TASK})
        if "faction_over_threshold" in want:
            rows = self._rows("query_faction_threat", {"character_id": cid}, "factions")
            if rows is None:
                miss("query_faction_threat")
            facts["events"]["faction_over_threshold"] = None if rows is None else sorted(
                {str(r["faction_id"]) for r in rows if r.get("faction_id") is not None and not r.get("at_war")
                 and isinstance(r.get("power_ratio"), (int, float)) and not isinstance(r.get("power_ratio"), bool)
                 and r["power_ratio"] >= FACTION_POWER_THRESHOLD})
        if want & {"title_gained", "title_lost"}:
            rows = self._rows("query_title_hierarchy", {"character_id": cid}, "held_titles")
            if rows is None:
                miss("query_title_hierarchy")
            keys = None if rows is None else sorted({str(r["key"]) for r in rows if isinstance(r.get("key"), str)})
            facts["events"]["title_lost"] = None if keys is None else [k for k in keys if k.startswith(LANDED_TIERS)]
            facts["metrics"]["holds_target_title"] = None if (keys is None or self.target_title is None) \
                else self.target_title in keys
        if want & {"realm_counties", "realm_changed"}:
            due = not (self._wake_running or self._wake_sub) or self._wake_checks % WAKE_REALM_EVERY == 0
            if due:
                m = self.metrics(seat, ("realm_counties",))
                v, st = m.get("realm_counties")
                facts["metrics"]["realm_counties"] = v if st == METRIC_OK else None
                if st != METRIC_OK:
                    miss("realm_counties")
            else:
                facts["metrics"]["realm_counties"] = None      # not read at this running check (sub-cadence)
        return facts

    def _rows(self, action: str, params: dict, key: str) -> Optional[list]:
        r = self._call(action, params)
        rows = r.get(key) if isinstance(r, dict) else None
        return [x for x in rows if isinstance(x, dict)] if isinstance(rows, list) else None

    def advance_until(self, max_days: int, check_days: int,
                      probe: Optional[Callable[[str], list]], aligned: bool = False) -> AdvanceResult:
        """A wake step on the game (wake.py). The alarm is armed at
        `max_days`; while the clock RUNS, `probe(date)` is called whenever the
        date has moved `check_days` past the last check (the game is not paused
        for it). On a fire the alarm is armed again for the next day and the
        clock stops there; the probe is read once more, paused, at the stop: it
        still holds -> the step ends (`condition`); it does not (a reading torn
        while the clock ran) -> counted `torn` and the clock runs on to max_days.
        At max_days (paused) the probe is read once, as a last check. `extra`:
        days, checks, fired, rearms, wake_diag {checks_running, checks_paused,
        probe_wall_s, probe_wall_max_s, unread, torn, stop_arms, fire_read_date,
        stop_date, lag_days}. With `probe` None: one run of max_days. `aligned`:
        every check paused on its date instead (_run_clock_aligned)."""
        if self.date is None:
            raise EnvRefusal(R_NOT_OPEN, "advance before open", "ck3")
        start = self.date
        if self.channel_closed_at is not None:
            return AdvanceResult(self.date, False, TRUNC_SCRIPT_LOG_CLOSED,
                                 f"a tool reply read the script log channel closed at {self.channel_closed_at}",
                                 extra={"days": 0, "checks": 0, "fired": []})
        target = calendar.add_days(start, max_days)
        if probe is None:
            adv = self._run_clock(target, max_days)
            adv.extra = {**adv.extra, "days": calendar.days_between(start, self.date), "checks": 0, "fired": []}
            return adv
        if aligned:
            return self._run_clock_aligned(start, target, max_days, check_days, probe)
        return self._run_clock_probed(start, target, max_days, check_days, probe)

    def _run_clock_aligned(self, start: str, target: str, max_days: int, check_days: int,
                           probe: Callable[[str], list]) -> AdvanceResult:
        """ALIGNED checks (scenario.WakeDecl.aligned). The check dates are start + k * check_days and
        the step's end (`target`) last. For each: arm the bridge's game-day pause alarm at it (`unpause_game
        {pause_at}`, pause_alarm.h: the drain pauses at the first pass whose date is at or past it), wait for
        the clock read paused with the alarm `fired`, read the probe PAUSED there, and arm the next. The
        witness `extra["check_dates"]` (types.check_dates_witness) records each check's intended date beside
        the date read at its stop: a stop past its date (a frame that ran a day before the pause took hold)
        is `mismatch`, never rounded back; the check is still made there, and the next date stays on the
        schedule. A fire stops the step AT the check that read it (lag 0: the reading was paused, nothing is
        torn). A stop past the step's end is clock_stalled, as in the polled loop. The realm read keeps its
        sub-cadence at the checks before the end (every WAKE_REALM_EVERY-th) and is read at the end. The
        wait between reads shrinks as the alarm nears (the clock rate this loop measured), so a fast clock
        is not held paused for a whole poll after each stop."""
        self._listing, self._courts = None, {}
        self._wake_checks, self._wake_unread = 0, []
        self._speeds = []
        t0 = self.host.monotonic()
        bound = max_days / self.gd_per_s * self.wall_margin + self.wall_slack_s
        diag: dict[str, Any] = {"checks_running": 0, "checks_paused": 0, "probe_wall_s": 0.0,
                                "probe_wall_max_s": 0.0, "unread": 0, "torn": 0, "stop_arms": 0,
                                "fire_read_date": None, "stop_date": None, "lag_days": None}
        rearms, checks, fired = 0, 0, []
        rows: list[tuple[str, Optional[str]]] = []
        o_start, o_target = calendar.ordinal(start), calendar.ordinal(target)

        def intended_after(o_now: int) -> str:
            """The first check date strictly after the ordinal `o_now` on the step's schedule (the end last)."""
            k = (o_now - o_start) // check_days + 1
            return calendar.date_from_ordinal(min(o_start + k * check_days, o_target))

        def done(now: Optional[str], reached: bool, trunc: Optional[str] = None, detail: str = "") -> AdvanceResult:
            diag["unread"] = len(self._wake_unread)
            if self._wake_unread:
                diag["unread_reads"] = sorted(set(self._wake_unread))
            ran = calendar.days_between(start, self.date) if self.date else 0
            return AdvanceResult(now, reached, trunc, detail, self.host.monotonic() - t0,
                                 {"rearms": rearms, "days": ran, "checks": checks, "fired": fired,
                                  "wake_diag": diag, "check_dates": check_dates_witness(rows)})

        def check(date: str, last: bool) -> list:
            nonlocal checks
            self._wake_sub = not last
            t = self.host.monotonic()
            try:
                out = probe(date)
            finally:
                self._wake_sub = False
            for seat in self._wars:
                if self._poll_wars(seat, self._watch_ids(seat), False) is not None:
                    self._wake_unread.append("query_war_declarations")
            dt = self.host.monotonic() - t
            checks += 1
            self._wake_checks += 1
            diag["checks_paused"] += 1
            diag["probe_wall_s"] = round(diag["probe_wall_s"] + dt, 4)
            diag["probe_wall_max_s"] = round(max(diag["probe_wall_max_s"], dt), 4)
            return out

        def arm(at: str) -> Optional[str]:
            """Arm the alarm for `at` (running the clock), or the day after the date the bridge read when
            `at` is no longer in the future (counted in stop_arms); the day armed, or None."""
            for _ in range(WAKE_STOP_ARMS):
                r = self._call("unpause_game", {"pause_at": at})
                if isinstance(r, dict) and r.get("reason") == "pause_at_not_in_future" \
                        and calendar.ordinal(r.get("game_date")) is not None:
                    diag["stop_arms"] += 1
                    at = calendar.add_days(r["game_date"], 1)
                    continue
                return at if r is not None else None
            return None

        if self.pid is not None and not self.host.alive(self.pid):
            return done(self.date, False, TRUNC_GAME_EXITED, f"pid {self.pid} is gone")
        intended = intended_after(o_start)
        armed = arm(intended)
        if armed is None:
            return done(self.date, False, TRUNC_BRIDGE_UNAVAILABLE,
                        f"unpause_game: no reply in {self.call_tries} tries: {self.link.error}")
        seg_t, seg_o = self.host.monotonic(), o_start      # the running segment's start (the rate estimate)
        while True:
            if self.pid is not None and not self.host.alive(self.pid):
                return done(self.date, False, TRUNC_GAME_EXITED, f"pid {self.pid} is gone")
            t = self._tick()
            if t is None:
                return done(self.date, False, TRUNC_BRIDGE_UNAVAILABLE,
                            f"get_tick_info: no reply in {self.call_tries} tries")
            now, paused, alarm = self._date(t), t.get("paused"), self._alarm(t)
            self._speeds.append(t.get("game_speed"))
            if now is not None:
                self.date = now
            o_now = calendar.ordinal(now)
            if paused is True and alarm == "fired":
                wall = self.host.monotonic() - seg_t
                if o_now is not None and wall > 0 and o_now > seg_o:
                    self._aligned_rate = (o_now - seg_o) / wall
                rows.append((intended, now))
                at_end = o_now is not None and o_now >= o_target
                got = check(now, at_end)
                if got:
                    fired = got
                    diag.update(fire_read_date=now, stop_date=now, lag_days=0)
                    return done(now, True)
                if at_end or o_now is None:
                    if now == target:
                        return done(now, True)
                    return done(now, False, TRUNC_CLOCK_STALLED, f"the alarm fired at {now}, the target was {armed}")
                intended = intended_after(o_now)
                armed = arm(intended)
                if armed is None:
                    return done(now, False, TRUNC_BRIDGE_UNAVAILABLE,
                                f"unpause_game: no reply in {self.call_tries} tries: {self.link.error}")
                seg_t, seg_o = self.host.monotonic(), o_now
                continue
            if self.host.monotonic() - t0 > bound:
                return done(now, False, TRUNC_CLOCK_STALLED, f"not at {armed} after {bound:.0f} s (read {now}, "
                                                             f"paused {paused}, alarm {alarm})")
            wall = self.host.monotonic() - seg_t
            if paused is False and o_now is not None and o_now > seg_o and wall > 0:
                self._aligned_rate = (o_now - seg_o) / wall      # the clock seen running, not held by a stop
            if (paused is True and alarm != "fired") or (paused is False and alarm in ("cleared", "none")):
                rearms += 1
                armed = arm(armed) or armed
            self.host.sleep(self._aligned_nap(o_now, calendar.ordinal(armed)))

    def _aligned_nap(self, o_now: Optional[int], o_armed: Optional[int]) -> float:
        """The wait before the next read in the aligned loop: `poll_s`, shortened to just under the time the
        measured clock rate needs to reach the armed date (at least ALIGNED_NAP_MIN_S)."""
        if self._aligned_rate is None or o_now is None or o_armed is None:
            return self.poll_s
        eta = max(0, o_armed - o_now) / self._aligned_rate
        return min(self.poll_s, max(ALIGNED_NAP_MIN_S, 0.8 * eta))

    def _run_clock_probed(self, start: str, target: str, max_days: int, check_days: int,
                          probe: Callable[[str], list]) -> AdvanceResult:
        self._listing, self._courts = None, {}
        self._wake_checks, self._wake_unread = 0, []
        self._speeds = []
        t0 = self.host.monotonic()
        bound = max_days / self.gd_per_s * self.wall_margin + self.wall_slack_s
        diag: dict[str, Any] = {"checks_running": 0, "checks_paused": 0, "probe_wall_s": 0.0,
                                "probe_wall_max_s": 0.0, "unread": 0, "torn": 0, "stop_arms": 0,
                                "fire_read_date": None, "stop_date": None, "lag_days": None}
        rearms, checks, fired = 0, 0, []
        armed = target                         # the alarm's current target
        pending: Optional[tuple[str, list]] = None   # (the date a running check fired at, what fired)
        last = calendar.ordinal(start)

        def check(date: str, running: bool) -> list:
            nonlocal checks
            self._wake_running = running
            t = self.host.monotonic()
            try:
                out = probe(date)
            finally:
                self._wake_running = False
            for seat in self._wars:
                # between step ends the ring may fill: read it at every check too (WAR_RING)
                why = self._poll_wars(seat, self._watch_ids(seat), running)
                if why is not None:
                    self._wake_unread.append("query_war_declarations")
            dt = self.host.monotonic() - t
            checks += 1
            self._wake_checks += 1
            diag["checks_running" if running else "checks_paused"] += 1
            diag["probe_wall_s"] = round(diag["probe_wall_s"] + dt, 4)
            diag["probe_wall_max_s"] = round(max(diag["probe_wall_max_s"], dt), 4)
            return out

        def done(now: Optional[str], reached: bool, trunc: Optional[str] = None, detail: str = "") -> AdvanceResult:
            diag["unread"] = len(self._wake_unread)
            if self._wake_unread:
                diag["unread_reads"] = sorted(set(self._wake_unread))
            ran = calendar.days_between(start, self.date) if self.date else 0
            return AdvanceResult(now, reached, trunc, detail, self.host.monotonic() - t0,
                                 {"rearms": rearms, "days": ran, "checks": checks, "fired": fired,
                                  "wake_diag": diag})

        def arm(at: str) -> Optional[str]:
            """Arm the alarm for `at`, or the day after the date the bridge read
            when `at` is no longer in the future (counted); the day armed, or None."""
            for _ in range(WAKE_STOP_ARMS):
                r = self._call("unpause_game", {"pause_at": at})
                if isinstance(r, dict) and r.get("reason") == "pause_at_not_in_future" \
                        and calendar.ordinal(r.get("game_date")) is not None:
                    diag["stop_arms"] += 1
                    at = calendar.add_days(r["game_date"], 1)
                    continue
                return at if r is not None else None
            return None
        if self.pid is not None and not self.host.alive(self.pid):
            return done(self.date, False, TRUNC_GAME_EXITED, f"pid {self.pid} is gone")
        if self._call("unpause_game", {"pause_at": target}) is None:
            return done(self.date, False, TRUNC_BRIDGE_UNAVAILABLE,
                        f"unpause_game: no reply in {self.call_tries} tries: {self.link.error}")
        while True:
            if self.pid is not None and not self.host.alive(self.pid):
                return done(self.date, False, TRUNC_GAME_EXITED, f"pid {self.pid} is gone")
            t = self._tick()
            if t is None:
                return done(self.date, False, TRUNC_BRIDGE_UNAVAILABLE,
                            f"get_tick_info: no reply in {self.call_tries} tries")
            now, paused, alarm = self._date(t), t.get("paused"), self._alarm(t)
            self._speeds.append(t.get("game_speed"))
            if now is not None:
                self.date = now
            if paused is True and alarm == "fired":
                got = check(now, False)
                if pending is not None:
                    if got:
                        fired = got
                        diag.update(stop_date=now, lag_days=calendar.days_between(pending[0], now))
                        return done(now, True)
                    diag["torn"] += 1             # it held while running and not at the stop: not a fire
                    pending = None
                    if calendar.ordinal(now) < calendar.ordinal(target):
                        armed = arm(target) or target
                        continue
                if now == target:
                    fired = got
                    if got:
                        diag.update(fire_read_date=now, stop_date=now, lag_days=0)
                    return done(now, True)
                return done(now, False, TRUNC_CLOCK_STALLED, f"the alarm fired at {now}, the target was {armed}")
            if self.host.monotonic() - t0 > bound:
                return done(now, False, TRUNC_CLOCK_STALLED, f"not at {armed} after {bound:.0f} s (read {now}, "
                                                             f"paused {paused}, alarm {alarm})")
            if (paused is True and alarm != "fired") or (paused is False and alarm in ("cleared", "none")):
                rearms += 1
                self._call("unpause_game", {"pause_at": armed})
            elif paused is False and pending is None and now is not None \
                    and calendar.ordinal(now) - last >= check_days:
                last = calendar.ordinal(now)
                got = check(now, True)
                if got:
                    pending = (now, got)
                    diag["fire_read_date"] = now
                    stop = min(calendar.add_days(now, 1), target, key=calendar.ordinal)
                    armed = arm(stop) or armed
                    continue                     # poll at once: the stop is about a day away
            self.host.sleep(self.poll_s)

    def clock_speeds(self) -> Optional[list]:
        return list(self._speeds) if self._speeds is not None else None

    def advance_diagnostics(self, adv: AdvanceResult) -> AdvanceDiagnostics:
        """The step's alarm re-arms (from this advance) and the control-session
        replies dropped as another session's since the last step (the advance
        and the metric reads)."""
        frames = self.foreign_frames - self._frames_reported
        self._frames_reported = self.foreign_frames
        rearms = adv.extra.get("rearms") if isinstance(adv.extra, dict) else None
        if not isinstance(rearms, int) or isinstance(rearms, bool):
            return AdvanceDiagnostics(DIAG_REPORTED, None, frames,
                                      detail=f"alarm re-arms not read: the advance ended {adv.truncation or 'early'} "
                                             "before the clock loop counted them")
        return AdvanceDiagnostics(DIAG_REPORTED, rearms, frames)

    def _read_cached(self, action: str, params: dict) -> Optional[dict]:
        """The control session's read, except the census's ruler listing, which
        is read once per paused moment (the census at open and every vassal
        seat's `realm_counties` share it) and dropped whenever the clock runs."""
        if action != "search_characters" or params != _census.LISTING_PARAMS:
            return self._call(action, params)
        if self._listing is None or self._listing[0] != self.date:
            self._listing = (self.date, self._call(action, params))
        return self._listing[1]

    def _court_rows(self, liege: int) -> dict[int, dict]:
        """A vassal's row read NARROW: the ruler listing of his liege's court
        (`search_characters` with `court_of`: the liege's courtiers and direct
        vassals), whose rows carry the same `realm_counties` as the whole
        listing's. Read live on the 1066 start (the Duke of Normandy, liege the
        King of France): 13 to 16 KB in 0.81 s against the whole listing's 2.4 MB
        in 1.28 s, the duke's row 9 in both; the whole listing also stops at its
        5,000-row cap. Read once per liege per paused moment."""
        key = (self.date, liege)
        if key not in self._courts:
            reply = self._call("search_characters", {**_census.LISTING_PARAMS, "max_results": COURT_MAX,
                                                     "court_of": liege})
            why = _census._refusal(reply)
            self._courts[key] = (why, {r["id"]: r for r in _census._rows(reply, "characters")
                                       if isinstance(r.get("id"), int)} if why is None else None)
        why, rows = self._courts[key]
        if why is not None:
            raise EnvRefusal(R_CENSUS_READ, f"search_characters(ruler, court_of={liege}) answered {why!r}", "ck3")
        return rows

    @staticmethod
    def _line_ids(w: Any) -> set[int]:
        """The line's rulers a played-line watch names: who it is now and whom it last passed from."""
        out: set[int] = set()
        for k in ("start_character_id", "line_character_id", "previous_character_id"):
            v = w.get(k) if isinstance(w, dict) else None
            if isinstance(v, int) and not isinstance(v, bool) and v & 0xFFFFFF:
                out.add(v & 0xFFFFFF)
        return out

    def _watch_ids(self, seat: str) -> set[int]:
        r = self._call("query_character", {"character_id": self.seats[seat], "played_line": 1})
        return self._line_ids(r.get("played_line") if isinstance(r, dict) else None)

    def _poll_wars(self, seat: str, line_ids: set[int], running: bool) -> Optional[str]:
        """Read the war-declaration ring into `seat`'s ledger (WAR_RING). Returns None when the read counted, else
        why it did not (the ledger left unchanged, or broken for good when the ring may have dropped rows)."""
        led = self._wars[seat]
        if led.broken is not None:
            return led.broken
        led.line |= line_ids | {self.seats[seat]}
        r = self._call("query_war_declarations", {"since_tick": led.since or 0, "limit": WAR_RING})
        rows = r.get("declarations") if isinstance(r, dict) else None
        tick, total = (r.get("current_tick"), r.get("total_recorded")) if isinstance(r, dict) else (None, None)
        if not isinstance(rows, list) or not isinstance(tick, int) or not isinstance(total, int) \
                or isinstance(tick, bool) or isinstance(total, bool):
            why = _census._refusal(r) if isinstance(r, dict) and _census._refusal(r) else "no declarations list"
            return f"query_war_declarations{' (clock running)' if running else ''}: {why}"
        rows = [x for x in rows if isinstance(x, dict)]
        if led.since is None:
            # the baseline: what the ring holds now was declared before the episode began
            led.seen |= {(x.get("war_id"), x.get("tick")) for x in rows}
            led.since = tick
            return None
        if r.get("monitor_active") is False:
            led.broken = "the bridge's war monitor reads inactive after the clock ran: declarations were not seen"
            return led.broken
        new = [x for x in rows if (x.get("war_id"), x.get("tick")) not in led.seen]
        if total >= WAR_RING and len(new) >= total:
            # nothing the last read saw is still in the ring: what fell out between the reads is unknown
            led.broken = (f"the declaration ring was full ({total}) and held no row the last read (tick "
                          f"{led.since}) had seen: declarations may have been dropped between reads")
            return led.broken
        for x in new:
            led.seen.add((x.get("war_id"), x.get("tick")))
            a = x.get("attacker_id")
            if isinstance(a, int) and not isinstance(a, bool) and (a & 0xFFFFFF) in led.line:
                led.declared.append({"war_id": x.get("war_id"), "tick": x.get("tick"), "attacker_id": a & 0xFFFFFF,
                                     "cb_type": x.get("cb_type")})
        led.since = tick
        return None

    def _wars_metric(self, seat: str, line_ids: set[int]) -> tuple[Optional[int], str]:
        why = self._poll_wars(seat, line_ids, False)
        led = self._wars[seat]
        if why is not None:
            return None, why
        ids = ", ".join(str(d["war_id"]) for d in led.declared)
        return len(led.declared), (f"query_war_declarations: declarations since the episode began whose "
                                   f"attacker_id is a ruler of the line {sorted(led.line)}"
                                   + (f" (war ids {ids})" if ids else ""))

    def _heir_present(self, cid: int) -> tuple[Optional[bool], str]:
        """query_succession(cid) `primary_title_id`, then query_title_succession(that title) `heir_count`, the
        title's own heir line (its holder must be cid)."""
        s = self._call("query_succession", {"character_id": cid})
        tid = s.get("primary_title_id") if isinstance(s, dict) else None
        if not isinstance(tid, int) or isinstance(tid, bool) or tid <= 0:
            return None, f"query_succession({cid}) gave no primary_title_id"
        t = self._call("query_title_succession", {"title_id": tid})
        n = t.get("heir_count") if isinstance(t, dict) else None
        holder = t.get("current_holder_id") if isinstance(t, dict) else None
        if not isinstance(n, int) or isinstance(n, bool):
            return None, f"query_title_succession({tid}) gave no heir_count"
        if not isinstance(holder, int) or isinstance(holder, bool) or (holder & 0xFFFFFF) != cid:
            return None, f"query_title_succession({tid}) names holder {holder!r}, not {cid}"
        return n >= 1, f"query_title_succession({tid}) heir_count {n} (the primary title's heir line)"

    def metrics(self, seat: str, names: Sequence[str]) -> Metrics:
        """Every metric for the line's CURRENT character (types.METRIC_DEFINITIONS).

        realm_counties    an independent ruler (his query_character `top_liege_id`
                          is his own id): query_realm `realm_total_counties`, the
                          read the shipped baseline used. A vassal: his row of the
                          census's ruler listing (search_characters filter=ruler),
                          `realm_counties` = his own counties and his vassal
                          tree's; query_realm's total is NOT used for him, because
                          the bridge credits every holder to his independent top
                          liege only, so for a vassal it is his demesne.
        demesne_counties  query_realm `domain_size` (county-tier titles he holds).
        ruler_changes     the watch's own `successions` (the bridge counts every
                          pass of the line tick by tick from the arm at open).
        offensive_wars_declared  the war-declaration ring (WAR_RING, _poll_wars):
                          declarations whose attacker_id is a ruler of the line
                          (the watch's start, current and previous characters, and
                          every one read before).
        heir_present      _heir_present: the primary title's heir line.
        A read that fails, lacks its key or cannot tell a vassal from a ruler is
        `unreadable` (the reason in `source`), never a number."""
        start = self.seats[seat]
        m = Metrics(self.date, None)
        watch_reply = self._call("query_character", {"character_id": start, "played_line": 1})
        w = watch_reply.get("played_line") if isinstance(watch_reply, dict) else None
        line_char = w.get("line_character_id") if isinstance(w, dict) else None
        status = w.get("status") if isinstance(w, dict) else None
        if status in ("following", "successor_pending", "line_ended") and isinstance(line_char, int):
            m.line_status = status
            m.character_id = line_char & 0xFFFFFF
        ended = m.line_status == "line_ended"
        cid = m.character_id
        prof = self._call("query_character", {"character_id": cid}) if cid is not None else None
        # the line character's DYNASTY. The bridge derives query_character dynasty_id from the house
        # (char -> house -> its dynasty) and returns it only when the dynasty database round-trips it, else null; the
        # house id is not compared, since a cadet house of the same dynasty is still the seat's dynasty
        dyn = prof.get("dynasty_id") if isinstance(prof, dict) else None
        if isinstance(dyn, int) and not isinstance(dyn, bool) and dyn > 0:
            m.dynasty_id, m.dynasty_source = dyn & 0xFFFFFF, f"query_character({cid}) dynasty_id"
        else:
            m.dynasty_source = ("no line character read" if cid is None else
                                f"query_character({cid}) gave no dynasty_id ({dyn!r})" if isinstance(prof, dict)
                                else f"query_character({cid}) did not answer")
        top = prof.get("top_liege_id") if isinstance(prof, dict) else None
        top = top & 0xFFFFFF if isinstance(top, int) and not isinstance(top, bool) else None
        independent = None if (cid is None or top is None or top == 0) else top == cid
        realm_reply: Any = None
        if cid is not None and not ended and ("demesne_counties" in names or
                                              ("realm_counties" in names and independent is True)):
            realm_reply = self._call("query_realm", {"character_id": cid})
        rows: Any = None
        liege = prof.get("liege_id") if isinstance(prof, dict) else None
        liege = liege & 0xFFFFFF if isinstance(liege, int) and not isinstance(liege, bool) and liege > 0 else None
        if cid is not None and not ended and "realm_counties" in names and independent is False:
            try:
                rows = self._court_rows(liege) if liege is not None else _census.realm_rows(self._read_cached)
            except EnvRefusal as e:
                rows = e.detail
        target = self._target_reads(seat, cid, names) if cid is not None and not ended else {}
        for n in names:
            v: Any = None
            src = "no line character read" if cid is None else ""
            if n in TARGET_METRICS and ended:
                v, src = (0 if n == "dejure_counties" else False), "the line has ended"
            elif n in TARGET_METRICS and cid is not None:
                v, src = target[n]
            elif n == "line_alive" and m.line_status is not None:
                v, src = m.line_status != "line_ended", "query_character played_line.status"
            elif n == "ruler_changes":
                k = w.get("successions") if m.line_status is not None else None
                if isinstance(k, int) and not isinstance(k, bool) and k >= 0:
                    v, src = k, "query_character played_line.successions (passes since the watch was armed at open)"
                else:
                    src = "query_character played_line gave no successions" if m.line_status is not None \
                        else "the played-line watch did not read"
            elif n == "offensive_wars_declared":
                if seat not in self._wars:
                    src = "the war-declaration ledger was not opened for this task"
                else:
                    v, src = self._wars_metric(seat, self._line_ids(w) if m.line_status is not None else set())
            elif n == "heir_present" and ended:
                v, src = False, "the line has ended"
            elif n == "heir_present" and cid is not None:
                v, src = self._heir_present(cid)
            elif n in ("realm_counties", "demesne_counties") and ended:
                v, src = 0, "the line has ended"
            elif n == "realm_counties" and cid is not None:
                v, src = self._realm_counties(cid, independent, realm_reply, rows)
                if independent is False and liege is not None:
                    src = src.replace("search_characters(ruler)", f"search_characters(ruler, court_of={liege})")
            elif n == "demesne_counties" and cid is not None:
                d = realm_reply.get("domain_size") if isinstance(realm_reply, dict) else None
                if isinstance(d, int) and not isinstance(d, bool):
                    v, src = d, "query_realm domain_size"
                else:
                    src = "query_realm gave no domain_size"
            elif n in ("gold", "prestige") and isinstance(prof, dict) \
                    and isinstance(prof.get(n), (int, float)) and not isinstance(prof.get(n), bool):
                v, src = float(prof[n]), f"query_character {n}"
            if v is None:
                m.status[n] = METRIC_UNREADABLE
                m.source[n] = src or f"query_character gave no {n}"
            else:
                m.values[n], m.status[n], m.source[n] = v, METRIC_OK, src
        return m

    def observation(self, seat: str) -> dict[str, Any]:
        """`news`: what changed for the seat since its previous turn, as its own toolkit
        server says it: observe(depth="news") on the seat's server, which diffs against
        its own last news read and leaves the seat's observe news where it was. Read once
        per game date (the turn's start), so seat_observation within a turn repeats it.
        A read that failed is `{"unread": <the call and why>}`, never an empty block."""
        got = self._news.get(seat)
        if got is None or got[0] != self.date:
            # the turn's news is the seat's: its later pages are kept for the seat (own=False)
            res, what = self._seat_tool(seat, "observe", {"depth": "news"}, own=False)
            news = res.get("news") if isinstance(res, dict) and isinstance(res.get("news"), dict) else None
            if news is not None and isinstance(res.get("more"), dict):
                # the read was paged; say where the rest is, first, so the turn's news opens with the page line
                # and the blocks on the later pages (a seat seldom follows `more` on its own)
                news = {"more": res["more"], **news}
            got = self._news[seat] = (self.date, news if news is not None else {"unread": what})
        out = {"news": got[1]}
        goal = self._goal_line(seat)
        if goal is not None:
            out["goal"] = goal
        return out

    def _goal_line(self, seat: str) -> Optional[str]:
        """The turn's goal line (goal_line.py) on the target title, from the first turn on: that it can be created
        now (the exact call), that it waits for gold only, the chain of titles of the tier below it waits on (each
        with its price, and the death clause), or what else the game's own check says it lacks. Read once per game
        date after the news (so the toolkit's realised net is this step's): one formable_titles read on the seat's
        own server, a second with the chain's titles. None when there is no target, the read failed, or the target
        row gives no line. Under a once goal the env replaces it after the first holding (env.py _world). The
        months are unread at the first turn only (no step has run); the toolkit keeps the step's net by the ruler's
        id, which the read without a character_id passes."""
        if self.target_title is None:
            return None
        cache = getattr(self, "_goals", None)
        if cache is None:
            cache = self._goals = {}
        got = cache.get(seat)
        if got is None or got[0] != self.date:
            res, _what = self._seat_tool(seat, "do", {"action_class": "formable_titles",
                                                       "candidates": [self.target_title]})
            every = [r for k in ("available", "unavailable") for r in ((res or {}).get(k) or [])
                     if isinstance(r, dict)] if isinstance(res, dict) else []
            rows = [r for r in every if r.get("title_key") == self.target_title]
            extra = _goal.chain_keys(rows[0]) if rows else []
            if extra and not _goal.has_chain(rows[0]):
                # the target waits on titles of the tier below: the same read with them, so the line prices them.
                # the toolkit then reads each of them onto the target row (tier_rule.chain), the first row, which a
                # paged reply keeps on its first page; the line formats that, not the chain titles' own rows
                res2, _w2 = self._seat_tool(seat, "do", {"action_class": "formable_titles",
                                                         "candidates": [self.target_title] + extra})
                every2 = [r for k in ("available", "unavailable") for r in ((res2 or {}).get(k) or [])
                          if isinstance(r, dict)] if isinstance(res2, dict) else []
                rows2 = [r for r in every2 if r.get("title_key") == self.target_title]
                if rows2:
                    rows, every = rows2, every2
            got = cache[seat] = (self.date, _goal.goal_line(rows[0], every) if rows else None)
            # the target row's price, for a standing order that mints the title (title_price)
            self._goal_prices = dict(getattr(self, "_goal_prices", None) or {})
            self._goal_prices[seat] = (self.date, _goal.price_of(rows[0]) if rows else None)
        return got[1]

    def title_price(self, seat: str, title_key: Any) -> Optional[dict]:
        """The price of the scenario's target title as this date's goal read gave it ({title_key, cost,
        currency, at_death?}), else None: no other title is read for it (no extra read)."""
        got = (getattr(self, "_goal_prices", None) or {}).get(seat)
        if not got or got[0] != self.date or not isinstance(got[1], dict):
            return None
        return got[1] if got[1].get("title_key") == title_key else None

    def _seat_tool(self, seat: str, tool: str, args: dict, own: bool = True) -> tuple[Any, str]:
        """One public tool call on the seat's own toolkit server, for a metric:
        (the envelope's `result` or None, the call as written, or why it failed). `own` (the default) marks it
        the bench's own read (call_own), so its paged reply leaves the seat's kept pages alone."""
        tr = self.transports.get(seat)
        what = f"{tool}({', '.join(f'{k}={v}' for k, v in args.items())})"
        if tr is None:
            return None, f"{what}: the seat has no toolkit server"
        try:
            call = getattr(tr, "call_own", None) if own else None
            reply = call(tool, dict(args)) if callable(call) else tr.call(tool, dict(args))
        except Exception as e:  # noqa: BLE001
            return None, f"{what}: {type(e).__name__}: {e}"
        if not isinstance(reply, dict) or reply.get("ok") is not True or not isinstance(reply.get("result"), dict):
            err = (reply.get("blocked") or reply.get("error")) if isinstance(reply, dict) else None
            return None, f"{what} answered not ok: {json.dumps(err, default=str)[:200] if err else 'no result'}"
        return reply["result"], what

    def title_origin(self, seat: str) -> Mapping[str, Any]:
        """The newest entry of the target title's history (query title_history, the public tool on the
        seat's own server): {date, event, char_id, read}, or {"unread": why}."""
        tid = self._target_id
        if tid is None:
            return {"unread": f"the id of {self.target_title} was not read"}
        res, what = self._seat_tool(seat, "query", {"domain": "title_history", "title_id": tid})
        rows = res.get("entries") if isinstance(res, dict) else None
        e = next((x for x in rows or [] if isinstance(x, dict)), None)
        if e is None:
            return {"unread": what if res is None else f"{what} gave no entry"}
        cid = e.get("char_id")
        return {"date": e.get("date"), "event": e.get("event_type"),
                "char_id": (cid & 0xFFFFFF) if isinstance(cid, int) and not isinstance(cid, bool) else cid,
                "read": f"{what}: the newest entry"}

    reads_outcomes = True

    def outcome_reader(self, seat: str):
        """outcome.py's reads, each a public tool on the seat's own toolkit server (`_seat_tool`)."""
        return lambda tool, args: self._seat_tool(seat, tool, args)

    def _target_reads(self, seat: str, cid: int, names: Sequence[str]) -> dict[str, tuple[Any, str]]:
        """The target-title metrics for the line's character `cid`, read with the
        public tools on the seat's own server (the read that gives each value is
        its source):

        dejure_counties     inspect(subject=title, title_key=T, character_id=cid)
                            `summary.held`: the bridge's de jure share, a county
                            counted when its holder's liege chain reaches cid.
                            A total of 0 (the bridge's `titular_or_transient`) or
                            a missing number is unreadable.
        holds_target_title  inspect(subject=title_hierarchy, title_id=<T's id from
                            the read above>) `title.holder_id` == cid. The read
                            must name T's key; a title with no holder_id is held
                            by nobody (false)."""
        want = [n for n in names if n in TARGET_METRICS]
        if not want:
            return {}
        t = self.target_title
        if t is None:
            return {n: (None, "the scenario declares no target_title") for n in want}
        out: dict[str, tuple[Any, str]] = {}
        res, what = self._seat_tool(seat, "inspect", {"subject": "title", "title_key": t, "character_id": cid})
        summ = res.get("summary") if isinstance(res, dict) else None
        tid = res.get("title_id") if isinstance(res, dict) else None
        if isinstance(tid, int) and not isinstance(tid, bool) and tid > 0 and res.get("title_key") == t:
            self._target_id = tid
        held = summ.get("held") if isinstance(summ, dict) else None
        total = summ.get("total") if isinstance(summ, dict) else None
        if res is None:
            out["dejure_counties"] = (None, what)
        elif not (isinstance(held, int) and isinstance(total, int)) or isinstance(held, bool) or total <= 0:
            out["dejure_counties"] = (None, f"{what} gave held {held!r} of total {total!r}: no count")
        else:
            out["dejure_counties"] = (held, f"{what} summary.held (of {total})")
        if "holds_target_title" in want:
            if self._target_id is None:
                out["holds_target_title"] = (None, f"the title id of {t} was not read ({what})")
            else:
                r2, w2 = self._seat_tool(seat, "inspect", {"subject": "title_hierarchy", "title_id": self._target_id,
                                                          "character_id": cid})
                title = r2.get("title") if isinstance(r2, dict) else None
                if not isinstance(title, dict):
                    out["holds_target_title"] = (None, w2 if r2 is None else f"{w2} gave no title")
                elif title.get("key") != t:
                    out["holds_target_title"] = (None, f"{w2} names {title.get('key')!r}, not {t}")
                else:
                    h = title.get("holder_id")
                    if h is None:
                        out["holds_target_title"] = (False, f"{w2}: the title has no holder")
                    elif not isinstance(h, int) or isinstance(h, bool):
                        out["holds_target_title"] = (None, f"{w2} gave holder_id {h!r}")
                    else:
                        out["holds_target_title"] = ((h & 0xFFFFFF) == cid, f"{w2} title.holder_id {h & 0xFFFFFF}")
        return {n: out[n] for n in want}

    @staticmethod
    def _realm_counties(cid: int, independent: Optional[bool], realm_reply: Any, rows: Any
                        ) -> tuple[Optional[int], str]:
        if independent is None:
            return None, "query_character gave no top_liege_id: a ruler cannot be told from a vassal"
        if independent:
            n = realm_reply.get("realm_total_counties") if isinstance(realm_reply, dict) else None
            if not isinstance(n, int) or isinstance(n, bool):
                return None, "query_realm gave no realm_total_counties"
            unresolved = realm_reply.get("realm_total_counties_unresolved")
            note = f" (a lower bound: {unresolved} holders' lieges unresolved)" if unresolved else ""
            return n, "query_realm realm_total_counties (an independent ruler)" + note
        if not isinstance(rows, dict):
            return None, f"the ruler listing was not read: {rows}"
        row = rows.get(cid)
        n = row.get("realm_counties") if isinstance(row, dict) else None
        if not isinstance(n, int) or isinstance(n, bool):
            return None, f"character {cid} has no realm_counties row in the ruler listing"
        return n, "search_characters(ruler) realm_counties (a vassal: his and his vassal tree's counties)"

    # ── close ─────────────────────────────────────────────────────────────
    def close(self) -> None:
        for tr in self.transports.values():
            with contextlib.suppress(Exception):
                tr.close()
        self.transports = {}
        if self.link is not None:
            with contextlib.suppress(Exception):
                self.link.close()
            self.link = None
        if self.pid is not None and (self.host.exe(self.pid) or "").endswith(CK3_EXE_SUFFIX):
            self.host.kill(self.pid)
            # read live: a SIGKILLed game takes seconds to leave /proc, and its bridge socket answers until it
            # has (its exe link is gone long before); a launch in that window is refused `a bridge is
            # already listening`. Wait, bounded, for /proc/PID to go AND the socket to stop answering.
            t0 = self.host.monotonic()

            def lingering() -> bool:
                return self.host.exists(self.pid) or (self.sock is not None and self.socket_probe(self.sock))
            while lingering() and self.host.monotonic() - t0 < self.exit_wait_s:
                self.host.sleep(0.5)
            self.close_leftover = self.pid if lingering() else None
        self.pid = None
        self.date = None
        self._listing = None
        self.channel_closed_at = None
        self.close_removed = []
        if self.staged is not None:
            # only the file this open staged, only while it still holds what was staged (the game is
            # dead by now); the game's own autosaves and the launcher's continue_game.json stay
            p, sha = self.staged
            with contextlib.suppress(OSError):
                if p.is_file() and sha256_file(p) == sha:
                    p.unlink()
                    self.close_removed.append(str(p))
            self.staged = None
        if self._tmp is not None:
            shutil.rmtree(self._tmp, ignore_errors=True)
            self._tmp = None
