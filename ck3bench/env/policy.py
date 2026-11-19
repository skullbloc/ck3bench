"""Policies: whatever drives a seat.

A policy is anything with `act(observation, tools) -> action`, where `tools`
is the seat's `ToolHandle` (in-turn calls, game paused, each one recorded) and
the action is a list of tool calls executed at the step (empty: the game's own
AI plays the seat). `report_usage()` may return the tokens the policy spent
since its last report; it goes into the step record as reported, never
estimated.

Reference policies here: `NoOpPolicy` (the game's AI plays: the baseline),
`RandomPolicy` (seeded, over a declared menu of calls), `ScriptedPolicy` (a
fixed per-step list). An LLM policy is the same interface with a model behind
it: it reads the observation (`render_text` gives a plain-text view), makes
in-turn calls through `tools.call` while it thinks, and returns its action.
A policy in another language drives a seat through the env server instead
(server.py), with the same shapes.
"""
from __future__ import annotations

import json
import random
from typing import Any, Callable, Mapping, Optional, Protocol, Sequence

from .errors import R_POLICY_NOT_APPLICABLE, R_POLICY_UNKNOWN, EnvRefusal
from .types import ToolCall, ToolResult


R_TURN_CLOSED = "turn_closed"


class ToolHandle:
    """One seat's in-turn tool access for ONE turn. `specs` are the input
    schemas of the seat's tools (a list, or a function fetching them on first
    use). After `close()` every call answers `turn_closed` without reaching the
    environment: a policy thread that overran its turn cannot act in the next."""

    def __init__(self, call: Callable[[str, ToolCall], ToolResult], seat: str, tools: Sequence[str],
                 specs: Optional[Sequence[dict] | Callable[[], Sequence[dict]]] = None):
        self._call, self.seat, self.tools = call, seat, tuple(tools)
        self._specs = specs
        self.closed = False

    @property
    def specs(self) -> Optional[list[dict]]:
        if callable(self._specs):
            self._specs = list(self._specs())
        return list(self._specs) if self._specs is not None else None

    def close(self) -> None:
        self.closed = True

    def call(self, tool_name: str, **arguments: Any) -> ToolResult:
        if self.closed:
            return ToolResult(tool_name, False, error=f"{R_TURN_CLOSED}: this turn is over; nothing was called")
        return self._call(self.seat, ToolCall(tool_name, arguments))


class Policy(Protocol):
    def act(self, observation: Mapping[str, Any], tools: ToolHandle) -> list[ToolCall]: ...


class NoOpPolicy:
    """Does nothing: the seat is played by the game's own AI. The baseline."""
    name = "noop"

    def act(self, observation, tools):
        return []

    def report_usage(self):
        return None


class RandomPolicy:
    """Picks uniformly from `menu` (a list of tool calls, or of factories
    taking the observation) with its own seeded generator; `p_act` is the
    chance it acts at all on a step."""
    name = "random"

    def __init__(self, seed: int, menu: Sequence[ToolCall | Callable[[Mapping[str, Any]], ToolCall]],
                 p_act: float = 0.5):
        if not menu:
            raise EnvRefusal(R_POLICY_UNKNOWN, "a random policy needs a non-empty menu", "policy")
        self.rng = random.Random(seed)
        self.menu = list(menu)
        self.p_act = p_act

    def act(self, observation, tools):
        if self.rng.random() >= self.p_act:
            return []
        pick = self.menu[self.rng.randrange(len(self.menu))]
        return [pick(observation) if callable(pick) else pick]

    def report_usage(self):
        return None


class ScriptedPolicy:
    """Step k (1-based) returns script[k-1]; past the end, []. `turn_calls`
    makes in-turn calls first (e.g. an observe) so the trajectory shows them."""
    name = "scripted"

    def __init__(self, script: Sequence[Sequence[ToolCall]], turn_calls: Sequence[ToolCall] = ()):
        self.script = [list(s) for s in script]
        self.turn_calls = list(turn_calls)
        self.k = 0

    def act(self, observation, tools):
        for c in self.turn_calls:
            tools.call(c.tool_name, **dict(c.arguments))
        self.k += 1
        return list(self.script[self.k - 1]) if self.k <= len(self.script) else []

    def report_usage(self):
        return None


def fake_menu(observation: Mapping[str, Any]) -> list:
    """A random-policy menu for the FAKE world's own tools (its `do` intents
    are the fake's: the toolkit does not have them; see `game_menu`)."""
    def war(obs):
        targets = (obs.get("world") or {}).get("neighbours") or [0]
        return ToolCall("declare_war", {"target_id": targets[0]})
    return [ToolCall("do", {"intent": "raise_taxes"}), ToolCall("do", {"intent": "hold_feast"}), war,
            ToolCall("pass_turn", {"reason": "random"})]


def gift_call(actor_id: int, target_id: int) -> ToolCall:
    """The toolkit's gift: `do {intent: interact}` with `gift_interaction`. Read
    live with the game paused: verdict `applied` (path `script`), both purses
    moved at once; a second gift the king could not afford was `rejected` with
    the game's own reason."""
    return ToolCall("do", {"intent": "interact", "character_id": int(actor_id),
                           "args": {"interaction": "gift_interaction", "target_id": int(target_id)}})


#: Who a game task's seat gifts to: (start save, start date, the seat's character
#: at the start) -> the recipient. Only a pair whose gift was seen applied live is
#: here: the King of France to the Duke of Normandy, his largest vassal, at 1066.09.15.
GIFT_TARGETS: dict[tuple[str, str, int], int] = {("_1066_09_15.ck3", "1066.09.15", 37067): 31808}


def _gift_target(scenario) -> Optional[int]:
    """The recipient for this scenario's first seat, None on the fake backend
    (its world names the neighbours), refused on a game task with none known."""
    if scenario is None or scenario.backend == "fake":
        return None
    seat = scenario.seats[0]
    key = (scenario.start_save, scenario.start_date, seat.character_id)
    if key not in GIFT_TARGETS:
        raise EnvRefusal(R_POLICY_NOT_APPLICABLE, f"no gift recipient is known for {scenario.id} (save "
                                                  f"{scenario.start_save}, {scenario.start_date}, character "
                                                  f"{seat.character_id}); known: {sorted(GIFT_TARGETS)}", "policy")
    return GIFT_TARGETS[key]


class ObserveThenGiftPolicy:
    """Each turn an in-turn `observe`; on its FIRST step one gift (the write
    seen applied live); afterwards no action. `target_id` None takes the first
    neighbour the fake world lists."""
    name = "observe-then-gift"

    def __init__(self, target_id: Optional[int]):
        self.target_id = target_id
        self.k = 0

    def act(self, observation, tools):
        tools.call("observe")
        self.k += 1
        if self.k != 1:
            return []
        target = self.target_id
        if target is None:
            neighbours = (observation.get("world") or {}).get("neighbours") or []
            if not neighbours:
                raise EnvRefusal(R_POLICY_NOT_APPLICABLE, "the observation names no neighbour to gift to", "policy")
            target = neighbours[0]
        return [gift_call(observation["character_id"], target)]

    def report_usage(self):
        return None


def game_menu(scenario) -> list:
    """A random-policy menu for a GAME task: only calls the toolkit accepts
    (pass_turn, and the gift where a recipient is known)."""
    menu: list = [ToolCall("pass_turn", {"reason": "random"})]
    try:
        target = _gift_target(scenario)
    except EnvRefusal:
        target = None
    if target is not None:
        cid = scenario.seats[0].character_id
        menu.append(lambda obs, t=target, c=cid: gift_call(obs.get("character_id") or c, t))
    return menu


#: The policies a name selects (`ck3bench env run --policy`, `play --seat S=P`).
POLICY_NAMES = ("noop", "random", "observe-then-gift")


def make_policy(name: str, seed: int, scenario=None, observation: Optional[Mapping[str, Any]] = None):
    """The CLI's policies by name, for `scenario` (a game task gets only calls
    the toolkit accepts; the fake world its own menu)."""
    if name == "noop":
        return NoOpPolicy()
    if name == "random":
        if scenario is not None and scenario.backend != "fake":
            return RandomPolicy(seed, game_menu(scenario))
        return RandomPolicy(seed, fake_menu(observation or {}))
    if name == "observe-then-gift":
        return ObserveThenGiftPolicy(_gift_target(scenario))
    raise EnvRefusal(R_POLICY_UNKNOWN, f"{name!r} is not one of {', '.join(POLICY_NAMES)}", "policy")


def render_text(obs: Mapping[str, Any]) -> str:
    """A plain-text view of an observation for a language model's prompt."""
    lines = [f"You play seat {obs.get('seat')} (character {obs.get('character_id')}).",
             f"Date {obs.get('game_date')}; step {obs.get('step')} of {obs.get('steps_total')}, "
             f"{obs.get('cycle_days')} game-days per step, the episode ends {obs.get('end_date')}.",
             "Metrics: " + ", ".join(f"{k}={v}" for k, v in sorted((obs.get("metrics") or {}).items())),
             f"Tools: {', '.join(obs.get('tools') or [])}."]
    if obs.get("fake"):
        lines.insert(0, "[FAKE BACKEND: a scripted test world, not the game]")
    for r in obs.get("last_results") or []:
        lines.append(f"Last cycle you called {json.dumps(r['call'])} -> {json.dumps(r['result'])[:400]}")
    return "\n".join(lines)


def run_episode(env, policies: Mapping[str, Any], *, seed: Optional[int] = None,
                options: Optional[Mapping[str, Any]] = None) -> dict[str, Any]:
    """Drive one episode of a ParallelEnv with one policy per seat. Returns
    {returns, steps, end_reason, seed}."""
    obs, infos = env.reset(seed=seed, options=options)
    seed_used = next(iter(infos.values()))["seed"] if infos else seed
    missing = [s for s in env.possible_agents if s not in policies]
    if missing:
        raise EnvRefusal(R_POLICY_UNKNOWN, f"no policy for seats {missing}", "policy")
    returns = {s: 0.0 for s in env.agents}
    steps = 0
    while env.agents:
        actions, usage, turns = {}, {}, {}
        for s in env.agents:
            pol = policies[s]
            handle = ToolHandle(env.call, s, env.scenario.tools, specs=lambda s=s: env.tool_specs(s))
            actions[s] = pol.act(obs[s], handle)
            handle.close()
            u = getattr(pol, "report_usage", lambda: None)()
            if u:
                usage[s] = u
            t = getattr(pol, "report_turn", lambda: None)()
            if t:
                turns[s] = t
        obs, rewards, _, _, infos = env.step(actions, usage=usage or None, turns=turns or None)
        steps += 1
        for s, r in rewards.items():
            returns[s] = returns[s] + r
    return {"returns": returns, "steps": steps, "end_reason": env.end_reason, "seed": seed_used}
