"""The toolkit identity beside the version (toolkit_id.py) and in the trajectory header."""
import importlib.metadata as md
import io
import json
import zipfile

from ck3bench.env import toolkit_id as T


def _site(tmp, files: dict, version="2026.12.12"):
    """An installed distribution laid out as pip leaves it: files, dist-info with METADATA and RECORD (a pyc, an
    entry-point script outside site-packages and the dist-info's own files listed too)."""
    site = tmp / "site"
    for n, b in files.items():
        (site / n).parent.mkdir(parents=True, exist_ok=True)
        (site / n).write_bytes(b)
    di = site / f"ck3agent-{version}.dist-info"
    di.mkdir(parents=True)
    (di / "METADATA").write_text(f"Metadata-Version: 2.1\nName: ck3agent\nVersion: {version}\n")
    (site / "ck3agent" / "__pycache__").mkdir(parents=True, exist_ok=True)
    (site / "ck3agent" / "__pycache__" / "x.pyc").write_bytes(b"pyc")
    rec = list(files) + ["ck3agent/__pycache__/x.pyc", "../../../bin/ck3agent", f"{di.name}/METADATA", f"{di.name}/RECORD"]
    (di / "RECORD").write_text("".join(f"{r},,\n" for r in rec))
    return md.PathDistribution(di)


def _wheel(tmp, files: dict, version="2026.12.12"):
    p = tmp / f"ck3agent-{version}-py3-none-any.whl"
    with zipfile.ZipFile(p, "w") as z:
        for n, b in files.items():
            z.writestr(n, b)
        z.writestr(f"ck3agent-{version}.dist-info/RECORD", "whatever")
        z.writestr(f"ck3agent-{version}.data/scripts/x", "s")
    return p


FILES = {"ck3agent/__init__.py": b"v = 1\n", "ck3agent/server/tools.py": b"def f(): pass\n"}


def test_an_intact_install_and_its_wheel_give_one_digest(tmp_path):
    dist = _site(tmp_path, FILES)
    whl = _wheel(tmp_path, FILES)
    c = T.compare(whl, dist)
    assert c["verdict"] == "identical" and c["members"] == 2 and c["identical"] == 2, c
    ident = T.identity(dist)
    assert ident["installed_members_sha256"] == c["wheel_members_sha256"] == T.digest(T.wheel_members(whl))
    assert ident["version"] == "2026.12.12" and ident["installed_members"] == 2


def test_two_builds_with_one_version_are_told_apart(tmp_path):
    a = _site(tmp_path / "a", FILES)
    b = _site(tmp_path / "b", {**FILES, "ck3agent/server/tools.py": b"def f(): return 2\n"})
    ia, ib = T.identity(a), T.identity(b)
    assert ia["version"] == ib["version"]                                   # what the header said before
    assert ia["installed_members_sha256"] != ib["installed_members_sha256"]  # what it says now


def test_a_changed_installed_file_is_named(tmp_path):
    dist = _site(tmp_path, FILES)
    whl = _wheel(tmp_path, FILES)
    (tmp_path / "site" / "ck3agent" / "server" / "tools.py").write_bytes(b"edited\n")
    c = T.compare(whl, dist)
    assert c["verdict"] == "DIFFERS" and c["differ"] == ["ck3agent/server/tools.py"]


def test_a_missing_file_is_missing_and_the_digest_is_not_computed(tmp_path):
    dist = _site(tmp_path, FILES)
    (tmp_path / "site" / "ck3agent" / "__init__.py").unlink()
    ident = T.identity(dist)
    assert ident["installed_members_sha256"] is None and ident["missing"] == ["ck3agent/__init__.py"]


def test_the_wheel_sha_is_declared_or_says_it_was_not(tmp_path, monkeypatch):
    dist = _site(tmp_path, FILES)
    monkeypatch.delenv(T.WHEEL_SHA_ENV, raising=False)
    assert T.identity(dist)["wheel_sha256_declared"] == "not_declared"
    monkeypatch.setenv(T.WHEEL_SHA_ENV, "ab" * 32)
    assert T.identity(dist)["wheel_sha256_declared"] == "ab" * 32


def test_an_unreadable_distribution_is_unread_not_a_default():
    class Broken:
        version = "x"

        def read_text(self, name):
            raise OSError("no RECORD")
    r = T.identity(Broken())
    assert "unread" in r and "installed_members_sha256" not in r


def test_the_ck3_header_carries_the_identity_beside_the_version(tmp_path, monkeypatch):
    from ck3bench.env import test_wake_ck3 as W
    seen = {}
    monkeypatch.setattr(T, "identity", lambda dist=None: seen.setdefault("id", {"installed_members_sha256": "cd" * 32}))
    buf = io.StringIO()
    W._env(tmp_path, buf=buf)
    head = json.loads(buf.getvalue().splitlines()[0])
    assert head["provenance"]["toolkit_identity"] == {"installed_members_sha256": "cd" * 32}
    assert "toolkit" in head["provenance"]
    from ck3bench.env.trajectory import summary
    (row,) = summary([head])
    assert row["toolkit_identity"] == {"installed_members_sha256": "cd" * 32}
