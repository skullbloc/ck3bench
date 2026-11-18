# Changelog

All notable changes to ck3bench. Versions are calendar-based
(`YYYY.MM.PATCH`), like ck3agent's; the git tag is the version with a `v`
prefix. No version has been released yet.

## 2026.11.0-dev0 — unreleased

The first public tree.

* **The experiment file.** `ck3bench.experiment`: an experiment is one TOML
  file, every key typed and named in a schema, nothing defaulted. The
  registration's conditions block is generated from it
  (`to_conditions_block`) and a frozen document's block is checked against it
  key by key (`check_block`). Host-specific values live only in an untracked
  `<name>.local.toml`; a tracked file naming a host path, a host key or a host
  name the overlay forbids is refused. Lines a registration still owes are
  declared in `[pending]` and rendered as placeholders the launcher refuses.
* **The CLI.** `ck3bench experiment check` loads a file, prints what it
  declares beside what it observed (the installed ck3agent version, the
  registration document under `--tree`), prints the generated block
  (`--print-block`) and runs the mirror check (`--block`). A document marked
  `TEMPLATE - NOT FROZEN` is refused. Every other subcommand (`census`,
  `freeze`, `pilot`, `run`, `collect`, `report`, `verify`) refuses
  `not_implemented`, naming the milestone that brings it.
* **The examples.** `orgdesign-v4.toml`, the organisation-design study (arms
  E0, K1, COUNCIL on k_france at 1066.9.15; pre-registration not signed, six
  lines pending the pilot and the freeze); its generated pilot variants
  `orgdesign-v4-pilot.toml`, `orgdesign-v4-pilot25.toml` and
  `orgdesign-v4-pilot-e0var100.toml`; and the two earlier pilots `scale0.toml`
  and `budget0.toml`, declared as they ran.
* **The toolkit pin.** Depends on ck3agent at exactly `2026.12.10`;
  `orgdesign-v4.toml` declares that release and the sha256 of the four wrapper
  files a seat runs under, read from its wheel.
* **The repository.** Apache-2.0 (`LICENSE`, `NOTICE`, `CITATION.cff`),
  `SECURITY.md`, `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, CI on Python 3.11 to
  3.14 with actions pinned by commit sha, and tests that keep the tree public:
  no host path, personal email address or internal work label in any file
  (`tests/test_public_tree.py`), every relative link and anchor resolves
  (`tests/test_links.py`), and the README lists every refusal code and every
  research-tree path a shipped file names (`tests/test_readme.py`).
