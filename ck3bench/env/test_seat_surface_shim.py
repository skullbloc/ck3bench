"""The seat shim's surface switches: the lean reply envelope, and the pad patch at end_turn (`notes_edit`).

The lean envelope leaves anything that is not a wrapped tool reply alone, and is sent only when asked. The pad patch,
on the fake env with a carried pad: a patch gives the pad a whole rewrite would, a bad patch is refused with the
turn still open or ends the turn with the pad unchanged, the listing names notes_edit only with --notes-edit.
No game, no model."""
from __future__ import annotations

import json

import pytest

from ck3bench.env import ParallelEnv
from ck3bench.env import mcp_shim as S
from ck3bench.env.server import EnvClient, EnvServer

#: one toolkit reply as the env hands it to the shim ({tool_name, ok, result, error}), with the echoes a lean
#: envelope drops (the toolkit's tool, actor and game fields)
WRAPPED = {"tool_name": "observe", "ok": True, "error": None,
           "result": {"ok": True, "tool": "observe", "actor": 31506, "game": {"date": "1066.09.15", "paused": True},
                      "warnings": [], "result": {"gold": 54.2, "realm": {"counties": 1, "title_key": "c_dublin"}}}}


def wrapped():
    yield json.loads(json.dumps(WRAPPED))


def test_lean_leaves_anything_else_alone():
    for x in (None, [1], "s", {"ok": False, "code": "turn_ended", "detail": "d", "leg": "shim"}):
        assert S.lean_reply(x) == x


class _Client:
    """A stand-in env client: one scripted reply for every call, and a pad."""

    def __init__(self, reply, pad=""):
        self.reply, self.pad, self.submits = reply, pad, []

    def call(self, seat, name, args):
        return json.loads(json.dumps(self.reply))

    def tools(self, seat):
        return [{"name": "observe", "description": "d", "inputSchema": {"type": "object"}}]

    def spec(self):
        return {"scenario": {"wake": None}}

    def observation(self, seat):
        return {"observation": {"notes_pad": {"text": self.pad, "version": 1}}}

    def submit(self, seat, action, **kw):
        self.submits.append(kw)
        return {"step": 3, "stepped": False, "waiting_for": ["other"]}


def test_the_shim_sends_lean_only_when_asked():
    w = next(wrapped())
    full, _ = S.SeatShim(_Client(w), "s", 1).call("observe", {})
    lean, _ = S.SeatShim(_Client(w), "s", 1, lean=True).call("observe", {})
    assert full == w and lean == S.lean_reply(w)
    assert lean != full and lean["result"]["result"] == w["result"]["result"]     # CONTROL: lean drops something


# ── the pad patch ──────────────────────────────────────────────────────────────────────────────────────────────────────────
PAD = "goal: k_ireland\nwar: none\nheir: Murchad (31506)\nnext: fabricate on c_meath"


@pytest.mark.parametrize("edits,want", [
    ([{"op": "replace", "line": 2, "text": "war: vs Leinster"}],
     "goal: k_ireland\nwar: vs Leinster\nheir: Murchad (31506)\nnext: fabricate on c_meath"),
    ([{"op": "delete", "line": 4}, {"op": "append", "text": "next: siege\nwatch: gold"}],
     "goal: k_ireland\nwar: none\nheir: Murchad (31506)\nnext: siege\nwatch: gold"),
    ([{"op": "replace", "line": 1, "text": "goal: k_ireland\nwhy: the task"}, {"op": "delete", "line": 3}],
     "goal: k_ireland\nwhy: the task\nwar: none\nnext: fabricate on c_meath"),
])
def test_a_patch_gives_the_pad_a_rewrite_would(edits, want):
    assert S.apply_notes_edit(PAD, edits) == want


@pytest.mark.parametrize("edits", [[], None, [{"op": "replace", "line": 9, "text": "x"}],
                                   [{"op": "delete", "line": 0}], [{"op": "insert", "line": 1, "text": "x"}],
                                   [{"op": "replace", "line": 1}], [{"op": "append"}],
                                   [{"op": "delete", "line": 2}, {"op": "replace", "line": 2, "text": "y"}],
                                   [{"op": "delete", "line": True}], ["x"]])
def test_a_bad_patch_is_refused_whole(edits):
    with pytest.raises(ValueError):
        S.apply_notes_edit(PAD, edits)


def test_end_turn_takes_a_patch_and_submits_the_patched_pad():
    c = _Client({}, pad=PAD)
    shim = S.SeatShim(c, "s", 1, ack=True)
    out, err = shim.call("end_turn", {"summary": "x", "notes_edit": [{"op": "replace", "line": 2, "text": "war: on"}]})
    assert not err and out["turn_ended"] and c.submits[0]["notes"] == PAD.replace("war: none", "war: on")
    assert out["notes_bytes"] == len(c.submits[0]["notes"].encode())


def test_end_turn_with_a_bad_patch_ends_the_turn_with_the_pad_unchanged():
    """A patch naming a line the pad does not have is not applied, the turn ends (a second end_turn would cost the
    seat a call), and the env is told (notes_unwritten) so the next turn says so; notes AND notes_edit is still
    refused."""
    c = _Client({}, pad=PAD)
    shim = S.SeatShim(c, "s", 1, ack=True)
    out, err = shim.call("end_turn", {"notes": "a", "notes_edit": [{"op": "append", "text": "b"}]})
    assert err and out["code"] == S.R_NOTES_EDIT and not c.submits and shim.ended is None
    out, err = shim.call("end_turn", {"notes_edit": [{"op": "delete", "line": 99}]})
    assert not err and out["turn_ended"] and shim.ended is not None
    assert "notes" not in c.submits[0] and c.submits[0]["notes_unwritten"]["code"] == S.R_NOTES_EDIT
    assert "line 99 is not a line of the pad (1..4)" in c.submits[0]["notes_unwritten"]["why"]


def test_a_good_patch_is_sent_whole_with_the_end_turn_rule():
    c = _Client({}, pad=PAD)
    out, err = S.SeatShim(c, "s", 1, ack=True).call("end_turn", {"notes_edit": [{"op": "append", "text": "b"}]})
    assert not err and c.submits[0]["notes"] == PAD + "\nb" and c.submits[0]["notes_if_refused"] == "end_turn"


def test_the_listing_names_notes_edit_only_when_switched_on():
    c = _Client({})
    off = S.SeatShim(c, "s", 1, ack=True).list_tools()
    on = S.SeatShim(c, "s", 1, ack=True, notes_edit=True).list_tools()
    et_off = next(t for t in off if t["name"] == "end_turn")
    et_on = next(t for t in on if t["name"] == "end_turn")
    assert "notes_edit" not in et_off["inputSchema"]["properties"]
    assert et_on["inputSchema"]["properties"]["notes_edit"] == S.NOTES_EDIT_PROPERTY
    assert et_on["description"] == S.ACK_DESCRIPTION + S.NOTES_EDIT_NOTE
    assert S.SeatShim(c, "s", 1, notes_edit=True).list_tools() == S.SeatShim(c, "s", 1).list_tools()  # ack only


def test_a_patch_reaches_the_env_pad_on_the_fake_backend(tmp_path):
    env = ParallelEnv("fake-solo", notes_pad_bytes=4096)
    srv = EnvServer(env, turn_timeout_s=30).start()
    try:
        cl = EnvClient(srv.url, timeout=30)
        cl.reset(seed=1)
        shim = S.SeatShim(cl, "king", 30, ack=True)
        out, err = shim.call("end_turn", {"notes": PAD})
        assert not err
        shim = S.SeatShim(cl, "king", 30, ack=True)                # the next turn's session
        out, err = shim.call("end_turn", {"notes_edit": [{"op": "replace", "line": 4, "text": "next: siege"}]})
        assert not err, out
        pad = cl.observation("king")["observation"]["notes_pad"]
        assert pad["text"] == PAD.replace("next: fabricate on c_meath", "next: siege") and pad["version"] == 2
    finally:
        srv.stop()
        env.close()
