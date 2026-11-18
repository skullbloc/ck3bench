# Contributing

## 1. Where this repo comes from

This repository is exported from a research tree; `UPSTREAM` names the source
commit. Your merged PR is carried upstream by the maintainer (`ported`),
adapted (`superseded`) or, rarely, dropped with a reason (`reverted`); the next
export commit names each PR in its trailers and credits you with
`Co-authored-by:`. The sync tooling identifies the release bot by name and
email, which anyone can type, so a PR containing a commit authored as the bot
is not merged. Merges here are merge commits, never squashes.

## 2. Dev setup

```bash
python3 -m venv .venv && . .venv/bin/activate
pin=$(python -c 'import tomllib,re; d=tomllib.load(open("pyproject.toml","rb"))["project"]["dependencies"]; print([re.match(r"ck3agent==(\S+)$", x).group(1) for x in d if x.startswith("ck3agent")][0])')
pip install "https://github.com/skullbloc/ck3agent/releases/download/v$pin/ck3agent-$pin-py3-none-any.whl"
pip install -e ".[test]"
python -m pytest
```

Python 3.11 to 3.14. No game is needed for anything in this repository.

## 3. Tests

* Tests sit beside the module they test (`ck3bench/test_*.py`) and in `tests/`.
* `pyproject.toml` deselects the upstream test ids that pin research-tree
  facts; each one is listed, with its reason, in a comment above `addopts`.
* A new refusal code needs a row in the README's refusal table
  (`tests/test_readme.py` enumerates them from the code).
* No file may name a machine, a home directory or a personal email address
  (`tests/test_public_tree.py`).

## 4. Experiment files

An experiment is one TOML file and nothing defaults. Change the schema in
`ck3bench/experiment.py` and the examples together, and paste the conditions
block before and after (`ck3bench experiment check <file> --print-block`) in
the PR. Host-specific values go in an untracked `<name>.local.toml`, never in a
tracked file.

## 5. Style

Match the surrounding code. One concern per PR. No reformat-only or
rename-only PRs: the export overwrites files shared with upstream, and the
churn is lost.

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
