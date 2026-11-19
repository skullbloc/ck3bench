"""The `ck3bench` command.

    ck3bench env ...          the environment (ck3bench.env.cli): tasks, run an
                              episode, serve, check a trajectory, baselines

An optional module `ck3bench.extra_cli` (`SUBCOMMANDS` and
`add_commands(subparsers)`), when installed beside this one, adds its
subcommands; none ships with this package, so the command is `env`. An
`extra_cli` that is present and fails to import is an error, never a smaller
command.
"""
from __future__ import annotations

import argparse
import sys

from . import __version__

EXIT_OK = 0

try:
    from .extra_cli import SUBCOMMANDS as _EXTRA, add_commands as _add_extra
except ModuleNotFoundError as _e:
    if _e.name != f"{__package__}.extra_cli":
        raise
    _EXTRA, _add_extra = (), None

#: The subcommands registered in this installation.
SUBCOMMANDS: tuple[str, ...] = ("env", *_EXTRA)


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="ck3bench",
                                 description="Crusader Kings III as an environment for LLM agents.")
    ap.add_argument("--version", action="version", version=f"ck3bench {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)
    from .env import cli as env_cli
    v = sub.add_parser("env", help="the environment: tasks, run an episode, serve, check a trajectory, baselines")
    env_cli.add_arguments(v)
    v.set_defaults(fn=lambda a: a.env_fn(a))
    if _add_extra is not None:
        _add_extra(sub)
    return ap


def main(argv: list[str] | None = None) -> int:
    a = build_parser().parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
