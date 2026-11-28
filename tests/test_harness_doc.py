"""docs/harness.md states what the harness's code reads and writes, and the shipped text names its own package.

* Every environment variable the harness's code names is a row of the doc's "Environment variables" table. The
  names are enumerated from the code (every all-capitals string constant in every non-test module of
  ck3bench/harness, by AST), never from a list of names here, so a new variable reds this test until the doc has
  it. A short list of reviewed constants that are not variables (verdict words) is exempt, and each exemption must
  still occur in the code.
* Every run-directory file name the harness's code names is a row of the doc's "The run directory" table.
* Every module docstring of the package, and every help and usage text the CLIs print, names the package and
  carries none of the shapes a text loses when a word is cut out of it (a module path that lost
  its package after `-m` or `from`, empty backticks). A control plants each shape and proves the check fires.
"""
import ast
import re
import subprocess
import sys

from test_public_tree import ROOT

DOC = ROOT / "docs" / "harness.md"
HARNESS = ROOT / "ck3bench" / "harness"
VAR = re.compile(r"^[A-Z][A-Z0-9_]{2,}$")
#: all-capitals string constants of the harness that are not environment variables (reviewed)
NOT_VARIABLES = {"DIFFERS", "DISAGREES", "END", "FREEZE", "HEAD", "IDENTICAL", "III", "REFUSE"}
#: the user and host variables `series publish` removes the values of where a copy carries them (publish.py
#: IDENTITY_ENV_KEYS): names it reads in the files it rewrites, never from the environment (reviewed)
NOT_VARIABLES |= {"USER", "LOGNAME", "USERNAME", "SUDO_USER", "HOSTNAME", "HOST", "COMPUTERNAME", "USERDOMAIN"}
FILE = re.compile(r"^[A-Za-z0-9_.-]+\.(?:json|jsonl|tsv|txt|log|done|out|list|sha|before|md)$|^done$|^cwd$")
#: file-name constants of the harness that are not run-directory files (reviewed): the prompt template's name, and
#: the scorer's override log, which is written beside the run directories
#: names the code holds that are no run directory's file: ".damaged.json" is the suffix of the stub `series publish`
#: writes into its DEST (documented in the table as `<file>.damaged.json`)
NOT_RUN_FILES = {"seat_prompt.md", "score_rate_overrides.log", ".damaged.json"}
DAMAGE = re.compile(r"(?:-m|from)\.(?:env|harness)\b|\(\.(?:env|harness)\.|(?<!`)`" r"`(?!`)|python -m\s+\.")


def _code_files():
    return sorted(p for p in HARNESS.rglob("*.py") if not p.name.startswith("test_") and "__pycache__" not in p.parts)


def _constants(pred) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for p in _code_files():
        for n in ast.walk(ast.parse(p.read_text(encoding="utf-8"))):
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and pred(n.value):
                out.setdefault(n.value, set()).add(p.name)
    return out


def _section(title: str) -> str:
    text = DOC.read_text(encoding="utf-8")
    i = text.index(f"\n## {title}\n")
    j = text.find("\n## ", i + 1)
    return text[i:j if j > 0 else None]


def _first_cells(section: str) -> set[str]:
    names = set()
    for ln in section.splitlines():
        if ln.startswith("| `"):
            names |= set(re.findall(r"`([^`]+)`", ln.split(" | ")[0]))
    return names


def _covered(name: str, documented: set[str]) -> bool:
    return name in documented or any(d.endswith("*") and name.startswith(d[:-1]) for d in documented) or \
        any(d.endswith("_*") and name == d[:-1] for d in documented)


def test_every_environment_variable_the_code_names_is_documented():
    names = _constants(lambda v: bool(VAR.match(v)))
    assert len(names) > 40, "the enumeration found almost nothing: it is not reading the code"
    stale = NOT_VARIABLES - set(names)
    assert not stale, f"reviewed exemptions no longer in the code: {sorted(stale)}"
    documented = _first_cells(_section("Environment variables"))
    missing = sorted(n for n in names if n not in NOT_VARIABLES and not _covered(n, documented))
    assert missing == [], f"read by the code, not in docs/harness.md: {[(m, sorted(names[m])) for m in missing]}"


def test_every_run_directory_file_the_code_names_is_documented():
    names = _constants(lambda v: bool(FILE.match(v)))
    assert {"pressure.tsv", "rate_run.done", "run.json", "post.tsv", "ck3.before"} <= set(names)
    documented = _first_cells(_section("The run directory"))
    documented |= {f"{d}{x}" for d in ("own.before", "own.after") for x in ("", ".txt", ".list", ".sha")}
    assert not NOT_RUN_FILES - set(names), "a reviewed exemption is no longer in the code"
    missing = sorted(n for n in names if n not in NOT_RUN_FILES and n not in documented and n.rstrip("/") not in
                     {d.rstrip("/") for d in documented})
    assert missing == [], missing


def test_CONTROL_a_variable_the_doc_lacks_is_found():
    """The enumeration and the match fire: a planted module's new variable is not covered."""
    tree = ast.parse('import os\nx = os.environ.get("RATE_PLANTED_NEW")\n')
    planted = {n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)
               and VAR.match(n.value)}
    documented = _first_cells(_section("Environment variables"))
    assert planted == {"RATE_PLANTED_NEW"} and not _covered("RATE_PLANTED_NEW", documented)
    assert _covered("CK3_BRIDGE_ARMY_ORDERS", documented) and _covered("RATE_RUNS", documented)


def _docstrings():
    for p in sorted((ROOT / "ck3bench").rglob("*.py")):
        if "__pycache__" in p.parts:
            continue
        d = ast.get_docstring(ast.parse(p.read_text(encoding="utf-8")), clean=False)
        if d:
            yield p.relative_to(ROOT).as_posix(), d


def test_no_module_docstring_carries_a_cut_word_shape():
    bad = [(rel, m.group(0)) for rel, d in _docstrings() for m in DAMAGE.finditer(d)]
    assert bad == []


def test_every_module_docstring_that_shows_a_command_names_the_package():
    bad = []
    for rel, d in _docstrings():
        for m in re.finditer(r"python3? -m (\S+)", d):
            if not m.group(1).startswith(("ck3bench", "pytest")):
                bad.append((rel, m.group(0)))
    assert bad == []


HELP = [["-m", "ck3bench.harness"], ["-m", "ck3bench.harness", "series"], ["-m", "ck3bench.harness", "score"],
        ["-m", "ck3bench.harness", "run-seat", "--help"], ["-m", "ck3bench.harness", "series", "run", "--help"],
        ["-m", "ck3bench.cli", "--help"], ["-m", "ck3bench.cli", "env", "--help"]]


def test_every_help_text_names_the_package_whole():
    for args in HELP:
        r = subprocess.run([sys.executable, *args], capture_output=True, text=True, timeout=120, cwd=ROOT)
        text = r.stdout + r.stderr
        assert "ck3bench" in text, (args, text[:300])
        assert not DAMAGE.search(text), (args, DAMAGE.search(text).group(0))


def test_CONTROL_each_cut_word_shape_is_found():
    for planted in ("python -m" ".harness VERB", "from" ".env import ParallelEnv", "The `" "` command.", "(" ".env.scenario)",
                    "python -m .harness"):
        assert DAMAGE.search(planted), planted
    for clean in ("python -m ck3bench.harness", "from ck3bench.env import x", "```bash", "`ck3bench` command"):
        assert not DAMAGE.search(clean), clean


# ── what the docs say about the harness's verbs, its reference driver and its briefings ──────────────────────
CHANGELOG = ROOT / "CHANGELOG.md"
README = ROOT / "README.md"
#: the reference driver's runs, as the docs state them: on the real game, or the stand-in backend alone
DRIVER_LIVE = re.compile(r"(?i)two-attempt series of the Munster task paid once on\s+the\s+real game")
DRIVER_STANDIN_ONLY = re.compile(r"(?i)stand-in\s+backend\s+only|no live series")


def _words(p) -> str:
    return " ".join(p.read_text(encoding="utf-8").split())


def driver_runs_disagree(changelog: str, harness: str) -> list[str]:
    """Where the CHANGELOG and docs/harness.md disagree on where the reference driver has run ([] when they agree)."""
    out = []
    for name, text in (("CHANGELOG.md", changelog), ("docs/harness.md", harness)):
        if DRIVER_STANDIN_ONLY.search(text):
            out.append(f"{name} says the driver ran on the stand-in backend only")
    if bool(DRIVER_LIVE.search(changelog)) != bool(DRIVER_LIVE.search(harness)):
        out.append("one of CHANGELOG.md and docs/harness.md says the driver played a series on the real game, the "
                   "other does not")
    return out


def test_the_changelog_and_the_doc_agree_where_the_reference_driver_ran():
    assert driver_runs_disagree(_words(CHANGELOG), _words(DOC)) == []


def test_CONTROL_a_stand_in_only_claim_beside_a_live_one_is_found():
    live = "it played a two-attempt series of the Munster task paid once on the real game"
    only = "exercised on the stand-in backend only"
    assert driver_runs_disagree(only, live) and driver_runs_disagree(live, "the stand-in backend")
    assert driver_runs_disagree(live, live) == [] and driver_runs_disagree("a driver", "a driver") == []


def _verbs() -> list[str]:
    import ck3bench.harness.__main__ as M
    # a verb line is `VERB ARG  what it does` (the docstring is indented or not, by Python version)
    return [m.group(1) for ln in M.__doc__.splitlines()[1:] if (m := re.match(r"^\s*([a-z][a-z-]+) +[A-Z][A-Z_]*\b", ln))]


def verbs_missing(doc: str, verbs: list[str]) -> list[str]:
    return [v for v in verbs if f"python -m ck3bench.harness {v} " not in doc]


def test_every_verb_the_entry_point_offers_is_in_the_doc_s_verb_list():
    verbs = _verbs()
    assert len(verbs) >= 8 and "game-pid" in verbs, verbs
    assert verbs_missing(DOC.read_text(encoding="utf-8"), verbs) == []


def test_CONTROL_a_verb_missing_from_the_doc_is_found():
    text = DOC.read_text(encoding="utf-8").replace("python -m ck3bench.harness game-pid ", "")
    assert verbs_missing(text, _verbs()) == ["game-pid"]


def test_the_tasks_the_runner_cannot_brief_are_the_ones_the_docs_name():
    """`run-seat` sends only a task with a briefing. The tasks without one are computed from the prompt code, and
    must be exactly the France and fake tasks the README and docs/harness.md name."""
    from ck3bench.env.scenario import BUILTIN_DICTS, resolve
    from ck3bench.harness import prompt as P
    refused = set()
    for k in BUILTIN_DICTS:
        sc = resolve(k)
        try:
            P.render_prompt(sc, sc.n_steps)
        except SystemExit:
            refused.add(k)
    assert refused == {k for k in BUILTIN_DICTS if k.startswith(("france1066-", "fake-"))}, sorted(refused)
    assert "The four `france1066-*` tasks and the fake tasks have none" in _words(DOC)
    assert "the four `france1066-*` tasks have no briefing" in _words(README)


# ── the lock, as the doc states it ───────────────────────────────────────────
def lock_doc_problems(doc: str, lock_follows_runs: bool) -> list[str]:
    """Where docs/harness.md misstates the driver's lock ([] when it does not): the RATE_RUNS row says it holds the
    lock while the default lock does not follow RATE_RUNS, or the waits it states are not the driver's."""
    from ck3bench.harness import driver
    out = []
    row = next((ln for ln in doc.splitlines() if ln.startswith("| `RATE_RUNS` |")), "")
    says_lock = re.search(r"\bledger and (?:the )?lock\b|\band (?:the )?lock\b", row)
    if says_lock and not lock_follows_runs:
        out.append(f"the RATE_RUNS row says it holds the lock ({says_lock.group(0)!r}); the default lock does not "
                   "follow RATE_RUNS")
    flat = " ".join(doc.split())
    for want in (f"up to {driver.GAME_EXIT_WAIT_S:g} s", f"KILL {driver.TERM_WAIT_S:g} s later"):
        if want not in flat:
            out.append(f"the doc does not state the driver's wait: {want!r}")
    return out


def test_the_doc_states_the_lock_as_the_driver_takes_it(monkeypatch, tmp_path):
    from ck3bench.harness import driver
    monkeypatch.setenv("RATE_RUNS", str(tmp_path / "runs"))
    follows = str(tmp_path) in str(driver.default_lock_dir())
    assert lock_doc_problems(DOC.read_text(encoding="utf-8"), follows) == []


def test_CONTROL_a_stale_lock_row_or_wait_is_found():
    doc = DOC.read_text(encoding="utf-8")
    stale = re.sub(r"(\| `RATE_RUNS` \|[^\n]*?)default ledger[^|]*", r"\1default ledger and lock ", doc, count=1)
    assert any("RATE_RUNS row" in p for p in lock_doc_problems(stale, False))
    assert lock_doc_problems(stale, True) == []                 # non-discriminating half: a lock that did follow
    assert any("wait" in p for p in lock_doc_problems(doc.replace("up to 900 s", "up to 5 s"), False))
