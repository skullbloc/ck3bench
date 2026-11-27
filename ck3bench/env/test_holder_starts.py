"""Two starts whose seat is found on the save at open, and the metrics and goal they read.

* sweden867-kingdom-20y-wake: the holder of d_svealand on the 867 save, goal kingdom_held (k_sweden); it records
  offensive_wars_declared, ruler_changes and heir_present at every step without scoring them.
* lorraine1066-line-25y-wake: the holder of d_lower_lorraine on the 1066 save, goal line_kept_peacefully, judged at
  the end date only.

The seat is the title's holder as the save reads him (census.py select_by_holder), confirmed by his name; no
runtime character id is written into either task. The real backend runs here against a fake host and a fake
bridge (test_wake_ck3.WakeBridge plus the reads these metrics make): these prove the route as written, never that
the game answers as the fakes do.
"""
from __future__ import annotations

import copy
import hashlib
import io
import json
import re

import pytest

from ck3bench.env import ParallelEnv, calendar
from ck3bench.env import census as C
from ck3bench.env.ck3 import WAR_RING, Ck3Backend
from ck3bench.env.errors import TERM_GOAL, TRUNC_HORIZON, EnvRefusal
from ck3bench.env.rewards import (GOAL_METRICS, HORIZON_GOALS, LINE_KEPT_RULER_CHANGES, RewardUnavailable,
                                  goal_met, line_kept_legs)
from ck3bench.env.scenario import BUILTIN_DICTS, builtin, scenario_from_dict
from ck3bench.env.test_ck3_backend import FakeHost
from ck3bench.env.test_wake_ck3 import NEVER, Seat, WakeBridge
from ck3bench.env.trajectory import TrajectoryWriter
from ck3bench.env.types import METRIC_DEFINITIONS, METRIC_OK, METRIC_UNREADABLE, Metrics

SWE, LOR = "sweden867-kingdom-20y-wake", "lorraine1066-line-25y-wake"
#: runtime ids as a save might read them: not the history ids (163108, 11030), and never written in a task
BJORN, GOTT, HEIR, OTHER = 7001, 7002, 7003, 9999


# ── the declarations ────────────────────────────────────────────────────────

def test_sweden_is_the_munster_task_shape_on_the_867_save_with_a_census_seat():
    s, m = builtin(SWE), builtin("munster1066-kingdom-30y-wake")
    assert s.backend == "ck3" and s.start_save == "_867_01_01.ck3" and s.start_date == "0867.01.01"
    assert s.start_save_sha256 == "1a08927d4515ebaeb20f528f56430084348556359606ac54d2ba4b47a82bc5be"
    assert (s.horizon_days, s.cycle_days, s.n_steps, s.end_date) == (20 * 365, 73, 100, "0887.01.01")
    assert (s.goal, s.target_title) == ("kingdom_held", "k_sweden")
    for f in ("reward", "tools", "game_speed", "wake"):
        assert getattr(s, f) == getattr(m, f), f
    seat = s.seats[0]
    assert (seat.selector, seat.holder_of, seat.holder_name, seat.holder_name_key) == \
        ("holder_of", "d_svealand", "Björn", "BjO_rn")
    assert s.record_metrics == ("offensive_wars_declared", "ruler_changes", "heir_present")
    assert set(s.record_metrics) <= set(s.metric_names())
    assert "however it comes to him" in s.description and "run live with a model" in s.status_reason


def test_lorraine_is_judged_at_its_end_date_on_four_legs():
    s = builtin(LOR)
    assert s.start_save == "_1066_09_15.ck3" and s.start_date == "1066.09.15" and s.end_date == "1091.09.15"
    assert s.start_save_sha256 == "0c94b59880e0549a5a0c8fbe4dc675a3de171f28d79f7628ad765116de782d5d"
    assert (s.horizon_days, s.n_steps, s.goal, s.target_title) == (25 * 365, 125, "line_kept_peacefully", None)
    assert s.goal in HORIZON_GOALS and LINE_KEPT_RULER_CHANGES == 1      # one change of ruler, not two
    assert GOAL_METRICS[s.goal] == ("line_alive", "ruler_changes", "offensive_wars_declared", "realm_counties")
    assert set(GOAL_METRICS[s.goal]) | {"heir_present"} <= set(s.metric_names())
    assert s.seats[0].holder_of == "d_lower_lorraine" and s.seats[0].holder_name == "Gottfried"
    assert "at least once" in s.description and "twice" not in s.description and "does not end the episode" in s.description


@pytest.mark.parametrize("task", [SWE, LOR])
def test_no_runtime_id_is_written_into_a_census_task(task):
    d = BUILTIN_DICTS[task]
    assert all("character_id" not in seat for seat in d["seats"])
    assert "163108" not in json.dumps(d) and "11030" not in json.dumps(d)


def test_every_new_metric_is_defined_in_one_sentence():
    for n in ("ruler_changes", "offensive_wars_declared", "heir_present"):
        assert METRIC_DEFINITIONS[n].count(". ") == 0 and len(METRIC_DEFINITIONS[n]) > 40


#: Work and run labels, people named by role, our machines and a private run's file names: none belongs in a task's
#: text (spelled in pieces, so this file carries none of them).
TASK_TEXT_LABEL = re.compile(r"(?<![A-Za-z0-9])(?:TK|tk)[0-9]{1,3}(?![0-9])|\b[Ll]a" r"nes?\b|\benv[a-z]+[0-9]|"
                             r"\b(?:ws|no" r"de)[0-9]{1,2}\b|\bck-[a-z0-9]{4,}\b|\b[Oo]wn" r"er\b|_auto" r"save\.ck3|"
                             r"/home/|/mnt/|\bno" r"de\b|\bwork" r"station\b|\bru" r"n (?:one|two|three|nine)\b")


@pytest.mark.parametrize("task", sorted(BUILTIN_DICTS))
def test_the_task_text_is_public_text(task):
    d = BUILTIN_DICTS[task]
    for text in (d["description"], d["status_reason"]):
        assert TASK_TEXT_LABEL.search(text) is None, TASK_TEXT_LABEL.search(text)


def test_the_public_text_control_runs():
    """CONTROL: the pattern the task text is checked with finds each kind of label when there is one."""
    for planted in ("added in " + "la" + "ne ZZ", "T" + "K99 item 9", "env" + "w" + "s99", "on " + "w" + "s" + "99",
                    "an " + "own" + "er", "w" + "s99_9999_auto" + "save.ck3", "a " + "no" + "de"):
        assert TASK_TEXT_LABEL.search(planted) is not None, planted
    for clean in ("added in this release", "a plane", "Ireland", "the 1066 save", "tkinter", "ownership"):
        assert TASK_TEXT_LABEL.search(clean) is None, clean


def _ck3_dict(**seat):
    d = copy.deepcopy(BUILTIN_DICTS[LOR])
    d["seats"] = [{"name": "lorraine", **seat}]
    return d


@pytest.mark.parametrize("seat,code", [
    ({"holder_of": "d_lower_lorraine"}, "scenario_missing_key"),
    ({"holder_of": "Lower Lorraine", "holder_name": "Gottfried"}, "scenario_bad_value"),
    ({"character_id": 5, "holder_name": "Gottfried"}, "scenario_bad_value"),
    ({"character_id": 5, "holder_of": "d_lower_lorraine", "holder_name": "Gottfried"}, "scenario_bad_value"),
])
def test_a_bad_census_seat_is_refused(seat, code):
    with pytest.raises(EnvRefusal) as e:
        scenario_from_dict(_ck3_dict(**seat))
    assert e.value.code == code


def test_a_census_seat_on_the_fake_backend_is_refused_never_given_a_made_up_id():
    d = copy.deepcopy(BUILTIN_DICTS["fake-solo"])
    d["seats"] = [{"name": "king", "holder_of": "k_fake", "holder_name": "A"}]
    with pytest.raises(EnvRefusal) as e:
        scenario_from_dict(d)
    assert e.value.code == "scenario_bad_value" and "no game" in e.value.detail


@pytest.mark.parametrize("rec", [["gold", "gold"], ["wealth"], [3]])
def test_record_metrics_must_be_distinct_known_names(rec):
    d = copy.deepcopy(BUILTIN_DICTS[LOR])
    d["record_metrics"] = rec
    with pytest.raises(EnvRefusal) as e:
        scenario_from_dict(d)
    assert e.value.code == "scenario_bad_value"


def test_a_recorded_target_metric_needs_the_target_title():
    d = copy.deepcopy(BUILTIN_DICTS[LOR])
    d["record_metrics"] = ["dejure_counties"]
    with pytest.raises(EnvRefusal) as e:
        scenario_from_dict(d)
    assert e.value.code == "scenario_missing_key"


# ── the holder census ───────────────────────────────────────────────────────

def _reader(title=None, prof=None):
    title = {"title_id": 4200, "key": "d_svealand", "holder_id": BJORN} if title is None else title
    prof = {"id": BJORN, "name_key": "BjO_rn", "name_pending": True, "house_name": "Munsö"} if prof is None else prof
    calls = []

    def read(action, params):
        calls.append((action, dict(params)))
        if action == "query_de_jure":
            return title if "error" in title else {"title": title, "de_jure_parents": []}
        if action == "query_character":
            return prof
        return {"error": f"unknown {action}"}
    read.calls = calls
    return read


def test_the_census_finds_the_holder_by_his_name_key_on_a_save_whose_names_are_pending():
    read = _reader()
    cid, rec = C.select_by_holder(read, "bjorn", "d_svealand", "Björn", "BjO_rn")
    assert cid == BJORN and rec["name_field"] == "name_key" and rec["name_read"] == "BjO_rn"
    assert rec["holder_id"] == BJORN and rec["title_id"] == 4200 and rec["house_name"] == "Munsö"
    assert read.calls == [("query_de_jure", {"title_key": "d_svealand"}), ("query_character", {"character_id": BJORN})]


def test_the_census_finds_the_holder_by_his_localised_name():
    read = _reader(prof={"id": BJORN, "name": "Björn"})
    assert C.select_by_holder(read, "bjorn", "d_svealand", "Björn", None)[0] == BJORN


@pytest.mark.parametrize("title,prof,key,code,words", [
    ({"title_id": 1, "key": "d_svealand"}, None, "BjO_rn", "census_seat_unconfirmed", "has no holder"),
    (None, {"name": "Sigurdr"}, "BjO_rn", "census_seat_unconfirmed", "not the declared 'Björn'"),
    (None, {"name_key": "Sigurdr", "name_pending": True}, "BjO_rn", "census_seat_unconfirmed", "not the declared"),
    (None, None, None, "census_seat_unconfirmed", "declares no holder_name_key"),
    (None, {"id": BJORN}, "BjO_rn", "census_seat_unconfirmed", "has no name"),
    (None, {"error": "character_not_found"}, "BjO_rn", "census_read_unavailable", "character_not_found"),
    ({"title_id": 1, "key": "d_uppland", "holder_id": BJORN}, None, "BjO_rn", "census_read_unavailable", "names"),
    ({"error": "title_key_not_found"}, None, "BjO_rn", "census_read_unavailable", "title_key_not_found"),
])
def test_the_census_refuses_rather_than_guesses(title, prof, key, code, words):
    with pytest.raises(EnvRefusal) as e:
        C.select_by_holder(_reader(title, prof), "bjorn", "d_svealand", "Björn", key)
    assert e.value.code == code and words in e.value.detail


# ── the real backend: a fake bridge with the census, the ring and the heir reads ──

class NorseBridge(WakeBridge):
    """WakeBridge plus query_de_jure (a title's holder), the holder's name, the war-declaration ring (the
    bridge's newest WAR_RING rows; `tick` is the date's ordinal), query_succession and query_title_succession."""

    def __init__(self, holder=GOTT, title="d_lower_lorraine", name="Gottfried", **kw):
        super().__init__(**kw)
        self.titles = {title: {"title_id": 300, "key": title, "holder_id": holder}}
        self.names = {holder: {"name": name}}
        self.watch = {"status": "following", "line_character_id": holder, "start_character_id": holder,
                      "previous_character_id": 0, "successions": 0}
        self.realm = {holder: 5}
        self.ring: list[dict] = []
        self.monitor = True
        self.ring_refused = False
        self.heirs = {300: 1}
        self.primary = {holder: 300}
        self.war_reads = 0
        self._wid = 0

    def declare(self, attacker, defender=OTHER):
        self._wid += 1
        self.ring.append({"tick": calendar.ordinal(self.date), "war_id": self._wid, "attacker_id": attacker,
                          "defender_id": defender, "cb_type": "x"})
        del self.ring[:-WAR_RING]

    def call(self, action, params=None, timeout=8.0):
        params = params or {}
        if action == "query_de_jure":
            self.calls.append((action, params))
            t = self.titles.get(params.get("title_key"))
            return {"title": dict(t), "de_jure_parents": []} if t else {"error": "title_key_not_found"}
        if action == "query_character" and params.get("played_line") != 1 and params.get("character_id") in self.names:
            self.calls.append((action, params))
            cid = params["character_id"]
            return {**self.profile, "id": cid, "top_liege_id": self.top.get(cid, cid), **self.names[cid]}
        if action == "query_war_declarations":
            self.calls.append((action, params))
            self.war_reads += 1
            if self.ring_refused:
                return {"error": "not served"}
            rows = [dict(r) for r in reversed(self.ring) if r["tick"] >= params.get("since_tick", 0)]
            return {"success": True, "current_tick": calendar.ordinal(self.date), "monitor_active": self.monitor,
                    "total_recorded": len(self.ring), "declarations": rows[:params.get("limit", 64)]}
        if action == "query_succession":
            self.calls.append((action, params))
            p = self.primary.get(params["character_id"])
            return {"character_id": params["character_id"], **({"primary_title_id": p} if p else {})}
        if action == "query_title_succession":
            self.calls.append((action, params))
            tid = params["title_id"]
            return {"title_id": tid, "current_holder_id": self.watch["line_character_id"],
                    "heir_count": self.heirs.get(tid, 0)}
        return super().call(action, params, timeout)

    def succeed(self, heir):
        self.watch.update(previous_character_id=self.watch["line_character_id"], line_character_id=heir,
                          successions=self.watch["successions"] + 1)
        self.realm[heir] = self.realm[self.watch["previous_character_id"]]
        self.primary[heir] = 300


def _save(tmp_path, name, data=b"SAVE"):
    d = tmp_path / "saves"
    d.mkdir(exist_ok=True)
    (d / name).write_bytes(data)
    return d


def _env(tmp_path, br, task=LOR, buf=None):
    if isinstance(task, str):                # the declared save's sha256, made the stand-in file's
        d = copy.deepcopy(BUILTIN_DICTS[task])
        d["start"]["save_sha256"] = hashlib.sha256(b"SAVE").hexdigest()
        task = scenario_from_dict(d)
    sc = task
    bound = {}

    def tf(seat, cid, env):
        bound[seat] = cid
        return Seat()
    b = Ck3Backend(_save(tmp_path, sc.start_save), host=FakeHost(), link_factory=lambda s, c, m: br,
                   socket_resolver=lambda i: "/run/fake.sock", transport_factory=tf, work_dir=tmp_path,
                   poll_s=1.0, retry_s=0.5, socket_probe=lambda s: False)
    env = ParallelEnv(sc, backend=b, trajectory=TrajectoryWriter(stream=buf) if buf is not None else None)
    env.reset(seed=1)
    return env, bound


def test_the_lorraine_seat_is_bound_to_the_holder_the_save_reads(tmp_path):
    br, buf = NorseBridge(), io.StringIO()
    env, bound = _env(tmp_path, br, buf=buf)
    assert env.backend.seats == {"lorraine": GOTT} and bound == {"lorraine": GOTT}
    head = json.loads(buf.getvalue().splitlines()[0])
    sc = head["provenance"]["seat_census"]["lorraine"]
    assert head["seats"] == {"lorraine": GOTT} and sc["holder_id"] == GOTT and sc["name_read"] == "Gottfried"
    assert ("query_de_jure", {"title_key": "d_lower_lorraine"}) in br.calls
    start = head["start_metrics"]["lorraine"]
    assert start["values"] == {"realm_counties": 5, "line_alive": True, "ruler_changes": 0,
                               "offensive_wars_declared": 0, "heir_present": True}, start


def test_the_sweden_seat_is_bound_by_its_name_key_on_the_867_save(tmp_path):
    br = NorseBridge(holder=BJORN, title="d_svealand", name=None)
    br.names = {BJORN: {"name_key": "BjO_rn", "name_pending": True}}
    br.date = "0867.01.01"
    d = copy.deepcopy(BUILTIN_DICTS[SWE])
    d["start"]["save_sha256"] = hashlib.sha256(b"SAVE").hexdigest()
    env, bound = _env(tmp_path, br, task=scenario_from_dict(d))
    assert bound == {"bjorn": BJORN}
    m = env._start["bjorn"]
    assert {n: m.status[n] for n in ("offensive_wars_declared", "ruler_changes", "heir_present")} == \
        {"offensive_wars_declared": METRIC_OK, "ruler_changes": METRIC_OK, "heir_present": METRIC_OK}


def test_a_census_seat_whose_name_does_not_match_refuses_the_reset(tmp_path):
    br = NorseBridge(name="Godfrey")
    with pytest.raises(EnvRefusal) as e:
        _env(tmp_path, br)
    assert e.value.code == "census_seat_unconfirmed" and "'Godfrey'" in e.value.detail


def _m(env, seat="lorraine"):
    return env.backend.metrics(seat, env.scenario.metric_names())


def test_ruler_changes_is_the_watchs_own_count(tmp_path):
    br = NorseBridge()
    env, _ = _env(tmp_path, br)
    br.succeed(HEIR)
    br.succeed(HEIR + 1)
    m = _m(env)
    assert m.get("ruler_changes") == (2, METRIC_OK) and "played_line.successions" in m.source["ruler_changes"]
    del br.watch["successions"]
    assert _m(env).get("ruler_changes") == (None, METRIC_UNREADABLE)


def test_offensive_wars_count_only_declarations_by_the_line_since_the_start(tmp_path):
    br = NorseBridge()
    br.declare(GOTT)                      # before the episode: in the ring at the baseline, not counted
    env, _ = _env(tmp_path, br)
    assert _m(env).get("offensive_wars_declared") == (0, METRIC_OK)
    br.date = "1066.10.01"
    br.declare(OTHER, defender=GOTT)      # declared on the line: not offensive
    br.declare(OTHER, defender=1234)      # somebody else's war
    assert _m(env).get("offensive_wars_declared") == (0, METRIC_OK)
    br.date = "1066.11.01"
    br.declare(GOTT)
    m = _m(env)
    assert m.get("offensive_wars_declared") == (1, METRIC_OK) and "war ids 4" in m.source["offensive_wars_declared"]
    assert _m(env).get("offensive_wars_declared") == (1, METRIC_OK)     # a row read twice is counted once


def test_a_war_the_previous_ruler_declared_before_he_died_counts(tmp_path):
    br = NorseBridge()
    env, _ = _env(tmp_path, br)
    br.date = "1067.01.01"
    br.declare(GOTT)
    br.succeed(HEIR)                      # in the same step: the watch names him as the previous ruler
    br.date = "1067.02.01"
    br.declare(HEIR)
    assert _m(env).get("offensive_wars_declared") == (2, METRIC_OK)


def test_a_full_ring_of_new_rows_makes_the_count_unreadable_for_good(tmp_path):
    br = NorseBridge()
    env, _ = _env(tmp_path, br)
    br.date = "1067.01.01"
    for _ in range(WAR_RING + 5):
        br.declare(OTHER)
    m = _m(env)
    assert m.get("offensive_wars_declared") == (None, METRIC_UNREADABLE)
    assert "may have been dropped" in m.source["offensive_wars_declared"]
    br.date = "1068.01.01"
    br.ring = []
    assert _m(env).get("offensive_wars_declared") == (None, METRIC_UNREADABLE)     # sticky


def test_a_full_ring_with_older_rows_still_in_it_lost_nothing(tmp_path):
    """The control for the overflow rule: the ring is full, but a row older than the last read is still in it,
    so nothing since that read was dropped."""
    br = NorseBridge()
    for _ in range(WAR_RING):
        br.declare(OTHER)
    env, _ = _env(tmp_path, br)
    br.date = "1067.01.01"
    for _ in range(10):
        br.declare(OTHER)
    br.declare(GOTT)
    assert _m(env).get("offensive_wars_declared") == (1, METRIC_OK)


def test_an_inactive_monitor_after_the_clock_ran_is_unreadable_and_a_refused_read_is_not_a_zero(tmp_path):
    br = NorseBridge()
    env, _ = _env(tmp_path, br)
    br.ring_refused = True
    m = _m(env)
    assert m.get("offensive_wars_declared") == (None, METRIC_UNREADABLE) and "not served" in \
        m.source["offensive_wars_declared"]
    br.ring_refused = False
    br.date = "1067.01.01"
    br.declare(GOTT)
    assert _m(env).get("offensive_wars_declared") == (1, METRIC_OK)       # a refused read lost nothing
    br.monitor = False
    assert _m(env).get("offensive_wars_declared") == (None, METRIC_UNREADABLE)


def test_the_ring_is_read_at_every_wake_check_so_a_busy_year_loses_nothing(tmp_path):
    """A declaration by the line, then 60 other wars over the next weeks: by the step's end the ring has dropped
    it, and only the checks made while the clock ran saw it."""
    br = NorseBridge()
    br.at["1066.10.01"] = lambda w: w.declare(GOTT)
    for i, day in enumerate(("1066.10.20", "1066.11.10", "1066.12.01")):
        br.at[day] = lambda w: [w.declare(OTHER) for _ in range(30)]
    env, _ = _env(tmp_path, br)
    env.step({"lorraine": []}, wake={"lorraine": {"max_days": 120, "when": [NEVER]}})
    m = env._prev["lorraine"]
    assert all(r["attacker_id"] != GOTT for r in br.ring)               # gone from the ring by the step's end
    assert m.get("offensive_wars_declared") == (1, METRIC_OK), m.source
    assert br.war_reads > 3


@pytest.mark.parametrize("heirs,primary,want", [(2, 300, (True, METRIC_OK)), (0, 300, (False, METRIC_OK)),
                                                (1, None, (None, METRIC_UNREADABLE))])
def test_heir_present_reads_the_primary_titles_heir_line(tmp_path, heirs, primary, want):
    br = NorseBridge()
    env, _ = _env(tmp_path, br)
    br.heirs[300] = heirs
    if primary is None:
        br.primary.pop(GOTT)
    assert _m(env).get("heir_present") == want


def test_heir_present_is_unreadable_when_the_title_names_another_holder(tmp_path):
    br = NorseBridge()
    env, _ = _env(tmp_path, br)
    br.primary[GOTT] = 301
    br.heirs[301] = 3
    orig = br.call

    def call(action, params=None, timeout=8.0):
        r = orig(action, params, timeout)
        if action == "query_title_succession":
            r["current_holder_id"] = OTHER
        return r
    br.call = call
    m = _m(env)
    assert m.get("heir_present") == (None, METRIC_UNREADABLE) and "names holder" in m.source["heir_present"]


# ── the goal ────────────────────────────────────────────────────────────────

def _metrics(**vals):
    m = Metrics("1091.09.15", GOTT)
    for k, v in vals.items():
        if v is None:
            m.status[k] = METRIC_UNREADABLE
        else:
            m.values[k], m.status[k] = v, METRIC_OK
    return m


START = _metrics(realm_counties=5, line_alive=True, ruler_changes=0, offensive_wars_declared=0)
KEPT = dict(realm_counties=5, line_alive=True, ruler_changes=1, offensive_wars_declared=0)


def test_the_line_is_kept_peacefully_only_when_every_leg_holds():
    assert goal_met("line_kept_peacefully", START, _metrics(**KEPT)) is True
    for k, v in (("ruler_changes", 0), ("offensive_wars_declared", 1), ("realm_counties", 4),
                 ("line_alive", False)):
        legs = line_kept_legs(START, _metrics(**{**KEPT, k: v}))
        assert legs[k]["holds"] is False and goal_met("line_kept_peacefully", START, _metrics(**{**KEPT, k: v})) \
            is False, k
    assert goal_met("line_kept_peacefully", START, _metrics(**{**KEPT, "ruler_changes": 3,
                                                                 "realm_counties": 9})) is True


@pytest.mark.parametrize("leg", ["ruler_changes", "offensive_wars_declared", "realm_counties", "line_alive"])
def test_an_unread_leg_is_no_verdict(leg):
    with pytest.raises(RewardUnavailable) as e:
        goal_met("line_kept_peacefully", START, _metrics(**{**KEPT, leg: None}))
    assert e.value.metric == leg
    assert line_kept_legs(START, _metrics(**{**KEPT, leg: None}))[leg]["holds"] is None


def test_an_unread_start_realm_is_no_verdict():
    with pytest.raises(RewardUnavailable) as e:
        goal_met("line_kept_peacefully", _metrics(realm_counties=None), _metrics(**KEPT))
    assert e.value.metric == "realm_counties" and e.value.status.startswith("start ")


def test_a_bool_is_not_a_count():
    legs = line_kept_legs(START, _metrics(**{**KEPT, "offensive_wars_declared": False}))
    assert legs["offensive_wars_declared"] == {"value": False, "status": "not_a_number", "holds": None}


def _fake_lor(years=2):
    d = copy.deepcopy(BUILTIN_DICTS["fake-solo"])
    d.update(id="fake-line", goal="line_kept_peacefully", horizon_days=years * 365, cycle_days=73,
             tools=["observe", "do", "declare_war", "pass_turn"],
             reward={"terms": [{"name": "realm_counties_delta", "weight": 1.0}], "team": "none"},
             fake={"counties": [12], "gold": [100.0], "prestige": [50.0], "ages": [20]})
    return scenario_from_dict(d)


def _run(env, keep_revolts=True, war_at=None):
    env.reset(seed=3)
    r = env.backend.realms["king"]
    r.successions = 1                       # one pass already made (the fake's draws rarely give one)
    out = []
    while env.agents:
        if not keep_revolts:
            r.counties = max(r.counties, 12)
        acts = [{"tool_name": "declare_war", "arguments": {"target_id": 100}}] if env._step == war_at else []
        out.append(env.step({"king": acts}))
    return out


def test_the_horizon_goal_does_not_end_the_episode_before_its_end_date():
    env = ParallelEnv(_fake_lor())
    out = _run(env, keep_revolts=False)
    assert len(out) == env.scenario.n_steps                         # every leg held from step 1 and it ran on
    last = out[-1][4]["king"]
    assert out[-1][2]["king"] is True and last["termination_reason"] == TERM_GOAL
    assert last["end"]["goal"] == "line_kept_peacefully" and all(v["holds"] for v in last["end"]["legs"].values())
    assert all(o[2]["king"] is False and o[3]["king"] is False for o in out[:-1])


def test_one_offensive_war_fails_the_goal_and_the_end_says_which_leg():
    env = ParallelEnv(_fake_lor())
    out = _run(env, keep_revolts=False, war_at=1)
    last = out[-1][4]["king"]
    assert out[-1][3]["king"] is True and last["truncation_reason"] == TRUNC_HORIZON
    legs = last["end"]["legs"]
    assert legs["offensive_wars_declared"] == {"value": 1, "status": METRIC_OK, "holds": False}
    assert legs["ruler_changes"]["holds"] is True


def test_the_fake_world_says_it_has_no_heir_rather_than_a_value():
    env = ParallelEnv("fake-solo")
    env.reset(seed=1)
    m = env.backend.metrics("king", ("heir_present", "ruler_changes"))
    assert m.get("heir_present") == (None, METRIC_UNREADABLE) and m.get("ruler_changes") == (0, METRIC_OK)
