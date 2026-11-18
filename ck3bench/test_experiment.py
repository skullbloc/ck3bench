"""The experiment loader refuses by name, the block round-trips, the mirror
check catches one changed key, and host values stay out of tracked files."""
from __future__ import annotations

import copy
import json
import re
import tomllib
from pathlib import Path

import pytest

from ck3bench import experiment as X

EXAMPLES = Path(__file__).resolve().parent / "experiments"
NAMES = ("scale0", "budget0", "orgdesign-v4")
_BARE_KEY = re.compile(r"^[A-Za-z0-9_-]+$")


def _write_toml(path: Path, data: dict) -> None:
    """A small TOML writer for the shapes the tests mutate (no nested arrays
    of tables beyond [[passes]], no inline tables). A key that is not bare
    (`[pending]`'s "census.king_id") is written quoted, never as a dotted key."""
    out = []

    def key(k):
        return k if _BARE_KEY.match(k) else json.dumps(k)

    def scalar(v):
        if isinstance(v, bool):
            return "true" if v else "false"
        if isinstance(v, (int, float)):
            return repr(v)
        if isinstance(v, str):
            # a JSON string is a TOML basic string: the escapes json emits
            # (\" \\ \n \t \uXXXX ...) are TOML's, and with ensure_ascii off
            # no surrogate pair (which TOML refuses) is ever written
            return json.dumps(v, ensure_ascii=False)
        if isinstance(v, list):
            return "[" + ", ".join(scalar(x) for x in v) + "]"
        raise TypeError(v)

    def table(name, t):
        out.append(f"[{name}]")
        for k, v in t.items():
            if isinstance(v, dict):
                continue
            out.append(f"{key(k)} = {scalar(v)}")
        out.append("")
        for k, v in t.items():
            if isinstance(v, dict):
                table(f"{name}.{k}", v)

    for k, v in data.items():
        if isinstance(v, list) and v and isinstance(v[0], dict):
            for row in v:
                out.append(f"[[{k}]]")
                for kk, vv in row.items():
                    out.append(f"{key(kk)} = {scalar(vv)}")
                out.append("")
        elif not isinstance(v, dict):
            out.append(f"{key(k)} = {scalar(v)}")
    out.append("")
    for k, v in data.items():
        if isinstance(v, dict):
            table(k, v)
    path.write_text("\n".join(out), encoding="utf-8")


@pytest.fixture
def scale0_data():
    return tomllib.loads((EXAMPLES / "scale0.toml").read_text(encoding="utf-8"))


@pytest.fixture
def v4_data():
    return tomllib.loads((EXAMPLES / "orgdesign-v4.toml").read_text(encoding="utf-8"))


def _mutant(tmp_path, base):
    def _run(mutate):
        data = copy.deepcopy(base)
        mutate(data)
        p = tmp_path / "mut.toml"
        _write_toml(p, data)
        try:
            return X.load_experiment(p)
        except X.ExperimentRefusal as exc:
            return exc
    return _run


@pytest.fixture
def mutant(tmp_path, scale0_data):
    """Write a mutated scale0 and load it; returns the refusal or the Experiment."""
    return _mutant(tmp_path, scale0_data)


@pytest.fixture
def mutant4(tmp_path, v4_data):
    """The same over orgdesign-v4 (cells, roles, [pending], horizon_start)."""
    return _mutant(tmp_path, v4_data)


def test_the_writer_round_trips_scale0(tmp_path, scale0_data):
    p = tmp_path / "copy.toml"
    _write_toml(p, scale0_data)
    assert tomllib.loads(p.read_text()) == scale0_data


def test_the_writer_round_trips_orgdesign_v4_and_its_quoted_pending_keys(tmp_path, v4_data):
    p = tmp_path / "copy.toml"
    _write_toml(p, v4_data)
    assert tomllib.loads(p.read_text()) == v4_data
    assert '"cycle.game_days" = "pilot"' in p.read_text()
    assert X.load_experiment(p).block_values() == \
        X.load_experiment(EXAMPLES / "orgdesign-v4.toml").block_values()


def test_the_writer_round_trips_escapes_and_non_ascii(tmp_path):
    data = {"t": {"s": 'a "quoted" \\ back\\slash\n\ttab \x00nul', "u": "\u00e9\U0001f600 \u2028",
                  "l": ['x"y', "p\\q"]}}
    p = tmp_path / "esc.toml"
    _write_toml(p, data)
    assert tomllib.loads(p.read_text(encoding="utf-8")) == data


# ── the three examples ──────────────────────────────────────────────────────

@pytest.mark.parametrize("name", NAMES)
def test_each_example_loads_and_generates_a_block(name):
    exp = X.load_experiment(EXAMPLES / f"{name}.toml")
    assert exp.name == name
    assert exp.runner_host == "absent"
    block = X.to_conditions_block(exp)
    assert block.startswith("```registered-conditions\n") and block.endswith("```\n")
    assert X.block_disagreements(exp, block) == []
    X.check_block(exp, block)


@pytest.mark.parametrize("name", NAMES)
def test_block_round_trips_to_the_declared_values(name):
    exp = X.load_experiment(EXAMPLES / f"{name}.toml")
    scalars, lists = X.read_block(X.to_conditions_block(exp))
    want_s, want_l = exp.block_values()
    assert scalars == want_s
    assert lists == want_l
    assert lists["tool_surface.held_constant"] == exp.data["tool_surface"]["held_constant"]
    assert len(lists["tool_surface.held_constant"]) == 13


@pytest.mark.parametrize("name", NAMES)
def test_examples_carry_no_host_path(name):
    assert X.host_path_problems((EXAMPLES / f"{name}.toml").read_bytes()) == []


def test_orgdesign_v4_cells():
    exp = X.load_experiment(EXAMPLES / "orgdesign-v4.toml")
    cells = exp.cells
    assert tuple(cells) == X.BLOCK_TOPOLOGY_ARMS == ("e0", "k1", "council")
    assert not any(c["exploratory"] for c in cells.values())
    assert {n: (c["topology"], c["notepad"]) for n, c in cells.items()} == {
        "e0": ("none", "none"), "k1": ("none", "private"), "council": ("complete", "private,council")}
    assert {n: (c["king"], c["duke"]) for n, c in cells.items()} == {
        "e0": ("native", "native"), "k1": ("king", "native"), "council": ("king", "duke")}
    # blocks of three, equal N: E0 runs inside the blocks like the others, so no
    # arm declares its N until it is fixed at signing; unsteered episodes run
    # before it are a variance pilot, not E0's registered members
    assert not any("episodes" in c for c in cells.values())
    # a cell names no file: a steered seat boots its role's files from [guide]
    assert not any(k.endswith("_fragments") or k == "conditional_on" for c in cells.values() for k in c)
    assert exp.data["guide"]["manifest_sha256"] and exp.data["tool_surface"]["guide_arms_add"] == ["guide_read"]
    ep, seats = exp.data["episode"], exp.data["seats"]
    assert ep["horizon_start"] == "1066.09.15" and "horizon" not in ep and "horizon_game_years" not in ep
    assert (seats["rule"], seats["title_key"], seats["n_dukes"]) == ("named_title_v4", "k_france", 5)
    # the census run on the live start save; the cycle still pending the pilot
    assert "cycle_game_days" not in seats
    assert (seats["king_id"], seats["king_title_id"], seats["realm_counties"]) == (37067, 7052, 84)
    assert seats["duke_ids"] == [31808, 31466, 29249, 29247, 36151]
    assert exp.data["save"] == {"basename": "_1066_09_15.ck3",
                                "sha256": "0c94b59880e0549a5a0c8fbe4dc675a3de171f28d79f7628ad765116de782d5d"}
    assert seats["model_id"] == {"king": "claude-opus-5-5", "duke": "claude-sonnet-5-5"}
    assert "msg_send" not in exp.block_values()[1]["tool_surface.held_constant"]


def test_pilots_as_run():
    s = X.load_experiment(EXAMPLES / "scale0.toml").data
    b = X.load_experiment(EXAMPLES / "budget0.toml").data
    assert s["sweep"]["n"] == [2, 4, 6, 8, 12]
    assert s["seats"]["cycles"] == 3 and s["budget"]["cap_usd"] == 40
    assert len(s["seats"]["ids"]) == 12
    assert b["seats"]["ids"] == s["seats"]["ids"][:6]
    assert [(p["model"], p["episodes"]) for p in b["passes"]] == [("sonnet", 3), ("opus", 3)]
    assert b["seats"]["cycles"] == 18 and b["budget"]["cap_usd"] == 230
    assert b["seats"]["billing"]["accounts"] == ["api_key"]
    assert s["save"]["sha256"] == b["save"]["sha256"]


# ── refusals by name ────────────────────────────────────────────────────────

def test_unknown_key(mutant):
    r = mutant(lambda d: d["budget"].__setitem__("cap_eur", 1))
    assert r.reason == X.R_UNKNOWN_KEY and r.key == "budget.cap_eur"


def test_unknown_section(mutant):
    r = mutant(lambda d: d.__setitem__("board", {"x": 1}))
    assert r.reason == X.R_UNKNOWN_KEY and r.key == "board"


def test_missing_key_is_named(mutant):
    r = mutant(lambda d: d["save"].pop("sha256"))
    assert r.reason == X.R_MISSING_KEY and r.key == "save.sha256"


def test_missing_section_is_named(mutant):
    r = mutant(lambda d: d.pop("budget"))
    assert r.reason == X.R_MISSING_KEY and r.key == "budget"


def test_wrong_type_int_for_bool(mutant):
    r = mutant(lambda d: d["budget"].__setitem__("enforce", 0))
    assert r.reason == X.R_WRONG_TYPE and r.key == "budget.enforce"


def test_wrong_type_bool_is_not_int(mutant):
    r = mutant(lambda d: d["episode"].__setitem__("game_speed", True))
    assert r.reason == X.R_WRONG_TYPE and r.key == "episode.game_speed"


def test_wrong_type_int_is_not_float(mutant):
    r = mutant(lambda d: d["episode"].__setitem__("unavail_exclusion_fraction", 1))
    assert r.reason == X.R_WRONG_TYPE


def test_bad_date(mutant):
    r = mutant(lambda d: d["episode"].__setitem__("horizon", ["867.01.03", "tomorrow"]))
    assert r.reason == X.R_INVALID_VALUE and r.key == "episode.horizon"


def test_bad_sha(mutant):
    r = mutant(lambda d: d["save"].__setitem__("sha256", "ABC"))
    assert r.reason == X.R_INVALID_VALUE and r.key == "save.sha256"


def test_bad_choice(mutant):
    r = mutant(lambda d: d["episode"].__setitem__("mode", "observer"))
    assert r.reason == X.R_INVALID_VALUE and r.key == "episode.mode"


def test_bad_tool_name(mutant):
    r = mutant(lambda d: d["tool_surface"]["held_constant"].append("Msg-Send"))
    assert r.reason == X.R_INVALID_VALUE and r.key.startswith("tool_surface.held_constant[")


def test_empty_surface(mutant):
    r = mutant(lambda d: d["tool_surface"].__setitem__("held_constant", []))
    assert r.reason == X.R_INVALID_VALUE and r.key == "tool_surface.held_constant"


def test_list_source_needs_ids(mutant):
    r = mutant(lambda d: d["seats"].pop("ids"))
    assert r.reason == X.R_MISSING_KEY and r.key == "seats.ids"


def test_census_source_needs_rule_and_n(mutant):
    r = mutant(lambda d: d["seats"].update(source="census"))
    assert r.reason == X.R_MISSING_KEY and r.key == "seats.rule"


def test_cell_role_must_be_declared(mutant):
    def m(d):
        d["seats"]["model_id"] = {"duke": "claude-sonnet-5-5"}
        d["channel"] = {"max_text_bytes": 600, "quota_per_cycle": 2}
        d["cells"] = {"x": {"arm": "X", "topology": "complete", "episodes": 1, "king": "king",
                            "duke": "duke", "king_steered": True, "dukes_steered": True,
                            "exploratory": False}}
    r = mutant(m)
    assert r.reason == X.R_INVALID_VALUE and r.key == "cells.x.king"


def test_a_steered_role_needs_the_guide(mutant):
    """v4: a steered seat boots its role's files from [guide]; a cell with a
    steered role and no [guide] is refused naming it (a cell names no
    `*_fragments` list)."""
    def m(d):
        d["seats"]["model_id"] = {"king": "claude-opus-5-5"}
        d["cells"] = {"k1": {"arm": "K1", "topology": "none", "episodes": 1, "king": "king",
                             "duke": "native", "king_steered": True, "dukes_steered": False,
                             "exploratory": False}}
    r = mutant(m)
    assert r.reason == X.R_MISSING_KEY and r.key == "guide"


def test_a_cell_naming_a_fragment_list_is_an_unknown_key(mutant4):
    r = mutant4(lambda d: d["cells"]["k1"].update(king_fragments=["play/king.md"]))
    assert r.reason == X.R_UNKNOWN_KEY and r.key == "cells.k1.king_fragments"


def test_topology_needs_a_channel_table(mutant):
    def m(d):
        d["seats"]["model_id"] = {"king": "claude-opus-5-5", "duke": "claude-sonnet-5-5"}
        d["guide"] = {"manifest_sha256": "0" * 64}
        d["tool_surface"]["guide_arms_add"] = ["guide_read"]
        d["cells"] = {"f": {"arm": "F", "topology": "complete", "episodes": 1, "king": "king",
                            "duke": "duke", "king_steered": True, "dukes_steered": True,
                            "exploratory": False}}
    r = mutant(m)
    assert r.reason == X.R_MISSING_KEY and r.key == "channel"


@pytest.mark.parametrize("topology", ["star", "flat", ""])
def test_a_topology_v4_does_not_declare_is_refused(mutant4, topology):
    """v4: `complete` (COUNCIL) and `none` (E0, K1) are the only topologies."""
    r = mutant4(lambda d: d["cells"]["council"].update(topology=topology))
    assert r.reason == X.R_INVALID_VALUE and r.key == "cells.council.topology"


def test_a_pad_needs_its_bounds_and_its_tools(mutant4):
    r = mutant4(lambda d: d.pop("notepad"))
    assert r.reason == X.R_MISSING_KEY and r.key == "notepad"
    r = mutant4(lambda d: d["tool_surface"].pop("notepad_arms_add"))
    assert r.reason == X.R_MISSING_KEY and r.key == "tool_surface.notepad_arms_add"
    r = mutant4(lambda d: d["cells"]["k1"].update(notepad="council"))
    assert r.reason == X.R_INVALID_VALUE and r.key == "cells.k1.notepad"


def test_missing_file_and_invalid_toml(tmp_path):
    with pytest.raises(X.ExperimentRefusal) as e:
        X.load_experiment(tmp_path / "nope.toml")
    assert e.value.reason == X.R_FILE_MISSING
    p = tmp_path / "bad.toml"
    p.write_text("[experiment\nname = 1\n")
    with pytest.raises(X.ExperimentRefusal) as e:
        X.load_experiment(p)
    assert e.value.reason == X.R_TOML_INVALID


# ── host values ─────────────────────────────────────────────────────────────

def test_host_path_in_a_tracked_file_is_refused_even_in_a_comment(tmp_path, scale0_data):
    p = tmp_path / "scale0.toml"
    _write_toml(p, scale0_data)
    p.write_text(p.read_text() + "\n# the lock lives at /home/someone/ck3bench/NODE-LOCK\n")
    with pytest.raises(X.ExperimentRefusal) as e:
        X.load_experiment(p)
    assert e.value.reason == X.R_HOST_PATH_IN_TRACKED_FILE
    assert "/home/s" in str(e.value)


@pytest.mark.parametrize("path", ["/mnt/vol/saves", "/tmp/ck3-bench.sock", "/home/u"])
def test_every_host_root_is_refused_in_a_tracked_file(tmp_path, scale0_data, path):
    p = tmp_path / "scale0.toml"
    _write_toml(p, scale0_data)
    p.write_text(p.read_text() + f"\n# see {path}\n")
    with pytest.raises(X.ExperimentRefusal) as e:
        X.load_experiment(p)
    assert e.value.reason == X.R_HOST_PATH_IN_TRACKED_FILE


def test_a_bare_root_is_not_a_host_path():
    assert X.host_path_problems(b"# roots: /home/ /mnt/ /tmp/ and /var/lib/x\n") == []


def test_the_loader_names_no_host():
    """The product file carries no host: by its own rule, and by the shape of
    a name (user@host) the rule does not cover."""
    raw = Path(X.__file__).read_bytes()
    assert X.host_path_problems(raw) == []
    assert not any(b"@" in line and b"ssh" in line for line in raw.splitlines())


def test_hosts_forbidden_from_the_overlay_is_enforced(tmp_path, scale0_data):
    p = tmp_path / "scale0.toml"
    _write_toml(p, scale0_data)
    p.write_text(p.read_text() + "\n# ran on bench-node-7 as operator@bench-node-7\n")
    assert X.load_experiment(p).hosts_forbidden == ()        # no overlay: nothing declared
    (tmp_path / "scale0.local.toml").write_text(
        '[runner]\nhosts_forbidden = ["bench-node-7", "operator@"]\ndisplay = ":0"\n')
    with pytest.raises(X.ExperimentRefusal) as e:
        X.load_experiment(p)
    assert e.value.reason == X.R_HOST_PATH_IN_TRACKED_FILE
    assert "'bench-node-7'" in str(e.value)
    # the names are the overlay's; they are not runner values and not merged
    _write_toml(p, scale0_data)
    exp = X.load_experiment(p)
    assert exp.hosts_forbidden == ("bench-node-7", "operator@")
    assert "hosts_forbidden" not in exp.data["runner"] and exp.data["runner"]["display"] == ":0"
    assert X.check_report(exp)["hosts_forbidden"] == 2


def test_hosts_forbidden_in_a_tracked_file_is_refused(mutant):
    r = mutant(lambda d: d["runner"].__setitem__("hosts_forbidden", ["x"]))
    assert r.reason == X.R_HOST_KEY_IN_TRACKED_FILE and r.key == "runner.hosts_forbidden"


@pytest.mark.parametrize("text", ['hosts_forbidden = "x"', 'hosts_forbidden = [1]', 'hosts_forbidden = [""]'])
def test_hosts_forbidden_must_be_a_list_of_names(tmp_path, scale0_data, text):
    p = tmp_path / "scale0.toml"
    _write_toml(p, scale0_data)
    (tmp_path / "scale0.local.toml").write_text(f"[runner]\n{text}\n")
    with pytest.raises(X.ExperimentRefusal) as e:
        X.load_experiment(p)
    assert e.value.reason == X.R_WRONG_TYPE and e.value.key == "runner.hosts_forbidden"


def test_host_key_in_a_tracked_file_is_refused(mutant):
    r = mutant(lambda d: d["runner"].__setitem__("display", ":0"))
    assert r.reason == X.R_HOST_KEY_IN_TRACKED_FILE and r.key == "runner.display"


def test_overlay_supplies_host_keys(tmp_path, scale0_data):
    p = tmp_path / "scale0.toml"
    _write_toml(p, scale0_data)
    (tmp_path / "scale0.local.toml").write_text(
        '[runner]\nlock = "/home/someone/ck3bench/NODE-LOCK"\ndisplay = ":0"\nollama_unit = "ollama"\n')
    exp = X.load_experiment(p)
    assert exp.runner_host == "overlay"
    assert exp.data["runner"]["lock"].endswith("NODE-LOCK")
    assert exp.data["runner"]["memory_floor_gb"] == 20      # the tracked rules survive
    assert X.load_experiment(p, use_overlay=False).runner_host == "absent"


def test_overlay_may_carry_only_host_keys(tmp_path, scale0_data):
    p = tmp_path / "scale0.toml"
    _write_toml(p, scale0_data)
    (tmp_path / "scale0.local.toml").write_text('[runner]\nmemory_floor_gb = 1\n')
    with pytest.raises(X.ExperimentRefusal) as e:
        X.load_experiment(p)
    assert e.value.reason == X.R_OVERLAY_KEY_NOT_HOST and e.value.key == "runner.memory_floor_gb"
    (tmp_path / "scale0.local.toml").write_text('[budget]\ncap_usd = 1\n')
    with pytest.raises(X.ExperimentRefusal) as e:
        X.load_experiment(p)
    assert e.value.reason == X.R_OVERLAY_KEY_NOT_HOST and e.value.key == "budget"


def test_loading_the_overlay_itself_is_refused(tmp_path):
    p = tmp_path / "x.local.toml"
    p.write_text("[runner]\n")
    with pytest.raises(X.ExperimentRefusal):
        X.load_experiment(p)


def test_explicit_overlay_must_exist(tmp_path, scale0_data):
    p = tmp_path / "scale0.toml"
    _write_toml(p, scale0_data)
    with pytest.raises(X.ExperimentRefusal) as e:
        X.load_experiment(p, overlay=tmp_path / "missing.local.toml")
    assert e.value.reason == X.R_FILE_MISSING


# ── the mirror check ────────────────────────────────────────────────────────

def _flip(block: str, old: str, new: str) -> str:
    assert block.count(old) == 1
    return block.replace(old, new)


def test_mirror_catches_one_changed_scalar():
    exp = X.load_experiment(EXAMPLES / "scale0.toml")
    block = _flip(X.to_conditions_block(exp), "CK3_EPISODE_GAME_SPEED=2", "CK3_EPISODE_GAME_SPEED=3")
    with pytest.raises(X.ExperimentRefusal) as e:
        X.check_block(exp, block)
    assert e.value.reason == X.R_BLOCK_DISAGREES
    rows = e.value.extra["disagreements"]
    assert rows == [{"key": "CK3_EPISODE_GAME_SPEED", "declared": "2", "block": "3",
                     "verdict": "value_differs"}]


def test_mirror_catches_a_missing_line_and_an_extra_line():
    exp = X.load_experiment(EXAMPLES / "budget0.toml")
    block = _flip(X.to_conditions_block(exp), "CK3_STRICT_BUILD_ID=1\n", "")
    rows = X.block_disagreements(exp, block)
    assert rows == [{"key": "CK3_STRICT_BUILD_ID", "declared": "1", "block": "not_in_block",
                     "verdict": "missing_in_block"}]
    block = _flip(X.to_conditions_block(exp), "CK3_EPISODE_GAME_SPEED=2\n",
                  "CK3_EPISODE_GAME_SPEED=2\nCK3_EPISODE_FLUSH_PERIOD_MS=10000\n")
    rows = X.block_disagreements(exp, block)
    assert rows == [{"key": "CK3_EPISODE_FLUSH_PERIOD_MS", "declared": "not_declared",
                     "block": "10000", "verdict": "extra_in_block"}]


def test_mirror_catches_a_changed_list():
    exp = X.load_experiment(EXAMPLES / "orgdesign-v4.toml")
    block = _flip(X.to_conditions_block(exp), "tool_surface.held_constant=steer\n", "")
    rows = X.block_disagreements(exp, block)
    assert len(rows) == 1 and rows[0]["key"] == "tool_surface.held_constant"
    assert rows[0]["verdict"] == "list_differs"
    block = _flip(X.to_conditions_block(exp), "tool_surface.held_constant=steer\n",
                  "tool_surface.held_constant=steer\ntool_surface.held_constant=msg_send\n")
    assert X.block_disagreements(exp, block)[0]["verdict"] == "list_differs"


def test_mirror_catches_a_key_outside_the_vocabulary():
    exp = X.load_experiment(EXAMPLES / "orgdesign-v4.toml")
    block = _flip(X.to_conditions_block(exp), "registration.version=4\n",
                  "registration.version=4\nseat.model_id.pope=claude-opus-5-5\n")
    rows = X.block_disagreements(exp, block)
    assert rows == [{"key": "seat.model_id.pope", "declared": "not_a_generated_key",
                     "block": "claude-opus-5-5", "verdict": "extra_in_block"}]


def test_mirror_reads_the_document_not_the_prose():
    """A document with prose around the fence, comments and blank lines inside."""
    exp = X.load_experiment(EXAMPLES / "scale0.toml")
    doc = "# A document\n\nprose with CK3_EPISODE_GAME_SPEED=9 outside the fence\n\n" + \
          X.to_conditions_block(exp) + "\nmore prose\n"
    X.check_block(exp, doc)


def test_read_block_refusals():
    for text, reason in (("no fence", X.R_NO_BLOCK),
                         ("```registered-conditions\na=1\n", X.R_NO_BLOCK),
                         ("```registered-conditions\n```\n```registered-conditions\n```\n",
                          X.R_MULTIPLE_BLOCKS),
                         ("```registered-conditions\nnot a pair\n```\n", X.R_MALFORMED_LINE),
                         ("```registered-conditions\na=1\na=2\n```\n", X.R_DUPLICATE_KEY),
                         ("```registered-conditions\ntool_surface.held_constant=do\n"
                          "tool_surface.held_constant=do\n```\n", X.R_DUPLICATE_KEY)):
        with pytest.raises(X.ExperimentRefusal) as e:
            X.read_block(text)
        assert e.value.reason == reason, text


def test_generation_is_deterministic_and_the_check_reports_it(tmp_path):
    exp = X.load_experiment(EXAMPLES / "orgdesign-v4.toml")
    assert X.to_conditions_block(exp) == X.to_conditions_block(exp)
    rep = X.check_report(exp)
    assert rep["cells"] == 3 and rep["episodes"] == 0          # N equal per arm, fixed at signing: undeclared
    assert rep["episodes_undeclared"] == ["council", "e0", "k1"]
    assert rep["pending"] == dict(sorted(exp.pending.items())) and len(rep["pending"]) == 6
    assert "CK3_EPISODE_OBSERVER_CADENCE_MIN_DAYS" in rep["block_keys"]
    assert "CK3_EPISODE_FLUSH_PERIOD_MS" not in rep["block_keys"]
    assert "tool_surface.channel_arms_add" in rep["block_keys"]
    assert "tool_surface.notepad_arms_add" in rep["block_keys"]
    assert "seat.model_id.king" in rep["block_keys"] and "seat.model_id.duke" in rep["block_keys"]
    assert "census.king_id" in rep["block_keys"]               # written by the pilot's census
    assert "tool_surface.guide_arms_add" in rep["block_keys"] and "guide.manifest_sha256" in rep["block_keys"]
    assert rep["block_keys_not_emitted"] == ["seat.billing.key_file"]


# ── the v4 keys (pre-registration v4) ───────────────────────────────────────

def test_orgdesign_v4_block_carries_its_families():
    exp = X.load_experiment(EXAMPLES / "orgdesign-v4.toml")
    s, l = X.read_block(X.to_conditions_block(exp))
    assert s["registration.version"] == "4"
    assert [k for k in s if k.startswith("seat.model_id.")] == list(X.ROLE_BLOCK_KEYS.values())
    assert (s["seat.model_id.king"], s["seat.model_id.duke"]) == ("claude-opus-5-5", "claude-sonnet-5-5")
    assert [k for k in s if k.startswith("channel.topology.")] == \
        [f"channel.topology.{a}" for a in X.BLOCK_TOPOLOGY_ARMS]
    assert {a: s[f"channel.topology.{a}"] for a in X.BLOCK_TOPOLOGY_ARMS} == \
        {"e0": "none", "k1": "none", "council": "complete"}
    assert {a: s[f"notepad.pads.{a}"] for a in X.BLOCK_NOTEPAD_ARMS} == \
        {"e0": "none", "k1": "private", "council": "private,council"}
    assert (s["notepad.private_max_bytes"], s["notepad.council_max_bytes"],
            s["notepad.max_write_bytes"]) == ("4096", "8192", "1024")
    assert (s["census.rule"], s["census.title_key"], s["census.n_dukes"]) == \
        ("named_title_v4", "k_france", "5")
    assert not [k for k in s if k in ("census.min_dukes", "census.max_demesne_share")]
    assert (s["seat.billing.accounts"], s["seat.billing.account"]) == ("subscription,api_key",
                                                                      "subscription")
    assert "seat.billing.key_file" not in s                      # only under api_key
    assert (s["channel.max_text_bytes"], s["channel.quota_per_cycle"]) == ("600", "2")
    assert l["tool_surface.channel_arms_add"] == ["msg_send"]
    assert l["tool_surface.notepad_arms_add"] == ["note_read", "note_write"]
    # the horizon: its start declared, its end and its length pending the pilot
    assert s["CK3_EPISODE_HORIZON_START"] == "1066.09.15"
    # every pending line is its placeholder, and nothing else is a placeholder
    assert {k: v for k, v in s.items() if v.startswith("<")} == \
        {k: X.pending_placeholder(f) for k, f in exp.pending.items()}
    assert s["census.king_id"] == "37067" and s["cycle.game_days"] == "<pending_pilot>"
    assert s["run_order.seed"] == "<pending_freeze>"
    assert s["guide.manifest_sha256"] == exp.data["guide"]["manifest_sha256"]      # filled from the tree


def test_pilot_blocks_carry_their_account_and_no_v3_family_they_lack():
    for name in ("scale0", "budget0"):
        exp = X.load_experiment(EXAMPLES / f"{name}.toml")
        s, l = X.read_block(X.to_conditions_block(exp))
        assert s["registration.version"] == "2"
        assert s["seat.billing.account"] == exp.data["seats"]["billing"]["accounts"][0]
        assert not [k for k in s if k.startswith(("census.", "seat.model_id.", "channel.",
                                                   "cycle.", "run_order.", "notepad.",
                                                   "horizon.", "guide."))]
        assert "tool_surface.channel_arms_add" not in l and "tool_surface.notepad_arms_add" not in l
    b = X.load_experiment(EXAMPLES / "budget0.toml")
    assert X.read_block(X.to_conditions_block(b))[0]["seat.billing.key_file"] == \
        "~/.config/ck3agent/api_key"


def test_a_group_comment_is_written_only_above_a_group_that_emits_a_line():
    block = X.to_conditions_block(X.load_experiment(EXAMPLES / "scale0.toml"))
    assert "# -- the census" not in block and "# -- the channel" not in block
    assert "# -- the billing accounts" in block


def test_census_source_needs_the_named_title(mutant):
    r = mutant(lambda d: d["seats"].update(source="census", rule="named_title_v4", n_dukes=5))
    assert r.reason == X.R_MISSING_KEY and r.key == "seats.title_key"
    r = mutant(lambda d: d["seats"].update(source="census", rule="named_title_v4", n_dukes=5,
                                           title_key="k_france"))
    assert isinstance(r, X.Experiment)
    assert r.block_values()[0]["census.title_key"] == "k_france"


def test_census_ids_are_written_together_or_not_at_all(mutant):
    r = mutant(lambda d: d["seats"].update(king_id=100))
    assert r.reason == X.R_MISSING_KEY and r.key == "seats.king_title_id"
    r = mutant(lambda d: d["seats"].update(king_id=100, king_title_id=777, duke_ids=[1, 2]))
    assert r.reason == X.R_MISSING_KEY and r.key == "seats.realm_counties"     # v4: R is a census line
    r = mutant(lambda d: d["seats"].update(king_id=100, king_title_id=777, duke_ids=[1, 2],
                                           realm_counties=40))
    assert isinstance(r, X.Experiment)                           # no n_dukes to disagree with
    assert r.block_values()[0]["census.realm_counties"] == "40"
    r = mutant(lambda d: d["seats"].update(king_id=100, king_title_id=777, duke_ids=[1, 2],
                                           realm_counties=40, n_dukes=3))
    assert r.reason == X.R_INVALID_VALUE and r.key == "seats.duke_ids"
    r = mutant(lambda d: d["seats"].update(king_id=2, king_title_id=777, duke_ids=[1, 2],
                                           realm_counties=40))
    assert r.reason == X.R_INVALID_VALUE and r.key == "seats.king_id"


def test_the_episodes_account_must_be_one_the_block_allows(mutant):
    r = mutant(lambda d: d["seats"]["billing"].update(account="api_key"))
    assert r.reason == X.R_INVALID_VALUE and r.key == "seats.billing.account"
    r = mutant(lambda d: d["seats"]["billing"].update(accounts=["api_key"], account="api_key"))
    assert r.reason == X.R_MISSING_KEY and r.key == "seats.billing.key_file"
    r = mutant(lambda d: d["seats"]["billing"].update(accounts=["api_key"], account="api_key",
                                                      key_file="~/k"))
    assert isinstance(r, X.Experiment)
    assert r.block_values()[0]["seat.billing.key_file"] == "~/k"


def test_a_sha_without_a_document_is_refused(mutant):
    r = mutant(lambda d: d["registration"].update(sha256="a" * 64))
    assert r.reason == X.R_MISSING_KEY and r.key == "registration.document"


def test_the_registration_document_witness(tmp_path, mutant):
    exp = mutant(lambda d: None)
    row = X.registration_document_witness(exp, tmp_path)
    assert row["state"] == "not_declared" and row["sha256_observed"] == "unwitnessed"
    exp = mutant(lambda d: d["registration"].update(document="docs/reg.md"))
    row = X.registration_document_witness(exp, tmp_path)
    assert row["state"] == "registration_document_not_shipped" and row["looked_in"] == str(tmp_path)
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs/reg.md").write_bytes(b"# frozen\n")
    sha = X.hashlib.sha256(b"# frozen\n").hexdigest()
    assert X.registration_document_witness(exp, tmp_path)["state"] == "present_sha_not_declared"
    exp = mutant(lambda d: d["registration"].update(document="docs/reg.md", sha256=sha))
    row = X.registration_document_witness(exp, tmp_path)
    assert row["state"] == "present_sha_agrees" and row["sha256_observed"] == sha
    exp = mutant(lambda d: d["registration"].update(document="docs/reg.md", sha256="b" * 64))
    with pytest.raises(X.ExperimentRefusal) as e:
        X.registration_document_witness(exp, tmp_path)
    assert e.value.reason == X.R_INVALID_VALUE and e.value.key == "registration.sha256"
    with pytest.raises(X.ExperimentRefusal):
        X.check_report(exp, tree_root=tmp_path)


def test_the_toolkit_witness_reports_installed_beside_declared(monkeypatch):
    exp = X.load_experiment(EXAMPLES / "orgdesign-v4.toml")
    # the toolkit the registration pins; a release before it differs
    declared = "2026.12.10"
    assert exp.data["toolkit"]["ck3agent"] == declared
    for installed, state in ((None, "not_installed"), ("2026.12.9", "installed_differs"),
                             (declared, "installed_matches")):
        monkeypatch.setattr(X, "installed_toolkit_version", lambda v=installed: v)
        row = X.check_report(exp)["toolkit"]
        assert row == {"declared": declared,
                       "installed": installed if installed is not None else "not_installed",
                       "state": state}


def test_a_template_is_refused_before_its_fence_is_read():
    exp = X.load_experiment(EXAMPLES / "scale0.toml")
    doc = f"# {X.TEMPLATE_MARKER}\n\n" + X.to_conditions_block(exp)
    with pytest.raises(X.ExperimentRefusal) as e:
        X.read_block(doc)
    assert e.value.reason == X.R_TEMPLATE_NOT_FROZEN
    with pytest.raises(X.ExperimentRefusal) as e:
        X.check_block(exp, doc)
    assert e.value.reason == X.R_TEMPLATE_NOT_FROZEN
    X.check_block(exp, doc.replace(X.TEMPLATE_MARKER, "FROZEN"))     # the marker, and only it


def test_a_role_nothing_renders_is_unknown(mutant):
    r = mutant(lambda d: d["seats"].__setitem__("model_id", {"pope": "claude-opus-5-5"}))
    assert r.reason == X.R_UNKNOWN_KEY and r.key == "seats.model_id.pope"


def test_a_version_4_file_declares_the_channel(mutant):
    r = mutant(lambda d: d["registration"].update(version="4"))
    assert r.reason == X.R_MISSING_KEY and r.key == "channel"

    def m(d):
        d["registration"].update(version="4")
        d["channel"] = {"max_text_bytes": 600, "quota_per_cycle": 2}
    r = mutant(m)
    assert r.reason == X.R_MISSING_KEY and r.key == "tool_surface.channel_arms_add"


# ── [pending]: the lines the registration still owes ─────────────────────────

def test_a_pending_line_renders_its_placeholder_and_excuses_its_required_field(mutant):
    def m(d):
        d["save"].pop("sha256")
        d["pending"] = {"start_save.sha256": "save"}
    r = mutant(m)
    assert isinstance(r, X.Experiment) and r.pending == {"start_save.sha256": "save"}
    assert r.block_values()[0]["start_save.sha256"] == "<pending_save>"
    assert "start_save.sha256=<pending_save>\n" in X.to_conditions_block(r)
    # CONTROL, labelled: the same field absent and NOT pending is the missing key
    r = mutant(lambda d: d["save"].pop("sha256"))
    assert r.reason == X.R_MISSING_KEY and r.key == "save.sha256"


def test_a_pending_key_also_declared_is_refused(mutant):
    r = mutant(lambda d: d.__setitem__("pending", {"start_save.sha256": "save"}))
    assert r.reason == X.R_INVALID_VALUE and r.key == "pending.start_save.sha256"


def test_a_pending_key_also_declared_is_refused_in_orgdesign_v4(mutant4):
    r = mutant4(lambda d: d["seats"].update(cycle_game_days=90))
    assert r.reason == X.R_INVALID_VALUE and r.key == "pending.cycle.game_days"


@pytest.mark.parametrize("key", ["census.pope", "no_such_line", "tool_surface.held_constant"])
def test_an_unknown_pending_key_is_refused(mutant, key):
    """Not a block line, or a LIST line (a list is never a placeholder)."""
    r = mutant(lambda d: d.__setitem__("pending", {key: "pilot"}))
    assert r.reason == X.R_UNKNOWN_KEY and r.key == f"pending.{key}"


def test_a_pending_filler_outside_the_list_is_refused(mutant4):
    r = mutant4(lambda d: d["pending"].update({"run_order.seed": "owner"}))
    assert r.reason == X.R_INVALID_VALUE and r.key == "pending.run_order.seed"
    assert set(X.PENDING_FILLERS) == {"census", "pilot", "save", "freeze"}


def test_a_pending_table_that_is_not_a_table_is_refused(mutant):
    r = mutant(lambda d: d.__setitem__("pending", "census"))
    assert r.reason == X.R_WRONG_TYPE and r.key == "pending"


# ── the horizon: [start, end], or its start with the end pending; its length ─

def test_horizon_start_with_the_end_pending(mutant):
    def m(d):
        d["episode"].pop("horizon")
        d["episode"]["horizon_start"] = "867.01.03"
        d["pending"] = {"CK3_EPISODE_HORIZON_END": "pilot"}
    r = mutant(m)
    assert isinstance(r, X.Experiment)
    s = r.block_values()[0]
    assert (s["CK3_EPISODE_HORIZON_START"], s["CK3_EPISODE_HORIZON_END"]) == ("867.01.03", "<pending_pilot>")


def test_horizon_start_without_an_end_declared_or_pending_is_refused(mutant):
    def m(d):
        d["episode"].pop("horizon")
        d["episode"]["horizon_start"] = "867.01.03"
    r = mutant(m)
    assert r.reason == X.R_MISSING_KEY and r.key == "episode.horizon"
    r = mutant(lambda d: d["episode"].pop("horizon"))
    assert r.reason == X.R_MISSING_KEY and r.key == "episode.horizon"


def test_horizon_start_must_be_the_pairs_start(mutant):
    r = mutant(lambda d: d["episode"].update(horizon_start="867.01.03"))
    assert isinstance(r, X.Experiment)                           # the same start, said twice
    r = mutant(lambda d: d["episode"].update(horizon_start="867.01.04"))
    assert r.reason == X.R_INVALID_VALUE and r.key == "episode.horizon_start"


def test_horizon_game_years_is_the_pairs_length(mutant):
    """scale0's horizon is 867.01.03 -> 868.01.03: one game-year."""
    r = mutant(lambda d: d["episode"].update(horizon_game_years=1))
    assert isinstance(r, X.Experiment)
    assert r.block_values()[0]["horizon.game_years"] == "1"
    for bad in (2, 0):
        r = mutant(lambda d, v=bad: d["episode"].update(horizon_game_years=v))
        assert r.reason == X.R_INVALID_VALUE and r.key == "episode.horizon_game_years", bad
    r = mutant(lambda d: d["episode"].update(horizon=["867.01.03", "868.01.04"], horizon_game_years=1))
    assert r.reason == X.R_INVALID_VALUE and r.key == "episode.horizon_game_years"


def test_horizon_start_is_a_date(mutant):
    r = mutant(lambda d: d["episode"].update(horizon_start="soon"))
    assert r.reason == X.R_WRONG_TYPE and r.key == "episode.horizon_start"
