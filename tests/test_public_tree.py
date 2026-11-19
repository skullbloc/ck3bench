"""This repository is public and finished: no file in it names a machine, a home
directory or a person's email address, nothing calls it private, and no file
carries work-unit labels, run ids, unfinished-work markers or the story of how
something was found (a measured fact is stated with its conditions instead).

Every file of the checkout is read as bytes (walked from the tree, never a list
of names; an unreadable file is a finding, so the walk fails open). One
exception, by rule and not by file name: an email address under
`users.noreply.github.com` (the release bot) is not a person's address.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKIP_DIRS = {".git", "dist", "build", "__pycache__", ".pytest_cache", ".venv", ".ruff_cache"}

HOST_PATH = re.compile(rb"/home/[A-Za-z0-9_.-]+|/mnt/[A-Za-z0-9_.-]+|/run/user/\d+|/Users/[A-Za-z0-9_.-]+")
EMAIL = re.compile(rb"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
EMAIL_OK = re.compile(rb"@users\.noreply\.github\.com$")
# spelled in pieces so this file does not match itself
PRIVATE = re.compile(rb"\(pri" rb"vate\)|[Pp]ri" rb"vate for no" rb"w|pri" rb"vate repo(sitory)?\b")
# Internal vocabulary: a work-unit label (a work lane's name, a release number, a
# tracker id), and people and machines named by role (an owner, a build node, a
# coordinator). Meaningless to a reader here.
INTERNAL = re.compile(rb"\bla" rb"ne [A-Za-z0-9]+|\bRE" rb"L[0-9]+\b|\bck-[a-z0-9]{4,}\b|"
                      rb"\b[Tt]he own" rb"er\b|\bown" rb"er's\b|\b[Tt]he no" rb"de\b|\borches" rb"trator\b")

# Run labels used bare (a run's or a work unit's name, a finding's number: a
# letter and one or two digits) and paths of evidence that does not ship. A
# shipped comment says the fact instead ("read live on ck3agent 2026.12.11: ...").
RUN_LABEL = re.compile(rb"\b(?:LI" rb"VE|EN" rb"V|ENVLI" rb"VE|MET" rb"ER)[0-9]+\b|\b(?:VRE" rb"AD|BENCH" rb"PUB)\b|"
                       rb"\b[FTE][0-9]{1,2}\b|do" rb"cs/cleanup-20|/evi" rb"dence/|PL" rb"AN \xc2\xa7")

# Run ids of the games behind the shipped data (a version-and-label slug such as
# v<digit><letters>...) and the dash-numbered episode labels inside them.
RUN_ID = re.compile(rb"\bv[0-9][a-z][a-z0-9]+\b|-e[0-9]-s[0-9]\b")
# Unfinished-work markers.
WIP = re.compile(rb"\bTO" rb"DO\b|\bFIX" rb"ME\b|\bXX" rb"X\b|\bfor no" rb"w\b|\bnot ye" rb"t\b|"
                 rb"\bneeds[ _]wo" rb"rk\b|unpro" rb"ven\b")
# How something was found, told as a story, and records of a format or a tool
# that does not ship here.
HISTORY = re.compile(rb"\bfirst li" rb"ve\b|\bsecond li" rb"ve\b|\blive rou" rb"nd\b|"
                     rb"before this environment exi" rb"sted|\bresearch tr" rb"ee\b|\bsource tr" rb"ee\b|"
                     rb"[Rr]ead live \(20[0-9][0-9]-|\bwrite-u" rb"p\b|\bthe old refu" rb"sal\b|"
                     rb"trajectory/" rb"1\b|\bfree_cl" rb"ock\b|\bnot_in_bun" rb"dle\b|\bsealed bun" rb"dle")


def _files():
    out = []
    for p in sorted(ROOT.rglob("*")):
        rel = p.relative_to(ROOT)
        if any(part in SKIP_DIRS or part.endswith(".egg-info") for part in rel.parts):
            continue
        if p.is_file() and not p.name.startswith(".ck3bench-export"):
            out.append(p)
    return out


def _hits(rx, keep=lambda rel, m: True):
    hits = []
    for p in _files():
        rel = p.relative_to(ROOT).as_posix()
        try:
            data = p.read_bytes()
        except OSError as e:
            hits.append((rel, 0, f"unreadable: {e}"))
            continue
        for n, line in enumerate(data.split(b"\n"), 1):
            for m in rx.finditer(line):
                if keep(rel, m.group(0)):
                    hits.append((rel, n, m.group(0).decode("utf-8", "replace")))
    return hits


def test_the_walk_sees_the_tree():
    rels = {p.relative_to(ROOT).as_posix() for p in _files()}
    assert {"README.md", "pyproject.toml", "ck3bench/env/env.py", "ck3bench/env/baselines_france1066.json",
            "tests/test_public_tree.py"} <= rels


def test_no_file_names_a_home_directory_or_a_mount():
    assert _hits(HOST_PATH) == []


def test_the_host_path_pattern_fires():
    """CONTROL: the pattern is not vacuous (spelled in pieces so this file does not match itself)."""
    for planted in (b"/ho" b"me/someone/x", b"/m" b"nt/data/x", b"/run/us" b"er/1000/x", b"/Us" b"ers/me"):
        assert HOST_PATH.search(planted), planted
    assert not HOST_PATH.search(b"/tmp/x") and not HOST_PATH.search(b"home/x")


def test_no_file_carries_a_personal_email_address():
    hits = _hits(EMAIL, keep=lambda rel, m: not EMAIL_OK.search(m))
    assert hits == []


def test_nothing_calls_this_repository_private():
    assert _hits(PRIVATE) == []


def test_no_file_carries_an_internal_label():
    """UPSTREAM is the export's provenance record (it names the source commit and
    ref by design) and is not prose; every other file is read."""
    hits = _hits(INTERNAL, keep=lambda rel, m: rel != "UPSTREAM")
    assert hits == []


def test_the_internal_label_pattern_fires_on_each_kind():
    """CONTROL: the pattern is not vacuous; each kind, planted, is a hit."""
    for planted in (b"la" b"ne v" b"4note", b"RE" b"L10", b"ck" b"-6kvq", b"the own" b"er", b"on the no" b"de",
                    b"the orches" b"trator"):
        assert INTERNAL.search(planted), planted
    for clean in (b"plane of play", b"RELEASE", b"ck3bench", b"owned", b"node.js"):
        assert not INTERNAL.search(clean), clean


def test_no_file_carries_a_run_label_or_an_evidence_path():
    """UPSTREAM names the source commit by design and is not prose."""
    assert _hits(RUN_LABEL, keep=lambda rel, m: rel != "UPSTREAM") == []


def test_the_run_label_pattern_fires_on_each_kind():
    """CONTROL: each kind, planted, is a hit; ordinary words are not."""
    for planted in (b"LI" b"VE0", b"(LI" b"VE1)", b"EN" b"V4's patch", b"F" b"7 (fixed)", b"T" b"6", b"E" b"10",
                    b"VRE" b"AD", b"MET" b"ER13", b"do" b"cs/cleanup-2026.11/x", b"x/evi" b"dence/y",
                    b"PL" b"AN \xc2\xa7K"):
        assert RUN_LABEL.search(planted), planted
    for clean in (b"live", b"GAME_SPEED_" b"ENV", b"ENVIRONMENT", b"F-16", b"T-shirt", b"E2E", b"evidence",
                  b"step 7", b"Live", b"RUN ON A LIVE GAME", b"ENV vars"):
        assert not RUN_LABEL.search(clean), clean


def test_no_file_carries_a_run_id_a_wip_marker_or_a_history():
    """UPSTREAM names the source commit by design and is not prose."""
    hits = [h for rx in (RUN_ID, WIP, HISTORY) for h in _hits(rx, keep=lambda rel, m: rel != "UPSTREAM")]
    assert hits == []


def test_the_run_id_wip_and_history_patterns_fire_on_each_kind():
    """CONTROL: each kind, planted, is a hit; ordinary words are not."""
    for rx, planted in ((RUN_ID, b"v" b"4e0var"), (RUN_ID, b"x-e" b"0-s1"), (WIP, b"TO" b"DO: x"),
                        (WIP, b"FIX" b"ME"), (WIP, b"for no" b"w"), (WIP, b"not ye" b"t run"),
                        (WIP, b"needs_wo" b"rk"), (WIP, b"needs wo" b"rk"), (WIP, b"checkpoint_unpro" b"ven"),
                        (HISTORY, b"the first li" b"ve hold episode"), (HISTORY, b"the second li" b"ve round"),
                        (HISTORY, b"run before this environment exi" b"sted"), (HISTORY, b"the research tr" b"ee"),
                        (HISTORY, b"a larger source tr" b"ee"), (HISTORY, b"Read live (2026" b"-11-19)"),
                        (HISTORY, b"the set's own write-u" b"p"), (HISTORY, b"ck3bench.trajectory/" b"1"),
                        (HISTORY, b"phase free_cl" b"ock"), (HISTORY, b"a sealed bun" b"dle")):
        assert rx.search(planted), planted
    for rx, clean in ((RUN_ID, b"v2026.12.11"), (RUN_ID, b"v7.0.1"), (RUN_ID, b"game-1"), (WIP, b"nowhere"),
                      (WIP, b"mods_not_wired"), (WIP, b"proven"), (HISTORY, b"read live on ck3agent 2026.12.11"),
                      (HISTORY, b"ck3bench.trajectory/2"), (HISTORY, b"trajectory/10"), (HISTORY, b"first line")):
        assert not rx.search(clean), clean
