"""Where the importable toolkit's files are, for the tests that check a mirror against them.

ck3bench depends on ck3agent; a test that compares this package with the toolkit's own code or data (its server's
tool list, its notes file, its argument rule) reads the files of the ck3agent package Python would import, never
a path beside this package. When no toolkit is importable, `server_dir()` is None and such a test skips with the
reason `ABSENT`; it never reads anything else in its place.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Optional

ABSENT = "the toolkit (ck3agent) is not installed here, so its own files cannot be read"
#: the toolkit's server imports the `mcp` library (a dependency of the toolkit): installed without it (a wheel put in
#: with --no-deps, say), its files can be read but its server cannot run, and a test that runs it skips with this
MCP_ABSENT = "the toolkit (ck3agent) is installed without its `mcp` dependency, so its server cannot be imported"


def server_dir() -> Optional[Path]:
    """The `server` directory of the importable ck3agent package (the one holding mcp_server.py), or None."""
    try:
        spec = importlib.util.find_spec("ck3agent")
    except (ImportError, ValueError):
        return None
    for loc in (spec.submodule_search_locations or []) if spec is not None else []:
        d = Path(loc) / "server"
        if (d / "mcp_server.py").is_file():
            return d
    return None


def notes_file() -> Optional[Path]:
    """The toolkit's shared-notes file (server/notes.json), or None when no toolkit is importable."""
    d = server_dir()
    f = d / "notes.json" if d is not None else None
    return f if f is not None and f.is_file() else None


def server_importable() -> tuple[Optional[Path], str]:
    """(the toolkit's server directory, "") when its server can be imported here, else (None, why): ABSENT when no
    toolkit is importable, MCP_ABSENT when it is but its `mcp` dependency is not."""
    d = server_dir()
    if d is None:
        return None, ABSENT
    try:
        mcp = importlib.util.find_spec("mcp")
    except (ImportError, ValueError):
        mcp = None
    if mcp is None:
        return None, MCP_ABSENT
    return d, ""


# ── the keys the toolkit's server can write into a reply ─────────────────────
def emitted_keys(src: str) -> set[str]:
    """The literal string keys a module's code can put into a dict it builds: a dict display's keys, a subscript it
    assigns (`row["k"] = ...`, `row["k"] += ...`), the keywords of `dict(k=...)` and `.update(k=...)`, and
    `.setdefault("k", ...)`. Every such key counts, whether or not that dict reaches a seat's reply. A key spelled
    through a name (`{CALL_ALLY: ...}` with `CALL_ALLY = "..."`) or computed at run time is not read: the set is
    every literal key, not every key."""
    import ast
    out: set[str] = set()
    for n in ast.walk(ast.parse(src)):
        if isinstance(n, ast.Dict):
            out |= {k.value for k in n.keys if isinstance(k, ast.Constant) and isinstance(k.value, str)}
        elif isinstance(n, ast.Subscript) and isinstance(n.ctx, ast.Store):
            if isinstance(n.slice, ast.Constant) and isinstance(n.slice.value, str):
                out.add(n.slice.value)
        elif isinstance(n, ast.Call):
            name = getattr(n.func, "id", None) or getattr(n.func, "attr", None)
            if name in ("dict", "update"):
                out |= {k.arg for k in n.keywords if k.arg}
            if name == "setdefault" and n.args and isinstance(n.args[0], ast.Constant) \
                    and isinstance(n.args[0].value, str):
                out.add(n.args[0].value)
    return out


def _server_module(p: Path) -> bool:
    """A module the toolkit's server runs (its tests and their fixtures' helpers are not)."""
    return p.suffix == ".py" and not p.name.startswith("test_") and p.name != "conftest.py"


def reply_keys(server: Path) -> dict[str, set[str]]:
    """{module name: emitted_keys} for every module of a toolkit `server` directory."""
    return {p.stem: emitted_keys(p.read_text(encoding="utf-8")) for p in sorted(server.glob("*.py"))
            if _server_module(p)}


def reply_keys_digest(keys: dict[str, set[str]]) -> str:
    """sha256 over the sorted lines `<module> <key>`: one number for the whole set."""
    import hashlib
    body = "".join(f"{m} {k}\n" for m in sorted(keys) for k in sorted(keys[m]))
    return hashlib.sha256(body.encode()).hexdigest()
