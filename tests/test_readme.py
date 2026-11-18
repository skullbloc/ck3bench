"""The README names every refusal the code can emit and every milestone the CLI names.

Enumerated from the code (`ck3bench.experiment` R_*, `ck3bench.cli`), so a
refusal added without a README row reds here; an empty enumeration is a red
too (fail open).
"""
import re
from pathlib import Path

from ck3bench import cli, experiment

ROOT = Path(__file__).resolve().parents[1]


def _readme() -> str:
    return (ROOT / "README.md").read_text(encoding="utf-8")


def _refusal_codes() -> set[str]:
    codes = {v for k, v in vars(experiment).items() if k.startswith("R_") and isinstance(v, str)}
    codes.add(cli.R_NOT_IMPLEMENTED)
    return codes


def test_the_readme_names_every_refusal_code():
    codes = _refusal_codes()
    assert len(codes) >= 15, codes
    text = _readme()
    missing = sorted(c for c in codes if f"`{c}`" not in text)
    assert missing == [], missing


def test_the_readme_names_every_milestone_and_no_lane_label():
    text = _readme()
    for m in cli.MILESTONES:
        assert f"`{m}`" in text, m
    assert not re.search(r"\blane K[1-6]\b", text) and not re.search(r"\| K[1-6]\b", text)


def test_the_readme_names_the_research_tree_references_the_shipped_files_carry():
    text = _readme()
    assert "## References that live in the research tree" in text
    for ref in ("docs/pre-registration-v4-orgdesign.md", "census-lines.txt"):
        assert ref in text, ref


# A repo-relative path some shipped file names: research-tree roots, and the
# ck3agent package's. Not inside a URL or a longer path (no `/` or word char before).
REF = re.compile(r"(?<![\w/.-])((?:docs|scripts|release|ck3agent|ck3bench)/[A-Za-z0-9_.*/-]*[A-Za-z0-9_*/])")
SKIP = {".git", "dist", "build", "__pycache__", ".pytest_cache", ".venv"}


def _section(text: str) -> str:
    start = text.index("## References that live in the research tree")
    end = text.find("\n## ", start + 1)
    return text[start:] if end < 0 else text[start:end]


def _named_paths() -> dict[str, str]:
    """{path: first file naming it} over every shipped file but the README,
    .gitignore (its lines are patterns, not references) and the export's marker."""
    named: dict[str, str] = {}
    for p in sorted(ROOT.rglob("*")):
        rel = p.relative_to(ROOT)
        if not p.is_file() or rel.as_posix() in ("README.md", ".gitignore", ".ck3bench-export") or any(
                s in SKIP or s.endswith(".egg-info") for s in rel.parts):
            continue
        text = p.read_bytes().decode("utf-8", "replace")
        for m in REF.finditer(text):
            named.setdefault(m.group(1).rstrip("/"), rel.as_posix())
    return named


def _present(path: str) -> bool:
    return any(ROOT.glob(path)) if "*" in path else (ROOT / path).exists()


def _listed(path: str, section: str) -> bool:
    if path.startswith("ck3agent/"):
        return "paths under `ck3agent/`" in section
    parts = path.split("/")
    # the path itself, or a parent of at least two segments the section names
    return any("/".join(parts[:k]) in section for k in range(len(parts), 1, -1))


def test_every_research_tree_path_a_shipped_file_names_is_listed_in_the_readme():
    section = _section(_readme())
    named = _named_paths()
    absent = {p: f for p, f in named.items() if not _present(p)}
    # non-vacuous: the study's declared document and census freeze are research-tree
    # paths the walk must find, so an empty walk cannot pass
    assert {"docs/pre-registration-v4-orgdesign.md",
            "docs/cleanup-2026.11/evidence/v4pilot/census/census-lines.txt"} <= set(absent), absent
    unlisted = sorted(f"{f}: {p}" for p, f in absent.items() if not _listed(p, section))
    assert unlisted == [], unlisted
