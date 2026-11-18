"""ck3bench command line: one subcommand per step of a registered study.

This version ships the names and `experiment check`. Every other subcommand
refuses with `not_implemented`, naming the milestone that brings it, so a
caller learns the shape of the tool now and never a silent no-op.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .experiment import ExperimentRefusal, check_block, check_report, load_experiment, to_conditions_block

EXIT_OK = 0
EXIT_REFUSED = 1
EXIT_NOT_IMPLEMENTED = 2

#: The milestones that bring the unimplemented subcommands, in order.
MILESTONES: tuple[str, ...] = ("runner", "collect", "report/verify")

#: subcommand -> (help, the milestone that brings it).
UNIMPLEMENTED: dict[str, tuple[str, str]] = {
    "census": ("run the kingdom census on the start save", "runner"),
    "freeze": ("freeze the registration: plan, fragments, conditions", "runner"),
    "pilot": ("replay a pilot experiment (scale0, budget0)", "runner"),
    "collect": ("collect a cell or pilot directory into an artifact", "collect"),
    "report": ("report a cell from its artifact", "report/verify"),
    "verify": ("verify a bundle or an artifact", "report/verify"),
}

R_NOT_IMPLEMENTED = "not_implemented"


def _refuse_not_implemented(name: str) -> int:
    _, milestone = UNIMPLEMENTED[name]
    print(json.dumps({"ok": False, "reason": R_NOT_IMPLEMENTED,
                      "subcommand": name, "milestone": milestone}))
    return EXIT_NOT_IMPLEMENTED


RUN_HELP = "run one cell of an experiment on this host"


def cmd_run(a: argparse.Namespace) -> int:
    """The cell runner (`ck3bench.runner.cell`) drives a research checkout and
    is not part of this distribution; without it `run` refuses by name."""
    try:
        from .runner import cell
    except ImportError:
        print(json.dumps({"ok": False, "reason": R_NOT_IMPLEMENTED, "subcommand": "run",
                          "milestone": "runner"}))
        return EXIT_NOT_IMPLEMENTED
    return cell.main(a)


def _run_arguments(s: argparse.ArgumentParser) -> None:
    try:
        from .runner import cell
    except ImportError:
        s.add_argument("args", nargs=argparse.REMAINDER)
        return
    cell.add_arguments(s)


def cmd_experiment_check(a: argparse.Namespace) -> int:
    try:
        exp = load_experiment(a.experiment, overlay=a.overlay)
        if a.block is not None:
            text = Path(a.block).read_text(encoding="utf-8")
            check_block(exp, text)
        report = check_report(exp, tree_root=Path(a.tree))
        if a.block is not None:
            report["mirror"] = {"document": str(a.block), "verdict": "agreed"}
        if a.print_block:
            sys.stdout.write(to_conditions_block(exp))
            return EXIT_OK
    except ExperimentRefusal as exc:
        row = {"ok": False}
        row.update(exc.as_dict())
        print(json.dumps(row, default=str))
        return EXIT_REFUSED
    print(json.dumps(report, indent=2))
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="ck3bench",
                                 description="The research harness runner over ck3agent.")
    ap.add_argument("--version", action="version", version=f"ck3bench {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, (help_, milestone) in UNIMPLEMENTED.items():
        s = sub.add_parser(name, help=f"{help_} [milestone {milestone}: not in this version]")
        s.add_argument("args", nargs=argparse.REMAINDER)
        s.set_defaults(fn=lambda a, _n=name: _refuse_not_implemented(_n))
    r = sub.add_parser("run", help=RUN_HELP)
    _run_arguments(r)
    r.set_defaults(fn=cmd_run)
    e = sub.add_parser("experiment", help="experiment-file commands")
    esub = e.add_subparsers(dest="ecmd", required=True)
    c = esub.add_parser("check", help="load an experiment TOML, generate its conditions block, "
                                      "and compare it with a document's frozen block")
    c.add_argument("experiment", help="the tracked <name>.toml")
    c.add_argument("--overlay", default=None,
                   help="an explicit <name>.local.toml (default: the one beside the experiment, if any)")
    c.add_argument("--block", default=None,
                   help="a document whose ```registered-conditions fence must agree with the TOML")
    c.add_argument("--tree", default=".",
                   help="the tree a relative [registration].document is looked for in "
                        "(default: the current directory); absent is a state, never an error")
    c.add_argument("--print-block", action="store_true",
                   help="print the generated block and nothing else")
    c.set_defaults(fn=cmd_experiment_check)
    return ap


def main(argv: list[str] | None = None) -> int:
    a = build_parser().parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
