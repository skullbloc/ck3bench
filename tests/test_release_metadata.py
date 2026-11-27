"""The version agrees everywhere a user or a citation reads it, and the toolkit is pinned exactly.

A -dev version is never released: its citation carries no date-released and
its CHANGELOG heading says unreleased. A release version carries both, equal.
"""
import os
import re
import tomllib
import urllib.error
import urllib.request
from pathlib import Path

import pytest

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
    """One exact ck3agent version is pinned, and the README's install line names the same release.

    THE PINNED VERSION MUST BE A PUBLISHED ck3agent RELEASE BEFORE THIS TREE IS SYNCED: the Quickstart installs
    that release's wheel by URL and CI installs the pin, so an unpublished pin makes both fail for every user.
    This test reads no network, so it cannot tell a published version from an unpublished one: the one below does,
    when asked (CK3BENCH_SYNC_CHECK=1)."""
    deps = _pyproject()["project"]["dependencies"]
    pins = [d for d in deps if d.strip().startswith("ck3agent")]
    assert len(pins) == 1 and re.fullmatch(r"ck3agent==\d{4}\.\d{1,2}\.\d+", pins[0].strip()), pins
    version = pins[0].strip().split("==", 1)[1]
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    urls = re.findall(r"releases/download/v([0-9.]+)/ck3agent-([0-9.]+)-py3-none-any\.whl", readme)
    assert urls and all(a == b == version for a, b in urls), (version, urls)


def test_the_optional_extras_are_test_and_anthropic():
    extras = _pyproject()["project"]["optional-dependencies"]
    assert set(extras) == {"test", "anthropic"}
    assert [d.split(">")[0].split("=")[0].strip() for d in extras["anthropic"]] == ["anthropic"]


#: the release asset the README's quickstart and CI install
ASSET = "https://github.com/skullbloc/ck3agent/releases/download/v{v}/ck3agent-{v}-py3-none-any.whl"
SYNC_CHECK = "CK3BENCH_SYNC_CHECK"


def _pinned() -> str:
    pins = [d.strip() for d in _pyproject()["project"]["dependencies"] if d.strip().startswith("ck3agent")]
    return pins[0].split("==", 1)[1]


def _asset_answers(url: str, opener=urllib.request.urlopen) -> tuple[bool, str]:
    """(the asset is downloadable, what was read): a HEAD request that follows redirects; any HTTP error or failed
    connection is `False` with why, never a pass."""
    try:
        with opener(urllib.request.Request(url, method="HEAD"), timeout=30) as r:
            return 200 <= r.status < 300, f"HTTP {r.status}"
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code}"
    except (urllib.error.URLError, OSError) as e:
        return False, f"{type(e).__name__}: {e}"


@pytest.mark.skipif(os.environ.get(SYNC_CHECK) != "1",
                    reason=f"offline by default; {SYNC_CHECK}=1 asks the network whether the pinned release exists")
def test_the_pinned_toolkit_release_is_published_before_a_sync():
    """Run before every sync to the public repository (CONTRIBUTING.md): the pinned ck3agent version's wheel must
    be a published release asset, else the quickstart, CI and every contributor's setup fail. A failure to reach
    the network fails too: this check never passes on what it could not read."""
    url = ASSET.format(v=_pinned())
    ok, why = _asset_answers(url)
    assert ok, f"the pinned toolkit release is not downloadable ({why}): {url}. Publish it before syncing."


def test_CONTROL_the_sync_check_reads_a_missing_asset_and_an_unreachable_network_as_not_published():
    """The check's reading, without a network: a 404, a refused connection and a 200 are told apart, and only the
    200 passes."""
    def answer(code=None, exc=None):
        def opener(req, timeout):
            assert req.get_method() == "HEAD" and req.full_url.endswith(f"ck3agent-{_pinned()}-py3-none-any.whl")
            if exc is not None:
                raise exc
            if code >= 400:
                raise urllib.error.HTTPError(req.full_url, code, "x", {}, None)

            class R:
                status = code

                def __enter__(self):
                    return self

                def __exit__(self, *a):
                    return False
            return R()
        return opener
    url = ASSET.format(v=_pinned())
    assert _asset_answers(url, answer(404)) == (False, "HTTP 404")
    assert _asset_answers(url, answer(exc=urllib.error.URLError("refused")))[0] is False
    assert _asset_answers(url, answer(200)) == (True, "HTTP 200")
