# Changelog

All notable changes to ck3bench. Versions are calendar-based
(`YYYY.MM.PATCH`), like ck3agent's; the git tag is the version with a `v`
prefix.

## 2026.12.0 — 2026-11-27

The first version: Crusader Kings III as an environment for LLM agents.

* The research harness, `ck3bench.harness` (docs/harness.md): the seat
  runner (one fresh agent CLI session per turn, the notes pad carried by the
  env, a watchdog, the run directory's records, and `--policy noop` for the
  game's own AI alone), the one seat prompt template filled from the task,
  a series run's identity, witness, validity and sealed-result steps, and the
  scorer (success from the trajectory, the Wilson interval, the creator split,
  the resource rule, the stopping rule), and a minimal reference series
  driver (`series run`: a lock, a host-pressure sampler, an optional
  protected-directory digest, the end marker), run in its tests on the
  stand-in backend. Screen recording is not part of it. A `--policy` run (the
  game's own AI, no agent session) writes no tool listing and no session init
  event; `series end` records both as not applicable rather than as missing
  witnesses, unless the expectation declares a value for one; it starts no
  agent CLI, so its identity records no agent CLI version and a declared one
  is not held (`RATE_POLICY`). A series attempt's default run id (the host's
  next slot) is read with the variables `--config` sets. A check run plays
  the expectation an attempt will, with the values it reads (`<FREEZE: ...>`)
  left out. A start refused on its identity (exit 6) starts no game, writes no
  ledger entry and moves its run directory to `RATE_RUNS/refused-starts/`
  (`start_refused`, not an attempt), so the slot can be retried. A protected
  directory that differs (`owner_tree_differs`) makes the run invalid in
  `run.json`, the ledger, the stopping rule and the scorer: the driver takes
  its after digest before `series end`. One expectation per series: every
  ledger entry records the sha256 of the file its attempt started with, a
  start with another is refused, and the `hosts` table holds the agent CLI
  version only. A series attempt holds the expectation's `timings`, runs
  `RATE_CLAUDE_BIN` (`--agent-cli` and `--session-env` are refused in a
  series) and needs `RATE_PROTECTED_DIR`, and `series start` holds the
  runner's timing flags to the expectation's `timings` whatever the driver
  (read as the runner reads them); `series declare` writes a `<FREEZE: ...>`
  placeholder for an identity value it cannot read. A schedule names its hosts
  by labels (`a`, `b`, `h1`), never by a machine's name. An attempt is played
  out once the env wrote its end row into the trajectory (a cut end row is
  none), its driver wrote `done`, its runner wrote `end.json` with any end but
  `stopped_by_signal` (or one that does not read), or `series end` has begun
  (`score.played_out`); the runner writes `end.json` (`final: false`) before
  it closes the session and the game, so an end it decided (the seat exited,
  stopped or hung, the wall budget) is played out whatever happens to its host
  while it closes them. Both arms judge the env's end row by its own time:
  one stamped before the second of the wall-budget deadline ends
  `episode_over` whether a seat or a policy plays it, and the budget then cuts
  the post-episode hold where it runs out (`hold_cut_by_wall_budget`); one
  stamped in that second or later ends `wall_budget` in both (`end.json`
  `budget_judged`). A driver is not stopped between its attempt's end row and
  its own end; a stop there leaves the attempt played out and counted,
  invalid (a record written after the end row is absent or not final:
  `witness_absent:end_json` with `file_absent:end.json`,
  `witness_absent:end_json_final`, `runner_end:stopped_by_signal`,
  `witness_absent:game_end` or `witness_absent:owner_tree`) and a deviation,
  and the scorer prints beside k/n the sensitivity line k'/n' that adds, with
  the outcome their trajectory shows, the invalid attempts that hold a
  readable end row stamped before the second of the wall-budget deadline (the
  runner's rule, counted from the start the runner records as it starts,
  `runner_start.json`, or from the attempt's launch in a run directory without
  that file) and have no reason outside those
  six, and none numbered after the last run of a scored set that reached its
  target, naming them (`score --json`:
  `sensitivity_stopped_after_end`). `series end` takes the attempt's arm,
  model, task, speed, expectation and host label from what `series start`
  recorded in `run.json` (`declared`, with the expectation's path absolute),
  so a step run by hand reads the attempt as the driver does. The
  runner takes the first TERM or INT and ignores any further one, so a
  driver's second TERM cannot interrupt its clean end. `series end` writes a played-out attempt's
  validity once, into `run.json` and its ledger entry, and refuses to run
  again (`end_ended`) or on an attempt not played out (`end_not_played`); a
  run file absent or not read whole is a reason (`file_absent:<file>`,
  `file_unreadable:<file>`), a reading of the attempt's own run files that
  fails is `end_failed:<error>`, and it refuses by name when
  `run.json` cannot be written, the expectation (a JSON value that is not an
  object included), the schedule, the host's identity or the ledger does not
  read, the expectation is not the file `series start` read (its sha256),
  `run.json` has no `declared` block, or
  there is no start to end (`end_unwritten`, `end_expect_unreadable`,
  `end_expect_differs`, `end_schedule_unreadable`, `end_host_unreadable`, `end_declared_absent`,
  `end_ledger`, `series_ledger_unreadable`, `end_no_start`); a `run.json`
  that does not read after `series start` entered the attempt is recorded in its ledger entry, ended, invalid
  (`file_unreadable:run.json`, `file_absent:run.json`) and counted; one whose
  `declared.RATE_EXPECT` is empty while it records an expectation does not
  read. `series start` and the reference driver refuse an expectation that is
  not a JSON object (`expect_schema`), and the driver a schedule that is not
  one (nothing written, no traceback).
  Each attempt has one standing (not an attempt, in flight, valid, invalid)
  that the driver's slot and the scorer both read. `series withdraw` (and
  `--cancelled`) closes an attempt that was not played out, only once its
  driver and its game have exited (`withdraw_alive`), before `series end`
  (`withdraw_ended`), and not once it was played out (`withdraw_played`: a
  played-out attempt with no validity is ended by `series end` by hand, then
  `series close`); `series close` finishes an attempt whose `series end` wrote
  `run.json`'s validity and whose end marker is missing (the ledger entry from
  that validity when it was not written, `result.json`, the end marker), and
  `series result` writes and prints no outcome for a series attempt with no
  validity (`result_before_end`) or a `run.json` that does not read
  (`result_run_json_unreadable`). The reference driver writes the end marker
  only when `series end` and `series result` both exited 0 (or `series end`
  recorded a `run.json` that does not read), and otherwise exits 8 and names
  the command that completes the attempt, also when it is stopped by a TERM or
  a Ctrl-C. The records the `series` steps write (`run.json`, the ledger
  entries, `result.json`, the operator's records, the end marker, the
  watcher's witness files), the runner's `end.json` and the driver's `done`
  and end marker go to a temporary file, flushed to the disk and renamed over
  their name, and the env's end row is flushed to the disk as it is written
  (`ck3bench.env.durable`; the test-only switch that skips the flushes is
  recorded in `run.json` as `records_flushed`, and the scorer refuses a
  series with an attempt started under it: `series_records_not_flushed`).
  `series start` refuses a start on a host whose clock reads more than 300 s
  before its own latest start (`start_clock_behind`), printing both times.
  `analyze` reads a trajectory or transcript that is cut, empty, absent or
  holds a field of another type through the scorer's readers and names what
  did not read. `series publish` refuses a series attempt whose `run.json` is
  absent until its machines are named (`--host-name`). `run.json` and a ledger entry are each read by one
  function that checks the types of the fields the kit writes (`run.json`'s
  top level, its `series`, `validity`, and `start` and `end` identity; each
  field of a ledger entry: `score.read_run_json`, `score.ledger_files`); a ledger file of another name than `attempt-NN.json`
  (`attempt-1.json`) or a field of another type is refused by name, never
  skipped, by the slot and the scorer alike. The scorer scores a series against its ledger (every ledger attempt
  given, nothing else, one expectation; else refused) and refuses, whatever the
  override, a ledger entry and a run directory that disagree on the standing,
  the validity or its reasons (`series_ledger_disagrees`), a ledger entry that
  does not read (`series_ledger_unreadable`) or started more than 300 s
  before the same host's earlier attempt (`series_ledger_order`; the hosts'
  clocks are not compared with each other), a run directory with no
  `run.json` or one that does not read whose ledger entry does not record it
  (`series_run_json_absent`, `series_run_json_unreadable`), a `run.json`
  validity the rule does not read again (`series_validity_disagrees`: the
  protected directory, the resource rule and the run's files are read again)
  and a set mixing series and other runs (`series_mixed`); a reading of a run
  that fails is that run's `read_failed:<error>`; its `--json` names runs by basename, reduces paths and
  host and user names, and carries the series' identity (the task, the start
  save, the map, the game build). `series publish` writes a run directory's
  publishable copy (limit text, host names in any capitalisation, configured,
  home and other private paths, network shares, sockets and temporary paths,
  IP and MAC addresses, user ids, session and Drive links, the agent CLI's
  account and limit fields and session ids replaced; the account names `root`,
  `shared`, `public` and `runner` name no person and are left); it refuses, by class,
  file and line, when one survives, and before any rule a symbolic or hard
  link and a host that is not a label; a file that is not UTF-8 text, holds a
  NUL byte or does not decompress, and a `run.json` that is not one JSON
  object, is published as a stub naming its size, sha256 and damage; the
  machine names a damaged `run.json` still holds are rewritten, and with none
  there the copy is refused unless the operator names them (`--host-name`,
  `RATE_HOST_NAMES`); the copy holds the driver's `done` and end marker and
  the operator's withdrawal record, so a withdrawn attempt's copy shows it was
  not played out; an e-mail address and the key and token shapes
  `publish.SECRET` lists (a quoted value after a key's name read to the
  closing quote of its kind; docs/harness.md names each, and what no rule
  catches) refuse; a person reads
  the copy before it is released.
  `score --compare` compares two stopped series, the seated one then the
  control (the difference with Newcombe's hybrid score interval, Fisher's
  exact test), and refuses two that are not of one task from one start
  (`identity_differs`, `identity_unread`). The schema
  names are `ck3bench.series.run/1`, `.expect/1`, `.schedule/1`,
  `.result/1` and `.published/1`.
* The pinned build (`BUILD_PINS.toml`): the toolkit release this version
  requires (its wheel's members digest, its reply keys, its tool listing with
  the bridge switches unset and with the army switches set, the bridge's
  source files), the seat prompt and notes version of
  `munster1066-kingdom-once-30y-wake`, and this package's parts that decide
  what a seat is told; `tests/test_build_pins.py` recomputes every value from
  the code. No measured result is published with this version.
* A series of `munster1066-kingdom-once-30y-wake` and its control, declared
  in advance (`docs/prereg-munster-kingdom.md`): the commands on each host,
  the fixed settings, validity, the stop at 40 valid runs or 56 attempts,
  what is sealed until the stop, the outcomes and the comparison, what is
  published, and what the seat is given beyond the task (notes written from
  earlier play of the same start, and the bench's scaffold: the goal line
  from the first turn, standing orders, forced wakes). Every de jure task's
  seat prompt states that a line passing to a ruler of another dynasty ends
  the episode as a loss (the rule every task scores by).

* A parallel multi-agent environment (PettingZoo's parallel shape; seats are
  agents) and a Gymnasium-shaped one-seat view. Actions are typed tool calls
  on ck3agent's 13 public game tools; one step is one decision cycle of N
  game-days with the game paused while the seats act; rewards are declared
  terms read from game state; truncations are typed, and a failed step's
  reward is NaN, never 0.
* Two backends: the real game through ck3agent (pinned at exactly
  `2026.12.12`), and a deterministic fake world for tests and policy wiring.
  Each trajectory header records the installed toolkit's version and the
  digest of its files.
* 23 built-in tasks, declared by one strict reader that also reads task TOML
  files, each with its run status and whether a user can obtain its start
  (`ck3bench env scenarios`): four France 1066 tasks (2 to 10
  game-years, one with six seats); Ireland 1066 (Murchad of Dublin: 15 and
  30 years, and two wake forms); Munster 1066 (Murchad ua Briain: the kingdom
  ending the episode, or paid once with play going on to the end date);
  Francia and Sweden from the 867 start; two line tasks from the 1066 start
  (Lorraine and Carinthia: keep the line ruling, in peace, judged at the end
  date); four decision tasks on mid-game Ireland saves, paid as the Ireland
  task, each recording at its end an outcome measure of the one decision its
  start was built for (not part of the return; the seat is not told of it);
  and five fake tasks.
* Wake tasks: a seat ends its turn with conditions and the step's clock runs
  until one holds; forced wakes (a succession, a war declared on the seat);
  standing orders, lever calls the environment sends when their conditions
  hold. An order that creates a title on a gold threshold is told the price
  read beside its threshold (`order_notes`). An order ends when the ruler
  changes; one whose call names no character (it creates a title by its key
  alone) is handed back in the turn's `ruler_changed` news ready to set again
  (`set_again`), with the title's price read for the new ruler when that
  price reads.
* Seats found on the save at open (the holder of a named title, confirmed by
  his name); a line that passes to another dynasty ends the episode as a
  loss.
* On the real game, each seat's observation carries its toolkit news since
  its previous turn (`world.news`) and, on a task with a target title, a
  goal line on what the title still waits for; the toolkit is told the
  task's target title, goal kind and notes set.
* A declared game speed (1 to 5) read back at every step, with wake checks
  aligned to their dates.
* Environment-owned messages and notepads, and team rewards; a carried notes
  pad per seat for a new session every turn.
* Seeded random, scripted and no-op policies, a provider-neutral tool-calling
  agent with an optional Anthropic adapter, an HTTP+JSON env server, a
  mixed-policy runner, and an MCP shim for a Claude Code seat (an
  acknowledgement mode for one session per turn, a lean reply envelope, and
  pad patches).
* A JSONL trajectory format (`ck3bench.trajectory/2`) and its checker, and a
  re-judge of a recorded episode under a task (`replay.py`).
* A baseline of eight unsteered 100-game-year games from the 1066 France start.
