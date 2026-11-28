"""Wake tasks on the REAL backend (ck3.py advance_until, wake_facts) against a fake host, a fake bridge
session whose clock runs between reads, and a fake seat transport. These prove the route as written: the
checks are control-session reads made while the clock runs, a fire stops the clock on the pause alarm the
next day and is confirmed paused there, a reading torn while running is not a fire, the forced wakes, and
the refusals. Only a live game proves the game behaves as these fakes do."""
from __future__ import annotations

import copy
import io
import json

import pytest

from ck3bench.env import ParallelEnv, calendar
from ck3bench.env.errors import EnvRefusal
from ck3bench.env.scenario import BUILTIN_DICTS, builtin, scenario_from_dict
from ck3bench.env.test_ck3_backend import FakeBridge, FakeHost, _save
from ck3bench.env.ck3 import WAKE_REALM_EVERY, Ck3Backend
from ck3bench.env.trajectory import TrajectoryWriter, check_trajectory

TASK = "ireland1066-kingdom-30y-wake"
DUBLIN = 31506
NEVER = {"metric": "gold", "op": ">=", "value": 1e9}


class WakeBridge(FakeBridge):
    """FakeBridge plus the reads a wake check makes, a world that changes on given dates, a clock that also
    moves `drift` days on every read made while it runs (a check takes wall time), and the alarm's
    `pause_at_not_in_future` refusal."""

    def __init__(self, **kw):
        super().__init__(date="1066.09.15", speed=0, rate=2, **kw)
        self.watch = {"status": "following", "line_character_id": DUBLIN}
        self.realm = {DUBLIN: 1}
        self.wars: list[dict] = []
        self.claims: list[dict] = []
        self.tasks: list[dict] = []
        self.held = [{"title_id": 1, "key": "c_dublin"}, {"title_id": 2, "key": "b_dublin"}]
        self.at: dict[str, callable] = {}        # date -> a change to the world on that day
        self.drift = 0
        self.flicker: dict[str, int] = {}        # read kind -> on which read (1-based) it shows a ghost row
        self.reads: dict[str, int] = {}
        self.refusals = 0

    def _move(self, days):
        o0 = calendar.ordinal(self.date)
        stop = calendar.ordinal(self.target)
        o = min(o0 + days, stop)
        for d in range(o0 + 1, o + 1):
            f = self.at.pop(calendar.date_from_ordinal(d), None)
            if f:
                f(self)
        self.date = calendar.date_from_ordinal(o)
        if o == stop:
            self.paused, self.alarm = True, "fired"

    def call(self, action, params=None, timeout=8.0):
        params = params or {}
        if action == "unpause_game":
            if calendar.ordinal(params["pause_at"]) <= calendar.ordinal(self.date):
                self.calls.append((action, params))
                self.refusals += 1
                return {"success": False, "action": "unpause_game", "reason": "pause_at_not_in_future",
                        "pause_at": params["pause_at"], "game_date": self.date}
            return super().call(action, params, timeout)
        if action == "get_tick_info":
            self.calls.append((action, params))
            if not self.paused and not self.stall:
                if self.disarm_once:
                    self.disarm_once, self.paused, self.alarm = False, True, "cleared"
                else:
                    self._move(self.rate)
            return {"game_date": self.date, "paused": self.paused, "game_speed": self.speed,
                    "pause_alarm": {"state": self.alarm}}
        if not self.paused and self.drift and action != "set_speed":
            self._move(self.drift)
        kind = {"get_active_wars": "wars", "query_claims": "claims", "query_council_tasks": "seats",
                "query_title_hierarchy": "held_titles"}.get(action)
        if kind is None:
            return super().call(action, params, timeout)
        self.calls.append((action, params))
        self.reads[kind] = self.reads.get(kind, 0) + 1
        rows = {"wars": self.wars, "claims": self.claims, "seats": self.tasks, "held_titles": self.held}[kind]
        rows = [dict(r) for r in rows]
        if self.flicker.get(kind) == self.reads[kind]:
            rows.append({"title_id": 999, "title_name": "a ghost"})
        if kind == "claims" and self.flicker.get("unread") == self.reads[kind]:
            return {"error": "timeout"}
        return {kind: rows, "character_id": params.get("character_id")}


class Seat:
    """The seat's toolkit server: the target-title reads metrics() makes, and the news observe."""

    def __init__(self):
        self.calls = []

    def call(self, tool, args):
        self.calls.append((tool, dict(args)))
        if tool == "inspect" and args.get("subject") == "title":
            return {"ok": True, "result": {"title_id": 500, "title_key": "k_ireland",
                                           "summary": {"held": 1, "total": 14}}}
        if tool == "inspect" and args.get("subject") == "title_hierarchy":
            return {"ok": True, "result": {"title": {"key": "k_ireland", "holder_id": None}}}
        return {"ok": True, "result": {"news": {}}}

    def health(self):
        self.calls.append(("session", {"action": "health"}))
        return {"ok": True, "result": {"notes": {"state": "on", "version": "abc123def456", "notes": 30}}}

    def close(self):
        pass


def _env(tmp_path, bridge=None, task=TASK, buf=None, **kw):
    br = bridge or WakeBridge()
    seats = {}

    def tf(seat, cid, env):
        seats[seat] = Seat()
        return seats[seat]
    b = Ck3Backend(_save(tmp_path), host=FakeHost(), link_factory=lambda s, c, m: br,
                   socket_resolver=lambda i: "/run/fake.sock", transport_factory=tf, work_dir=tmp_path,
                   poll_s=1.0, retry_s=0.5, socket_probe=lambda s: False, **kw)
    env = ParallelEnv(task if isinstance(task, str) else task, backend=b,
                      trajectory=TrajectoryWriter(stream=buf) if buf is not None else None)
    env.reset(seed=1)
    return env, br, seats


def _wake(env):
    return env.backend._last_wake if hasattr(env.backend, "_last_wake") else None


def _step(env, req=None):
    obs, r, te, tr, info = env.step({"dublin": []}, **({"wake": {"dublin": req}} if req is not None else {}))
    return obs["dublin"], env._last_wake


def _claim(tid):
    return lambda w: w.claims.append({"title_id": tid, "title_name": f"t{tid}"})


# ── the task and the vocabulary ──────────────────────────────────────────────

def test_the_wake_task_is_the_30y_task_with_a_wake_cadence():
    w, f = builtin(TASK), builtin("ireland1066-kingdom-30y")
    same = ("backend", "start_save", "start_date", "seats", "horizon_days", "cycle_days", "reward", "goal", "tools",
            "game_speed", "target_title", "end_date", "n_steps")
    assert all(getattr(w, k) == getattr(f, k) for k in same)
    assert (w.wake.check_days, w.wake.max_days, w.wake.forced) == (7, 365, ("ruler_changed", "war_declared_on"))
    assert f.wake is None and w.status == "buildable_now" and "run live with a model" in w.status_reason
    assert "scripted seat with no model" in w.status_reason


def test_the_vocabulary_is_control_session_reads_and_dejure_counties_is_refused(tmp_path):
    env, br, seats = _env(tmp_path)
    voc = env.backend.wake_vocabulary()
    assert "dejure_counties" not in voc["metrics"] and "piety" in voc["metrics"]
    assert {"claim_gained", "war_declared_on", "title_lost", "fabrication_ended"} <= set(voc["events"])
    with pytest.raises(EnvRefusal) as e:
        env.check_wake("dublin", {"when": [{"metric": "dejure_counties", "op": ">=", "value": 8}]})
    assert e.value.code == "wake_condition_unwitnessable"
    obs = env.observe("dublin")
    assert obs["wake"]["conditions"]["metrics"] == list(voc["metrics"]) and obs["steps_total"] is None


def test_without_a_target_title_title_gained_is_not_witnessed(tmp_path):
    d = copy.deepcopy(BUILTIN_DICTS[TASK])
    d.pop("target_title")
    d.update(id="no-target", goal="none", reward={"terms": [{"name": "gold_delta", "weight": 1.0}], "team": "none"})
    env, *_ = _env(tmp_path, task=scenario_from_dict(d))
    assert "title_gained" not in env.backend.wake_vocabulary()["events"]


# ── a fire stops the clock the next day, confirmed paused there ──────────────

def test_a_claim_arriving_while_the_clock_runs_stops_it_the_next_day(tmp_path):
    br = WakeBridge()
    br.at["1066.10.20"] = _claim(77)
    env, br, seats = _env(tmp_path, bridge=br)
    n_observe = sum(1 for t, a in seats["dublin"].calls if t == "observe")
    obs, w = _step(env, {"max_days": 365, "when": [{"event": "claim_gained"}]})
    p = w["probe"]
    assert w["stop"] == "condition" and w["fired"][0]["condition"] == {"event": "claim_gained"}
    assert w["fired"][0]["read"] == ["77"]
    assert calendar.ordinal(p["fire_read_date"]) >= calendar.ordinal("1066.10.20")
    assert p["lag_days"] == 1 and p["stop_date"] == obs["game_date"] == calendar.add_days(p["fire_read_date"], 1)
    assert w["days"] == calendar.days_between("1066.09.15", obs["game_date"]) < 365
    assert p["checks_running"] >= 4 and p["checks_paused"] == 1 and p["torn"] == 0
    # the checks are control-session reads; the seat's own server read its news once, after the step
    assert any(a == "query_claims" for a, _ in br.calls)
    assert sum(1 for t, a in seats["dublin"].calls if t == "observe") == n_observe + 1


def test_a_check_is_made_every_check_days_while_running(tmp_path):
    env, br, _ = _env(tmp_path)
    _, w = _step(env, {"max_days": 70, "when": [NEVER]})
    assert w["stop"] == "max_days" and w["days"] == 70
    # 70 days at check_days 7: a check every 7-8 days of clock (the clock moves 2 days a poll) + the paused last
    assert 8 <= w["probe"]["checks_running"] <= 10 and w["probe"]["checks_paused"] == 1


def test_an_alarm_day_already_reached_is_armed_for_the_day_after_the_bridge_read(tmp_path):
    br = WakeBridge()
    br.drift = 1                 # every read made while running moves the clock a day
    br.at["1066.10.01"] = _claim(5)
    env, br, _ = _env(tmp_path, bridge=br)
    obs, w = _step(env, {"max_days": 365, "when": [{"event": "claim_gained"}]})
    assert w["stop"] == "condition" and w["probe"]["stop_arms"] >= 1 and br.refusals >= 1
    assert w["probe"]["lag_days"] >= 2, "the stop lands later than the next day, and the lag says so"


def test_a_reading_torn_while_running_is_not_a_fire(tmp_path):
    br = WakeBridge()
    br.flicker["claims"] = 3     # one running read names a claim the next read does not
    env, br, _ = _env(tmp_path, bridge=br)
    obs, w = _step(env, {"max_days": 100, "when": [{"event": "claim_gained"}]})
    assert w["probe"]["torn"] == 1 and w["stop"] == "max_days" and w["days"] == 100 and w["fired"] == []


def test_a_read_that_does_not_answer_fires_nothing_and_is_counted(tmp_path):
    br = WakeBridge()
    br.flicker["unread"] = 3
    env, br, _ = _env(tmp_path, bridge=br)
    _, w = _step(env, {"max_days": 60, "when": [{"event": "claim_gained"}]})
    assert w["stop"] == "max_days" and w["probe"]["unread"] == 1 and w["probe"]["unread_reads"] == ["query_claims"]


def test_a_threshold_and_the_realm_read_on_its_sub_cadence(tmp_path):
    br = WakeBridge()
    env, br, _ = _env(tmp_path, bridge=br)
    before = sum(1 for a, _ in br.calls if a == "query_realm")
    br.at["1066.12.01"] = lambda w: w.profile.update(gold=200.0)
    _, w = _step(env, {"max_days": 365, "when": [{"metric": "gold", "op": ">=", "value": 150},
                                                 {"event": "realm_changed"}]})
    assert w["stop"] == "condition" and w["fired"][0]["read"] == 200.0
    realm = sum(1 for a, _ in br.calls if a == "query_realm") - before
    checks = w["probe"]["checks_running"] + w["probe"]["checks_paused"]
    # paused reads (the step's start, the stop) always; running checks every WAKE_REALM_EVERY-th
    assert realm < checks + 1 and realm >= 2 + (w["probe"]["checks_running"] - 1) // WAKE_REALM_EVERY


def test_piety_and_a_lost_county_wake_the_seat(tmp_path):
    br = WakeBridge()
    br.profile["piety"] = 10.0
    br.held.append({"title_id": 3, "key": "c_leinster"})
    br.at["1066.11.10"] = lambda w: w.held.remove({"title_id": 3, "key": "c_leinster"})
    env, br, _ = _env(tmp_path, bridge=br)
    _, w = _step(env, {"max_days": 365, "when": [{"event": "title_lost"},
                                                 {"metric": "piety", "op": ">=", "value": 1000}]})
    assert w["stop"] == "condition" and w["fired"] == [{"seat": "dublin", "index": 0, "condition": {
        "event": "title_lost"}, "read": ["c_leinster"], "date": w["probe"]["stop_date"]}]


def test_a_fabrication_that_stops_and_a_war_that_ends(tmp_path):
    br = WakeBridge()
    br.tasks = [{"task_name": "task_fabricate_claim", "target_title_id": 34}]
    br.wars = [{"war_id": 9, "is_attacker": True}]
    br.at["1066.10.05"] = lambda w: w.tasks.clear()
    env, br, _ = _env(tmp_path, bridge=br)
    _, w = _step(env, {"max_days": 365, "when": [{"event": "fabrication_ended"}, {"event": "war_ended"}]})
    assert [f["condition"]["event"] for f in w["fired"]] == ["fabrication_ended"] and w["fired"][0]["read"] == ["34"]
    br.at[calendar.add_days(env._date, 20)] = lambda w: w.wars.clear()
    _, w = _step(env, {"max_days": 365, "when": [{"event": "war_ended"}]})
    assert w["stop"] == "condition" and w["fired"][0]["read"] == ["9"]


# ── forced wakes: whatever the seat asked ─────────────────────────────────────

def test_a_war_declared_on_the_seat_wakes_it_though_it_asked_nothing(tmp_path):
    br = WakeBridge()
    br.at["1066.10.10"] = lambda w: w.wars.append({"war_id": 41, "is_attacker": False, "defender_id": DUBLIN})
    env, br, _ = _env(tmp_path, bridge=br)
    obs, w = _step(env)            # no request: the default 73 days, declared false
    assert w["stop"] == "condition" and w["days"] < 73 and w["forced"] == ["ruler_changed", "war_declared_on"]
    assert w["fired"][0]["forced"] is True and w["fired"][0]["condition"] == {"event": "war_declared_on"}
    rec = env._last_wake
    assert rec["planned_days"] == 73


def test_a_war_the_seat_attacks_in_does_not_force_a_wake(tmp_path):
    br = WakeBridge()
    br.at["1066.10.10"] = lambda w: w.wars.append({"war_id": 42, "is_attacker": True, "attacker_id": DUBLIN})
    env, br, _ = _env(tmp_path, bridge=br)
    _, w = _step(env)
    assert w["stop"] == "max_days" and w["days"] == 73 and w["fired"] == []


def test_a_succession_in_the_line_wakes_the_seat(tmp_path):
    br = WakeBridge()
    br.at["1066.11.01"] = lambda w: w.watch.update(line_character_id=36124)
    env, br, _ = _env(tmp_path, bridge=br)
    _, w = _step(env, {"max_days": 365, "when": [NEVER]})
    assert w["stop"] == "condition" and [f["condition"] for f in w["fired"]] == [{"event": "ruler_changed"}]
    assert w["fired"][0]["read"] == 36124 and w["fired"][0]["forced"] is True


# ── the record ────────────────────────────────────────────────────────────────

def test_the_episode_is_a_well_formed_wake_record(tmp_path):
    buf = io.StringIO()
    br = WakeBridge()
    br.at["1066.10.20"] = _claim(77)
    env, br, _ = _env(tmp_path, bridge=br, buf=buf)
    _step(env, {"max_days": 365, "when": [{"event": "claim_gained"}]})
    _step(env)
    env.close()
    recs = [json.loads(x) for x in buf.getvalue().splitlines()]
    assert check_trajectory(recs) == []
    steps = [r for r in recs if r["kind"] == "step"]
    assert [s["wake"]["stop"] for s in steps] == ["condition", "max_days"]
    assert all(isinstance(s["wake"]["probe"]["probe_wall_s"], float) for s in steps)
    assert recs[0]["scenario"]["wake"]["forced"] == ["ruler_changed", "war_declared_on"]


def test_the_fixed_30y_task_on_the_real_backend_takes_no_wake_path(tmp_path):
    env, br, _ = _env(tmp_path, task="ireland1066-kingdom-30y")
    obs, r, te, tr, info = env.step({"dublin": []})
    assert obs["dublin"]["game_date"] == "1066.11.27" and "wake" not in obs["dublin"]
    assert not any(a in ("query_claims", "get_active_wars") for a, _ in br.calls)
    with pytest.raises(EnvRefusal) as e:
        env.step({"dublin": []}, wake={"dublin": {"max_days": 5}})
    assert e.value.code == "wake_not_declared"


# ── the notes the seat was served ───────────────────────────────────────────

def test_the_header_records_each_seats_notes_from_its_own_server(tmp_path, monkeypatch):
    buf = io.StringIO()
    monkeypatch.setenv("CK3_NOTES", "on")
    env, br, seats = _env(tmp_path, buf=buf)
    head = json.loads(buf.getvalue().splitlines()[0])
    assert head["provenance"]["notes"] == {"dublin": {"state": "on", "version": "abc123def456", "notes": 30}}
    assert head["provenance"]["notes_switch"] == "on"
    assert ("session", {"action": "health"}) in seats["dublin"].calls


def test_a_server_that_reports_no_notes_is_unread_not_off(tmp_path, monkeypatch):
    monkeypatch.delenv("CK3_NOTES", raising=False)

    class Old(Seat):
        def health(self):
            return {"ok": True, "result": {"state": "connected"}}
    br = WakeBridge()
    b = Ck3Backend(_save(tmp_path), host=FakeHost(), link_factory=lambda s, c, m: br,
                   socket_resolver=lambda i: "/run/fake.sock", transport_factory=lambda s, c, e: Old(),
                   work_dir=tmp_path, poll_s=1.0, retry_s=0.5, socket_probe=lambda s: False)
    w = b.open(builtin(TASK), seed=1)
    assert w.provenance["notes"] == {"dublin": {"unread": 'session(action="health") result carries no notes'}}
    assert w.provenance["notes_switch"] == "unset"


def test_a_control_log_holds_every_request_and_reply_when_asked(tmp_path, monkeypatch):
    log = tmp_path / "control.jsonl"
    monkeypatch.setenv("CK3BENCH_CONTROL_LOG", str(log))
    env, br, _ = _env(tmp_path)
    _step(env, {"max_days": 20, "when": [NEVER]})
    rows = [json.loads(x) for x in log.read_text().splitlines()]
    assert len(rows) == len(br.calls) and {"get_tick_info", "unpause_game", "query_character"} <= {r["action"] for r in rows}
    assert all("reply" in r and r["dt"] >= 0 for r in rows)
    monkeypatch.delenv("CK3BENCH_CONTROL_LOG")
    (tmp_path / "b").mkdir()
    env2, br2, _ = _env(tmp_path / "b")
    assert not isinstance(env2.backend.link, __import__("ck3bench.env.ck3", fromlist=["x"]).LoggedLink)


# ── the 180-day variant of the wake task ──
TASK180 = "ireland1066-kingdom-30y-wake180"
#: sha256 of the 365-day task's dict (json, sorted keys, without `status_reason`: the task's prose on what has run
#: of it) before the variant was added: everything an episode reads is untouched
WAKE365_SHA = "ddeccb4f0ab3ce5427e2313361a5b87a1444196240c917e0d26f9c6376c97b70"


def _without_prose(d):
    """The declaration without its prose on what has run (`status_reason`) and without its notes set, which the
    Ireland tasks declare since the toolkit's default set became general advice (asserted to be the Ireland set:
    the notes those tasks were served before, under its own name)."""
    assert d.get("notes_set") == "ireland1066", d.get("notes_set")
    return {k: v for k, v in d.items() if k not in ("status_reason", "notes_set")}


def test_wake180_the_365_day_task_is_byte_for_byte_the_base_one():
    import hashlib
    blob = json.dumps(_without_prose(BUILTIN_DICTS[TASK]), sort_keys=True, ensure_ascii=False).encode()
    assert hashlib.sha256(blob).hexdigest() == WAKE365_SHA


def test_wake180_the_180_day_variant_differs_only_in_its_maximum_step():
    a, b = BUILTIN_DICTS[TASK], BUILTIN_DICTS[TASK180]
    assert {k for k in set(a) | set(b) if a.get(k) != b.get(k)} == {"id", "description", "wake", "status_reason"}
    assert b["wake"] == dict(a["wake"], max_days=180) and a["wake"]["max_days"] == 365
    assert b["description"] == a["description"].replace("at most 365 days", "at most 180 days")
    assert b["description"] != a["description"]
    sa, sb = builtin(TASK), builtin(TASK180)
    for f in ("seats", "horizon_days", "cycle_days", "reward", "goal", "target_title", "start_save", "start_date",
              "tools", "game_speed"):
        if hasattr(sa, f):
            assert getattr(sa, f) == getattr(sb, f), f
    assert sb.wake.max_days == 180 and sb.wake.check_days == sa.wake.check_days and sb.wake.forced == sa.wake.forced


def test_wake180_a_seat_may_ask_up_to_180_days_and_no_more():
    from ck3bench.env import wake as W
    cap = builtin(TASK180).wake.max_days
    assert W.parse_request("dublin", {"max_days": 180}, cap_days=cap, default_days=73, vocabulary={})["max_days"] == 180
    with pytest.raises(EnvRefusal):
        W.parse_request("dublin", {"max_days": 181}, cap_days=cap, default_days=73, vocabulary={})
    # control: the 365-day task takes 181
    assert W.parse_request("dublin", {"max_days": 181}, cap_days=builtin(TASK).wake.max_days, default_days=73,
                           vocabulary={})["max_days"] == 181


# ── the wake task played from Munster's seat ──
TASKM = "munster1066-kingdom-30y-wake"


def test_munster_the_wake_task_is_untouched_by_the_seat_variant():
    import hashlib
    blob = json.dumps(_without_prose(BUILTIN_DICTS[TASK]), sort_keys=True, ensure_ascii=False).encode()
    assert hashlib.sha256(blob).hexdigest() == WAKE365_SHA


def test_munster_differs_from_the_wake_task_only_in_its_seat():
    a, b = BUILTIN_DICTS[TASK], BUILTIN_DICTS[TASKM]
    assert {k for k in set(a) | set(b) if a.get(k) != b.get(k)} == {"id", "description", "seats", "status_reason"}
    assert b["seats"] == [{"name": "munster", "character_id": 31762}]
    sa, sb = builtin(TASK), builtin(TASKM)
    for f in ("horizon_days", "cycle_days", "reward", "goal", "target_title", "start_save", "start_date", "end_date",
              "tools", "game_speed", "wake", "n_steps"):
        if hasattr(sa, f):
            assert getattr(sa, f) == getattr(sb, f), f
    assert sb.end_date == "1096.09.15" and sb.wake.max_days == 365 and sb.target_title == "k_ireland"
    # the measured wall time is the base task's; the description names the seat, never Dublin's ruler
    assert sb.status_reason.split(". Time: ")[1] == sa.status_reason.split(". Time: ")[1]
    assert "Duke of Munster" in sb.description and "Dublin" not in sb.description
    assert "31506" not in json.dumps(b) and "run live with a model" in sb.status_reason


def test_munster_seat_binds_its_own_ruler_on_the_real_backend(tmp_path):
    """The real backend (fake host and bridge) binds the seat `munster` to 31762: the observation, the reward and
    the trajectory header are keyed `munster` and read his line, not Dublin's."""
    br = WakeBridge()
    br.watch = {"status": "following", "line_character_id": 31762}
    br.realm = {31762: 3}
    buf = io.StringIO()
    env, br, seats = _env(tmp_path, bridge=br, task=TASKM, buf=buf)
    assert env.backend.seats == {"munster": 31762} and set(seats) == {"munster"}
    obs, r, te, tr, info = env.step({"munster": []})
    assert set(obs) == set(r) == {"munster"} and obs["munster"]["character_id"] == 31762
    head = json.loads(buf.getvalue().splitlines()[0])
    assert head["kind"] == "header" and head["seats"] == {"munster": 31762}
    cids = {p.get("character_id") for a, p in br.calls if "character_id" in p}
    assert 31762 in cids and DUBLIN not in cids, cids
