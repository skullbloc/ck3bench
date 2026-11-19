"""The FAKE backend: a small scripted world, in-process and deterministic.

NOTHING HERE IS THE GAME. It exists so the environment, the policies, the
server and the trajectory writer can be tested end to end without a game,
and so a policy author can wire a loop before paying for a real episode.
Every record it emits says `backend: fake`, `fake: true`.

The world: each seat rules a realm (counties, gold, prestige, a ruler with an
age); three non-seat neighbours (ids 100-102) hold counties too. Per advance:
income, ageing, a seeded chance of a revolt (one county lost), a seeded
chance of the ruler's death (an heir continues the line with a new character
id, or the line ends), and every war declared this cycle resolved by a seeded
draw weighted by realm size. The same (scenario, seed, actions) gives the same
world, byte for byte.

Failure injection (`fake.fail = {at_step, kind}`) makes the backend die in
each of the ways the real game is known to, so the environment's typed
truncations are tested.
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Any, Optional, Sequence

from . import calendar
from .backend import Backend, OpenedWorld
from .errors import (R_SCENARIO_VALUE, TRUNC_BRIDGE_UNAVAILABLE, TRUNC_CLOCK_STALLED, TRUNC_GAME_EXITED,
                     TRUNC_SCRIPT_LOG_CLOSED, EnvRefusal)
from .scenario import Scenario
from .types import (DIAG_NOT_APPLICABLE, METRIC_OK, METRIC_UNREADABLE, AdvanceDiagnostics, AdvanceResult, Metrics,
                    ToolCall, ToolResult)

NPC_IDS = (100, 101, 102)
NPC_COUNTIES = 6
#: An injected failure that is not an advance's: every metric read from that step on is unreadable.
FAIL_METRICS = "metric_unreadable"
FAIL_KINDS = (TRUNC_GAME_EXITED, TRUNC_CLOCK_STALLED, TRUNC_BRIDGE_UNAVAILABLE, TRUNC_SCRIPT_LOG_CLOSED,
              FAIL_METRICS)
INTENTS = {"raise_taxes": (20.0, -5.0), "hold_feast": (-30.0, 15.0)}   # (gold, prestige)
#: The toolkit's own shape for a gift, modelled: `do {intent: interact, args:
#: {interaction: gift_interaction, target_id}}` moves GIFT_GOLD to the target.
INTERACT = "interact"
GIFT = "gift_interaction"
GIFT_GOLD = 20.0
REVOLT_P = 0.1          # per seat per cycle
HEIR_P = 0.8            # a death leaves an heir
WAR_COST_PRESTIGE = 10.0
FAKE_ERR_NOT_MODELLED = "not_modelled_by_fake"
_OBJ = {"type": "object", "additionalProperties": False}
#: The fake world's tools, described the way an MCP server describes its own.
FAKE_TOOL_SPECS: dict[str, dict[str, Any]] = {
    "observe": {"name": "observe", "description": "[fake backend] Your realm: counties, gold, prestige, age, "
                                                  "neighbours and their counties.", "inputSchema": dict(_OBJ)},
    "query": {"name": "query", "description": "[fake backend] The same view as observe.",
              "inputSchema": {"type": "object"}},
    "pass_turn": {"name": "pass_turn", "description": "[fake backend] Do nothing this step.",
                  "inputSchema": {**_OBJ, "properties": {"reason": {"type": "string"}}}},
    "do": {"name": "do", "description": "[fake backend] raise_taxes: +20 gold, -5 prestige; hold_feast: -30 "
                                        f"gold, +15 prestige; interact with args.interaction {GIFT} and "
                                        f"args.target_id: give {GIFT_GOLD:g} gold to that ruler.",
           "inputSchema": {**_OBJ, "required": ["intent"],
                           "properties": {"intent": {"type": "string", "enum": sorted(INTENTS) + [INTERACT]},
                                          "character_id": {"type": "integer"},
                                          "args": {"type": "object"}}}},
    "declare_war": {"name": "declare_war", "description": "[fake backend] Declare war on a neighbour (costs "
                                                          f"{WAR_COST_PRESTIGE:g} prestige); it resolves at "
                                                          "the end of the step, weighted by realm size.",
                    "inputSchema": {**_OBJ, "required": ["target_id"],
                                    "properties": {"target_id": {"type": "integer"}}}},
}


@dataclass
class _Realm:
    character_id: int
    counties: int
    gold: float
    prestige: float
    age: int
    line: str = "following"     # following | line_ended


class FakeBackend(Backend):
    name = "fake"
    fake = True

    def __init__(self) -> None:
        self.rng: Optional[random.Random] = None
        self.realms: dict[str, _Realm] = {}
        self.npcs: dict[int, int] = {}
        self.date = ""
        self.advances = 0
        self.wars: list[tuple[str, int]] = []
        self.fail: dict[str, Any] = {}
        self.closed = False
        self.calls: list[tuple[str, str]] = []

    # ── Backend ───────────────────────────────────────────────────────────
    def open(self, scenario: Scenario, seed: int) -> OpenedWorld:
        f = dict(scenario.fake)
        n = len(scenario.seats)
        for key in ("counties", "gold", "prestige", "ages"):
            if key not in f or len(f[key]) != n:
                raise EnvRefusal(R_SCENARIO_VALUE, f"fake.{key} must list one value per seat ({n})", "fake")
        fail = f.get("fail") or {}
        if fail and (fail.get("kind") not in FAIL_KINDS or not isinstance(fail.get("at_step"), int)):
            raise EnvRefusal(R_SCENARIO_VALUE, f"fake.fail needs at_step (int) and kind in {FAIL_KINDS}", "fake")
        self.fail = fail
        self.rng = random.Random(seed)
        self.realms = {s.name: _Realm(s.character_id if s.character_id is not None else 1000 + i,
                                      int(f["counties"][i]), float(f["gold"][i]), float(f["prestige"][i]),
                                      int(f["ages"][i]))
                       for i, s in enumerate(scenario.seats)}
        self.npcs = {i: NPC_COUNTIES for i in NPC_IDS}
        self.date, self.advances, self.wars = scenario.start_date, 0, []
        return OpenedWorld(self.date, {k: r.character_id for k, r in self.realms.items()},
                           {"backend": self.name, "fake": True, "seed": seed,
                            "world": "scripted; not the game"})

    def call(self, seat: str, call: ToolCall) -> ToolResult:
        self.calls.append((seat, call.tool_name))
        r = self.realms[seat]
        a = call.arguments
        if r.line == "line_ended":
            return ToolResult(call.tool_name, False, error="line_ended: this seat has no ruler")
        if call.tool_name in ("observe", "query"):
            return ToolResult(call.tool_name, True, self._view(seat))
        if call.tool_name == "pass_turn":
            return ToolResult(call.tool_name, True, {"passed": True, "reason": a.get("reason", "")})
        if call.tool_name == "do" and a.get("intent") == INTERACT:
            return self._interact(seat, a.get("args") or {})
        if call.tool_name == "do":
            intent = a.get("intent")
            if intent not in INTENTS:
                return ToolResult("do", False, error=f"intent_unknown: {intent!r} not in {sorted(INTENTS) + [INTERACT]}")
            dg, dp = INTENTS[intent]
            if r.gold + dg < 0:
                return ToolResult("do", False, error="not_enough_gold")
            r.gold += dg
            r.prestige += dp
            return ToolResult("do", True, {"intent": intent, "gold": r.gold, "prestige": r.prestige})
        if call.tool_name == "declare_war":
            target = a.get("target_id")
            targets = self._targets(seat)
            if target not in targets:
                return ToolResult("declare_war", False, error=f"invalid_target: {target!r} not in {targets}")
            if r.prestige < WAR_COST_PRESTIGE:
                return ToolResult("declare_war", False, error="not_enough_prestige")
            r.prestige -= WAR_COST_PRESTIGE
            self.wars.append((seat, int(target)))
            return ToolResult("declare_war", True, {"declared": True, "target_id": target,
                                                    "resolves": "at the end of this cycle"})
        return ToolResult(call.tool_name, False, error=f"{FAKE_ERR_NOT_MODELLED}: {call.tool_name}")

    def advance(self, days: int) -> AdvanceResult:
        self.advances += 1
        if self.fail and self.fail["at_step"] == self.advances and self.fail["kind"] != FAIL_METRICS:
            kind = self.fail["kind"]
            return AdvanceResult(self.date, False, kind, f"fake failure injected at step {self.advances}")
        assert self.rng is not None
        rng = self.rng
        for name in sorted(self.realms):
            r = self.realms[name]
            if r.line == "line_ended":
                continue
            r.gold += r.counties * 0.1 * days / 30.0
            if rng.random() < REVOLT_P and r.counties > 1:
                r.counties -= 1
        for seat, target in self.wars:
            a = self.realms[seat]
            if a.line == "line_ended":
                continue
            t_c = self._counties_of(target)
            if t_c <= 0:
                continue
            if rng.random() < a.counties / (a.counties + t_c):
                a.counties += 1
                self._set_counties(target, t_c - 1)
            else:
                a.counties = max(0, a.counties - 1)
                self._set_counties(target, t_c + 1)
        self.wars = []
        years_before = calendar.ordinal(self.date) // calendar.DAYS_PER_YEAR
        self.date = calendar.add_days(self.date, days)
        years = calendar.ordinal(self.date) // calendar.DAYS_PER_YEAR - years_before
        for name in sorted(self.realms):
            r = self.realms[name]
            if r.line == "line_ended":
                continue
            r.age += years
            p_death = max(0.0, (r.age - 45) * 0.02) * days / 365.0
            if r.counties == 0 or rng.random() < p_death:
                if r.counties > 0 and rng.random() < HEIR_P:
                    r.character_id, r.age = r.character_id + 1000, 20
                else:
                    r.line, r.counties = "line_ended", 0
        return AdvanceResult(self.date, True)

    def advance_diagnostics(self, adv: AdvanceResult) -> AdvanceDiagnostics:
        return AdvanceDiagnostics(DIAG_NOT_APPLICABLE, detail="the fake world has no pause alarm and no bridge")

    def metrics(self, seat: str, names: Sequence[str]) -> Metrics:
        r = self.realms[seat]
        unreadable = (self.fail.get("kind") == FAIL_METRICS and self.advances >= self.fail["at_step"])
        # the fake world has no vassal tree: every county a realm has, its ruler holds himself
        values = {"realm_counties": r.counties, "demesne_counties": r.counties, "gold": round(r.gold, 5),
                  "prestige": round(r.prestige, 5), "line_alive": r.line != "line_ended"}
        m = Metrics(self.date, r.character_id, line_status=r.line)
        for n in names:
            if unreadable:
                m.status[n], m.source[n] = METRIC_UNREADABLE, "fake: failure injected"
            else:
                m.values[n], m.status[n], m.source[n] = values[n], METRIC_OK, "fake world"
        return m

    def observation(self, seat: str) -> dict[str, Any]:
        return {"fake": True, "neighbours": self._targets(seat)}

    def tool_specs(self, seat: str, names: Sequence[str]) -> list[dict[str, Any]]:
        out = []
        for n in names:
            spec = FAKE_TOOL_SPECS.get(n)
            out.append(dict(spec) if spec is not None else
                       {"name": n, "description": f"[fake backend] {n} is not modelled; it answers "
                                                  f"{FAKE_ERR_NOT_MODELLED}",
                        "inputSchema": {"type": "object"}})
        return out

    def close(self) -> None:
        self.closed = True

    def _interact(self, seat: str, args: Any) -> ToolResult:
        r = self.realms[seat]
        if not isinstance(args, dict) or args.get("interaction") != GIFT:
            return ToolResult("do", False, error=f"{FAKE_ERR_NOT_MODELLED}: interaction "
                                                 f"{(args or {}).get('interaction') if isinstance(args, dict) else args!r}")
        target = args.get("target_id")
        if target not in self._targets(seat):
            return ToolResult("do", False, error=f"invalid_target: {target!r} not in {self._targets(seat)}")
        if r.gold < GIFT_GOLD:
            return ToolResult("do", False, error="not_enough_gold")
        r.gold -= GIFT_GOLD
        for other in self.realms.values():
            if other.character_id == target:
                other.gold += GIFT_GOLD
        return ToolResult("do", True, {"verdict": "applied", "interaction": GIFT, "target_id": target,
                                       "gold": round(r.gold, 5), "fake": True})

    # ── the world ─────────────────────────────────────────────────────────
    def _view(self, seat: str) -> dict[str, Any]:
        r = self.realms[seat]
        return {"date": self.date, "character_id": r.character_id, "counties": r.counties,
                "gold": round(r.gold, 5), "prestige": round(r.prestige, 5), "age": r.age,
                "neighbours": {t: self._counties_of(t) for t in self._targets(seat)}, "fake": True}

    def _targets(self, seat: str) -> list[int]:
        others = [r.character_id for k, r in sorted(self.realms.items()) if k != seat and r.line != "line_ended"]
        return others + [i for i in NPC_IDS if self.npcs[i] > 0]

    def _counties_of(self, cid: int) -> int:
        if cid in self.npcs:
            return self.npcs[cid]
        for r in self.realms.values():
            if r.character_id == cid:
                return r.counties
        return 0

    def _set_counties(self, cid: int, n: int) -> None:
        if cid in self.npcs:
            self.npcs[cid] = n
            return
        for r in self.realms.values():
            if r.character_id == cid:
                r.counties = n
