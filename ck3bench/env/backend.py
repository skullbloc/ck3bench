"""The backend interface: what the environment needs from a world.

The environment (env.py) owns the episode: seats, steps, rewards, the
trajectory. A backend owns the world: it opens it at a scenario's start,
executes one seat's tool call, advances the clock by a cycle, and reads the
metrics a reward needs. Two backends exist: `FakeBackend` (fake.py, an
in-process scripted world for tests) and `Ck3Backend` (ck3.py, the real game
through the ck3agent toolkit).

A backend never reports success it did not observe: an advance that did not
reach its target says why (`AdvanceResult.truncation`), a metric it could not
read carries a status other than `ok`, and a capability it does not have
(checkpoint/restore at a step boundary) is a typed refusal.
"""
from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from .errors import R_CHECKPOINT_UNSUPPORTED, EnvRefusal
from .scenario import Scenario
from .types import DIAG_NOT_REPORTED, AdvanceDiagnostics, AdvanceResult, Metrics, ToolCall, ToolResult


@dataclass
class OpenedWorld:
    """What a backend reports after opening a scenario."""
    game_date: str
    #: seat name -> the character id it plays at the start
    characters: dict[str, int]
    #: provenance a trajectory header carries (backend name, versions, save sha, ...)
    provenance: dict[str, Any] = field(default_factory=dict)


class Backend(abc.ABC):
    #: written into every trajectory record; `fake` is True for a backend that is not the game
    name: str = "abstract"
    fake: bool = False

    @abc.abstractmethod
    def open(self, scenario: Scenario, seed: int) -> OpenedWorld:
        """Bring the world to the scenario's start, paused. Raises EnvRefusal."""

    @abc.abstractmethod
    def call(self, seat: str, call: ToolCall) -> ToolResult:
        """Execute one tool call as `seat`, the game paused."""

    @abc.abstractmethod
    def advance(self, days: int) -> AdvanceResult:
        """Run the clock `days` game-days and leave the game paused."""

    @abc.abstractmethod
    def metrics(self, seat: str, names: Sequence[str]) -> Metrics:
        """Read the named metrics for `seat` (the game paused)."""

    @abc.abstractmethod
    def close(self) -> None:
        """Release the world (stop the game the backend launched)."""

    def advance_diagnostics(self, adv: AdvanceResult) -> AdvanceDiagnostics:
        """How the step's advance went, as typed numbers (alarm re-arms, reply
        frames dropped as another session's), called once per step after the
        advance and the metric reads. A backend that does not override this
        says so (`not_reported`), never 0."""
        return AdvanceDiagnostics(DIAG_NOT_REPORTED, detail=f"backend {self.name} does not report them")

    def observation(self, seat: str) -> dict[str, Any]:
        """Extra per-seat observation data the backend can read cheaply. The
        environment always adds date, cycle, metrics and the last results."""
        return {}

    def tool_specs(self, seat: str, names: Sequence[str]) -> list[dict[str, Any]]:
        """The input schemas of the named game tools for `seat`, each
        {name, description, inputSchema} (the MCP tool shape). A backend that
        cannot describe its tools refuses; it never invents a schema."""
        from .errors import R_TOOL_SPECS
        raise EnvRefusal(R_TOOL_SPECS, f"backend {self.name} does not describe its tools", "backend")

    def checkpoint(self) -> Mapping[str, Any]:
        raise EnvRefusal(R_CHECKPOINT_UNSUPPORTED, f"backend {self.name} cannot checkpoint", "backend")

    def restore(self, token: Mapping[str, Any]) -> OpenedWorld:
        raise EnvRefusal(R_CHECKPOINT_UNSUPPORTED, f"backend {self.name} cannot restore", "backend")


def backend_for(scenario: Scenario, **kwargs: Any) -> Backend:
    """The backend a scenario declares. ck3 options (saves_dir, ...) pass through."""
    from .errors import R_BACKEND_UNKNOWN
    if scenario.backend == "fake":
        from .fake import FakeBackend
        return FakeBackend(**kwargs)
    if scenario.backend == "ck3":
        from .ck3 import Ck3Backend
        return Ck3Backend(**kwargs)
    raise EnvRefusal(R_BACKEND_UNKNOWN, scenario.backend, "backend")

