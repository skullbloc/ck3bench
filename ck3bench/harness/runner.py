"""One model seat, one fresh agent CLI session per turn, one episode; with a watchdog.

    python -m ck3bench.harness run-seat --out DIR --scenario munster1066-kingdom-once-30y-wake --seed 1
                   --claude claude --model MODEL_ID --saves-dir SAVES --turn-timeout-s 1200 --wall-budget-s 28800

What it does, in order (every file under DIR, which is the operator's alone: umask 077):
 1. The seat prompt is rendered from the task (ck3bench.harness.prompt: one template, the task's briefing and its
    scenario's numbers) before anything starts: a task the harness cannot describe costs no game.
 2. `ck3bench env play SCENARIO --external SEAT` (the env server and the runner; the game, for a ck3
    scenario) in its own process group; the URL from its first line; waits until the episode is reset.
 3. DIR/mcp.json: ONE MCP server, the env's shim for SEAT (`python -m ck3bench.env.mcp_shim`).
    A real MCP client (the `mcp` library) starts that exact command and lists its tools:
    DIR/tool_listing.json, before any session exists.
 4. Every turn is a NEW session of the agent CLI (Claude Code's `claude`, or any CLI that takes the same flags and
    speaks the same stream-json), stream-json in and out, flags in SESSION_FLAGS: no built-in tools
    (`--tools ""`), only that MCP server (`--strict-mcp-config`), only the `local` setting source
    in an empty cwd of the run (no user or project settings, hooks or CLAUDE.md), the seat prompt AS
    the system prompt, auto-memory off, no session persistence, no slash commands.
    Its `system/init` event is checked: the tools must be exactly the shim's (mcp__ck3seat__*),
    the MCP servers exactly one; anything else ends the episode (`surface_mismatch`).
    `env play` keeps a carried notes pad per seat (--notes-pad-bytes, 4096) and the shim runs `--end-turn ack`
    (end_turn takes the seat's rewritten `notes`, returns only an acknowledgement and refuses any later call). The
    session's one user message holds the observation (last turn's calls one line each) and the seat's notes pad;
    nothing else of an earlier turn is carried. Every line written to a session's stdin (the turn's message, and the
    reminder when one is sent) goes to DIR/session_stdin.jsonl as sent. The reply's events go to
    DIR/transcript.jsonl as they arrive (every session in order); at the reply's `result` the turn row (calls by tool, tokens as the session reported them,
    wall seconds, whether end_turn was called, the env's step after) goes to DIR/turns.jsonl.
 5. Watchdog ends the episode CLEANLY (session killed by its recorded process group, `env play`
    stopped by SIGTERM to its recorded pid so it closes the env, the game and its staged save;
    SIGKILL only if it outlives STOP_WAIT_S) when: the episode is over (`episode_over`); the session
    exits (`seat_exited`); a reply ends without end_turn after one reminder in its session (`seat_stopped`); a
    rate/usage limit is read (`rate_limited`); the session is SILENT (no stream event) for 2 x the turn timeout
    (`seat_hung`); the wall budget runs out with no end row, or the env's end row is stamped in the deadline's second
    or later, in both arms alike (`wall_budget`); SIGTERM/SIGINT to this runner (`stopped_by_signal`).
    DIR/end.json says which, with the env's own end line.
Hooks for an operator's own tooling (a screen recorder, say; none is part of this package): `--before-turns CMD`
runs once after the episode opened and before the first session; `--hold-first-turn-file F` sends turn 1 only once
F exists; `--frame-gate-dir D`: before each turn's prompt, when D/frame_due exists, the runner writes D/frame_now and
holds the turn (the game paused) until D/frame_done, bounded by --frame-gate-s. Each is bounded and never ends the
episode.
`--notes off` passes CK3_NOTES=off to `env play` (the seats' toolkit servers inherit it: the toolkit serves no notes);
the declared value goes to end.json beside what the seats' servers reported (the trajectory header's
provenance.notes), and a disagreement is said there (`notes_witness`).
SEAT SURFACE (each a switch, all declared in the trajectory header's `seat_surface` through
`env play --seat-surface`, a changed surface being a new measured condition):
  --reply-envelope lean|full (lean default): the shim sends game tools' replies without the envelope fields no
      recorded call drew on (mcp_shim.lean_reply); the trajectory keeps them whole.
  --reply-pages on|off (on default): the toolkit servers page observe, plan_war and succession_plan over the reply
      cap instead of cutting them (reply_fit.paginate; CK3_REPLY_PAGES for the seats' servers).
  --notes-edit on|off (on default): end_turn lists `notes_edit` (a patch to the pad by line number) and
      the turn prompt numbers the pad's lines; a patch is accepted by the shim either way.
  --obs-delta on|off (off default): the turn prompt's observation is what changed since the last
      turn's (obs_delta), the full one on turn 1 and after a ruler change; seat_observation reads it whole.
  --quiet-prompt on|off (off default): the seat prompt ends with QUIET_LINES (no narration between calls, no
      closing text).
No token, credential or key is read, copied or written by this file; the session reads its own
login where the agent CLI keeps it.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

from ck3bench.env.durable import write_whole
from ck3bench.harness.prompt import (CENSUS_TOKEN, NOTES_PAD_BYTES, QUIET_LINES, SERVER, TEMPLATE_PATH,  # noqa: F401
                                     TOOL_PREFIX, fill_census, render_prompt)

SHIM_TOOLS = ("seat_observation", "end_turn", "await_step")
STOP_WAIT_S = 180.0
RATE_RE = re.compile(r"rate.?limit|usage limit|limit reached|too many requests|\b429\b|overloaded", re.I)
#: Every flag the session gets besides the prompt, the model and the MCP config path.
SESSION_FLAGS = ["-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
                 "--tools", "", "--strict-mcp-config", "--setting-sources", "local",
                 "--permission-mode", "dontAsk", "--allowedTools", f"mcp__{SERVER}", f"mcp__{SERVER}__*",
                 "--disable-slash-commands", "--no-session-persistence"]
#: --obs-delta: the observation fields every turn prompt carries whole (what the turn is for: the date and
#: step, the scored metrics, the wake clock and what woke the seat); a dotted name is a field of a nested block.
#: The rest (the news, the wake conditions' vocabulary, the tools) is sent as what changed.
OBS_ALWAYS = ("seat", "character_id", "game_date", "step", "steps_total", "cycle_days", "end_date", "metrics",
              "metric_status", "line_status", "wake.check_days", "wake.max_days", "wake.default_days",
              "wake.days_left", "wake.last_step")
OBS_SECTION_DEPTH = 4


def _sections(o: dict, path: tuple = ()) -> dict:
    """{path: value} of an observation's sections: a dict's entries, descending into dicts to OBS_SECTION_DEPTH."""
    out = {}
    for k, v in o.items():
        p = path + (k,)
        if isinstance(v, dict) and v and len(p) < OBS_SECTION_DEPTH and ".".join(p) not in OBS_ALWAYS:
            out.update(_sections(v, p))
        else:
            out[p] = v
    return out


def _always(p: tuple) -> bool:
    return any(".".join(p[:i]) in OBS_ALWAYS for i in range(1, len(p) + 1))


def obs_delta(prev: dict | None, cur: dict) -> tuple[dict, list[str]]:
    """(the observation to send, the sections left out as unchanged). The whole observation when there is no
    previous one or the ruler changed (character_id differs); else the OBS_ALWAYS fields whole and, of the rest,
    only the sections whose value differs from the previous observation's (a section is a field, descending into
    nested blocks to OBS_SECTION_DEPTH: world.news.purse.gold), a section gone since listed under `gone`. Fields
    keep the observation's order."""
    if prev is None or prev.get("character_id") != cur.get("character_id"):
        return cur, []
    ps, cs = _sections(prev), _sections(cur)
    out: dict = {}
    same = []
    for p, v in cs.items():
        if not _always(p) and p in ps and (json.dumps(ps[p], sort_keys=True, default=str)
                                           == json.dumps(v, sort_keys=True, default=str)):
            same.append(".".join(p))
            continue
        d = out
        for x in p[:-1]:
            d = d.setdefault(x, {})
        d[p[-1]] = v
    gone = [".".join(p) for p in ps if p not in cs]
    if gone:
        out["gone"] = gone
    return out, same


#: each switch's OFF value: what a caller that names no switch (an older driver, a test's Namespace) gets
SURFACE_OFF = {"reply_envelope": "full", "reply_pages": "off", "notes_edit": "off", "obs_delta": "off",
               "quiet_prompt": "off"}


def sw(a, name: str) -> str:
    """A surface switch's value on the runner's arguments; its OFF value when the arguments do not carry it."""
    return getattr(a, name, None) or SURFACE_OFF[name]


def delta_head(same: list[str]) -> str:
    """The line above a delta observation."""
    return (f"YOUR OBSERVATION (JSON; only what changed since your last turn's; unchanged: {len(same)} sections, "
            f"in {', '.join(unchanged_groups(same))}; the whole of it: {TOOL_PREFIX}seat_observation):")


def unchanged_groups(same: list[str]) -> list[str]:
    """The unchanged sections named compactly: by their first two path parts, each once, in order."""
    out: list[str] = []
    for p in same:
        g = ".".join(p.split(".")[:2])
        if g not in out:
            out.append(g)
    return out


def pad_size(pad: dict, left_too: bool = True) -> str:
    """The pad's bytes used and left (a seat that cannot see the room left keeps sending a pad over the bound); the
    bytes left are shown with the default seat surface (notes_edit on), and not with every switch off."""
    used, cap = pad.get("bytes"), pad.get("max_bytes")
    left = f", {cap - used} left" if left_too and isinstance(used, int) and isinstance(cap, int) else ""
    return f"{used} of {cap} bytes{left}"


def turn_lines(obs: dict, pad: dict) -> str:
    """The lines a turn opens with before the notes, each only when it has something to say, taken out of
    the observation so nothing is said twice: GOAL (the target title blocked only by gold: world.goal), STANDING
    ORDERS (orders_standing) and the notes of the last turn that were not written (notes_pad.unwritten)."""
    out = []
    world = obs.get("world") if isinstance(obs.get("world"), dict) else {}
    goal = world.get("goal")
    if "goal" in world:
        obs["world"] = {k: v for k, v in world.items() if k != "goal"}
    if isinstance(goal, str) and goal:
        out.append(f"GOAL: {goal}")
    st = obs.pop("orders_standing", None)
    if isinstance(st, list) and st:
        out.append("STANDING ORDERS (still waiting for their conditions): " + "; ".join(map(str, st)))
    un = pad.get("unwritten")
    if isinstance(un, str) and un:
        out.append(f"NOTE: {un}.")
    return "".join(x + "\n\n" for x in out)


def numbered_pad(text: str) -> str:
    """--notes-edit: the pad's lines as `n|line`, the numbers end_turn's notes_edit names."""
    return "\n".join(f"{i}|{ln}" for i, ln in enumerate(text.split("\n"), 1)) if text else text


#: What the session's environment keeps from ours: enough to find its own login and run; nothing of the game's.
KEEP_ENV = ("HOME", "PATH", "LANG", "LC_ALL", "TERM", "USER", "LOGNAME", "XDG_RUNTIME_DIR", "TMPDIR")


def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def now_ms() -> str:
    """UTC with milliseconds: a turn sent in the same second as an operator's hook stamped something (a
    recorder's first frame, say) must not read as before it."""
    t = time.time()
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(t)) + f".{int(t * 1000) % 1000:03d}Z"


def append_line(fd: int, data: bytes) -> None:
    """DATA (one line) appended to the O_APPEND descriptor FD in one write(2) call, so another appender's bytes never
    land inside it; a short write (a full disk, a signal) is finished by further writes and never dropped."""
    n = os.write(fd, data)
    while n < len(data):
        data = data[n:]
        n = os.write(fd, data)


class Run:
    def __init__(self, a):
        self.a = a
        self.out = Path(a.out)
        self.out.mkdir(parents=True, exist_ok=True)
        self.t0 = time.monotonic()
        self.t0_wall = time.time()             # the same instant on the wall clock: the wall budget's deadline
        from ck3bench.harness.score import RUNNER_START  # score imports the runner only inside a call
        # that instant on the disk before anything else, the one start both this runner and the scorer
        # (score.end_row_budget) count the wall budget from
        write_whole(self.out / RUNNER_START, json.dumps({"start_s": self.t0_wall, "start_utc": time.strftime(
            "%Y-%m-%dT%H:%M:%SZ", time.gmtime(self.t0_wall))}) + "\n")
        self.play = None
        self.sess = None
        self.events: list[dict] = []          # this reply's events
        self.cv = threading.Condition()
        self.init = None
        self.exited = False
        self.last_event = time.monotonic()      # the session's last stream event (a reply may span many turns)
        self.end_reason = None
        self.sessions = 0
        self.tx = open(self.out / "transcript.jsonl", "a", encoding="utf-8")
        self.turns = open(self.out / "turns.jsonl", "a", encoding="utf-8")
        # one write(2) per line on an O_APPEND descriptor: a line is never split, whatever its length
        self.stdin_rec = os.open(self.out / "session_stdin.jsonl", os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        self.log = open(self.out / "runner.log", "a", encoding="utf-8")

    def L(self, msg: str) -> None:
        self.log.write(f"{now()} {msg}\n"); self.log.flush()

    # ── 1. the env ────────────────────────────────────────────────────────
    def start_play(self) -> str:
        a = self.a
        # --policy: the seat is one of the env's scripted policies (noop: the game's own AI alone), no external seat
        seat = ["--seat", f"{a.seat}={a.policy}"] if getattr(a, "policy", None) else ["--external", a.seat]
        argv = [a.python, "-m", "ck3bench.cli", "env", "play", a.scenario, "--seed", str(a.seed),
                *seat, "--turn-timeout-s", str(a.turn_timeout_s),
                "--out", str(self.out / "trajectory.jsonl")]
        for s in a.other or []:
            argv += ["--seat", s]
        argv += ["--notes-pad-bytes", str(a.notes_pad_bytes)]
        if a.saves_dir:
            argv += ["--saves-dir", a.saves_dir]
        if a.instance_id:
            argv += ["--instance-id", a.instance_id]
        if a.hold_before_close_s > 0:
            argv += ["--hold-before-close-s", str(a.hold_before_close_s)]
        if getattr(a, "game_speed", None) is not None:
            argv += ["--game-speed", str(a.game_speed)]
        argv += ["--seat-surface", json.dumps(seat_surface(a), sort_keys=True)]
        self.play_out = open(self.out / "play.out", "w", encoding="utf-8")
        penv = dict(os.environ)
        if a.notes == "off":
            penv["CK3_NOTES"] = "off"          # the seats' toolkit servers inherit it (no notes served)
        # the seats call the toolkit's tools as mcp__<SERVER>__<tool>; its hints name them so
        penv["CK3_TOOL_PREFIX"] = f"mcp__{SERVER}__"
        penv["CK3_REPLY_PAGES"] = sw(a, "reply_pages")    # the seats' toolkit servers inherit it
        self.play = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=open(self.out / "play.err", "w"),
                                     text=True, start_new_session=True, env=penv)
        self.L(f"env play pid {self.play.pid}: {' '.join(argv)} (notes {a.notes})")
        if getattr(a, "policy", None):
            self._drainer = threading.Thread(target=self._drain_play, daemon=True)
            self._drainer.start()
            return ""
        first = self.play.stdout.readline()
        self.play_out.write(first); self.play_out.flush()
        threading.Thread(target=self._drain_play, daemon=True).start()
        line = json.loads(first)
        if not line.get("ok") or "serving" not in line:
            raise SystemExit(f"env play did not serve: {first[:300]}")
        url = line["serving"]
        from ck3bench.env.server import EnvClient
        self.client = EnvClient(url, timeout=60)
        deadline = time.monotonic() + a.open_wait_s
        while time.monotonic() < deadline:
            if self.play.poll() is not None:
                raise SystemExit(f"env play exited rc {self.play.returncode} before the episode opened")
            try:
                if self.client.state().get("open"):
                    break
            except Exception:  # noqa: BLE001
                pass
            time.sleep(1)
        else:
            raise SystemExit(f"the episode did not open in {a.open_wait_s} s")
        self.L(f"episode open at {url}")
        return url

    def _drain_play(self) -> None:
        for line in self.play.stdout:
            self.play_out.write(line); self.play_out.flush()

    # ── 2. the surface ────────────────────────────────────────────────────
    def write_mcp(self, url: str) -> Path:
        a = self.a
        cfg = {"mcpServers": {SERVER: {"type": "stdio", "command": a.python,
                                       "args": ["-m", "ck3bench.env.mcp_shim", "--url", url, "--seat", a.seat,
                                                "--step-wait-s", str(a.step_wait_s)]
                                               + ["--end-turn", "ack"]
                                               + (["--reply-envelope", "lean"] if sw(a, "reply_envelope") == "lean" else [])
                                               + (["--notes-edit"] if sw(a, "notes_edit") == "on" else []),
                                       "env": {"PYTHONPATH": a.repo}}}}
        p = self.out / "mcp.json"
        p.write_text(json.dumps(cfg, indent=1))
        return p

    def list_with_mcp_client(self, cfg: Path) -> list[str]:
        import asyncio
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
        srv = json.loads(cfg.read_text())["mcpServers"][SERVER]

        async def go():
            p = StdioServerParameters(command=srv["command"], args=srv["args"],
                                      env={**{k: os.environ[k] for k in KEEP_ENV if k in os.environ}, **srv["env"]})
            async with stdio_client(p) as (r, w):
                async with ClientSession(r, w) as s:
                    await s.initialize()
                    return [{"name": t.name, "description": t.description, "inputSchema": t.inputSchema}
                            for t in (await s.list_tools()).tools]
        tools = asyncio.run(go())
        (self.out / "tool_listing.json").write_text(json.dumps(tools, indent=1))
        names = [t["name"] for t in tools]
        self.L(f"real MCP client listed {len(names)} tools: {names}")
        return names

    def before_turns(self) -> None:
        """--before-turns CMD: run once after the episode opened (the game paused at its start) and before
        the session exists (an operator's own setup step, a recorder's view say). Bounded by --before-turns-timeout-s; its exit
        code, a timeout or a failure to start are logged and never end or delay the episode further."""
        a = self.a
        if not a.before_turns:
            return
        t = time.monotonic()
        try:
            with open(self.out / "before_turns.log", "a", encoding="utf-8") as f:
                p = subprocess.Popen(shlex.split(a.before_turns) + [str(self.out)], stdout=f, stderr=subprocess.STDOUT,
                                     start_new_session=True)
                try:
                    rc = p.wait(a.before_turns_timeout_s)
                except subprocess.TimeoutExpired:
                    os.killpg(p.pid, signal.SIGKILL); p.wait(10)
                    rc = "timeout"
            self.L(f"before-turns rc {rc} in {time.monotonic() - t:.1f} s: {a.before_turns}")
        except Exception as e:  # noqa: BLE001
            self.L(f"before-turns failed to run ({type(e).__name__}: {e}); the episode goes on")

    def hold_first_turn(self) -> None:
        """--hold-first-turn-file F: turn 1 is not sent until F exists (an operator's recorder writes it once it
        holds a frame), so every turn opens inside the recording. The game is paused at the episode's open, so the
        hold costs no game time. Bounded by --hold-first-turn-s; a hook that never reports is logged and the
        episode goes on."""
        a = self.a
        if not a.hold_first_turn_file:
            return
        f, t = Path(a.hold_first_turn_file), time.monotonic()
        while not f.exists() and time.monotonic() - t < a.hold_first_turn_s:
            if self.play is not None and self.play.poll() is not None:
                break
            time.sleep(0.5)
        if f.exists():
            self.L(f"turn 1 held {time.monotonic() - t:.1f} s until the recorder's first frame ({f}: "
                   f"{f.read_text(errors='replace').strip()[:80]})")
        else:
            self.L(f"turn 1 NOT held further: no {f} in {time.monotonic() - t:.0f} s; the episode goes on "
                   f"(offsets before the recording may be negative)")

    # ── 3. the session ────────────────────────────────────────────────────
    def start_session(self, cfg: Path) -> None:
        a = self.a
        with self.cv:                            # a new session: nothing of the last one's stream carries over
            self.events, self.exited, self.init = [], False, None
            self.last_event = time.monotonic()
        self.sessions += 1
        cwd = self.out / "cwd"
        cwd.mkdir(exist_ok=True)
        prompt = self.prompt
        argv = [*shlex.split(a.claude), *SESSION_FLAGS, "--model", a.model, "--mcp-config", str(cfg), "--system-prompt", prompt]
        env = {k: os.environ[k] for k in KEEP_ENV if k in os.environ}
        env["CLAUDE_CODE_DISABLE_AUTO_MEMORY"] = "1"
        for k in a.session_env or []:            # the dry run's stand-in reads its mode this way
            if k in os.environ:
                env[k] = os.environ[k]
        (self.out / "session_argv.json").write_text(json.dumps(
            {"argv": [x if x != prompt else f"<seat prompt: {a.prompt}>" for x in argv], "cwd": str(cwd),
             "env_keys": sorted(env)}, indent=1))
        self.sess = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=open(self.out / "session.err", "a"), text=True, cwd=cwd, env=env,
                                     start_new_session=True)
        self.L(f"session {self.sessions} pid {self.sess.pid} (its own process group)")
        threading.Thread(target=self._read_session, daemon=True).start()

    def _read_session(self) -> None:
        for line in self.sess.stdout:
            self.tx.write(line); self.tx.flush()
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            with self.cv:
                self.last_event = time.monotonic()
                if ev.get("type") == "system" and ev.get("subtype") == "init":
                    self.init = ev
                self.events.append(ev)
                self.cv.notify_all()
        with self.cv:
            self.exited = True
            self.cv.notify_all()

    def check_init(self, want: list[str]) -> tuple[bool, str]:
        tools = list(self.init.get("tools") or [])
        servers = [s.get("name") for s in self.init.get("mcp_servers") or []]
        expect = sorted(f"mcp__{SERVER}__{t}" for t in want)
        ok = sorted(tools) == expect and servers == [SERVER]
        rec = {"tools": tools, "mcp_servers": self.init.get("mcp_servers"), "model": self.init.get("model"),
               "expected_tools": expect, "ok": ok}
        (self.out / "session_init.json").write_text(json.dumps(rec, indent=1))
        return ok, json.dumps(rec)[:400]

    def send(self, text: str) -> None:
        """One user message to the session's stdin; the same line, byte for byte, is appended to
        DIR/session_stdin.jsonl once it is written (the record of what the seat was told each turn). The line holds
        the message only: the session's prompt, flags and environment are not in it. This runner is the record's one
        writer, and each line goes in whole (append_line)."""
        line = json.dumps({"type": "user", "message": {"role": "user", "content": text}}) + "\n"
        self.sess.stdin.write(line); self.sess.stdin.flush()
        append_line(self.stdin_rec, line.encode("utf-8"))

    def wait_reply(self, limit_s: float):
        """The events of one reply, up to its `result`; None when the session exited, "timeout" when the
        session has been SILENT (no stream event at all) for limit_s. A reply may play many turns (a seat
        that keeps calling end_turn), so its length is no measure of a hang; silence is."""
        with self.cv:
            while True:
                for i, ev in enumerate(self.events):
                    if ev.get("type") == "result":
                        got, self.events = self.events[:i + 1], self.events[i + 1:]
                        return got
                if self.exited:
                    return None
                left = self.last_event + limit_s - time.monotonic()
                if left <= 0:
                    return "timeout"
                self.cv.wait(min(left, 5))

    # ── 4. turns ──────────────────────────────────────────────────────────
    @staticmethod
    def account(evs: list[dict]) -> dict:
        calls: dict[str, int] = {}
        usage = {"input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 0,
                 "cache_creation_input_tokens": 0}
        seen_ids = set()
        for ev in evs:
            if ev.get("type") != "assistant":
                continue
            m = ev.get("message") or {}
            for b in m.get("content") or []:
                if isinstance(b, dict) and b.get("type") == "tool_use":
                    n = str(b.get("name", "")).removeprefix(f"mcp__{SERVER}__")
                    calls[n] = calls.get(n, 0) + 1
            mid = m.get("id")
            if mid in seen_ids:          # one API message can arrive as several events; count its usage once
                continue
            seen_ids.add(mid)
            for k in usage:
                usage[k] += int((m.get("usage") or {}).get(k) or 0)
        res = evs[-1] if evs and evs[-1].get("type") == "result" else {}
        return {"calls": calls, "n_calls": sum(calls.values()), "usage_from_messages": usage,
                "usage_reported": res.get("usage"), "cost_usd_reported": res.get("total_cost_usd"),
                "duration_ms": res.get("duration_ms"), "num_turns": res.get("num_turns"),
                "is_error": res.get("is_error"), "subtype": res.get("subtype"),
                "result_text": str(res.get("result") or "")[:300], "end_turn_called": calls.get("end_turn", 0) > 0}

    def env_end_row(self) -> bool:
        """Whether the trajectory holds the env's end row (score.trajectory_end: the episode's outcome is fixed). Read
        again only when the file grew; never raises. Once it is there, both arms judge it by its own time
        (end_by_budget), whenever each reads it."""
        tp = self.out / "trajectory.jsonl"
        try:
            size = tp.stat().st_size
        except OSError:
            return False
        if size == getattr(self, "_end_row_size", -1):
            return getattr(self, "_end_row_seen", False)
        from ck3bench.harness.score import trajectory_end  # score imports the runner only inside a call
        self._end_row_size, self._end_row_seen = size, trajectory_end(self.out) is not None
        return self._end_row_seen

    def end_by_budget(self) -> str:
        """The end of an episode whose end row is read, decided by the row's time, the same in both arms: `wall_budget`
        when the env stamped it (to the second, its `wall_utc`) in the second of this runner's wall-budget deadline
        (its start + --wall-budget-s) or later, else `episode_over`. A row is read before the budget is checked, and
        the budget ends a run only when no end row is there, so a seat's turn begun before the deadline and a policy
        checked each second give the same end for the same row time. A row with no readable time is judged by the
        time it is read. The rule is score.budget_end, which the scorer applies to a stopped attempt's end row. Never
        raises."""
        import math
        from ck3bench.harness.score import _utc, budget_end, trajectory_end_row  # score imports the runner only inside a call
        budget = getattr(self.a, "wall_budget_s", None)
        if not isinstance(budget, (int, float)) or not math.isfinite(budget):
            return "episode_over"
        deadline = math.floor(self.t0_wall + budget)
        row = trajectory_end_row(self.out) or {}
        t = _utc(row.get("wall_utc"))
        at = time.time() if t is None else t
        self.budget_judged = {"end_row_wall_utc": row.get("wall_utc"),
                              "deadline_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(deadline)),
                              "by": "read_time" if t is None else "end_row"}
        return budget_end(at, self.t0_wall, budget)

    def episode_ended(self) -> str:
        """`episode_over` for an episode that ended on its own, judged by the end row's time when there is one."""
        return self.end_by_budget() if self.env_end_row() else "episode_over"

    def episode_over(self) -> bool:
        if self.env_end_row():
            return True
        try:
            st = self.client.state()
        except Exception:  # noqa: BLE001
            return self.play.poll() is not None
        return (not st.get("open")) or not st.get("agents")

    def load_prompt(self) -> None:
        """The seat prompt rendered from the task, before anything starts (a refusal costs no game)."""
        a = self.a
        from ck3bench.env.scenario import resolve
        a.prompt = str(TEMPLATE_PATH)
        sc = resolve(a.scenario)
        self.prompt = render_prompt(sc, a.n_steps if a.n_steps is not None else sc.n_steps, a.notes_pad_bytes)
        if sw(a, "quiet_prompt") == "on":
            self.prompt = self.prompt.rstrip("\n") + QUIET_LINES
        (self.out / "seat_prompt.rendered.md").write_text(self.prompt, encoding="utf-8")
        self.L(f"seat prompt rendered from {a.prompt} for {a.scenario} ({len(self.prompt)} chars)")

    def fill_census_id(self) -> None:
        """A census-seat task's prompt carries CENSUS_TOKEN until the env has read the save; the
        seat's id is the trajectory header's `seats` entry for this seat (written at open). Refused when absent."""
        if CENSUS_TOKEN not in self.prompt:
            return
        try:
            head = json.loads((self.out / "trajectory.jsonl").open(encoding="utf-8").readline())
            cid = (head.get("seats") or {}).get(self.a.seat)
        except (OSError, ValueError, AttributeError):
            cid = None
        self.prompt = fill_census(self.prompt, cid)
        (self.out / "seat_prompt.rendered.md").write_text(self.prompt, encoding="utf-8")
        self.L(f"seat prompt: the census seat {self.a.seat} is character {cid} (trajectory header)")

    def run(self) -> None:
        self.load_prompt()
        if getattr(self.a, "policy", None):
            return self.run_policy()
        url = self.start_play()
        self.fill_census_id()
        cfg = self.write_mcp(url)
        names = self.list_with_mcp_client(cfg)
        self.before_turns()
        self.hold_first_turn()
        return self.run_fresh(cfg, names)

    def run_policy(self) -> None:
        """--policy P: no agent CLI and no session; `env play` plays the seat with the env's scripted policy P (noop:
        the game's own AI alone, no model) and runs the episode to its end by itself. The seat prompt is
        still rendered (the same task identity as a seat played by a model), never sent."""
        a = self.a
        self.start_play()
        self.fill_census_id_when_open()
        while self.play.poll() is None:
            if self.env_end_row():
                # the episode ended (the env's end row): judged by the row's own time (end_by_budget), as the seated
                # arm judges it; `env play` holds and closes by itself in `finish` when it ended in time
                self.end_reason = self.end_by_budget()
                return
            if time.monotonic() - self.t0 > a.wall_budget_s:
                self.end_reason = "wall_budget"
                return
            time.sleep(1)
        self._drainer.join(30)
        try:
            last = json.loads((self.out / "play.out").read_text().strip().splitlines()[-1])
        except (OSError, ValueError, IndexError):
            last = None
        ok = isinstance(last, dict) and last.get("ok") and "end_reason" in last
        self.end_reason = self.episode_ended() if ok and self.play.returncode == 0 else \
            f"env_failed: rc {self.play.returncode}"

    def fill_census_id_when_open(self) -> None:
        """A census-seat task's prompt gets its id once the header is written (bounded by --open-wait-s)."""
        if CENSUS_TOKEN not in self.prompt:
            return
        t = time.monotonic()
        tj = self.out / "trajectory.jsonl"
        while time.monotonic() - t < self.a.open_wait_s and self.play.poll() is None:
            if tj.is_file() and tj.stat().st_size:
                break
            time.sleep(1)
        self.fill_census_id()

    # ── 4b. the turns: one fresh session each ─────────────────────────────
    def fresh_message(self, k: int, view: dict) -> str:
        """Turn k's one user message: the step, the seat's notes pad, its observation (last turn's calls one
        line each, the pad shown once above it). With --notes-edit on the pad's lines are numbered;
        with --obs-delta on the observation is what changed since the last turn's (obs_delta)."""
        from ck3bench.env.mcp_shim import compact_step
        obs = compact_step({"observation": dict(view.get("observation") or {})})["observation"]
        pad = obs.pop("notes_pad", None) or {}
        lines = turn_lines(obs, pad)            # the goal line, the orders standing, a pad left unwritten
        total = f" of {self.a.n_steps}" if obs.get("steps_total") else ""
        edit = sw(self.a, "notes_edit") == "on"
        text = numbered_pad(pad.get("text", "")) if edit else pad.get("text", "")
        head, sent, same = "YOUR OBSERVATION (JSON):", obs, []
        if sw(self.a, "obs_delta") == "on":
            sent, same = obs_delta(getattr(self, "_prev_obs", None), obs)
            if sent is not obs:
                head = delta_head(same)
            self._prev_obs = obs
        self.obs_sent = {"obs": "delta" if sent is not obs else "full", "unchanged_sections": len(same),
                         "pad_numbered": edit}
        tail = (f"call {TOOL_PREFIX}end_turn with a one-line summary and your notes: notes_edit for the lines that "
                f"change, or the whole pad rewritten." if edit else
                f"call {TOOL_PREFIX}end_turn with a one-line summary and your rewritten notes.")
        return (f"Turn {k}: step {view.get('step')}{total} is open (game date {obs.get('game_date')}). The game is "
                f"paused.\n\n" + lines
                + f"YOUR NOTES (the pad you wrote at the end of your last turn; version "
                f"{pad.get('version')}, {pad_size(pad, edit)}"
                + ("; n| numbers each line and is not part of it" if edit else "") + "):\n<<<\n"
                f"{text}\n>>>\n\n{head}\n{json.dumps(sent, separators=(',', ':'))}"
                f"\n\nPlay this turn with your tools, then {tail}")

    def frame_gate(self) -> None:
        """--frame-gate-dir: hold the turn (the game is paused between turns) while an operator's hook works."""
        a = self.a
        if not a.frame_gate_dir:
            return
        d = Path(a.frame_gate_dir)
        if not (d / "frame_due").exists():
            return
        (d / "frame_now").write_text(now_ms() + "\n")
        t = time.monotonic()
        while not (d / "frame_done").exists() and time.monotonic() - t < a.frame_gate_s:
            if self.play is not None and self.play.poll() is not None:
                break
            time.sleep(0.5)
        got = (d / "frame_done").exists()
        for f in ("frame_now", "frame_done"):
            (d / f).unlink(missing_ok=True)
        self.L(f"frame gate: turn held {time.monotonic() - t:.1f} s for the gate's hook "
               f"({'framed' if got else 'NOT framed in time; the turn goes on'})")

    def close_session(self) -> None:
        if self.sess is None or self.sess.poll() is not None:
            return
        try:
            self.sess.stdin.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            self.sess.wait(20)
        except subprocess.TimeoutExpired:
            os.killpg(self.sess.pid, signal.SIGKILL); self.sess.wait(30)
            self.L(f"session pid {self.sess.pid} killed by its process group after 20 s")

    def run_fresh(self, cfg: Path, names: list) -> None:
        a = self.a
        k = 0
        while self.end_reason is None:
            # the episode's own end first: an end row is judged by its own time (end_by_budget), as the policy
            # arm judges it (run_policy); the budget ends the run only when no end row is there
            if self.episode_over():
                self.end_reason = self.episode_ended(); break
            if time.monotonic() - self.t0 > a.wall_budget_s:
                self.end_reason = "wall_budget"; break
            try:
                view = self.client.observation(a.seat)
            except Exception as e:  # noqa: BLE001
                try:                      # the server goes with `env play` when the episode ends
                    self.play.wait(15)
                except subprocess.TimeoutExpired:
                    pass
                self.end_reason = self.episode_ended() if self.play.poll() is not None else \
                    f"env_unreachable: {type(e).__name__}"
                break
            if view.get("submitted"):          # another seat is still playing the step: wait for it
                try:
                    self.client.await_step(view["step"], seat=a.seat, timeout_s=min(a.step_wait_s, 3600))
                except Exception:  # noqa: BLE001
                    pass
                continue
            self.frame_gate()
            k += 1
            text = self.fresh_message(k, view)
            self.start_session(cfg)
            t, sent_utc = time.monotonic(), now_ms()
            self.send(text)
            with self.cv:
                self.cv.wait_for(lambda: self.init is not None or self.exited, 60)
            if self.init is None:
                self.end_reason = "seat_exited" if self.exited else "no_init_event"; break
            ok, why = self.check_init(names)
            if not ok:
                self.end_reason = "surface_mismatch"; self.L(why); break
            evs = self.wait_reply(2 * a.turn_timeout_s)
            reminded = False
            if isinstance(evs, list) and not self.account(evs)["end_turn_called"]:
                reminded = True
                self.send(f"Your turn is still open: the step runs only when you call {TOOL_PREFIX}end_turn (with your notes). "
                          "Call it when you are done.")
                more = self.wait_reply(2 * a.turn_timeout_s)
                evs = evs + more if isinstance(more, list) else more
            if evs is None:
                self.end_reason = "seat_exited"; break
            if evs == "timeout":
                self.end_reason = "seat_hung"; break
            row = {"turn": k, "session": self.sessions, "session_pid": self.sess.pid, "mode": "fresh",
                   "step": view.get("step"), "prompt_bytes": len(text.encode()),
                   "prompt_sent": dict(getattr(self, "obs_sent", {})), "sent_utc": sent_utc,
                   "ended_utc": now(), "wall_s": round(time.monotonic() - t, 2), "reminded": reminded,
                   **self.account(evs)}
            self.close_session()
            try:
                st2 = self.client.state()
                row["env_after"] = {"step": st2.get("step"), "game_date": st2.get("game_date"),
                                    "open": st2.get("open"), "agents": st2.get("agents")}
            except Exception as e:  # noqa: BLE001
                row["env_after"] = {"error": f"{type(e).__name__}: {e}"}
            row["rate_limited"] = bool(row["is_error"]) and bool(RATE_RE.search(row["result_text"]))
            self.turns.write(json.dumps(row) + "\n"); self.turns.flush()
            if row["rate_limited"]:
                self.end_reason = "rate_limited"; break
            if not row["end_turn_called"]:
                self.end_reason = "seat_stopped"; break

    def notes_witness(self) -> dict:
        """The declared --notes beside what each seat's server reported at open (the header's provenance)."""
        try:
            head = json.loads((self.out / "trajectory.jsonl").open().readline())
        except (OSError, ValueError) as e:
            return {"declared": self.a.notes, "observed": None, "verdict": f"unwitnessed: {type(e).__name__}"}
        obs = (head.get("provenance") or {}).get("notes")
        if not isinstance(obs, dict):
            return {"declared": self.a.notes, "observed": None, "verdict": "unwitnessed: the header has no notes"}
        states = {s: (n or {}).get("state") if isinstance(n, dict) else None for s, n in obs.items()}
        if any(st is None for st in states.values()):
            verdict = "unwitnessed: a seat's notes were unread"
        else:
            verdict = "agreed" if all(st == self.a.notes for st in states.values()) else "DISAGREES"
        return {"declared": self.a.notes, "observed": obs, "verdict": verdict}

    # ── 5. the clean end ──────────────────────────────────────────────────
    def finish(self) -> None:
        """The clean end. The end reason is recorded first, to the disk (end.json with `final: false`), before the
        session is waited for and the game is closed: an end the runner decided (the seat exited, stopped or hung,
        the wall budget) is then on record whatever happens to the host while it closes (score.played_out reads it).
        end.json is written again, whole, with `final: true` once everything is closed."""
        rec = {"end_reason": self.end_reason, "at": now(), "wall_s": round(time.monotonic() - self.t0, 1),
               "turn_mode": "policy" if getattr(self.a, "policy", None) else self.a.turn_mode}
        if getattr(self, "budget_judged", None):
            rec["budget_judged"] = self.budget_judged     # the end row's time and the deadline it was judged against
        try:
            rec["notes_witness"] = self.notes_witness()
        except Exception as e:  # noqa: BLE001
            rec["notes_witness"] = {"verdict": f"unwitnessed: {type(e).__name__}"}
        try:
            write_whole(self.out / "end.json", json.dumps({**rec, "final": False}, indent=1))
        except OSError as e:
            self.L(f"end reason NOT recorded before the close: {type(e).__name__}: {e}")
        if self.sess is not None and self.sess.poll() is None:
            try:
                self.sess.stdin.close()
            except Exception:  # noqa: BLE001
                pass
            try:
                self.sess.wait(20)
            except subprocess.TimeoutExpired:
                os.killpg(self.sess.pid, signal.SIGKILL); self.sess.wait(30)
                rec["session_killed"] = self.sess.pid
        if self.play is not None and self.play.poll() is None and self.end_reason == "episode_over":
            # the episode ended on its own: let `env play` hold and close by itself, within what is left of the wall
            # budget (the same in both arms; the end stays `episode_over`, and a hold the budget cuts is recorded)
            hold = self.a.hold_before_close_s + STOP_WAIT_S
            left = getattr(self.a, "wall_budget_s", float("inf")) - (time.monotonic() - self.t0)
            try:
                self.play.wait(max(0.0, min(hold, left)))
            except subprocess.TimeoutExpired:
                if left < hold:
                    rec["hold_cut_by_wall_budget"] = True
        if self.play is not None and self.play.poll() is None:
            os.kill(self.play.pid, signal.SIGTERM)
            try:
                self.play.wait(STOP_WAIT_S)
            except subprocess.TimeoutExpired:
                os.killpg(self.play.pid, signal.SIGKILL); self.play.wait(30)
                rec["play_killed"] = self.play.pid
        rec["play_rc"] = self.play.returncode if self.play is not None else None
        rec["sessions"] = self.sessions
        if getattr(self.a, "policy", None):
            rec["policy"] = self.a.policy
        time.sleep(0.5)
        try:
            rec["play_last_line"] = json.loads((self.out / "play.out").read_text().strip().splitlines()[-1])
        except Exception:  # noqa: BLE001
            rec["play_last_line"] = None
        rec["final"] = True
        write_whole(self.out / "end.json", json.dumps(rec, indent=1))
        self.L(f"end {json.dumps(rec)[:300]}")


def seat_surface(a) -> dict:
    """The seat surface this run declares: every switch's value, as the trajectory header records it.
    notes_edit and obs_delta act on fresh turns only; their declared value is what this run had in effect."""
    fresh = a.turn_mode == "fresh"
    return {"reply_envelope": sw(a, "reply_envelope"), "reply_pages": sw(a, "reply_pages"),
            "notes_edit": sw(a, "notes_edit") if fresh else "off", "obs_delta": sw(a, "obs_delta") if fresh else "off",
            "quiet_prompt": sw(a, "quiet_prompt"), "turn_mode": a.turn_mode}


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m ck3bench.harness run-seat",
                                description="One model seat, one fresh agent CLI session per turn, one episode.")
    p.add_argument("--out", required=True, help="the run directory (created; everything the run writes goes here)")
    p.add_argument("--scenario", required=True, help="a built-in task id or a task file (ck3bench env scenarios)")
    p.add_argument("--seat", default=None, help="the seat the model plays (default: the task's one seat)")
    p.add_argument("--other", action="append", help="SEAT=POLICY for any other seat (env play --seat)")
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--n-steps", type=int, default=None, help="the task's steps (default: the scenario's own; a "
                                                              "different number is refused)")
    p.add_argument("--python", default=sys.executable, help="the python that runs env play and the seat's shim")
    p.add_argument("--repo", default=None, help="the directory holding the ck3bench package the seat's shim imports "
                                                "(default: this runner's own)")
    p.add_argument("--claude", default="claude", help="the agent CLI (Claude Code's claude, or a stand-in)")
    p.add_argument("--policy", default=None, choices=("noop", "random", "observe-then-gift"),
                   help="no agent CLI: the seat is this scripted env policy (noop: the game's own AI alone, "
                        "no model); everything else of the run (its files, validity, scoring) is the same")
    p.add_argument("--model", default="opus", help="the model id the session is started with (default: the alias opus, which the agent CLI resolves; a series pins a full id from its expectation)")
    p.add_argument("--turn-mode", choices=("fresh",), default="fresh",
                   help="fresh: a new session every turn, with the notes pad (the one mode)")
    p.add_argument("--notes-pad-bytes", type=int, default=NOTES_PAD_BYTES, help="the carried notes pad's bytes")
    p.add_argument("--notes", choices=("on", "off"), default="on",
                   help="the toolkit's shared notes for the seats' servers (off: CK3_NOTES=off, no notes served)")
    p.add_argument("--frame-gate-dir", default=None, help="a directory an operator's hook gates each turn through")
    p.add_argument("--frame-gate-s", type=float, default=150.0, help="the longest a turn is held by the gate")
    p.add_argument("--saves-dir", default=None)
    p.add_argument("--instance-id", default=None)
    p.add_argument("--game-speed", type=int, default=None,
                   help="env play --game-speed: declare this game speed (1..5) in place of the scenario's own")
    p.add_argument("--turn-timeout-s", type=float, default=1200.0)
    p.add_argument("--step-wait-s", type=float, default=600.0)
    p.add_argument("--open-wait-s", type=float, default=900.0)
    p.add_argument("--wall-budget-s", type=float, default=8 * 3600.0)
    p.add_argument("--hold-before-close-s", type=float, default=0.0,
                   help="env play keeps the finished game up this long before closing it (a recording's end)")
    p.add_argument("--before-turns", default=None,
                   help="a command run once (RUN_DIR appended) after the episode opens, before the session starts")
    p.add_argument("--before-turns-timeout-s", type=float, default=240.0)
    p.add_argument("--hold-first-turn-file", default=None,
                   help="send turn 1 only once this file exists (an operator's recorder writes it), bounded")
    p.add_argument("--hold-first-turn-s", type=float, default=600.0)
    p.add_argument("--session-env", action="append", help="a variable of ours the session also gets (a stand-in's)")
    # the seat surface (module docstring, SEAT SURFACE)
    p.add_argument("--reply-envelope", choices=("lean", "full"), default="lean")
    p.add_argument("--reply-pages", choices=("on", "off"), default="on")
    p.add_argument("--notes-edit", choices=("on", "off"), default="on")
    p.add_argument("--obs-delta", choices=("on", "off"), default="off")
    p.add_argument("--quiet-prompt", choices=("on", "off"), default="off")
    return p


def default_seat(scenario: str) -> str:
    """The task's one seat; a task with several seats needs --seat."""
    from ck3bench.env.scenario import resolve
    seats = resolve(scenario).seats
    if len(seats) != 1:
        raise SystemExit(f"run-seat: {scenario} has {len(seats)} seats; name the model's with --seat")
    return seats[0].name


def stop(signum, _frame):
    """The runner's TERM and INT handler: the first signal stops the run (its clean end follows); every further one
    is ignored from here on, so a second TERM (a driver's own, right after its process group's) cannot interrupt the
    clean end that writes end.json and closes `env play` and its game."""
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    raise KeyboardInterrupt(signal.Signals(signum).name)


def main(argv: list[str] | None = None) -> int:
    a = parser().parse_args(argv)
    if a.repo:
        sys.path.insert(0, str(Path(a.repo)))
    else:                       # the shim imports the same ck3bench this runner runs (an installed one or a checkout)
        a.repo = str(Path(__file__).resolve().parents[2])
    if a.seat is None:
        a.seat = default_seat(a.scenario)
    old_umask = os.umask(0o077)
    run = Run(a)
    # restored after the run: a caller in the same process (a test, a driver) keeps its own umask and handlers, and
    # the children it starts later do not inherit ignored signals
    old_handlers = {s: signal.getsignal(s) for s in (signal.SIGTERM, signal.SIGINT)}

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        return _run(run)
    finally:
        for s, h in old_handlers.items():
            signal.signal(s, h)
        os.umask(old_umask)


def _run(run: "Run") -> int:
    try:
        run.run()
    except KeyboardInterrupt:
        run.end_reason = "stopped_by_signal"
    except SystemExit as e:
        run.end_reason = f"setup_failed: {e}"
    except Exception as e:  # noqa: BLE001
        run.end_reason = f"runner_error: {type(e).__name__}: {e}"
    finally:
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        run.finish()
    return 0 if run.end_reason == "episode_over" else 1


if __name__ == "__main__":
    sys.exit(main())
