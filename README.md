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
pip install https://github.com/skullbloc/ck3agent/releases/download/v2026.12.11/ck3agent-2026.12.11-py3-none-any.whl
pip install -e ".[test]"
ck3bench env scenarios                                     # the tasks, each with its status
ck3bench env run fake-duel --seed 1 --policy random --out traj.jsonl
ck3bench env check traj.jsonl                              # the trajectory file's shape
ck3bench env play fake-council --seed 1 --seat king=agent-scripted --seat duke1=noop --seat duke2=random --turn-timeout-s 30 --out play.jsonl
ck3bench env check play.jsonl
ck3bench env baseline france1066-hold-10y                  # the game's own AI on that task
python -m pytest                                           # the package tests and tests/
```

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
* **Terminated**: `line_ended`, `goal_reached`, `team_seat_done`.
  **Truncated**: `horizon`, or the world failing (`game_exited`,
  `bridge_unavailable`, `clock_stalled`, `reward_unreadable`,
  `script_log_closed`), when the step's reward is NaN, never 0.
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

  then give the session the shim in its MCP config (for example `.mcp.json`):

  ```json
  {"mcpServers": {"ck3bench-king": {"command": "ck3bench",
    "args": ["env", "shim", "--url", "http://127.0.0.1:8770", "--seat", "king", "--step-wait-s", "900"]}}}
  ```

## Tasks

`ck3bench env scenarios` lists them; a task can also be a TOML file, read by
the same strict reader. A task named `-Ny` runs N game-years of 365 days at
game speed 3.

| task | seats | steps x game-days | reward | status |
|---|---|---|---|---|
| `france1066-treasury-2y` | the King of France | 10 x 73 | gold change | not run live as declared (a 24 x 30-day variant ran to its horizon) |
| `france1066-gain-county-5y` | the King of France | 25 x 73 | +1 when the realm first exceeds its start; -1 if the line ends | not run live |
| `france1066-hold-10y` | the King of France | 25 x 146 | realm county change; -50 if the line ends | not run live as declared (a 20 x 180-day variant ran to its horizon) |
| `france1066-council-10y` | the king and his five largest vassals (chosen by rank at the start), with messages and notepads | 25 x 146 | the king's realm county change, paid to every seat; -50 if his line ends | not run live as declared (a two-year variant with six seats ran to its horizon) |
| `fake-duel` | two rulers | 8 x 90 | realm county change; -10 if the line ends | runs (fake backend) |
| `fake-solo` | one ruler | 12 x 30 | gold change | runs (fake backend) |
| `fake-council` | a king and two vassals, with messages and notepads | 8 x 90 | the king's realm county change, paid to every seat | runs (fake backend) |

The France tasks start from the 1066 bookmark and expect your own save of it,
`_1066_09_15.ck3`, in the saves directory you name (`--saves-dir`).

## Baselines

`ck3bench env baseline TASK [--years Y]` reads the game's own AI on a task
from eight unsteered 100-game-year games from the 1066 France start (no seat;
ck3agent 2026.12.9; CK3 1.18.3.1; game speed 2), shipped with their
provenance in `ck3bench/env/baselines_france1066.json` (each game's realm
counties about every 30 game-days), and replays the task's own reward over the
series (the no-op return). It rests on **eight** games. Realm counties of the
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
  bridge (its release tarball and installer). The backend starts the game
  with `ck3agent launch` and gives each seat its own toolkit MCP server.
* **Time**: at game speed 3 the clock runs about 1.6 game-days per wall
  second: a 2-year task is about 8 minutes of clock, a 10-year one about 38,
  plus a minute to launch and the policy's own time (the game is paused while
  it thinks). With six seats a step costs about 10 s outside the clock.
* **Several games**: one instance each (`CK3_INSTANCE_ID` and its own
  `XDG_STATE_HOME`), one env server each, about 8 GB of memory each (two ran
  at once on a 32 GB host; a third did not fit).
* **Model cost** is your policy's. For scale: a 25-game-year game with six
  Claude seats (one Opus, five Sonnet) through the same toolkit, its clock
  running free, cost 168k tokens and USD 32 as the client reported, in 3.8 h.

## Status and limits

* **Run live** (one Linux host, CK3 1.18.3.1, ck3agent 2026.12.11, scripted
  policies, no model): one-seat France episodes
  of 2 and 10 game-years to their horizon; a six-seat episode (the king and
  five census-chosen vassals, two game-years, messages and the shared
  notepad) in which a succession happened and the king's seat followed the
  line to the heir; writes applied with the game paused (a gift, a `steer`, a
  council task), and a law change the game refused, read as refused; the HTTP
  server and the MCP shim (driven by an MCP client) against a live game; a
  killed game typed `game_exited`; two games at once.
* **Not run live:** an episode driven by a model; a war declared through the
  environment (none was declarable at the 1066 start); the MCP shim under a
  Claude Code session.
* **Limits.** Game speed 3 is the fastest (`set_speed 4` answers success and
  writes 3; a task declaring 4 or 5 is refused `scenario_speed_unreachable`).
  There is no checkpoint at a step boundary (`checkpoint_unsupported`): no
  bridge verb writes or loads a save, and an episode always starts from its
  start save. The toolkit's standing-policy and proposal mods are refused
  (`mods_not_wired`). **The seed controls nothing in the real game**, only
  the environment's own ordering, the fake world and seeded policies: seven
  no-op runs with one seed from one save read the king's gold anywhere from
  263 to 413 six months in, so report means over episodes.

## Citing, license and versions

Cite with [CITATION.cff](CITATION.cff). [Apache-2.0](LICENSE); attribution in
[NOTICE](NOTICE). Crusader Kings and Paradox Interactive are registered
trademarks of Paradox Interactive AB; Claude is a trademark of Anthropic, PBC.
ck3bench is not affiliated with or endorsed by either. Calendar versions
(`YYYY.MM.PATCH`), `2026.11.0-dev0` until the first release
([CHANGELOG.md](CHANGELOG.md)); see also [CONTRIBUTING.md](CONTRIBUTING.md),
[SECURITY.md](SECURITY.md) and [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).
