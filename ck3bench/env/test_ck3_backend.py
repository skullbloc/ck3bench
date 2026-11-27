"""The real backend's logic against a fake host, a fake bridge session and a
fake seat transport. These prove the route as WRITTEN: the refusals, the
clock loop's ends, the metric reads; they cannot prove the game behaves as the
fakes do (only a live game can)."""
from __future__ import annotations

import hashlib

import pytest

from ck3bench.env import ParallelEnv, ToolCall, contract
from ck3bench.env import calendar
from ck3bench.env import ck3 as ck3mod
from ck3bench.env.ck3 import Ck3Backend, _channel_closed
from ck3bench.env.errors import (R_CHECKPOINT_UNSUPPORTED, R_LAUNCH, R_SAVE_MISSING, R_SAVE_SHA,
                                 R_SPEED, R_START_DATE, TRUNC_BRIDGE_UNAVAILABLE, TRUNC_CLOCK_STALLED,
                                 TRUNC_GAME_EXITED, TRUNC_SCRIPT_LOG_CLOSED, EnvRefusal)
from ck3bench.env.scenario import BUILTIN_DICTS, scenario_from_dict

PID = 4242
EXE = "/opt/game/binaries/ck3"


class FakeHost:
    def __init__(self, out=f"CK3 pid {PID}; output x\ngame paused\n", rc=0):
        self.out, self.rc = out, rc
        self.alive_pids = {PID}
        self.killed: list[int] = []
        self.t = 0.0
        self.argv = None

    def run(self, argv, env, timeout):
        self.argv, self.env = argv, env
        return self.rc, self.out

    def exe(self, pid):
        return EXE if pid in self.alive_pids else None

    def alive(self, pid):
        return pid in self.alive_pids

    def exists(self, pid):
        return pid in self.alive_pids

    def kill(self, pid):
        self.killed.append(pid)
        self.alive_pids.discard(pid)

    def sleep(self, s):
        self.t += s

    def monotonic(self):
        return self.t


class FakeBridge:
    """get_tick_info's clock: running, it moves `rate` days per read until the
    alarm's date, where it pauses with the alarm `fired`."""

    def __init__(self, date="1066.09.15", speed=1, rate=10):
        self.date, self.speed, self.rate = date, speed, rate
        self.paused, self.alarm, self.target = True, "none", None
        self.stall = False
        self.dead_after: int | None = None
        self.disarm_once = False
        self.fire_short = 0          # the alarm reads fired this many days before its target
        self.calls: list[tuple[str, dict]] = []
        self.error = ""
        self.realm = {37067: 84}
        self.watch = {"status": "following", "line_character_id": 37067}
        self.profile = {"gold": 123.5, "prestige": 400.0, "alive": True, "id": 37067}
        self.speed_reads_as = None
        self.foreign: list[dict] = []    # frames another session caused, read as this call's reply
        #: query_character `top_liege_id` by character (absent: the character is independent);
        #: None makes the reply carry no top_liege_id
        self.top: dict[int, int | None] = {}
        self.domain = {37067: 4}         # query_realm domain_size (the counties a ruler holds himself)
        self.rulers: list[dict] = []     # search_characters(filter=ruler) rows
        #: query_character `liege_id` by character (absent: the reply carries none); with
        #: `court_of` the ruler listing keeps the rows whose `_liege` is that ruler
        self.liege: dict[int, int] = {}
        self.court_reply: dict | None = None    # a court_of listing's reply, when set (a refusal)

    def call(self, action, params=None, timeout=8.0):
        self.calls.append((action, params or {}))
        if self.foreign:
            return self.foreign.pop(0)
        if self.dead_after is not None and len(self.calls) > self.dead_after:
            self.error = "socket not listening"
            return None
        if action == "set_speed":
            self.speed = params["speed"] if self.speed_reads_as is None else self.speed_reads_as
            return {"success": True}
        if action == "unpause_game":
            self.target, self.paused, self.alarm = params["pause_at"], False, "armed"
            return {"success": True}
        if action == "get_tick_info":
            if not self.paused and not self.stall:
                if self.disarm_once:
                    self.disarm_once, self.paused, self.alarm = False, True, "cleared"
                else:
                    stop = calendar.ordinal(self.target) - self.fire_short
                    o = min(calendar.ordinal(self.date) + self.rate, stop)
                    self.date = calendar.date_from_ordinal(o)
                    if o == stop:
                        self.paused, self.alarm = True, "fired"
            return {"game_date": self.date, "paused": self.paused, "game_speed": self.speed,
                    "pause_alarm": {"state": self.alarm}}
        if action == "query_character":
            if params.get("played_line") == 1:
                return {**self.profile, "played_line": dict(self.watch)}
            cid = params["character_id"]
            top = self.top.get(cid, cid)
            return {**self.profile, "id": cid, **({"top_liege_id": top} if top is not None else {}),
                    **({"liege_id": self.liege[cid]} if cid in self.liege else {})}
        if action == "query_realm":
            n = self.realm.get(params["character_id"])
            if n is None:
                return {"error": "no realm"}
            d = self.domain.get(params["character_id"])
            return {"realm_total_counties": n, **({"domain_size": d} if d is not None else {})}
        if action == "search_characters":
            if "court_of" in params:
                if self.court_reply is not None:
                    return dict(self.court_reply)
                rows = [{k: v for k, v in r.items() if k != "_liege"} for r in self.rulers
                        if r.get("_liege") == params["court_of"]]
                return {"characters": rows, "returned": len(rows)}
            return {"characters": [{k: v for k, v in r.items() if k != "_liege"} for r in self.rulers],
                    "returned": len(self.rulers)}
        return {"error": f"unknown {action}"}

    def close(self):
        pass


class FakeTransport:
    def __init__(self, replies=None):
        self.replies = replies or {}
        self.calls: list = []
        self.closed = False

    def call(self, tool_name, arguments):
        self.calls.append((tool_name, arguments))
        return self.replies.get(tool_name, {"ok": True, "tool": tool_name})

    def close(self):
        self.closed = True


def _save(tmp_path, name="_1066_09_15.ck3", data=b"SAVE"):
    d = tmp_path / "saves"
    d.mkdir(exist_ok=True)
    (d / name).write_bytes(data)
    return d


def _backend(tmp_path, bridge=None, host=None, transports=None, **kw):
    bridge = bridge or FakeBridge(speed=0)
    host = host or FakeHost()
    transports = transports if transports is not None else {}

    def tf(seat, cid, env):
        transports[seat] = FakeTransport()
        transports[seat].env = env
        return transports[seat]
    kw.setdefault("socket_probe", lambda sock: False)
    b = Ck3Backend(_save(tmp_path), host=host, link_factory=lambda s, c, m: bridge,
                   socket_resolver=lambda i: "/run/fake.sock", transport_factory=tf, work_dir=tmp_path,
                   poll_s=1.0, retry_s=0.5, **kw)
    return b, bridge, host, transports


def _scn(**over):
    d = dict(BUILTIN_DICTS["france1066-treasury-2y"], **over)
    return scenario_from_dict(d)


def test_open_launches_the_copy_sets_speed_and_binds_each_seat(tmp_path):
    b, br, host, tr = _backend(tmp_path)
    w = b.open(_scn(), seed=1)
    assert w.game_date == "1066.09.15" and w.characters == {"king": 37067}
    assert host.argv[:3] == ["ck3agent", "launch", "--save"] and host.argv[3].endswith("_1066_09_15.ck3")
    # the launch waits with margin over the slowest measured load (119.5 s), not the CLI's 120 s
    assert host.argv[4:] == ["--wait", str(ck3mod.LAUNCH_ACTIVE_WAIT_S)] and ck3mod.LAUNCH_ACTIVE_WAIT_S >= 2.5 * 119.5
    assert host.argv[3] != str(tmp_path / "saves" / "_1066_09_15.ck3"), "the game runs a COPY of the save"
    assert ("set_speed", {"speed": 3}) in br.calls
    assert ("query_character", {"character_id": 37067, "played_line": 1}) in br.calls, "the line watch is armed"
    assert tr["king"].env["CK3_AGENT_CHARACTER_ID"] == "37067"
    assert tr["king"].env["CK3_CLOCK_OWNER"] == "env", "the seat's server is told the environment owns the clock"
    assert w.provenance["start_save_sha256"] == hashlib.sha256(b"SAVE").hexdigest()
    assert w.provenance["fake"] is False


def test_open_runs_the_clock_to_a_later_declared_start(tmp_path):
    b, br, _, _ = _backend(tmp_path, bridge=FakeBridge(date="1066.09.01", speed=0))
    assert b.open(_scn(), seed=1).game_date == "1066.09.15"
    assert ("unpause_game", {"pause_at": "1066.09.15"}) in br.calls


def test_a_save_past_the_declared_start_is_refused(tmp_path):
    b, *_ = _backend(tmp_path, bridge=FakeBridge(date="1067.01.01", speed=0))
    with pytest.raises(EnvRefusal) as e:
        b.open(_scn(), seed=1)
    assert e.value.code == R_START_DATE


def test_a_speed_that_does_not_read_back_is_refused(tmp_path):
    br = FakeBridge(speed=0)
    br.speed_reads_as = 2
    b, *_ = _backend(tmp_path, bridge=br)
    with pytest.raises(EnvRefusal) as e:
        b.open(_scn(), seed=1)
    assert e.value.code == R_SPEED


@pytest.mark.parametrize("out,rc", [("CK3 pid 4242\n", 0), ("no pid here\ngame paused\n", 0),
                                    (f"CK3 pid {PID}\ngame paused\n", 1)])
def test_a_launch_that_did_not_reach_a_paused_game_is_refused_and_its_game_killed(tmp_path, out, rc):
    host = FakeHost(out=out, rc=rc)
    b, *_ = _backend(tmp_path, host=host)
    with pytest.raises(EnvRefusal) as e:
        b.open(_scn(), seed=1)
    assert e.value.code == R_LAUNCH
    assert host.killed == ([PID] if "CK3 pid" in out else [])


def test_open_refusals_before_anything_launches(tmp_path):
    b, _, host, _ = _backend(tmp_path)
    with pytest.raises(EnvRefusal) as e:
        b.open(_scn(start={"save": "other.ck3", "date": "1066.09.15"}), seed=1)
    assert e.value.code == R_SAVE_MISSING
    with pytest.raises(EnvRefusal) as e:
        b.open(_scn(start={"save": "_1066_09_15.ck3", "save_sha256": "0" * 64, "date": "1066.09.15"}), seed=1)
    assert e.value.code == R_SAVE_SHA
    with pytest.raises(EnvRefusal) as e:
        b.open(_scn(mods={"policy_mod": True}), seed=1)
    assert e.value.code == "mods_not_wired"
    assert host.argv is None, "nothing was launched"
    with pytest.raises(EnvRefusal) as e:
        Ck3Backend(None).open(_scn(), seed=1)
    assert e.value.code == R_SAVE_MISSING


def test_advance_reaches_the_target_and_re_arms_a_disarmed_alarm(tmp_path):
    b, br, _, _ = _backend(tmp_path)
    b.open(_scn(), seed=1)
    br.disarm_once = True
    r = b.advance(30)
    assert r.reached and r.game_date == "1066.10.15" and r.extra["rearms"] == 1


def test_an_alarm_that_fired_short_of_the_target_is_not_a_reached_step(tmp_path):
    b, br, _, _ = _backend(tmp_path)
    b.open(_scn(), seed=1)
    br.fire_short = 3
    r = b.advance(30)
    assert not r.reached and r.truncation == TRUNC_CLOCK_STALLED and "the target was 1066.10.15" in r.detail


def test_advance_ends_typed_when_the_clock_stalls(tmp_path):
    b, br, _, _ = _backend(tmp_path, wall_slack_s=5)
    b.open(_scn(), seed=1)
    br.stall = True
    r = b.advance(30)
    assert not r.reached and r.truncation == TRUNC_CLOCK_STALLED


def test_advance_ends_typed_when_the_bridge_stops_answering(tmp_path):
    b, br, _, _ = _backend(tmp_path)
    b.open(_scn(), seed=1)
    br.dead_after = len(br.calls)
    r = b.advance(30)
    assert r.truncation == TRUNC_BRIDGE_UNAVAILABLE and "5 tries" in r.detail


def test_advance_ends_typed_when_the_game_is_gone(tmp_path):
    b, _, host, _ = _backend(tmp_path)
    b.open(_scn(), seed=1)
    host.alive_pids.clear()
    assert b.advance(30).truncation == TRUNC_GAME_EXITED


def test_a_tool_reply_reading_the_script_log_closed_truncates_the_next_advance(tmp_path):
    b, _, _, tr = _backend(tmp_path)
    b.open(_scn(), seed=1)
    tr["king"].replies["do"] = {"ok": True, "result": {"script_log_channel": {"state": "closed"}}}
    assert b.call("king", ToolCall("do", {"intent": "x"})).ok
    assert b.advance(30).truncation == TRUNC_SCRIPT_LOG_CLOSED


def test_metrics_read_the_line_then_its_realm_and_profile(tmp_path):
    b, br, _, _ = _backend(tmp_path)
    b.open(_scn(), seed=1)
    m = b.metrics("king", ("realm_counties", "gold", "prestige", "line_alive"))
    assert m.values == {"realm_counties": 84, "gold": 123.5, "prestige": 400.0, "line_alive": True}
    br.watch = {"status": "following", "line_character_id": 56019}    # a succession: the line moved
    br.realm[56019] = 81
    m = b.metrics("king", ("realm_counties",))
    assert m.character_id == 56019 and m.values["realm_counties"] == 81


# ── realm and demesne: what query_realm's total means for a vassal ──────────
#: The bridge credits every county holder to his independent TOP liege only, so
#: query_realm `realm_total_counties` of a vassal is the counties he holds himself.
#: Read live: a duke whose own observe showed his duchy 7 of 7 read 1 there; the
#: census's ruler listing gave the same duke 9 (his own and his vassal tree's).
DUKE = 31808


def _vassal_world(br):
    br.top[DUKE] = 37067
    br.realm[DUKE], br.domain[DUKE] = 1, 1          # query_realm: for a vassal, his demesne twice
    br.rulers = [{"id": 37067, "tier": 4, "realm_counties": 84, "is_independent": True},
                 {"id": DUKE, "tier": 3, "realm_counties": 9, "is_independent": False}]


def _council(tmp_path, br):
    d = dict(BUILTIN_DICTS["france1066-treasury-2y"],
             seats=[{"name": "king", "character_id": 37067}, {"name": "duke1", "character_id": DUKE}],
             reward={"terms": [{"name": "realm_counties_delta", "weight": 1.0},
                               {"name": "demesne_counties_delta", "weight": 0.0}], "team": "none"})
    b, *_ = _backend(tmp_path, bridge=br)
    b.open(scenario_from_dict(d), seed=1)
    return b


def test_a_vassal_seats_realm_is_his_vassal_tree_and_his_demesne_is_his_own(tmp_path):
    br = FakeBridge(speed=0)
    _vassal_world(br)
    b = _council(tmp_path, br)
    br.watch = {"status": "following", "line_character_id": DUKE}
    m = b.metrics("duke1", ("realm_counties", "demesne_counties"))
    assert m.values == {"realm_counties": 9, "demesne_counties": 1}
    assert "search_characters" in m.source["realm_counties"] and "domain_size" in m.source["demesne_counties"]


def test_an_independent_rulers_realm_is_query_realms_total_and_reads_no_listing(tmp_path):
    br = FakeBridge(speed=0)
    _vassal_world(br)
    b = _council(tmp_path, br)
    n = len(br.calls)
    m = b.metrics("king", ("realm_counties", "demesne_counties"))
    assert m.values == {"realm_counties": 84, "demesne_counties": 4}
    assert "realm_total_counties" in m.source["realm_counties"]
    assert not [c for c in br.calls[n:] if c[0] == "search_characters"]


def test_a_character_that_cannot_be_told_ruler_or_vassal_reads_no_realm(tmp_path):
    br = FakeBridge(speed=0)
    _vassal_world(br)
    b = _council(tmp_path, br)
    for top in (None, 0):
        br.top[37067] = top
        m = b.metrics("king", ("realm_counties",))
        assert m.status["realm_counties"] == "unreadable" and "realm_counties" not in m.values


def test_a_vassal_missing_from_the_listing_reads_unreadable(tmp_path):
    br = FakeBridge(speed=0)
    _vassal_world(br)
    b = _council(tmp_path, br)
    br.watch = {"status": "following", "line_character_id": DUKE}
    br.rulers = [r for r in br.rulers if r["id"] != DUKE]
    m = b.metrics("duke1", ("realm_counties",))
    assert m.status["realm_counties"] == "unreadable" and str(DUKE) in m.source["realm_counties"]


def test_the_ruler_listing_is_read_once_per_paused_moment(tmp_path):
    br = FakeBridge(speed=0)
    _vassal_world(br)
    b = _council(tmp_path, br)
    br.watch = {"status": "following", "line_character_id": DUKE}

    def listings():
        return sum(1 for c in br.calls if c[0] == "search_characters")
    n = listings()
    b.metrics("duke1", ("realm_counties",))
    b.metrics("duke1", ("realm_counties",))
    assert listings() == n + 1
    b.advance(30)
    b.metrics("duke1", ("realm_counties",))
    assert listings() == n + 2, "a listing read before the clock ran is not reused after it"


# ── the narrow read: a vassal's row from his liege's court ──────────────────
#: Read live (1066 start, the Duke of Normandy under the King of France): the court
#: listing (search_characters court_of=liege) gave his row the same realm_counties
#: (9) as the whole listing, at 13-16 KB against 2.4 MB.

def _court_world(br):
    _vassal_world(br)
    br.liege[DUKE] = 37067
    br.rulers[1]["_liege"] = 37067


def test_a_vassal_with_a_liege_reads_his_row_from_the_liege_court_not_the_whole_listing(tmp_path):
    br = FakeBridge(speed=0)
    _court_world(br)
    b = _council(tmp_path, br)
    br.watch = {"status": "following", "line_character_id": DUKE}
    n = len(br.calls)
    m = b.metrics("duke1", ("realm_counties",))
    assert m.values == {"realm_counties": 9}
    assert "court_of=37067" in m.source["realm_counties"]
    lists = [c for c in br.calls[n:] if c[0] == "search_characters"]
    assert len(lists) == 1 and lists[0][1].get("court_of") == 37067 and lists[0][1]["max_results"] == 500


def test_the_court_listing_is_read_once_per_liege_per_paused_moment(tmp_path):
    br = FakeBridge(speed=0)
    _court_world(br)
    b = _council(tmp_path, br)
    br.watch = {"status": "following", "line_character_id": DUKE}

    def courts():
        return sum(1 for c in br.calls if c[0] == "search_characters" and "court_of" in c[1])
    b.metrics("duke1", ("realm_counties",))
    b.metrics("duke1", ("realm_counties",))
    assert courts() == 1
    b.advance(30)
    b.metrics("duke1", ("realm_counties",))
    assert courts() == 2, "a court read before the clock ran is not reused after it"


@pytest.mark.parametrize("refused", [{"error": "bridge_busy"}, {"success": False, "reason": "x"}])
def test_a_refused_court_listing_reads_unreadable(tmp_path, refused):
    br = FakeBridge(speed=0)
    _court_world(br)
    br.court_reply = refused
    b = _council(tmp_path, br)
    br.watch = {"status": "following", "line_character_id": DUKE}
    m = b.metrics("duke1", ("realm_counties",))
    assert m.status["realm_counties"] == "unreadable" and "court_of=37067" in m.source["realm_counties"]


def test_a_vassal_absent_from_his_liege_court_reads_unreadable(tmp_path):
    br = FakeBridge(speed=0)
    _court_world(br)
    br.rulers[1]["_liege"] = 99999           # listed under someone else's court
    b = _council(tmp_path, br)
    br.watch = {"status": "following", "line_character_id": DUKE}
    m = b.metrics("duke1", ("realm_counties",))
    assert m.status["realm_counties"] == "unreadable" and str(DUKE) in m.source["realm_counties"]


def test_the_census_record_carries_what_it_ranked_on(tmp_path):
    from ck3bench.env import census
    rows = [{"id": 1, "tier": 3, "realm_counties": 9}, {"id": 2, "tier": 3, "realm_counties": 5}]
    replies = {"search_characters": {"characters": rows},
               "query_realm": {"vassals": [{"id": 1, "military_strength": 500}, {"id": 2, "military_strength": 900}]}}
    chosen, rec = census.select_by_rank(lambda a, p: replies[a], 7, {"duke1": 1, "duke2": 2})
    assert chosen == {"duke1": 2, "duke2": 1}
    assert rec["ranked_rows"] == [{"id": 2, "military_strength": 900, "realm_counties": 5},
                                  {"id": 1, "military_strength": 500, "realm_counties": 9}]


#: Read live on ck3agent 2026.12.11: the first metric read after the seat's MCP
#: server binds its character can receive THIS frame on the control session: the
#: bind's result, broadcast without a request_id, which the toolkit's correlation
#: (`frame_matches`: no request_id and not a named push -> "match") hands back as
#: the reply. Taken as the answer, it makes the start metrics unreadable and the
#: first step truncate `reward_unreadable`.
FOREIGN_BIND_RESULT = {"caller_request_id": "deadbeef-cafe-4fad-beef-cafebabeface", "tick": 0,
                       "game_date": "1066.09.15", "flush_id": 0, "action": "session_control",
                       "verb": "set_controlled_character", "success": True, "type": "result"}


def test_a_reply_for_another_action_is_not_taken_as_this_calls_answer(tmp_path):
    b, br, _, _ = _backend(tmp_path)
    b.open(_scn(), seed=1)
    br.foreign = [dict(FOREIGN_BIND_RESULT)]
    m = b.metrics("king", ["gold", "realm_counties", "line_alive"])
    assert m.line_status == "following" and m.character_id == 37067
    assert m.values == {"gold": 123.5, "realm_counties": 84, "line_alive": True}
    assert b.foreign_frames == 1, "the dropped frame is counted, not silent"


def test_foreign_frames_alone_are_no_reply(tmp_path):
    b, br, _, _ = _backend(tmp_path, call_tries=2)
    b.open(_scn(), seed=1)
    br.foreign = [dict(FOREIGN_BIND_RESULT) for _ in range(10)]
    assert b._call("query_realm", {"character_id": 37067}) is None


def test_a_metric_the_bridge_does_not_give_is_unreadable_never_a_number(tmp_path):
    b, br, _, _ = _backend(tmp_path)
    b.open(_scn(), seed=1)
    br.realm.clear()
    br.watch = {"status": "watch_refused", "reason": "full"}
    m = b.metrics("king", ("realm_counties", "line_alive", "gold"))
    assert m.status == {"realm_counties": "unreadable", "line_alive": "unreadable", "gold": "unreadable"}
    assert m.values == {}


def test_an_ended_line_reads_zero_counties_and_not_alive(tmp_path):
    b, br, _, _ = _backend(tmp_path)
    b.open(_scn(), seed=1)
    br.watch = {"status": "line_ended", "line_character_id": 37067}
    m = b.metrics("king", ("realm_counties", "line_alive"))
    assert m.values == {"realm_counties": 0, "line_alive": False} and m.line_status == "line_ended"


def test_close_stops_the_seats_and_kills_only_the_game_it_launched(tmp_path):
    b, _, host, tr = _backend(tmp_path)
    b.open(_scn(), seed=1)
    b.close()
    assert host.killed == [PID] and tr["king"].closed
    b.close()
    assert host.killed == [PID]


class SlowDeathHost(FakeHost):
    """Read live: after SIGKILL the game's /proc/PID/exe
    stops resolving AT ONCE (its memory is being torn down), but /proc/PID stays
    for `linger` more reads and its bridge socket keeps answering until the
    process has closed its files."""

    def __init__(self, linger, **kw):
        super().__init__(**kw)
        self.linger = linger

    def kill(self, pid):
        self.killed.append(pid)

    def exe(self, pid):
        return None if pid in self.killed else super().exe(pid)

    def alive(self, pid):
        return self.exe(pid) is not None

    def exists(self, pid):
        if pid not in self.alive_pids:
            return False
        if pid in self.killed:
            if self.linger <= 0:
                self.alive_pids.discard(pid)
                return False
            self.linger -= 1
        return True


def test_close_waits_until_the_killed_game_has_left_proc_and_its_socket(tmp_path):
    """Read live: a launch 1 s after a close that waited only on /proc/PID/exe
    is refused `a bridge is already listening ... its process is not visible
    here`. close() waits, bounded, until /proc/PID is gone AND the socket stops
    answering."""
    host = SlowDeathHost(linger=5)
    b, *_ = _backend(tmp_path, host=host, socket_probe=lambda sock: host.exists(PID))
    b.open(_scn(), seed=1)
    b.close()
    assert host.killed == [PID] and not host.exists(PID)
    assert b.close_leftover is None


def test_close_waits_for_a_socket_that_outlives_the_process(tmp_path):
    host = FakeHost()
    answers = [True, True, True]
    b, *_ = _backend(tmp_path, host=host, socket_probe=lambda sock: bool(answers) and answers.pop())
    b.open(_scn(), seed=1)
    b.close()
    assert answers == [] and b.close_leftover is None


def test_close_does_not_hang_on_a_game_that_will_not_die(tmp_path):
    host = SlowDeathHost(linger=10 ** 9)
    b, *_ = _backend(tmp_path, host=host, exit_wait_s=30.0, socket_probe=lambda sock: True)
    b.open(_scn(), seed=1)
    t0 = host.t
    b.close()
    assert host.t - t0 <= 31.0 and b.close_leftover == PID


class StagingHost(FakeHost):
    """`ck3agent launch --save COPY` copies COPY into the game's own `save games/`
    (the toolkit's stage_save; a same-named file with the same content is kept as
    it is) and the game writes autosaves beside it while it runs."""

    def __init__(self, game_saves, autosave=True, **kw):
        super().__init__(**kw)
        self.game_saves, self.autosave = game_saves, autosave

    def run(self, argv, env, timeout):
        import shutil
        from pathlib import Path
        src = Path(argv[3])
        self.game_saves.mkdir(parents=True, exist_ok=True)
        dst = self.game_saves / src.name
        if not dst.exists():
            shutil.copy2(src, dst)
        if self.autosave:
            (self.game_saves / "autosave.ck3").write_bytes(b"AUTO")
        return super().run(argv, env, timeout)


def test_close_removes_the_save_copy_it_staged_in_the_game_dir_and_nothing_else(tmp_path):
    """The launch stages a copy of the start save in the toolkit's game dir.
    close() removes the copy the launch staged for THIS open, and leaves the
    game's own files (its autosaves) where they are."""
    gs = tmp_path / "game" / "save games"
    host = StagingHost(gs)
    b, *_ = _backend(tmp_path, host=host, game_saves_resolver=lambda inst: gs)
    b.open(_scn(), seed=1)
    assert (gs / "_1066_09_15.ck3").is_file()
    b.close()
    assert not (gs / "_1066_09_15.ck3").exists(), "the env's staged copy is removed"
    assert (gs / "autosave.ck3").read_bytes() == b"AUTO", "the game's autosave is left"
    assert b.close_removed == [str(gs / "_1066_09_15.ck3")]


def test_close_keeps_a_same_named_save_that_was_there_before_the_launch(tmp_path):
    gs = tmp_path / "game" / "save games"
    gs.mkdir(parents=True)
    (gs / "_1066_09_15.ck3").write_bytes(b"SAVE")          # not staged by this open
    b, *_ = _backend(tmp_path, host=StagingHost(gs, autosave=False), game_saves_resolver=lambda inst: gs)
    b.open(_scn(), seed=1)
    b.close()
    assert (gs / "_1066_09_15.ck3").read_bytes() == b"SAVE" and b.close_removed == []


def test_close_keeps_a_staged_name_whose_content_changed(tmp_path):
    gs = tmp_path / "game" / "save games"
    b, *_ = _backend(tmp_path, host=StagingHost(gs, autosave=False), game_saves_resolver=lambda inst: gs)
    b.open(_scn(), seed=1)
    (gs / "_1066_09_15.ck3").write_bytes(b"THE GAME SAVED OVER IT")
    b.close()
    assert (gs / "_1066_09_15.ck3").exists() and b.close_removed == []


def test_checkpoint_and_restore_are_refused_unsupported(tmp_path):
    b, *_ = _backend(tmp_path)
    for fn in (b.checkpoint, lambda: b.restore({})):
        with pytest.raises(EnvRefusal) as e:
            fn()
        assert e.value.code == R_CHECKPOINT_UNSUPPORTED


def test_the_environment_runs_a_whole_episode_over_the_real_backend_and_fakes(tmp_path):
    b, *_ = _backend(tmp_path, bridge=FakeBridge(speed=0, rate=7))
    env = ParallelEnv(_scn(), backend=b)
    got = contract.drive(env, 1, lambda env, obs: {"king": [ToolCall("observe")]})
    contract.check_horizon(env, got["steps"], got["last_infos"])
    assert got["steps"] == 10 and env.state()["game_date"] == env.scenario.end_date == "1068.09.15"
    env.close()


def test_channel_closed_is_found_nested_and_only_when_closed():
    assert _channel_closed({"a": [{"script_log_channel": {"state": "closed"}}]})
    assert not _channel_closed({"script_log_channel": {"state": "open"}})
    assert not _channel_closed({"script_log_channel": {"state": "unmeasured"}})


def test_a_step_reports_its_alarm_rearms_and_dropped_frames(tmp_path):
    """The advance's re-arms and the frames dropped as another session's
    reach the step record through Backend.advance_diagnostics."""
    from ck3bench.env.types import DIAG_REPORTED
    b, br, _, _ = _backend(tmp_path)
    b.open(_scn(), seed=1)
    at_open = b.foreign_frames
    adv = b.advance(30)
    b.foreign_frames += 2                    # two replies dropped during the step's reads
    d = b.advance_diagnostics(adv)
    assert d.status == DIAG_REPORTED and d.alarm_rearms == adv.extra["rearms"] and d.dropped_frames == 2
    assert b.advance_diagnostics(adv).dropped_frames == 0      # counted once, per step
    assert at_open == 0


# ── the step's game speed: declared beside read ──────────────────────────────────────────────────

def test_speed_witness_states():
    from ck3bench.env.types import speed_witness
    assert speed_witness(5, None)["status"] == "not_reported"
    assert speed_witness(5, [])["status"] == "unwitnessed"
    assert speed_witness(5, [None, None]) == {"declared": 5, "read": [], "reads": 0, "unread": 2,
                                               "status": "unwitnessed"}
    assert speed_witness(5, [5, 5, None]) == {"declared": 5, "read": [5], "reads": 2, "unread": 1,
                                               "status": "agreed"}
    assert speed_witness(5, [5, 3])["status"] == "mismatch"
    assert speed_witness(5, [3])["read"] == [3]
    assert speed_witness(3, [True])["status"] == "unwitnessed"        # a bool is not a speed


def test_a_declared_fast_speed_is_established_and_witnessed_each_step(tmp_path):
    b, br, host, _ = _backend(tmp_path)
    w = b.open(_scn(game_speed=5), seed=1)
    assert ("set_speed", {"speed": 5}) in br.calls
    assert host.env[ck3mod.GAME_SPEED_ENV] == "5"                      # declared to the bridge at launch
    assert w.provenance["game_speed"] == {"declared": 5, "read_at_open": 5}
    b.advance(30)
    got = b.clock_speeds()
    assert got and set(got) == {5}


def test_a_step_that_ran_at_another_speed_is_recorded_and_named(tmp_path):
    import io
    import json
    from ck3bench.env.trajectory import TrajectoryWriter, check_trajectory
    b, br, _, _ = _backend(tmp_path)
    buf = io.StringIO()
    env = ParallelEnv(_scn(game_speed=5), backend=b, trajectory=TrajectoryWriter(stream=buf))
    env.reset(seed=1)
    env.step({"king": [ToolCall("observe")]})
    br.speed = 3                                   # the world runs slower than declared
    env.step({"king": [ToolCall("observe")]})
    env.close()
    records = [json.loads(x) for x in buf.getvalue().splitlines() if x.strip()]
    steps = [r for r in records if r["kind"] == "step"]
    assert steps[0]["advance"]["speed"]["status"] == "agreed" and steps[0]["advance"]["speed"]["read"] == [5]
    assert steps[1]["advance"]["speed"] == {**steps[1]["advance"]["speed"], "declared": 5, "read": [3],
                                            "status": "mismatch"}
    problems = check_trajectory(records)
    assert any("ran at speed [3], declared 5" in x for x in problems), problems
