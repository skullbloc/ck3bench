# Security

ck3bench reads experiment files and documents you point it at and prints what
it found. This page states what it protects, what it does not, and how to
report a problem.

## Supported versions

| Version | Supported |
|---------|-----------|
| the latest commit on `main` (no release has been cut yet) | yes |
| anything older | no: update |

In scope: the `ck3bench` package and CLI, the example experiment files, and
this repository's CI workflow. The game, the model and the agent that plays
are ck3agent's: report those to
[ck3agent](https://github.com/skullbloc/ck3agent/security/advisories/new).

## Threat model

- **No network, no game, no model.** `ck3bench experiment check` opens the
  TOML file you name, the registration document under `--tree`, and the
  frozen document you pass with `--block`; it reads ck3agent's installed
  version from the package metadata. It starts no process and opens no
  connection.
- **Experiment files are data.** They are parsed with `tomllib`; nothing in
  them is executed. Every key is typed; an unknown key is refused.
- **No secrets in tracked files.** An experiment can name where a key file
  lives (`[seats.billing] key_file`), never a key. A tracked experiment that
  carries a path under `/home`, `/mnt` or `/tmp`, or a host name you declare
  in the untracked `<name>.local.toml`, is refused
  (`host_path_in_tracked_file`). Keep host values in that untracked overlay.
- **CI.** The workflow runs with a read-only token, uses no repository
  secrets and no self-hosted runner, and pins every action by commit sha.

**Out of scope:** attacks that already need your user account, and
ck3agent, the game, and your model provider.

## Reporting a vulnerability

Open a [private security advisory](https://github.com/skullbloc/ck3bench/security/advisories/new).
If that form is unavailable, send a private message to the maintainer
([@stebbins](https://github.com/stebbins)) on GitHub with "ck3bench security"
in the first line. Do not file public issues for security problems. We
acknowledge within 7 days and aim to fix within 90 days.
