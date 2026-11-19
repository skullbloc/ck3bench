"""The reference tool-calling agent (agent.py) with a scripted model, and the
optional Anthropic adapter's translation with a stub client (no network)."""
from __future__ import annotations

import io
import json

import pytest

from ck3bench.env import NoOpPolicy, ParallelEnv, run_episode
from ck3bench.env.agent import (END_TURN, T_END_TURN, T_MAX_CALLS, T_MAX_WALL, T_MODEL_ERROR, T_MODEL_STOPPED,
                                ModelReply, ModelToolCall, ScriptedModel, ToolCallingAgent, calls)
from ck3bench.env.anthropic_model import AnthropicModel, from_response, to_request
from ck3bench.env.errors import EnvRefusal
from ck3bench.env.policy import ToolHandle
from ck3bench.env.trajectory import TrajectoryWriter, check_trajectory


def _turn(model, *, max_calls=10, max_wall_s=60.0, clock=None, scenario="fake-council", seat="king"):
    env = ParallelEnv(scenario)
    obs, _ = env.reset(seed=4)
    kw = {"clock": clock} if clock else {}
    agent = ToolCallingAgent(model, max_calls=max_calls, max_wall_s=max_wall_s, **kw)
    h = ToolHandle(env.call, seat, env.scenario.tools, specs=lambda: env.tool_specs(seat))
    action = agent.act(obs[seat], h)
    return env, agent, action


def test_the_loop_runs_calls_in_turn_feeds_results_back_and_ends_on_end_turn():
    m = ScriptedModel([calls(("observe", {}), ("do", {"intent": "raise_taxes"}), usage={"input_tokens": 100,
                                                                                         "output_tokens": 7}),
                       calls((END_TURN, {"summary": "taxed"}), usage={"input_tokens": 150, "output_tokens": 3})])
    env, agent, action = _turn(m)
    assert action == [], "the agent acts through in-turn calls"
    assert env.backend.calls == [("king", "observe"), ("king", "do")]
    second = m.requests[1]["messages"]
    tool_msgs = [x for x in second if x["role"] == "tool"]
    assert [x["name"] for x in tool_msgs] == ["observe", "do"]
    assert json.loads(tool_msgs[1]["content"])["result"]["intent"] == "raise_taxes"
    assert m.requests[0]["tools"] == list(env.scenario.tools) + [END_TURN], "the env's specs, plus end_turn"
    assert "[FAKE BACKEND" in m.requests[0]["messages"][1]["content"]
    turn = agent.report_turn()
    assert turn.pop("wall_s") >= 0
    assert turn == {"status": T_END_TURN, "tool_calls": 2, "model_calls": 2, "stop": "tool_use", "detail": "taxed"}
    assert agent.report_usage() == {"input_tokens": 250, "output_tokens": 10, "model_calls": 2,
                                    "model_calls_unreported": 0}
    assert agent.report_usage() is None, "a report is handed over once"


def test_a_reply_with_no_tool_call_ends_the_turn_model_stopped():
    _, agent, _ = _turn(ScriptedModel([ModelReply(text="I will wait.", stop="end_turn")]))
    t = agent.report_turn()
    assert t["status"] == T_MODEL_STOPPED and "end_turn" in t["detail"]
    assert agent.report_usage() == {"model_calls": 1, "model_calls_unreported": 1}, "absent usage is not zero"


def test_the_call_budget_ends_the_turn_and_every_requested_call_gets_a_result():
    m = ScriptedModel([calls(("observe", {}), ("observe", {}), ("observe", {})), calls(("observe", {}))])
    env, agent, _ = _turn(m, max_calls=2)
    t = agent.report_turn()
    assert t["status"] == T_MAX_CALLS and t["tool_calls"] == 2
    assert len(env.backend.calls) == 2, "the third call never reached the env"
    msgs = agent.transcripts[-1]
    asked = [c["id"] for x in msgs if x["role"] == "assistant" for c in x["tool_calls"]]
    answered = [x["tool_call_id"] for x in msgs if x["role"] == "tool"]
    assert asked == answered and msgs[-1]["is_error"] and "budget" in msgs[-1]["content"]


def test_the_wall_budget_ends_the_turn_between_calls():
    t = [0.0]

    def clock():
        t[0] += 4.0
        return t[0]
    m = ScriptedModel([calls(("observe", {})), calls(("observe", {})), calls(("observe", {}))])
    _, agent, _ = _turn(m, max_wall_s=10.0, clock=clock)
    assert agent.report_turn()["status"] == T_MAX_WALL


def test_a_model_that_raises_ends_the_turn_typed():
    class Boom:
        def complete(self, messages, tools):
            raise ConnectionError("provider down")
    _, agent, action = _turn(Boom())
    t = agent.report_turn()
    assert action == [] and t["status"] == T_MODEL_ERROR and "provider down" in t["detail"]
    assert agent.report_usage() is None


def test_malformed_arguments_and_refused_tools_come_back_as_error_results():
    m = ScriptedModel([ModelReply(tool_calls=[ModelToolCall("a", "do", "raise_taxes"),
                                              ModelToolCall("b", "declare_war", {"target_id": 1}),
                                              ModelToolCall("c", END_TURN, {}),
                                              ModelToolCall("d", "observe", {})])])
    env, agent, _ = _turn(m)
    tool = {x["tool_call_id"]: x for x in agent.transcripts[-1] if x["role"] == "tool"}
    assert tool["a"]["is_error"] and "JSON object" in tool["a"]["content"]
    assert tool["b"]["is_error"] and "tool_not_allowed" in tool["b"]["content"]
    assert not tool["c"]["is_error"]
    assert tool["d"]["is_error"] and "already ended" in tool["d"]["content"]
    assert env.backend.calls == [], "neither the malformed nor the disallowed call reached the backend"


def test_a_budget_is_required():
    with pytest.raises(ValueError):
        ToolCallingAgent(ScriptedModel(), max_calls=0, max_wall_s=1)


def test_run_episode_records_the_agents_usage_and_turn_ends_in_the_trajectory():
    buf = io.StringIO()
    env = ParallelEnv("fake-council", trajectory=TrajectoryWriter(stream=buf))
    replies = []
    for _ in range(8):
        replies += [calls(("msg_send", {"to": "duke1", "text": "report"}), usage={"input_tokens": 9}),
                    calls((END_TURN, {}), usage={"input_tokens": 1})]
    king = ToolCallingAgent(ScriptedModel(replies), max_calls=5, max_wall_s=60)
    res = run_episode(env, {"king": king, "duke1": NoOpPolicy(), "duke2": NoOpPolicy()}, seed=2)
    env.close()
    recs = [json.loads(x) for x in buf.getvalue().splitlines()]
    assert check_trajectory(recs) == []
    steps = [r for r in recs if r["kind"] == "step"]
    assert len(steps) == res["steps"]
    assert steps[0]["usage"]["king"] == {"input_tokens": 10, "model_calls": 2, "model_calls_unreported": 0}
    assert steps[0]["turns"]["king"]["status"] == T_END_TURN
    assert "duke1" not in steps[0]["usage"], "a policy that reports nothing has no usage"
    assert steps[0]["comms"]["delivered"][0]["to"] == "duke1"


# ── the Anthropic adapter (stub client; the live API is never called) ─────

class _Obj:
    def __init__(self, **kw):
        self.__dict__.update(kw)

    def model_dump(self, exclude_none=False):
        return {k: v for k, v in self.__dict__.items() if not (exclude_none and v is None)}


class StubClient:
    def __init__(self, responses):
        self.responses, self.bodies = list(responses), []
        self.messages = self

    def create(self, **body):
        self.bodies.append(body)
        return self.responses.pop(0)


def _resp(blocks, stop, usage):
    return _Obj(content=blocks, stop_reason=stop, usage=_Obj(**usage))


def test_the_anthropic_adapter_translates_both_ways():
    r1 = _resp([_Obj(type="thinking", thinking="", signature="sig"), _Obj(type="text", text="Looking."),
                _Obj(type="tool_use", id="tu1", name="observe", input={}),
                _Obj(type="tool_use", id="tu2", name="do", input={"intent": "raise_taxes"})],
               "tool_use", {"input_tokens": 120, "output_tokens": 30, "cache_read_input_tokens": 0,
                            "cache_creation_input_tokens": None})
    r2 = _resp([_Obj(type="tool_use", id="tu3", name=END_TURN, input={"summary": "done"})], "tool_use",
               {"input_tokens": 200, "output_tokens": 5})
    client = StubClient([r1, r2])
    model = AnthropicModel("a-model-id", max_tokens=1000, client=client, request={"output_config": {"effort": "low"}})
    _, agent, _ = _turn(model)
    assert agent.report_turn()["status"] == T_END_TURN
    assert agent.report_usage() == {"input_tokens": 320, "output_tokens": 35, "cache_read_input_tokens": 0,
                                    "model_calls": 2, "model_calls_unreported": 0}
    b1, b2 = client.bodies
    assert b1["model"] == "a-model-id" and b1["max_tokens"] == 1000 and b1["output_config"] == {"effort": "low"}
    assert "system" in b1 and all(m["role"] != "system" for m in b1["messages"])
    assert {t["name"] for t in b1["tools"]} >= {"observe", "msg_send", END_TURN}
    assert all("input_schema" in t for t in b1["tools"])
    asst, results = b2["messages"][1], b2["messages"][2]
    assert asst["role"] == "assistant" and asst["content"][0] == {"type": "thinking", "thinking": "",
                                                                  "signature": "sig"}, "replayed unchanged"
    assert results["role"] == "user" and [b["tool_use_id"] for b in results["content"]] == ["tu1", "tu2"], \
        "parallel results go back in ONE user message"


def test_the_adapter_refuses_without_a_model_id_and_translates_errors_and_text_only_history():
    with pytest.raises(EnvRefusal):
        AnthropicModel("", max_tokens=10, client=StubClient([]))
    req = to_request([{"role": "system", "content": "S"}, {"role": "user", "content": "U"},
                      {"role": "assistant", "content": "A", "tool_calls": [{"id": "x", "name": "n",
                                                                            "arguments": {}}]},
                      {"role": "tool", "tool_call_id": "x", "name": "n", "content": "bad", "is_error": True}],
                     [{"name": "n", "inputSchema": {"type": "object"}}])
    assert req["system"] == "S"
    assert req["messages"][1]["content"] == [{"type": "text", "text": "A"},
                                             {"type": "tool_use", "id": "x", "name": "n", "input": {}}]
    assert req["messages"][2]["content"][0]["is_error"] is True
    r = from_response(_resp([_Obj(type="text", text="no")], "refusal", {}))
    assert r.tool_calls == [] and r.stop == "refusal" and r.usage is None
