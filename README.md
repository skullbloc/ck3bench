# ck3bench

The research harness over [ck3agent](https://github.com/skullbloc/ck3agent),
the toolkit that lets Claude play Crusader Kings III by steering the game's own
AI. ck3agent plays; ck3bench declares the experiments. An experiment is one
TOML file, every key typed and named in a schema, nothing defaulted; the
pre-registration's conditions block is generated from that file and checked
against the frozen document key by key.

## Status

**A research harness, and the experiment it was built for has not been run.**

* What this version does is check experiment files (`ck3bench experiment check`).
  The subcommands that would run a study exist and refuse `not_implemented`,
  naming the milestone that brings them ([below](#not-yet-implemented)).
* The organisation-design study (`orgdesign-v4.toml`) is declared here, but its
  pre-registration is **not signed**: the file's `[pending]` table lists the
  lines it still owes (the horizon's end and length, the decision cycle, the
  budget ceiling, the run-order seed), and no result of that study exists.
* The pilot files record pilots that did run (`scale0.toml`, `budget0.toml`, and
  the generated v4 pilot variants). Their result tables are not published here.

## What it needs

* To check experiment files (everything this version does): Linux or another
  POSIX system with Python 3.11 to 3.14, and the ck3agent wheel at the pinned
  version. No game, no model account, no cost.
* To run a study (not in this version): everything ck3agent needs, namely your
  own copy of Crusader Kings III at the build ck3agent supports (its
  `docs/supported-versions.md`), and Claude, through a Claude subscription or
  an Anthropic API key. Seats are billed by the model provider: the pilots
  declared caps of USD 40 (`scale0.toml`) and USD 230 (`budget0.toml`), and a
  study multiplies seats, game-years and episodes, so expect costs of that
  order or more per arm. ck3bench ships no game files and no keys.

## Install, and check the examples

ck3bench depends on the ck3agent wheel at one exact version (`pyproject.toml`),
a ck3agent release asset; install it first:

```bash
pip install https://github.com/skullbloc/ck3agent/releases/download/v2026.12.10/ck3agent-2026.12.10-py3-none-any.whl
pip install -e ".[test]"
for e in scale0 budget0 orgdesign-v4; do ck3bench experiment check "ck3bench/experiments/$e.toml"; done
ck3bench experiment check ck3bench/experiments/scale0.toml --print-block      # the generated conditions block
ck3bench experiment check ck3bench/experiments/scale0.toml --block FROZEN.md  # agree with a frozen document, or refuse by key
python -m pytest                                                             # the package tests and tests/
```

## The experiment files

* `ck3bench/experiment.py`: the schema and its three readers (the loader, the
  block generator, the mirror check against a frozen document).
* `ck3bench/experiments/`, the examples:
  * `orgdesign-v4.toml`: the organisation-design study. Three arms on the same
    start (k_france at the 1066.9.15 bookmark): E0, the game's own AI; K1, an
    Opus king alone with a private notepad; COUNCIL, an Opus king and five
    Sonnet dukes with private notepads, a council notepad and messages. The
    lines its registration still owes are declared in `[pending]` and rendered
    as placeholders the launcher refuses.
  * the generated pilot variants, each the study's file with its pilot lines
    written and labelled `PILOT`: `orgdesign-v4-pilot.toml` (one unsteered
    150-game-year episode), `orgdesign-v4-pilot25.toml` (one K1 and one COUNCIL
    episode, 25 game-years) and `orgdesign-v4-pilot-e0var100.toml` (the
    unsteered variance set, 100 game-years). A pilot enters no test of the study.
  * `scale0.toml` (seats per game, 2 to 8 concurrent seats on one game) and
    `budget0.toml` (per-seat cost over three game-years, Sonnet then Opus): the
    two earlier pilots, run under no registration. Their `[episode]` and
    `[budget]` keys are declared for the block, never armed; each file says why.
* `ck3bench/cli.py`: the `ck3bench` command.

Host-specific runner values go in an untracked `<name>.local.toml` beside the
experiment (its only table is `[runner]`); a tracked experiment file that
carries one, or any path under `/home`, `/mnt` or `/tmp`, is refused. The
overlay may also declare `hosts_forbidden = ["<hostname>", ...]`: names of your
own machines and accounts that a tracked experiment file must not carry.

## What `experiment check` reports

It prints what the file declares and, beside it, what it observed:

* `toolkit`: the declared ck3agent version and the installed distribution
  (`installed_matches`, `installed_differs` or `not_installed`; a witness, not
  a refusal);
* `registration`: the declared document looked for under `--tree` (default the
  current directory): `present_sha_agrees`, `present_sha_not_declared`,
  `registration_document_not_shipped` or `not_declared`, with the observed
  sha256 or `unwitnessed`;
* `pending`: the lines the registration still owes;
* `block_keys`, and `block_keys_not_emitted` (a declaration the block has no
  line for, reported rather than dropped);
* with `--block`, the mirror verdict.

## Refusals

Nothing defaults. Every refusal is typed and names its leg: the JSON row
carries `reason`, `key`, `path` and `detail`, and the command exits 1. The
vocabulary (`ck3bench/experiment.py` `R_*`; `tests/test_readme.py` keeps this
list complete):

| `reason` | when |
|---|---|
| `file_missing`, `file_unreadable`, `toml_invalid` | the file itself cannot be read as TOML |
| `unknown_key` | a key or section the schema does not name: an experiment may not declare what nothing reads |
| `missing_key` | a required key or section is absent, or one a declared value needs (`seats.ids` under `source = "list"`, the census keys under `"census"`, `[channel]` and `[guide]` under `registration.version = "4"`, `registration.document` beside a `sha256`, a census id without the others) |
| `wrong_type` | the value's type is not the schema's (`1` where `1.0` is required; a boolean where an integer is) |
| `invalid_value` | a value of the right type the schema cannot accept (a date, a sha256, a choice, a tool name, a cell whose role is not in `[seats.model_id]`, a declared `registration.sha256` that disagrees with the document present) |
| `host_path_in_tracked_file` | a path under `/home`, `/mnt` or `/tmp`, or a name from the overlay's `hosts_forbidden`, anywhere in the tracked file's bytes, comments included |
| `host_key_in_tracked_file` | a `[runner]` host key (`lock`, `display`, `xauthority_glob`, `ollama_unit`, `saves_dir`, `tarball`, `root`, `game_install`, `hosts_forbidden`) in the tracked file |
| `overlay_key_not_host` | an overlay (`<name>.local.toml`) carrying a table other than `[runner]` or a key that is not a host key |
| `block_disagrees` | `--block`: a key the document's fence and the TOML read differently, listed one row per key (`missing_in_block`, `extra_in_block`, `value_differs`, `list_differs`) |
| `no_conditions_block`, `multiple_conditions_blocks`, `malformed_line`, `duplicate_key` | the document's ```` ```registered-conditions ```` fence: none, two, a line that is not `KEY=VALUE`, a scalar declared twice |
| `template_not_frozen` | the document carries `TEMPLATE - NOT FROZEN`: a template is not a pre-registration |
| `not_implemented` | a subcommand this version does not bring (exit 2; the row names the milestone) |

## Not yet implemented

Every subcommand below exists and refuses `not_implemented`, naming the
milestone that brings it:

| Subcommand | What it will do | Milestone |
|---|---|---|
| `census`, `freeze`, `pilot`, `run` | the kingdom census on the start save; freeze the registration (plan, fragments, conditions); replay a pilot experiment; run one cell of an experiment | `runner` (control: the scale0 and budget0 tables reproduced byte for byte from the pilots' raw records) |
| `collect` | collect a cell or pilot directory into an artifact | `collect` |
| `report`, `verify` | report a cell from its artifact; verify a bundle or an artifact | `report/verify` |

## References that live in the research tree

This repository is exported from the research checkout the study is run from.
A few values the experiment files declare are paths in that checkout, not in
this repository (`tests/test_readme.py` checks that every such path a shipped
file names is listed here):

* `docs/pre-registration-v4-orgdesign.md` (`orgdesign-v4.toml`
  `[registration].document`): the pre-registration, written when it is signed.
  `experiment check` reports it as `registration_document_not_shipped` under a
  tree without it. The conditions block the TOML generates is the
  registration's machine-readable part, and a frozen copy of it is what
  `--block` checks.
* `docs/cleanup-2026.11/evidence/v4pilot/census/census-lines.txt`
  (`[seats].census_freeze`, pinned by `census_freeze_sha256`): the census lines
  of k_france, written by the census run on the 1066.9.15 start save; the cell
  runner re-runs the census live and refuses a difference.
* `docs/reg.md`: a placeholder document name in a test.
* `release/export.py`: the export that writes this repository and its
  `UPSTREAM` record.
* Paths under `ck3agent/` name the [ck3agent](https://github.com/skullbloc/ck3agent)
  package or the files it writes at run time (its API key file, its logs), not
  this repository.

## Contributing and security

See [CONTRIBUTING.md](CONTRIBUTING.md) (this repository is generated by an
export; how a pull request is carried upstream), [SECURITY.md](SECURITY.md)
and [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).

## License

[Apache-2.0](LICENSE), like ck3agent; attribution in [NOTICE](NOTICE); cite
with [CITATION.cff](CITATION.cff). Crusader Kings and Paradox Interactive are
registered trademarks of Paradox Interactive AB; Claude is a trademark of
Anthropic, PBC. ck3bench is not affiliated with or endorsed by either.

## Versioning

Calendar versions like ck3agent (`YYYY.MM.PATCH`); `2026.11.0-dev0` until the
first one is cut. `VERSION`, `ck3bench/_version.py` and `CHANGELOG.md` agree
(`tests/test_release_metadata.py`); `UPSTREAM` records the research commit this
tree was exported from. Changes are listed in [CHANGELOG.md](CHANGELOG.md).
