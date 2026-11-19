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
from .errors import (R_LAUNCH, R_SAVE_MISSING, R_SAVE_SHA, R_SEAT_TRANSPORT, R_SPEED,
                     R_START_DATE, R_TOOL_SPECS, R_TOOLKIT_MISSING, TRUNC_BRIDGE_UNAVAILABLE, TRUNC_CLOCK_STALLED,
                     TRUNC_GAME_EXITED, TRUNC_SCRIPT_LOG_CLOSED, EnvRefusal)
from .scenario import Scenario
from .types import (DIAG_REPORTED, METRIC_OK, METRIC_UNREADABLE, AdvanceDiagnostics, AdvanceResult,
                    Metrics, ToolCall, ToolResult)

R_MODS = "mods_not_wired"
R_NOT_OPEN = "backend_not_open"
CK3_EXE_SUFFIX = "/binaries/ck3"
PID_LINE = re.compile(r"CK3 pid (\d+)")
GAME_SPEED_ENV = "CK3_EPISODE_GAME_SPEED"
CHARACTER_ENV = "CK3_AGENT_CHARACTER_ID"
INSTANCE_ENV = "CK3_INSTANCE_ID"
#: The measured unsteered clock rate (eight 100-year games: 0.87 to 0.99 game-days per second).
GD_PER_S = 0.9


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

    def call(self, tool_name: str, arguments: Mapping[str, Any]) -> Any:
        async def f(s):
            res = await s.call_tool(tool_name, dict(arguments))
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
        self._listing: Optional[tuple[Optional[str], Any]] = None   # (date, the census's ruler listing)
        self._tmp: Optional[Path] = None

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
        rc, out = self.host.run([self.ck3agent, "launch", "--save", str(copy)], env, self.launch_timeout + 30)
        m = PID_LINE.findall(out)
        pid = int(m[-1]) if m else None
        if rc != 0 or pid is None or not (self.host.exe(pid) or "").endswith(CK3_EXE_SUFFIX) \
                or "game paused" not in out:
            if pid is not None and (self.host.exe(pid) or "").endswith(CK3_EXE_SUFFIX):
                self.host.kill(pid)
            raise EnvRefusal(R_LAUNCH, f"rc {rc}, pid {pid}: {out[-300:]}", "ck3")
        self.pid = pid
        if staged_here and staged is not None and staged.is_file():
            self.staged = (staged, sha)
        self.sock = self.socket_resolver(self.instance_id)
        self.link = self.link_factory(self.sock, "ck3bench-env", "control")
        sp = self._call("set_speed", {"speed": scenario.game_speed})
        t = self._tick()
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
        if ranks:
            chosen, census = _census.select_by_rank(self._read_cached, int(scenario.seats[0].character_id), ranks)
        self.seats = {s.name: int(s.character_id) if s.character_id is not None else chosen[s.name]
                      for s in scenario.seats}
        for s, cid in self.seats.items():
            self._call("query_character", {"character_id": cid, "played_line": 1})
            try:
                self.transports[s] = self.transport_factory(s, cid, self._env(**{CHARACTER_ENV: str(cid)}))
            except EnvRefusal:
                raise
            except Exception as e:  # noqa: BLE001
                raise EnvRefusal(R_SEAT_TRANSPORT, f"seat {s}: {type(e).__name__}: {e}", "ck3") from None
        self._frames_reported = self.foreign_frames   # the open's own drops go in the header, not step 1
        return OpenedWorld(now, dict(self.seats),
                           {"backend": self.name, "fake": False, "start_save": scenario.start_save,
                            "start_save_sha256": sha, "declared_sha256": scenario.start_save_sha256,
                            "pid": pid, "instance_id": self.instance_id, "seed_controls": "seat order only",
                            "toolkit": self._toolkit_version(), "dropped_frames_at_open": self.foreign_frames,
                            **({"census": census} if census is not None else {})})

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
        top = prof.get("top_liege_id") if isinstance(prof, dict) else None
        top = top & 0xFFFFFF if isinstance(top, int) and not isinstance(top, bool) else None
        independent = None if (cid is None or top is None or top == 0) else top == cid
        realm_reply: Any = None
        if cid is not None and not ended and ("demesne_counties" in names or
                                              ("realm_counties" in names and independent is True)):
            realm_reply = self._call("query_realm", {"character_id": cid})
        rows: Any = None
        if cid is not None and not ended and "realm_counties" in names and independent is False:
            try:
                rows = _census.realm_rows(self._read_cached)
            except EnvRefusal as e:
                rows = e.detail
        for n in names:
            v: Any = None
            src = "no line character read" if cid is None else ""
            if n == "line_alive" and m.line_status is not None:
                v, src = m.line_status != "line_ended", "query_character played_line.status"
            elif n in ("realm_counties", "demesne_counties") and ended:
                v, src = 0, "the line has ended"
            elif n == "realm_counties" and cid is not None:
                v, src = self._realm_counties(cid, independent, realm_reply, rows)
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
