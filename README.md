# ck3bench

Crusader Kings III as an environment for LLM agents.

Each agent is a **seat**: a ruler in a running game. An action is a list of
typed tool calls on the 13 public game tools of
[ck3agent](https://github.com/skullbloc/ck3agent), the toolkit that reads and
writes the game through a bridge loaded into it. Observations are JSON;
rewards are read from game state (realm counties, gold, prestige, whether the
line survives). One step is one decision cycle: the game is paused while the
seats act, then the clock runs N game-days and stops.

Any policy can drive a seat: a model from any provider, a script, or none (an
empty action leaves the seat to the game's own AI: the baseline). The API has
PettingZoo's parallel shape and a Gymnasium-shaped one-seat view; an HTTP
server serves policies in other languages; seats can message each other, and a
team can be paid by one seat's result. A deterministic **fake backend** runs
all of it with no game, and every record it writes says `fake: true`.

## Quickstart (fake backend, no game, about a minute)

Linux (or another POSIX system) with Python 3.11 to 3.14. ck3bench depends on
the ck3agent wheel at one exact version (`pyproject.toml`), a ck3agent release
asset; install it first:

```bash
python3 -m venv .venv && . .venv/bin/activate
curl -LO https://github.com/skullbloc/ck3agent/releases/download/v2026.12.12/ck3agent-2026.12.12-py3-none-any.whl
pip install ./ck3agent-2026.12.12-py3-none-any.whl   # keep the file: a series names it (RATE_WHEEL)
pip install -e ".[test]"
ck3bench env scenarios                                     # the tasks, and which you can run
ck3bench env run fake-duel --seed 1 --policy random --out traj.jsonl
ck3bench env check traj.jsonl                              # the trajectory file's shape
ck3bench env play fake-council --seed 1 --seat king=agent-scripted --seat duke1=noop --seat duke2=random --turn-timeout-s 30 --out play.jsonl
ck3bench env check play.jsonl
ck3bench env baseline france1066-hold-10y                  # the game's own AI on that task
python -m pytest                                           # the package tests and tests/ (about 20 minutes)
```

Offline, `pip install -e .` cannot fetch its build backend: install
`hatchling` and `editables` (an editable install needs both) into the venv
first and add `--no-build-isolation`; the toolkit's dependencies are in its
release tarball's `wheels/` directory (`pip install --no-index --find-links
<tarball>/wheels ...`).

`env run` plays one episode with one policy on every seat and prints one JSON
line; `env play` serves the environment on loopback and plays it with a policy
per seat (`noop`, `random`, `observe-then-gift`, or `agent-scripted`: the
reference agent on a scripted model, so no model is called).
`tests/test_readme.py` runs the `ck3bench env` lines above.

## The API in ten lines

```python
from ck3bench.env import ParallelEnv, ToolCall, TrajectoryWriter

env = ParallelEnv("fake-duel", trajectory=TrajectoryWriter("t.jsonl"))
obs, infos = env.reset(seed=7)                       # one observation per seat
while env.agents:                                    # seats leave when done
    r = env.call("east", ToolCall("observe")) if "east" in env.agents else None   # in-turn: clock stays
    gift = {"intent": "interact", "args": {"interaction": "gift_interaction", "target_id": 100}}
    actions = {s: [ToolCall("do", gift)] if s == "east" else [] for s in env.agents}
    obs, rewards, terminations, truncations, infos = env.step(actions)            # clock runs one cycle
env.close()
```

* `reset(seed, options)`; `seed=None` draws one and records it.
  `step(actions)`: every live seat sends a list of calls (`[]`: the game's AI
  plays it this cycle). `Env` is the one-seat view: `reset -> (obs, info)`,
  `step(action) -> (obs, reward, terminated, truncated, info)`.
* A malformed action is an `EnvRefusal` with a code; a tool outside the task,
  or a call the game refuses, is an `ok: false` result the policy reads. **The
  clock belongs to the step**: a call that would pause, unpause, set the speed
  or wait is refused `clock_verb_refused` and never reaches the game.
* **Terminated**: `line_ended`, `line_lost_to_other_dynasty` (the line
  passed to a ruler of another dynasty, scored as `line_ended`),
  `goal_reached`, `team_seat_done`. **Truncated**: `horizon`, or the world
  failing (`game_exited`, `bridge_unavailable`, `clock_stalled`,
  `reward_unreadable`, `script_log_closed`, `line_dynasty_unread`), when the
  step's reward is NaN, never 0.
* Policies: `act(observation, tools) -> list[ToolCall]`, optional
  `report_usage()` (tokens as the policy reports them, never estimated) and
  `report_turn()`. Built in: `NoOpPolicy`, `RandomPolicy(seed, menu)`,
  `ScriptedPolicy`, `ToolCallingAgent`.
* `ck3bench env serve TASK` serves one environment over HTTP+JSON on loopback.

The trajectory format, metrics, server routes and codes: [docs/reference.md](docs/reference.md).

## Several seats, agents and the runner

* **Messages and notepads** are environment tools declared per task:
  `msg_send(to, text)`, delivered at the step's end in the seeded seat order,
  and `note_read` / `note_write` on a private pad and a shared one. **Team
  rewards**: `none`, `sum`, `mean`, or `seat` (every seat paid one seat's
  reward, as a council paid by the king's realm).
* **The reference agent** (`ToolCallingAgent`) drives a seat from any chat
  model behind a small `Model` protocol, each tool call run at once in the
  seat's turn, under a per-step budget you set; `AnthropicModel` (the
  `anthropic` extra) is a thin adapter that takes the model id from you.
* **The runner** (`run_mixed_episode`, or `env play`) plays each seat's turn
  on its own thread; a seat that overruns `--turn-timeout-s` is stepped with
  `[]` and a `timeout` record.
* **A Claude Code seat** plays through the MCP shim, a stdio MCP server
  exposing one seat's tools from a running env server. Start the episode with
  that seat external (it prints the server's URL first):

  ```bash
  ck3bench env play fake-council --seed 1 --seat duke1=noop --seat duke2=noop --external king --turn-timeout-s 900 --port 8770
  ```

  then give the session the shim in its MCP config (for example `.mcp.json`;
  the harness's runner names the server `ck3seat`, so the seat prompt's tool
  names are `mcp__ck3seat__...`; a session you configure yourself may use any
  name):

  ```json
  {"mcpServers": {"ck3seat": {"command": "ck3bench",
    "args": ["env", "shim", "--url", "http://127.0.0.1:8770", "--seat", "king", "--step-wait-s", "900"]}}}
  ```

  By default `end_turn` returns the seat's next observation, so one session
  can play turn after turn. With `env play --notes-pad-bytes 4096` and the
  shim's `--end-turn ack`, `end_turn` takes `notes` (the seat's whole notes
  pad, refused over its bound, never cut), returns only an acknowledgement
  and refuses any later call: each turn is then played by a new session that
  is given the observation and the pad, and every step record carries the
  pads' text.

## Tasks

`ck3bench env scenarios` lists them, one JSON line each: `start` says how its
start is had (`no_save_needed`, `your_own_save`, or `declared_save_not_shipped`)
and `runnable_by_you` whether you can run it with what you can obtain (false
for the eight tasks whose save is declared by its sha256: see
[Starts and saves](#starts-and-saves)). A task can also be a TOML file, read by
the same strict reader. A task named `-Ny` runs N game-years of 365 days.
Every built-in task declares game speed 3 (see [Game speed](#game-speed)).
A **wake** task's step runs until a wake condition the seat set holds
(checked every 7 game-days), at most 365 days (180 for `-wake180`), or 73
days when the seat sets none; a succession in the seat's line and a war
declared on the seat always wake it, and the seat may leave standing orders.
The steps column gives the steps at the default length.

Status, from each task's declaration (`status_reason`): **with a model**,
model seats have played it live; **unseated only**, only scripted or no-op
seats have run it live (or a variant of it); **not run live**, it has not
run on the game.

| task | seats | steps x game-days | reward | status |
|---|---|---|---|---|
| `france1066-treasury-2y` | the King of France | 10 x 73 | gold change | run live unseated only (a 24 x 30-day variant ran to its horizon) |
| `france1066-gain-county-5y` | the King of France | 25 x 73 | +1 when the realm first exceeds its start; -1 if the line ends or passes to another dynasty | not run live |
| `france1066-hold-10y` | the King of France | 25 x 146 | realm county change; -50 if the line ends or passes to another dynasty | run live unseated only (a 20 x 180-day variant ran to its horizon) |
| `france1066-council-10y` | the king and his five largest vassals (chosen by rank at the start), with messages and notepads | 25 x 146 | the king's realm county change, paid to every seat; -50 if his line ends or passes to another dynasty | run live unseated only (a two-year variant with six seats ran to its horizon) |
| `ireland1066-kingdom-15y` | Murchad, Count of Dublin | 75 x 73 | +1 per de jure Ireland county gained and -1 per county lost; +10 when he holds the Kingdom of Ireland (the episode ends); -5 if the line ends or passes to another dynasty | run live with a model. Its declaration's own estimate (from the 30-year task): fifteen years are too short for the counties and gold the kingdom needs |
| `ireland1066-kingdom-30y` | Murchad, Count of Dublin | 150 x 73 | as `ireland1066-kingdom-15y` | run live with a model |
| `ireland1066-kingdom-30y-wake` | Murchad, Count of Dublin | 150 x 73, wake | as `ireland1066-kingdom-15y` | run live with a model |
| `ireland1066-kingdom-30y-wake180` | Murchad, Count of Dublin | 150 x 73, wake (at most 180 days) | as `ireland1066-kingdom-15y` | run live unseated only (six steps, a scripted seat) |
| `munster1066-kingdom-30y-wake` | Murchad ua Briain, Duke of Munster | 150 x 73, wake | as `ireland1066-kingdom-15y` | run live with a model |
| `munster1066-kingdom-once-30y-wake` | Murchad ua Briain, Duke of Munster | 150 x 73, wake | +1 per de jure Ireland county gained, -1 per county lost; +10 once, on the first step the seat's ruler holds the Kingdom of Ireland (play goes on to 1096.09.15); -5 if the line ends (no heir) or passes to a ruler of another dynasty | run live with a model |
| `francia867-empire-40y-wake` | Charles, King of West Francia | 200 x 73, wake | as `ireland1066-kingdom-15y`, for the Empire of Francia (`e_france`) | run live with a model |
| `sweden867-kingdom-20y-wake` | Björn Ironside (the holder of `d_svealand`, found at the start) | 100 x 73, wake | as `ireland1066-kingdom-15y`, for the Kingdom of Sweden | run live with a model |
| `lorraine1066-line-25y-wake` | Gottfried, Duke of Lower Lorraine (the holder of `d_lower_lorraine`, found at the start) | 125 x 73, wake | realm county change; -5 if the line ends or passes to another dynasty; the goal, judged at 1091.09.15: the line still rules, has passed at least once, declared no offensive war, and holds at least its start's counties | run live with a model. Its start was judged not winnable for the line goal in the declared save: the ruler's heir is matrilineally married there, so the son who succeeds him is of his wife's dynasty |
| `carinthia1066-line-25y-wake` | Berthold, Duke of Carinthia (the holder of `d_carinthia`, found at the start) | 125 x 73, wake | as `lorraine1066-line-25y-wake` | run live with a model |
| `ireland1070-target-choice-3y` | Murchad, Count of Dublin, in 1070 | 15 x 73, wake | as `ireland1066-kingdom-15y`; an outcome recorded at the end, not paid (below): which of two claims he declared on, and the war's result | run live with a model. Feasibility of the goal a series of it scores (holding `k_ireland`): no declaration estimates it for three years from this start; the kingdom needs eight counties and 850 gold by `ireland1066-kingdom-30y`'s declaration, which judges fifteen years from the 1066 start too short for them |
| `ireland1075-succession-5y` | Murchad, Count of Dublin, in 1075 | 5 x 365, wake | as `ireland1066-kingdom-15y`; an outcome recorded at the end, not paid (below): the counties that stay with his primary heir, and the levers used | run live with a model. Feasibility of the goal a series of it scores (holding `k_ireland`): no declaration estimates it; five years from five counties and 308 gold of debt (this task's declaration), and the kingdom needs eight counties and 850 gold by `ireland1066-kingdom-30y`'s declaration, which judges fifteen years from the 1066 start too short for them |
| `ireland1075-faction-3y` | Murchad, Count of Dublin, in 1075 | 15 x 73, wake | as `ireland1066-kingdom-15y`; an outcome recorded at the end, not paid (below): the counties kept against two factions, and whether he set an answer before their demand | run live with a model. Feasibility of the goal a series of it scores (holding `k_ireland`): no declaration estimates it; three years from the same start as `ireland1075-succession-5y` (five counties, 308 gold of debt), and the kingdom needs eight counties and 850 gold by `ireland1066-kingdom-30y`'s declaration, which judges fifteen years from the 1066 start too short for them |
| `ireland1076-succession-death-4y` | Murchad, Count of Dublin, in 1076 | 20 x 73, wake | as `ireland1066-kingdom-15y`; an outcome recorded at the end, not paid (below): the counties in his primary heir's realm, and the levers used | run live unseated only (a no-op seat). Feasibility of the goal a series of it scores (holding `k_ireland`): no declaration estimates it; four years from five counties that his sons will split at his death, and the kingdom needs eight counties and 850 gold by `ireland1066-kingdom-30y`'s declaration, which judges fifteen years from the 1066 start too short for them |
| `fake-duel` | two rulers | 8 x 90 | realm county change; -10 if the line ends or passes to another dynasty | runs (fake backend) |
| `fake-kingdom` | a count and a rival | 15 x 73 | +1 per de jure county gained and -1 per county lost; +10 when a seat holds the kingdom; -5 if the line ends or passes to another dynasty | runs (fake backend) |
| `fake-kingdom-wake` | as `fake-kingdom` | 15 x 73, wake (checked every game-day) | as `fake-kingdom` | runs (fake backend) |
| `fake-solo` | one ruler | 12 x 30 | gold change | runs (fake backend) |
| `fake-council` | a king and two vassals, with messages and notepads | 8 x 90 | the king's realm county change, paid to every seat; -10 if his line ends or passes to another dynasty | runs (fake backend) |

The two Munster tasks share the seat, start, wake rules and end date. In
`munster1066-kingdom-30y-wake` the episode ends on the first step the ruler
holds the kingdom (+10). `munster1066-kingdom-once-30y-wake` is the task of
the series this version of the bench is declared for
([docs/prereg-munster-kingdom.md](docs/prereg-munster-kingdom.md)): the kingdom pays +10 once (never
again if it is lost and regained), play goes on to the end date, and the
task is met if the line held the kingdom at any step; its seats are served
the notes set `munster1066once`. On every task a line that passes to a ruler
of another dynasty ends the episode as a loss (the env judges the new ruler's
dynasty at every ruler change), and every seat prompt this version renders
says so; a run's own `seat_prompt.rendered.md` records the prompt that run was
told.

**The decision tasks' outcome is recorded, not paid, and the seat is not told
of it.** `ireland1070-target-choice-3y`, `ireland1075-succession-5y`,
`ireland1075-faction-3y` and `ireland1076-succession-death-4y` pay the
Ireland task's reward and are met by its goal (holding the kingdom). The
"scored at the end" measure in their rows is a separate record: the env
computes it at the episode's end and writes it into the trajectory's end
record (`outcome`); it is not added to the return, and the harness's scorer
does not read it. The seat prompt names no such decision and ends "Nothing
else is scored" ([docs/harness.md](docs/harness.md#running-a-seated-episode)).
A series of one of these tasks therefore scores the kingdom goal, over
three to five years from a count's realm; no declaration estimates that the
goal can be reached in that time (each row says what the declarations do
say), while the measure the task was built for is recorded and not scored.

## Starts and saves

Each game task opens a save from the saves directory you name
(`--saves-dir`). Nothing here ships a save. A task that declares a
`save_sha256` opens only that exact file: another file under the same name is
refused (`start_save_sha256_mismatch`) before the game is launched.

| save file | sha256 declared | tasks | how to get it |
|---|---|---|---|
| `_1066_09_15.ck3` | no | the four `france1066-*` tasks, `ireland1066-kingdom-15y`, `-30y`, `-30y-wake`, `-30y-wake180`, both `munster1066-*` tasks | make it yourself: start a game at the 1066 bookmark and save on its first day (1066.09.15) under this name. The seats are named by the character ids the bridge read on such a save (37067, 31506, 31762) |
| `_1066_09_15.ck3` | `0c94b598...` | `lorraine1066-line-25y-wake`, `carinthia1066-line-25y-wake` | **cannot currently be reproduced by a user**: the declared file is the 1066 save the bench's own runs used, and is not shipped; your own save of the bookmark has other bytes |
| `_867_01_01.ck3` | `1a08927d...` | `francia867-empire-40y-wake`, `sweden867-kingdom-20y-wake` | **cannot currently be reproduced by a user**: an 867 observer save the bench's runs used, not shipped |
| `scen_b2_1070_01_01.ck3`, `scen_b2_1075_01_01.ck3` | `a666fb33...`, `f2508772...` | `ireland1070-target-choice-3y`, `ireland1075-succession-5y`, `ireland1075-faction-3y` | **cannot currently be reproduced by a user**: yearly autosaves of a scripted game from the 1066 start; the script is not shipped, and the game is not deterministic, so running it again makes other files |
| `dublin_1076_01_01.ck3` | `8b3dc2cf...` | `ireland1076-succession-death-4y` | **cannot currently be reproduced by a user**: the yearly autosave of a model-played game of `ireland1066-kingdom-30y-wake`, not shipped |

A task declared as a TOML file names its own save and may leave out
`save_sha256`; such a copy of a pinned task runs on your own save, which is
another start than the declared one. The Lorraine, Carinthia and Sweden seats
are found on the save at open (the holder of a named title, confirmed by his
name), so no runtime id is written into those tasks. The fake tasks need no
save.

## Scaffold

The environment never shows a task's description to a seat: a runner gives
its seat the task, for example in its prompt. Beyond its tools and its
observation (metrics, last results, the turn's news on the real game), a seat
on the real game receives:

* **A goal line.** On a task with a target title, each turn's observation
  carries one line on that title (`world.goal`), from the toolkit's own `formable_titles`
  read: that it can be created now with the exact call; or that it waits only
  for gold (the price, the gold held, the months at the last step's net, a
  price that rises at the ruler's death); or the titles of the tier below it
  still waits on and what each costs, with the whole chain's bill; or, in the
  game's own words, what else it lacks.
* **The death clause.** In the goal line, when the ruler's death would take
  goal titles out of the realm by partition: which titles go to whom, the
  price after the death, and up to three succession levers the game allows
  now, each with its call.
* **Standing orders** (wake tasks, on by default; `wake.orders = false` turns
  them off). A seat may end its turn with up to four lever calls, each sent by
  the environment once when its conditions hold at a check; an order stands
  until it fires, is cancelled, the ruler changes, or its step limit passes.
  An order that creates a title on a gold threshold is told the price read.
  When the ruler changes, an order whose call names no character (it creates
  a title by its key alone) is still cancelled, and the turn's
  `ruler_changed` news hands it back ready to set again (`set_again`), with
  the title's price read for the new ruler when that price reads.
* **Forced wakes** (wake tasks). A succession in the seat's line and a war
  declared on the seat stop the clock whatever the seat asked.
* **The episode's end, said.** The last reply names why the episode ended
  (the title, its holder, the date and who created it; or the horizon) and
  the state at the end.
* **The toolkit's shared notes** (`discover(what="notes")` on the seat's own
  toolkit server; `CK3_NOTES=off` turns them off). A task that names a notes
  set (`notes_set`) is served that set: `francia867`, `sweden867`,
  `lorraine1066`, `carinthia1066` and `munster1066once` are start-specific
  (a path to the target title, what to do on the first turn), and the
  Ireland tasks (the Dublin seat, the Munster task that ends at the first
  holding, the decision starts) are served `ireland1066`, written for the
  1066 Irish start: 8 of its notes are scoped to one start, and one of its
  turn-one notes (shown in every seat's first observation) ends with the call
  that creates the kingdom of Ireland (`do intent=mint_title
  args.title_key=k_ireland`). A task that names no set (the four
  `france1066-*` tasks) is served the toolkit's default set: general game
  rules and tool use, 0 of its notes scoped to one start and no title key in
  any of them.
* **The goal, to the toolkit.** The seat's toolkit server is told the task's
  target title (its spending veto never stops the game's AI creating that
  title for the seat) and the task's goal kind (a goal that forbids offensive
  war gets observations without the conquest material).

**Where the scaffold came from.** The notes sets and the goal line were
developed from earlier runs, seated by a model or scripted, of several of
these same starts (the 1066 Dublin and Munster seats, Francia and Sweden at
867, Lorraine and Carinthia),
including the start of the Munster tasks. They are the toolkit's, not this
package's: their text is in the toolkit's notes file (`server/notes.json`),
which ships in ck3agent from the pinned version on, and
`discover(what="notes")` serves it. This README does not repeat the text: the
table below counts the notes and names their scopes and title keys. The
Munster set is the Ireland set without 6 of its notes (its notes-pad note and
the 5 that describe the Dublin start) and with 2 of its own (the task's reward
with the kingdom's creation call, and a notes-pad note): its seat is served 27
notes, 4 of them scoped to one start, and its turn-one notes carry the
kingdom's creation call (`tests/test_scaffold_statements.py` reads these
numbers from the notes file).

The table below is generated from the task definitions and the toolkit's
notes file (`tests/test_scaffold_statements.py` checks that it is the one this
tree generates): how many notes each set serves, how many are scoped to one
start (and which), and which kingdom or empire title keys its notes and its
turn-one notes name.

| tasks | notes set | notes | scoped to one start | title keys the notes name | in a turn-one note |
|---|---|---|---|---|---|
| `france1066-council-10y`, `france1066-gain-county-5y`, `france1066-hold-10y`, `france1066-treasury-2y` | the default set | 22 | 0 | none | none |
| `carinthia1066-line-25y-wake` | `carinthia1066` | 27 | 4 (a vassal ruler; confederate partition; the 1066 start; the 1066 start, a vassal duke) | none | none |
| `francia867-empire-40y-wake` | `francia867` | 27 | 3 (the 1066 start; the 867 start; the 867 start, a king) | `e_france` | `e_france` |
| `ireland1066-kingdom-15y`, `ireland1066-kingdom-30y`, `ireland1066-kingdom-30y-wake`, `ireland1066-kingdom-30y-wake180`, `ireland1070-target-choice-3y`, `ireland1075-faction-3y`, `ireland1075-succession-5y`, `ireland1076-succession-death-4y`, `munster1066-kingdom-30y-wake` | `ireland1066` | 31 | 8 (1066 start, Dublin; 1066 start, Ireland; the 1066 start) | `k_ireland` | `k_ireland` |
| `lorraine1066-line-25y-wake` | `lorraine1066` | 27 | 3 (a vassal ruler; the 1066 start; the 1066 start, a vassal duke) | none | none |
| `munster1066-kingdom-once-30y-wake` | `munster1066once` | 27 | 4 (1066 start, Ireland; the 1066 start; the Munster task paid once) | `k_ireland` | `k_ireland` |
| `sweden867-kingdom-20y-wake` | `sweden867` | 27 | 3 (a tribal ruler; the 1066 start; the 867 start) | `k_sweden` | `k_sweden` |
| `fake-council`, `fake-duel`, `fake-kingdom`, `fake-kingdom-wake`, `fake-solo` | none (the fake backend has no toolkit) | 0 | - | - | - |

What each set serves, against the toolkit's pool of notes (the Ireland set
serves it whole; the default set leaves out the notes of one start):

* `ireland1066`: every note, with a turn-one notes-pad note that ends with the
  kingdom's creation call ("At 8 counties, 2 duchy titles and the gold: do
  intent=mint_title ...").
* `munster1066once`: without the Ireland set's notes-pad note and its Dublin
  notes; plus a note of the task's reward with the call that creates the
  kingdom ("At 8 counties, 2 duchies, gold: do mint_title title_key=k_ireland") and
  its own notes-pad note.
* `francia867`: without the Irish notes; plus the empire's
  creation path and an 867 spending note (what to veto on the first step).
* `sweden867`: as `francia867`, with the kingdom's path and the tribal
  ruler's prices.
* `lorraine1066`, `carinthia1066`: without the Irish notes; plus the goal as
  scored, with "Turn one: do ai_veto what=war, what=faction", a faction note,
  and for Carinthia a partition note.

The line tasks' seat prompt also prescribes those two turn-one calls, and says
of the faction veto that it was built and not run live; that text is what
the model seats that have played those tasks were sent, and is kept as it is.

## Baselines

`ck3bench env baseline TASK [--years Y]` reads the game's own AI on a task
from eight unsteered 100-game-year games from the 1066 France start (no seat;
ck3agent 2026.12.9; CK3 1.18.3.1; game speed 2), started from the 1066
observer save with sha256 `0c94b598...` (the file's `start`: not shipped, and
not the save you make yourself for the `france1066-*` tasks, so a comparison
with your own runs is against another start), shipped with their
provenance in `ck3bench/env/baselines_france1066.json` (each game's realm
counties about every 30 game-days), and replays the task's own reward over the
series (the no-op return). It rests on **eight** games. The per-game files it
was read from, the start save and the builder are not shipped (the file names
each source by its sha256), so the series cannot be rebuilt from this
repository. Realm counties of the
King of France's line (84 at the start):

| game-years | 10 | 25 | 50 | 75 | 100 |
|---|---|---|---|---|---|
| mean | 79.0 | 72.5 | 62.6 | 49.4 | 52.2 |
| sample SD | 4.6 | 29.2 | 53.9 | 52.1 | 45.2 |
| games under 10 | 0 | 1 | 3 | 4 | 2 |

From about year 40 the outcome is two-peaked (France held, 75 to 135 counties,
or almost nothing, 0 to 11), so a mean there describes neither peak. The set
did not read gold or prestige (those terms read `absent`).

## Running on the real game

* **The game**: your own copy of Crusader Kings III, Linux native, at the
  build ck3agent supports (1.18.3.1), and your own saves. Nothing here ships
  game data.
* **The toolkit**: ck3agent at the version `pyproject.toml` pins, with its
  bridge (its release tarball and installer). Run `ck3agent setup` once (the
  installer does): it finds the game binary and records it for `ck3agent
  launch` (`CK3_BINARY` overrides it); ck3agent's getting-started guide has
  the steps. The backend starts the game with `ck3agent launch` and gives
  each seat its own toolkit MCP server.
* **The display**: the game is started from the environment of whatever
  starts the env (your shell, the runner, the series driver) and opens its
  window on that environment's `DISPLAY`; the variables it inherits are in
  [docs/harness.md](docs/harness.md#the-games-environment).
* **Time**: at game speed 3 the clock runs about 1.6 game-days per wall
  second: a 2-year task is about 8 minutes of clock, a 10-year one about 38,
  plus a minute to launch and the policy's own time (the game is paused while
  it thinks). With six seats a step costs about 10 s outside the clock.
* **Several games**: one instance each (`CK3_INSTANCE_ID` and its own
  `XDG_STATE_HOME`), one env server each, and the memory of a whole game each:
  read one game's resident memory (the series sampler's `game_rss_kb` column
  in `pressure.tsv`) before starting another.
* **Model cost** is your policy's. For scale: a 25-game-year game with six
  Claude seats (one Opus, five Sonnet), played on an earlier version of the
  toolkit, its clock running free, cost
  168k tokens and USD 32 as the client reported, in 3.8 h.

## Running a seated episode

`python -m ck3bench.harness run-seat` runs one model seat of a task: a fresh
agent CLI session every turn (Claude Code's `claude`, or a CLI with the same
flags and stream-json), the seat's notes pad carried between turns by the
env, and a watchdog that ends the episode cleanly and says why. You supply
the game, the toolkit release and the start save (above), the agent CLI
logged in, and a model name:

```
python -m ck3bench.harness run-seat --out runs/try-1 \
    --scenario munster1066-kingdom-once-30y-wake --saves-dir ~/saves --model claude-opus-5-5
python -m ck3bench.harness prompt munster1066-kingdom-once-30y-wake   # what the seat is told
```

The seat prompt is one template filled from the task. What a seat was told
is that prompt, each turn's message (built by the runner from the
observation; the run directory's `session_stdin.jsonl` records each one as
sent), and what its tools say: the toolkit's tool descriptions, its
notes set and its replies, which depend on the toolkit build and on the
bridge switches the run sets (the build this version is pinned to:
[docs/harness.md](docs/harness.md#the-pinned-build)). `--policy noop` plays the same task
with the game's own AI alone (the baseline), with no model. `run-seat`
briefs the Ireland, Munster, Francia, Sweden, Lorraine and Carinthia tasks
and the four decision tasks; the four `france1066-*` tasks have no briefing,
and a seated run of one ends `setup_failed` before the game starts. The run
directory's files and the end reasons are in
[docs/harness.md](docs/harness.md).

## Running and scoring a series

A series is a frozen schedule (task, host pattern, valid target, attempt
cap) and a frozen expectation (the identity every run must match), both
written before the first attempt. `python -m ck3bench.harness series` has the
steps of one attempt (slot, start identity, watch, end witnesses and
validity, sealed result); `series run` is a minimal reference driver that
performs them in order with a lock (one game per user on the host), a
host-pressure sampler and the end marker the scorer needs; and
`python -m ck3bench.harness score DIR ...` reads the stopped series: success
from the trajectory, valid runs only, a Wilson 95% interval, and a refusal to
read anything before the stop; `score --compare` compares two stopped
series (the difference with Newcombe's interval, Fisher's exact test), and
`series publish` writes a run directory's copy with host names, paths,
account fields and session ids replaced. Without host-pressure samples every
run that ended in a stop or a hang is voided, not counted as a failure. The
validity classes, the resource rule, the environment variables and the run
directory's files are in [docs/harness.md](docs/harness.md). Not included: screen recording and video
delivery, display checks, and anything about particular machines; those meet
the harness at documented hooks.

## The pinned build and published results

This version of the bench is pinned to one toolkit build and to one state of
the parts of this package that decide what a seat is told:
[BUILD_PINS.toml](BUILD_PINS.toml) names each by its digest, and
`tests/test_build_pins.py` recomputes them from the code
([docs/harness.md](docs/harness.md#the-pinned-build)). No measured result is
published with this version. The series of
`munster1066-kingdom-once-30y-wake` it is declared for, and its control, are fixed in advance in
[docs/prereg-munster-kingdom.md](docs/prereg-munster-kingdom.md).

## Game speed

Every built-in task declares game speed 3, the speed its wall-time estimate
was measured at. `--game-speed N` on `env run`, `env serve` and `env play`
declares another speed, 1 to 5 (the game's own range), and a task file may
declare one; the game is set to it at open and read back at every step. A
speed above 3 needs a bridge that takes a declared speed: one that does not
writes 3, and the open refuses `speed_not_established`. A wake task faster
than 3 runs its checks aligned (the clock stops on each check date):
`--game-speed` turns that on, and a task file declaring a faster speed
without `aligned_checks` is refused. Wall-time estimates exist for speeds 2
and 3 only.

## Status and limits

* **Run live** (Linux, CK3 1.18.3.1; one host, or two for a series that
  alternates hosts): seats driven by a model
  (Claude Code sessions, each on the MCP shim) on the tasks marked "with a
  model" above, with wars declared through the environment, standing orders
  fired and wake steps; with scripted policies and no model, one-seat France episodes of 2
  and 10 game-years to their horizon; a six-seat episode (the king and five
  census-chosen vassals, two game-years, messages and the shared notepad) in
  which a succession happened and the king's seat followed the line to the
  heir; writes applied with the game paused (a gift, a `steer`, a council
  task), and a law change the game refused, read as refused; the HTTP server
  and the MCP shim against a live game; a killed game typed `game_exited`;
  two games at once.
* **Not run live:** `france1066-gain-county-5y`; the tasks marked "unseated
  only" have not run with a model.
* **Limits.** There is no checkpoint at a step boundary (`checkpoint_unsupported`): no
  bridge verb writes or loads a save, and an episode always starts from its
  start save. The toolkit's standing-policy and proposal mods are refused
  (`mods_not_wired`). **The seed controls nothing in the real game**, only
  the environment's own ordering, the fake world and seeded policies: the
  game's own randomness is not seeded, so two runs with one seed from one save
  play differently. Report means over episodes.

## Citing, license and versions

Cite with [CITATION.cff](CITATION.cff). [Apache-2.0](LICENSE); attribution in
[NOTICE](NOTICE). Crusader Kings and Paradox Interactive are registered
trademarks of Paradox Interactive AB; Claude is a trademark of Anthropic, PBC.
ck3bench is not affiliated with or endorsed by either. Calendar versions
(`YYYY.MM.PATCH`); this is `2026.12.0`
([CHANGELOG.md](CHANGELOG.md)); see also [CONTRIBUTING.md](CONTRIBUTING.md),
[SECURITY.md](SECURITY.md) and [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).
