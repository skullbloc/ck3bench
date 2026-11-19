"""ck3bench.env: Crusader Kings III as an environment for LLM agents.

A parallel multi-agent environment whose agents are SEATS (rulers), whose
actions are typed tool calls on the ck3agent toolkit's surface, and whose
rewards are read from game state. One step is one decision cycle of N
game-days.

    from ck3bench.env import ParallelEnv, ToolCall
    env = ParallelEnv("fake-duel")
    obs, infos = env.reset(seed=1)

Multi-agent: scenarios may declare env-owned messages and notepads (comms.py)
and a team reward keyed on one seat. Agents: a provider-neutral tool-calling
agent (agent.py; an optional Anthropic adapter in anthropic_model.py), an MCP
shim for a Claude Code seat (mcp_shim.py), and a mixed-policy runner over the
env server (episode.py).

Backends: `fake` (an in-process scripted world for tests; every record it
emits says so) and `ck3` (the real game through the toolkit; it refuses by
name what it cannot do).
"""
from .env import Env, ParallelEnv
from .errors import EnvRefusal
from .policy import NoOpPolicy, RandomPolicy, ScriptedPolicy, ToolHandle, run_episode
from .scenario import BUILTIN_DICTS, Scenario, builtin, load_scenario, resolve
from .trajectory import TrajectoryWriter, check_trajectory, read_trajectory
from .types import PUBLIC_TOOLS, SEAT_TOOLS, ToolCall, ToolResult
from .agent import Model, ModelReply, ModelToolCall, ScriptedModel, ToolCallingAgent
from .episode import run_mixed_episode

__all__ = ["Env", "ParallelEnv", "EnvRefusal", "NoOpPolicy", "RandomPolicy", "ScriptedPolicy", "ToolHandle",
           "run_episode", "BUILTIN_DICTS", "Scenario", "builtin", "load_scenario", "resolve",
           "TrajectoryWriter", "check_trajectory", "read_trajectory", "PUBLIC_TOOLS", "SEAT_TOOLS",
           "ToolCall", "ToolResult", "Model", "ModelReply", "ModelToolCall", "ScriptedModel",
           "ToolCallingAgent", "run_mixed_episode"]
