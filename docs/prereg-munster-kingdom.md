# The Munster kingdom series and its control, declared in advance

This document fixes, before the first attempt, how two series of
`munster1066-kingdom-once-30y-wake` are run, when they stop, what makes an
attempt count, and how their outcomes are read and compared. It is part of
this version of the bench and needs nothing outside it: every rule below is
the bench's own code (`ck3bench/harness/`), named where it applies, the
comparison of section 9 included (`score --compare`). The build both series
play is the one this version is pinned to,
[BUILD_PINS.toml](../BUILD_PINS.toml) ([the pinned build](harness.md#the-pinned-build));
this document restates none of its values.

The two series:

* **the seated series**: a Claude model plays the seat, one fresh agent CLI
  session per turn;
* **the control series**: nobody plays the seat; the game's own AI runs the
  ruler throughout (the bench's `noop` policy, `--policy noop`).

Nothing else is run under this document.

## 1. The task

`munster1066-kingdom-once-30y-wake` (`ck3bench/env/scenario.py`; `python -m
ck3bench.harness prompt munster1066-kingdom-once-30y-wake` prints the seat
prompt): Murchad ua Briain (character 31762), Duke of Munster, from the 1066
start (1066.09.15) to the end date 1096.09.15. The task's nominal length is
150 steps of 73 game-days; it is a wake task, so the episode ends at the end
date (or earlier, below), whatever number of steps that takes: a turn may end
with wake conditions and the step's clock runs until one holds, checked every
7 game-days, at most 365 days, 73 days when the seat sets none; a succession
in the seat's line and a war declared on the seat always wake it; the seat
may leave standing orders.

Reward: +1 for each de jure Irish county gained, -1 for each lost; +10 once,
on the first step the seat's ruler holds the Kingdom of Ireland
(`k_ireland`; the prompt: "your ruler first holds"), however it comes to
him, never again if it is lost and regained; -5 if the line ends (no heir)
or passes to a ruler of another dynasty. Holding the kingdom does not end the episode: play goes on to the
end date. The line ending, or passing to a ruler of another dynasty, ends
the episode there, as a loss. The seat is served the notes set
`munster1066once` and the bench's scaffold (section 12).

The start save is `_1066_09_15.ck3`, a save of the 1066 bookmark on its first
day. The bench does not ship it (it is the game's own data); each host makes
one as the README's "Starts and saves" says: start a game at the 1066
bookmark and save on its first day (1066.09.15) under that name. The task
declares no sha256 for it: both series use the same one file, the same bytes
on both hosts (made once, copied to the other), whose sha256 both
expectations record (`start_save_sha256`), so every attempt of either series
starts from that file. `score --compare` refuses two series whose task, start
save, map or game build differ (`identity_differs:<key>`).

## 2. The fixed settings

Both series, on both hosts:

| setting | value | where the kit holds it |
|---|---|---|
| build | the pinned one: the toolkit release `pyproject.toml` requires, its bridge library, this bench version from a clean git checkout | the expectation's `git_commit`, `wheel_sha256`, `wheel_members_sha256`, `bridge_so_sha256` (`series declare`; a value it cannot read is a `<FREEZE: ...>` placeholder, which every attempt refuses until it is filled), held at each start and end (`series start`, `series end`) |
| game | Crusader Kings III 1.18.3.1, Linux native; one game build on both hosts | `game_build_id` |
| map graph | one `ck3map.bin` baked from that build, the same bytes on both hosts | `map_sha256` |
| game speed | 5, declared, with the wake checks aligned to their dates | `game_speed: 5`, `aligned_checks: true` (from `RATE_GAME_SPEED=5` at `series declare`); read back at every step (`speed_differs`) |
| bridge switches | `CK3_BRIDGE_ARMY_ORDERS=1`, `CK3_BRIDGE_ARMY_HOLD=1`; every other `CK3_BRIDGE_*` unset | `switches` (`series switches` refuses another value or an undeclared switch) |
| kit switches | `TURN_MODE=fresh`, `SEAT_PROMPT=default`, `NOTES=on`, `FULL_HOLD=600`, `RECORD=0`, `VIEW=` (empty), `CLEANMAP=0` | `kit`; the reference driver refuses `RECORD`, `VIEW` or `CLEANMAP` on (`kit_switch_unhonoured`) |
| seat surface | the runner's defaults: lean reply envelope, reply pages on, notes edits on, observation deltas off, quiet prompt off, fresh turns; notes pad 4096 bytes | recorded, not held: the trajectory header's `seat_surface` (the rendered prompt, which holds the pad size, and the notes version are held) |
| timings | the reference driver's defaults: `--turn-timeout-s 1200`, `--step-wait-s 900`, `--wall-budget-s 27900`, `--wall-s 28800` | the expectation's `timings` (`series declare` writes them); the driver runs an attempt with them and refuses a flag naming another value (`expect_differs:timings`, exit 5); `series start` holds the runner's three timing flags in `run.json` `runner_flags` to them whatever the driver (`expect_differs:timings.<key>`; `wall_s` is the driver's own limit) |
| seat prompt | the one template rendered for this task | `prompt_sha256`, held at the end (`prompt_differs_from_launch`) |
| notes | the set `munster1066once`, at the version a check run reads | `notes_version` |
| protected directory | each host's game default user-data directory (`~/.local/share/Paradox Interactive/Crusader Kings III`), which the run's game does not use (`ck3agent launch` gives it its own) | `RATE_PROTECTED_DIR`, digested before the run and before `series end`; unset, an attempt is refused (`protected_dir_not_configured`) |
| seated series only: model | `claude-opus-5-5`, passed as `--model` | `model`, and `seat_model_reported` from the session's init event |
| seated series only: agent CLI | Claude Code, one versioned binary per host (`RATE_CLAUDE_BIN`), its version declared per host; the sessions run that binary (the driver refuses `--agent-cli` and `--session-env` in a series: `series_agent_cli`) | `claude_version` (per host in the expectation's `hosts` table, which holds that key and no other: `expect_hosts_key`) |
| seated series only: tools | the tool listing a check run reads | `tool_listing_sha256` |
| control series only: model | none (`RATE_MODEL=none`); no agent CLI starts | `model: none`; no agent CLI version is recorded or held, and no `tool_listing_sha256` or `seat_model_reported` (a `--policy` run has no session: [docs/harness.md](harness.md#running-and-scoring-a-series)), so the hosts' agent CLIs play no part |

A value that differs from its expectation at a start refuses that start
before the game (exit 6; not an attempt): no ledger entry is written, and
the reference driver moves the run directory to `RATE_RUNS/refused-starts/`
with the refusal in `start_refused.txt`, so the same slot is run again once
the difference is put right. A refusal for a switch or a timing that differs, a
placeholder left in the expectation, a usage error (an agent CLI other than
`RATE_CLAUDE_BIN`, no protected directory, an expectation without `timings`
among them), a held lock or a slot that is not the host's (exit 5, 2, 3 or
7) comes before the lock and the run directory. A
value that changes between a run's start and its end makes the run invalid
(`start_end_differs:<key>`).

## 3. Commands

Two hosts, named `a` and `b`. Each host runs one game at a time (the
reference driver's lock). Every path below is a placeholder for that host's
own.

**Once per host: the host configuration**, a TOML file the driver and every
series step read with `--config` (a variable already in the environment
wins):

```toml
[env]
KIT_REPO = "/path/to/ck3bench"                  # a clean git checkout of this bench version
RATE_RUNS = "/path/to/runs"                     # the run directories and the series ledgers
RATE_SAVES = "/path/to/saves"                   # holds _1066_09_15.ck3
RATE_SO = "/path/to/libck3_bridge.so"           # the toolkit release's bridge library
RATE_WHEEL = "/path/to/ck3agent-<version>-py3-none-any.whl"   # the wheel this environment installed
MAP_BIN = "/path/to/ck3map.bin"
RATE_GAME_BIN = "/path/to/steamapps/common/Crusader Kings III/binaries/ck3"
RATE_CLAUDE_BIN = "/path/to/claude/versions/<version>"         # a versioned file, not a link an update moves (the seated series)
RATE_INSTANCE = "a"                             # "b" on host b
RATE_PROTECTED_DIR = "/path/to/.local/share/Paradox Interactive/Crusader Kings III"   # the game's default user-data directory
CK3_BRIDGE_ARMY_ORDERS = "1"
CK3_BRIDGE_ARMY_HOLD = "1"
FULL_HOLD = "600"
TURN_MODE = "fresh"
SEAT_PROMPT = "default"
NOTES = "on"
RECORD = "0"
VIEW = ""
CLEANMAP = "0"
```

The game's display variables (`DISPLAY`, and `XAUTHORITY` and
`SDL_VIDEODRIVER` where the host needs them) come from the environment the
driver runs in ([the game's environment](harness.md#the-games-environment));
the bridge log the creator witness reads is the toolkit's own path for
`RATE_INSTANCE`, so `RATE_BRIDGE_LOG` is not set.

**Once per series: the expectation and the schedule.** On host `a`:

```bash
# the seated series
RATE_SCENARIO=munster1066-kingdom-once-30y-wake RATE_MODEL=claude-opus-5-5 RATE_GAME_SPEED=5 \
  python3 -m ck3bench.harness series declare --config host-a.toml > expect-seated.json
# the control series: a --policy series declares no agent CLI version and no session values
RATE_SCENARIO=munster1066-kingdom-once-30y-wake RATE_MODEL=none RATE_POLICY=noop RATE_GAME_SPEED=5 \
  python3 -m ck3bench.harness series declare --config host-a.toml > expect-control.json
```

The values a check run reads are `<FREEZE: ...>` placeholders in the
declared files (`notes_version` in both; `tool_listing_sha256` and
`seat_model_reported` in the seated one). An attempt refuses a file that
still has one (`expect_not_frozen`); a check run plays the file as it is,
leaving them out (`run.json` `expectation.unfrozen` lists them). One check
run per series on host `a` (a check is never scored and takes no slot):

```bash
python3 -m ck3bench.harness series run --host a --config host-a.toml --expect expect-seated.json \
    --check --run-id check-seated --wall-budget-s 900
python3 -m ck3bench.harness series run --host a --config host-a.toml --expect expect-control.json \
    --policy noop --check --run-id check-control --wall-budget-s 900
```

`run.json` `end.seen` of each check holds the values; they replace the
placeholders. A value `series declare` could not read (a missing file, a
binary that did not answer) is a placeholder too, naming where the check's
`run.json` `start` has it. In `expect-seated.json`, add a `hosts` table with
each host's agent CLI version (`{"a": {"claude_version": "..."}, "b":
{"claude_version": "..."}}`, as each host's `RATE_CLAUDE_BIN --version`
prints it); it holds that key and nothing else. The two expectations are then
frozen, and neither is edited again. A check refused at its start (exit 6) leaves
nothing at `RATE_RUNS/check-*`, so the same command runs again. The two
schedules (`ck3bench.series.schedule/1`,
[docs/harness.md](harness.md#running-and-scoring-a-series)):

```json
{"schema": "ck3bench.series.schedule/1", "series": "munster-kingdom-seated", "task": "munster1066-kingdom-once-30y-wake",
 "pattern": ["a", "b"], "valid_target": 40, "attempt_cap": 56}
```

```json
{"schema": "ck3bench.series.schedule/1", "series": "munster-kingdom-control", "task": "munster1066-kingdom-once-30y-wake",
 "pattern": ["a", "b"], "valid_target": 40, "attempt_cap": 56}
```

Both hosts hold the same four files, byte for byte. Every attempt's
`run.json` records the expectation and the schedule with its sha256.

**One attempt** of the seated series on host `a` (on host `b`: `--host b`
and `--config host-b.toml`); a dry run first, which prints the slot and the
runner's command and starts nothing:

```bash
python3 -m ck3bench.harness series run --host a --config host-a.toml \
    --series seated.json --expect expect-seated.json --dry-run
python3 -m ck3bench.harness series run --host a --config host-a.toml \
    --series seated.json --expect expect-seated.json
```

**One attempt** of the control series:

```bash
python3 -m ck3bench.harness series run --host a --config host-a.toml \
    --series control.json --expect expect-control.json --policy noop --dry-run
python3 -m ck3bench.harness series run --host a --config host-a.toml \
    --series control.json --expect expect-control.json --policy noop
```

The run id is the host's next slot (`<series>-aNN-<host>`). Before each
attempt, the other host's ledger entries (`RATE_RUNS/<series>.ledger/`
`attempt-NN.json`) are copied in; after each start and each end, this host's
new entry is copied to the other host. The driver refuses an attempt that is
not its host's next slot, or whose lower-numbered attempts have not all
started (`series_alternation`, exit 7), so the hosts alternate. Each ledger entry
records the sha256 of the expectation its attempt started with, and a start
with another file is refused (`series_expectation_differs`, exit 6): a series
plays one expectation, frozen before its first attempt and never edited
after it.

**After the stop**, the run directories of both hosts are put in one place
(`RUNS` below), with the series' ledger beside them as
`RUNS/<series>.ledger/` (either host's: each holds every entry once the
copies above are made), and every attempt directory of the series is scored
once against that ledger: the scorer refuses (exit 4) when an attempt the
ledger names is not among the directories given, when a directory given is
not one of its attempts, when the attempts played more than one
expectation, and when a ledger entry or a `run.json` validity disagrees with
the validity read again (section 5). The two series are then compared
(section 9):

```bash
python3 -m ck3bench.harness score RUNS/munster-kingdom-seated-a01-a RUNS/munster-kingdom-seated-a02-b ... --json > seated-score.json
python3 -m ck3bench.harness score RUNS/munster-kingdom-control-a01-a RUNS/munster-kingdom-control-a02-b ... --json > control-score.json
python3 -m ck3bench.harness score --compare seated-score.json control-score.json
```

`ck3bench/harness/test_prereg_sequence.py` runs this sequence on the bench's
stand-in backend, in this order and with these commands (declare with
`--config`, the check, the freeze, the dry run, the attempts on two hosts, a
refused start and its retry, `series withdraw` on an ended attempt
(refused), the stop, `score`, `score --compare`, `series publish`), with the differences its
docstring lists: the stand-in task, a short wall budget declared
(`RATE_WALL_BUDGET_S`, 15 s and 60 s, which the attempts then hold, and
which cut the stand-in's 600 s hold after its end row), a schedule of
target 2 and cap 3 (control) or 2 and 2 (seated), and a stand-in agent CLI
as each host's `RATE_CLAUDE_BIN`. Its seated half needs the `mcp` package and
is skipped without it.

## 4. Order of the series and the hosts

The seated series runs first, to its stop. The control series starts after
the seated series has stopped. Within a series, attempt *n* plays on host `a`
when *n* is odd and on host `b` when *n* is even, back to back: each host
starts its next attempt as soon as its previous one has ended and the other
host's lower attempt has started. One game per host at a time.

**Played out, or not.** An attempt is played out once the env has written its
end row into the trajectory (the episode's own end, which fixes its outcome),
its driver has written `done` (its runner returned to it), its
runner has written `end.json` with any end but `stopped_by_signal` (an
`end.json` that does not read counts as played out), or `series end` has
begun (`bridge_title_witness.json`, `run.json`'s validity, `result.json`, the
end marker): a played-out attempt is ended by `series end`, then `series
close` when its driver stopped, and only an attempt not played out (its
runner and driver stopped from outside before the end row: the operator's
stop, or its host going down) is withdrawn (`score.py` `played_out`, which
`series end`, `series withdraw` and the reference driver all read). The end
row counts when it reads whole as a row of the trajectory: a host that went
down while writing it leaves a cut last line, which is no end row, and the
attempt is then not played out. An end row of `closed` or
`abandoned_by_reset` is the env closed before its episode's end and is no end
of the episode. When the runner ends an episode itself (the seat exited,
stopped or hung, or the wall budget ran out), it writes `end.json` first,
before it closes the session and the game (`final: false` until both are
closed), so such an attempt is played out even when its host goes down while
it closes them and the env's end row reads `closed`.

**The wall budget and the end row, the same in both arms.** The runner
judges the env's end row by the row's own time, whether a seat or the
control's policy plays: an episode whose end row is stamped (to the second,
as the env stamps it) before the second of the attempt's wall-budget
deadline (the runner's start, which it records as it starts in
`runner_start.json`, plus its budget) ends `episode_over` (valid as
far as the runner's end goes), and the 600 s hold after it is cut where the
budget runs out (`end.json` `hold_cut_by_wall_budget`); one stamped in that
second or later ends `wall_budget` (invalid), and so does a run with no end
row when the budget runs out. The same row time gives the same end in both
series: a seat's turn begun before the deadline and played past it ends
`wall_budget` when its end row comes after the deadline, as a control
episode cut at its budget does (`end.json` `budget_judged` records the row's
time and the deadline). A hold the budget cuts can also cut the final save
and the hold check, which `result.json` records and which are no validity
reason.

**No stop between the end row and the driver's own end.** Once the env has
written an attempt's end row, its outcome is fixed in the trajectory. Its
driver is not stopped (no kill, no Ctrl-C, no shutdown, no `series end` run
by hand) until it ends by itself: through the 600 s hold after the end row,
while the runner closes the game, and through the driver's own `series end`
and `series result`. A stop there does not change that the attempt was
played out and counts toward the attempt cap; the attempt is then invalid,
because a record written after the end row is absent or not final: the
runner's `end.json` (`witness_absent:end_json` with `file_absent:end.json`,
`witness_absent:end_json_final`, or `runner_end:stopped_by_signal`), the
watcher's game witness at the end (`witness_absent:game_end`), or the
protected directory's after-digest, which the driver takes only on its own
way through (`witness_absent:owner_tree`). These six reasons are the whole
class (`score.py` `STOPPED_AFTER_END`). A Ctrl-C reaches a seated runner in
the hold only through the driver's kill after its 300 s wait, so a late one
leaves the runner's record whole and the attempt invalid by
`witness_absent:owner_tree` alone. Any attempt stopped there is a recorded
deviation (section 10). An attempt whose trajectory holds a readable end row
stamped before the second of its wall-budget deadline, that is invalid, and whose every
reason is in that class is reported with its outcome in the sensitivity line
(section 8); an attempt with any other reason as well is not in that line. A recovery by hand reads the attempt as
the driver would: `series end` takes the attempt's arm (a `--policy` run or
a seated one), its model, task, speed, expectation and host label from what
`series start` recorded in `run.json` (`declared`), never from the
environment it is run in.

**The hosts' clocks.** Across the two hosts the order of a series is its
attempt numbers (the slot gives them); the hosts' clocks are never compared
with each other. A host's own clock must not be set back by more than 300 s
during a series: the scorer refuses a ledger in which one host's attempt
started more than that before the same host's earlier attempt
(`series_ledger_order`), and nothing repairs a frozen ledger, so the hosts
keep their clocks synchronised; `series start` refuses a start whose host's
clock reads more than that before the host's own latest start
(`start_clock_behind`), so the refusal comes before the attempt, not at the
score.

**What the kit guarantees.** A crash or a kill at any moment, a power loss
at any moment but two, and any file either leaves truncated, empty, missing
or not UTF-8, never gives a traceback from a `series` step, the scorer or
`series publish`, never changes whether a played attempt counts, and has a
documented recovery ([harness.md](harness.md#a-driver-stopped-mid-attempt);
a record damaged after its attempt's end is a named refusal of the score, and
the run directory is copied again from the host that played it). Every
record that decides whether an attempt was played out (the `series` steps'
records, the runner's `end.json`, the driver's `done` and end marker, the
env's end row) is flushed to the disk before its writer goes on; the two
moments left are a power loss while the end row itself is being flushed,
which can lose that row, and one while the runner's first `end.json` (for an
end the runner decided) is being flushed, which can lose that record; in
either case the attempt then reads as not played out. A file
edited by hand is outside that guarantee and is a deviation (section 10); a
field of another type than the kit writes, anywhere in `run.json`, `end.json`
or a ledger entry, gives a named refusal, or, for a `run.json` damaged after
its start, a named reason in its ledger entry (below), never a traceback.

**A host withdrawn mid-attempt, or the series cancelled while an attempt
plays.** The schedule does not change. An attempt that was not played out is
closed in this order, on the host that played it, and the kit refuses any
other:

1. its driver is stopped (or has stopped: a host that went down took it
   with it);
2. its game has exited;
3. `series withdraw RUN_DIR --config host-X.toml` (with `--cancelled` when
   the series is cancelled) writes `operator_withdrawn.txt` (or
   `operator_stopped.txt`) into the run directory and closes the attempt's
   ledger entry (ended, invalid `operator_withdrew_host` or
   `operator_cancelled_series`, not an attempt); the entry is copied to the
   other host like any other.

`series withdraw` refuses while the attempt's driver or a game its witnesses
recorded still runs (`withdraw_alive`, nothing written), refuses an
attempt `series end` has reached (`withdraw_ended`) and one that was played
out (`withdraw_played`): an ended attempt stands as its end step wrote it, a
played-out one is ended (`series end` by hand, then `series close`), and
`series end` refuses an attempt that is withdrawn or was not played out
(`end_not_played`). A withdrawal stopped between its two writes is completed by
running the same command again. The attempt keeps its number, does not count
toward the attempt cap, and the same host's next slot takes its place. The
other host waits at its next slot (the alternation holds). If a host cannot
return, the series is not completed under this document: it is reported as
abandoned, with its ledger (validity only), and its outcomes are not read;
the scorer refuses a series that has not stopped.

**A driver stopped during `series start`.** When its run directory is left in
the slot's place and the ledger has no entry for its attempt, the next `series
run` for that slot refuses (the directory exists). The directory is moved by
hand to `RATE_RUNS/refused-starts/RUN_ID-UTC` with a `start_refused.txt` saying
why; the slot is then free, and the directory scores as a refused start. When
the ledger has an entry for it, the attempt is withdrawn as above.

**A `run.json` damaged after `series start`.** When the ledger holds the
attempt's open entry and its `run.json` does not read or is gone, the ledger
entry is the attempt's record: `series end RUN_DIR` writes it ended, invalid
(`file_unreadable:run.json` or `file_absent:run.json`) and counted, and the
end marker; the attempt counts toward the cap whether or not its game was
played out, and `series withdraw` refuses it.

## 5. What makes an attempt valid

Validity is the kit's, decided at each attempt's end by these rules and
not by its outcome (an attempt stopped after its end row is invalid whatever
its outcome, and that outcome is reported beside k/n, section 8):

* **the identity rules** (`ck3bench/harness/series.py`: `check_static` at
  the start, `cmd_end` at the end, `session_witnesses`, `witness_reasons`,
  `header_reasons`): every start value equals its end value and every
  declared expectation; the game process witnessed at the start and at the
  end, with the bridge mapped; the rendered prompt, the tool listing, the
  model reported by the session (the seated series), the notes served, the
  runner's end reason (`runner_end:<reason>` for every end other than
  `episode_over`, `seat_stopped`, `seat_hung` and `env_failed`), and the
  env's technical truncations (`technical_truncation:<reason>`: a game that
  exited, a bridge that became unavailable, a clock that stalled, a reward
  that could not be read, the game's script log closed
  (`script_log_closed`), a ruler change whose dynasty did not read
  (`line_dynasty_unread`));
* **the clock** (`ck3bench/harness/score.py` `trajectory_validity`): the game
  speed read at every step equals 5, and each aligned check's date is
  recomputed from its rows: 1 to 7 game-days off is flagged and the run stays
  valid; more is invalid (`checks_off_beyond_cadence`); a missing date is
  `witness_absent:check_dates`;
* **the resource rule** (`score.py` `resource_rule`): a run whose runner ended
  `seat_stopped` or `seat_hung` is void, not a failure, when its stop window
  shows an account rate or usage limit, an API error or host pressure over
  the thresholds, or when that evidence is absent
  ([absent pressure evidence](harness.md#absent-pressure-evidence));
* **the protected directory** (`RATE_PROTECTED_DIR`, digested read-only
  before the run and again before `series end`; `score.py` `owner_tree`): a
  difference makes the run invalid (`owner_tree_differs`), in `run.json`, in
  the attempt's ledger entry, for the stopping rule and in the scorer's row;
  a directory digested before and not after is `witness_absent:owner_tree`;
* **the end marker** (`rate_run.done`): the scorer counts an attempt as ended
  only when `run.json` has its validity and the marker exists
  (`score.py` `validity_only`). An attempt whose driver stopped after
  `series end` wrote `run.json`'s validity (its ledger entry ended or still
  open, no marker) holds the stop until `series close RUN_DIR`, run on its
  host once its driver and its game have exited, writes the ledger entry from
  that validity when it is missing, then `result.json` and the marker; the
  scorer names such attempts. An attempt whose game was played out and that
  has no validity (its driver stopped before `series end`, or `series end`
  refused) is ended by `series end` run by hand, then `series close`; one
  whose game was not played out is withdrawn (section 4). The reference
  driver writes the marker only when `series end` and `series result` both
  exited 0, and otherwise exits 8 naming the command that completes the
  attempt;
* **the run's files** (`score.py` `file_reasons`): a trajectory or an
  `end.json` that is absent (`file_absent:<file>`), and a trajectory,
  `end.json`, `run.json` or `result.json` that does not read as JSON of its
  kind (a line cut short, a NUL-filled tail, another JSON type, bytes that are
  not UTF-8, a trajectory with no row; an `end.json` whose `end_reason` is not
  text or whose `notes_witness` is not an object; a launch time that is not a
  UTC time: `file_unreadable:<file>`), make the attempt invalid; it still
  counts toward the attempt cap. A reading of the attempt's own run files by
  `series end` that fails is `end_failed:<error>`; an expectation or schedule
  it cannot read, or an expectation that is not the file `series start` read,
  is a refusal, and nothing is written. The transcript and the turns file are read by the
  resource rule only, for a stop or a hang.

**One reading of validity.** `series end` writes the attempt's validity once,
into `run.json` and then into its ledger entry: the identity rules and the
clock, plus `score.py` `attempt_reasons` (the protected directory and the
resource rule, after waiting for the stop window's last pressure samples). It
refuses to run again (`end_ended`, exit 6, nothing written) on an attempt
whose `run.json` has its validity, that has `result.json`, that is
withdrawn, whose ledger entry has ended or carries an end's fields, or that
has no ledger entry (a `run.json` that does not read is section 4's). Each
attempt then has one standing (`score.py` `standing`: not an attempt, in
flight, valid or invalid), which the driver's stopping rule reads from the
ledger and the scorer from the run directories; the scorer refuses the
series (exit 4, nothing read) when a ledger entry and its run directory
disagree on it (`series_ledger_disagrees`: its validity and its reasons, or
whether it is an attempt), when a ledger entry does not read
(`series_ledger_unreadable`: a file name other than `attempt-NN.json` with at
least two digits, a field of another type, no start time with its zone) or
started more than 300 s before the same host's earlier attempt
(`series_ledger_order`), when a run directory has no `run.json` or one that
does not read and its ledger entry does not record that damage
(`series_run_json_absent`, `series_run_json_unreadable`), or when a `run.json`
validity and the rule read again disagree (`series_validity_disagrees`).
Within the states and file shapes the tests below reach, the driver and the
scorer stop a series on the same count, or the scorer refuses by name. `ck3bench/harness/test_series_states.py` checks
this over every order of the commands of sections 3 and 4 on a three-attempt
stand-in series on two hosts, each command (and the driver) also stopped
before each of its writes while the other attempts are finished or not
begun, with a `series end` whose reading raises and a trajectory cut after an
end, and checks that from every state it reaches the documented commands lead
to a scored stop or to a clean continuation, or the scorer refuses by name;
`test_series_damage.py` checks each run file in eight damaged shapes against
every command. These are the states and shapes the tests reach.

A run is valid when `run.json` `validity.valid` is true, its kind is `rate`
(never a check), the resource rule voids nothing, and its protected
directory is unchanged. An attempt with a withdrawal or cancellation file
that `series withdraw` wrote (before `series end`, section 4) is not an
attempt; such a file found beside the validity `series end` wrote was put
there by hand and refuses the score (`series_ledger_disagrees`,
`series_validity_disagrees`). Every invalid attempt counts toward the
attempt cap; a refused start (any exit other than 0: section 2) is not an
attempt. A tool that refuses or
misreports during a run does not void it: the toolkit is part of what is
measured.

## 6. When a series stops

Each series stops at **40 valid runs or 56 attempts, whichever comes first**
(the schedule's `valid_target` and `attempt_cap`; `score.py` `stop_state`,
by validity alone: the one validity of section 5, which the driver reads from
the ledger and the scorer from the run directories). The driver refuses every
attempt after the stop (`series_stopped`, exit 7). The scored set is the first 40 valid runs by
attempt number; a valid run that was still playing when the 40th valid run
ended is listed (`played_out_not_scored`) and not scored. If the cap is
reached first, every valid run is scored.

## 7. Blinding: no outcome is read before the stop

**What the kit enforces.** In a series, `series result` seals `result.json`:
its `success` reads "sealed" and the outcome keys are left out of its row
(`series.py` `cmd_result`, `OUTCOME_KEYS`); it writes and prints nothing but a
refusal for an attempt whose validity `series end` has not written
(`result_before_end`) and for a `run.json` that does not read or lacks
`series` in a series' run directory (`result_run_json_unreadable`). The ledger carries validity
only, never an outcome. The driver prints no outcome. The scorer refuses
(exit 4, nothing read) while the series has not stopped; its one override,
`--before-stop-for-tests`, is written to stderr and to
`score_rate_overrides.log` beside the first run directory.

**What is procedure.** The run directory also holds files that show the
outcome to whoever opens them: `trajectory.jsonl`, `transcript.jsonl`,
`session_stdin.jsonl`, `turns.jsonl`, `end.json`, `play.out`,
`bridge_title_witness.json`, `analysis.txt`, `analysis.json`, `runner.log`
and `watch.log`. None of them is opened, read or summarised by anyone
until the series has stopped. While a series runs, only these are looked at:
the ledger, `run.json` `validity`, `post.tsv`, `driver.log`, `pressure.tsv`,
`accounting.json` (cost), and the hosts' own health (the game process, the
lock, memory). `--before-stop-for-tests` is never used on a series' run
directories; a `score_rate_overrides.log` beside them is a deviation and is
published. The control series starts only after the seated series has
stopped; its outcome is read only after its own stop.

**What these files still show.** The files looked at during a series are
not free of the outcome: an episode that ended before its end date (its line
ended or passed to another dynasty, or a technical truncation) shows as a
shorter run in `driver.log`, `post.tsv`, `pressure.tsv` and
`accounting.json`, and a technical truncation as a validity reason. Whoever
watches them can tell that an attempt ended early, not whether its ruler
held the kingdom. What counts and what is valid is not decided on that:
stopping a driver after its end row, the one act that could turn such a
reading into a choice of which outcomes count, is forbidden (section 4), is
a deviation when it happens, and leaves the attempt's outcome in the report
(section 8).

## 8. Outcomes

**Primary outcome**, per valid run: success, as the scorer reads it for a
task paid once (`score.py` `once_outcome`): the trajectory's end row says the
line held `k_ireland` at some step (`target_title.held_at_any_step`), and the
step rows agree (the first step paying `target_title_first_gained`, or the
title held at the start); a disagreement or a missing end row is `unread`,
listed and not counted. For every success the scorer also reads who created
the title and corroborates it from the bridge log (`creator`, from
`bridge_title_witness.json`, which `series end` writes only when the log is
the one this run's game held): `corroborated` true, false (`unknown` with
`witnesses_disagree`) or not read (`null`), reported per success.

**Estimate**: k/n over the scored set, with its Wilson 95% interval, as the
scorer prints it. The same is reported over the successes the bridge log
corroborated, beside it, as a secondary reading.

**Sensitivity: attempts stopped after their end row.** Beside k/n the scorer
prints k'/n': the same count with every attempt added, with the outcome its
trajectory shows, that is invalid, whose trajectory holds a readable end row,
and whose every validity reason says a record written after the end row is
absent or not final (`witness_absent:end_json`, `file_absent:end.json`,
`witness_absent:end_json_final`, `runner_end:stopped_by_signal`,
`witness_absent:game_end`, `witness_absent:owner_tree`; `score.py`
`STOPPED_AFTER_END`; an attempt with any other reason is not added),
and the run ids of those attempts (`score --json`:
`sensitivity_stopped_after_end`, its `attempts` list). An attempt is added
only when its end row is stamped before the second of its wall-budget
deadline, by the runner's rule (`score.py` `budget_end`) counted from the
start its runner recorded as it started (`runner_start.json`; for a run
directory without that file, its launch, `run.json` `launch_utc`) with its
runner's `--wall-budget-s`; one stamped in that second or later would have
ended `wall_budget`, invalid, and is named and left out
(`end_row_after_budget`), as is one whose times do not read, or whose
`runner_start.json` is there and does not read (`end_row_budget_unread`). Like the scored
set (section 6), k'/n' leaves out an attempt numbered after the scored set's
last run when the set reached its target: valid, it would have been played
out and not scored (named in `after_scored_set`). The published results give
both k/n and k'/n'; when no attempt was stopped there they are equal.

**Secondary outcomes**, described and never tested:

* who created the kingdom: the seat's own call, a standing order the seat
  left, the game's AI for the seat's ruler, another character, or unknown
  (`creator.who`), with the corroboration;
* time to creation: the first-held date and step (`success_date`,
  `success_step`) and the creation date with its source (`creation_date`),
  for each success; whether the kingdom was still held at the end date
  (`held_at_horizon`);
* the reward: each run's return, the trajectory end row's `returns` for the
  seat;
* per run: the counties at the last step, the steps, the wall time, the cost
  as the sessions reported it (the seated series), the invalid reasons of
  every invalid attempt, and the flagged checks;
* per host: k/n, descriptive only (host and agent CLI version are one factor
  here, and neither is analysed).

## 9. The comparison, fixed before data

With the seated series' k₁/n₁ and the control series' k₀/n₀ (each over its
own scored set), as `score --compare seated-score.json control-score.json`
computes them (`score.py` `compare`, `newcombe`, `fisher_two_sided`):

* the difference in proportions, k₁/n₁ - k₀/n₀, with its 95% interval by
  Newcombe's hybrid score method (the square-and-add interval built from the
  two Wilson 95% intervals: lower = d - sqrt((p₁ - l₁)² + (u₀ - p₀)²),
  upper = d + sqrt((u₁ - p₁)² + (p₀ - l₀)²));
* Fisher's exact test of the 2 x 2 table (successes and failures by series),
  two-sided, at the 0.05 level;
* nothing else is tested. If either series is abandoned (section 4), no
  comparison is made.

The comparison is of the seat together with what the bench gives it (its
notes and its scaffold: the goal line, standing orders, forced wakes) against
the game's own AI (section 12).

## 10. Deviations

Any departure from this document after the first attempt of a series is a
deviation. Each is recorded when it happens (what, why, which attempts it
touches) and published with the results. A driver, runner or game stopped
between an attempt's end row and the driver's own end (section 4) is one,
for each attempt it touches. No deviation changes the build, the
task, the stop rule, the outcomes or the analysis: a change to any of those
is a new series under a new document. An attempt is never repeated, deleted
or renumbered. A defect found during a series is recorded and not fixed
until both series have stopped. A run directory is frozen once `series
end` has written its validity and its end marker exists: nothing in it is edited, added, removed, moved or
compressed (an archived `transcript.jsonl.gz`, an added pressure row) until
both series are scored and published; the scorer reads the rule again from
the files (`score.py` `attempt_reasons`: the protected directory, the resource
rule, the run's files) and refuses a series whose files changed after `series
end` in what that rule reads (`series_validity_disagrees`, exit 4), whatever
the override; a change it does not read (a value inside a file that still
reads) is not caught. A file edited by hand at any time (a ledger entry, a
`run.json`) is a deviation; the kit gives a named refusal for a field of the
wrong type, and removing every end field of an attempt by hand lets `series
end` run on it again, which is such a deviation. A record damaged after its
attempt's end (a run directory copied short between the hosts, a disk fault)
makes the scorer refuse the series by name; the run directory is copied again
from the host that played it, and with no good copy the series is reported
unscored with that refusal. A `series_ledger_order` refusal (section 4) is
reported the same way.

## 11. What is published

After both stops: this document as it stood at the first attempt; the four
frozen files (two expectations, two schedules); the two ledgers; the
scorer's output on each series (`score ...` text and `--json`, as the scorer
writes them: each run named by its directory's basename, no bridge log path,
every other absolute path reduced to its basename, host and user names
replaced as in a publishable copy, and the series' task and start identity in
the gate) and the comparison (`score --compare`); and, for
every attempt, the publishable copy
of its run directory that `series publish` writes
([publishing a run directory](harness.md#publishing-a-run-directory)), run on
the host that played it with that host's configuration:

```bash
python3 -m ck3bench.harness series publish --config host-a.toml RUNS/munster-kingdom-seated-a01-a OUT/munster-kingdom-seated-a01-a
```

The copy holds `run.json`, `runner_start.json`, `result.json`, `end.json`, `trajectory.jsonl`,
`turns.jsonl`, `accounting.json`, `pressure.tsv`, `post.tsv`,
`bridge_title_witness.json`, the driver's `done` and `rate_run.done`, the
operator's `operator_withdrawn.txt` or `operator_stopped.txt`, and for the
seated series `transcript.jsonl` (every session's stream-json events) and
`session_stdin.jsonl` (every message sent to a session), each rewritten as
its `PUBLISHED.json` lists. Every withdrawal and cancellation is published
the same way, with its run files and its reason (its ledger entry's code and
the operator's record), so a reader can check that no played attempt was
withdrawn: the copy holds every file `score.py` `played_out` reads.
What the rewrite catches, and nothing else:

* text about an account limit (a usage-limit message, a named limit such as
  a weekly, 5-hour or model limit, how much of it was used, its reset time, a
  rate limit's size, an organisation's limit, a credit balance) is replaced
  whole by a placeholder that keeps the scorer's reading of it (a turn's
  result-text excerpt that hit a limit with it);
* a path behind an escape (JSON's escaped slash, a unicode slash, percent
  encoding to three levels) is read plain first;
* the configured paths, the run directory and any home directory (a `~name/`
  home and a Windows user directory too) become placeholders named for what
  they are; a Windows network path, a share by URL (`smb://`, `cifs://`,
  `afp://`, `nfs://`) and `//server/share` become `<network path>`; paths under
  `/mnt`, `/srv`, `/opt`, `/data`, `/media`, `/scratch`, `/nfs`, `/var/lib`,
  `/Volumes`, `/workspace`, `/net` and `/run/media` become `<private path>`;
  sockets under `/tmp` become `<socket>`, and any other path below a
  directory of `/tmp`, or under `/var/tmp` or `/dev/shm`, `<tmp path>`;
* the values of `USER=`, `LOGNAME=`, `USERNAME=`, `SUDO_USER=`, `HOSTNAME=`
  and `HOST=`, in any capitalisation, and of `"HOSTNAME": ...` written as
  text;
* the machine's host name, in any capitalisation and joined to other words by
  `-` or `_`, becomes `<host name>`, and a user at a machine with no domain, as a
  login names it, `<user>@<host>`; the account's user name (and every name a home path in the
  files carries, three characters or more) becomes `<user name>` wherever it
  stands, the run directory's name included; a host or user name that is
  also an ordinary word is replaced in the seat's and the game's text too
  (each replacement is counted in `PUBLISHED.json`);
* IPv4 and IPv6 addresses, MAC addresses (in dot notation too), `uid=`/`gid=`/`groups=` numbers and
  names, claude.ai session links, and Drive and Docs links;
* in the transcript, each rate-limit event keeps only its status (what the
  resource rule reads) and each init event only its type, model, tools and
  MCP servers; the agent CLI's account and limit fields (and any field of
  their family: a reset time, a field about use beyond the limit, a use
  figure) are
  dropped everywhere; session ids and other UUIDs become `session-N` and
  `id-N`.

A copy is refused, and nothing is written, when a listed file is a symbolic
link or has more than one hard link, or when `run.json`'s host is not a host
label (`a`, `b`). A listed file that is damaged (not UTF-8 text, a NUL byte, a
`.gz` that does not decompress, a `run.json` that is not one JSON object:
what a power loss can leave in a counted attempt) is not read and not
published; the machine names a damaged `run.json` still holds are rewritten
everywhere, and with none there the copy is refused until the operator names
the hosts (`--host-name`); the copy holds a stub,
`<file>.damaged.json`, naming the file, its size, the sha256 of its bytes and
the damage, and `PUBLISHED.json` lists it, so every counted attempt has its
publishable copy (one whose damaged `run.json` holds no machine name once the
operator has named its hosts). The copy, `PUBLISHED.json` included, is then read for every
class above, an e-mail address, an account key and a key or token
(the shapes `publish.py` `SECRET` lists: a private key's header of any kind,
a provider's or forge's token by its prefix, a cloud provider's key id, a
bearer header, a JWT, a value after a name that holds `secret`, `api_key`,
`password`, `credential` or `private_key` (a quoted value whole, spaces
included), and one with a letter after a name that holds `token`; [harness.md](harness.md#publishing-a-run-directory) lists
each), each line also
with its escapes read plain (percent encoding to three levels) and its
base64 runs (both alphabets, and a second level inside the first) and hex
runs decoded; a copy in which any survives is not written (the command exits
5 and names each survivor by its class, file and line). Nothing else is
edited, and the rewrite does not see what it has no rule for (a machine's
name no `run.json` records, a name typed into a seat's text, a private detail
in a game's own text, a host name inside a longer word, a dotted address
after a version word, a value split
across fields or lines): **a person reads every published copy before it is
released**, and a copy that still names something private is not released.
The hosts' accounts have distinctive names: a user or host name that is also
an ordinary word (`will`, `mark`, `king`) is replaced in the seat's and the
game's text as well.

**A refused publish.** When `series publish` refuses an attempt's copy, that
attempt's copy is not published until the refusal is put right: the
survivor it names is added to the rewrite (a change to the publishing code,
recorded as a deviation, section 10, and applied to every attempt's copy
alike) or the copy is written again with the host configuration the run was
played with. The attempt itself, its validity and its outcome are unchanged
and are reported as they are; if no copy can be written, the attempt is
listed with its ledger entry and the scorer's row, and the deviation list
says why its copy is absent.

The kit records no video and no screen, and none is published. A deviation
list is published even when it is empty.

## 12. What the seat is given beyond the task

What the seated series measures is the seat together with what the bench
gives it beyond the task's prompt ([the scaffold](../README.md#scaffold)):

* **the notes set `munster1066once`** (`notes_version` in the expectation):
  its turn-one notes in the first observation and every note through
  `discover(what="notes")`;
* **the goal line**, from the first turn: each turn's observation carries one
  line on `k_ireland` (`world.goal`, `ck3bench/env/ck3.py` `_goal_line`, read
  from the toolkit's `formable_titles`): that it can be created now, with the
  exact creation call; or that it waits only for gold (the price, the gold
  held, the months at the last step's net); or the duchies it still waits on
  and what each costs, with the whole chain's bill; or, in the game's own
  words, what else it lacks; and the death clause (which titles a partition
  at the ruler's death would take, the price after it, and up to three
  succession levers the game allows, each with its call). Once the kingdom
  is first held, the env replaces that line with one saying the reward was
  paid once and play goes on (`ck3bench/env/env.py` `_world`);
* **standing orders**: a turn may leave up to four lever calls the env sends
  once their conditions hold at a check;
* **forced wakes**: a succession in the seat's line and a war declared on the
  seat stop the clock whatever the seat asked;
* the toolkit told the target title and the goal kind (its spending veto never
  stops the game's AI creating `k_ireland` for the seat).

**The notes and the goal line were written from earlier play of this same
start**: seated games and scripted reads of the 1066 Ireland and Munster
starts, before this document. The control series reads none of this (nobody
plays its seat; the game's own AI runs the ruler).

**Earlier seated games of this exact task.** Before this document, 38 seated
games of `munster1066-kingdom-once-30y-wake` were played with a Claude model
on earlier builds of the bench and the toolkit (17 of them as a series under
an earlier plan, the others one or two at a time). Their outcomes were seen,
and the toolkit's tools, the notes and the goal line were changed in response
to them. None of them is an attempt of either series here, and none is
counted in either.

The set's notes, by what they are about:

* **specific to this start or this task**: `munster-once-goal` (the task's
  reward and the creation call for `k_ireland`), `ai-may-create-title` (the
  1066 Irish duchies' minting and claim-war prices), `defender-allies-join`
  (allies that join a defender's war, observed in the 1066 Irish games),
  `no-fabrication-on-kin` (the 1066 start's kin rule as read there);
* **general game rules and tool use**, observed on this start among
  others: `ai-spends-for-you`, `claim-dies-with-ruler`,
  `raised-armies-block-declares`, `claim-paid-on-arrival`,
  `claim-war-prestige-price`, `yield-costs-gold`, `vassalization-size`,
  `piety-into-gold`, `ai-picks-cost-gold`, `guest-activity-blocks-declares`,
  `ally-call-costs-prestige`, `succession-swaps-purse`, `partition`,
  `vows-or-disinherit`, `kingdom-counts-vassals`, `duchy-opens-dejure-wars`,
  `perk-path-ducal-conquest`, `faction-demand-answered-by-ai`,
  `marriage-makes-allies`, `truce-after-any-war-end`, `read-idle-before-ending`,
  `no-detail-refusals`, `notes-pad`.

So the comparison in section 9 measures the seat **together with notes and a
scaffold written from experience of this start** against the game's own AI;
it does not separate what the model brings from what the notes and the goal
line tell it.
