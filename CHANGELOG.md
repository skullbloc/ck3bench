# Changelog

All notable changes to ck3bench. Versions are calendar-based
(`YYYY.MM.PATCH`), like ck3agent's; the git tag is the version with a `v`
prefix. No version has been released yet.

## 2026.11.0-dev0 — unreleased

The first version: Crusader Kings III as an environment for LLM agents.

* A parallel multi-agent environment (PettingZoo's parallel shape; seats are
  agents) and a Gymnasium-shaped one-seat view. Actions are typed tool calls
  on ck3agent's 13 public game tools; one step is one decision cycle of N
  game-days with the game paused while the seats act; rewards are declared
  terms read from game state; truncations are typed, and a failed step's
  reward is NaN, never 0.
* Two backends: the real game through ck3agent (pinned at exactly
  `2026.12.11`), and a deterministic fake world for tests and policy wiring.
* Four France 1066 tasks (2 to 10 game-years, one with six seats) and three
  fake tasks, declared by one strict reader that also reads task TOML files.
* Environment-owned messages and notepads, and team rewards.
* Seeded random, scripted and no-op policies, a provider-neutral tool-calling
  agent with an optional Anthropic adapter, an HTTP+JSON env server, a
  mixed-policy runner, and an MCP shim for a Claude Code seat.
* A JSONL trajectory format (`ck3bench.trajectory/2`) and its checker.
* A baseline of eight unsteered 100-game-year games from the 1066 France start.
