"""python -m ck3bench.harness VERB ...: the research harness's entry point (docs/harness.md).

    run-seat ARGS        one seated episode (ck3bench.harness.runner; --policy noop: the game's AI alone)
    prompt TASK          print the seat prompt the runner would send for TASK (--notes-pad-bytes N)
    score RUN_DIR ...    the success rate of the runs, with the validity, resource and stopping rules (--json)
    series STEP ...      a series run's identity, witness and result steps (switches, plan, declare, slot, start,
                         watch, end, result: ck3bench.harness.series); `series run`: the reference driver, one
                         attempt through every step in order (ck3bench.harness.driver); `series publish`: a
                         run directory's publishable copy (ck3bench.harness.publish)
    accounting RUN_DIR   per-turn tokens, calls and the sessions' reported cost (RUN_DIR/accounting.json)
    analyze RUN_DIR      turn by turn: calls, reply sizes and errors, the seat's own text (RUN_DIR/analysis.json)
    steps TRAJECTORY     one line per env step
    game-pid RUN_DIR     the run's game pid from what the run recorded (never a process name)
"""
from __future__ import annotations

import argparse
import sys


def _prompt(argv: list[str]) -> int:
    from ck3bench.env.scenario import resolve
    from ck3bench.harness import prompt
    p = argparse.ArgumentParser(prog="python -m ck3bench.harness prompt")
    p.add_argument("task")
    p.add_argument("--notes-pad-bytes", type=int, default=prompt.NOTES_PAD_BYTES)
    a = p.parse_args(argv)
    sc = resolve(a.task)
    sys.stdout.write(prompt.render_prompt(sc, sc.n_steps, a.notes_pad_bytes))
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0 if argv else 2
    verb, rest = argv[0], argv[1:]
    if verb == "run-seat":
        from ck3bench.harness import runner
        return runner.main(rest)
    if verb == "prompt":
        return _prompt(rest)
    if verb == "score":
        from ck3bench.harness import score
        return score.main(rest)
    if verb == "series":
        from ck3bench.harness import series
        return series.main(rest)
    if verb in ("accounting", "analyze", "steps", "game-pid"):
        import importlib
        mod = importlib.import_module("ck3bench.harness." + {"game-pid": "game_identity"}.get(verb, verb))
        if not rest or rest[0] in ("-h", "--help"):
            # a reader's help is its module's statement; no argument is a usage error
            print(mod.__doc__)
            return 0 if rest else 2
    if verb == "accounting":
        return mod.main(rest[0])
    if verb == "analyze":
        return mod.main(rest[0])
    if verb == "steps":
        sys.argv = ["steps", *rest]
        return mod.main()
    if verb == "game-pid":
        from pathlib import Path
        pid, why = mod.owned(Path(rest[0]))
        print(pid if pid is not None else f"REFUSED {why}")
        return 0 if pid is not None else 1
    print(f"unknown verb {verb!r}\n{__doc__}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
