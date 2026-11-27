"""A seat may ask to be woken when the primary heir becomes one of another dynasty (`heir_other_dynasty`): a son of his wife's dynasty by a matrilineal marriage can be born and
inherit every title with no read between. The real backend reads query_title_succession_map (the bridge's line, the
holder's children first) and the character's dynasty_id."""
from __future__ import annotations

from ck3bench.env import wake
from ck3bench.env.ck3 import WAKE_EVENTS
from ck3bench.env.test_wake_ck3 import DUBLIN, WakeBridge, _env, _step


class HeirBridge(WakeBridge):
    def __init__(self, **kw):
        super().__init__(**kw)
        self.heir = {"primary_heir_id": 33804, "primary_heir_dynasty_id": 2032}

    def call(self, action, params=None, timeout=8.0):
        if action == "query_title_succession_map":
            self.calls.append((action, params))
            return {"success": True, "character_id": (params or {}).get("character_id"), **self.heir}
        r = super().call(action, params, timeout)
        if action == "query_character" and isinstance(r, dict):
            r = dict(r, dynasty_id=2032)
        return r


def test_the_event_is_in_the_vocabulary_and_a_gained_kind():
    assert "heir_other_dynasty" in WAKE_EVENTS and "heir_other_dynasty" in wake.EVENTS
    assert "heir_other_dynasty" in wake.GAINED


def test_holds_on_a_new_other_dynasty_heir_only():
    start = {"character_id": 34471, "events": {"heir_other_dynasty": []}}
    now = {"character_id": 34471, "events": {"heir_other_dynasty": ["69510"]}}
    assert wake.holds({"event": "heir_other_dynasty"}, start, now) == (True, ["69510"])
    # CONTROL: already the heir at the start, or unread at the check: no wake
    assert wake.holds({"event": "heir_other_dynasty"}, now, now)[0] is False
    assert wake.holds({"event": "heir_other_dynasty"}, start,
                      {"character_id": 34471, "events": {"heir_other_dynasty": None}})[0] is False


def test_a_son_of_another_dynasty_becoming_heir_wakes_the_seat(tmp_path):
    br = HeirBridge()
    br.at["1066.11.20"] = lambda w: w.heir.update(primary_heir_id=69510, primary_heir_dynasty_id=2281)
    env, br, seats = _env(tmp_path, bridge=br)
    assert "heir_other_dynasty" in env.backend.wake_vocabulary()["events"]
    obs, w = _step(env, {"max_days": 365, "when": [{"event": "heir_other_dynasty"}]})
    assert w["stop"] == "condition" and w["fired"][0]["read"] == ["69510"]
    assert any(a == "query_title_succession_map" and p.get("character_id") == DUBLIN for a, p in br.calls)


def test_CONTROL_a_same_dynasty_heir_change_does_not_wake(tmp_path):
    br = HeirBridge()
    br.at["1066.11.20"] = lambda w: w.heir.update(primary_heir_id=69511, primary_heir_dynasty_id=2032)
    env, br, seats = _env(tmp_path, bridge=br)
    obs, w = _step(env, {"max_days": 120, "when": [{"event": "heir_other_dynasty"}]})
    assert w["stop"] == "max_days" and not w["fired"]
