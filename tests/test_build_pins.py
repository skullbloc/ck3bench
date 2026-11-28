"""The build this version of the bench is pinned to (BUILD_PINS.toml), recomputed from the code.

A run's own records (`run.json`, the trajectory header) name the build it was played on. BUILD_PINS.toml names the
one this version of the bench describes: the toolkit release it requires (its wheel's members digest, the literal
reply keys of its server modules, its tool listing with the bridge switches unset and with the army switches set,
the source files a bridge build compiles), the seat prompt and notes version of the task of the series this version
is declared for, and the parts of this package that decide what a seat is told each turn (the goal line, the standing
orders, the MCP shim, the observation and acknowledgement builders, the game backend, the runner's turn message).

Each value is recomputed here from what this environment can read: the package's own files always; the toolkit's
values only with the toolkit installed (the members digest from the install, else from TOOLKIT_BUILD.md when the
repository ships one); the
bridge source only from a toolkit checkout named in CK3BENCH_TOOLKIT_CHECKOUT (the toolkit's wheel does not carry
it). A value that differs from its pin fails, naming the key; so does a pin no reading here covers. Controls plant
a changed value, an unchecked pin and a pin restated by hand in a doc.
"""
import ast
import hashlib
import importlib.util
import io
import os
import re
import subprocess
import sys
import tokenize
import tomllib
from pathlib import Path

import pytest

from test_public_tree import ROOT
from ck3bench.env import toolkit_source
from ck3bench.env.scenario import resolve
from ck3bench.harness import prompt as P

PINS_FILE = ROOT / "BUILD_PINS.toml"
#: the task of the series this version is declared for: its seat prompt and its notes set are pinned
TASK = "munster1066-kingdom-once-30y-wake"
#: the toolkit's bridge switches that add the army orders to the seat's `do` tool
ARMY_SWITCHES = {"CK3_BRIDGE_ARMY_ORDERS": "1", "CK3_BRIDGE_ARMY_HOLD": "1"}
#: the bench artifacts hashed from source: (key, path, None for the whole file, or the Class.method names whose raw
#: source is hashed in this order)
SEGMENTS = (
    ("orders_module", "ck3bench/env/orders.py", None),
    ("mcp_shim_module", "ck3bench/env/mcp_shim.py", None),
    ("observation_builders", "ck3bench/env/env.py",
     ("ParallelEnv._observe", "ParallelEnv._world", "ParallelEnv._unwritten", "ParallelEnv._pad",
      "ParallelEnv.write_notes", "ParallelEnv.note_unwritten")),
    ("end_turn_ack_builders", "ck3bench/env/server.py", ("EnvServer._submit",)),
    ("ck3_module", "ck3bench/env/ck3.py", None),
)
#: the runner's turn message: every function that builds the text a session is sent each turn, hashed as code
#: (code_text: comments and docstrings out), with the tool-name prefix the text names
TURN_MESSAGE = ("ck3bench/harness/runner.py",
                ("Run.fresh_message", "Run.run_fresh", "Run.check_init", "turn_lines", "pad_size", "numbered_pad",
                 "sw", "obs_delta", "delta_head", "unchanged_groups", "_sections", "_always"))
#: the files of a toolkit checkout a bridge build compiles (CK3BENCH_TOOLKIT_CHECKOUT); a digest by the members rule
BRIDGE_SOURCE = ("bridge/src", "bridge/include", "bridge/third_party", "bridge/cmake", "bridge/CMakeLists.txt",
                 "re/offsets")
HEX64 = re.compile(r"\b[0-9a-f]{64}\b")
#: the pins every environment reads (this package's own files): a pin file without one of them is incomplete
ALWAYS = ("bench.goal_line_module", "bench.orders_module", "bench.mcp_shim_module", "bench.observation_builders",
          "bench.end_turn_ack_builders", "bench.ck3_module", "bench.runner_turn_message",
          f"task.{TASK}.seat_prompt_sha256", "toolkit.version")
#: the pins that need the toolkit installed, or a toolkit checkout (bridge_source)
TOOLKIT_READ = ("toolkit.members_sha256", "toolkit.reply_keys_sha256", "toolkit.tool_listing_sha256",
                "toolkit.tool_listing_army_switches_sha256", "toolkit.bridge_source_sha256",
                f"task.{TASK}.notes_version")
#: the documents that describe the pinned build: each links the pin file and restates none of its values by hand
DOCS = ("README.md", "docs/harness.md", "docs/prereg-munster-kingdom.md")


def flat(d: dict, prefix: str = "") -> dict:
    """{dotted key: value} of a nested table."""
    out = {}
    for k, v in d.items():
        if isinstance(v, dict):
            out.update(flat(v, f"{prefix}{k}."))
        else:
            out[f"{prefix}{k}"] = v
    return out


def pins() -> dict:
    return flat(tomllib.loads(PINS_FILE.read_text(encoding="utf-8")))


def _notes_module():
    d = toolkit_source.server_dir()
    spec = importlib.util.spec_from_file_location("_toolkit_notes_pins", d / "notes.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod                # a dataclass in it looks its module up
    spec.loader.exec_module(mod)
    return mod


def listing_sha(switches: dict) -> str | None:
    """sha256 of the toolkit's registered tools as JSON (sorted keys) with `switches` and no other CK3_ variable,
    or None when its server cannot be imported here."""
    d, _why = toolkit_source.server_importable()
    if d is None:
        return None
    code = ("import json, sys; sys.path.insert(0, sys.argv[1]); import mcp_server as m; "
            "print(json.dumps(m.registered_tool_definitions(), sort_keys=True))")
    env = {k: v for k, v in os.environ.items() if not k.startswith("CK3_")}
    env.update(switches)
    r = subprocess.run([sys.executable, "-c", code, str(d)], env=env, capture_output=True, text=True, timeout=120)
    if r.returncode:
        pytest.fail(f"the toolkit's server did not list its tools: {r.stderr[-1500:]}")
    return hashlib.sha256(r.stdout.strip().splitlines()[-1].encode()).hexdigest()


def segment(src: str, names) -> str:
    """The raw source of the named methods (Class.method), in order; the whole text when `names` is None."""
    if names is None:
        return src
    found = {f"{c.name}.{f.name}": ast.get_source_segment(src, f)
             for c in ast.parse(src).body if isinstance(c, ast.ClassDef)
             for f in c.body if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef))}
    missing = [n for n in names if n not in found]
    assert not missing, f"the pinned artifact's methods are gone: {missing} (name the new ones here, then re-pin)"
    return "\n".join(found[n] for n in names)


def code_text(src: str, names) -> str:
    """The code of the named functions (`f` or `Class.method`), in order, with every comment and docstring blanked
    and whitespace runs collapsed: two texts that differ only in comments, docstrings or layout read the same. Read
    from the AST's positions and the tokenizer's comments, which do not change between Python versions."""
    tree = ast.parse(src)
    nodes = {}
    for n in tree.body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            nodes[n.name] = n
        elif isinstance(n, ast.ClassDef):
            nodes.update({f"{n.name}.{f.name}": f for f in n.body if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef))})
    missing = [x for x in names if x not in nodes]
    assert not missing, f"the turn message's functions are gone: {missing} (name the new ones here, then re-pin)"
    lines = src.splitlines(keepends=True)

    def col(line: int, byte_col: int) -> int:
        return len(lines[line - 1].encode()[:byte_col].decode())
    cut = []
    for n in ast.walk(tree):
        body = getattr(n, "body", None)
        if isinstance(n, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and body \
                and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                and isinstance(body[0].value.value, str):
            d = body[0]
            cut.append(((d.lineno, col(d.lineno, d.col_offset)), (d.end_lineno, col(d.end_lineno, d.end_col_offset))))
    cut += [(t.start, t.end) for t in tokenize.generate_tokens(io.StringIO(src).readline) if t.type == tokenize.COMMENT]
    out = []
    for name in names:
        n = nodes[name]
        seg = {i: list(lines[i - 1]) for i in range(n.lineno, n.end_lineno + 1)}
        for (sl, sc), (el, ec) in cut:
            if sl < n.lineno or el > n.end_lineno:
                continue
            for i in range(sl, el + 1):
                for c in range(sc if i == sl else 0, min(ec if i == el else len(seg[i]), len(seg[i]))):
                    seg[i][c] = " "
        out.append(" ".join("".join("".join(seg[i]) for i in sorted(seg)).split()))
    return "\n".join(out)


def turn_message_sha() -> str:
    from ck3bench.harness import runner
    path, names = TURN_MESSAGE
    text = code_text((ROOT / path).read_text(encoding="utf-8"), names) + f"\nTOOL_PREFIX={runner.TOOL_PREFIX}"
    return hashlib.sha256(text.encode()).hexdigest()


def bridge_source_sha(checkout) -> str | None:
    """The members digest of a toolkit checkout's BRIDGE_SOURCE files, or None when it has none of them."""
    from ck3bench.env import toolkit_id
    root = Path(checkout)
    files = {}
    for top in BRIDGE_SOURCE:
        p = root / top
        for f in ([p] if p.is_file() else sorted(p.rglob("*")) if p.is_dir() else []):
            if f.is_file() and "__pycache__" not in f.parts:
                files[f.relative_to(root).as_posix()] = hashlib.sha256(f.read_bytes()).hexdigest()
    return toolkit_id.digest(files) if files else None


def toolkit_members() -> str | None:
    """The installed toolkit's members digest, else the one TOOLKIT_BUILD.md states for the pin (when the repository
    ships one), else None."""
    from ck3bench.env import toolkit_id
    got = toolkit_id.identity().get("installed_members_sha256")
    if got:
        return got
    note = ROOT / "TOOLKIT_BUILD.md"
    m = HEX64.search(note.read_text(encoding="utf-8")) if note.is_file() else None
    return m.group(0) if m else None


def pyproject_toolkit_version() -> str:
    deps = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["dependencies"]
    (pin,) = [d.strip() for d in deps if d.strip().startswith("ck3agent")]
    return pin.split("==", 1)[1]


def current() -> dict:
    """{pin key: the value this environment reads}, for every pin it can read."""
    sc = resolve(TASK)
    out = {"bench.goal_line_module": hashlib.sha256((ROOT / "ck3bench" / "env" / "goal_line.py").read_bytes()).hexdigest(),
           f"task.{TASK}.seat_prompt_sha256": hashlib.sha256(P.render_prompt(sc, sc.n_steps).encode()).hexdigest(),
           "bench.runner_turn_message": turn_message_sha(), "toolkit.version": pyproject_toolkit_version()}
    for key, path, names in SEGMENTS:
        out[f"bench.{key}"] = hashlib.sha256(segment((ROOT / path).read_text(encoding="utf-8"), names).encode()).hexdigest()
    members = toolkit_members()
    if members is not None:
        out["toolkit.members_sha256"] = members
    if toolkit_source.notes_file() is not None:
        nm = _notes_module()
        out[f"task.{TASK}.notes_version"] = nm.version(nm.derive(nm.load(toolkit_source.notes_file()), sc.notes_set))
    d = toolkit_source.server_dir()
    if d is not None:
        out["toolkit.reply_keys_sha256"] = toolkit_source.reply_keys_digest(toolkit_source.reply_keys(d))
    for key, sw in (("toolkit.tool_listing_sha256", {}), ("toolkit.tool_listing_army_switches_sha256", ARMY_SWITCHES)):
        got = listing_sha(sw)
        if got is not None:
            out[key] = got
    checkout = os.environ.get("CK3BENCH_TOOLKIT_CHECKOUT")
    if checkout:
        got = bridge_source_sha(checkout)
        assert got, f"CK3BENCH_TOOLKIT_CHECKOUT={checkout} holds none of {BRIDGE_SOURCE}"
        out["toolkit.bridge_source_sha256"] = got
    return out


def problems(pinned: dict, now: dict) -> list[str]:
    """Why the pin file misstates the build `now` reads ([] when it does not): a value that differs, an ALWAYS pin
    missing from the file, and a pin no reader here knows."""
    out = [f"{k}: the code gives {now[k]}, BUILD_PINS.toml says {pinned.get(k)}" for k in sorted(now)
           if k in pinned and pinned[k] != now[k]]
    out += [f"{k}: not pinned in BUILD_PINS.toml" for k in ALWAYS if k not in pinned]
    out += [f"{k}: pinned and read by nothing here" for k in sorted(pinned) if k not in ALWAYS + TOOLKIT_READ]
    return out


def test_every_pin_this_environment_reads_is_the_code_s():
    now = current()
    assert set(ALWAYS) <= set(now)
    assert problems(pins(), now) == []


def test_with_the_toolkit_installed_every_toolkit_pin_is_read_and_checked():
    """With the toolkit installed, its pins are read, not skipped: the members digest, the reply keys and the notes
    version, and both tool listings when its server imports here (the mcp library installed); every one is then held
    to BUILD_PINS.toml by the test above. Without the toolkit this has nothing to read."""
    if importlib.util.find_spec("ck3agent") is None:
        pytest.skip("the toolkit (ck3agent) is not installed: its pins are read only from an install")
    now = current()
    want = ["toolkit.members_sha256", "toolkit.reply_keys_sha256", f"task.{TASK}.notes_version"]
    if toolkit_source.server_importable()[0] is not None:
        want += ["toolkit.tool_listing_sha256", "toolkit.tool_listing_army_switches_sha256"]
    assert [k for k in want if k not in now] == [], "a toolkit pin went unread with the toolkit installed"
    assert problems(pins(), {k: now[k] for k in want}) == []


def test_the_pinned_toolkit_version_is_the_one_pyproject_requires():
    assert pins()["toolkit.version"] == pyproject_toolkit_version()


def test_every_toolkit_pin_is_present_and_of_its_form():
    """The toolkit pins are in the file whether or not this environment can read them (a user without the toolkit
    still reads which build the bench names)."""
    p = pins()
    assert all(k in p for k in TOOLKIT_READ), [k for k in TOOLKIT_READ if k not in p]
    for k in TOOLKIT_READ:
        assert re.fullmatch(r"[0-9a-f]{12}" if k.endswith("notes_version") else r"[0-9a-f]{64}", p[k]), k


def test_CONTROL_a_changed_missing_or_unread_pin_is_found():
    """A pin one character off reds by name; an ALWAYS pin dropped from the file reds; a pin no reader knows reds.
    The real file passes against its own values (the non-discriminating half)."""
    now, pinned = current(), pins()
    for k in now:
        bad = dict(pinned, **{k: "0" * len(str(pinned.get(k, "x")))})
        assert any(p.startswith(f"{k}: the code gives") for p in problems(bad, now)), k
    for k in ALWAYS:
        assert f"{k}: not pinned in BUILD_PINS.toml" in problems({x: v for x, v in pinned.items() if x != k}, now)
    assert any("read by nothing here" in p for p in problems(dict(pinned, **{"bench.extra": "x"}), now))
    assert problems({**pinned, **now}, now) == []


def restated(text: str, pinned: dict) -> list[str]:
    """The pin values (digests of 12 or more hex digits) a document restates by hand."""
    return sorted(k for k, v in pinned.items() if isinstance(v, str) and re.fullmatch(r"[0-9a-f]{12,}", v)
                  and v[:12] in text)


def test_the_docs_link_the_pin_file_and_restate_no_pin():
    pinned = pins()
    for rel in DOCS:
        text = (ROOT / rel).read_text(encoding="utf-8")
        assert "BUILD_PINS.toml" in text, f"{rel} does not name BUILD_PINS.toml"
        assert restated(text, pinned) == [], f"{rel} restates pins by hand: {restated(text, pinned)}"


def test_CONTROL_a_pin_restated_by_hand_is_found():
    pinned = pins()
    k = f"task.{TASK}.seat_prompt_sha256"
    assert restated(f"the prompt's sha256 is {pinned[k]}", pinned) == [k]
    assert restated("see BUILD_PINS.toml", pinned) == []


def test_CONTROL_code_text_reads_code_not_comments():
    """code_text: a comment, a docstring or a re-wrapped line does not change it; a changed string does."""
    a = 'class Run:\n    def f(self, k):\n        """doc."""\n        return f"Turn {k}"  # note\n'
    b = 'class Run:\n    def f(self,\n          k):\n        """another doc."""\n        # a comment\n        return f"Turn {k}"\n'
    c = 'class Run:\n    def f(self, k):\n        return f"Step {k}"\n'
    assert code_text(a, ("Run.f",)) == code_text(b, ("Run.f",)) != code_text(c, ("Run.f",))
