"""The env server: one environment behind a small HTTP+JSON API, so a policy
in any language, or a model behind any provider's tool-calling loop, can
drive the seats.

    GET  /spec                    the scenario, the seats, the action and observation documents
    GET  /state                   the global view
    GET  /tools?seat=S            the input schemas of the tools seat S may call (MCP tool shape)
    GET  /observation?seat=S      seat S's current observation and the step now open
    POST /reset  {seed, options}  -> {observations, infos}
    POST /call   {seat, call}     -> {result}           one in-turn tool call, the game paused
    POST /step   {actions, usage, turns} -> {observations, rewards, terminations, truncations, infos}
    POST /submit {seat, action, usage, turn} -> {submitted, step, waiting_for, stepped}
    POST /await  {step, seat?, timeout_s} -> the step's result (the seat's slice when seat is given)
    POST /close                   -> {}

TWO WAYS TO STEP. One driver holding every seat's action calls /step. Or each
seat SUBMITS its own action (/submit; a Claude Code seat through mcp_shim.py,
the runner's threads in episode.py), and the server steps when every live seat
has submitted, or at the step's deadline (`turn_timeout_s` after the step
opened; declared when the server is made, and /submit is refused without one)
once at least one seat has submitted: a seat that has not submitted is stepped
with `[]` and the turn record {"status": "timeout", "by": "server",
"timeout_s"}. A deadline that passes with no submission steps nothing (a /step
driver is never stepped under); the first later submission runs the step. A seat's /call after its
submission is refused `already_submitted` until the step runs, so a late
thread cannot act in the next step. /await waits (bounded) for a step's result.

A call is {"tool_name": ..., "arguments": {...}} (OpenEnv's CallToolAction
shape). Every reply is {"ok": true, ...} or a typed refusal {"ok": false,
"code", "detail", "leg"} (HTTP 400 for a malformed request, 409 for one the
episode's state refuses). A NaN reward is sent as null; the seat's info says
`reward_status: unavailable`.

One server holds one environment (one game). Several environments are several
servers, one per game instance. The server has no authentication: it binds
loopback unless `allow_remote` is passed explicitly.
"""
from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Mapping, Optional

from .errors import (R_ALREADY_SUBMITTED, R_BAD_REQUEST, R_REMOTE_BIND, R_STEP_PENDING, R_SUBMISSIONS_PENDING,
                     R_TURN_TIMEOUT_UNDECLARED, R_UNKNOWN_SEAT, EnvRefusal)
from .trajectory import json_safe
from .types import parse_action

LOOPBACK = ("127.0.0.1", "::1", "localhost")
#: codes that are the caller's request, not the episode's state
_BAD_REQUEST_CODES = {R_BAD_REQUEST, "action_malformed", "seed_not_an_int", "scenario_unknown",
                      R_TURN_TIMEOUT_UNDECLARED}


#: The longest single /await wait; a longer timeout_s is refused, not cut.
AWAIT_MAX_S = 3600.0


class EnvServer:
    def __init__(self, env, host: str = "127.0.0.1", port: int = 0, allow_remote: bool = False,
                 turn_timeout_s: Optional[float] = None):
        if host not in LOOPBACK and not allow_remote:
            raise EnvRefusal(R_REMOTE_BIND, f"{host}: the server has no authentication; pass allow_remote",
                             "server")
        if turn_timeout_s is not None and not turn_timeout_s > 0:
            raise EnvRefusal(R_BAD_REQUEST, f"turn_timeout_s {turn_timeout_s!r} must be > 0", "server")
        self.env = env
        self.turn_timeout_s = turn_timeout_s
        self.lock = threading.Lock()
        self.cond = threading.Condition(self.lock)
        self._subs: dict[str, dict] = {}
        self._results: dict[int, dict] = {}
        self._timer: Optional[threading.Timer] = None
        self._gen = 0
        self._expired_gen = -1
        handler = _make_handler(self)
        self.httpd = ThreadingHTTPServer((host, port), handler)
        self.thread: Optional[threading.Thread] = None
        self._serving = False

    @property
    def url(self) -> str:
        h, p = self.httpd.server_address[:2]
        return f"http://{h}:{p}"

    def start(self) -> "EnvServer":
        self._serving = True
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        return self

    def serve_forever(self) -> None:
        self._serving = True
        self.httpd.serve_forever()

    def stop(self) -> None:
        """Stop serving and release the port. `shutdown` waits for the serving loop
        to end, so it is only asked of a loop that was started (a loop that never
        ran would never answer it)."""
        self._disarm()
        if self._serving:
            self.httpd.shutdown()
            self._serving = False
        self.httpd.server_close()

    # ── the submit barrier (all under self.lock) ───────────────────────────
    def _disarm(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None

    def _arm(self) -> None:
        self._disarm()
        self._gen += 1
        if self.turn_timeout_s is not None and self.env.is_open and self.env.agents:
            self._timer = threading.Timer(self.turn_timeout_s, self._deadline, args=(self._gen,))
            self._timer.daemon = True
            self._timer.start()

    def _deadline(self, gen: int) -> None:
        with self.lock:
            if gen != self._gen or not self.env.is_open:
                return
            if not self._subs:
                # nobody is waiting (a driver that uses /step, or seats still
                # thinking): mark the step overdue; the first submission runs it
                self._expired_gen = gen
                return
            self._fill_and_run()

    def _fill_and_run(self) -> dict:
        for s in self.env.agents:
            if s not in self._subs:
                self._subs[s] = {"action": [], "usage": None,
                                 "turn": {"status": "timeout", "by": "server", "timeout_s": self.turn_timeout_s}}
        return self._run_submitted()

    def _run_submitted(self) -> dict:
        subs, self._subs = self._subs, {}
        step_no = self.env.steps_done + 1
        try:
            obs, r, te, tr, infos = self.env.step(
                {s: subs[s]["action"] for s in self.env.agents},
                usage={s: d["usage"] for s, d in subs.items() if d.get("usage")} or None,
                turns={s: d["turn"] for s, d in subs.items() if d.get("turn")} or None)
            res = {"ok": True, "observations": obs, "rewards": r, "terminations": te, "truncations": tr,
                   "infos": infos}
        except EnvRefusal as e:
            res = e.as_dict()
        self._results[step_no] = res
        self._arm()
        self.cond.notify_all()
        return res

    @staticmethod
    def _slice(step_no: int, res: dict, seat: Optional[str], agents: list) -> dict:
        if not res.get("ok"):
            return {"step": step_no, "error": res}
        if seat is None:
            return {"step": step_no, "agents": list(agents), "episode_over": not agents,
                    "rewards": res["rewards"], "terminations": res["terminations"],
                    "truncations": res["truncations"]}
        if seat not in res["observations"]:
            raise EnvRefusal(R_UNKNOWN_SEAT, f"{seat!r} was not live in step {step_no}", "server")
        return {"step": step_no, "observation": res["observations"][seat], "reward": res["rewards"][seat],
                "terminated": res["terminations"][seat], "truncated": res["truncations"][seat],
                "info": res["infos"][seat], "done": seat not in agents, "episode_over": not agents}

    # ── the routes ─────────────────────────────────────────────────────────
    def handle(self, method: str, path: str, body: Mapping[str, Any]) -> dict[str, Any]:
        env = self.env
        url = urllib.parse.urlsplit(path)
        path, query = url.path, urllib.parse.parse_qs(url.query)
        with self.lock:
            if method == "GET" and path == "/spec":
                return {"spec": env.spec()}
            if method == "GET" and path == "/state":
                return {"state": env.state()}
            if method == "GET" and path == "/observation":
                seat = (query.get("seat") or [None])[0]
                if seat is None:
                    raise EnvRefusal(R_BAD_REQUEST, "/observation needs ?seat=", "server")
                return {"seat": seat, "step": env.steps_done + 1, "observation": env.observe(seat),
                        "submitted": seat in self._subs}
            if method == "GET" and path == "/tools":
                seat = (query.get("seat") or [None])[0]
                if seat is None:
                    raise EnvRefusal(R_BAD_REQUEST, "/tools needs ?seat=", "server")
                return {"seat": seat, "tools": env.tool_specs(seat)}
            if method != "POST":
                raise EnvRefusal(R_BAD_REQUEST, f"{method} {path}", "server")
            if path == "/reset":
                self._subs, self._results = {}, {}
                obs, infos = env.reset(seed=body.get("seed"), options=body.get("options"))
                self._arm()
                return {"observations": obs, "infos": infos}
            if path == "/call":
                seat, call = body.get("seat"), body.get("call")
                if not isinstance(seat, str) or call is None:
                    raise EnvRefusal(R_BAD_REQUEST, "/call needs seat and call", "server")
                if seat in self._subs:
                    raise EnvRefusal(R_ALREADY_SUBMITTED, f"{seat}'s turn for step {env.steps_done + 1} is "
                                                          "over; calls reopen when the step has run", "server")
                return {"result": env.call(seat, call).to_dict()}
            if path == "/step":
                if "actions" not in body:
                    raise EnvRefusal(R_BAD_REQUEST, "/step needs actions", "server")
                if self._subs:
                    raise EnvRefusal(R_SUBMISSIONS_PENDING, f"{sorted(self._subs)} submitted through /submit; "
                                                            "a direct /step would drop them", "server")
                obs, r, te, tr, infos = env.step(body["actions"], usage=body.get("usage"),
                                                 turns=body.get("turns"))
                self._results[env.steps_done] = {"ok": True, "observations": obs, "rewards": r,
                                                 "terminations": te, "truncations": tr, "infos": infos}
                self._arm()
                self.cond.notify_all()
                return {"observations": obs, "rewards": r, "terminations": te, "truncations": tr,
                        "infos": infos}
            if path == "/submit":
                return self._submit(body)
            if path == "/await":
                return self._await(body)
            if path == "/close":
                self._disarm()
                env.close()
                self.cond.notify_all()
                return {}
            raise EnvRefusal(R_BAD_REQUEST, f"no route {path}", "server")

    def _submit(self, body: Mapping[str, Any]) -> dict[str, Any]:
        env = self.env
        if self.turn_timeout_s is None:
            raise EnvRefusal(R_TURN_TIMEOUT_UNDECLARED, "a server collecting per-seat submissions needs a "
                                                        "declared turn_timeout_s, so a missing seat cannot "
                                                        "hang the step", "server")
        seat = body.get("seat")
        if not isinstance(seat, str) or "action" not in body:
            raise EnvRefusal(R_BAD_REQUEST, "/submit needs seat and action", "server")
        if not env.is_open:
            raise EnvRefusal("episode_over" if env.steps_done else "not_reset", "reset first", "server")
        if seat not in env.agents:
            raise EnvRefusal(R_UNKNOWN_SEAT, f"{seat!r} is not a live seat {env.agents}", "server")
        if seat in self._subs:
            raise EnvRefusal(R_ALREADY_SUBMITTED, f"{seat} already submitted for step {env.steps_done + 1}",
                             "server")
        for k in ("usage", "turn"):
            if body.get(k) is not None and not isinstance(body[k], dict):
                raise EnvRefusal(R_BAD_REQUEST, f"/submit {k} must be an object", "server")
        action = [c.to_dict() for c in parse_action(body["action"])]
        step_no = env.steps_done + 1
        self._subs[seat] = {"action": action, "usage": body.get("usage"), "turn": body.get("turn")}
        waiting = [s for s in env.agents if s not in self._subs]
        if waiting and self._expired_gen != self._gen:
            return {"submitted": True, "step": step_no, "waiting_for": waiting, "stepped": False}
        res = self._fill_and_run() if waiting else self._run_submitted()
        return {"submitted": True, "step": step_no, "waiting_for": [], "stepped": True,
                **({"error": res} if not res.get("ok") else {})}

    def _await(self, body: Mapping[str, Any]) -> dict[str, Any]:
        step_no, seat, t = body.get("step"), body.get("seat"), body.get("timeout_s")
        if not isinstance(step_no, int) or isinstance(step_no, bool) or step_no < 1:
            raise EnvRefusal(R_BAD_REQUEST, "/await needs step (an int >= 1)", "server")
        if not isinstance(t, (int, float)) or isinstance(t, bool) or not 0 <= t <= AWAIT_MAX_S:
            raise EnvRefusal(R_BAD_REQUEST, f"/await needs timeout_s in [0, {AWAIT_MAX_S:g}]", "server")
        if seat is not None and not isinstance(seat, str):
            raise EnvRefusal(R_BAD_REQUEST, "seat is a string", "server")
        end = time.monotonic() + t
        while step_no not in self._results:
            left = end - time.monotonic()
            if left <= 0:
                raise EnvRefusal(R_STEP_PENDING, f"step {step_no} has not run after {t:g} s (waiting for "
                                                 f"{[s for s in self.env.agents if s not in self._subs]})",
                                 "server")
            self.cond.wait(left)
        return self._slice(step_no, self._results[step_no], seat, list(self.env.agents))


def _make_handler(server: EnvServer):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):        # quiet; the trajectory is the log
            pass

        def _reply(self, code: int, payload: dict) -> None:
            data = json.dumps(json_safe(payload), allow_nan=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _do(self, method: str) -> None:
            try:
                n = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(n) if n else b""
                try:
                    body = json.loads(raw) if raw else {}
                except ValueError as e:
                    raise EnvRefusal(R_BAD_REQUEST, f"body is not JSON: {e}", "server") from None
                if not isinstance(body, dict):
                    raise EnvRefusal(R_BAD_REQUEST, "body is not an object", "server")
                out = server.handle(method, self.path, body)
                self._reply(200, {"ok": True, **out})
            except EnvRefusal as e:
                self._reply(400 if e.code in _BAD_REQUEST_CODES else 409, e.as_dict())
            except Exception as e:  # noqa: BLE001
                self._reply(500, {"ok": False, "code": "server_error", "detail": f"{type(e).__name__}: {e}",
                                  "leg": "server"})

        def do_GET(self):
            self._do("GET")

        def do_POST(self):
            self._do("POST")
    return Handler


class EnvClient:
    """A minimal client (the standard library only). Refusals come back as
    EnvRefusal with the server's code."""

    def __init__(self, url: str, timeout: float = 3600.0):
        self.url, self.timeout = url.rstrip("/"), timeout

    def _req(self, method: str, path: str, body: Optional[dict] = None) -> dict:
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(self.url + path, data=data, method=method,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                out = json.loads(r.read())
        except urllib.error.HTTPError as e:
            out = json.loads(e.read() or b"{}")
        if not out.get("ok"):
            raise EnvRefusal(out.get("code", "unknown"), out.get("detail", ""), out.get("leg", "server"))
        return out

    def spec(self) -> dict:
        return self._req("GET", "/spec")["spec"]

    def state(self) -> dict:
        return self._req("GET", "/state")["state"]

    def reset(self, seed: Optional[int] = None, options: Optional[dict] = None) -> tuple[dict, dict]:
        out = self._req("POST", "/reset", {"seed": seed, "options": options})
        return out["observations"], out["infos"]

    def call(self, seat: str, tool_name: str, arguments: Optional[dict] = None) -> dict:
        return self._req("POST", "/call", {"seat": seat, "call": {"tool_name": tool_name,
                                                                  "arguments": arguments or {}}})["result"]

    def step(self, actions: Mapping[str, Any], usage: Optional[Mapping[str, Any]] = None,
             turns: Optional[Mapping[str, Any]] = None):
        out = self._req("POST", "/step", {"actions": actions, "usage": usage, "turns": turns})
        return out["observations"], out["rewards"], out["terminations"], out["truncations"], out["infos"]

    def observation(self, seat: str) -> dict:
        return self._req("GET", "/observation?" + urllib.parse.urlencode({"seat": seat}))

    def tools(self, seat: str) -> list[dict]:
        return self._req("GET", "/tools?" + urllib.parse.urlencode({"seat": seat}))["tools"]

    def submit(self, seat: str, action: Any, usage: Optional[dict] = None, turn: Optional[dict] = None) -> dict:
        return self._req("POST", "/submit", {"seat": seat, "action": action, "usage": usage, "turn": turn})

    def await_step(self, step: int, seat: Optional[str] = None, timeout_s: float = 600.0) -> dict:
        return self._req("POST", "/await", {"step": step, "seat": seat, "timeout_s": timeout_s})

    def close(self) -> None:
        self._req("POST", "/close", {})
