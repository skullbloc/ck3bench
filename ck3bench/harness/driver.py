"""python -m ck3bench.harness series run [options]: the reference series driver, one attempt of a series.

A minimal driver for one seated run, built on the harness's own steps (ck3bench.harness.series, the runner and the
readers): it calls each of them as a child process, in this order, and writes the files the scorer needs. The
harness's tests run it on the fake backend with the stand-in agent CLI. docs/harness.md, "The reference driver",
lists every file it writes.

     0. game env   for a task on the game: the display the game inherits from this driver's environment (DISPLAY,
                   or WAYLAND_DISPLAY with SDL_VIDEODRIVER=wayland; XAUTHORITY a readable file when set); refused
                   (rc 2) before any other step when it is missing (GAME_ENV_KEYS, docs/harness.md "The game's
                   environment")

     1. switches   `series switches`: refused (rc 5) on any switch that differs from the expectation; its export and
                   unset lines become the children's environment. A kit switch this driver cannot honour
                   (RECORD, VIEW or CLEANMAP other than off) is refused here too
     2. plan       `series plan`: the task's seat and steps
     3. slot       `series slot HOST RUN_ID` (a series): refused (rc 7) unless RUN_ID is this host's next attempt
     4. lock       a directory (RATE_LOCK_DIR, default one per user on this host: default_lock_dir) made with mkdir
                   and a holder line; refused (rc 3) when it exists, never taken over; removed at the end only when
                   its holder line is this run's
     5. run dir    RATE_RUNS/RUN_ID, mode 700, refused (rc 3) when it exists
     6. start      `series start OUT`: refused (rc 6) when the launch identity differs from the expectation. A start
                   that is refused or fails starts no game and writes no ledger entry: the run dir is moved to
                   RATE_RUNS/refused-starts/RUN_ID-UTC with start_refused.txt (the refusal), so the slot is free
     7. sampler    a thread appending one row to OUT/pressure.tsv every RATE_SAMPLE_S (30) seconds until the end
     8. protected  OUT/own.before.{list,sha,txt}: a digest of RATE_PROTECTED_DIR, or "not configured"
     9. watch      `series watch OUT` in the background (OUT/ck3.before is written first: the game processes alive
                   now, by /proc/<pid>/exe: the Linux game's or the configured RATE_GAME_BIN)
    10. episode    `run-seat` with the plan, the declared model and speed, FULL_HOLD, under --wall-s; then OUT/done,
                   and `accounting OUT`
    11. post       the watcher waited for (600 s, then stopped), `analyze OUT`
    12. protected  OUT/own.after.{list,sha,txt}
    13. end        `series end OUT`: run.json `end` and `validity` (a protected directory that differs is invalid
                   there, `owner_tree_differs`, and so in the ledger entry this step writes)
    14. result     `series result OUT`: result.json (sealed in a series)
    15. marker     the sampler stopped; the lock released, unless a game this run started is still alive (the
                   runner was stopped and the game outlived it): a game a witness recorded, or one no witness
                   recorded (a game binary's process not in ck3.before that carries this run's RATE_RUN_ID, or the
                   Linux game with this run's instance); then the driver waits for it (GAME_EXIT_WAIT_S)
                   and, when it is still alive, keeps the lock, names the game in it and says so; OUT/rate_run.done
                   written, only when `series end` and `series result` both exited 0: the end marker the scorer
                   needs before it counts the attempt as ended (`series end` on a run.json that no longer reads
                   records the attempt in its ledger entry and writes the marker; `series result` is then not run).
                   Otherwise no marker, exit 8 (END_INCOMPLETE), and the command that completes the attempt is
                   named (`series close OUT`, `series end OUT` by hand then `series close OUT`, or `series withdraw
                   OUT` for an attempt not played out). A TERM or an interrupt (Ctrl-C) to the driver runs this
                   step too, then exits 143 or 130
Every step is a row of OUT/post.tsv (step, rc, UTC, note). The driver signals only its own children (the watcher,
the runner on its wall limit); it never looks for a process by name.
"""
from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import hashlib
import json
import os
import re
import shlex
import signal
import stat
import subprocess
import sys
import threading
import time
import tomllib
from pathlib import Path
from typing import Optional

#: what the scorer reads to count a run as ended (score.validity_only; its one definition); docs/harness.md
#: "Recorded identifiers"
from ck3bench.harness.score import END_MARKER  # noqa: E402
#: the driver's own records that are read (`done`, the end marker) go to the disk whole: the series steps' writer
from ck3bench.env.durable import write_whole  # noqa: E402
#: the timings' defaults (docs/harness.md, "The reference driver"); each a driver option
DEFAULTS = {"turn_timeout_s": 1200.0, "step_wait_s": 900.0, "wall_budget_s": 27900.0, "wall_s": 28800.0}
#: kit switches this driver honours only when off: it records no video, sets no camera, installs no map mod
UNHONOURED = {"RECORD": "0", "VIEW": "", "CLEANMAP": "0"}
RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
PRESSURE_COLUMNS = ("utc", "cpu_some10", "cpu_full10", "mem_some10", "mem_full10", "io_some10", "io_full10",
                    "memavail_kb", "load1", "load5", "game_pid", "game_state", "game_rss_kb")
WATCHER_WAIT_S = 600
#: the exit code of an attempt whose `series end` or `series result` did not complete (no end marker written)
END_INCOMPLETE = 8
#: how long the runner gets after TERM at --wall-s before it is killed
TERM_WAIT_S = 300.0
#: how long the end waits, the lock held, for a game this run started that is still alive after the runner (the
#: runner was killed, say, and the game outlived it); a game alive after this keeps the lock until it exits
GAME_EXIT_WAIT_S = 900.0
#: the file in the lock directory naming a game that outlived its run: `pid PID exe EXE run RUN_ID`, one per line
LOCK_GAME = "game"
#: the lock directory's name in the per-user location (default_lock_dir)
LOCK_NAME = "ck3bench-game.lock"
#: the variables of this driver's environment the game inherits (through the runner, `env play` and `ck3agent
#: launch`) that decide whether it can open a window and where it keeps its files; recorded in driver.log at the
#: start, validated by game_env_refusals (docs/harness.md, "The game's environment")
GAME_ENV_KEYS = ("DISPLAY", "XAUTHORITY", "SDL_VIDEODRIVER", "WAYLAND_DISPLAY", "DBUS_SESSION_BUS_ADDRESS",
                 "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_RUNTIME_DIR", "CK3_BINARY")


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Refused(Exception):
    def __init__(self, rc: int, msg: str):
        super().__init__(msg)
        self.rc = rc


def _expectation(path: str) -> dict:
    """The expectation file as the driver reads it before any step: a JSON object, else `expect_schema` (the code
    `series start` gives it), never a traceback."""
    from ck3bench.harness import score
    try:
        d = score.read_bounded_json(path)
    except (OSError, ValueError) as x:
        raise Refused(2, f"REFUSE expect_schema: {path} does not read ({type(x).__name__}) (no lock taken, "
                         "no game)") from None
    if not isinstance(d, dict):
        raise Refused(2, f"REFUSE expect_schema: {path} is a JSON {type(d).__name__}, not an object (no lock "
                         "taken, no game)")
    return d


# ── host pressure ───────────────────────────────────────────────────────────
def _psi(kind: str, line: str) -> str:
    try:
        for ln in Path(f"/proc/pressure/{kind}").read_text().splitlines():
            if ln.startswith(line + " "):
                return ln.split("avg10=")[1].split()[0]
    except (OSError, IndexError):
        pass
    return "NA"


def _status(pid: str, key: str) -> str:
    try:
        for ln in Path(f"/proc/{pid}/status").read_text().splitlines():
            if ln.startswith(key + ":"):
                return ln.split()[1]
    except (OSError, IndexError):
        pass
    return "NA"


def pressure_row(out: Path) -> list[str]:
    """One sample: PSI avg10 (cpu, memory, io; some and full), MemAvailable kB, load 1/5, and the run's game (its
    pid from witness_start.json, its /proc state letter and VmRSS kB). A value that cannot be read is `NA`, never 0."""
    ma = "NA"
    try:
        for ln in Path("/proc/meminfo").read_text().splitlines():
            if ln.startswith("MemAvailable:"):
                ma = ln.split()[1]
    except (OSError, IndexError):
        pass
    try:
        l1, l5 = Path("/proc/loadavg").read_text().split()[:2]
    except (OSError, ValueError):
        l1 = l5 = "NA"
    gp, gs, rss = "NA", "none", "NA"
    try:
        pid = json.loads((out / "witness_start.json").read_text()).get("pid")
    except (OSError, ValueError, AttributeError):
        pid = None
    if isinstance(pid, int):
        gp = str(pid)
        try:
            exe = os.readlink(f"/proc/{pid}/exe")
        except OSError:
            exe = ""
        if exe.endswith("/binaries/ck3"):
            gs, rss = _status(gp, "State"), _status(gp, "VmRSS")
        else:
            gs = "gone"
    return [now(), _psi("cpu", "some"), _psi("cpu", "full"), _psi("memory", "some"), _psi("memory", "full"),
            _psi("io", "some"), _psi("io", "full"), ma, l1, l5, gp, gs, rss]


class Sampler(threading.Thread):
    """OUT/pressure.tsv, one row every `every` seconds from the start identity to the end (the scorer's resource rule
    reads it over a stopped run's window)."""

    def __init__(self, out: Path, every: float):
        super().__init__(daemon=True)
        self.out, self.every, self.stop = out, every, threading.Event()
        self.path = out / "pressure.tsv"

    def run(self) -> None:
        if not self.path.is_file() or not self.path.stat().st_size:
            self.path.write_text("\t".join(PRESSURE_COLUMNS) + "\n")
        while not self.stop.is_set():
            with open(self.path, "a", encoding="utf-8") as f:
                f.write("\t".join(pressure_row(self.out)) + "\n")
            self.stop.wait(self.every)


# ── the protected directory ────────────────────────────────────────────────
def _find_mtime(ns: int) -> str:
    """An mtime as GNU find's `%T@` prints it: seconds, a point, ten fractional digits."""
    return f"{ns // 1_000_000_000}.{ns % 1_000_000_000:09d}0"


def _sha_name(rel: str) -> tuple[str, str]:
    """(prefix, name) as sha256sum prints a file name: a name holding a backslash, newline or carriage return is
    escaped and its line starts with a backslash."""
    if any(c in rel for c in "\\\n\r"):
        return "\\", rel.replace("\\", "\\\\").replace("\n", "\\n").replace("\r", "\\r")
    return "", rel


def protected_digest(root: Optional[str], prefix: Path) -> tuple[int, str]:
    """PREFIX.list and PREFIX.sha of the directory RATE_PROTECTED_DIR names, read only, and PREFIX.txt a one-line
    summary. The two files are byte for byte what this shell pipeline writes in that directory, so a digest taken
    another way (a baseline taken before the harness existed, say) can be compared with this one:

        find . -printf '%y %p %s %T@\\n' | LC_ALL=C sort > PREFIX.list
        find . -type f -print0 | LC_ALL=C sort -z | xargs -0 sha256sum > PREFIX.sha

    and the summary is `root=R files=N list_sha=<16 hex> content_sha=<16 hex>` (the first 16 hex digits of each
    file's sha256). A file that cannot be read is a `unread:<error>  ./path` line in PREFIX.sha (the pipeline would
    stop). Unset: PREFIX.txt says so and no list is written, which `series result` reads as `unwitnessed`, never
    `IDENTICAL`."""
    txt = prefix.with_name(prefix.name + ".txt")
    if not root:
        txt.write_text("not configured (RATE_PROTECTED_DIR unset)\n")
        return 0, "not configured"
    r = Path(root).resolve()
    if not r.is_dir():
        txt.write_text(f"unread: {r} is not a directory\n")
        return 2, "unread"
    if prefix.resolve().parent == r or r in prefix.resolve().parents:
        txt.write_text(f"refused: the run directory is inside {r}\n")
        return 2, "refused"
    entries = [(".", r)]
    for d, dirs, files in os.walk(r, followlinks=False):
        for n in dirs + files:
            p = Path(d) / n
            entries.append(("./" + p.relative_to(r).as_posix(), p))
    lst, sha = [], []
    for rel, p in entries:
        try:
            st = p.lstat()
        except OSError:
            continue
        kind = _FIND_TYPE.get(stat.S_IFMT(st.st_mode), "U")
        lst.append(os.fsencode(f"{kind} {rel} {st.st_size} {_find_mtime(st.st_mtime_ns)}"))
        if kind == "f":
            h = hashlib.sha256()
            pre, name = _sha_name(rel)
            try:
                with open(p, "rb") as f:
                    for b in iter(lambda: f.read(1 << 20), b""):
                        h.update(b)
                sha.append((os.fsencode(rel), os.fsencode(f"{pre}{h.hexdigest()}  {name}")))
            except OSError as e:
                sha.append((os.fsencode(rel), os.fsencode(f"unread:{type(e).__name__}  {name}")))
    lst.sort()
    sha.sort()
    lb = b"".join(x + b"\n" for x in lst)
    sb = b"".join(x + b"\n" for _, x in sha)
    prefix.with_name(prefix.name + ".list").write_bytes(lb)
    prefix.with_name(prefix.name + ".sha").write_bytes(sb)
    line = (f"root={r} files={len(sha)} list_sha={hashlib.sha256(lb).hexdigest()[:16]} "
            f"content_sha={hashlib.sha256(sb).hexdigest()[:16]}")
    txt.write_text(line + "\n")
    return 0, line


#: GNU find's %y letters
_FIND_TYPE = {stat.S_IFREG: "f", stat.S_IFDIR: "d", stat.S_IFLNK: "l", stat.S_IFBLK: "b", stat.S_IFCHR: "c",
              stat.S_IFIFO: "p", stat.S_IFSOCK: "s"}


# ── the lock and the game's environment ────────────────────────────────────
#: where the default lock's per-user parent lives: a host-local directory that outlives the user's sessions (a
#: logout removes the user's runtime directory, never this) and is emptied at boot, when no game survives either
LOCK_ROOT = Path("/tmp")


def default_lock_dir(uid: Optional[int] = None) -> Path:
    """The default lock: ONE fixed path per user on this host, from the uid alone (never from the environment, which
    two drivers may not share, and never from whether a session directory exists, which changes at logout):
    /tmp/ck3bench-UID/ck3bench-game.lock. Every reference driver this user starts on this host without RATE_LOCK_DIR
    takes the same lock, whatever its RATE_RUNS and whether or not the user is logged in."""
    uid = os.getuid() if uid is None else uid
    return LOCK_ROOT / f"ck3bench-{uid}" / LOCK_NAME


def _private_parent(d: Path) -> None:
    """The default lock's parent: made 0700 when absent (one mkdir); refused when it is a link, not a directory, or
    not this user's (another user could otherwise hold, move or remove our lock), and made 0700 when it is ours with
    wider bits. Never removed: the lock inside it is the one thing that comes and goes."""
    try:
        d.mkdir(mode=0o700)
    except FileExistsError:
        pass
    st = d.lstat()
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid():
        raise Refused(3, f"lock parent {d} is not a directory of this user (uid {os.getuid()}): set RATE_LOCK_DIR")
    if stat.S_IMODE(st.st_mode) != 0o700:
        os.chmod(d, 0o700)              # ours and a directory (checked above, not a link)


def game_env_refusals(env: dict) -> list[str]:
    """What the game cannot start without, read from the environment it will inherit: a display (DISPLAY, or
    WAYLAND_DISPLAY when SDL_VIDEODRIVER=wayland), an XAUTHORITY that is a readable file when set, an SDL video
    driver the game can use, absolute XDG base directories. One REFUSE line per problem, naming the variable."""
    r = []
    drv = env.get("SDL_VIDEODRIVER", "")
    if drv not in ("", "x11", "wayland"):
        r.append(f"REFUSE game_env:SDL_VIDEODRIVER={drv!r}: the game opens its window with x11 or wayland")
    if drv == "wayland":
        if not env.get("WAYLAND_DISPLAY"):
            r.append("REFUSE game_env:WAYLAND_DISPLAY is not set and SDL_VIDEODRIVER=wayland: the game has no display")
    elif not env.get("DISPLAY"):
        r.append("REFUSE game_env:DISPLAY is not set: the game, started by `ck3agent launch` with this driver's "
                 "environment, has no display to open (set DISPLAY, and XAUTHORITY when the X server needs it)")
    xa = env.get("XAUTHORITY")
    if xa and not (os.path.isfile(xa) and os.access(xa, os.R_OK)):
        r.append(f"REFUSE game_env:XAUTHORITY={xa} is not a readable file")
    for k in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME"):
        if env.get(k) and not os.path.isabs(env[k]):
            r.append(f"REFUSE game_env:{k}={env[k]!r} is not an absolute path")
    return r


def _uses_game(task: str) -> bool:
    """Whether the task starts the game (its backend is ck3); a task that does not resolve is the plan step's to
    refuse."""
    try:
        from ck3bench.env.scenario import resolve
        return resolve(task).backend == "ck3"
    except Exception:  # noqa: BLE001
        return False


# ── the run ────────────────────────────────────────────────────────────────
def is_game_exe(exe: str, configured: str = "") -> bool:
    """`exe` (a /proc/<pid>/exe) is a game binary: the Linux game's (`.../binaries/ck3`, game_identity's rule) or the
    configured one (`configured`: RATE_GAME_BIN, resolved)."""
    return bool(exe) and (exe.endswith("/binaries/ck3") or (bool(configured) and exe == configured))


def ck3_pids(configured: str = "") -> list[str]:
    """Every game process alive now, by /proc/<pid>/exe (is_game_exe), never by a name."""
    from ck3bench.harness.game_identity import _exe
    return sorted(p.name for p in Path("/proc").iterdir() if p.name.isdigit() and is_game_exe(_exe(int(p.name)),
                                                                                                 configured))


def unwitnessed_games(out: Path, configured: str, run_id: str, instance: str = "") -> list[tuple[int, str]]:
    """The game processes of this run that no witness recorded, alive now: (pid, exe) of every process whose
    /proc/<pid>/exe is a game binary (is_game_exe) and that was not alive when the run began (OUT/ck3.before), and
    that is this run's by its environment: it carries this run's RATE_RUN_ID (every process the runner starts
    inherits it, in whatever session), or it is the Linux game carrying this run's CK3_INSTANCE_ID (game_identity's
    rule). A Linux game whose environment cannot be read counts (a lock kept too long is safe; a second game is
    not); any other process of an unreadable environment does not. Never a process name or a command line."""
    from ck3bench.harness.game_identity import _exe, environ
    try:
        before = set((out / "ck3.before").read_text().split())
    except OSError:
        return []                                         # the run never reached the point where a game starts
    games = []
    for pid in ck3_pids(configured):
        if pid in before or int(pid) == os.getpid():
            continue
        exe = _exe(int(pid))
        linux_game = exe.endswith("/binaries/ck3")
        try:
            env = environ(int(pid))
        except OSError:
            if linux_game:
                games.append((int(pid), exe))
            continue
        if (run_id and env.get("RATE_RUN_ID") == run_id) or \
                (linux_game and (env.get("CK3_INSTANCE_ID") or "default") == (instance or "default")):
            games.append((int(pid), exe))
    return games


def recorded_games(out: Path) -> list[tuple[int, str]]:
    """The game processes the run recorded as its own: (pid, exe) from `series watch`'s witnesses (start, end, after
    the final save). Never a process name: the pid and the exe the witness read at /proc/<pid>/exe."""
    games = []
    for name in ("witness_start.json", "witness_end.json", "witness_end2.json"):
        try:
            w = json.loads((out / name).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        pid, exe = w.get("pid") if isinstance(w, dict) else None, w.get("exe") if isinstance(w, dict) else None
        if isinstance(pid, int) and isinstance(exe, str) and exe and (pid, exe) not in games:
            games.append((pid, exe))
    return games


def alive_as(pid: int, exe: str) -> bool:
    """pid is alive and is still the process the witness read: its /proc/<pid>/exe is the recorded exe (a pid
    reused by another program is not the game)."""
    from ck3bench.harness.game_identity import _exe
    return _exe(pid) == exe


def lock_games_text(d: Path) -> str:
    """What a game left in the lock says: each `pid N exe E run R` line, and whether that process is running."""
    try:
        lines = [ln.split() for ln in (d / LOCK_GAME).read_text(encoding="utf-8", errors="replace").splitlines() if ln.strip()]
    except OSError:
        return ""
    out = []
    for f in lines:
        kv = dict(zip(f[0::2], f[1::2]))
        pid, exe = kv.get("pid", ""), kv.get("exe", "")
        state = ("still running" if pid.isdigit() and alive_as(int(pid), exe) else "no longer running")
        out.append(f"the game of run {kv.get('run', '?')}, pid {pid} ({exe}), {state}")
    return "; kept for " + "; ".join(out) if out else ""


def _tsv(v: str) -> str:
    return v.replace("\\", "\\\\").replace("\t", "\\t").replace("\n", "\\n").replace("\r", "\\r")


def load_config(path: Optional[str]) -> dict[str, str]:
    """--config FILE: a TOML file whose [env] table gives variables (strings) the environment does not already set."""
    if not path:
        return {}
    d = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    env = d.get("env") or {}
    bad = [k for k, v in env.items() if not isinstance(v, str)]
    if bad or set(d) - {"env"}:
        raise Refused(2, f"--config {path}: only an [env] table of strings is read ({bad or sorted(set(d) - {'env'})})")
    return env


class Driver:
    def __init__(self, a: argparse.Namespace):
        self.a = a
        self.env = dict(os.environ)
        for k, v in load_config(a.config).items():
            self.env.setdefault(k, v)
        self.py = sys.executable
        self.out: Optional[Path] = None
        self.lock: Optional[Path] = None
        self.holder = ""
        self.sampler: Optional[Sampler] = None
        self.watcher: Optional[subprocess.Popen] = None
        #: whether `series start` accepted this run (an attempt, or a check, began); until then a run dir is moved
        #: aside at the end (start_aside)
        self.started = False
        #: whether `series end` and `series result` both completed: only then is the end marker written
        self.ended = False
        self.refusal = ""

    # the harness's own steps, each a child process with this driver's environment
    def step(self, *args: str, timeout: float = 600, **kw) -> subprocess.CompletedProcess:
        return subprocess.run([self.py, "-m", "ck3bench.harness", *args], env=self.env, capture_output=True,
                              text=True, timeout=timeout, **kw)

    def post(self, step: str, rc, note: str = "") -> None:
        with open(self.out / "post.tsv", "a", encoding="utf-8") as f:
            f.write(f"{step}\t{rc}\t{now()}\t{_tsv(note)}\n")

    def log(self, msg: str) -> None:
        line = f"{now()} {msg}"
        print(line, flush=True)
        if self.out is not None and self.out.is_dir():
            with open(self.out / "driver.log", "a", encoding="utf-8") as f:
                f.write(line + "\n")

    def prepare(self) -> dict:
        a, e = self.a, self.env
        if a.series and not a.expect:
            raise Refused(2, "--series needs --expect: a series attempt plays a frozen expectation")
        if a.series and a.check:
            raise Refused(2, "--series with --check: a check is never an attempt")
        for k in ("RATE_RUNS",):
            if not e.get(k):
                raise Refused(2, f"{k} is not set (docs/harness.md, environment variables)")
        exp = _expectation(a.expect) if a.expect else {}
        if a.series:
            from ck3bench.harness import score
            try:
                ser = score.load_series(a.series)
            except (OSError, ValueError) as x:
                raise Refused(2, f"series: {type(x).__name__}: {x} (no lock taken, no game)") from None
        else:
            ser = {}
        self.resolve_timings(exp)
        if a.series and (a.agent_cli or a.session_env):
            raise Refused(2, "REFUSE series_agent_cli: --agent-cli and --session-env change the binary the sessions run "
                             "(and what they get) while the identity records RATE_CLAUDE_BIN's version; a series "
                             "attempt runs RATE_CLAUDE_BIN as it is (a stand-in agent CLI goes there)")
        if a.series and not e.get("RATE_PROTECTED_DIR"):
            raise Refused(2, "REFUSE protected_dir_not_configured: a series attempt digests RATE_PROTECTED_DIR before "
                             "and after its game (the game's default user-data directory); set it in the host "
                             "configuration (no lock taken, no game)")
        e.update(RATE_HOST=a.host, RATE_KIND="check" if a.check else "rate", RATE_EXPECT=a.expect or "",
                 RATE_SERIES=a.series or "", KIT_INSTANCE=e.get("RATE_INSTANCE", ""), RATE_POLICY=a.policy or "")
        e.setdefault("RATE_SCENARIO", exp.get("task") or ser.get("task") or "")
        if not e["RATE_SCENARIO"]:
            raise Refused(2, "no task: set RATE_SCENARIO or declare `task` in the expectation")
        e["RATE_MODEL"] = exp.get("model") or e.get("RATE_MODEL") or ("none" if a.policy else "")
        if not e["RATE_MODEL"]:
            raise Refused(2, "no model id: declare `model` in the expectation or set RATE_MODEL")
        speed, aligned = exp.get("game_speed"), exp.get("aligned_checks") is True
        if speed is not None and speed not in (1, 2, 3, 4, 5):
            raise Refused(2, f"expectation game_speed {speed!r} (1..5)")
        if aligned and speed is None:
            raise Refused(2, "the expectation declares aligned_checks and no game_speed (aligned checks come with the "
                             "declared speed)")
        e["RATE_GAME_SPEED"] = "" if speed is None else str(speed)
        if not a.policy and not e.get("RATE_CLAUDE_BIN"):
            raise Refused(2, "RATE_CLAUDE_BIN is not set: the agent CLI the sessions run, whose `--version` the "
                             "identity records (a --policy run starts none and records none)")
        # 0. the game's environment, before any child step
        self.game_env = "not checked: the task does not start the game"
        if _uses_game(e["RATE_SCENARIO"]):
            bad = game_env_refusals(e)
            if bad:
                raise Refused(2, "\n".join(bad) + "\n(docs/harness.md, \"The game's environment\"; no lock taken, "
                                                  "no game)")
            self.game_env = " ".join(f"{k}={e[k]}" if k in e else f"{k}=(unset)" for k in GAME_ENV_KEYS)
        # 1. switches
        r = self.step("series", "switches")
        if r.returncode != 0:
            raise Refused(5, "switches differ from the expectation (no lock taken, no game):\n" + r.stdout + r.stderr)
        for ln in r.stdout.splitlines():
            if ln.startswith("export "):
                k, _, v = ln[7:].partition("=")
                e[k] = v
            elif ln.startswith("unset "):
                e.pop(ln[6:].strip(), None)
        bad = [f"{k}={e.get(k)!r}" for k, off in UNHONOURED.items() if e.get(k, off) != off]
        if bad:
            raise Refused(5, f"REFUSE kit_switch_unhonoured: {', '.join(bad)}: this driver records no video, sets no "
                             "camera and installs no map mod; declare them off in the expectation's `kit` "
                             "(RECORD=0, VIEW=, CLEANMAP=0)")
        self.switches = r.stdout
        # 2. plan
        r = self.step("series", "plan")
        if r.returncode != 0:
            raise Refused(2, f"plan: {r.stdout}{r.stderr}")
        plan = json.loads(r.stdout)
        # 3. slot (and the run id it names)
        run_id = a.run_id
        if a.series:
            if run_id is None:
                from ck3bench.harness import score, series as SR
                with _environ(e):                 # the series module reads os.environ: give it this run's (--config)
                    got, _ = SR.slot(score.load_series(a.series), a.host, "")
                run_id = (got or {}).get("run_id") or ""
            r = self.step("series", "slot", a.host, run_id)
            if r.returncode != 0:
                raise Refused(7, f"series: not this host's next attempt (no lock taken, no game):\n{r.stdout}")
        if not run_id or not RUN_ID.match(run_id):
            raise Refused(2, f"run id {run_id!r} (want [A-Za-z0-9][A-Za-z0-9._-]*)")
        e["RATE_RUN_ID"] = run_id
        flags = ["--scenario", e["RATE_SCENARIO"], "--seat", plan["seat"], "--n-steps", str(plan["n_steps"]),
                 "--turn-timeout-s", str(a.turn_timeout_s), "--step-wait-s", str(a.step_wait_s),
                 "--wall-budget-s", str(a.wall_budget_s), "--hold-before-close-s", e.get("FULL_HOLD", "600")]
        if e.get("RATE_SAVES"):
            flags += ["--saves-dir", e["RATE_SAVES"]]
        if e.get("RATE_INSTANCE"):
            flags += ["--instance-id", e["RATE_INSTANCE"]]
        if e["RATE_GAME_SPEED"]:
            flags += ["--game-speed", e["RATE_GAME_SPEED"]]
        if a.policy:
            flags += ["--policy", a.policy]
        else:
            flags += ["--model", e["RATE_MODEL"], "--claude", a.agent_cli or e["RATE_CLAUDE_BIN"]]
        for k in a.session_env or []:
            flags += ["--session-env", k]
        flags += ["--notes", e.get("NOTES", "on")]
        e["RATE_RUNNER_FLAGS"] = json.dumps(flags)
        if a.paired:
            e["RATE_PAIRED"] = a.paired
        return {"plan": plan, "run_id": run_id, "flags": flags}

    def resolve_timings(self, exp: dict) -> None:
        """The runner's timings: in a series, the expectation's `timings` (series declare writes them), and a flag
        that names another value is refused; outside a series, the flag or the default (DEFAULTS)."""
        a = self.a
        if not a.series:
            for k, v in DEFAULTS.items():
                if getattr(a, k) is None:
                    setattr(a, k, v)
            return
        t = exp.get("timings")
        if not isinstance(t, dict) or sorted(t) != sorted(DEFAULTS) or not all(
                isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0 for v in t.values()):
            raise Refused(2, "REFUSE expect_timings: the expectation declares no `timings` "
                             f"({', '.join(sorted(DEFAULTS))}, seconds; series declare writes them): a series holds them")
        bad = [f"--{k.replace('_', '-')} {getattr(a, k):g} (declared {t[k]:g})" for k in DEFAULTS
               if getattr(a, k) is not None and float(getattr(a, k)) != float(t[k])]
        if bad:
            raise Refused(5, "REFUSE expect_differs:timings: " + "; ".join(bad) + " (no lock taken, no game)")
        for k in DEFAULTS:
            setattr(a, k, float(t[k]))

    def dry(self, p: dict) -> int:
        out = Path(self.env["RATE_RUNS"]) / p["run_id"]
        print(f"series run dry run: {self.env['RATE_KIND']} run {p['run_id']} on {self.a.host}; nothing below runs\n"
              f" game env: {self.game_env}\n"
              f" plan:     {json.dumps(p['plan'])}\n switches: {self.switches.strip().replace(chr(10), '; ')}\n"
              f" lock:     {self.lockdir()}\n run dir:  {out}\n"
              f" episode:  run-seat --out {out} {shlex.join(p['flags'])}")
        return 0

    def lockdir(self) -> Path:
        return Path(self.env["RATE_LOCK_DIR"]) if self.env.get("RATE_LOCK_DIR") else default_lock_dir()

    def take_lock(self, run_id: str) -> None:
        d = self.lockdir()
        if self.env.get("RATE_LOCK_DIR"):
            d.parent.mkdir(parents=True, exist_ok=True)
        else:
            _private_parent(d.parent)
        try:
            d.mkdir(mode=0o700)
        except FileExistsError:
            try:
                who = (d / "holder").read_text(encoding="utf-8", errors="replace").strip()
            except OSError:
                who = "no holder line"
            pid = who.rsplit(" pid ", 1)[-1] if " pid " in who else ""
            alive = (f"; pid {pid} is {'running' if Path(f'/proc/{pid}').exists() else 'not running'}"
                     if pid.isdigit() else "")
            raise Refused(3, f"lock {d} held: {who}{alive}{lock_games_text(d)}. It is never taken over: when its "
                             f"holder and the game it names are gone, check that no game of that run is left, then "
                             f"remove {d} by hand")
        self.holder = f"ck3bench-series {now()} {run_id} pid {os.getpid()}"
        (d / "holder").write_text(self.holder + "\n")
        self.lock = d
        self.env["RATE_LOCK_TAKEN"] = now()

    def release_lock(self) -> str:
        d = self.lock
        if d is None:
            return "no lock taken"
        try:
            mine = (d / "holder").read_text(encoding="utf-8", errors="replace").strip() == self.holder
        except OSError:
            mine = False
        if not mine:
            return f"lock {d} not ours at the end: left as found"
        (d / "holder").unlink()
        (d / LOCK_GAME).unlink(missing_ok=True)
        d.rmdir()
        self.lock = None
        return "lock released"

    def run(self) -> int:
        a, e = self.a, self.env
        p = self.prepare()
        if a.dry_run:
            return self.dry(p)
        out = Path(e["RATE_RUNS"]) / p["run_id"]
        if out.exists():
            raise Refused(3, f"{out} exists")
        self.take_lock(p["run_id"])
        rc = 1
        try:
            out.parent.mkdir(parents=True, exist_ok=True)
            out.mkdir(mode=0o700, parents=False)
            self.out = out
            self.log(f"lock taken: {self.lock}: {self.holder}")
            self.log(f"game env: {self.game_env}")
            e["RATE_LAUNCH_UTC"] = now()
            e["CK3BENCH_CONTROL_LOG"] = str(out / "control.jsonl")
            if e.get("RATE_SO"):
                e["CK3AGENT_BRIDGE"] = e["RATE_SO"]
            if e.get("MAP_BIN") and Path(e["MAP_BIN"]).is_file():
                e["CK3MAP_BIN"] = e["MAP_BIN"]
            if e.get("RATE_WHEEL") and Path(e["RATE_WHEEL"]).is_file():
                e["CK3BENCH_TOOLKIT_WHEEL_SHA256"] = hashlib.sha256(Path(e["RATE_WHEEL"]).read_bytes()).hexdigest()
            rc = self.attempt(out, p)
        finally:
            self.finish(rc)
        return rc

    def attempt(self, out: Path, p: dict) -> int:
        a, e = self.a, self.env
        r = self.step("series", "start", str(out))
        self.log(f"start rc {r.returncode}: {r.stdout.strip()[-400:]}")
        if r.returncode != 0:
            self.post("start", r.returncode, "refused: identity differs from the expectation")
            self.refusal = r.stdout + r.stderr
            return 6
        self.started = True
        self.sampler = Sampler(out, float(e.get("RATE_SAMPLE_S") or 30))
        self.sampler.start()
        rc, line = protected_digest(e.get("RATE_PROTECTED_DIR"), out / "own.before")
        self.post("own_before", rc, line)
        (out / "ck3.before").write_text("".join(x + "\n" for x in ck3_pids(self.game_bin())))
        with open(out / "watch.out", "w") as wo:
            self.watcher = subprocess.Popen([self.py, "-m", "ck3bench.harness", "series", "watch", str(out)],
                                            env=e, stdout=wo, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
        self.log(f"watcher pid {self.watcher.pid}")
        argv = [self.py, "-m", "ck3bench.harness", "run-seat", "--out", str(out), "--python", self.py,
                *json.loads(e["RATE_RUNNER_FLAGS"])]
        self.log("episode: " + shlex.join(argv))
        with open(out / "driver_runner.out", "w") as ro:
            runner = subprocess.Popen(argv, env=e, stdout=ro, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
            self.runner = runner
            seat_rc = self.wait_runner(runner)
        write_whole(out / "done", f"rc={seat_rc} end={now()}\n")
        self.post("run-seat", seat_rc, (out / "done").read_text().strip())
        r = self.step("accounting", str(out))
        (out / "accounting.txt").write_text(r.stdout + r.stderr)
        self.post("accounting", r.returncode)
        try:
            self.watcher.wait(WATCHER_WAIT_S)
            self.post("watcher", 0, "ended")
        except subprocess.TimeoutExpired:
            self.watcher.terminate()
            self.watcher.wait(60)
            self.post("watcher", 143, f"TERMed {WATCHER_WAIT_S} s after the episode")
        self.watcher = None
        r = self.step("analyze", str(out))
        (out / "analysis.txt").write_text(r.stdout + r.stderr)
        self.post("analyze", r.returncode)
        rc, line = protected_digest(e.get("RATE_PROTECTED_DIR"), out / "own.after")
        self.post("own_after", rc, line)
        r = self.step("series", "end", str(out))
        self.log(f"end rc {r.returncode}: {r.stdout.strip()[-400:]}")
        self.post("end", r.returncode)
        if r.returncode != 0:
            return END_INCOMPLETE
        from ck3bench.harness import score
        if score.read_run_json(out)[0] is None:
            # `series end` recorded the attempt in its ledger entry (its run.json does not read): no result to write
            self.post("result", 0, "not run: run.json does not read; the ledger entry is the attempt's record")
            self.ended = True
            return 0
        r = self.step("series", "result", str(out))
        self.log(f"result rc {r.returncode}: {r.stdout.strip()[-400:]}")
        self.post("result", r.returncode)
        if r.returncode != 0:
            return END_INCOMPLETE
        self.ended = True
        return 0

    def wait_runner(self, runner: subprocess.Popen) -> int:
        """The runner's exit code; at --wall-s it is sent TERM (it closes `env play` and its game), and killed
        TERM_WAIT_S later if it has not exited. A killed runner may leave its game alive: finish() sees to that."""
        try:
            return runner.wait(self.a.wall_s)
        except subprocess.TimeoutExpired:
            runner.terminate()                    # the runner closes the env and its game on TERM
            try:
                rc = runner.wait(TERM_WAIT_S)
            except subprocess.TimeoutExpired:
                runner.kill()
                rc = runner.wait(30)
                self.log(f"runner killed {TERM_WAIT_S:g} s after TERM")
            self.log(f"runner stopped at the driver's wall limit {self.a.wall_s} s")
            return rc

    def finish(self, rc: int) -> None:
        runner = getattr(self, "runner", None)
        if runner is not None and runner.poll() is None:
            # the driver was stopped (a TERM, an interrupt) while its runner plays: the runner closes the env and its
            # game on TERM, as at the wall limit
            runner.terminate()
            try:
                runner.wait(TERM_WAIT_S)
            except subprocess.TimeoutExpired:
                runner.kill()
                runner.wait(30)
        if self.sampler is not None:
            self.sampler.stop.set()
            self.sampler.join(30)
        if self.watcher is not None and self.watcher.poll() is None:
            self.watcher.terminate()
            try:
                self.watcher.wait(60)
            except subprocess.TimeoutExpired:
                self.watcher.kill()
        games = self.wait_for_games()
        if games and self.lock is not None:
            # a game this run started outlived the runner: the lock stays until it exits, and names it
            run = self.out.name if self.out is not None else "?"
            (self.lock / LOCK_GAME).write_text("".join(f"pid {p} exe {e} run {run}\n" for p, e in games))
            msg = (f"lock {self.lock} KEPT: the game of this run is still alive "
                   f"({', '.join(f'pid {p} {e}' for p, e in games)}) {GAME_EXIT_WAIT_S:g} s after the runner ended; "
                   f"remove the lock by hand once it has exited")
            self.log(msg)
            if self.out is not None and self.out.is_dir():
                self.post("lock", 1, msg)
            print(f"WARNING: {msg}", file=sys.stderr, flush=True)
        else:
            self.log(self.release_lock())
        if self.out is not None and self.out.is_dir():
            if self.ended:
                write_whole(self.out / END_MARKER, f"rc={rc} end={now()}\n")
            elif self.started:
                self.unfinished()
            else:
                self.start_aside(rc)

    def unfinished(self) -> None:
        """An attempt whose end steps did not complete (`series end` or `series result` failed, or the driver stopped
        before them): no end marker, and the command that completes it, said in driver.log, post.tsv and on stderr
        (docs/harness.md, "A driver stopped mid-attempt")."""
        from ck3bench.harness import score, series as SR
        out = self.out
        rj, _ = score.read_run_json(out)
        how = (SR.END_DAMAGED_BY_HAND if rj is None else
               SR.CLOSE_BY_HAND if isinstance(rj.get("validity"), dict) else
               SR.END_BY_HAND if score.played_out(out) else SR.WITHDRAW_BY_HAND).format(out=out)
        msg = f"the attempt's end steps did not complete: no end marker written; complete it with {how}"
        self.log(msg)
        self.post("marker", END_INCOMPLETE, msg)
        print(f"WARNING: {msg}", file=sys.stderr, flush=True)

    def start_aside(self, rc: int) -> None:
        """A run whose start was refused or failed: no game ran and no ledger entry was written. Its directory is
        moved out of the slot's place to RATE_RUNS/refused-starts/RUN_ID-UTC with start_refused.txt (the refusal):
        kept as the record, scored as not an attempt (score.START_REFUSED), and the slot is free for a retry."""
        from ck3bench.harness import score
        out = self.out
        (out / score.START_REFUSED_FILE).write_text(
            f"rc={rc} at={now()}: the start was refused or failed; no game ran and no ledger entry was written\n"
            + (self.refusal.strip() + "\n" if self.refusal.strip() else ""))
        dst = out.parent / score.REFUSED_DIR / f"{out.name}-{dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
        try:
            dst.parent.mkdir(mode=0o700, exist_ok=True)
            out.rename(dst)
        except OSError as e:
            self.log(f"start refused; the run dir could not be moved aside ({type(e).__name__}: {e}): move {out} "
                     f"out of {out.parent} by hand before retrying this slot")
            return
        self.out = dst
        self.log(f"start refused: run dir moved to {dst} (not an attempt; the slot {out.name} is free)")

    def game_bin(self) -> str:
        """The configured game binary (RATE_GAME_BIN), resolved; "" when none is configured."""
        g = (self.env or {}).get("RATE_GAME_BIN") or ""
        return os.path.realpath(g) if g else ""

    def run_games(self) -> list[tuple[int, str]]:
        """This run's games alive now: those its witnesses recorded (recorded_games) and those no witness recorded
        (unwitnessed_games), each once."""
        games = [g for g in recorded_games(self.out) if alive_as(*g)]
        e = self.env or {}
        for g in unwitnessed_games(self.out, self.game_bin(), e.get("RATE_RUN_ID", ""),
                                   e.get("KIT_INSTANCE") or e.get("RATE_INSTANCE", "")):
            if g[0] not in {p for p, _ in games}:
                games.append(g)
        return games

    def wait_for_games(self) -> list[tuple[int, str]]:
        """The games of this run that are still alive (run_games), after waiting up to GAME_EXIT_WAIT_S for them to
        exit ([] at once when none is alive: the usual end, the runner having closed its game)."""
        if self.out is None:
            return []
        t0 = time.monotonic()
        said = False
        while True:
            games = self.run_games()
            if not games or time.monotonic() - t0 >= GAME_EXIT_WAIT_S:
                return games
            if not said:
                self.log(f"the run's game is still alive ({games}): the lock is held until it exits "
                         f"(up to {GAME_EXIT_WAIT_S:g} s)")
                said = True
            time.sleep(min(2.0, GAME_EXIT_WAIT_S))


@contextlib.contextmanager
def _environ(env: dict):
    """os.environ is `env` inside the block and what it was after it (an in-process call to a module that reads the
    environment sees the variables --config set, as every child step does)."""
    saved = dict(os.environ)
    os.environ.clear()
    os.environ.update(env)
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(saved)


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m ck3bench.harness series run",
                                description="The reference series driver: one attempt (or one unscheduled run) "
                                            "through every series step, in order. The host configuration comes "
                                            "from the environment (docs/harness.md lists every variable) or "
                                            "--config; the game started for the run inherits this environment "
                                            "(docs/harness.md, \"The game's environment\"). One driver per user "
                                            "per host holds the default lock at a time.")
    p.add_argument("--host", required=True, help="this host's label in the schedule's pattern (one letter, or a "
                                                 "letter and a digit: a, b, h1; never the machine's name)")
    p.add_argument("--run-id", default=None, help="the run id (default, in a series: this host's next slot's)")
    p.add_argument("--series", default=None, help="the frozen schedule (ck3bench.series.schedule/1 JSON)")
    p.add_argument("--expect", default=None, help="the frozen expectation (ck3bench.series.expect/1 JSON)")
    p.add_argument("--config", default=None, help="a TOML file whose [env] table sets variables the environment "
                                                  "does not")
    p.add_argument("--check", action="store_true",
                   help="a check run (kind check): never an attempt, never scored. It plays the task like an attempt: "
                        "the whole task and its model cost unless --wall-budget-s cuts it (the 30-year task: its "
                        "whole 30 game-years of clock and about 10 USD of model use per run). For the three values an "
                        "expectation freezes from a check, --wall-budget-s 900 is enough: they are in the check's "
                        "run.json `end.seen` once one turn has run")
    p.add_argument("--policy", default=None, choices=("noop",),
                   help="no agent CLI: the game's own AI plays the seat (no model)")
    p.add_argument("--agent-cli", default=None, help="the command the sessions run (default RATE_CLAUDE_BIN), e.g. "
                                                     "the stand-in: 'python3 ck3bench/harness/testdata/fake_cli.py'; "
                                                     "refused in a series (it runs RATE_CLAUDE_BIN, whose version "
                                                     "the identity holds)")
    p.add_argument("--session-env", action="append", help="a variable the sessions also get (a stand-in's mode); "
                                                          "refused in a series")
    p.add_argument("--paired", default=None, help="the run another host plays at the same time (declared, "
                                                  "recorded in run.json `concurrency`)")
    p.add_argument("--turn-timeout-s", type=float, default=None,
                   help=f"default {DEFAULTS['turn_timeout_s']:g}; in a series the expectation's `timings` (another value is refused)")
    p.add_argument("--step-wait-s", type=float, default=None,
                   help=f"default {DEFAULTS['step_wait_s']:g}; in a series the expectation's `timings` (another value is refused)")
    p.add_argument("--wall-budget-s", type=float, default=None,
                   help="the runner's own wall budget (it ends the episode `wall_budget`); default "
                        f"{DEFAULTS['wall_budget_s']:g}; in a series the expectation's `timings`")
    p.add_argument("--wall-s", type=float, default=None,
                   help="the driver's limit on the runner process (it is stopped by TERM after it); default "
                        f"{DEFAULTS['wall_s']:g}; in a series the expectation's `timings`")
    p.add_argument("--dry-run", action="store_true", help="print the plan and the runner's command; nothing runs")
    return p


def main(argv: Optional[list[str]] = None) -> int:
    a = parser().parse_args(argv)
    from ck3bench.harness.score import HOST_LABEL
    if not HOST_LABEL.match(a.host):
        print(f"REFUSE: --host {a.host!r} is not a host label ({HOST_LABEL.pattern}: a, b, h1); the label stands in "
              "every run id and published copy, so it is never the machine's name", file=sys.stderr)
        return 2
    d = Driver(a)

    def stop(signum, frame):
        raise Stopped(signum)
    old = signal.signal(signal.SIGTERM, stop) if threading.current_thread() is threading.main_thread() else None
    try:
        return d.run()
    except Refused as e:
        msg = str(e)
        print(msg if msg.startswith("REFUSE") else f"REFUSE: {msg}", file=sys.stderr)
        return e.rc
    except (KeyboardInterrupt, Stopped) as e:
        # the driver's own end step ran (Driver.run's finally: the lock, the marker or the command that completes the
        # attempt); a stop is said in one line, never a traceback
        sig = e.signum if isinstance(e, Stopped) else signal.SIGINT
        print(f"STOPPED: the driver received {signal.Signals(sig).name}", file=sys.stderr)
        return 128 + int(sig)
    finally:
        if old is not None:
            signal.signal(signal.SIGTERM, old)


class Stopped(BaseException):
    """A TERM to the driver: raised in its main thread so that its end step runs (the lock, the marker)."""

    def __init__(self, signum: int):
        super().__init__(signum)
        self.signum = signum


if __name__ == "__main__":
    sys.exit(main())
