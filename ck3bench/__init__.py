"""ck3bench: Crusader Kings III as an environment for LLM agents.

The environment is `ck3bench.env` (tasks, the parallel multi-agent Env, the
fake and real backends, policies, the env server, trajectories, baselines) and
its command is `ck3bench env`. The package imports nothing outside itself but
the ck3agent wheel, at the version `pyproject.toml` pins.
"""
from ._version import __version__

__all__ = ["__version__"]
