"""The README and docs/reference.md are checked against the code they describe.

* every refusal and episode-end code the package can emit is named in the
  reference's table (enumerated from the modules, so a code added without a
  row reds here; an empty enumeration is a red too), and every code the table
  names is one the code has;
* every built-in task is in the README's task table with its declared status;
* the baseline table agrees with the shipped baseline file;
* the quickstart's `ck3bench env` lines and the ten-line API block run;
* every repo-relative path a shipped file names exists here (a `ck3agent/`
  path names the toolkit package this one depends on).
"""
import importlib
import pkgutil
import re
import shlex
from pathlib import Path

import ck3bench.env as env_pkg
from ck3bench import cli
from ck3bench.env import baselines
from ck3bench.env.scenario import BUILTIN_DICTS, builtin
from ck3bench.env.trajectory import check_trajectory, read_trajectory

ROOT = Path(__file__).resolve().parents[1]
#: refusals (R_), episode ends (TRUNC_, TERM_), turn ends (T_), message and notepad verdicts (V_)
CODE_NAME = re.compile(r"^(R|TRUNC|TERM|T|V)_[A-Z0-9_]+$|_ERR_[A-Z0-9_]+$")


def _readme() -> str:
    return (ROOT / "README.md").read_text(encoding="utf-8")


def _reference() -> str:
    return (ROOT / "docs" / "reference.md").read_text(encoding="utf-8")


def _section(text: str, heading: str) -> str:
    start = text.index(heading)
    end = text.find("\n## ", start + 1)
    return text[start:] if end < 0 else text[start:end]


def _fence(section: str, lang: str) -> str:
    m = re.search(rf"```{lang}\n(.*?)```", section, re.S)
    assert m, f"no ```{lang} block"
    return m.group(1)


def _codes() -> dict[str, str]:
    """{code: module} over every shipped module of ck3bench.env."""
    out: dict[str, str] = {}
    for info in pkgutil.iter_modules(env_pkg.__path__):
        if info.name.startswith("test_"):
            continue
        mod = importlib.import_module(f"ck3bench.env.{info.name}")
        for k, v in vars(mod).items():
            if CODE_NAME.search(k) and isinstance(v, str):
                out.setdefault(v, info.name)
    return out


def test_the_reference_names_every_code():
    codes = _codes()
    assert len(codes) >= 70, codes
    text = _section(_reference(), "## Refusals and end codes")
    missing = sorted(f"{c} ({m})" for c, m in codes.items() if f"`{c}`" not in text)
    assert missing == [], missing


def test_every_code_the_reference_tables_is_one_the_code_has():
    """The reverse: a code the table names that no module defines is a stale row."""
    codes = _codes()
    table = _section(_reference(), "## Refusals and end codes")
    rows = [ln for ln in table.splitlines() if ln.startswith("| ") and not ln.startswith("| leg")]
    named = {c for ln in rows for c in re.findall(r"`([a-z_0-9]+)`", ln.split("|")[2])}
    assert len(named) >= 70
    assert sorted(named - set(codes)) == []


def test_the_external_seat_commands_parse():
    """The Claude Code route's two commands (the play line and the shim's MCP
    config) are accepted by the CLI as written."""
    import json
    sec = _section(_readme(), "## Several seats, agents and the runner")
    play = [shlex.split(ln.strip()) for ln in _fence(sec, "bash").splitlines()
            if ln.strip().startswith("ck3bench env play")]
    cfg = json.loads(_fence(sec, "json"))
    shim = next(iter(cfg["mcpServers"].values()))
    assert play and shim["command"] == "ck3bench"
    p = cli.build_parser()
    a = p.parse_args(play[0][1:])
    assert a.external == ["king"] and a.env_fn.__name__ == "cmd_play"
    b = p.parse_args(shim["args"])
    assert b.env_fn.__name__ == "cmd_shim" and b.seat == "king" and str(a.port) in b.url


def _run_state(text: str) -> list[str]:
    """The run states (scenario.RUN_STATES) a text names."""
    from ck3bench.env.scenario import RUN_STATES
    return [r for r in RUN_STATES if r in text]


def test_every_builtin_task_is_in_the_table_with_its_status():
    """Each row's status is the one its declaration states: the run state in the row's status cell is the one
    in the task's status_reason (one each), and a fake task's row says it runs on the fake backend."""
    table = _section(_readme(), "## Tasks")
    assert len(BUILTIN_DICTS) >= 5
    rows = [ln for ln in table.splitlines() if ln.startswith("| `")]
    assert len(rows) == len(BUILTIN_DICTS), "one row per built-in task, and no row for a task that is not one"
    for name in BUILTIN_DICTS:
        sc = builtin(name)
        row = next((ln for ln in rows if ln.startswith(f"| `{name}` |")), None)
        assert row is not None, name
        status = row.rstrip(" |").split(" | ")[-1]
        if sc.backend == "fake":
            assert "runs (fake backend)" in status, (name, row)
        else:
            declared = _run_state(sc.status_reason)
            assert len(declared) == 1, (name, sc.status_reason)
            assert _run_state(status) == declared, (name, declared, status)
        assert f"{sc.n_steps} x {sc.cycle_days}" in row, (name, row)
        assert (", wake" in row) == (sc.wake is not None), (name, row)


def test_CONTROL_a_wrong_status_in_the_table_is_red():
    """The status check reads the declaration: a row saying another run state than its task's is a mismatch."""
    sc = builtin("france1066-gain-county-5y")
    assert _run_state(sc.status_reason) == ["not run live"]
    assert _run_state("run live with a model") != _run_state(sc.status_reason)


def test_every_save_a_task_opens_is_in_the_starts_table():
    """Starts and saves: every save file a built-in task opens is a row, and a task whose save is pinned by
    sha256 is named in a row that gives the pin's first eight characters."""
    sec = _section(_readme(), "## Starts and saves")
    rows = [ln for ln in sec.splitlines() if ln.startswith("| `")]
    for name in BUILTIN_DICTS:
        sc = builtin(name)
        if sc.start_save is None:
            continue
        mine = [r for r in rows if f"`{sc.start_save}`" in r.split(" | ")[0]
                and (sc.start_save_sha256 is None) == (" | no | " in r)]
        assert mine, (name, sc.start_save, sc.start_save_sha256)
        if sc.start_save_sha256:
            assert any(sc.start_save_sha256[:8] in r for r in mine), (name, sc.start_save_sha256[:8])
            short = name.split("-", 1)
            assert any(f"`{name}`" in r or f"`{short[0]}" in r for r in mine), name


def test_the_baseline_table_is_the_shipped_file():
    sec = _section(_readme(), "## Baselines")
    rows = {ln.split("|")[1].strip(): [c.strip() for c in ln.strip().strip("|").split("|")[1:]]
            for ln in sec.splitlines() if ln.startswith("|")}
    years = [int(y) for y in rows["game-years"]]
    b = baselines.load_baseline()
    assert b.games == 8 and "**eight**" in sec and len(years) >= 4
    for i, y in enumerate(years):
        s = baselines.summarize(b.mark(y).counties)
        assert abs(float(rows["mean"][i]) - s["mean"]) <= 0.05 + 1e-9, (y, rows["mean"][i], s["mean"])
        assert abs(float(rows["sample SD"][i]) - s["sd"]) <= 0.05 + 1e-9, (y, rows["sample SD"][i], s["sd"])
        assert int(rows["games under 10"][i]) == s["under_10"], y


def test_the_quickstart_env_lines_run(tmp_path, capsys, monkeypatch):
    block = _fence(_section(_readme(), "## Quickstart"), "bash")
    lines = [ln.split("#")[0].strip() for ln in block.splitlines()]
    env_lines = [shlex.split(ln) for ln in lines if ln.startswith("ck3bench env ")]
    assert len(env_lines) >= 4, env_lines          # non-vacuous: the block still carries its commands
    monkeypatch.chdir(tmp_path)
    for argv in env_lines:
        rc = cli.main(argv[1:])
        out = capsys.readouterr().out
        assert rc == 0, (argv, out)
    assert check_trajectory(read_trajectory(tmp_path / "traj.jsonl")) == []


def test_the_ten_line_api_block_runs(tmp_path, monkeypatch):
    code = _fence(_section(_readme(), "## The API in ten lines"), "python")
    assert len(code.strip().splitlines()) <= 10
    monkeypatch.chdir(tmp_path)
    exec(compile(code, "README.md", "exec"), {})                       # noqa: S102
    recs = read_trajectory(tmp_path / "t.jsonl")
    assert check_trajectory(recs) == [] and recs[-1]["reason"] == "horizon" and recs[0]["fake"] is True


# A repo-relative path some shipped file names. Not inside a URL or a longer path
# (no `/` or word character before it).
REF = re.compile(r"(?<![\w/.-])((?:docs|scripts|release|ck3agent|ck3bench|tests)/[A-Za-z0-9_.*/-]*[A-Za-z0-9_*/])")
SKIP = {".git", "dist", "build", "__pycache__", ".pytest_cache", ".venv"}


def _named_paths() -> dict[str, str]:
    named: dict[str, str] = {}
    for p in sorted(ROOT.rglob("*")):
        rel = p.relative_to(ROOT)
        # UPSTREAM is the build step's provenance record (it names the generator by design)
        if not p.is_file() or rel.as_posix() in (".gitignore", ".ck3bench-export", "UPSTREAM") or any(
                s in SKIP or s.endswith(".egg-info") for s in rel.parts):
            continue
        text = p.read_bytes().decode("utf-8", "replace")
        for m in REF.finditer(text):
            named.setdefault(m.group(1).rstrip("/"), rel.as_posix())
    return named


def _present(path: str) -> bool:
    return any(ROOT.glob(path)) if "*" in path else (ROOT / path).exists()


def test_every_path_a_shipped_file_names_is_here():
    named = _named_paths()
    # non-vacuous: the README names the reference and the baseline file, which are here
    assert {"docs/reference.md", "ck3bench/env/baselines_france1066.json"} <= set(named), sorted(named)
    absent = sorted(f"{f}: {p}" for p, f in named.items() if not _present(p) and not p.startswith("ck3agent/"))
    assert absent == []


def test_the_cli_is_the_environment_alone():
    sub = next(a for a in cli.build_parser()._actions if a.dest == "cmd")
    assert set(sub.choices) == {"env"}
