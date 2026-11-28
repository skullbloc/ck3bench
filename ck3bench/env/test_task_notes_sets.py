"""What the Lorraine task tells its seat about the wars that fail it.

The bridge's war-declaration ring (bridge/src/war_declaration_hook.cpp) records every new war by polling the war
database, however the game made it, with its primary attacker; offensive_wars_declared counts a row whose attacker
was a ruler of the line. A faction's war against its target is started by the faction (00_faction_effects.txt
faction_start_war) with its leader as attacker, so a faction the seat's ruler leads fails the goal; joining an
ally's war makes no new war and does not. The task's text said only "by your call or by the game's AI".
"""
from ck3bench.env.scenario import BUILTIN_DICTS
from ck3bench.env.types import METRIC_DEFINITIONS

LOR = "lorraine1066-line-25y-wake"


def test_the_lorraine_task_says_a_faction_he_leads_counts_and_an_ally_war_does_not():
    d = BUILTIN_DICTS[LOR]["description"]
    assert "a faction he leads" in d and "joining an ally's war does not" in d


def test_the_metric_definition_says_how_a_faction_war_is_counted():
    t = METRIC_DEFINITIONS["offensive_wars_declared"]
    assert "a faction's war counts when he leads the faction" in t


# ── the notes set each task declares (a task served another start's notes reads advice for the wrong seat) ──
import copy  # noqa: E402
import hashlib  # noqa: E402
import io  # noqa: E402
import json  # noqa: E402

from ck3bench.env.ck3 import NOTES_SET_ENV, Ck3Backend  # noqa: E402
from ck3bench.env.env import ParallelEnv  # noqa: E402
from ck3bench.env.scenario import scenario_from_dict  # noqa: E402
from ck3bench.env.test_holder_starts import FakeHost, NorseBridge, Seat, _save  # noqa: E402
from ck3bench.env.trajectory import TrajectoryWriter  # noqa: E402



def toolkit_notes() -> dict:
    """The importable toolkit's notes file (toolkit_source.notes_file); the test skips when there is none."""
    import pytest
    from ck3bench.env import toolkit_source
    f = toolkit_source.notes_file()
    if f is None:
        pytest.skip(toolkit_source.ABSENT)
    return json.loads(f.read_text("utf-8"))


DECLARED = {"francia867-empire-40y-wake": "francia867", "sweden867-kingdom-20y-wake": "sweden867",
            LOR: "lorraine1066", "munster1066-kingdom-once-30y-wake": "munster1066once",
            "carinthia1066-line-25y-wake": "carinthia1066",
            **{t: "ireland1066" for t in ("ireland1066-kingdom-15y", "ireland1066-kingdom-30y",
                                          "ireland1066-kingdom-30y-wake", "ireland1066-kingdom-30y-wake180",
                                          "munster1066-kingdom-30y-wake")},
            # the decision tasks: the Dublin seat from mid-game saves of the Ireland task
            **{t: "ireland1066" for t, d in BUILTIN_DICTS.items() if d.get("outcome")}}


def test_these_tasks_declare_a_notes_set_and_no_other_task_does():
    for tid, d in BUILTIN_DICTS.items():
        assert d.get("notes_set") == DECLARED.get(tid), tid
    assert scenario_from_dict(BUILTIN_DICTS[LOR]).notes_set == "lorraine1066"


def test_every_declared_set_is_in_the_toolkits_notes_file():
    assert set(DECLARED.values()) <= set(toolkit_notes()["sets"])


def _open(tmp_path, d):
    d = copy.deepcopy(d)
    d["start"]["save_sha256"] = hashlib.sha256(b"SAVE").hexdigest()
    sc = scenario_from_dict(d)
    seen = {}

    def tf(seat, cid, env):
        seen[seat] = dict(env)
        return Seat()
    b = Ck3Backend(_save(tmp_path, sc.start_save), host=FakeHost(), link_factory=lambda s, c, m: NorseBridge(),
                   socket_resolver=lambda i: "/run/fake.sock", transport_factory=tf, work_dir=tmp_path,
                   poll_s=1.0, retry_s=0.5, socket_probe=lambda s: False)
    buf = io.StringIO()
    ParallelEnv(sc, backend=b, trajectory=TrajectoryWriter(stream=buf)).reset(seed=1)
    return seen, json.loads(buf.getvalue().splitlines()[0])


def test_the_seat_server_is_given_the_declared_set_and_the_header_says_it(tmp_path, monkeypatch):
    monkeypatch.delenv(NOTES_SET_ENV, raising=False)
    seen, head = _open(tmp_path, BUILTIN_DICTS[LOR])
    assert seen["lorraine"][NOTES_SET_ENV] == "lorraine1066"
    assert head["provenance"]["notes_set_declared"] == "lorraine1066"


def test_CONTROL_a_task_with_no_set_never_passes_one_on(tmp_path, monkeypatch):
    """A stray CK3_NOTES_SET in the env's own environment does not reach a seat of a task that declares none."""
    monkeypatch.setenv(NOTES_SET_ENV, "francia867")
    d = copy.deepcopy(BUILTIN_DICTS[LOR])
    d.pop("notes_set")
    seen, head = _open(tmp_path, d)
    assert NOTES_SET_ENV not in seen["lorraine"] and head["provenance"]["notes_set_declared"] == "default"
