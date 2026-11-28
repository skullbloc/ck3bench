# Security

ck3bench is an environment: it runs episodes on a fake in-process world or,
through ck3agent, on your own copy of Crusader Kings III, and it can serve an
environment over HTTP. This page states what it protects, what it does not,
and how to report a problem.

## Supported versions

| Version | Supported |
|---------|-----------|
| the latest release and the latest commit on `main` | yes |
| anything older | no: update |

In scope: the `ck3bench` package and CLI, the environment server, the
research harness (`ck3bench.harness`: the seat runner, the series steps, the
reference series driver and the scorer), the shipped baseline file, and this
repository's CI workflow. The game-side toolkit (its MCP server and the
bridge loaded into the game) is ck3agent's: report those to
[ck3agent](https://github.com/skullbloc/ck3agent/security/advisories/new).
The agent CLI and the model are their vendors'.

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
  its own credentials. The seat runner reads no credential: the agent CLI it
  starts uses its own login, and the session's environment keeps only `HOME`,
  `PATH`, the locale, `TERM`, the user name, `XDG_RUNTIME_DIR`, `TMPDIR` and the
  names you pass with `--session-env`.
- **The harness starts an agent CLI that acts without asking.** `run-seat`
  starts the agent CLI (Claude Code's `claude` by default, or the command you
  give `--claude`) once per turn with `--permission-mode dontAsk`, no built-in
  tools (`--tools ""`), only the seat's MCP shim (`--strict-mcp-config`,
  `--allowedTools` naming only the shim's tools), only the `local` settings
  source, in an empty working directory under the run directory. Its tool
  calls act on the game the episode runs; nothing asks you first. Review the
  command you pass as `--claude` (or `RATE_CLAUDE_BIN` / `--agent-cli`), and
  run it as a user whose files you do not mind the CLI being able to read.
- **The harness runs commands you configure.** `run-seat --before-turns CMD`
  runs CMD (the run directory appended); a series' watch step runs
  `RATE_HOLD_CHECK` (split on spaces, the run directory appended) and, for the
  final save, `bash RATE_UI ...` with click and key arguments; the reference
  driver runs the harness's own steps as child processes with your
  environment. Each runs with your user's rights and is not sandboxed: set
  only commands you have read. `--config FILE` is read as TOML data, never
  executed.
- **What the harness writes.** Everything under the run directory (mode 700,
  the runner's umask 077), the series ledger, and the reference driver's lock
  directory. The protected-directory digest (`RATE_PROTECTED_DIR`) only reads
  that directory. `run.json` records the machine's host name, and the
  transcript the agent CLI's account and rate-limit events; `series publish`
  writes a copy with the fields and the text it has rules for rewritten, and
  refuses to write one in which a pattern of its refusal list survives
  (docs/harness.md, "Publishing a run directory", lists both and what no rule
  catches). The rewrite is a floor, not a review: a person reads every copy
  before it is released.
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
