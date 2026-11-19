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
  rewards was unavailable).

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

Each step records, per metric, which read the value came from. A value that
could not be read is `null` beside its status, never a number.

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
| the real backend | `start_save_missing`, `start_save_sha256_mismatch`, `launch_failed`, `toolkit_not_installed`, `start_not_established`, `speed_not_established`, `seat_transport_failed`, `tool_specs_unavailable`, `mods_not_wired`, `checkpoint_unsupported`, `backend_not_open`, `census_read_unavailable`, `census_too_few_dukes` |
| the server and the runner | `turn_timeout_not_declared`, `already_submitted`, `submissions_pending`, `step_pending`, `stopped_by_signal` |
| turns (a step's `turns`) | `end_turn`, `model_stopped`, `max_calls`, `max_wall`, `model_error` (the agent); `timeout`, `policy_error`, `action_malformed` (the runner and the server) |
| messages and notepads (results) | `queued`, `delivered`, `applied`, `read`, `args_malformed`, `recipient_unknown`, `recipient_self`, `recipient_done`, `text_too_long`, `quota_exceeded`, `pad_unknown`, `pad_not_declared`, `mode_invalid`, `version_required`, `version_stale`, `write_too_long`, `pad_full` |
| the MCP shim and the model adapter | `tool_name_clash`, `nothing_pending`, `model_unavailable` |
| ends of an episode | `horizon`, `game_exited`, `bridge_unavailable`, `clock_stalled`, `reward_unreadable`, `script_log_closed`, `line_ended`, `goal_reached`, `team_seat_done` |
| baselines | `baseline_unknown`, `baseline_file_invalid`, `baseline_none_for_scenario`, `baseline_beyond_series` |
