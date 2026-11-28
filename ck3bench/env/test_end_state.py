"""A seat's last read can be the treasury at the step's start (293.68 gold) where the episode ended with 365.76, so
every episode end (`info.end`, which the shim's ack reply carries as
`your_episode_ended`) carries `final`: the ruler, gold, realm counties and the target title's reads at the end
date; a value not read is `unread` with its reason, never a number. Tested on stand-in backends only."""
from __future__ import annotations

from ck3bench.env import ParallelEnv, contract
from ck3bench.env.errors import TRUNC_HORIZON
from ck3bench.env.fake import FakeBackend
from ck3bench.env.server import EnvClient, EnvServer
from ck3bench.env.mcp_shim import SeatShim
from ck3bench.env.types import Metrics


def test_c_the_horizon_end_carries_the_final_gold_counties_and_ruler():
    env = ParallelEnv("fake-solo")
    out = contract.drive(env, seed=1)
    info = out["last_infos"]["king"]
    end = info["end"]
    assert end["reason"] == TRUNC_HORIZON
    fin = end["final"]
    realm = env.backend.realms["king"]
    assert fin["date"] == info["game_date"] and fin["character_id"] == realm.character_id
    assert fin["gold"] == round(realm.gold, 5) and fin["realm_counties"] == realm.counties


def test_c_a_final_value_not_read_is_unread_with_its_reason(monkeypatch):
    env = ParallelEnv("fake-solo")
    env.reset(seed=1)
    m = Metrics("1096.09.15", 7, values={}, status={}, line_status="following")      # the step read nothing

    def no_gold(self, seat, names):
        out = Metrics("1096.09.15", 7)
        for n in names:
            out.status[n], out.source[n] = "unreadable", f"query_character gave no {n}"
        return out
    monkeypatch.setattr(FakeBackend, "metrics", no_gold)
    fin = env._final_state("king", m, "1096.09.15")
    assert fin["gold"] == "unread: query_character gave no gold" and fin["character_id"] == 7
    env.close()


def test_c_the_ack_reply_of_the_last_step_carries_the_final_state():
    env = ParallelEnv("fake-solo")
    srv = EnvServer(env, turn_timeout_s=30).start()
    cl = EnvClient(srv.url, timeout=30)
    cl.reset(seed=1)
    try:
        while True:
            out, err = SeatShim(cl, "king", 30, ack=True).call("end_turn", {"summary": "pass"})
            assert not err
            if out["episode_over"]:
                break
        assert isinstance(out["your_episode_ended"]["final"]["gold"], float)
    finally:
        srv.stop()
