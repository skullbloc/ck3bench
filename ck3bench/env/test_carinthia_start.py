"""carinthia1066-line-25y-wake: the Lorraine line task's goal and terms from another seat on the same 1066 save.

The Lorraine start was judged unwinnable (its heir was matrilineally married in the save). This start's seat is the
holder of d_carinthia, found on the save at open and confirmed by his name (Berthold). The task keeps
`line_kept_peacefully` and every term, tool, cadence and clock of the Lorraine task; only the seat, the description
and the notes set differ. The real backend runs here against the fake host and bridge of test_holder_starts: these
prove the route as written, never that the game answers as the fakes do.
"""
from __future__ import annotations

import io
import json
import re

import pytest

from ck3bench.env.ck3 import NOTES_SET_ENV
from ck3bench.env.errors import EnvRefusal
from ck3bench.env.rewards import GOAL_METRICS, HORIZON_GOALS
from ck3bench.env.scenario import BUILTIN_DICTS, builtin
from ck3bench.env.test_holder_starts import LOR, TASK_TEXT_LABEL, NorseBridge, _env
from ck3bench.env.test_task_notes_sets import _open

CAR = "carinthia1066-line-25y-wake"
#: a runtime id as a save might read it (the 1066 save reads 30331, history 1610: neither is written in the task)
BERTHOLD = 7101


def test_carinthia_is_the_lorraine_task_from_the_holder_of_d_carinthia():
    c, lor = builtin(CAR), builtin(LOR)
    assert c.start_save == "_1066_09_15.ck3" and c.start_date == "1066.09.15" and c.end_date == "1091.09.15"
    assert c.start_save_sha256 == "0c94b59880e0549a5a0c8fbe4dc675a3de171f28d79f7628ad765116de782d5d"
    assert (c.horizon_days, c.n_steps, c.goal, c.target_title) == (25 * 365, 125, "line_kept_peacefully", None)
    assert c.goal in HORIZON_GOALS
    assert set(GOAL_METRICS[c.goal]) | {"heir_present"} <= set(c.metric_names())
    for f in ("reward", "tools", "game_speed", "wake", "cycle_days", "record_metrics", "start_save_sha256"):
        assert getattr(c, f) == getattr(lor, f), f
    seat = c.seats[0]
    assert (seat.name, seat.selector, seat.holder_of, seat.holder_name, seat.holder_name_key) == \
        ("carinthia", "holder_of", "d_carinthia", "Berthold", "Berthold")
    assert c.notes_set == "carinthia1066"


def test_the_description_states_the_four_legs_as_lorraines_does():
    d, lor = BUILTIN_DICTS[CAR]["description"], BUILTIN_DICTS[LOR]["description"]
    for words in ("at least once", "a faction he leads", "joining an ally's war does not", "does not end the episode",
                  "1091.09.15", "holder of d_carinthia", "at least as many counties as at the start"):
        assert words in d and (words in lor or "carinthia" in words), words
    assert "twice" not in d and "Lorraine" not in d and "Gottfried" not in d


def test_no_runtime_id_is_written_into_the_task():
    d = BUILTIN_DICTS[CAR]
    assert all("character_id" not in seat for seat in d["seats"])
    assert "30331" not in json.dumps(d) and "1610" not in json.dumps(d)


def test_the_task_text_is_public_text():
    d = BUILTIN_DICTS[CAR]
    for text in (d["description"], d["status_reason"]):
        assert TASK_TEXT_LABEL.search(text) is None, TASK_TEXT_LABEL.search(text)
        assert not re.search(r"/home/|/mnt/|\bnode\b|\bws\d|\benv[a-z]+|\bowner\b|\bTK\d", text), text


def test_the_seat_is_bound_to_the_holder_the_save_reads(tmp_path):
    br, buf = NorseBridge(holder=BERTHOLD, title="d_carinthia", name="Berthold"), io.StringIO()
    env, bound = _env(tmp_path, br, task=CAR, buf=buf)
    assert env.backend.seats == {"carinthia": BERTHOLD} and bound == {"carinthia": BERTHOLD}
    head = json.loads(buf.getvalue().splitlines()[0])
    assert head["provenance"]["seat_census"]["carinthia"]["name_read"] == "Berthold"
    assert ("query_de_jure", {"title_key": "d_carinthia"}) in br.calls
    assert head["start_metrics"]["carinthia"]["values"]["line_alive"] is True


def test_CONTROL_another_holder_of_d_carinthia_is_refused(tmp_path):
    """The census confirms the name: a holder named otherwise (Markward, the claimant vassal) is no seat."""
    br = NorseBridge(holder=BERTHOLD, title="d_carinthia", name="Markward")
    with pytest.raises(EnvRefusal) as e:
        _env(tmp_path, br, task=CAR)
    assert e.value.code == "census_seat_unconfirmed" and "Berthold" in e.value.detail


def test_the_seat_server_is_given_the_carinthia_set(tmp_path, monkeypatch):
    monkeypatch.delenv(NOTES_SET_ENV, raising=False)
    import ck3bench.env.test_task_notes_sets as SA
    monkeypatch.setattr(SA, "NorseBridge", lambda: NorseBridge(holder=BERTHOLD, title="d_carinthia", name="Berthold"))
    seen, head = _open(tmp_path, BUILTIN_DICTS[CAR])
    assert seen["carinthia"][NOTES_SET_ENV] == "carinthia1066"
    assert head["provenance"]["notes_set_declared"] == "carinthia1066"
