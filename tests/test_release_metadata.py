"""The version agrees everywhere a user or a citation reads it, and the toolkit is pinned exactly.

A -dev version is never released: its citation carries no date-released and
its CHANGELOG heading says unreleased. A release version carries both, equal.
"""
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HEAD = re.compile(r"^## (\d{4}\.\d{1,2}\.\d+(?:-dev\d+)?) — (\d{4}-\d{2}-\d{2}|unreleased)$", re.M)


def _pyproject():
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def _changelog_head():
    m = HEAD.search((ROOT / "CHANGELOG.md").read_text(encoding="utf-8"))
    assert m, "CHANGELOG.md has no '## <version> — <date or unreleased>' heading"
    return m.group(1), m.group(2)


def _citation():
    text = (ROOT / "CITATION.cff").read_text(encoding="utf-8")
    v = re.search(r'^version:\s*"?([^"\n]+)"?$', text, re.M)
    d = re.search(r'^date-released:\s*"?(\d{4}-\d{2}-\d{2})"?$', text, re.M)
    assert v, "CITATION.cff lacks version"
    return v.group(1), d.group(1) if d else None


def test_version_is_the_same_in_version_file_pyproject_changelog_and_citation():
    version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    pyproject = _pyproject()
    assert "version" in pyproject["project"].get("dynamic", [])
    src = (ROOT / pyproject["tool"]["hatch"]["version"]["path"]).read_text(encoding="utf-8")
    m = re.search(r'__version__\s*=\s*"([^"]+)"', src)
    assert m and m.group(1) == version
    assert _changelog_head()[0] == version
    assert _citation()[0] == version


def test_a_dev_version_is_unreleased_and_a_release_is_dated():
    version, date = _changelog_head()
    assert (date == "unreleased") == ("-dev" in version), (version, date)


def test_citation_release_date_is_the_changelog_date_or_absent_for_a_dev_version():
    version, date = _changelog_head()
    cited = _citation()[1]
    if "-dev" in version:
        assert cited is None, f"a dev version is never released; CITATION.cff says date-released {cited}"
    else:
        assert cited == date, (cited, date)


def test_the_license_is_apache_2_0_everywhere_it_is_named():
    assert _pyproject()["project"]["license"] == "Apache-2.0"
    assert "NOTICE" in _pyproject()["project"]["license-files"]
    assert "Apache License" in (ROOT / "LICENSE").read_text(encoding="utf-8")
    assert "Apache License, Version 2.0" in (ROOT / "NOTICE").read_text(encoding="utf-8")
    assert re.search(r"^license: Apache-2.0$", (ROOT / "CITATION.cff").read_text(encoding="utf-8"), re.M)


def test_the_toolkit_is_pinned_to_one_exact_ck3agent_version():
    deps = _pyproject()["project"]["dependencies"]
    pins = [d for d in deps if d.strip().startswith("ck3agent")]
    assert len(pins) == 1 and re.fullmatch(r"ck3agent==\d{4}\.\d{1,2}\.\d+", pins[0].strip()), pins


def test_the_optional_extras_are_test_and_anthropic():
    extras = _pyproject()["project"]["optional-dependencies"]
    assert set(extras) == {"test", "anthropic"}
    assert [d.split(">")[0].split("=")[0].strip() for d in extras["anthropic"]] == ["anthropic"]
