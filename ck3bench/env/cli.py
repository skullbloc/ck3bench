"""`ck3bench env ...`: the environment from the command line.

    ck3bench env scenarios                       the built-in tasks, with status
    ck3bench env run SCENARIO --seed N --policy P [--out traj.jsonl] [--saves-dir DIR]
    ck3bench env serve SCENARIO [--port N] [--turn-timeout-s T] [--saves-dir DIR]
    ck3bench env play SCENARIO --seed N --seat S=POLICY ... --turn-timeout-s T [--external S] [--out F]
                                                 one episode, mixed policies over the env server
    ck3bench env shim --url URL --seat S --step-wait-s N
                                                 a seat's tools as a stdio MCP server (a Claude Code seat)
    ck3bench env check traj.jsonl                the trajectory file's shape
    ck3bench env baseline SCENARIO [--years Y]   the game's own AI on that task: realm counties and
                                                 the noop return over the shipped unsteered games
    ck3bench env baseline --marks                the shipped baseline's marks, range and what it lacks

`run` drives one episode in-process with the same policy on every seat (noop,
random, observe-then-gift) and prints one JSON line: returns, steps, end reason,
seed, backend and whether it was fake. On a `ck3` scenario it launches the game
(the user's own install and save).

`play` serves the environment on loopback and drives it with the mixed-policy
runner (episode.py): one policy per seat (`--seat king=agent-scripted --seat
duke1=noop`), each seat's turn on its own thread, bounded by `--turn-timeout-s`
(an overrun is stepped with [] and a typed record). `agent-scripted` is the
reference tool-calling agent with a deterministic scripted model (no model is
called). A seat named with `--external` is driven from outside (a Claude Code
session through `ck3bench env shim`); `play` then prints the server's URL first
and the server's own deadline for that seat is `--turn-timeout-s` plus
SERVER_MARGIN_S.

STOPPING. `serve` runs until it is signalled; `serve` and `play` both close the
environment (which kills the game the real backend launched and removes the
save copy it staged) on SIGTERM or SIGINT, then print one JSON line naming the
signal. They install both handlers themselves: a background job of a
non-interactive shell starts with SIGINT ignored, and Python then installs no
KeyboardInterrupt handler, so Ctrl-C alone would not stop it. A second signal
during the close is ignored, so it cannot cut the close short. `serve` exits 0
(a signal is how it stops); `play` exits 1 with `stopped_by_signal` (its episode
did not finish).
"""
from __future__ import annotations

import argparse
import contextlib
import json
import signal
import sys
import threading
from typing import Iterator, Optional

from .env import ParallelEnv
from .errors import R_BAD_REQUEST, R_STOPPED_BY_SIGNAL, EnvRefusal
from .policy import POLICY_NAMES, make_policy, run_episode
from .scenario import BUILTIN_DICTS, builtin, resolve
from .trajectory import TrajectoryWriter, check_trajectory, read_trajectory

EXIT_OK, EXIT_REFUSED, EXIT_BAD_TRAJECTORY = 0, 1, 3
#: The policies `play` can seat: the CLI's own, and the reference agent on a scripted model.
AGENT_SCRIPTED = "agent-scripted"
PLAY_POLICIES = (*POLICY_NAMES, AGENT_SCRIPTED)
#: How much longer than the runner's turn timeout the server waits for an external seat.
SERVER_MARGIN_S = 5.0
#: The signals that stop `serve` and `play` with the environment closed.
STOP_SIGNALS = (signal.SIGTERM, signal.SIGINT)
#: How long a stop waits for a request the server is serving (a reset launching the game, a
#: step running the clock) before it closes anyway; the game must not outlive the process.
CLOSE_LOCK_WAIT_S = 120.0


class Stopped(BaseException):
    """Raised in the main thread by a stop signal (a BaseException, like
    KeyboardInterrupt, so no `except Exception` on the way swallows it)."""

    def __init__(self, signum: int):
        super().__init__(signal.Signals(signum).name)
        self.signum = signum

    @property
    def name(self) -> str:
        return signal.Signals(self.signum).name


def _hold_signals() -> None:
    """From here on a stop signal is ignored: the close must run to its end."""
    if threading.current_thread() is not threading.main_thread():
        return
    for s in STOP_SIGNALS:
        signal.signal(s, signal.SIG_IGN)


@contextlib.contextmanager
def _stop_on_signals() -> Iterator[None]:
    """SIGTERM and SIGINT raise `Stopped` in the main thread (also when SIGINT
    was inherited ignored); the handlers in force before are put back after."""
    if threading.current_thread() is not threading.main_thread():
        yield                 # signal handlers can only be set from the main thread
        return
    old = {s: signal.getsignal(s) for s in STOP_SIGNALS}

    def handler(signum, frame):
        _hold_signals()
        raise Stopped(signum)
    for s in STOP_SIGNALS:
        signal.signal(s, handler)
    try:
        yield
    finally:
        for s, h in old.items():
            signal.signal(s, h if h is not None else signal.SIG_DFL)


def _close_after_stop(env, srv) -> float:
    """Close the env once the server is not inside a request (bounded by
    CLOSE_LOCK_WAIT_S; past it the env is closed anyway). Returns the wait."""
    import time
    t0 = time.monotonic()
    got = srv.lock.acquire(timeout=CLOSE_LOCK_WAIT_S) if srv is not None else False
    try:
        env.close()
    finally:
        if got:
            srv.lock.release()
    return round(time.monotonic() - t0, 3)


def _backend_kwargs(a) -> dict:
    sc = resolve(a.scenario)
    if sc.backend != "ck3":
        return {}
    kw = {"saves_dir": a.saves_dir}
    if a.instance_id:
        kw["instance_id"] = a.instance_id
    return kw


def cmd_scenarios(a) -> int:
    for name in sorted(BUILTIN_DICTS):
        s = builtin(name)
        print(json.dumps({"id": s.id, "backend": s.backend, "seats": list(s.seat_names), "steps": s.n_steps,
                          "cycle_days": s.cycle_days, "status": s.status, "why": s.status_reason}))
    return EXIT_OK


def cmd_run(a) -> int:
    writer = TrajectoryWriter(a.out) if a.out else None
    env = None
    try:
        env = ParallelEnv(a.scenario, trajectory=writer, **_backend_kwargs(a))
        pols = {s: make_policy(a.policy, a.seed + i, env.scenario) for i, s in enumerate(env.possible_agents)}
        res = run_episode(env, pols, seed=a.seed)
        print(json.dumps({"ok": True, **res, "scenario": env.scenario.id, "backend": env.backend.name,
                          "fake": env.backend.fake, "trajectory": a.out}, allow_nan=True))
        return EXIT_OK
    except EnvRefusal as e:
        print(json.dumps(e.as_dict()))
        return EXIT_REFUSED
    finally:
        if env is not None:
            env.close()
        if writer is not None:
            writer.close()


def cmd_serve(a) -> int:
    with _stop_on_signals():
        return _serve(a)


def _serve(a) -> int:
    from .server import EnvServer
    writer = TrajectoryWriter(a.out) if a.out else None
    try:
        env = ParallelEnv(a.scenario, trajectory=writer, **_backend_kwargs(a))
        srv = EnvServer(env, host=a.host, port=a.port, allow_remote=a.allow_remote,
                        turn_timeout_s=a.turn_timeout_s)
    except EnvRefusal as e:
        if writer is not None:
            writer.close()
        print(json.dumps(e.as_dict()))
        return EXIT_REFUSED
    print(json.dumps({"ok": True, "url": srv.url, "scenario": env.scenario.id, "backend": env.backend.name,
                      "turn_timeout_s": a.turn_timeout_s}), flush=True)
    stopped: Optional[str] = None
    waited = 0.0
    try:
        srv.serve_forever()
    except Stopped as e:
        stopped = e.name
    except KeyboardInterrupt:
        stopped = "SIGINT"
    finally:
        _hold_signals()
        srv.stop()
        waited = _close_after_stop(env, srv)
        if writer is not None:
            writer.close()
    print(json.dumps({"ok": True, "stopped_by": stopped, "closed": True, "close_waited_s": waited}), flush=True)
    return EXIT_OK


def _play_policy(name: str, seed: int, a, scenario):
    if name == AGENT_SCRIPTED:
        from .agent import ObserveThenEndModel, ToolCallingAgent
        return ToolCallingAgent(ObserveThenEndModel(), max_calls=a.max_calls, max_wall_s=a.max_wall_s)
    return make_policy(name, seed, scenario)


def _seat_pairs(pairs: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for p in pairs:
        seat, eq, pol = p.partition("=")
        if not eq or not seat or pol not in PLAY_POLICIES:
            raise EnvRefusal(R_BAD_REQUEST, f"--seat {p!r}: want SEAT=POLICY with POLICY one of "
                                            f"{list(PLAY_POLICIES)}", "cli")
        if seat in out:
            raise EnvRefusal(R_BAD_REQUEST, f"--seat {seat} given twice", "cli")
        out[seat] = pol
    return out


def cmd_play(a) -> int:
    with _stop_on_signals():
        return _play(a)


def _play(a) -> int:
    from .episode import run_mixed_episode
    from .server import EnvClient, EnvServer
    writer = env = srv = None
    try:
        if not a.turn_timeout_s > 0:
            raise EnvRefusal(R_BAD_REQUEST, f"--turn-timeout-s {a.turn_timeout_s} must be > 0", "cli")
        seats = _seat_pairs(a.seat)
        writer = TrajectoryWriter(a.out) if a.out else None
        env = ParallelEnv(a.scenario, trajectory=writer, **_backend_kwargs(a))
        order = list(env.possible_agents)
        pols = {s: _play_policy(p, a.seed + (order.index(s) if s in order else 0), a, env.scenario)
                for s, p in seats.items()}
        srv = EnvServer(env, host=a.host, port=a.port, allow_remote=a.allow_remote,
                        turn_timeout_s=a.turn_timeout_s + SERVER_MARGIN_S).start()
        if a.external:
            print(json.dumps({"ok": True, "serving": srv.url, "external": a.external, "scenario": env.scenario.id,
                              "server_turn_timeout_s": a.turn_timeout_s + SERVER_MARGIN_S}), flush=True)
        out = run_mixed_episode(EnvClient(srv.url), pols, seed=a.seed, turn_timeout_s=a.turn_timeout_s,
                                external=a.external)
        timeouts = sum(1 for st in out["turns"] for r in st["seats"].values()
                       if (r.get("turn") or {}).get("status") == "timeout")
        print(json.dumps({"ok": True, "returns": out["returns"], "steps": out["steps"],
                          "end_reason": out["end_reason"], "seed": out["seed"], "seats": seats,
                          "external": a.external, "runner_timeouts": timeouts, "scenario": env.scenario.id,
                          "backend": env.backend.name, "fake": env.backend.fake, "trajectory": a.out},
                         allow_nan=True))
        return EXIT_OK
    except EnvRefusal as e:
        print(json.dumps(e.as_dict()))
        return EXIT_REFUSED
    except Stopped as e:
        stopped = e.name
    finally:
        _hold_signals()
        if srv is not None:
            srv.stop()
        waited = _close_after_stop(env, srv) if env is not None else 0.0
        if writer is not None:
            writer.close()
    print(json.dumps({**EnvRefusal(R_STOPPED_BY_SIGNAL, f"{stopped}: the episode was closed before its end",
                                   "cli").as_dict(), "stopped_by": stopped, "closed": env is not None,
                      "close_waited_s": waited}), flush=True)
    return EXIT_REFUSED


def cmd_shim(a) -> int:
    from . import mcp_shim
    return mcp_shim.main(["--url", a.url, "--seat", a.seat, "--step-wait-s", str(a.step_wait_s)])


def cmd_check(a) -> int:
    try:
        recs = read_trajectory(a.trajectory)
    except (OSError, ValueError) as e:
        print(json.dumps({"ok": False, "problems": [str(e)]}))
        return EXIT_BAD_TRAJECTORY
    problems = check_trajectory(recs)
    print(json.dumps({"ok": not problems, "records": len(recs), "problems": problems,
                      "fake": sorted({bool(r.get("fake")) for r in recs})}))
    return EXIT_OK if not problems else EXIT_BAD_TRAJECTORY


def cmd_baseline(a) -> int:
    from . import baselines
    try:
        if a.marks:
            row = baselines.list_marks()
        elif a.scenario is None:
            print(json.dumps(EnvRefusal(R_BAD_REQUEST, "name a scenario, or pass --marks", "baseline").as_dict()))
            return EXIT_REFUSED
        else:
            row = baselines.reading(a.scenario, years=a.years)
    except EnvRefusal as e:
        print(json.dumps(e.as_dict()))
        return EXIT_REFUSED
    print(json.dumps(row, indent=None if a.compact else 2))
    return EXIT_OK


def add_arguments(p: argparse.ArgumentParser) -> None:
    sub = p.add_subparsers(dest="env_cmd", required=True)
    sub.add_parser("scenarios", help="list the built-in tasks").set_defaults(env_fn=cmd_scenarios)
    for name, fn, help_ in (("run", cmd_run, "run one episode in-process, one policy on every seat"),
                            ("serve", cmd_serve, "serve one environment over HTTP+JSON"),
                            ("play", cmd_play, "run one episode over the env server, a policy per seat")):
        s = sub.add_parser(name, help=help_)
        s.add_argument("scenario", help="a built-in id or a scenario .toml")
        s.add_argument("--saves-dir", default=None, help="where your own CK3 saves are (ck3 scenarios)")
        s.add_argument("--instance-id", default=None, help="CK3_INSTANCE_ID for this game (ck3 scenarios)")
        s.add_argument("--out", default=None, help="write the trajectory JSONL here")
        if name == "run":
            s.add_argument("--seed", type=int, required=True)
            s.add_argument("--policy", choices=POLICY_NAMES, required=True)
        else:
            s.add_argument("--host", default="127.0.0.1")
            s.add_argument("--port", type=int, default=8770 if name == "serve" else 0)
            s.add_argument("--allow-remote", action="store_true")
        if name == "serve":
            s.add_argument("--turn-timeout-s", type=float, default=None,
                           help="the step's deadline for per-seat /submit (required for /submit: a seat that has "
                                "not submitted by then is stepped with [] and a typed timeout record)")
        if name == "play":
            s.add_argument("--seed", type=int, required=True)
            s.add_argument("--seat", action="append", default=[], metavar="SEAT=POLICY",
                           help=f"one per seat the runner drives; POLICY one of {list(PLAY_POLICIES)}")
            s.add_argument("--external", action="append", default=[], metavar="SEAT",
                           help="a seat driven from outside (ck3bench env shim); repeatable")
            s.add_argument("--turn-timeout-s", type=float, required=True,
                           help="each runner seat's turn bound (an overrun is stepped with [] and a typed record)")
            s.add_argument("--max-calls", type=int, default=20, help="agent-scripted: tool calls per turn")
            s.add_argument("--max-wall-s", type=float, default=60.0, help="agent-scripted: seconds per turn")
        s.set_defaults(env_fn=fn)
    sh = sub.add_parser("shim", help="a seat's tools from a running env server as a stdio MCP server")
    sh.add_argument("--url", required=True, help="the env server (ck3bench env serve / play)")
    sh.add_argument("--seat", required=True)
    sh.add_argument("--step-wait-s", type=float, required=True,
                    help="how long end_turn waits for the step to run before answering step_pending")
    sh.set_defaults(env_fn=cmd_shim)
    c = sub.add_parser("check", help="check a trajectory file's shape")
    c.add_argument("trajectory")
    c.set_defaults(env_fn=cmd_check)
    b = sub.add_parser("baseline", help="the game's own AI on a task, from the shipped unsteered games")
    b.add_argument("scenario", nargs="?", default=None, help="a built-in id or a scenario .toml")
    b.add_argument("--years", type=int, default=None,
                   help="read realm counties at this many game-years instead of the scenario's horizon")
    b.add_argument("--marks", action="store_true", help="list the baseline's marks, range and what it lacks")
    b.add_argument("--compact", action="store_true", help="one JSON line")
    b.set_defaults(env_fn=cmd_baseline)


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(prog="ck3bench env")
    add_arguments(p)
    a = p.parse_args(argv)
    return a.env_fn(a)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
