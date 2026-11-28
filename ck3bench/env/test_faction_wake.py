"""A wake event when a faction against the seat crosses the demand power threshold (a claimant faction far over
it can send its demand months after the seat's last read). The real backend's check reads query_faction_threat on the control session; the fake bridge
here is test_wake_ck3's with that read added."""
from __future__ import annotations

from ck3bench.env.test_wake_ck3 import WakeBridge, _env, _step


class FacBridge(WakeBridge):
    def __init__(self, **kw):
        super().__init__(**kw)
        self.factions: list[dict] = [{"faction_id": 60, "power_ratio": 60.0, "at_war": False}]

    def call(self, action, params=None, timeout=8.0):
        if action == "query_faction_threat":
            self.calls.append((action, params))
            return {"success": True, "factions": [dict(f) for f in self.factions],
                    "character_id": (params or {}).get("character_id")}
        return super().call(action, params, timeout)


def _grow(power):
    def f(w):
        w.factions[0]["power_ratio"] = power
    return f


def test_a_faction_crossing_the_threshold_wakes_the_seat(tmp_path):
    br = FacBridge()
    br.at["1066.11.20"] = _grow(171.4)
    env, br, seats = _env(tmp_path, bridge=br)
    assert "faction_over_threshold" in env.backend.wake_vocabulary()["events"]
    obs, w = _step(env, {"max_days": 365, "when": [{"event": "faction_over_threshold"}]})
    assert w["stop"] == "condition" and w["fired"][0]["read"] == ["60"]
    assert any(a == "query_faction_threat" for a, _ in br.calls)


def test_a_faction_already_over_or_at_war_does_not_fire(tmp_path):
    br = FacBridge()
    br.factions = [{"faction_id": 60, "power_ratio": 120.0, "at_war": False},
                   {"faction_id": 61, "power_ratio": 50.0, "at_war": False}]
    br.at["1066.11.20"] = lambda w: w.factions[1].update(power_ratio=150.0, at_war=True)
    env, br, seats = _env(tmp_path, bridge=br)
    obs, w = _step(env, {"max_days": 120, "when": [{"event": "faction_over_threshold"}]})
    assert w["stop"] == "max_days" and not w["fired"]
