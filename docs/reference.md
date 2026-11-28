# Reference

The detail behind the [README](../README.md): what a trajectory file holds,
what each metric reads, the env server's routes, and every refusal and end
code. The module docstrings in `ck3bench/env/` are the full statement of each
rule.

## The trajectory format

One JSONL file per run (`ck3bench.trajectory/2`), flushed per record. Every
record carries `schema`, `kind`, `episode_id`, `backend`, `fake` and
`wall_utc`. Kinds, in order:

* `header`: the task's full declaration, the seed (and whether it was drawn),
  the seats (name to character id), the backend's provenance, the start
  metrics;
* `call`: one per tool call (`step`, `seat`, `phase`: `turn` during the seat's
  turn or `action` as its step action, `call`, `result`, `game_date`,
  `wall_s`);
* `step`: `order` (the seats' execution order), `actions`, `observations`,
  `rewards`, `terminations`, `truncations`, `infos`, `metrics`, `advance`
  (`reached`, `truncation`, `detail`, `wall_s`, and `diagnostics`: the
  backend's own count of pause-alarm re-arms and dropped reply frames, with a
  status `reported`, `not_applicable` or `not_reported`), `usage` as
  reported, `turns` (how each seat's turn ended, when reported) and, for a
  task with messages and notepads, `comms` (what was delivered, dropped and
  applied);
* `end`: `reason`, `steps`, `returns` (a return is `null` if any of its
  rewards was unavailable); for a task paid once, `target_title` (per seat:
  the title, `held_at_any_step`, `first_held`, `dates`, `held_at_last_step`,
  `last_step_date`, `end_date`, `horizon_reached`); for a decision task,
  `outcome_end` (the end read) and `outcome` (the measure scored at the end,
  not paid).

`ck3bench env check FILE` checks the order and the required keys.

## Metrics

Read for the played line's current character (the seat's ruler at the start,
then his successors), with the game paused:

* `realm_counties`: the counties in the realm he rules, his own plus every
  vassal's, recursively (0 once the line has ended). For a king this is the
  toolkit's `query_realm` total; for a vassal it is his row of the ruler
  listing, because `query_realm`'s total of a vassal counts only his own
  counties.
* `demesne_counties`: the counties he holds himself.
* `gold`, `prestige`, and `line_alive` (whether the played line still has a
  ruler).
* For a task that names a target title (`target_title`, such as
  `k_ireland`): `dejure_counties`, the counties of that title's de jure
  territory in his realm, held by him or by a vassal at any depth (0 once
  the line has ended), and `holds_target_title`, whether he holds the title
  himself.
* `ruler_changes` (how many times the line has passed to a new ruler since
  the start), `offensive_wars_declared` (wars declared since the start whose
  attacker was a ruler of the line, whether the seat or the game's AI chose
  to) and `heir_present` (whether his primary title has an heir). All
  three have been read on a live game (the Sweden 867 and Lorraine 1066
  starts).

Each step records, per metric, which read the value came from. A value that
could not be read is `null` beside its status, never a number.

## The observation's news

On the real game, each seat's observation carries `world.news`: what changed
for the seat since its previous turn, as its own ck3agent server says it
(`observe(depth="news")`, read once per game date, so a seat that asks for its
observation again within a turn gets the same block). That read has its own
baseline and does not move the news of the seat's own `observe`. A quiet turn
is `{"since": ..., "nothing_new": true}`; a read that failed is
`{"unread": ...}`. The step records keep it. The fake world has no news.

## The env server

`ck3bench env serve TASK [--port N] [--turn-timeout-s T]` serves one
environment over HTTP+JSON on loopback (`--allow-remote` to bind elsewhere;
it has no authentication). A call is `{"tool_name", "arguments"}`.

| route | what it does |
|---|---|
| `GET /spec`, `/state`, `/tools?seat=S`, `/observation?seat=S` | the task, the global view, a seat's tool schemas, a seat's observation |
| `POST /reset`, `/call`, `/step`, `/close` | reset; one in-turn call; step every seat; close |
| `POST /submit`, `/await` | one seat submits its turn; wait (bounded) for a step's result |

With `/submit` the server steps when every live seat has submitted, or at the
deadline `T` once one has; a seat that has not is stepped with `[]` and a
typed `timeout` record. One server drives one game.

## Refusals and end codes

Every refusal is an `EnvRefusal` with a machine-readable `code`, a `detail` and
the `leg` that refused (on the CLI and the server: one JSON object,
`{"ok": false, "code", "detail", "leg"}`). Nothing defaults. A *result* code is
an `ok: false` tool result the seat reads; its turn goes on.
`tests/test_readme.py` keeps this list complete and every name in it real.

| leg | codes |
|---|---|
| the API | `not_reset`, `episode_over`, `unknown_seat`, `action_malformed`, `seed_not_an_int`, `bad_request`, `remote_bind_refused`, `policy_unknown`, `policy_not_applicable` |
| tool results | `tool_not_allowed`, `clock_verb_refused`, `session_action_unvetted`, `turn_closed` (a call after the runner closed a seat's turn), `not_modelled_by_fake` (the fake backend) |
| declarations | `scenario_unknown`, `scenario_unknown_key`, `scenario_missing_key`, `scenario_wrong_type`, `scenario_bad_value`, `scenario_speed_unreachable`, `scenario_needs_other_backend`, `reward_unknown`, `backend_unknown` |
| the backends | `outcome_unsupported` (a decision task's outcome on a backend that cannot read it), `start_save_missing`, `start_save_sha256_mismatch`, `launch_failed`, `toolkit_not_installed`, `start_not_established`, `speed_not_established`, `seat_transport_failed`, `tool_specs_unavailable`, `mods_not_wired`, `checkpoint_unsupported`, `backend_not_open`, `census_read_unavailable`, `census_too_few_dukes`, `census_seat_unconfirmed` |
| the server and the runner | `turn_timeout_not_declared`, `already_submitted`, `submissions_pending`, `step_pending`, `stopped_by_signal` |
| turns (a step's `turns`) | `end_turn`, `model_stopped`, `max_calls`, `max_wall`, `model_error` (the agent); `timeout`, `policy_error`, `action_malformed` (the runner and the server) |
| messages and notepads (results) | `queued`, `delivered`, `applied`, `read`, `args_malformed`, `recipient_unknown`, `recipient_self`, `recipient_done`, `text_too_long`, `quota_exceeded`, `pad_unknown`, `pad_not_declared`, `mode_invalid`, `version_required`, `version_stale`, `write_too_long`, `pad_full` |
| the MCP shim and the model adapter | `tool_name_clash`, `nothing_pending`, `turn_ended`, `tool_not_listed`, `model_unavailable` |
| wake tasks and notes pads | `wake_not_declared`, `wake_malformed`, `wake_condition_unwitnessable`, `wake_unsupported`, `notes_not_kept`, `notes_too_long`, `notes_malformed`, `notes_edit_refused` (the shim could not apply a pad patch: the turn ends with the pad unchanged) |
| standing orders | `orders_not_declared`, `order_malformed`, `order_lever_refused`, `order_needs_confirm` |
| ends of an episode | `horizon`, `game_exited`, `bridge_unavailable`, `clock_stalled`, `reward_unreadable`, `script_log_closed`, `line_dynasty_unread` (the line's ruler changed and his dynasty did not read), `line_ended`, `line_lost_to_other_dynasty` (scored as `line_ended`), `goal_reached`, `team_seat_done` |
| re-judging a recorded episode (`replay.py`) | `recording_ended` |
| baselines | `baseline_unknown`, `baseline_file_invalid`, `baseline_none_for_scenario`, `baseline_beyond_series` |
