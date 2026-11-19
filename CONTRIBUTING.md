# Contributing

## 1. How a pull request lands

This repository is generated from the maintainer's development tree (`UPSTREAM`
names the commit). A merged pull request is carried into that tree, and the
next sync commit here names it and credits you with `Co-authored-by:`. Merges
here are merge commits, never squashes. A pull request containing a commit
authored as the release bot is not merged.

## 2. Dev setup

```bash
python3 -m venv .venv && . .venv/bin/activate
pin=$(python -c 'import tomllib,re; d=tomllib.load(open("pyproject.toml","rb"))["project"]["dependencies"]; print([re.match(r"ck3agent==(\S+)$", x).group(1) for x in d if x.startswith("ck3agent")][0])')
pip install "https://github.com/skullbloc/ck3agent/releases/download/v$pin/ck3agent-$pin-py3-none-any.whl"
pip install -e ".[test]"
python -m pytest
```

Python 3.11 to 3.14. No game is needed for the tests or the fake backend; the real backend needs
your own copy of the game (README, "Running on the real game").

## 3. Tests

* Tests sit beside the module they test (`ck3bench/test_*.py`) and in `tests/`.
* A new refusal code needs a row in the table of `docs/reference.md`
  (`tests/test_readme.py` enumerates them from the code).
* No file may name a machine, a home directory or a personal email address,
  or carry a work label or an unfinished-work marker (`tests/test_public_tree.py`).

## 4. Tasks, rewards and the trajectory format

A task is one declaration (`ck3bench/env/scenario.py`, or a TOML file read by
the same reader) and nothing defaults. A change to a task, a reward term, the
trajectory format or the baseline file changes what an existing policy or
reader sees: say what in the PR, and keep the README's task and baseline tables
and the reference's code table in step (`tests/test_readme.py` checks all three).
Nothing measured is filled in: a value the game or the baseline did not read
is `null` beside a status.

## 5. Style

Match the surrounding code. One concern per PR. No reformat-only or
rename-only PRs.

## 6. Review process

Add a line under the next version's heading in `CHANGELOG.md`; skip it for
docs-only and test-only changes. CI must be green. CODEOWNERS routes the
review; a maintainer merges here. First response within 7 days. No reply after
14: ping on the PR.

## 7. Credit and licensing

Sign off every commit with `git commit -s` to certify the
[DCO](https://developercertificate.org/); the sync does not port or credit a
commit without a `Signed-off-by:` naming its author. Credit is on by default;
say so in the PR if you want none. Licensed Apache-2.0
([LICENSE](LICENSE), [NOTICE](NOTICE)).

## 8. Conduct and security

[CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md). Vulnerabilities go in a private
advisory per [SECURITY.md](SECURITY.md), never a public issue.
