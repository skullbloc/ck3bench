"""`--help` on every command the package offers answers at once, exits 0 and touches nothing.

The commands are enumerated from the code, never from a list here: the harness's verbs from its entry point's
statement (ck3bench.harness.__main__), the series steps from ck3bench.harness.series.STEPS (itself checked against
the steps that module's statement lists), and the `ck3bench` command's subcommands from its argparse parser, walked
to every leaf. Each runs in an empty directory with RATE_RUNS, RATE_LEDGER and the scorer's override log pointed into
it: a help that reads a run, starts a watch or writes a file would leave something there, or time out.
"""
import argparse
import re
import subprocess
import sys
import time

import pytest

from ck3bench.harness import __main__ as M
from ck3bench.harness import series

LIMIT_S = 30


def harness_verbs() -> list[str]:
    return [m.group(1) for ln in M.__doc__.splitlines()[1:] if (m := re.match(r"^\s*([a-z][a-z-]+) +[A-Z][A-Z_]*\b", ln))]


def series_steps_in_statement() -> list[str]:
    return [m.group(1) for ln in series.__doc__.splitlines() if (m := re.match(r"^    series ([a-z]+)\b", ln))]


def env_commands() -> list[list[str]]:
    from ck3bench.cli import build_parser

    def walk(p: argparse.ArgumentParser, path: list[str]) -> list[list[str]]:
        subs = [a for a in p._actions if isinstance(a, argparse._SubParsersAction)]
        if not subs:
            return [path]
        return [path] + [x for a in subs for name, sp in a.choices.items() for x in walk(sp, path + [name])]
    return walk(build_parser(), [])


def commands() -> list[list[str]]:
    out = [["-m", "ck3bench.harness"]]
    for v in harness_verbs():
        if v == "series":
            out += [["-m", "ck3bench.harness", "series"]]
            out += [["-m", "ck3bench.harness", "series", s] for s in series.STEPS]
        else:
            out.append(["-m", "ck3bench.harness", v])
    out += [["-m", "ck3bench.cli", *c] for c in env_commands()]
    return out


def test_the_enumeration_sees_every_command():
    assert len(harness_verbs()) >= 8 and {"series", "score", "run-seat", "game-pid"} <= set(harness_verbs())
    assert set(series.STEPS) == set(series_steps_in_statement()), (series.STEPS, series_steps_in_statement())
    envs = {" ".join(c) for c in env_commands()}
    assert {"env", "env scenarios", "env play", "env shim", "env baseline"} <= envs, envs


@pytest.mark.parametrize("cmd", commands(), ids=lambda c: " ".join(c[1:]))
@pytest.mark.parametrize("flag", ["--help", "-h"])
def test_help_answers_quickly_with_no_side_effect(cmd, flag, tmp_path):
    env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path / "home"), "RATE_RUNS": str(tmp_path / "runs"),
           "RATE_LEDGER": str(tmp_path / "ledger"), "SCORE_RATE_OVERRIDE_LOG": str(tmp_path / "override.log"),
           "PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": ":".join(sys.path)}
    work = tmp_path / "cwd"
    work.mkdir()
    t = time.monotonic()
    r = subprocess.run([sys.executable, *cmd, flag], cwd=work, env=env, capture_output=True, text=True,
                       timeout=LIMIT_S, stdin=subprocess.DEVNULL)
    assert r.returncode == 0, (cmd, r.returncode, r.stdout[-400:], r.stderr[-400:])
    assert time.monotonic() - t < LIMIT_S
    assert "ck3bench" in r.stdout + r.stderr and "Traceback" not in r.stderr, (cmd, r.stderr[-400:])
    assert sorted(p.name for p in tmp_path.iterdir()) == ["cwd"] and list(work.iterdir()) == [], \
        (cmd, sorted(str(p) for p in tmp_path.rglob("*")))
