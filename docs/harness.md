# The research harness

`ck3bench.harness` runs a model in a seat of a task, one fresh agent session per
turn, and runs and scores a series of such episodes declared in advance. It
sits on the environment (`ck3bench.env`): the env plays the game and keeps the
trajectory; the harness decides what the seat is told, drives the agent CLI,
watches the run and reads the result. The game-side mechanism is the toolkit,
`ck3agent`.

```
python -m ck3bench.harness run-seat ...    one seated episode
python -m ck3bench.harness prompt TASK     the seat prompt the runner sends for TASK
python -m ck3bench.harness series STEP ... a series run's identity, witness and result steps
python -m ck3bench.harness series run ...  the reference series driver: one attempt, every step in order
python -m ck3bench.harness series publish RUN DEST   a run directory's publishable copy
python -m ck3bench.harness score DIR ...   the success rate, with the validity and stopping rules
python -m ck3bench.harness score --compare SEATED.json CONTROL.json   two stopped series compared (difference, Fisher)
python -m ck3bench.harness accounting DIR  a finished run's per-turn tokens and the sessions' reported cost
python -m ck3bench.harness analyze DIR     a finished run turn by turn: calls, reply sizes, the seat's own text
python -m ck3bench.harness steps TRAJ      one line per env step of a trajectory
python -m ck3bench.harness game-pid DIR    the run's game pid, from what the run recorded (never a process name)
```

`python -m ck3bench.harness` with no verb lists every verb; `series` and
`score` with no arguments print their module's full statement of the rules.
Every verb and every `series` step answers `--help` (or `-h`) with its usage,
reads nothing and writes nothing (`ck3bench/harness/test_cli_help.py` runs
each one); a `series` step given a run directory that does not exist refuses
at once (rc 2).

## Layout

| module | what it is |
|---|---|
| `runner.py` | the seat runner: renders the prompt, starts `env play` with the seat external, writes the shim's MCP config, lists its tools with a real MCP client, then one agent CLI session per turn (stream-json), a watchdog, and the run directory's records. `--policy noop` plays the seat with the env's no-op policy instead (the game's own AI alone): no agent CLI, the same run directory |
| `prompt.py`, `prompts/seat_prompt.md` | the ONE seat prompt template and each task's briefing (who the seat is, where it starts, the task sentence, the reward lines). The numbers (horizon, end date, steps, cycle, wake cadence, reward weights, what holding the title does) come from the scenario |
| `series.py` | one run of a series: the declared switches against the environment, the plan, the launch identity, the start and end witnesses of the game process, the validity classes, the ledger and the next slot, the sealed result |
| `driver.py` | the reference series driver (`series run`): every series step for one attempt, in order, with a lock, a check of the game's environment, a host-pressure sampler, an optional protected-directory digest and the end marker |
| `score.py` | the success reader, the Wilson 95% interval, the creator split, the resource rule, the schedule and the stopping rule; the comparison of two scored series (`--compare`: the difference with Newcombe's hybrid score interval, Fisher's exact test) |
| `publish.py` | `series publish`: a run directory's publishable copy, with host names, paths, account fields and session ids replaced, refused when one survives |
| `game_identity.py` | which game process is this run's, from what the run recorded (never a process name) |
| `accounting.py`, `analyze.py`, `steps.py` | readers of a finished run: per-turn tokens and the sessions' reported cost; turn by turn calls and replies; one line per env step |
| `testdata/` | a stand-in agent CLI (`fake_cli.py`, no model) and three stand-in tasks on the fake backend, for the harness's own tests and dry runs |

The replay of a recorded trajectory is the env's (`ck3bench.env.replay`,
`ck3bench env check`).

## What is not included

The bench ships the harness that produces and scores a series of runs and a
minimal reference driver. It does not ship screen recording and video
delivery, a check of the game's display, a clean-map mod, or anything about
particular machines. Each meets the harness at a hook (below). A hook that is
not configured is recorded as such (`no hold check configured`, `no UI driver
configured`, `not configured`), never as a pass; what that absence does to a
run's validity depends on the hook, and is stated for each one:

* the display check (`RATE_HOLD_CHECK`) and the final save (`RATE_UI`) are
  recorded in `result.json` and do not change validity;
* the protected-directory digest (`RATE_PROTECTED_DIR`): outside a series,
  unset reads `not_configured` and does not change validity; a series attempt
  is refused without it (`protected_dir_not_configured`: the reference driver
  before the lock, exit 2; `series start` for any driver, exit 6); a
  directory digested before the run and again before `series end` that
  DIFFERS makes the run invalid
  (`owner_tree_differs`, in `run.json`, the ledger and the scorer's row), and
  one digested before and not after is `witness_absent:owner_tree`;
* the host-pressure file (`RUN_DIR/pressure.tsv`) absent **voids every run that
  ended `seat_stopped` or `seat_hung`** (see
  [Absent pressure evidence](#absent-pressure-evidence)). The reference driver
  writes it.

The runner carries the hooks a recorder of your own meets it at (`run-seat
--help`): `--before-turns CMD` (run once after the episode opens, before the
first session), `--hold-first-turn-file F` (turn 1 is sent once F exists, at
most `--hold-first-turn-s`), and `--frame-gate-dir D` (before a turn, when
`D/frame_due` exists, the runner writes `D/frame_now` and holds the paused
game until `D/frame_done`, at most `--frame-gate-s`). Each wait is bounded and
never ends an episode; the reference driver passes none of them.

## Running a seated episode

Prerequisites:

* the game and the toolkit, as in the README's "Running on the real game"
  (the toolkit set up with `ck3agent setup`, which records where the game
  binary is; `CK3_BINARY` overrides it); the start save the task names (the
  README's "Starts and saves");
* a display for the game, and the rest of
  [the game's environment](#the-games-environment);
* an agent CLI that takes Claude Code's flags and speaks its stream-json
  (`claude`, logged in; the runner reads no credential), and a model id;
* the `mcp` Python library (the toolkit's dependency).

```
python -m ck3bench.harness run-seat --out runs/try-1 \
    --scenario munster1066-kingdom-once-30y-wake --saves-dir ~/saves \
    --claude claude --model claude-opus-5-5 --turn-timeout-s 1200 --wall-budget-s 28800
```

`python -m ck3bench.harness run-seat --help` lists every flag. `--model`
defaults to the alias `opus`, which the agent CLI resolves to a model of its
choosing; name a full model id for anything you will compare. The session gets
no built-in tools, only the shim's MCP server, only the `local` settings source
in an empty working directory, the rendered prompt as its system prompt, and
no session persistence; its init event is checked against the shim's tool
list. On the fake backend, `--scenario
ck3bench/harness/testdata/standin-ireland-fake.toml --claude "python3
ck3bench/harness/testdata/fake_cli.py"` plays a whole episode with no game
and no model.

**Which tasks it can brief.** The runner sends a task only with a briefing
(`ck3bench/harness/prompt.py`): the ten Ireland, Munster, Francia, Sweden,
Lorraine and Carinthia tasks and the four decision tasks, and the stand-in
tasks under `testdata/`. The four `france1066-*` tasks and the fake tasks
have none: `python -m ck3bench.harness prompt TASK` refuses them, and
`run-seat` ends `setup_failed` before anything starts.

**What the seat is told about scoring.** The seat prompt states the task's
reward terms and ends "Your return is the sum. Nothing else is scored." (a
line task: "The goal above is judged apart from it"). The four decision tasks
(`ireland1070-target-choice-3y`, `ireland1075-succession-5y`,
`ireland1075-faction-3y`, `ireland1076-succession-death-4y`) also declare an
`outcome` (`target_choice`, `succession`, `faction`): a measure of the one
decision each start was built for, which the env computes at the episode's
end from its own reads and the seat's applied calls and writes into the
trajectory's end record (`outcome` and `outcome_end`; the start read is the
header's `outcome_start`, and each applied `declare_war` record carries a
`witness`). It is not a reward term: it is never added to a step's reward or
to the return, and the harness's scorer does not read it (a series of one of
these tasks scores its kingdom goal, as for any Ireland task: holding
`k_ireland` within three to five years from a count's realm, which no task
declaration estimates as reachable; `ireland1066-kingdom-30y`'s declaration
puts the kingdom at eight counties and 850 gold and judges fifteen years from
the 1066 start too short for them, and the README's task table gives each
decision task's start). **The seat is
not told about it**: its prompt is the Ireland task's from a game under way,
names no decision, and says "Nothing else is scored", which is true of the
return and not of what the task records.

`end_reason` (in `end.json`): `episode_over` (the env ended it: horizon, goal,
line ended; its end row stamped before the second of the wall-budget deadline,
judged by the row's own time in either arm, and the post-episode hold is then
cut where the budget runs out, `hold_cut_by_wall_budget`), `seat_stopped` (a reply ended without end_turn after one
reminder), `seat_hung` (no stream event for twice the turn timeout),
`seat_exited`, `rate_limited`, `surface_mismatch`, `no_init_event`,
`wall_budget` (the budget ran out with no end row, or the end row is stamped
in the deadline's second or later; `budget_judged` records the row's time and
the deadline), `env_unreachable: ...`, `setup_failed: ...` (nothing started,
e.g. a task the prompt cannot describe), `runner_error: ...`,
`stopped_by_signal`, and, with `--policy`, `env_failed: rc N` (`env play`
exited without its end line). In a series, every one of these except
`episode_over`, `seat_stopped`, `seat_hung` and `env_failed` makes the run
invalid (`runner_end:<reason>`); `env_failed` is read through the trajectory
(a run with no end row has no success to read).

## Running and scoring a series

A series is a frozen schedule and a frozen expectation, both written before
the first attempt:

* the **schedule** (a JSON file whose `schema` is `ck3bench.series.schedule/1`): the
  series id, the task, the host pattern (attempt *a* plays on
  `pattern[(a - 1) % len]`; a host is named by a label of one letter, or a
  letter and a digit: `a`, `b`, `h1`, never the machine's name, since the
  label stands in every run id and published copy), the valid target and the
  attempt cap. The **stopping rule**: the series stops at the valid target or
  the attempt cap, by validity alone; an attempt withdrawn or played while the
  series was cancelled is numbered and not counted. Validity is one reading
  with three readers: `series end` writes it once into `run.json` and the
  attempt's ledger entry (the identity check below, plus `score.py`
  `attempt_reasons`: the protected directory and the resource rule); each
  attempt then has one standing (`score.py` `standing`: not an attempt, in
  flight, valid or invalid), which the driver's slot check reads from the
  ledger and the scorer from the run directories, and the scorer reads
  `attempt_reasons` again and refuses a series whose record disagrees (below);
* the **expectation** (`ck3bench.series.expect/1` JSON, from `series declare` on the
  staged tree, reviewed and frozen): the task, the declared switches, and the
  identity every run must match (the bench tree's commit, the bridge and wheel
  hashes, the map's hash, the game build id, the agent CLI version, the start
  save's hash, the rendered prompt's hash, the model, the hold), and,
  optionally, three values read from a check run (`notes_version` from its
  trajectory header's `provenance.notes`, `tool_listing_sha256`,
  `seat_model_reported` from its `session_init.json`). `declare` writes those
  three as placeholders; a placeholder left in the file refuses an attempt
  (`expect_not_frozen`), a check run leaves it out (`run.json`
  `expectation.unfrozen` lists them), and a key left out is not held. With
  `RATE_POLICY` set (a `--policy` series) `declare` writes no agent CLI
  version and neither session value. An identity value `declare` cannot read
  (a file that is not there, a binary that does not answer) is written as a
  `<FREEZE: unread at declare (why); where a check run's run.json has it>`
  placeholder, never left out, so an attempt refuses the file until it is
  filled. `timings` holds the driver's four timings (`turn_timeout_s`,
  `step_wait_s`, `wall_budget_s`, `wall_s`: `RATE_TURN_TIMEOUT_S`,
  `RATE_STEP_WAIT_S`, `RATE_WALL_BUDGET_S`, `RATE_WALL_S` at `declare`, else
  the reference driver's defaults); a series attempt runs with them. A `hosts`
  table gives each host its agent CLI version (`{"a": {"claude_version":
  "..."}}`) and nothing else (another key refuses the file:
  `expect_hosts_key`). **One expectation per series**: freeze it, `hosts`
  table included, before the first attempt and never edit it after; every
  ledger entry records the sha256 of the file its attempt started with, and a
  start with another file is refused (`series_expectation_differs`; an entry
  that records none, `series_expectation_unwitnessed`).
  The expectation's `kind` is `rate`: an attempt plays it, and so does a
  check (which reads the values it will freeze); an attempt refuses an
  expectation declared `kind: check` (`expect_kind:check!=rate`).
  `series declare --config host.toml` reads the same host configuration as
  the driver (every `series` step but `run` takes `--config`; `run` takes it
  as its own option).

**What a check run costs.** A check (`series run --check`) plays the task
exactly as an attempt does: without a shorter budget it plays the whole task
and pays for every turn (the 30-year Munster task: its whole 30 game-years of
clock, and about 10 USD of model use per run, as the sessions reported it). The
three values are all written by the first turn, so a short budget is enough:

```bash
python3 -m ck3bench.harness series run --host a --expect expect.json --check --run-id check-1 \
    --wall-budget-s 900
python3 -c 'import json,sys; s = json.load(open(sys.argv[1]))["end"]["seen"]; \
  print({k: s[k] for k in ("notes_version", "tool_listing_sha256", "seat_model_reported")})' $RATE_RUNS/check-1/run.json
```

`--wall-budget-s 900` ends the check no later than about 15 minutes after the
runner starts (`runner_end:wall_budget`, unless the episode ends first, as a
do-nothing check on a fast host can; a check is never scored, so its validity
does not matter): the game's launch and as many turns, and sessions' cost, as
fit in that time.
The budget is checked before each turn (after the episode's end row, which
is read first and judged by its own time), so it must be longer than the game
takes to open; a value read as `null` means no turn ran, and a longer budget
is needed. Run the check from the declared file as it is (its placeholders
are left out), then put the values in and freeze it. A check that is refused
at its start leaves nothing in its run id's place (below), so the same
command can be run again.

Each attempt is one run directory, driven in this order (the reference driver
does all of it; any driver must do the same). Whatever the driver, `series
start` holds the runner's timing flags (`run.json` `runner_flags`:
`--turn-timeout-s`, `--step-wait-s`, `--wall-budget-s`) to the expectation's
`timings` (`expect_differs:timings.<key>`, a flag missing
`witness_absent:runner_flag:<key>`); `wall_s` is the driver's own limit on the
runner process, which no flag carries:

1. `series switches`: refused unless every switch in the environment equals
   the expectation's; prints the `export` / `unset` lines for the run;
   `series plan`: the task's seat and steps;
2. `series slot HOST RUN_ID`: refused unless RUN_ID is the next slot of HOST
   and every lower attempt has started (the ledger). The reference driver
   also refuses here, before the lock: timing flags other than the
   expectation's `timings` (exit 5), `--agent-cli` or `--session-env` in a
   series (exit 2: an attempt runs `RATE_CLAUDE_BIN`, whose version the
   identity holds), and `RATE_PROTECTED_DIR` unset (exit 2);
3. take the lock (see [the reference driver](#the-reference-driver) for what
   its default excludes);
4. `series start OUT`: the launch identity in `run.json`; refused when it
   differs from the expectation (exit 6: no game starts, no ledger entry is
   written, and the reference driver moves the run directory to
   `RATE_RUNS/refused-starts/RUN_ID-UTC` with `start_refused.txt`, the
   refusal, so the same slot can be retried; scored, that directory is
   `start_refused`, not an attempt); it also refuses an attempt whose
   expectation is not the file the ledger's attempts started with, and an
   attempt with no `RATE_PROTECTED_DIR`; a series attempt is entered in the
   ledger with its expectation's sha256;
5. start sampling host pressure into `OUT/pressure.tsv`; digest the protected
   directory into `OUT/own.before.*`; write `OUT/ck3.before`;
6. `series watch OUT` in the background, `run-seat` in the foreground; then
   `OUT/done` and `accounting OUT`;
7. digest the protected directory into `OUT/own.after.*`;
8. `series end OUT`: the same identity read again, the witnesses, the
   trajectory's speed and check dates, the protected directory's two digests,
   the resource rule (it first waits, at most about a minute, until the stop
   window's padding has passed, so every sample the rule reads is written),
   and `validity`, written to `run.json` and then to the ledger entry. A run
   file that is absent or does not read is a reason of its own
   (`file_absent:<file>`, `file_unreadable:<file>`, below), and a reading of
   the attempt's own run files that fails with an error no reader expected is
   `end_failed:<error>`: a played-out attempt that `series end` reaches gets a
   validity unless `run.json` cannot be written (`end_unwritten`), the
   expectation or the schedule it names does not read
   (`end_expect_unreadable`, `end_schedule_unreadable`; an expectation that is
   a JSON value other than an object does not read, nor one, or a schedule,
   nested more than 32 levels deep, which `series start` and the driver refuse
   by the same count), the expectation is not
   the file `series start` read (its sha256 against `run.json`
   `expectation.sha256`: `end_expect_differs`), this host's identity
   does not read (`end_host_unreadable`), `run.json` has no `declared` block
   (`end_declared_absent`) or the ledger does not read
   (`series_ledger_unreadable`, `end_ledger`). An expectation, a schedule, a
   host identity or a `declared` block that does not read is a refusal that
   writes nothing, so the step runs again once it is put right; `series start`
   records the expectation's and the schedule's paths absolute. A `run.json` that does not read
   after `series start` entered the attempt in the ledger (absent, cut, a NUL
   byte, another JSON type, a field of another type) gives the attempt no
   `run.json` validity: `series end` writes its ledger entry ended, invalid
   (`file_unreadable:run.json`, or `file_absent:run.json`), counted, and the end
   marker, and the ledger entry is then the attempt's record. It is written
   once, for a played-out attempt only: `series end` refuses (exit 6, nothing
   written) an attempt whose `run.json` has its validity, that has
   `result.json`, that is withdrawn, whose ledger entry has ended or carries an
   end's fields (`ended_utc`, `valid`, `reasons`), or that has no ledger entry
   (`end_ended`; `end_no_start` for a `run.json` that does not read and no open
   entry), and one that was not played out (`end_not_played`; see [a driver
   stopped mid-attempt](#a-driver-stopped-mid-attempt));
9. `series result OUT`: `result.json`. In a series it is **sealed**: no
   outcome is written or printed until the series has stopped, and none is
   written or printed for an attempt without the validity `series end` wrote:
   `result_before_end` while `run.json` has none, `result_run_json_unreadable`
   when `run.json` does not read or (with `RATE_SERIES` set, or a run directory
   the ledger names) has no `series` (exit 6, only the refusal printed);
10. stop the sampler, release the lock (unless the run's game is still
    alive: see [the reference driver](#the-reference-driver)), and write the
    end marker `OUT/rate_run.done` only when steps 8 and 9 both exited 0, or
    step 8 recorded a `run.json` that does not read in the ledger entry (step 9
    is then not run) (the reference driver otherwise writes no marker, exits 8
    and names the command that completes the attempt). **Without the end marker the scorer never
    counts the attempt as ended, so the series does not stop and `score`
    refuses (exit 4).** An attempt with `run.json`'s validity and no marker
    (its driver stopped after step 8, or step 9 failed) is closed by `series
    close OUT`: it writes the ledger entry from that validity when it is
    missing, `result.json` when it is absent, and then the marker. It refuses
    (exit 6) an attempt `series end` has not reached (a played-out one: run
    `series end OUT` by hand first; one not played out is withdrawn), one that
    is withdrawn, one whose ended ledger entry disagrees with its `run.json`,
    one that has its marker, `result.json` and its ended entry, and one whose
    driver or recorded game still runs. The scorer's refusal names the
    attempts that need it.

The **validity classes**: a run is valid only when every end value equals its
start value and every declared expectation. Each failure is a reason code:
`unread:<key>` and `witness_absent:<key>` (nobody could read it),
`expectation_not_declared`, `tree_dirty`, `expect_differs:<key>`,
`wheel_install_differs`, `start_end_differs:<key>`, `*_differs_start` /
`*_differs_end` (a witness changed), `game_pid_changed`,
`prompt_differs_from_launch`, `notes_witness:<verdict>`, a run file absent
or not read whole (`file_absent:trajectory.jsonl`, `file_absent:end.json`;
`file_unreadable:<file>` for the trajectory, `end.json`, `run.json` or
`result.json` when it is not JSON of its kind: a line cut short, a NUL-filled
tail, another JSON type, bytes that are not UTF-8, a trajectory with no row;
`file_unreadable:end.json` also when its `end_reason` is not text or its
`notes_witness` not an object, `file_unreadable:run.json:launch_utc` when the
launch time is not a UTC time; a field left out is not damage of its file),
a reading of `series end` that failed (`end_failed:<error>`), the runner's own
failures (`runner_end:rate_limited`, `runner_end:setup_failed`,
`runner_end:seat_exited`, ...), the env's technical truncations
(`technical_truncation:game_exited`, `bridge_unavailable`, `clock_stalled`,
...), `speed_differs`, `checks_off_beyond_cadence`, `unread:success`, and
`owner_tree_differs`: the protected directory hashed before and after the run
differed (`witness_absent:owner_tree`: digested before and not after). The **resource rule**: a run that ended `seat_stopped` or
`seat_hung` is void, not a failure, when its stop window shows an account rate
or usage limit, an API error, or host pressure over the thresholds below; the
evidence being absent voids it too (next section).

Score with every run directory of the series:

```
python -m ck3bench.harness score runs/s-a01-h1 runs/s-a02-h2 ... [--ledger DIR] [--json]
```

A series is scored against its ledger (`--ledger DIR`, else `RATE_LEDGER`,
else `<series>.ledger` beside the first run directory): put both hosts' run
directories in one place with a ledger holding every entry (each host's
ledger has them all once the entries are copied after each start and end).
The scorer refuses (exit 4, nothing read, whatever the override) when an
attempt the ledger names is not among the directories given
(`series_attempt_missing`), when a directory given is no ledger attempt
(`series_attempt_not_in_ledger`), when the ledger is absent
(`series_ledger_absent`), when the attempts played more than one
expectation (`series_expectation_differs`), when a ledger entry and its
run directory disagree on its validity or on whether it is an attempt
(`series_ledger_disagrees`, its reasons included), when a ledger entry does
not read (`series_ledger_unreadable`: a file name other than the one `series
start` writes, `attempt-NN.json` with at least two digits, so `attempt-1.json`
beside `attempt-01.json` is refused by name; not one JSON object; no `attempt`
equal to its file's number; a field of another type; or no `started_utc` UTC
time, a time with no zone included), or started more than 300 s before the
same host's attempt numbered before it (`series_ledger_order`: that host's
clock was set back, or an entry was removed and its slot played again after
the host's next attempt started). The hosts' clocks are never compared with
each other: across hosts the order of a series is its attempt numbers, which
the slot gives, so two hosts whose clocks differ by any amount score; a
host's own clock must not be set back by more than 300 s during a series
(keep it synchronised), since nothing repairs a frozen ledger. It also
refuses when a run directory among a series' has no `run.json`
(`series_run_json_absent`) or one that does not read
(`series_run_json_unreadable`): the table below says which command applies,
and an attempt whose ledger entry `series end` ended for that damage is read
from that entry, when a
`run.json`
validity disagrees with `attempt_reasons` read again (a reason read now that
it does not record, or one of those reasons it records that is no longer read:
`series_validity_disagrees`), and when the run directories mix a series with
runs of no series, or two series (`series_mixed`).

No peeking: the scorer refuses (exit 4, nothing read) while the series has not
stopped; `--before-stop-for-tests` overrides that and is logged (stderr, and
`SCORE_RATE_OVERRIDE_LOG`, default `score_rate_overrides.log` beside the first
run directory). Success is read from the trajectory (the seat's
`goal_reached`, or for a task paid once the env's latch and the step rows,
which must agree), never from a return or from prose; `k/n` counts valid runs
only, with a Wilson 95% interval. A series scores its first `valid_target`
valid runs by attempt number; a valid run that was still playing when the
target landed is listed (`played_out_not_scored`), not scored, and is an
attempt. `--json` also writes the gate (`gate`: the series, whether it had
stopped, whether the override was used, and its `identity`: the task, the
start save's sha256, the map's sha256 and the game build id, one value over
its attempts as each `run.json` start identity read them, with the kit's
commit, wheel members and bridge beside them; the text output prints the same
as an `identity:` line). The output names each run by its directory's
basename; `--json` keeps no bridge log path, reduces every other absolute
path to its basename and replaces in its free text (an operator's record, a
reader's message) the host and user names `series publish` replaces, so it is
published as written. `score --compare
SEATED.json CONTROL.json` compares two such outputs and refuses anything else:
each must be one stopped series scored without the override, the first seated
(every run played by an agent CLI), the second the control (every run a
`--policy` one), and both of one task from one start (the four identity
values equal and each one value: `identity_differs:<key>`,
`identity_unread:<key>`).

**The game's own AI as the baseline** (no model): the same series path with
`run-seat --policy noop` (the driver's `--policy noop`). The seat prompt is
still rendered (the same task identity), never sent; no agent CLI starts;
`end.json` says `turn_mode: policy`. Such a run has no agent session, so it
writes no `tool_listing.json` and no `session_init.json`: `series end` records
both as not applicable (`run.json` `end.seen`) and gives no reason for their
absence, unless the expectation declares `tool_listing_sha256` or
`seat_model_reported`, which is then held and reads absent. It starts no agent
CLI either: its identity records `claude_version` as not applicable, a
declared version is not held, and the driver needs no `RATE_CLAUDE_BIN`, so
the hosts of a baseline series may have different agent CLIs. Declare a
baseline's expectation with `RATE_POLICY=noop` set: it then names no agent
CLI version and neither session key.

### A driver stopped mid-attempt

**What the kit guarantees.** A crash or a kill at any moment of a series, a
power loss at any moment but the one named below, and any file either leaves
truncated, empty, missing or not UTF-8, never gives a traceback from a
`series` step, the scorer or `series publish`, never changes whether a played
attempt counts, and has a recovery in the table below (or, for a record
damaged after its attempt's end, a named refusal of the score: copy the run
directory again from the host that played it). The tests named after the
table check it over the states and file shapes they reach. What a power loss
can take is what had not reached the disk: every record that decides whether
an attempt was played out is flushed to the disk before the writer goes on
(the `series` steps' records, the runner's `end.json`, the driver's `done`
and end marker, and the env's end row in the trajectory); the trajectory's
other rows, the transcript, the turns file, `post.tsv` and `driver.log` reach
the disk in the kernel's own time, so a power loss can cut their last
seconds. Two moments are left, each the time one flush of a file takes: a
power loss while the env's end row is being flushed can lose that row, and
one while the runner's first `end.json` (for an end the runner decided) is
being flushed can lose that record; the attempt then reads as not played
out. A file edited by hand is outside that
guarantee and is a deviation of the series (the series' document, section
10); a field of another type than the kit writes, anywhere in `run.json`
(its `start` and `end` identity included), `end.json` or a ledger entry
(`score.read_run_json`, `score.ledger_files`, the one reading of each that
every step and the scorer go through), gives a named refusal, never a
traceback (`test_series_damage.py` sets every field the kit writes in them,
to three levels, to each JSON type and runs every command). Removing every end field by hand
(the ledger entry's `ended`, `valid`, `reasons`, `ended_utc`, `run.json`'s
validity, the end marker and `result.json`) lets `series end` run again: that
is such a hand edit.

The `series` steps write each of their records (`run.json`, a ledger entry,
`result.json`, the operator's records, the end marker, and the watcher's
`witness_start.json`, `witness_end.json`, `witness_end2.json`,
`framecheck_hold.json` and `final_save.json`), and the runner and the driver
write `end.json`, `done` and the end marker, to a temporary file, flushed to
the disk and renamed over it (`ck3bench.env.durable`), so a writer stopped at
any point leaves each record as it was before or after the write (the copy
of the final save in `final_save/` is written as it goes). The trajectory,
`post.tsv` and `driver.log` are written as they go (the trajectory's end row
flushed to the disk as it is written): a trajectory a host left cut short is
read as damaged (`file_unreadable:<file>`), and a transcript or a turns file so cut as
evidence the resource rule cannot read (for a stop or a hang,
`resource_evidence_absent:transcript|window`). What to run, on the attempt's
host, once its driver and its game have exited (`series withdraw` and `series
close` check both: the game processes the run's witnesses recorded, and the
driver that holds the lock `driver.log` names):

| the attempt's state | the command |
|---|---|
| its run directory exists, the ledger has no entry for it (stopped during `series start`) | move the directory to `RATE_RUNS/refused-starts/RUN_ID-UTC` and write `start_refused.txt` in it saying why; the slot is free, and the directory scores as a refused start |
| the ledger entry is open, `run.json` has no validity, and the game was not played out (no end row in the trajectory, no `done`, no `end.json` other than `stopped_by_signal`, no end marker: the runner and the driver were stopped from outside before the episode ended) | `series withdraw OUT` (`--cancelled` when the series is cancelled); a withdrawal stopped between its two writes: the same command again |
| the ledger entry is open, `run.json` has no validity, and the game was played out (the trajectory's end row, `done`, an `end.json` of any other end or one that does not read, or the end marker; a kill or a stop in the post-episode hold included) | `series end OUT` by hand, then `series close OUT`; `series withdraw` refuses it (`withdraw_played`) |
| the ledger entry is open and `run.json` does not read or is gone (`series_run_json_unreadable`, `series_run_json_absent`; `series withdraw`, `series close` and `series result` refuse it by name), played out or not | `series end OUT`: it writes the end marker, then the ledger entry ended, invalid (`file_unreadable:run.json` or `file_absent:run.json`), counted; `run.json` is left as found, and the scorer reads the attempt from its ledger entry; stopped between the two, the same command again |
| a refused-starts directory the ledger names (stopped between the start's ledger entry and its return) | `series withdraw` on that directory; the scorer reads it as that attempt, not an attempt |
| `run.json` has its validity, and the end marker is missing or the ledger entry is open | `series close OUT` |
| a ledger entry does not read (`series_ledger_unreadable`, from the slot, the steps and the scorer) | restore it from the other host's copy of the ledger; with no copy, write it again by hand as `attempt-NN.json` (the attempt number with at least two digits: `attempt-01.json`) from `run.json` (its `series.attempt`, `host`, `run_id`, `start.at` as `started_utc`, `expectation.sha256`; at the end, its `validity`), recorded as a deviation; a second file for the same number (`attempt-1.json`) is removed |
| `series start` refuses `start_clock_behind` (this host's clock reads more than 300 s before its own latest start in the ledger; the line prints both times) | if this host's clock is behind, set it right, then start again; if it is right, that earlier start was stamped by a clock that was ahead: wait until the time the line prints, or correct that ledger entry's `started_utc` by hand and record the correction as a deviation of the series. Either way the refused start is moved to `refused-starts/` as in the first row |
| the scorer refuses `series_ledger_order` (a host's start more than 300 s before the same host's earlier start) | none in the kit (`series start` refuses such a start in its first second, so this is a ledger edited by hand or a slot played again); the series is reported with that refusal as a deviation (the series' document, section 10) |

**Played out, or not.** An attempt is played out once the env has written
its end row into the trajectory (the episode's end, which fixes its outcome;
the env then holds the game 600 s; a row that does not read whole, a last line
cut short, is no end row, and an end row of `closed` or `abandoned_by_reset`
is the env closed early, not the episode's end), its driver has written
`done` (its runner returned to it), its runner has written `end.json` with
any end but `stopped_by_signal` (an `end.json` that does not read counts as
played out), or `series end` has begun (`bridge_title_witness.json`, `run.json`'s
validity, `result.json`, the end marker) (`score.played_out`): a played-out
attempt is ended by `series end`, then `series close` when its driver stopped,
and only an attempt not played out (its runner and driver stopped from
outside before the end row: the operator's stop, or its host going down) is
withdrawn. The runner writes `end.json` FIRST when it ends an episode, before
it waits for the session and closes the game (`final: false`; written again,
whole, with `final: true` once both are closed): an end the runner decided
(the seat exited, stopped or hung, the wall budget ran out) is on the disk
before the env's end row reads `closed` (the runner closing it), so such an
attempt is played out whatever happens to its host while it closes.

Do not stop a driver between its attempt's end row and its own end: through
the 600 s hold, while the runner closes the game, and through the driver's
own `series end` and `series result`. A kill, a Ctrl-C or a shutdown there
does not change that the attempt was played out and counts toward the
attempt cap. It does make the attempt invalid: a record written after the
end row is then absent or not final (`witness_absent:end_json_final`:
`end.json` was not written again once the session and the game were closed;
`witness_absent:end_json` with `file_absent:end.json`: no `end.json`;
`runner_end:stopped_by_signal`: the runner was stopped before it saw the
episode's end; `witness_absent:game_end`: the watcher's end witness of the
game is missing; `witness_absent:owner_tree`: the protected directory's
after-digest, which the driver takes only on its own way through, is
missing). These six are the whole class (`score.py` `STOPPED_AFTER_END`). A
Ctrl-C reaches a seated runner in the hold only through the driver's kill
after its 300 s wait, so a late one leaves `end.json` whole and the attempt
invalid by `witness_absent:owner_tree` alone. Such an attempt is a deviation
of the series (its document, sections 4 and 10). `score` prints, beside k/n
over the valid attempts, the sensitivity line `k'/n'` that adds, with the
outcome its trajectory shows, every attempt that is invalid, holds a
readable end row and has no validity reason outside that class, and names
them (`score --json`: `sensitivity_stopped_after_end`, `attempts`); an
attempt with any other reason as well is not in it. An attempt is in it only
when its end row is stamped before the second of its wall-budget deadline by
the runner's rule (`score.budget_end`), counted from the start the runner
counts its own deadline from (`runner_start.json` `start_s`, which the runner
writes as it starts; in a run directory without that file, from `run.json`
`launch_utc`, stamped before the runner starts) with the runner's
`--wall-budget-s` (one stamped in that second or later is named in
`end_row_after_budget` and left out, one whose times do not read, a
`runner_start.json` that is there and does not read included, in
`end_row_budget_unread`), and, as the scored set, not
when it is numbered after the last run of a scored set that reached its
target (`after_scored_set`). The published results
give both. `series end` run by hand reads the attempt as its driver would:
the arm (a `--policy` run or a seated one), the model, the task and its
speed, the expectation and the host label come from what `series start`
recorded in `run.json` (`declared`), not from the environment of the step.

The reference driver stopped by a TERM or a Ctrl-C stops its runner (the
runner takes the first signal and ignores any further one, so its clean end,
which closes `env play` and its game, runs whole), then names the command for the attempt's state in `driver.log`,
`post.tsv` and on stderr, and exits 143 or 130. A host withdrawn while its attempt plays, or a series cancelled
then, is handled in this order and no other: stop the driver, wait until the
game has exited, then `series withdraw OUT [--cancelled]`. It refuses while
either runs (`withdraw_alive`; a lock whose holder pid now belongs to another
program reads as running, and the refusal says how to check it), once `series
end` has written the validity (`withdraw_ended`), and once the attempt was
played out (`withdraw_played`).
`ck3bench/harness/test_series_states.py` runs every order of these commands
on a three-attempt stand-in series on two hosts (a `series end` whose reading
raises, a trajectory cut after an end, and a driver stopped after the end row
with no `end.json`, with `end.json` `stopped_by_signal`, with the end row cut
short, and with the runner's own end recorded (`final: false`) and a `closed`
end row, among them), each command (and the
driver) also stopped before each of its writes while the other attempts are
finished or not begun, and checks that the slot and the scorer read one count
(an attempt left for `series close` counted as it recorded, and named by the
scorer) and that from every state it reaches a way on is left or the scorer
refuses by name (a file damaged after its end). `test_series_damage.py` lays
each run file and the ledger entry in eight damaged shapes (absent, empty, cut
mid-line, mid-string and mid-number, a NUL-filled tail, another JSON type, not
UTF-8) before and after the attempt's end and checks that no step, the scorer
and `series publish` raise, that every refusal names its code, and that a
damaged file the validity reads is in it or refuses the score; it also runs
the end row followed by a kill, an interrupt and a TERM, the runner's own end
(the seat exited, stopped or hung, the wall budget) with its host killed while
it closes the game, a `run.json` damaged after its start (ten shapes, the
table's row followed as written), `series result` on a `run.json` that does
not read, a second ledger file for one number, every field the kit writes in
`run.json`, `end.json` and the ledger entry set to each JSON type against
every command, a run directory named `.`, lock files that are not text, and a
host clock an hour behind and ahead. `test_runner.py` sends a playing runner
two TERMs with no gap between them. Those are the states and shapes the tests
reach; others are not proven.

**A run directory is frozen once `series end` has written its validity and its end marker exists.** Nothing in it
is edited, added, removed, moved or compressed until the series is scored
and published: the scorer reads `attempt_reasons` again from the files, so an
archived `transcript.jsonl.gz` or a pressure row added inside the stop window
refuses the series (`series_validity_disagrees`), whatever the override, and
a withdrawal record put into an ended attempt refuses it too
(`series_ledger_disagrees`).

## Absent pressure evidence

This rule is part of every series' scoring and is not configurable:

* A run whose runner ended `seat_stopped` or `seat_hung` is judged by the
  **resource rule** before its outcome is read. Its stop window runs from the
  stopping turn's `sent_utc` (`seat_stopped`) or `ended_utc` (`seat_hung`) in
  `turns.jsonl`, else from `run.json`'s `launch_utc`, to `end.json`'s `at`,
  widened 60 s each side.
* The run is **void** (invalid, not a failure) when, in that window, the last
  session's transcript shows a rate or usage limit
  (`resource_limited:rate_limit`) or an API error
  (`resource_limited:api_error`), or any one `pressure.tsv` sample is at or
  over one threshold: memory full avg10 >= 10 %, io full avg10 >= 25 %, cpu
  some avg10 >= 75 %, MemAvailable < 1 GiB, or the game in `D` state for 2
  samples in a row (`resource_limited:host_pressure`).
* The run is **also void** when the evidence is absent or unreadable:
  no `transcript.jsonl` (`resource_evidence_absent:transcript`), no stop
  window (`resource_evidence_absent:window`), or no `pressure.tsv`, an empty
  one, no sample inside the window, or every sample in it `NA`
  (`resource_evidence_absent:pressure`).
* **Consequence for k/n:** a driver that writes no `pressure.tsv` has every
  stop and every hang voided. They leave n instead of counting as failures,
  so k/n reads higher than it would with the evidence; such runs are still
  attempts and count toward the attempt cap. The scorer's summary line says
  how many runs were voided for absent evidence (`; N voided for absent
  resource evidence, not failures`) whenever there are any.
* Any other end reason is untouched by this rule. The runs voided for a rate
  limit or an API error are listed by the scorer
  (`voided_for_limit_or_api_error`, and a line under its table).

The reference driver's sampler writes `pressure.tsv` every `RATE_SAMPLE_S`
seconds (30 by default) from the start identity to the end, so its stops and
hangs are judged rather than voided by absence. On a host without
`/proc/pressure` (PSI) the PSI columns read `NA`; the MemAvailable and game
state columns still judge the window.

## The reference driver

```
python -m ck3bench.harness series run --host HOST --series SCHEDULE --expect EXPECTATION \
    [--run-id RUN_ID] [--config host.toml] [--policy noop] [--check] [--agent-cli CMD] \
    [--session-env NAME] [--paired RUN_ID] [--turn-timeout-s S] [--step-wait-s S] \
    [--wall-budget-s S] [--wall-s S] [--dry-run]
```

It is `ck3bench/harness/driver.py`: it runs the harness's own steps as child
processes, in the order of [the list above](#running-and-scoring-a-series),
and reimplements none of them. Its tests
(`ck3bench/harness/test_driver.py`) run a whole two-attempt series on the fake
backend through the lock, the ledger, the sampler, validity, the stop and the
score; `ck3bench/harness/test_prereg_sequence.py` runs the declared series'
commands in [its document](prereg-munster-kingdom.md#3-commands)'s order on
the stand-in, with the differences its docstring lists (a short declared wall
budget, a stand-in agent CLI as `RATE_CLAUDE_BIN`, a small schedule).

* The host configuration is the environment (next section) or `--config FILE`,
  a TOML file with one `[env]` table of strings; a variable already in the
  environment wins.
* `--run-id` defaults, in a series, to this host's next slot's id
  (`<series>-aNN-<host>`).
* The task is `RATE_SCENARIO`, else the expectation's `task`, else the
  schedule's. The model is the expectation's `model`, else `RATE_MODEL`; there
  is no default model. The game speed is the expectation's `game_speed`
  (passed as `--game-speed`, which aligns a wake task's checks).
* The timings' defaults: `--turn-timeout-s 1200`, `--step-wait-s 900`,
  `--wall-budget-s 27900` (the runner's own budget) and `--wall-s 28800` (the
  driver's limit on the runner process). In a series they are the
  expectation's `timings` (`series declare` writes them), and a flag naming
  another value is refused (`expect_differs:timings`, exit 5); an expectation
  with no `timings` is refused in a series (`expect_timings`, exit 2).
* `--agent-cli CMD` and `--session-env NAME` (a stand-in agent CLI and its
  mode) are for runs outside a series: in a series they are refused
  (`series_agent_cli`, exit 2), since the identity records the version of
  `RATE_CLAUDE_BIN` and an attempt runs that binary.
* The kit switches `RECORD`, `VIEW` and `CLEANMAP` must be off (`0`, empty,
  `0`) in the expectation's `kit` table: this driver records no video, sets no
  camera view and installs no map mod, and refuses (`kit_switch_unhonoured`,
  rc 5) rather than record a switch it did not act on. `NOTES` is passed to
  the runner as `--notes`.
* **The lock.** Before the run directory and the game, the driver makes a
  lock directory with one `mkdir` (atomic: of two drivers, exactly one makes
  it) and writes a holder line into it (`holder`: the run id and the driver's
  pid). A lock that exists is a refusal (rc 3) naming its holder and whether
  that pid is running; it is never taken over or removed by another driver.
  At the end the driver removes it only when the holder line is still its
  own, and only when no game this run started is still alive. A game is the
  run's by what the run recorded (`series watch`'s witnesses: its pid and
  the program at `/proc/<pid>/exe`), or, when no witness recorded it, by
  what the host shows at the end: a process whose `/proc/<pid>/exe` is a game
  binary (the Linux game's `.../binaries/ck3`, or the file `RATE_GAME_BIN`
  names), that was not alive when the run began (`ck3.before`), and whose
  environment carries this run's `RATE_RUN_ID` (every process the runner
  starts inherits it, in whatever session) or, for the Linux game, this
  run's instance (`CK3_INSTANCE_ID`). A Linux game whose environment cannot
  be read counts; no other unreadable process does. Never by a name or a
  command line. When the runner was
  stopped (killed at the wall limit, say) and its game outlived it, the
  driver waits for the game, the lock held, up to 900 s; a game still alive
  then keeps the lock: the driver writes the game into it (`game`: `pid N
  exe E run RUN_ID`), records a `lock` row with rc 1 in `post.tsv`, says so
  on stderr, and exits without removing it. The next driver's refusal names
  the holder and that game, and whether each still runs. A lock left this
  way, or by a driver that was killed, stays until you remove it by hand
  (after checking that no game of that run is left).
  * **Default: one fixed path per user on the host,**
    `/tmp/ck3bench-UID/ck3bench-game.lock`. The path comes from your uid
    alone: not from the environment, `RATE_RUNS`, or whether you are logged
    in (it is not under the session's runtime directory, which a logout
    removes). Its parent is made `0700` with one `mkdir`; a parent that is a
    link, not a directory or not yours is refused (rc 3, set `RATE_LOCK_DIR`),
    and one of yours with wider permissions is narrowed to `0700`. The parent
    is never removed.
  * **What holds:** two reference drivers run by the same user on the same
    host, with no `RATE_LOCK_DIR`, both seeing the host's own `/tmp`, never
    hold a game at the same time, whatever their runs directories,
    configurations or environments, and whether or not the user is logged in.
    **What does not:** drivers of other users (each has its own lock); a
    driver given another `RATE_LOCK_DIR`; a driver in a private `/tmp` (a
    service with systemd's `PrivateTmp`, a container), which sees another
    directory; a host that gives each login a `/tmp` of its own
    (polyinstantiated `/tmp`, `pam_namespace`): a driver in a login session
    and one started by cron or a service then see different directories,
    and "whether or not the user is logged in" holds only where every
    session sees the host's one `/tmp` (set the same `RATE_LOCK_DIR`, on a
    path every session sees, on such a host); a game of this run that a
    killed driver left alive (the driver never ran its end, so nothing
    waited for the game; the lock stays, naming the dead driver); a game of
    this run that no witness recorded and whose environment carries neither
    this run's `RATE_RUN_ID` nor (the Linux game) its instance, such as a
    game started through a launcher that does not pass the environment on, at
    a path other than `.../binaries/ck3` and `RATE_GAME_BIN`; a `/tmp` cleaner that removes a lock older than its age limit
    while a run still holds it (systemd-tmpfiles ages `/tmp` entries, ten days
    on many distributions; a run is limited by `--wall-s`); a game started by
    anything other than the reference driver; and other hosts. `/tmp` is
    emptied at boot, and a lock with it. To share the lock with a driver of
    your own, have it take the same directory the same way.
  * `RATE_LOCK_DIR` overrides the path (several machines' drivers on a shared
    filesystem, say); its parent is created when missing.
* It signals only its own children: the watcher (after a 600 s wait for it)
  and the runner (TERM at `--wall-s`, which makes the runner close `env play`
  and its game; KILL 300 s later if it has not exited). It never looks for a
  process by name. A game left alive after the run is the env's to close;
  the driver does not kill it, and keeps the lock while it lives (above).

Exit codes: 0 the attempt ran (valid or not: read `run.json` and the ledger),
2 a usage or configuration refusal, 3 the lock is held or the run directory
exists, 5 a switch differs from the expectation (or a placeholder is left in
it), 6 the launch identity differs (no game started and no ledger entry was
written; the run directory, with its `run.json` saying why, is moved to
`RATE_RUNS/refused-starts/`), 7 not this host's slot (or the series has
stopped), 8 the attempt's end steps did not complete (`series end` or `series
result` exited non-zero, or the driver stopped before them): no end marker is
written, and `driver.log`, `post.tsv` (a `marker` row) and stderr name the
command that completes it (`series close OUT`; `series end OUT` by hand, then
`series close OUT`; or, for a game not played out, `series withdraw OUT`). Exit codes 2, 3, 5 and 7 come before the lock and the run
directory; 6 after them and before the game. None of them is an attempt.

### The game's environment

The game the run plays is started by `ck3agent launch` inside `env play`,
which the runner starts, which the driver starts: **the driver's environment
is the game's environment.** Nothing in between sets a display or a user
directory. The driver checks these keys before any other step, for a task on
the game (`backend = "ck3"`; the fake backend needs none), and records their
values in `driver.log` (`game env:`) and in the `--dry-run` output:

| variable | what the game uses it for | checked |
|---|---|---|
| `DISPLAY` | the X display the game's window opens on | **required** (unless `SDL_VIDEODRIVER=wayland`): refused, `game_env:DISPLAY is not set` |
| `XAUTHORITY` | the X server's authority file, when it needs one | when set: a readable file, else refused |
| `SDL_VIDEODRIVER` | the game's video driver (SDL) | when set: `x11` or `wayland`, else refused |
| `WAYLAND_DISPLAY` | the Wayland display (`SDL_VIDEODRIVER=wayland` only) | required with `SDL_VIDEODRIVER=wayland` |
| `DBUS_SESSION_BUS_ADDRESS` | the session bus the game and its libraries talk to | not checked |
| `XDG_CONFIG_HOME`, `XDG_DATA_HOME`, `XDG_STATE_HOME` | where the toolkit keeps its configuration (`ck3agent setup`) and where `ck3agent launch` puts the game's user directory (`$XDG_STATE_HOME/ck3agent/game/`) | when set: absolute paths, else refused |
| `XDG_RUNTIME_DIR` | the toolkit's per-instance runtime directory (the bridge's socket and log) | not checked |
| `CK3_BINARY` | the game binary, overriding `ck3agent setup`'s record | not checked |

A refusal is exit code 2 within the first second, with no lock, run
directory or game. Practical notes, each observed on a Linux host:

* A headless host needs an X server of its own (a virtual one, or a headless
  compositor's X display); point `DISPLAY` and `XAUTHORITY` at it and set
  `SDL_VIDEODRIVER=x11`.
* A desktop session's `WAYLAND_DISPLAY` and `DBUS_SESSION_BUS_ADDRESS`, left in
  the environment of a game meant for another display, stalled the game a
  few seconds after it started. Unset both when the game is not meant for
  the desktop.
* To keep the game's files apart from your own play, give the driver its own
  `XDG_CONFIG_HOME`, `XDG_DATA_HOME` and `XDG_STATE_HOME` (and run
  `ck3agent setup` once with the same three).
* Running the driver under `env -i` with an explicit list of variables makes
  the game's environment exactly that list.

### A stranger's two-attempt series on the stand-in backend

This runs with no game and no model, from a clean checkout and the toolkit
installed (`mcp` is needed only for the stand-in agent CLI; `--policy noop`
needs neither). Every attempt is **invalid** (no game process exists to
witness, `witness_absent:game_start`), so the series stops at its attempt cap
and scores `k/n = 0/0`: the point is the path, not a result. Files stand in
for the bridge and the map; the python binary stands in for the agent CLI
whose version is recorded, and an ELF file with a build id for the game
binary.

Two inputs must be files on disk:

* **The toolkit wheel** (`RATE_WHEEL`): the wheel file of the toolkit installed
  in this environment, compared member by member with the install. Install the
  toolkit from a file, so the file stays: download the release asset named in
  the README's quickstart (`curl -LO <that URL>`) and `pip install` that file;
  `RATE_WHEEL` is its path. A toolkit installed from a URL leaves no file, and
  a wheel that is not the installed one makes every attempt
  `wheel_install_differs`.
* **A file with an ELF build id** (`RATE_GAME_BIN`): the stand-in's game binary
  is only read for its build id. The line below picks the first of the venv's
  python, `ls`, `env` and `sh` that has one, by the harness's own reader. When
  it prints nothing, none of them has one: name any ELF file whose
  `readelf -n` shows a `Build ID`. Without one, `series declare` records
  `game_build_id` as unread and every start refuses (`unread:game_build_id`).

```bash
B=$PWD                                   # a clean git checkout of ck3bench, the toolkit installed
W=$HOME/demo && mkdir -p $W/stage $W/protected && printf 'stand-in' > $W/stage/bridge.so && printf 'stand-in' > $W/stage/map.bin
export KIT_REPO=$B RATE_RUNS=$W/runs RATE_SCENARIO=$B/ck3bench/harness/testdata/standin-ireland-fake-wake-once.toml \
  RATE_SO=$W/stage/bridge.so MAP_BIN=$W/stage/map.bin RATE_WHEEL=/path/to/ck3agent-<version>-py3-none-any.whl \
  RATE_GAME_BIN=$(python3 -c 'import os, shutil, sys; from ck3bench.harness.series import build_id
for p in [os.path.realpath(sys.executable)] + [shutil.which(n) for n in ("ls", "env", "sh")]:
    if p and build_id(os.path.realpath(p)).get("build_id"): print(os.path.realpath(p)); break') \
  RATE_CLAUDE_BIN=$(command -v python3) RATE_MODEL=stand-in RATE_INSTANCE=demo RATE_SAMPLE_S=5 \
  RATE_LOCK_DIR=$W/lock RATE_PROTECTED_DIR=$W/protected TURN_MODE=fresh NOTES=on SEAT_PROMPT=default RECORD=0 \
  VIEW= CLEANMAP=0 FULL_HOLD=600
RATE_GAME_SPEED=3 RATE_POLICY=noop RATE_WALL_BUDGET_S=30 python3 -m ck3bench.harness series declare > $W/expect.json
# review it; fill the "<FREEZE: ...>" key from a check run (here: leave it out, so it is not held)
python3 - $W/expect.json <<'EOF'
import json, sys; p = sys.argv[1]; d = json.load(open(p))
d.pop("notes_version"); json.dump(d, open(p, "w"), indent=1)
EOF
cat > $W/series.json <<EOF
{"schema": "ck3bench.series.schedule/1", "series": "demo", "task": "$RATE_SCENARIO", "pattern": ["h1", "h2"],
 "valid_target": 1, "attempt_cap": 2}
EOF
python3 -m ck3bench.harness series run --host h1 --series $W/series.json --expect $W/expect.json --policy noop
python3 -m ck3bench.harness series run --host h2 --series $W/series.json --expect $W/expect.json --policy noop
python3 -m ck3bench.harness series run --host h1 --series $W/series.json --expect $W/expect.json --policy noop   # refused: stopped (rc 7)
python3 -m ck3bench.harness score $W/runs/demo-a01-h1 $W/runs/demo-a02-h2
```

The checkout must be clean (`git status` empty) or the start refuses
(`tree_dirty`), and it must be a git checkout (an unpacked archive has no
commit to record: `unread:git`; `git init && git add -A && git commit` makes
one). `RATE_LOCK_DIR=$W/lock` keeps the demo off the default lock (one per user on
the host), which a real series of yours may hold; without it the demo takes
that lock like any other run. The declared wall budget of 30 s
(`RATE_WALL_BUDGET_S=30`, held by every attempt) bounds each no-op attempt
to 30 s: on the fake backend the episode is over in seconds and `env play`
then holds for `FULL_HOLD` (600 s), which the budget cuts (`end.json`
`hold_cut_by_wall_budget`); the end stays `episode_over`, since an end row
stamped before the second of the budget's deadline ends the episode so in either arm. A seat played by the stand-in agent CLI instead: make
`RATE_CLAUDE_BIN` a script that answers `--version` and otherwise runs
`python3 $B/ck3bench/harness/testdata/fake_cli.py "$@"` with
`FAKE_CLAUDE_MODE=exit_at:3` set (the stand-in plays two turns and exits, so
the hold is skipped), declare without `RATE_POLICY`, and drop `--policy noop`
(`ck3bench/harness/test_prereg_sequence.py` builds one).

## Environment variables

Every variable the harness's code reads or sets (a test,
`tests/test_harness_doc.py`, fails when the code names one this table lacks).
"Refused without" says what happens when it is unset: **refused** means the
run does not start (or the step exits non-zero); **invalid** means the run is
recorded and is invalid with the code shown; *no* means it is optional.

| variable | read by | meaning | default | refused without |
|---|---|---|---|---|
| `RATE_RUNS` | driver, series | the directory holding the run directories and the default ledger (not the lock: see `RATE_LOCK_DIR`) | none | refused (driver); `series_ledger` refusal for a series without `RATE_LEDGER` |
| `RATE_LEDGER` | series | the series ledger directory (`attempt-NN.json`, one per attempt) | `RATE_RUNS/<series>.ledger` | no |
| `RATE_SERIES` | series | the frozen schedule (`ck3bench.series.schedule/1`); set by the driver from `--series` | unset: not a series | no |
| `RATE_EXPECT` | series | the frozen expectation (`ck3bench.series.expect/1`); set by the driver from `--expect` | unset | invalid, `expectation_not_declared` |
| `RATE_HOST` | series | this host's name in the schedule's pattern; set by the driver from `--host` | empty | refused in a series (`series_host_unknown`) |
| `RATE_RUN_ID` | series | the run id (`<series>-aNN-<host>` in a series); set by the driver | empty | refused in a series (`series_slot`) |
| `RATE_KIND` | series | `rate` (an attempt) or `check` (never scored; it leaves the expectation's placeholders out); set by the driver (`--check`) | `rate` | no |
| `RATE_POLICY` | series | the `--policy` the run plays (no agent CLI, no session): `claude_version` is recorded as not applicable and not held, and `series declare` writes no agent CLI version and no session value; set by the driver from `--policy` | empty: a seated run | no |
| `RATE_SCENARIO` | series, driver | the task: a built-in id or a task file | the driver: the expectation's `task`, else the schedule's | refused (`plan`) |
| `RATE_MODEL` | series, driver | the model id recorded as `model` and passed to the runner | the expectation's `model` (driver) | refused (driver); `expect_differs:model` when the expectation declares another |
| `RATE_GAME_SPEED` | series | the declared game speed; also aligns a wake task's checks; set by the driver from the expectation | the task's own | no; set it when running `series declare` for a series that declares a speed |
| `KIT_REPO` | series | the bench checkout the run plays from; its commit and cleanliness are the `git` identity | none | refused, `unread:git` (and `tree_dirty` when it has changes) |
| `RATE_SO` | series, driver | the toolkit's bridge library the game loads; hashed into the identity; the driver passes it on as `CK3AGENT_BRIDGE` | none | refused, `unread:bridge_so` |
| `RATE_WHEEL` | series, driver | the toolkit wheel this environment installed; compared member by member with the installed toolkit | none | refused, `unread:wheel` (`wheel_install_differs` when the install is not that wheel) |
| `MAP_BIN` | series, driver | the toolkit's baked map graph (`ck3map.bin`, see [The map graph](#the-map-graph)); hashed; the driver passes it on as `CK3MAP_BIN` | none | refused, `unread:map` |
| `RATE_GAME_BIN` | series | the game binary whose ELF build id is the `game_build_id` identity | none | refused, `unread:game_build_id` |
| `RATE_CLAUDE_BIN` | series, driver | the agent CLI executable: `RATE_CLAUDE_BIN --version` is the `claude_version` identity of a seated run; the driver's sessions run it (outside a series `--agent-cli` replaces it; in a series that is refused); not read for a `--policy` run | `claude` (series) | refused (driver, a seated run); `unread:claude_version` when it does not answer |
| `RATE_SAVES` | series, driver | the directory holding the task's start save; hashed; passed as `--saves-dir` | none | refused, `unread:start_save` (a task with a start save) |
| `RATE_INSTANCE` | series, driver | the game instance id, passed as `--instance-id`; also picks the toolkit's bridge log path | unset: the default instance | no |
| `KIT_INSTANCE` | game identity | the instance the run declares, held against the game's `CK3_INSTANCE_ID`; set by the driver from `RATE_INSTANCE` | the trajectory header's | no |
| `RATE_BRIDGE_LOG` | series | the bridge log read for the target title's create commands (`bridge_title_witness.json`), and the path whose open file the game witnesses record | the toolkit's path for `RATE_INSTANCE` | no (the witness says `absent`, or `unwitnessed` when the file is not the one this run's game held) |
| `RATE_GAME_SAVES` | series | the game's own save directory, where the final save through the menu lands | none | no (with `RATE_UI` only) |
| `RATE_UI` | series | hook: a UI driver script run as `bash RATE_UI click X Y` / `key K` / `type TEXT` for the final save | unset | no (`no UI driver configured`) |
| `RATE_HOLD_CHECK` | series | hook: a command (RUN_DIR appended) printing one JSON object about the game's display in the hold | unset | no (`no hold check configured`) |
| `RATE_WATCH_S` | series | the watcher's limit in seconds | `36000` | no |
| `RATE_LAUNCH_UTC` | series | the launch time; `run.json` wall seconds are measured from it; set by the driver | the start step's time | no |
| `RATE_LOCK_TAKEN` | series | when the host's lock was taken, recorded in `result.json`; set by the driver | empty | no |
| `RATE_RUNNER_FLAGS` | series | the runner's flags as a JSON list, recorded in `run.json`; set by the driver | `[]` | no |
| `RATE_PAIRED` | series | a run another host plays at the same time, declared (`--paired`) and recorded as declared | unset | no |
| `RATE_LOCK_DIR` | driver | the lock directory (see [the reference driver](#the-reference-driver)) | one per user on the host: `/tmp/ck3bench-UID/ck3bench-game.lock` | no |
| `DISPLAY`, `XAUTHORITY`, `SDL_VIDEODRIVER`, `WAYLAND_DISPLAY` | driver | the game's display, inherited from the driver ([The game's environment](#the-games-environment)) | none | refused (rc 2) for a task on the game without a display, `game_env:<variable>` |
| `DBUS_SESSION_BUS_ADDRESS`, `XDG_CONFIG_HOME`, `XDG_DATA_HOME`, `XDG_STATE_HOME`, `CK3_BINARY` | driver | inherited by the game and the toolkit; recorded in `driver.log`; the XDG directories must be absolute paths when set | unset | refused when an XDG directory is a relative path |
| `RATE_SAMPLE_S` | driver | seconds between host-pressure samples | `30` | no |
| `RATE_TURN_TIMEOUT_S`, `RATE_STEP_WAIT_S`, `RATE_WALL_BUDGET_S`, `RATE_WALL_S` | series (`declare`) | the timings `series declare` writes into the expectation's `timings` (a series attempt runs with them) | the reference driver's defaults (1200, 900, 27900, 28800) | refused when not a number (`declare_timing`) |
| `RATE_PROTECTED_DIR` | driver, series | a directory the run must leave unchanged, digested read-only (names, types, sizes, mtimes and contents) before the run and again before `series end`; for a series on the game, the game's default user-data directory (`~/.local/share/Paradox Interactive/Crusader Kings III`), which the run's game does not use (`ck3agent launch` starts it with `-userdir=` set to the toolkit's own directory); a difference makes the run invalid (`owner_tree_differs`) | unset | refused in a series (`protected_dir_not_configured`); outside one, no (`not configured`: `run.json` `end.seen.owner_tree` and `result.json` read `not_configured`) |
| `FULL_HOLD` | series, driver | seconds `env play` keeps the finished game up (`--hold-before-close-s`); a series holds exactly 600 | exported as `600` by `series switches` | refused when set to anything else (`switch_differs`); `full_hold_s` records 0 when unset at `declare` |
| `TURN_MODE`, `SEAT_PROMPT` | series | kit switches: the runner plays `fresh` turns with its `default` prompt; anything else is refused | `fresh`, `default` | no |
| `NOTES` | series, driver | kit switch: the toolkit's shared notes `on` or `off` (passed as `--notes`) | `on` | no |
| `RECORD`, `VIEW`, `CLEANMAP` | series, driver | kit switches of a driver with screen recording, a camera view and a map mod; recorded in the identity; the reference driver requires them off | `0`, empty, `0` (off) when nothing is declared | the reference driver refuses `RECORD`/`VIEW`/`CLEANMAP` not off (`kit_switch_unhonoured`) |
| `CK3_BRIDGE_*` | series | the bridge's own switches (the toolkit documents each); recorded, held to the expectation, exported to the game and the toolkit servers | unset | refused when one differs from or is missing in the expectation (`switch_differs`, `switch_undeclared`) |
| `CK3_INSTANCE_ID` | game identity, series | read from the game's and the toolkit servers' `/proc/<pid>/environ` (never from ours) | | |
| `CK3AGENT_BRIDGE`, `CK3MAP_BIN`, `CK3BENCH_TOOLKIT_WHEEL_SHA256`, `CK3BENCH_CONTROL_LOG` | driver (sets) | for the runner's children: the bridge to load, the map graph, the wheel's sha256 for the trajectory header, the control-session log (`RUN_DIR/control.jsonl`) | | |
| `CK3_NOTES`, `CK3_REPLY_PAGES`, `CK3_TOOL_PREFIX` | runner (sets) | for `env play` and the seats' toolkit servers: notes off (`--notes off`), reply pages, the tool-name prefix | | |
| `CLAUDE_CODE_DISABLE_AUTO_MEMORY`, `PYTHONPATH` | runner (sets) | for the session: auto-memory off; for the shim: the bench checkout to import | | |
| `HOME`, `PATH`, `LANG`, `LC_ALL`, `TERM`, `USER`, `LOGNAME`, `XDG_RUNTIME_DIR`, `TMPDIR` | runner | the only variables of ours the session keeps (so it finds its own login); plus each `--session-env` name | | |
| `SCORE_RATE_OVERRIDE_LOG` | score | where `--before-stop-for-tests` is logged | `score_rate_overrides.log` beside the first run directory | no |
| `KIT_STANDIN_GAME_PID_FILE` | game identity | a file naming a stand-in game pid, read only for a fake-backend header | unset | no |
| `FAKE_CLAUDE_MODE`, `FAKE_CLAUDE_READS`, `FAKE_CLAUDE_WAKE` | stand-in agent CLI | its failure mode, its extra reads, its wake request (`testdata/fake_cli.py`) | `normal`, off, none | no |
| `CK3BENCH_TEST_NO_FSYNC` | series, driver, runner, env (`ck3bench.env.durable`) | TEST ONLY: `1` skips the flushes to the disk (the renames stay), for the kit's own tests; the kit never sets it, and `series start` records whether it was set (`run.json` `records_flushed`: `false` under it); `score` refuses a series with such an attempt (`series_records_not_flushed`); only `series start` records it, so a step run by hand under it after the start (`series end`, `close`, `result`, `withdraw`) is not caught | unset: every record flushed | no |
| `RATE_HOST_NAMES` | publish | machine names (space- or comma-separated) `series publish` rewrites beside the ones `run.json` records and this host's own, as `--host-name` does; needed to publish a run directory whose `run.json` does not read and holds no name | unset | no (a damaged `run.json` with no name in it and none given refuses the copy) |

## The run directory

Every file the bench's code reads or writes in a run directory. A driver of
your own may add files; nothing here reads them.

| file | written by | read by | what |
|---|---|---|---|
| `seat_prompt.rendered.md` | runner | `series end` | the system prompt every session got; its sha256 must equal the launch's |
| `trajectory.jsonl` (or `.gz`) | env (`env play --out`) | series, score, readers | the episode: header (task, seats, seat surface, provenance: toolkit identity, notes, start save, game pid, instance), one row per step and call, the end row |
| `control.jsonl` | env (`CK3BENCH_CONTROL_LOG`) | nothing here | every control-session request and reply |
| `transcript.jsonl` | runner | score (resource rule), accounting, analyze | every session's stream-json events, in order |
| `session_stdin.jsonl` | runner | nothing here | every line the runner wrote to a session's stdin, byte for byte, in order: one stream-json `user` message per line (`{"type": "user", "message": {"role": "user", "content": TEXT}}`), the turn's message (it starts `Turn K:`) and, when sent, the reminder after it. Turn K's message belongs to row K of `turns.jsonl`; a message whose session ended the episode before replying (`seat_exited`, say) has no row. It holds the messages only: the seat prompt is `seat_prompt.rendered.md`, the flags `session_argv.json`, and no credential, key or environment value is written (the runner reads none). Empty with `--policy` |
| `turns.jsonl` | runner | score (stop window), accounting, analyze | one row per turn: `turn`, `session`, `step`, `sent_utc`, `ended_utc`, `wall_s`, `reminded`, calls by tool, tokens as reported, `cost_usd_reported`, `rate_limited`, `env_after` |
| `runner_start.json` | runner, as it starts (written whole) | score (the wall-budget deadline of a stopped attempt's end row) | `start_s` (seconds since the epoch) and `start_utc`: the instant the runner counts its wall budget from |
| `end.json` | runner | series end, score | `end_reason`, `at`, `wall_s`, `turn_mode`, `sessions`, `play_rc`, `notes_witness`, `play_last_line` |
| `mcp.json`, `tool_listing.json`, `session_init.json`, `session_argv.json` | runner | series end (`tool_listing.json`, `session_init.json`) | the seat's surface as configured, listed by a real MCP client, reported by the session's init event, and the session's argv (the prompt elided) |
| `runner.log`, `play.out`, `play.err`, `session.err`, `cwd/`, `before_turns.log` | runner | nothing here | logs; the session's empty working directory; the `--before-turns` hook's output |
| `accounting.json` | `accounting RUN_DIR` | score (`cost_usd_reported`) | per-turn tokens and the sessions' reported cost |
| `analysis.json` | `analyze RUN_DIR` | nothing here | turn by turn calls, replies and text |
| `run.json` | `series start`, `series end` | series, score | schema `ck3bench.series.run/1`: `run_id`, `kind`, `host`, `launch_utc`, `plan`, `runner_flags`, `expectation`, `concurrency`, `series` (id, schedule and its sha256, attempt, target, cap, pattern), `start` and `end` (the identity, including the machine's host name as `host`), `start_reasons`, `validity` (`valid`, `reasons`) |
| `ck3.before` | the driver | game identity | the pids of the game processes alive before the run (one per line: the Linux game's binary or `RATE_GAME_BIN`), so the watch and the lock tell this run's game from another |
| `witness_start.json`, `witness_end.json`, `witness_end2.json` | `series watch` (each written whole) | series end, the sampler (`pid`) | the game process at start, at the end row and after the final save: exe, build id, the bridge mapped and its hash, environ switch keys, the toolkit servers, the bridge log it holds open (`bridge_log_held`: device and inode of each open file at the bridge log's path); or `unread` with why |
| `framecheck_hold.json` | `series watch` | series result | the `RATE_HOLD_CHECK` hook's JSON, or `checked: false` with why |
| `final_save.json`, `final_save/` | `series watch` | series result | the final save through the menu and its header-date readback, or `saved: false` with why; a copy of the save |
| `watch.log` | `series watch` | nothing here | the watcher's log |
| `bridge_title_witness.json` | `series end` | score (creator) | the bridge log's create commands for the target title and their completions (`state: read`), only when the file at the bridge log's path is the one this run's game held open (same device and inode as a game witness recorded; `held_by_game`). Otherwise `state: unwitnessed` with a `reason_code`: `no_game_witness` (no witness saw this run's game, so nobody could look), `log_not_held` (the game held no file at that path), `log_replaced` (the file there is another one: an earlier or later game's). `absent` with why when there is no target title or no file. The scorer corroborates the creator only from `read` |
| `done` | the driver, after the runner | `series watch` (stops waiting), `series end` and `series withdraw` (played out: `score.played_out`) | `rc=N end=UTC` |
| `pressure.tsv` | the driver's sampler | score (resource rule) | a header row, then one row per sample, tab-separated: `utc`, `cpu_some10`, `cpu_full10`, `mem_some10`, `mem_full10`, `io_some10`, `io_full10` (PSI avg10, percent), `memavail_kb`, `load1`, `load5`, `game_pid`, `game_state` (the `/proc` state letter, `none` before the game is witnessed, `gone` after), `game_rss_kb`; a value not read is `NA`, never 0 |
| `own.before.txt`, `own.before.list`, `own.before.sha`, `own.after.*` | the driver | series end, series result, score | the protected directory's digest, byte for byte what `find . -printf '%y %p %s %T@\n' \| LC_ALL=C sort` (`.list`: type, path, size, mtime) and `find . -type f -print0 \| LC_ALL=C sort -z \| xargs -0 sha256sum` (`.sha`) write in that directory, so a digest taken with those two commands compares with it; `.txt` is `root=R files=N list_sha=<16 hex> content_sha=<16 hex>` (the first 16 hex digits of the two files' sha256) or `not configured`. A file that cannot be read is an `unread:<error>` line in `.sha`. The verdict (`score.owner_tree`, read by `series end`, `series result` and the scorer): `IDENTICAL` when both `.list` and both `.sha` are byte-equal, `DIFFERS` (invalid, `owner_tree_differs`) when not, `unwitnessed` (invalid, `witness_absent:owner_tree`) when a directory was digested and a file of the pair is missing, `not_configured` when `.txt` says so, `not_taken` when no file exists |
| `post.tsv` | the driver | series result (`post_steps`) | one row per driver step, tab-separated: `step`, `rc`, `utc`, `note` (backslash, tab, newline and carriage return escaped as `\\`, `\t`, `\n`, `\r`) |
| `result.json` | `series result` | nothing here | schema `ck3bench.series.result/1`: `run_id`, `kind`, `host`, `task`, `valid`, `invalid_reasons`, `success` (sealed in a series), `row` (the scorer's row without its outcome keys in a series), `final_save`, `framecheck_hold`, `owner_tree`, `lock`, `post_steps` |
| `rate_run.done` | the driver, last, only when `series end` and `series result` exited 0; or `series close` | score (the end marker) | `rc=N end=UTC`. The scorer counts an attempt as ended only when `run.json` has `validity` and this file exists |
| `operator_withdrawn.txt` | `series withdraw OUT` | series end (refuses), series close (refuses), score | the host was withdrawn mid-attempt, its driver and game stopped first. The run is invalid (`operator_withdrew_host`) and is not an attempt; `series withdraw` refuses while the driver or game runs (`withdraw_alive`), an attempt `series end` has reached (`withdraw_ended`) and one played out (`withdraw_played`); one beside an ended attempt's validity refuses the score |
| `operator_stopped.txt` | `series withdraw OUT --cancelled` | series end (refuses), series close (refuses), score | the series was cancelled while this attempt played, its driver and game stopped first. The same standing (`operator_cancelled_series`) |
| `start_refused.txt` | the driver, when `series start` refused or failed | score | the refusal: no game ran and no ledger entry was written; the directory is moved to `RATE_RUNS/refused-starts/RUN_ID-UTC`. Scored invalid (`start_refused`), not an attempt, never part of the stopping rule |
| `driver.log`, `driver_runner.out`, `watch.out`, `accounting.txt`, `analysis.txt` | the driver | nothing here | logs and the readers' printed tables |
| `<file>.damaged.json` | `series publish`, in its DEST (never in the run directory) | nothing here | the stub that stands for a damaged listed file: its name, size, the sha256 of its bytes and the damage |
| `PUBLISHED.json` | `series publish`, in its DEST (never in the run directory) | nothing here | the publishable copy's record: each file's sha256 before and after, the placeholders and the rules ([Publishing a run directory](#publishing-a-run-directory)) |

The ledger (`RATE_LEDGER`, `attempt-NN.json`, the number with at least two
digits): `attempt`, `host`, `run_id`, `started_utc`, `expectation_sha256`
(text), then at the end `ended`, `valid` (true or false), `reasons` (a list of
text), `ended_utc`, `not_attempt`. It carries validity only, never an outcome.
An entry that does not read (another file name for the same number, a field
of another type: `score.ledger_files`) is never skipped (that would free its
slot): the slot, every step that reads the ledger and the scorer refuse it by
name (`series_ledger_unreadable`). With two hosts the
ledger must hold both hosts' entries (copy the other host's in) before the
next slot can start. A refused start writes no entry.

## Publishing a run directory

```
python -m ck3bench.harness series publish RUN_DIR DEST [--host-name NAME ...] [--config host.toml]
```

writes into the new directory `DEST` a copy of the files a series publishes
(`run.json`, `runner_start.json`, `result.json`, `end.json`,
`trajectory.jsonl`, `turns.jsonl`, `accounting.json`, `pressure.tsv`, `post.tsv`, `bridge_title_witness.json`,
`transcript.jsonl`, `session_stdin.jsonl`, the driver's `done` and
`rate_run.done`, and the operator's `operator_withdrawn.txt` or
`operator_stopped.txt`; one the run did not write is listed as absent),
rewritten (`ck3bench/harness/publish.py`). Before any
rule, the copy is refused (exit 5, nothing written) when a listed file is a
symbolic link or resolves outside the run directory (its target is not read)
or has more than one hard link (another name of a file that may live outside
the run), and when `run.json`'s `host` is not a host label (`a`, `b`, `h1`). A
listed file that is damaged (not UTF-8 text, a NUL byte, a `.gz` that does not
decompress, a `run.json` that is not one JSON object: what a host going down can leave, so a counted attempt with such a
file is still published) is not read and not published: the copy holds
`<file>.damaged.json` in its place, naming the file, its size, the sha256 of
its bytes and the damage, and `PUBLISHED.json` lists it as damaged. Every JSON string, key or value, and every line
of the other files then goes through these rules in order:

* **limit text**: a string the agent CLI wrote about an account limit (a
  usage-limit message, "limit reached", "hit your limit", a named limit such
  as a weekly, daily, 5-hour, session or model limit, how much of it was used
  ("used N%", "N% of your"), a reset time with a digit, a weekday, a month or
  "tomorrow", `|` and ten digits, "rate limit of N", an organisation's rate
  limit, a per-minute token limit, a credit balance, "out of usage") is
  replaced whole by `<limit text removed>`, followed by `(rate limit)` and
  `(api error)` when the scorer's own patterns read the original that way, so
  the copy's resource evidence is the run's (in `turns.jsonl` this blanks the
  result-text excerpt of a turn that hit a limit); a line of a non-JSON file
  is replaced whole;
* **escapes**: JSON's escaped slash, a unicode-escaped slash and percent
  encoding (to three levels: `%252F`) are read plain first, so the path rules
  read a path behind them;
* **paths**: every absolute path of the host configuration (the `RATE_*` path
  variables, `KIT_REPO`, `MAP_BIN`, the XDG directories and `HOME`, each also
  as its real path), the run directory, and the expectation and schedule
  paths `run.json` records become `<NAME>` placeholders, longest first; any
  other home directory (a `~name/` home and a Windows `C:\Users\name` too)
  becomes `<HOME>`; a Windows network path (`\\server\share`), a share by URL
  (`smb://`, `cifs://`, `afp://`, `nfs://`) and `//server/share` become
  `<network path>`; a path under `/mnt`, `/srv`, `/opt`, `/data`, `/media`,
  `/scratch`, `/nfs`, `/var/lib`, `/Volumes`, `/workspace`, `/net` or
  `/run/media` becomes `<private path>`; a socket under `/tmp` becomes
  `<socket>`, and any other path below a directory of `/tmp`, or under
  `/var/tmp` or `/dev/shm`, `<tmp path>`;
* **variables**: the value of `USER=`, `LOGNAME=`, `USERNAME=`,
  `SUDO_USER=`, `HOSTNAME=` and `HOST=` (in a text in any capitalisation, as a
  JSON key, or as `"HOSTNAME": "..."` written inside a string, in any
  capitalisation but `"USER"` and `"HOST"`, read in capitals only since
  `"user"` and `"host"` are ordinary keys) is removed;
* **host names**: the names `run.json` records (`start.host`, `end.host`;
  when `run.json` does not read, every `"host": "NAME"` its bytes hold that
  is not a label), the names given with `--host-name NAME` (repeated) or in
  `RATE_HOST_NAMES`, and this host's own name, each also without its domain, in any capitalisation,
  wherever they stand outside a run of letters and digits (so `NAME-ck3` and
  `name_backup` are caught), become `<host name>`, and a user at a machine
  with no domain, as a login names it (after `ssh`, `scp`, `rsync`, `sftp` or
  `mosh`, or before `:/path`), becomes `<user>@<host>`. A `run.json` that does
  not read and holds no name, with none given, refuses the copy (exit 5,
  nothing written): its names are not known, and a copy is never made with
  them unread. A host name that is also an ordinary word is
  replaced in the seat's and the game's text as well (a host named `castle`
  turns "the Castle of Dublin" into "the `<host name>` of Dublin");
  `PUBLISHED.json` counts every replacement, and a host with a distinctive
  name avoids it;
* **user names**: the account's name (the process's, `USER`, `LOGNAME`,
  `HOME`'s last part) and every name a home path or a `~name/` in the files
  carries, three characters or more, wherever it stands outside letters and
  digits (a run directory named with it, a value split from its path), become
  `<user name>`, with the same ordinary-word caveat;
* **addresses**: an IPv4 address becomes `<ip address>` (a dotted number after
  "version", "v", "build" or "CK3" whose first number is below 10 is a
  version and is kept; a game date such as 1066.9.15 has three parts and is
  not an address), an IPv6 address too, a MAC address (also in dot notation,
  `001a.2b3c.4d5e`) `<mac address>`;
  `uid=N(name)`, `gid=N` and `groups=...` lose their numbers and names; a
  claude.ai session link becomes `<session link>`, a Drive or Docs link
  `<drive link>`;
* **the agent CLI's events**: in `transcript.jsonl`, each rate-limit event
  keeps only its type and its status (what the resource rule reads) and each
  `system`/`init` event only `type`, `subtype`, `model`, `tools` and the MCP
  servers' names and statuses; the account and limit keys
  (`publish.ACCOUNT_KEYS`, and any key of their family: a reset time, a
  field about use beyond the limit, a use figure) are dropped from every file;
* **ids**: every `session_id` becomes `session-N`, numbered by first
  appearance before anything else is numbered (a session id is `session-N`
  wherever it stands, in a `session_id` key or in a text), and every other
  UUID `id-N` (the same id, the same placeholder in every file).

`DEST/PUBLISHED.json` lists each file's sha256 before and after, the run
directory's name rewritten by the same rules, and each placeholder with what
it stands for and how often it was put in (never a value). The copy,
`PUBLISHED.json` included, is then read again for every class above, an
e-mail address, an account key, a key or token (the shapes `publish.SECRET`
lists: a private key's header of any kind, a PGP key block's too, a
provider's or a forge's token (`sk-`, `sk-ant-`, `gh?_`, `github_pat_`,
`xox?-`, `AIza`, `sk_live_`/`sk_test_`/`rk_live_`/`rk_test_`), a cloud
provider's key id (`AKIA`, `ASIA`), a bearer header, a JWT, and a value of 12
characters or more after a name that holds `secret`, `api_key`, `password`,
`credential` or `private_key`, as in `aws_secret_access_key = ...`, read up to
a space, a quote, a comma or `}` when unquoted, and when quoted to the
closing quote of its own kind, so spaces, an apostrophe or the other quote
inside it are part of it (`password = "correct horse battery staple"`,
`password = "don't tell anyone"`), and quoted after a name that holds
`passphrase` or `pwd` (a `pwd` value that is a path is a working directory,
rewritten as a path); the same quoted after `password =>` or a
`--password` flag (or `passphrase`); or after a name that holds `token` when the value has a
letter, so a token count is not one; a value that is a placeholder whole
(`"<redacted>"`), and a quoted or unquoted value that is exactly one ISO date
or time and nothing more, are not read as a key (`"2026-01-01 and more"` is read); `hf_`, `npm_` and `glpat-` tokens too), the host names, the user names and the
configured paths, each line as written, with its escapes read plain, and with
every base64 run (the standard and the URL-safe alphabet, and a base64 text
inside one) and every hex run decoded: if one survives, nothing is written (exit 5, each
survivor named by its class, file and line, never its value). An e-mail
address and a key or token of those shapes are never rewritten; a copy holding
one is refused. What no rule catches: a key of a shape `SECRET` does not list
(a provider's token with no fixed prefix, a value under a name holding none of
those words, a value shorter than 12 characters), a machine's name that no
`run.json` records, no `--host-name` gives and no variable carries (or that
only the lost part of a damaged `run.json` held), a name shorter than three characters, a host or user name inside a
longer word (`<host>es`), a dotted address after a
version word (`version 9.20.30.40` is read as a version), a value split
across fields or lines so that no rule reads it whole (a base64 run wrapped
over two lines), and an encoding the rules do not decode (a gzip inside
base64, a third level of base64). Give the hosts' accounts distinctive names:
a user name that is also an ordinary word (`will`, `mark`, `king`) is
replaced in the seat's and the game's text too, as a host name is. The scorer reads the copy's resource
evidence as it reads the run's, but for a damaged file, which the copy holds as its stub only. Run it with the host configuration the run
was played with, so its paths are known. The rewrite is a floor, not a
review: a person reads every copy before it is released
([the series' document](prereg-munster-kingdom.md#11-what-is-published)).

## Further refusal codes

The codes the sections above do not name, each with what refuses it and what to do. A refusal line begins with
`REFUSE` and its code.

| code | refused by | why, and what to do |
|---|---|---|
| `series_records_not_flushed` | `score` | an attempt's `run.json` records `records_flushed: false`: its `series start` ran with the test-only `CK3BENCH_TEST_NO_FSYNC=1` set; a measured series runs with it unset, and such a series is reported with that refusal as a deviation |
| `expect_schema` | every step that reads the expectation | the file `RATE_EXPECT` names is not a `ck3bench.series.expect/1` expectation: name the frozen expectation `series declare` wrote |
| `switch_not_bridge` | `series switches` | the expectation's `switches` holds a name that is no `CK3_BRIDGE_*` switch: kit switches go in its `kit` table |
| `switch_value` | `series switches` | a declared switch value holds characters the kit does not export: declare a plain value |
| `kit_switch_unknown` | `series switches` | the expectation's `kit` table names a switch the kit does not have |
| `series_absent` | `series slot` | `RATE_SERIES` is not set: the slot is a series step |
| `series_stopped` | `series slot`, `series start` | the series has reached its stop (the valid target or the attempt cap): no attempt is started |
| `series_alternation` | `series slot`, `series start` | this host's next attempt waits for the other host's lower attempt to start (section 4 of the series' document): start that one first |
| `series_task` | `series slot`, `series start` | the run plays another task than the schedule's |
| `start_clock_behind` | `series start` | this host's clock reads more than 300 s before its own latest start in the ledger (see the table under "A driver stopped mid-attempt") |
| `withdraw_run_json_unreadable` | `series withdraw` | `run.json` does not read and the ledger holds the attempt's open entry: the attempt stays counted; `series end OUT` records it ended and invalid |
| `series_not_stopped` | the scorer | the series has not reached its stop: nothing is read (`--before-stop-for-tests` reads it anyway, logged, for the kit's tests) |

## Recorded identifiers

These names are the harness's schema names, file names and codes, and the
scorer reads them as they are spelled; each means only what is said here:

| identifier | meaning |
|---|---|
| `ck3bench.series.run/1`, `ck3bench.series.expect/1`, `ck3bench.series.schedule/1`, `ck3bench.series.result/1`, `ck3bench.series.published/1` | the schema names of `run.json`, the expectation, the schedule, `result.json` and a publishable copy's `PUBLISHED.json` (the scorer refuses a schedule with another, `series start` an expectation with another) |
| `rate_run.done` | the end marker the scorer requires; `series close` writes it for an attempt whose driver stopped after `series end` |
| `series_ledger_disagrees`, `series_validity_disagrees`, `series_mixed` | the scorer's agreement refusals: a ledger entry and its `run.json` validity disagree; a `run.json` validity and `attempt_reasons` read again disagree; a set mixing series and other run dirs |
| `identity_differs:<key>`, `identity_unread:<key>` | `score --compare`: the two series' task, start save, map or game build differ, or one series has no one value for it |
| `expect_differs:timings.<key>`, `witness_absent:runner_flag:<key>` | `series start`: a runner timing flag other than the expectation's, or missing |
| `owner_tree_differs`, `witness_absent:owner_tree`, `owner_tree` | the protected directory's digest differed, or was taken before and not after; `result.json`'s record of that digest |
| `start_refused.txt`, `start_refused`, `refused-starts/` | a start the driver refused: its record, its code, where its directory is moved |
| `series_expectation_differs`, `series_expectation_unwitnessed`, `series_expectation_absent`, `expect_hosts_key` | one expectation per series: an attempt (or a scored attempt) with another file than the ledger's, a ledger entry or run that records none, a series attempt with none; a `hosts` table with a key other than `claude_version` |
| `expect_timings`, `expect_differs:timings`, `series_agent_cli`, `protected_dir_not_configured`, `declare_timing` | the reference driver's series refusals: no `timings`, a timing flag that differs, `--agent-cli`/`--session-env` in a series, `RATE_PROTECTED_DIR` unset; a timing that is not a number at `declare` |
| `series_attempt_missing`, `series_attempt_not_in_ledger`, `series_attempt_differs`, `series_ledger_absent` | the scorer's ledger refusals: a ledger attempt with no run directory given, a run directory the ledger does not name, an attempt numbered differently, no ledger |
| `withdraw_ended`, `withdraw_alive` | `series withdraw` on an attempt `series end` has reached (or that has no ledger entry); on one whose driver or recorded game still runs |
| `end_ended`, `end_ledger` | `series end` on an attempt whose validity is written, whose end marker exists, that is withdrawn, whose ledger entry has ended or that has none; `series end` that wrote `run.json` and could not write the ledger entry (`series close` writes it) |
| `operator_withdrawn.txt`, `operator_stopped.txt`, `operator_withdrew_host`, `operator_cancelled_series` | the withdrawal and cancellation records and their codes |
| `RATE_*`, `KIT_*`, `SCORE_RATE_OVERRIDE_LOG`, `score_rate_overrides.log` | the environment interface above |

## The map graph

`MAP_BIN` is the toolkit's map graph, `ck3map.bin`: the provinces, their
adjacencies and the title hierarchy, baked once from your game's own data
files so the bridge can answer map queries (`CK3MAP_BIN`). The bench does not
make it and does not ship one (it is derived from game data). The toolkit's
repository has the baker, `bridge/tools/ck3mapgen` (its README has the
details); it needs CMake and a C++ compiler:

```bash
cmake -S bridge/tools/ck3mapgen -B build-mapgen -DCMAKE_BUILD_TYPE=Release && cmake --build build-mapgen
./build-mapgen/ck3mapgen --game-dir "/path/to/steamapps/common/Crusader Kings III/game" \
    --out ck3map.bin --meta ck3map.bin.meta.json
sha256sum ck3map.bin
```

`--game-dir` is the install's `game` directory (it holds `map_data/` and
`common/landed_titles/`, read only). Write to a temporary name and move it
into place, so a half-written file is never used; the bridge opens the path
as given, so make it a regular file, not a link. A host without a compiler
can copy a `ck3map.bin` baked on another host from the same game build. The
expectation holds whichever you declare (`map_sha256`).

## Requirements of the hooks

* **The game binary.** The harness recognises the game process only by its
  `/proc/<pid>/exe` ending in `/binaries/ck3` (the Steam layout of the Linux
  native build); a game at another path is never witnessed
  (`witness_absent:game_start`).
* **The final save** (`RATE_UI`) clicks the game's menu at fixed points of a
  **1920x1080** game window (`series.SAVE_INPUTS`); at another size the
  clicks miss and `final_save.json` says which input failed or that no save
  was written.
* **The display check** (`RATE_HOLD_CHECK`) is your command; the harness only
  records the JSON it prints.

## The pinned build

This version of the bench is pinned to one build, named in
[`BUILD_PINS.toml`](../BUILD_PINS.toml) at the repository's root:

* the toolkit: the ck3agent release `pyproject.toml` requires, its wheel's
  members digest (`ck3bench/env/toolkit_id.py`), the digest of the literal
  keys its server modules can write into a reply
  (`ck3bench/env/toolkit_source.py`), its tool listing (the registered tools
  as JSON with sorted keys) with the bridge switches unset and with
  `CK3_BRIDGE_ARMY_ORDERS=1` and `CK3_BRIDGE_ARMY_HOLD=1`, and the digest of
  the source files a bridge build compiles (`bridge/src`, `bridge/include`,
  `bridge/third_party`, `bridge/cmake`, `bridge/CMakeLists.txt`,
  `re/offsets` of a toolkit checkout);
* the task of the series this version is declared for
  (`munster1066-kingdom-once-30y-wake`): its rendered seat prompt and the
  version of the notes set it is served;
* this package's parts that decide what a seat is told: the goal line
  (`ck3bench/env/goal_line.py`), the standing orders (`orders.py`), the MCP
  shim (`mcp_shim.py`), the observation builders (`env.py`), the `end_turn`
  acknowledgement (`server.py`), the game backend (`ck3.py`) and the runner's
  turn message (the code of the functions that build it, comments and
  docstrings left out, with the tool-name prefix it names).

`tests/test_build_pins.py` recomputes every value from the code and fails
when one differs: this package's values in every run of the tests, the
toolkit's with the toolkit installed (the members digest from the install, or
from `TOOLKIT_BUILD.md` when the repository ships one), and the bridge source
only when `CK3BENCH_TOOLKIT_CHECKOUT=<a toolkit checkout>` names one (the
toolkit's wheel does not carry it). A run records its own build in `run.json`
and in its trajectory header, and an expectation holds a series to the build
it declares ([Running and scoring a series](#running-and-scoring-a-series)).

No measured result is published with this version of the bench. The series
it is declared for, and its control, are fixed in advance in
[prereg-munster-kingdom.md](prereg-munster-kingdom.md).
