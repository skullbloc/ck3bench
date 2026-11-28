"""A standing order's refused fire names its reason code and gate in its own record (not only `error: "not ok"`,
with the gate and the reason left in `last_results`); an order cannot spend into debt unconfirmed (the tool's
confirm_debt, checked at fire).

The reply used below is a recorded one, cut to its envelope: a refused claim declaration on the Sweden 867 start
(gate armies_raised), as ck3.py's backend wraps it
(ToolResult(tool, False, reply, "not ok")). Tested on stand-in backends only.
"""
from __future__ import annotations

import json

from ck3bench.env import orders as O
from ck3bench.env.types import ToolResult

#: the recorded refusal (0868.08.11, tick 587), its envelope without the script and the purse reads
REFUSED = json.loads('{"ok": false, "tool": "declare_war", "verdict": "rejected", "applied": false, "blocked": {"gate": "armies_raised", "class": "timing", "severity": "blocked", "summary": "the game refused the declaration: your armies are raised (has_raised_armies)", "raw_reason": "can_declare_war_false"}, "game": {"tick": 587, "date": "0868.08.11", "paused": true, "speed": 3}}')
DECLARE_ARGS = {"target_id": 14814, "cb": "claim_cb", "cb_title_id": 2526, "claimant_id": 12330}
ME = {"character_id": 12330, "metrics": {"gold": 50.0}}
ORDER = {"id": "press", "when": [{"metric": "gold", "op": ">=", "value": 0}], "unless": [], "on_refusal": "wake",
         "do": {"tool": "declare_war", "args": {"declare_args": DECLARE_ARGS}}}


def _fired(res):
    book = O.Book({"bjorn": [dict(ORDER)]}, "0868.08.11")
    book.check("bjorn", ME, ME, "0868.08.11", lambda seat, o, date: res)
    return book.records["bjorn"][-1], book.wakes


def test_a_refused_fire_names_its_gate_and_reason_code():
    """Without the rule the record carries error "not ok" and nothing else of the refusal."""
    env = REFUSED
    rec, wakes = _fired(ToolResult("declare_war", False, env, "not ok"))
    assert rec["outcome"] == "fired" and rec["verdict"] == "refused" and rec["error"] == "not ok"
    assert rec["gate"] == "armies_raised" and rec["reason_code"] == env["blocked"]["raw_reason"]
    assert rec["why"].startswith("the game refused the declaration: your armies are raised")
    assert wakes and wakes[0]["read"] == "not ok"               # the wake row is unchanged


def test_an_envelope_under_result_is_read_too():
    rec, _ = _fired(ToolResult("declare_war", False, {"tool_name": "declare_war", "result": REFUSED},
                               "not ok"))
    assert rec["gate"] == "armies_raised"


def test_an_env_refusal_gives_its_code():
    rec, _ = _fired(ToolResult("declare_war", False, None, "clock_running: the clock refuses writes now"))
    assert rec["reason_code"] == "clock_running" and rec["gate"] is None


def test_CONTROL_nothing_readable_is_said_unread():
    rec, _ = _fired(ToolResult("declare_war", False, None, None))
    assert rec["reason_code"] is None and rec["reason"].startswith("unread")


def test_CONTROL_an_applied_fire_carries_no_reason():
    rec, _ = _fired(ToolResult("declare_war", True, {"verdict": "applied"}, None))
    assert rec["verdict"] == "applied" and "gate" not in rec and "reason_code" not in rec


def test_the_order_rule_is_the_tool_s():
    """A standing order's hire carries confirm_debt in its args verbatim; the tool checks it at fire, as it checks
    confirm_straddle (no set-time word is asked)."""
    assert "confirm_debt" in O.__doc__ and "SPEND INTO DEBT" in O.__doc__
    assert O.confirm_needed("do", {"intent": "hire_merc", "args": {"company_id": 33}}) is None
