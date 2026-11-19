# Security

ck3bench is an environment: it runs episodes on a fake in-process world or,
through ck3agent, on your own copy of Crusader Kings III, and it can serve an
environment over HTTP. This page states what it protects, what it does not,
and how to report a problem.

## Supported versions

| Version | Supported |
|---------|-----------|
| the latest commit on `main` (no release has been cut yet) | yes |
| anything older | no: update |

In scope: the `ck3bench` package and CLI, the environment server, the shipped
baseline file, and this repository's CI workflow. The game, the model and the agent that plays
are ck3agent's: report those to
[ck3agent](https://github.com/skullbloc/ck3agent/security/advisories/new).

## Threat model

- **The env server has no authentication.** `ck3bench env serve` binds
  loopback (127.0.0.1) and refuses any other address (`remote_bind_refused`)
  unless you pass `--allow-remote`; anyone who can reach the port can drive
  the game it serves. Put it behind your own authenticated transport if you
  expose it.
- **The fake backend, `scenarios`, `check` and `baseline` open no connection
  and start no process.** `run` and `serve` on a real task start the game
  through ck3agent (`ck3agent launch`) and talk to its bridge over a local
  socket; they kill only the game they started, after re-reading its identity.
- **Declarations are data.** Task files are parsed with `tomllib`, trajectory
  and baseline files with `json`; nothing in them is executed. Every key is
  typed; an unknown key is refused.
- **No secrets.** ck3bench reads no API key; a policy that calls a model holds
  its own credentials.
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
