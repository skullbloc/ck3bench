"""Bounds read on a live game (CK3 1.18.3.1, ck3agent 2026.12.11), held in the code.

* The bridge's fastest reachable speed is 3 (`set_speed 4` writes 3): every
  built-in declares a reachable speed, and the scenario reader refuses an
  unreachable one when the declaration is READ, before anything is launched,
  naming the bound and where it was measured. Wall-time estimates in a task's
  text are computed from the measured rates.
* No shipped policy emits a call the toolkit refuses: every tool name and
  argument a shipped policy emits on a game task validates against the
  toolkit's OWN schemas (its registered tool definitions and the `do` tool's
  per-intent argument table, both read from the toolkit's code), with a
  control that a call the toolkit refused live (`do {intent: raise_taxes}`)
  is red under the same validator.
* A step record carries the backend's advance diagnostics (alarm re-arms,
  dropped reply frames) as typed fields, with a status; the fake backend says
  they do not apply.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from ck3bench.env import cli, policy as pol, scenario as scn
from ck3bench.env.backend import Backend
from ck3bench.env.env import ParallelEnv
from ck3bench.env.errors import EnvRefusal
from ck3bench.env.fake import FakeBackend
from ck3bench.env.trajectory import SCHEMA, TrajectoryWriter, check_trajectory, read_trajectory
from ck3bench.env.types import (DIAG_NOT_APPLICABLE, DIAG_NOT_REPORTED, DIAG_REPORTED, AdvanceDiagnostics,
                                ToolCall, ToolResult)

PKG = Path(__file__).resolve().parent
REPO = PKG.parents[1]


# ── a reachable game speed (speed 4 read back 3 live) ─────────────────────────────────────────────

def test_every_builtin_declares_a_reachable_speed():
    bad = {n: d["game_speed"] for n, d in scn.BUILTIN_DICTS.items() if d["game_speed"] > scn.MAX_GAME_SPEED}
    assert bad == {}
    assert scn.MAX_GAME_SPEED == 3


@pytest.mark.parametrize("speed", [4, 5])
def test_an_unreachable_speed_is_refused_when_the_declaration_is_read(speed, tmp_path):
    d = dict(scn.BUILTIN_DICTS["france1066-treasury-2y"], game_speed=speed)
    with pytest.raises(EnvRefusal) as e:
        scn.scenario_from_dict(d)
    assert e.value.code == scn.R_SCENARIO_SPEED
    assert "3" in e.value.detail and "measured" in e.value.detail and f"{speed}" in e.value.detail
    # the TOML route is the same reader: refused at load, so no backend is ever made
    p = tmp_path / "s.toml"
    p.write_text('id = "t"\ndescription = "d"\nbackend = "ck3"\ntools = ["observe"]\n'
                 f'game_speed = {speed}\nstatus = "buildable_now"\nstatus_reason = "r"\n'
                 'horizon_days = 30\ncycle_days = 30\n[start]\nsave = "x.ck3"\ndate = "1066.09.15"\n'
                 '[[seats]]\nname = "king"\ncharacter_id = 1\n[reward]\nterms = [{name = "gold_delta", weight = 1.0}]\n',
                 encoding="utf-8")
    with pytest.raises(EnvRefusal) as e2:
        ParallelEnv(str(p), saves_dir=str(tmp_path))
    assert e2.value.code == scn.R_SCENARIO_SPEED


def test_reachable_speeds_load():
    for s in (1, 2, 3):
        assert scn.scenario_from_dict(dict(scn.BUILTIN_DICTS["france1066-treasury-2y"], game_speed=s)).game_speed == s


WALL = re.compile(r"about (\d+) wall-minutes of clock at speed (\d)")


@pytest.mark.parametrize("name", sorted(n for n, d in scn.BUILTIN_DICTS.items() if d["backend"] == "ck3"))
def test_a_wall_time_in_task_text_is_the_measured_rate(name):
    sc = scn.builtin(name)
    hits = WALL.findall(sc.status_reason)
    assert hits, (name, sc.status_reason)           # every game task states one
    for minutes, speed in hits:
        assert int(speed) == sc.game_speed
        assert int(minutes) == scn.wall_minutes(sc.horizon_days, sc.game_speed), (name, minutes)
    assert "measured on one host" in sc.status_reason


def test_wall_minutes_use_the_measured_rates():
    assert scn.MEASURED_GD_PER_S == {3: 1.6, 2: 0.89}
    assert scn.wall_minutes(3650, 3) == 38 and scn.wall_minutes(730, 3) == 8
    with pytest.raises(EnvRefusal):
        scn.wall_minutes(730, 1)                       # never measured: refused, not guessed


# ── shipped policies emit only calls the toolkit accepts ───────────────

_SCHEMA_PROBE = r"""
import json, sys
src = sys.argv[1]
if src:
    sys.path.insert(0, src)
    from server import mcp_server as m, do_intent_args as a
else:
    from ck3agent.server import mcp_server as m, do_intent_args as a
tools = {t["name"]: t.get("input_schema") or t.get("inputSchema") for t in m.registered_tool_definitions()}
table = a.extract(a.REDESIGN.read_text(encoding="utf-8")); table.update(a.extract_dedicated())
print(json.dumps({"tools": tools, "intent_args": table}))
"""


@pytest.fixture(scope="module")
def toolkit():
    """The toolkit's own tool schemas and `do` argument table, read from its
    code in a child process: this tree's source, else the installed wheel."""
    tree = REPO / "ck3agent"
    if (tree / "server" / "mcp_server.py").is_file():
        src = str(tree)
    else:
        import importlib.util
        if importlib.util.find_spec("ck3agent") is None:
            pytest.skip("the toolkit is neither in this tree nor installed")
        src = ""
    env = {k: v for k, v in os.environ.items() if not k.startswith("CK3_")}
    out = subprocess.run([sys.executable, "-c", _SCHEMA_PROBE, src], capture_output=True, text=True,
                         timeout=300, env=env, cwd=REPO if src else None)
    assert out.returncode == 0, out.stderr[-800:]
    doc = json.loads(out.stdout.strip().splitlines()[-1])
    assert len(doc["tools"]) == 13 and doc["intent_args"], doc.keys()
    return doc


def problems(call: ToolCall, toolkit) -> list[str]:
    """Why the toolkit would refuse this call's name or arguments: its input
    schema (jsonschema), and for `do` the keys its intent's code reads."""
    import jsonschema
    schema = toolkit["tools"].get(call.tool_name)
    if schema is None:
        return [f"{call.tool_name!r} is not a toolkit tool"]
    out = [e.message for e in jsonschema.Draft202012Validator(schema).iter_errors(dict(call.arguments))]
    props = set((schema.get("properties") or {}))
    out += [f"unknown argument {k!r}" for k in call.arguments if k not in props]
    if call.tool_name == "do":
        intent = call.arguments.get("intent")
        keys = toolkit["intent_args"].get(intent)
        args = call.arguments.get("args") or {}
        if keys is None:
            out.append(f"intent {intent!r} has no argument table")
        else:
            out += [f"{intent}: args.{k} is not read by the toolkit" for k in args if k not in keys]
    return out


class _Recorder:
    """A tool handle that records and answers ok."""

    def __init__(self, tools):
        self.calls: list[ToolCall] = []
        self.tools = tuple(tools)
        self.closed = False
        self.specs = None

    def call(self, tool_name, **arguments):
        self.calls.append(ToolCall(tool_name, arguments))
        return ToolResult(tool_name, True, {"ok": True})


def _obs(sc, step):
    return {"seat": sc.seats[0].name, "character_id": sc.seats[0].character_id, "step": step,
            "steps_total": sc.n_steps, "world": {}, "metrics": {}, "tools": list(sc.tools), "fake": False}


def _emitted(name, sc, steps=40):
    p = pol.make_policy(name, 7, sc)
    out: list[ToolCall] = []
    if isinstance(p, pol.RandomPolicy):            # every item on its menu, not only those the draw hit
        out += [m(_obs(sc, 0)) if callable(m) else m for m in p.menu]
    for k in range(steps):
        h = _Recorder(sc.tools)
        action = list(p.act(_obs(sc, k), h))
        out += h.calls + action
    return out


GAME_TASKS = sorted(n for n, d in scn.BUILTIN_DICTS.items() if d["backend"] == "ck3"
                    and d["status"] == "buildable_now")


def test_the_census_covers_every_shipped_policy():
    """Enumerated from the code: the CLI's policy names and the runner's."""
    assert set(pol.POLICY_NAMES) | {cli.AGENT_SCRIPTED} == set(cli.PLAY_POLICIES)
    assert "observe-then-tax" not in cli.PLAY_POLICIES
    assert len(GAME_TASKS) >= 4


@pytest.mark.parametrize("task", GAME_TASKS)
@pytest.mark.parametrize("name", pol.POLICY_NAMES)
def test_every_call_a_shipped_policy_emits_validates_against_the_toolkit(name, task, toolkit):
    sc = scn.builtin(task)
    try:
        calls = _emitted(name, sc)
    except EnvRefusal as e:
        assert e.code == pol.R_POLICY_NOT_APPLICABLE, e.as_dict()
        return
    bad = {f"{c.tool_name}{json.dumps(dict(c.arguments), sort_keys=True)}": problems(c, toolkit)
           for c in calls if problems(c, toolkit)}
    assert bad == {}
    if name != "noop":
        assert calls, name                             # non-vacuous: the policy did emit something


def test_the_reference_agent_on_its_scripted_model_emits_valid_calls(toolkit):
    from ck3bench.env.agent import END_TURN, ObserveThenEndModel
    m = ObserveThenEndModel()
    tools = [{"name": n, "description": "", "inputSchema": toolkit["tools"][n]} for n in scn.PUBLIC_TOOLS]
    first = m.complete([{"role": "user", "content": "x"}], tools)
    calls = [ToolCall(c.name, c.arguments) for c in first.tool_calls if c.name != END_TURN]
    assert calls and all(problems(c, toolkit) == [] for c in calls)


def test_control_the_call_the_live_run_saw_refused_is_red(toolkit):
    """CONTROL: `do {intent: raise_taxes}` (what observe-then-tax sent; refused
    `unknown_verb` live) fails the same validator, as do an unread `args` key
    and a tool that does not exist. The gift seen applied live passes."""
    assert problems(ToolCall("do", {"intent": "raise_taxes"}), toolkit)
    assert problems(ToolCall("do", {"intent": "interact", "args": {"interaction": "gift_interaction",
                                                                   "amount": 5}}), toolkit)
    assert problems(ToolCall("raise_taxes", {}), toolkit)
    assert problems(ToolCall("observe", {"depth": "everything"}), toolkit)
    assert problems(pol.gift_call(37067, 31808), toolkit) == []


def test_observe_then_gift_is_the_live_write_once():
    sc = scn.builtin("france1066-treasury-2y")
    p = pol.make_policy("observe-then-gift", 1, sc)
    h = _Recorder(sc.tools)
    first = p.act(_obs(sc, 0), h)
    assert [c.tool_name for c in h.calls] == ["observe"]
    assert first == [ToolCall("do", {"intent": "interact", "character_id": 37067,
                                     "args": {"interaction": "gift_interaction", "target_id": 31808}})]
    assert all(p.act(_obs(sc, k), _Recorder(sc.tools)) == [] for k in range(1, 5))


def test_a_game_task_with_no_known_gift_target_is_refused_by_name():
    d = dict(scn.BUILTIN_DICTS["france1066-treasury-2y"], seats=[{"name": "king", "character_id": 99}])
    with pytest.raises(EnvRefusal) as e:
        pol.make_policy("observe-then-gift", 1, scn.scenario_from_dict(d))
    assert e.value.code == pol.R_POLICY_NOT_APPLICABLE


def test_observe_then_gift_runs_on_the_fake_world(tmp_path):
    out = tmp_path / "t.jsonl"
    rc = cli.main(["run", "fake-solo", "--seed", "3", "--policy", "observe-then-gift", "--out", str(out)])
    assert rc == 0
    recs = read_trajectory(out)
    assert check_trajectory(recs) == []
    acts = [r for r in recs if r["kind"] == "call" and r["phase"] == "action"]
    assert len(acts) == 1 and acts[0]["result"]["ok"] is True


# ── advance diagnostics in the step record ─────────────────────────────

def test_diagnostics_are_typed():
    with pytest.raises(ValueError):
        AdvanceDiagnostics(DIAG_REPORTED)                          # reported, nothing in it
    with pytest.raises(ValueError):
        AdvanceDiagnostics(DIAG_NOT_APPLICABLE, alarm_rearms=0)    # a number beside "not applicable"
    with pytest.raises(ValueError):
        AdvanceDiagnostics(DIAG_REPORTED, alarm_rearms=-1, dropped_frames=0)
    with pytest.raises(ValueError):
        AdvanceDiagnostics(DIAG_REPORTED, alarm_rearms=True, dropped_frames=0)
    with pytest.raises(ValueError):
        AdvanceDiagnostics("fine")
    d = AdvanceDiagnostics(DIAG_REPORTED, alarm_rearms=2, dropped_frames=1)
    assert d.to_dict() == {"status": "reported", "alarm_rearms": 2, "dropped_frames": 1, "detail": ""}


def test_the_fake_backend_says_the_diagnostics_do_not_apply(tmp_path):
    w = TrajectoryWriter(tmp_path / "t.jsonl")
    env = ParallelEnv("fake-solo", trajectory=w)
    pol.run_episode(env, {"king": pol.NoOpPolicy()}, seed=1)
    env.close()
    w.close()
    recs = read_trajectory(tmp_path / "t.jsonl")
    steps = [r for r in recs if r["kind"] == "step"]
    assert steps and all(r["advance"]["diagnostics"]["status"] == DIAG_NOT_APPLICABLE for r in steps)
    assert all(r["advance"]["diagnostics"]["alarm_rearms"] is None for r in steps)
    assert check_trajectory(recs) == []


class _Reporting(FakeBackend):
    """The fake world with a backend that reports diagnostics, as the real one can."""

    def __init__(self):
        super().__init__()
        self.n = 0

    def advance_diagnostics(self, adv):
        self.n += 1
        return AdvanceDiagnostics(DIAG_REPORTED, alarm_rearms=self.n, dropped_frames=self.n * 2)


def test_a_reporting_backend_reaches_the_step_record(tmp_path):
    w = TrajectoryWriter(tmp_path / "t.jsonl")
    env = ParallelEnv("fake-solo", backend=_Reporting(), trajectory=w)
    pol.run_episode(env, {"king": pol.NoOpPolicy()}, seed=1)
    env.close()
    w.close()
    steps = [r for r in read_trajectory(tmp_path / "t.jsonl") if r["kind"] == "step"]
    assert [s["advance"]["diagnostics"]["alarm_rearms"] for s in steps] == list(range(1, len(steps) + 1))
    assert [s["advance"]["diagnostics"]["dropped_frames"] for s in steps] == [2 * i for i in range(1, len(steps) + 1)]


def test_a_backend_that_says_nothing_is_not_reported_not_zero():
    class Silent(FakeBackend):
        advance_diagnostics = Backend.advance_diagnostics
    env = ParallelEnv("fake-solo", backend=Silent())
    env.reset(seed=1)
    assert Backend.advance_diagnostics(env.backend, None).status == DIAG_NOT_REPORTED
    env.close()


def test_the_checker_reads_a_diagnostics_status():
    recs = [{"schema": SCHEMA, "kind": "header", "episode_id": "e", "backend": "fake",
             "fake": True, "wall_utc": "x", "scenario": {}, "seed": 1, "seats": {}, "provenance": {}},
            {"schema": SCHEMA, "kind": "step", "episode_id": "e", "backend": "fake",
             "fake": True, "wall_utc": "x", "step": 1, "game_date": "d", "order": [], "actions": {},
             "observations": {}, "rewards": {}, "terminations": {}, "truncations": {}, "infos": {},
             "metrics": {}, "advance": {"reached": True, "diagnostics": {"status": "great"}}},
            {"schema": SCHEMA, "kind": "end", "episode_id": "e", "backend": "fake",
             "fake": True, "wall_utc": "x", "reason": "horizon", "steps": 1, "returns": {}}]
    assert [p for p in check_trajectory(recs) if "diagnostics" in p] and len(check_trajectory(recs)) == 1
