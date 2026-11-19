"""Seats by census rank (census.py) on a written census, and through the real
backend's open with a fake bridge serving it."""
from __future__ import annotations

import pytest

from ck3bench.env import census
from ck3bench.env.errors import R_CENSUS_READ, R_CENSUS_TOO_FEW, EnvRefusal
from ck3bench.env.scenario import BUILTIN_DICTS, builtin

KING = 37067
#: (id, tier, realm_counties, military_strength): five dukes, a count, a landless duke, a duke with no row
VASSALS = [(501, 3, 9, 4000), (502, 3, 4, 9000), (503, 3, 6, 4000), (504, 2, 3, 9999), (505, 3, 0, 8000),
           (506, 3, 2, 100), (507, 3, 5, 2500), (508, None, None, 7000)]
RANKED = [502, 501, 503, 507, 506]          # by strength, ties by lowest id; 504 count, 505 landless, 508 no row


def _replies():
    rows = [{"id": i, "tier": t, "realm_counties": c} for i, t, c, _ in VASSALS if t is not None]
    return {"search_characters": {"characters": rows},
            "query_realm": {"vassals": [{"id": i, "military_strength": m} for i, *_, m in VASSALS]}}


def _reader(replies, log=None):
    def read(action, params):
        if log is not None:
            log.append((action, dict(params)))
        if action == "query_realm" and params != {"character_id": KING}:
            return {"error": "not the liege"}
        return replies.get(action, {"error": f"unknown {action}"})
    return read


def test_the_rule_ranks_tier_3_landed_vassals_by_strength():
    log: list = []
    ranked, rejected = census.rank_vassals(_reader(_replies(), log), KING)
    assert [d["id"] for d in ranked] == RANKED
    assert sorted((r["id"], r["why"]) for r in rejected) == [(504, "tier=2"), (505, "realm_counties=0"),
                                                             (508, "no_ruler_row")]
    assert log[0] == ("search_characters", census.LISTING_PARAMS)


def test_select_by_rank_maps_seats_and_refuses_ranks_past_the_qualifiers():
    read = _reader(_replies())
    chosen, rec = census.select_by_rank(read, KING, {"duke1": 1, "duke5": 5})
    assert chosen == {"duke1": RANKED[0], "duke5": RANKED[4]}
    assert rec["liege_id"] == KING and rec["ranked"] == RANKED and rec["rejected"] == 3
    with pytest.raises(EnvRefusal) as e:
        census.select_by_rank(read, KING, {"far": len(RANKED) + 1})
    assert e.value.code == R_CENSUS_TOO_FEW


@pytest.mark.parametrize("broken", ["search_characters", "query_realm"])
@pytest.mark.parametrize("reply", [{"success": False, "reason": "not_ready"}, {"error": "x"}, {"ok": False}, None])
def test_a_refused_census_read_is_refused_never_an_empty_census(broken, reply):
    rep = dict(_replies(), **{broken: reply})
    with pytest.raises(EnvRefusal) as e:
        census.rank_vassals(_reader(rep), KING)
    assert e.value.code == R_CENSUS_READ


def test_a_realm_reply_without_a_vassals_list_is_refused():
    rep = dict(_replies(), query_realm={"realm_total_counties": 84})
    with pytest.raises(EnvRefusal) as e:
        census.rank_vassals(_reader(rep), KING)
    assert e.value.code == R_CENSUS_READ


def test_the_real_backend_opens_the_council_with_census_seats(tmp_path):
    """france1066-council-10y through Ck3Backend.open, the bridge a fake that
    serves the written census (the clock and watch as in test_ck3_backend)."""
    from ck3bench.env import ParallelEnv
    from ck3bench.env import test_ck3_backend as t
    rep = _replies()

    class CensusBridge(t.FakeBridge):
        def call(self, action, params=None, timeout=8.0):
            if action == "search_characters" or (action == "query_realm" and params == {"character_id": KING}):
                self.calls.append((action, params or {}))
                return rep[action]
            return super().call(action, params, timeout)

    br = CensusBridge(speed=0)
    for d in RANKED:
        br.realm[d] = 5
    b, _, _, tr = t._backend(tmp_path, bridge=br)
    env = ParallelEnv(builtin("france1066-council-10y"), backend=b)
    obs, infos = env.reset(seed=1)
    assert env.backend.seats == {"king": KING, **{f"duke{i}": RANKED[i - 1] for i in range(1, 6)}}
    assert {s: tr[s].env["CK3_AGENT_CHARACTER_ID"] for s in tr} == {s: str(c) for s, c in env.backend.seats.items()}
    assert [c for c in br.calls if c[0] == "search_characters"] == [("search_characters", census.LISTING_PARAMS)]
    assert env.scenario.reward.team == "seat" and env.scenario.reward.team_seat == "king"
    assert "comms" in obs["duke3"]
    env.close()


def test_the_council_says_what_ran_live_and_what_did_not():
    d = BUILTIN_DICTS["france1066-council-10y"]
    sc = builtin("france1066-council-10y")
    assert sc.status == "buildable_now"
    assert "ran live" in sc.status_reason and "have not been run" in sc.status_reason
    assert sc.comms is not None and set(sc.comms.tools()) == {"msg_send", "note_read", "note_write"}
    assert [s.census_rank for s in sc.seats[1:]] == [1, 2, 3, 4, 5]
    assert d["reward"]["team"] == "seat"
