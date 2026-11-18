"""This repository is public: no file in it names a machine, a home directory
or a person's email address, nothing calls it private, and no file carries the
research tree's work-unit labels or its people and machines by role.

Every file of the checkout is read as bytes (walked from the tree, never a list
of names; an unreadable file is a finding, so the walk fails open). Two classes
of exception, both by rule and not by file name:

* a test module (`test_*.py`) may spell `/home/...`, `/mnt/...` and `/tmp/...`
  paths: they are the refusal fixtures of `experiment check`
  (`host_path_in_tracked_file`), which must be able to write the paths it refuses;
* an email address under `users.noreply.github.com` (the release bot) is not a
  person's address.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKIP_DIRS = {".git", "dist", "build", "__pycache__", ".pytest_cache", ".venv", ".ruff_cache"}

HOST_PATH = re.compile(rb"/home/[A-Za-z0-9_.-]+|/mnt/[A-Za-z0-9_.-]+|/run/user/\d+|/Users/[A-Za-z0-9_.-]+")
EMAIL = re.compile(rb"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
EMAIL_OK = re.compile(rb"@users\.noreply\.github\.com$")
# spelled in pieces so this file does not match itself
PRIVATE = re.compile(rb"\(pri" rb"vate\)|[Pp]ri" rb"vate for now|pri" rb"vate repo(sitory)?\b")
# The research tree's internal vocabulary: a work-unit label (a lane's name, a
# release lane's number, a bead id), and its people and machines named by role
# (its owner, its build node, its coordinator). Meaningless to a reader here.
INTERNAL = re.compile(rb"\bla" rb"ne [A-Za-z0-9]+|\bRE" rb"L[0-9]+\b|\bck-[a-z0-9]{4,}\b|"
                      rb"\b[Tt]he own" rb"er\b|\bown" rb"er's\b|\b[Tt]he no" rb"de\b|\borches" rb"trator\b")


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
    assert {"README.md", "pyproject.toml", "ck3bench/experiment.py", "tests/test_public_tree.py"} <= rels


def test_no_file_names_a_home_directory_or_a_mount_outside_the_refusal_fixtures():
    hits = _hits(HOST_PATH, keep=lambda rel, m: not Path(rel).name.startswith("test_"))
    assert hits == []


def test_the_refusal_fixtures_are_seen_so_the_exception_is_not_vacuous():
    assert any(Path(rel).name.startswith("test_") for rel, _, _ in _hits(HOST_PATH))


def test_no_file_carries_a_personal_email_address():
    hits = _hits(EMAIL, keep=lambda rel, m: not EMAIL_OK.search(m))
    assert hits == []


def test_nothing_calls_this_repository_private():
    assert _hits(PRIVATE) == []


def test_no_file_carries_the_research_trees_internal_labels():
    """UPSTREAM is the export's provenance record (it names the source commit and
    ref by design) and is not prose; every other file is read."""
    hits = _hits(INTERNAL, keep=lambda rel, m: rel != "UPSTREAM")
    assert hits == []


def test_the_internal_label_pattern_fires_on_each_kind():
    """CONTROL: the pattern is not vacuous; each kind, planted, is a hit."""
    for planted in (b"la" b"ne v4note", b"RE" b"L10", b"ck" b"-6kvq", b"the own" b"er", b"on the no" b"de",
                    b"the orches" b"trator"):
        assert INTERNAL.search(planted), planted
    for clean in (b"plane of play", b"RELEASE", b"ck3bench", b"owned", b"node.js"):
        assert not INTERNAL.search(clean), clean
