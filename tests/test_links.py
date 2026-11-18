"""Every relative link in every Markdown file of this repository resolves: the
file exists, and an anchor names a heading of the file it points into.

Walked from the tree, never a list of names. External links (a scheme such as
https: or mailto:) are not fetched. A control proves the checker reds on a dead
file and on a dead anchor, so a green here is not an empty walk.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKIP_DIRS = {".git", "dist", "build", "__pycache__", ".pytest_cache", ".venv", ".ruff_cache"}

INLINE = re.compile(r"(?<!!)\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
IMAGE = re.compile(r"!\[[^\]]*\]\(([^)\s]+)\)")
REFDEF = re.compile(r"^\s{0,3}\[[^\]]+\]:\s*(\S+)", re.M)
FENCE = re.compile(r"^(```|~~~).*?^\1\s*$", re.M | re.S)
CODE = re.compile(r"`[^`\n]*`")
HEADING = re.compile(r"^#{1,6}\s+(.+?)\s*#*\s*$", re.M)
SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")


def _markdown_files() -> list[Path]:
    out = []
    for p in sorted(ROOT.rglob("*.md")):
        rel = p.relative_to(ROOT)
        if any(part in SKIP_DIRS or part.endswith(".egg-info") for part in rel.parts):
            continue
        out.append(p)
    return out


def _prose(text: str) -> str:
    """The text with fenced blocks and inline code removed: a link written as
    code is an example, not a link."""
    return CODE.sub("", FENCE.sub("", text))


def slug(heading: str) -> str:
    """GitHub's heading anchor: lowercased, markup and punctuation dropped,
    spaces to hyphens."""
    h = re.sub(r"`([^`]*)`", r"\1", heading)
    h = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", h)
    h = h.strip().lower()
    h = re.sub(r"[^\w\- ]", "", h)
    return h.replace(" ", "-")


def anchors(path: Path) -> set[str]:
    seen: dict[str, int] = {}
    out: set[str] = set()
    for m in HEADING.finditer(FENCE.sub("", path.read_text(encoding="utf-8"))):
        s = slug(m.group(1))
        n = seen.get(s, 0)
        seen[s] = n + 1
        out.add(s if n == 0 else f"{s}-{n}")
    return out


def links(path: Path) -> list[str]:
    text = _prose(path.read_text(encoding="utf-8"))
    return [m.group(1) for rx in (INLINE, IMAGE, REFDEF) for m in rx.finditer(text)]


def dead_links(path: Path) -> list[str]:
    dead = []
    for target in links(path):
        if SCHEME.match(target):
            continue
        file_part, _, anchor = target.partition("#")
        dest = path if not file_part else (path.parent / file_part)
        if not dest.exists():
            dead.append(f"{target}: no such file")
            continue
        if anchor and dest.is_file() and dest.suffix == ".md" and anchor not in anchors(dest):
            dead.append(f"{target}: no heading #{anchor}")
    return dead


def test_every_relative_link_and_anchor_resolves():
    files = _markdown_files()
    assert {"README.md", "CONTRIBUTING.md", "SECURITY.md"} <= {p.relative_to(ROOT).as_posix() for p in files}
    checked = sum(1 for p in files for t in links(p) if not SCHEME.match(t))
    assert checked >= 5, checked              # non-vacuous: the docs link each other
    dead = [f"{p.relative_to(ROOT).as_posix()}: {d}" for p in files for d in dead_links(p)]
    assert dead == []


def test_the_checker_reds_on_a_dead_file_and_a_dead_anchor(tmp_path):
    """CONTROL: a planted dead file link and a planted dead anchor are both found;
    a live file and a live anchor are not."""
    (tmp_path / "a.md").write_text("# Top\n\n## Install, and check\n", encoding="utf-8")
    doc = tmp_path / "b.md"
    doc.write_text("[ok](a.md) [ok](a.md#install-and-check) [x](missing.md) [y](a.md#nowhere)\n"
                   "`[not a link](gone.md)`\n", encoding="utf-8")
    assert dead_links(doc) == ["missing.md: no such file", "a.md#nowhere: no heading #nowhere"]
