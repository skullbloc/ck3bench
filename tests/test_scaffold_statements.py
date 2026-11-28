"""What the README says about the scaffold is read from the code, not kept by hand.

* The README's notes table (section "Scaffold") is generated here from the task definitions and the toolkit's
  notes file: which tasks are served which notes set, how many notes it has, which of them are scoped to one start,
  and which title keys its notes and its turn-one notes name (a turn-one note is in every seat's first observation).
  The README's table must equal the generated one.
* Every task that records an outcome its seat prompt does not tell ("Nothing else is scored") is disclosed in the
  README and docs/harness.md, and each decision task's README row says what the declarations say of reaching the
  goal a series of it scores.
The build the bench is pinned to is checked in test_build_pins.py. Controls plant each claim and prove the check
fires.
"""
import importlib.util
import json
import re
import sys

import pytest

from test_public_tree import ROOT
from ck3bench.env import toolkit_source
from ck3bench.env.scenario import BUILTIN_DICTS, resolve
from ck3bench.harness import prompt as P

README = ROOT / "README.md"
DOC = ROOT / "docs" / "harness.md"
#: the scopes of notes that hold for any start
GENERAL = {"any start", "any", "any AI-run ruler", "a feudal count", "seats whose turns end with a notes pad"}
TITLE_KEY = re.compile(r"\b[ek]_[a-z]+\b")


def _notes_module():
    d = toolkit_source.server_dir()
    spec = importlib.util.spec_from_file_location("_toolkit_notes", d / "notes.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod                # a dataclass in it looks its module up
    spec.loader.exec_module(mod)
    return mod


def _needs_toolkit():
    if toolkit_source.notes_file() is None:
        pytest.skip(toolkit_source.ABSENT)


def _scope(note) -> str:
    return re.sub(r"^CK3 [\d.]+;\s*", "", note.get("scope") or "")


def coaching_table() -> str:
    """The README's notes table, from the task definitions and the toolkit's notes file."""
    nm = _notes_module()
    doc = json.loads(toolkit_source.notes_file().read_text(encoding="utf-8"))
    groups: dict = {}
    for k in BUILTIN_DICTS:
        sc = resolve(k)
        key = "none" if sc.backend != "ck3" else (sc.notes_set or "the default set")
        groups.setdefault(key, []).append(k)
    rows = ["| tasks | notes set | notes | scoped to one start | title keys the notes name | in a turn-one note |",
            "|---|---|---|---|---|---|"]
    order = ["the default set"] + sorted(k for k in groups if k not in ("the default set", "none")) + ["none"]
    for key in order:
        tasks = ", ".join(f"`{t}`" for t in sorted(groups[key]))
        if key == "none":
            rows.append(f"| {tasks} | none (the fake backend has no toolkit) | 0 | - | - | - |")
            continue
        served = nm.derive(doc, None if key == "the default set" else key)
        notes = [n for n in served["notes"] if isinstance(n, dict)]
        scoped = sorted({_scope(n) for n in notes if _scope(n) not in GENERAL})
        n_scoped = sum(_scope(n) not in GENERAL for n in notes)
        keys = sorted({m for n in notes for m in TITLE_KEY.findall(n.get("text") or "")})
        one = sorted({m for t in nm.turn_one_texts(served) for m in TITLE_KEY.findall(t)})
        name = key if key == "the default set" else f"`{key}`"
        rows.append(f"| {tasks} | {name} | {len(notes)} | {n_scoped}" + (f" ({'; '.join(scoped)})" if scoped else "")
                    + f" | {', '.join(f'`{x}`' for x in keys) or 'none'} | {', '.join(f'`{x}`' for x in one) or 'none'} |")
    return "\n".join(rows)


def _readme_table() -> str:
    text = README.read_text(encoding="utf-8")
    i = text.index("| tasks | notes set | notes |")
    j = text.index("\n\n", i)
    return text[i:j]


def test_the_readme_notes_table_is_the_generated_one():
    _needs_toolkit()
    assert _readme_table() == coaching_table(), "regenerate the README's notes table:\n" + coaching_table()


def test_CONTROL_the_generated_table_sees_a_title_key_in_a_turn_one_note():
    """The Ireland set's turn-one notes carry a kingdom's creation call: the generated table names it in the last
    column, so a README that omits it is red; the default set's name none."""
    _needs_toolkit()
    rows = coaching_table().splitlines()
    ire = [r for r in rows if "| `ireland1066` |" in r]
    assert ire and ire[0].rstrip(" |").split("|")[-1].strip() != "none", ire
    default = [r for r in rows if "| the default set |" in r]
    assert default and default[0].rstrip(" |").split("|")[-1].strip() == "none", default


# ── the decision tasks' outcome: recorded, not paid, and not told to the seat ──────────────────────────────────
#: the prompt's one sentence about scoring, and the disclosure each doc must carry when a task's outcome is not told
NOTHING_ELSE = "Nothing else is scored"
DISCLOSURE = {README: "The decision tasks' outcome is recorded, not paid, and the seat is not told of it.",
              DOC: "The seat is not told about it"}


def untold_outcomes(texts: dict, tasks: dict) -> list[str]:
    """For every task that declares an `outcome` while its seat prompt says nothing else is scored: each doc must
    carry the disclosure and name the task in the paragraph that carries it ([] when every doc does)."""
    out = []
    for name, prompt in sorted(tasks.items()):
        if NOTHING_ELSE not in prompt:
            continue
        for path, marker in DISCLOSURE.items():
            block = " ".join(_paragraph(texts[path], marker).split())
            if not block:
                out.append(f"{path.name}: {name} declares an outcome its seat is not told of, and the doc does not say so")
            elif f"`{name}`" not in block:
                out.append(f"{path.name}: the disclosure does not name {name}")
    return out


def _paragraph(text: str, marker: str) -> str:
    """The paragraph of `text` that carries `marker` (whitespace in the marker may be a line break in the text)."""
    flat = re.escape(marker).replace(r"\ ", r"\s+")
    m = re.search(flat, text)
    if not m:
        return ""
    i = text.rfind("\n\n", 0, m.start())
    j = text.find("\n\n", m.end())
    return text[i + 1 if i >= 0 else 0:j if j >= 0 else None]


def _outcome_prompts() -> dict:
    out = {}
    for k in BUILTIN_DICTS:
        sc = resolve(k)
        if getattr(sc, "outcome", None) is not None:
            out[k] = P.render_prompt(sc, sc.n_steps)
    return out


def test_every_task_with_an_untold_outcome_is_disclosed():
    tasks = _outcome_prompts()
    assert len(tasks) == 4 and all(NOTHING_ELSE in t for t in tasks.values()), sorted(tasks)
    texts = {p: p.read_text(encoding="utf-8") for p in DISCLOSURE}
    assert untold_outcomes(texts, tasks) == []


def test_CONTROL_an_undisclosed_outcome_is_found():
    """A new task with an outcome its prompt does not tell, a doc without the disclosure: both red. A prompt that
    tells the seat (no NOTHING_ELSE) asks nothing of the docs."""
    texts = {p: p.read_text(encoding="utf-8") for p in DISCLOSURE}
    tasks = _outcome_prompts()
    assert any("does not name x-new-task" in p for p in untold_outcomes(texts, {**tasks, "x-new-task": NOTHING_ELSE}))
    bare = {p: re.sub(re.escape(DISCLOSURE[p]).replace(r"\ ", r"\s+"), "x", t) for p, t in texts.items()}
    assert len([p for p in untold_outcomes(bare, tasks) if "does not say so" in p]) == 2 * len(tasks)
    assert untold_outcomes(bare, {"x-told": "your outcome is scored"}) == []


# ── the decision tasks: what a series of one scores, and whether it is in reach ───────────────────────────────
FEASIBILITY = "Feasibility of the goal a series of it scores"


def rows_without_feasibility(readme: str, tasks) -> list[str]:
    """The outcome tasks whose README row does not say what the declarations say of reaching the goal a series of
    it scores (the goal, not the outcome the task was built for)."""
    rows = {ln.split(" | ")[0][3:-1]: ln for ln in readme.splitlines() if ln.startswith("| `")}
    return sorted(t for t in tasks if FEASIBILITY not in rows.get(t, ""))


def test_every_decision_task_row_states_the_scored_goal_s_feasibility():
    tasks = _outcome_prompts()
    assert rows_without_feasibility(README.read_text(encoding="utf-8"), tasks) == []


def test_CONTROL_a_decision_row_without_its_feasibility_is_found():
    readme = README.read_text(encoding="utf-8")
    t = sorted(_outcome_prompts())[0]
    cut = "\n".join(ln.replace(FEASIBILITY, "x") if ln.startswith(f"| `{t}` |") else ln for ln in readme.splitlines())
    assert rows_without_feasibility(cut, _outcome_prompts()) == [t]


# ── the README's prose numbers on the notes, read from the notes file ─────────────────────────────────────────
PROSE = re.compile(r"The\s+Munster\s+set\s+is\s+the\s+Ireland\s+set\s+without\s+(\d+)\s+of\s+its\s+notes.*?and\s+the\s+(\d+)"
                   r"\s+that\s+describe\s+the\s+Dublin\s+start\)\s+and\s+with\s+(\d+)\s+of\s+its\s+own.*?served\s+(\d+)"
                   r"\s+notes,\s+(\d+)\s+of\s+them\s+scoped\s+to\s+one\s+start", re.S)
IRELAND = re.compile(r"`ireland1066`,\s+written\s+for\s+the\s+1066\s+Irish\s+start:\s+(\d+)\s+of\s+its\s+notes\s+are\s+"
                     r"scoped\s+to\s+one\s+start")
DEFAULT = re.compile(r"toolkit's\s+default\s+set:\s+general\s+game\s+rules\s+and\s+tool\s+use,\s+(\d+)\s+of\s+its\s+notes"
                     r"\s+scoped\s+to\s+one\s+start")


def notes_numbers() -> dict:
    """The numbers the README's prose states, computed from the toolkit's notes file."""
    nm = _notes_module()
    doc = json.loads(toolkit_source.notes_file().read_text(encoding="utf-8"))
    ire, mun, dflt = (nm.derive(doc, x) for x in ("ireland1066", "munster1066once", None))
    ids = lambda d: [n["id"] for n in d["notes"]]                                  # noqa: E731
    dropped = [x for x in ids(ire) if x not in ids(mun)]
    by = {n["id"]: n for n in doc["notes"]}
    scoped = lambda d: sum(_scope(n) not in GENERAL for n in d["notes"])         # noqa: E731
    return {"munster_drops": len(dropped),
            "munster_drops_dublin": sum("Dublin" in by[x]["scope"] for x in dropped),
            "munster_adds": len([x for x in ids(mun) if x not in ids(ire)]), "munster_notes": len(mun["notes"]),
            "munster_scoped": scoped(mun), "ireland_scoped": scoped(ire), "default_scoped": scoped(dflt)}


def prose_numbers(readme: str) -> dict:
    flat = " ".join(readme.split())
    m, i, d = PROSE.search(flat), IRELAND.search(flat), DEFAULT.search(flat)
    assert m and i and d, "the README's notes prose moved: update the patterns with it"
    keys = ("munster_drops", "munster_drops_dublin", "munster_adds", "munster_notes", "munster_scoped")
    return {**dict(zip(keys, map(int, m.groups()))), "ireland_scoped": int(i.group(1)), "default_scoped": int(d.group(1))}


def test_the_readme_prose_numbers_are_the_notes_files():
    _needs_toolkit()
    assert prose_numbers(README.read_text(encoding="utf-8")) == notes_numbers()


def test_CONTROL_a_stale_prose_number_is_found():
    _needs_toolkit()
    text = README.read_text(encoding="utf-8")
    flat = " ".join(text.split())
    stale = flat.replace("served 27 notes", "served 28 notes")
    assert stale != flat and prose_numbers(stale) != notes_numbers()


# ── the series' document on what the seat is given (section 12), against the code ───────────────────────────
SERIES_DOC = ROOT / "docs" / "prereg-munster-kingdom.md"


def _section12(text: str) -> str:
    i = text.index("## 12. What the seat is given beyond the task")
    return text[i:]


def test_the_document_lists_the_munster_sets_notes_by_scope():
    """Section 12's two lists are the Munster set as served: the start-specific ids are its notes scoped to one
    start, the general ids every other note."""
    _needs_toolkit()
    nm = _notes_module()
    doc = json.loads(toolkit_source.notes_file().read_text(encoding="utf-8"))
    served = nm.derive(doc, "munster1066once")["notes"]
    sec = " ".join(_section12(SERIES_DOC.read_text(encoding="utf-8")).split())
    spec = sec[sec.index("**specific to this start or this task**"):sec.index("**general game rules and tool use**")]
    gen = sec[sec.index("**general game rules and tool use**"):sec.index("So the comparison")]
    assert sorted(re.findall(r"`([a-z0-9-]+)`", spec)) == sorted(n["id"] for n in served if _scope(n) not in GENERAL)
    assert sorted(re.findall(r"`([a-z0-9-]+)`", gen)) == sorted(n["id"] for n in served if _scope(n) in GENERAL)


def test_the_document_says_the_goal_line_is_served_from_the_first_turn_and_the_code_serves_it(tmp_path):
    """The goal line is in the seat's first observation after the game opens (the backend on a stand-in bridge and
    the toolkit's recorded formable_titles row), and the document says "from the first turn", never that the goal
    line comes once the kingdom is first held; the env's paid-once line after the first holding is said too."""
    from ck3bench.env.test_goal_line_gold import ROW_1082, FakeBridge, _backend, _scn, _Tr
    tr = _Tr(ROW_1082)
    b, *_ = _backend(tmp_path, bridge=FakeBridge(speed=0, rate=7))
    b.transport_factory = lambda seat, cid, env: tr
    b.open(_scn(), seed=1)
    b.target_title = "k_ireland"
    assert "goal" in b.observation("king")                         # the first observation of the episode
    text = " ".join(SERIES_DOC.read_text(encoding="utf-8").split())
    assert "**the goal line**, from the first turn" in text and "standing orders" in text and "forced wakes" in text
    assert not re.search(r"once the kingdom is first held,? the goal line", text)
    assert "the env replaces that line with one saying the reward was paid once" in text
    assert "served the notes set `munster1066once` (`notes_version` in the expectation) and, once the kingdom" not in text


def test_CONTROL_the_document_checks_fire():
    """The old section 12 wording (the goal line only once the kingdom is held) is what the pattern catches."""
    old = "the goal line, once the kingdom is first held, the goal line the env adds"
    assert re.search(r"once the kingdom is first held,? the goal line", old)
