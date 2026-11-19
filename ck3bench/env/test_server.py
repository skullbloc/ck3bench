"""The env server over real HTTP on loopback, driven by the minimal client and
by raw JSON (what a policy in another language would send)."""
from __future__ import annotations

import json
import urllib.request

import pytest

from ck3bench.env import ParallelEnv
from ck3bench.env.errors import EnvRefusal
from ck3bench.env.server import EnvClient, EnvServer


@pytest.fixture
def served():
    srv = EnvServer(ParallelEnv("fake-duel")).start()
    yield srv, EnvClient(srv.url, timeout=30)
    srv.stop()


def test_a_client_plays_a_whole_episode_over_http(served):
    _, c = served
    spec = c.spec()
    assert spec["possible_agents"] == ["east", "west"] and spec["fake"] is True
    obs, infos = c.reset(seed=9)
    assert set(obs) == {"east", "west"} and infos["east"]["seed"] == 9
    r = c.call("east", "observe")
    assert r["ok"] is True and r["result"]["fake"] is True
    n, live = 0, ["east", "west"]
    while live:
        obs, rew, te, tr, infos = c.step({s: [{"tool_name": "do", "arguments": {"intent": "raise_taxes"}}]
                                          for s in live})
        live = [s for s in live if not (te[s] or tr[s])]
        n += 1
    assert n == 8 and c.state()["agents"] == []


def test_a_refusal_comes_back_typed_over_http(served):
    _, c = served
    with pytest.raises(EnvRefusal) as e:
        c.step({"east": []})
    assert e.value.code == "not_reset"
    c.reset(seed=1)
    with pytest.raises(EnvRefusal) as e:
        c.step({"east": [{"tool": "observe"}], "west": []})
    assert e.value.code == "action_malformed"


def test_raw_json_and_status_codes(served):
    srv, _ = served

    def post(path, body):
        req = urllib.request.Request(srv.url + path, data=body, method="POST",
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())
    assert post("/reset", b"not json")[0] == 400
    assert post("/nowhere", b"{}")[1]["code"] == "bad_request"
    assert post("/reset", b'{"seed": 2}')[0] == 200
    code, out = post("/step", b'{"actions": {"east": [], "pretender": []}}')
    assert code == 409 and out["code"] == "unknown_seat"


def test_a_nan_reward_crosses_the_wire_as_null_with_its_status():
    from ck3bench.env.scenario import BUILTIN_DICTS, scenario_from_dict
    d = json.loads(json.dumps(BUILTIN_DICTS["fake-solo"]))
    d["fake"]["fail"] = {"at_step": 1, "kind": "game_exited"}
    srv = EnvServer(ParallelEnv(scenario_from_dict(d))).start()
    try:
        c = EnvClient(srv.url, timeout=30)
        c.reset(seed=1)
        _, rew, _, tr, infos = c.step({"king": []})
        assert rew == {"king": None} and tr["king"] is True
        assert infos["king"]["reward_status"] == "unavailable"
        assert infos["king"]["truncation_reason"] == "game_exited"
    finally:
        srv.stop()


def test_a_non_loopback_bind_is_refused_without_allow_remote():
    with pytest.raises(EnvRefusal) as e:
        EnvServer(ParallelEnv("fake-solo"), host="0.0.0.0")
    assert e.value.code == "remote_bind_refused"
