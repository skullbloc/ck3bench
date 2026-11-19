"""Mirrors checked against their source (the toolkit server's code: this
tree's when it carries one, else the installed ck3agent wheel's), the seat
transport against real MCP servers (no game), and the package's public text."""
from __future__ import annotations

import os
import re
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from ck3bench.env import calendar
from ck3bench.env.types import PUBLIC_TOOLS

PKG = Path(__file__).resolve().parent
REPO = PKG.parents[1]
SERVER_SRC = REPO / "ck3agent" / "server"


def test_public_tools_are_what_the_toolkit_server_registers(tmp_path):
    """The toolkit server's own list_tools, read in a child process."""
    d = _toolkit_server_dir()
    code = ("import sys, json; sys.path.insert(0, %r); from server import mcp_server as m; "
            "print(json.dumps([t['name'] for t in m.registered_tool_definitions()]))" % str(d.parent))
    env = {k: v for k, v in os.environ.items() if not k.startswith("CK3_")}
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=300, env=env,
                         cwd=tmp_path)
    assert out.returncode == 0, out.stderr[-500:]
    import json
    assert tuple(json.loads(out.stdout.strip().splitlines()[-1])) == PUBLIC_TOOLS


def test_the_calendar_agrees_with_the_toolkits():
    import importlib.util
    d = _toolkit_server_dir()
    spec = importlib.util.spec_from_file_location("_toolkit_cycle_cadence", d / "cycle_cadence.py")
    cc = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = cc                        # a dataclass module must be importable by name
    try:
        spec.loader.exec_module(cc)
    finally:
        del sys.modules[spec.name]
    for o in range(0, 1100 * 365, 97):
        assert calendar.date_from_ordinal(o) == cc.date_from_ordinal(o)
        assert calendar.ordinal(cc.date_from_ordinal(o)) == cc.ordinal(cc.date_from_ordinal(o)) == o
    for bad in ("1066.13.01", "1066.02.29", "x", None, "1066.9"):
        assert calendar.ordinal(bad) is None


#: The bridge verbs that move the game clock (the toolkit's own clock-neutral test names the same four).
CLOCK_VERBS = {"pause_game", "unpause_game", "set_speed", "set_pause"}


def _toolkit_server_dir() -> Path:
    """The toolkit server's source: this tree's, else the installed wheel's.
    Neither is a red (ck3agent is this package's one dependency)."""
    if (SERVER_SRC / "redesign.py").is_file():
        return SERVER_SRC
    import importlib.util
    spec = importlib.util.find_spec("ck3agent")
    assert spec is not None and spec.submodule_search_locations, "ck3agent is not installed"
    d = Path(list(spec.submodule_search_locations)[0]) / "server"
    assert (d / "redesign.py").is_file() and (d / "mcp_server.py").is_file(), f"no toolkit server source in {d}"
    return d


def _clock_census(server_dir: Path) -> tuple[list[str], set[str], dict[str, bool]]:
    """Read from the toolkit's code, never its docs: (the registered tools, the
    ones whose implementation reaches a clock verb, and per session action
    whether its branch does). A function reaches a clock verb when its body
    names one or calls (by name, across every server module) a function that
    does; a name shared by two functions counts as both, so the census can only
    over-report (it fails open)."""
    import ast
    trees = {p.name: ast.parse(p.read_text(encoding="utf-8")) for p in sorted(server_dir.glob("*.py"))
             if not p.name.startswith(("test_", "conftest"))}

    def direct(node) -> tuple[bool, set[str]]:
        consts = {n.value for n in ast.walk(node) if isinstance(n, ast.Constant) and isinstance(n.value, str)}
        called = {n.func.id if isinstance(n.func, ast.Name) else getattr(n.func, "attr", "")
                  for n in ast.walk(node) if isinstance(n, ast.Call)}
        return bool(consts & CLOCK_VERBS), called
    info: dict[str, tuple[bool, set[str]]] = {}
    for t in trees.values():
        for n in ast.walk(t):
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                c, called = direct(n)
                old = info.get(n.name, (False, set()))
                info[n.name] = (old[0] or c, old[1] | called)
    clock = {k for k, (c, _) in info.items() if c}
    grew = True
    while grew:
        grew = False
        for k, (_, called) in info.items():
            if k not in clock and called & clock:
                clock.add(k)
                grew = True
    tools = [n.name for n in ast.walk(trees["mcp_server.py"]) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
             and any(isinstance(d, ast.Call) and getattr(d.func, "attr", "") == "tool" for d in n.decorator_list)]
    branches = next(n for n in ast.walk(trees["redesign.py"])
                    if isinstance(n, ast.AsyncFunctionDef) and n.name == "_session_impl_branches")
    per_action: dict[str, bool] = {}
    for st in branches.body:
        if not isinstance(st, ast.If):
            continue
        acts = []
        for c in ast.walk(st.test):
            if isinstance(c, ast.Compare) and isinstance(c.left, ast.Name) and c.left.id == "action":
                if isinstance(c.ops[0], ast.Eq) and isinstance(c.comparators[0], ast.Constant):
                    acts.append(c.comparators[0].value)
                elif isinstance(c.ops[0], ast.In) and isinstance(c.comparators[0], (ast.Tuple, ast.List, ast.Set)):
                    acts += [e.value for e in c.comparators[0].elts if isinstance(e, ast.Constant)]
        c, called = direct(ast.Module(body=st.body, type_ignores=[]))
        for a in acts:
            per_action[a] = per_action.get(a, False) or c or bool(called & clock)
    return tools, {t for t in tools if t in clock}, per_action


def test_the_clock_verbs_are_the_toolkits_own():
    """The env refuses session's clock actions (types.clock_refusal). Census of
    the toolkit's code: `session` is the only registered tool that reaches a
    clock verb, its clock-moving branches are exactly SESSION_CLOCK_ACTIONS, and
    every action it declares is classified one way or the other."""
    from ck3bench.env.types import CLOCK_TOOL, SESSION_CLOCK_ACTIONS, SESSION_CLOCK_FREE_ACTIONS
    d = _toolkit_server_dir()
    tools, clock_tools, per_action = _clock_census(d)
    assert tuple(tools) == PUBLIC_TOOLS
    assert clock_tools == {CLOCK_TOOL}
    assert {a for a, c in per_action.items() if c} == set(SESSION_CLOCK_ACTIONS)
    assert {a for a, c in per_action.items() if not c} == set(SESSION_CLOCK_FREE_ACTIONS)
    src = (d / "redesign.py").read_text(encoding="utf-8")
    declared = re.search(r"^SESSION_ACTIONS = \(([^)]*)\)", src, re.M | re.S)
    assert declared, "the toolkit's SESSION_ACTIONS tuple was not found"
    assert set(re.findall(r'"([a-z_]+)"', declared.group(1))) == set(SESSION_CLOCK_ACTIONS) | set(
        SESSION_CLOCK_FREE_ACTIONS)


def test_control_the_clock_census_sees_a_planted_clock_verb(tmp_path):
    """CONTROL: on a copy of the toolkit's server, a planted tool that unpauses
    the game is a second clock tool, and a clock write planted in session's
    whoami branch makes whoami a clock action: the census is not blind."""
    d = _toolkit_server_dir()
    for p in d.glob("*.py"):
        if not p.name.startswith(("test_", "conftest")):
            (tmp_path / p.name).write_bytes(p.read_bytes())
    with (tmp_path / "mcp_server.py").open("a", encoding="utf-8") as f:
        f.write("\n\n" + '@mcp.tool()\nasync def rewind() -> dict:\n    return await _send("unpause_game")\n')
    rd = tmp_path / "redesign.py"
    text = rd.read_text(encoding="utf-8")
    anchor = '    if action == "whoami":\n'
    assert text.count(anchor) == 1
    rd.write_text(text.replace(anchor, anchor + "        await _session_speed_write(gw, 1)\n"), encoding="utf-8")
    tools, clock_tools, per_action = _clock_census(tmp_path)
    assert tools[-1] == "rewind" and clock_tools == {"session", "rewind"}
    assert per_action["whoami"] is True


TINY_SERVER = textwrap.dedent('''
    import os
    from mcp.server.fastmcp import FastMCP
    m = FastMCP("tiny")

    @m.tool()
    def observe(depth: str = "brief") -> dict:
        """Echo."""
        return {"ok": True, "depth": depth, "character": os.environ.get("CK3_AGENT_CHARACTER_ID")}

    m.run(transport="stdio")
''')


def test_the_seat_transport_speaks_mcp_stdio_to_a_server(tmp_path):
    pytest.importorskip("mcp")
    from ck3bench.env.ck3 import McpStdioTransport
    p = tmp_path / "tiny_server.py"
    p.write_text(TINY_SERVER, encoding="utf-8")
    t = McpStdioTransport([sys.executable, str(p)], {**os.environ, "CK3_AGENT_CHARACTER_ID": "37067"}, timeout=60)
    try:
        assert t.list_tools() == ["observe"]
        assert t.call("observe", {"depth": "full"}) == {"ok": True, "depth": "full", "character": "37067"}
    finally:
        t.close()
    assert not t._thread.is_alive()


def test_the_seat_transport_lists_the_toolkit_servers_tools_without_a_game(tmp_path):
    """The real toolkit server, started as a seat's would be (no game, no bridge):
    the transport sees exactly the public tools."""
    pytest.importorskip("mcp")
    d = _toolkit_server_dir()
    from ck3bench.env.ck3 import McpStdioTransport
    env = {k: v for k, v in os.environ.items() if not k.startswith("CK3_")}
    # HOME moved so the server's own debug log lands in tmp; the user site stays importable
    import site
    env.update(HOME=str(tmp_path), CK3_BRIDGE_SOCKET=str(tmp_path / "absent.sock"),
               PYTHONUSERBASE=site.getuserbase())
    t = McpStdioTransport([sys.executable, str(d / "mcp_server.py")], env, timeout=240)
    try:
        assert tuple(t.list_tools()) == PUBLIC_TOOLS
    finally:
        t.close()


# ── the package ships: its text names no machine and no internal label ──

HOST_PATH = re.compile(r"/home/[A-Za-z0-9_.-]+|/mnt/[A-Za-z0-9_.-]+|/run/user/\d+|/Users/[A-Za-z0-9_.-]+")
INTERNAL = re.compile(r"\bla" r"ne [A-Za-z0-9]+|\bRE" r"L[0-9]+\b|\bck-[a-z0-9]{4,}\b|"
                      r"\b[Tt]he own" r"er\b|\bown" r"er's\b|\b[Tt]he no" r"de\b|\borches" r"trator\b")


def test_the_package_text_is_public():
    hits = []
    for f in sorted(PKG.glob("*.py")):
        text = f.read_text(encoding="utf-8")
        for rx in (HOST_PATH, INTERNAL):
            hits += [(f.name, m.group(0)) for m in rx.finditer(text)]
    assert hits == []


def test_control_the_public_text_patterns_fire():
    assert HOST_PATH.search("/mnt/" "data") and INTERNAL.search("the no" "de") and INTERNAL.search("ck" "-abcd1")
