"""Which toolkit build is installed: an identity that a version string is not.

Different ck3agent wheels can carry one version number, so a version string alone does not name a build. The
version stays; beside it the trajectory header carries the installed package's MEMBERS DIGEST: the
sha256 over the sorted lines `<path> <sha256 of its bytes>` of every file the distribution's RECORD lists under
site-packages, `*.dist-info/`, `__pycache__/`, `*.data/` and paths outside site-packages (`../`) left out. The
same digest is computed from a wheel file's members by the same rule (`wheel_members`), so a launcher holding
the wheel and the header holding the install compare one number; `compare` lists the members that differ.

The wheel file's own sha256 cannot be read from an install (pip keeps no copy); a launcher that knows it
declares it in CK3BENCH_TOOLKIT_WHEEL_SHA256 and the header records it as declared, or "not_declared".
"""
from __future__ import annotations

import csv
import hashlib
import os
import zipfile
from pathlib import Path
from typing import Any, Optional

WHEEL_SHA_ENV = "CK3BENCH_TOOLKIT_WHEEL_SHA256"
DIST = "ck3agent"


def _skip(name: str) -> bool:
    first = name.split("/", 1)[0]
    return (name.startswith("../") or name.startswith("/") or first.endswith(".dist-info")
            or first.endswith(".data") or "/__pycache__/" in f"/{name}" or name.endswith("/"))


def digest(members: dict[str, str]) -> str:
    """The one rule: sha256 over sorted `path sha256` lines."""
    body = "".join(f"{p} {h}\n" for p, h in sorted(members.items()))
    return hashlib.sha256(body.encode()).hexdigest()


def wheel_members(wheel: str | Path) -> dict[str, str]:
    out: dict[str, str] = {}
    with zipfile.ZipFile(wheel) as z:
        for n in z.namelist():
            if not _skip(n):
                out[n] = hashlib.sha256(z.read(n)).hexdigest()
    return out


def installed_members(dist: Any = None) -> dict[str, Optional[str]]:
    """Each RECORD-listed file of the installed distribution, hashed as it is on disk now (None: missing)."""
    if dist is None:
        import importlib.metadata as md
        dist = md.distribution(DIST)
    out: dict[str, Optional[str]] = {}
    # RECORD read here, not `dist.files`: Python 3.14's `files` drops the paths that no longer exist, which is
    # the very thing a member comparison must see.
    record = dist.read_text("RECORD")
    if record is None:
        raise FileNotFoundError(f"{DIST}: the installed distribution has no RECORD")
    for row in csv.reader(record.splitlines()):
        if not row:
            continue
        n = row[0].replace(os.sep, "/")
        if _skip(n):
            continue
        p = Path(dist.locate_file(n))
        out[n] = hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None
    return out


def compare(wheel: str | Path, dist: Any = None) -> dict[str, Any]:
    """The wheel's members against the install, member by member."""
    w, i = wheel_members(wheel), installed_members(dist)
    differ = sorted(n for n in w if n in i and i[n] != w[n])
    missing = sorted(n for n in w if n not in i or i[n] is None)
    extra = sorted(n for n in i if n not in w)
    same = len(w) - len(differ) - len(missing)
    return {"members": len(w), "identical": same, "differ": differ, "missing": missing, "extra_installed": extra,
            "wheel_members_sha256": digest(w), "verdict": "identical" if same == len(w) and not extra else "DIFFERS"}


def identity(dist: Any = None) -> dict[str, Any]:
    """The header's toolkit identity; a read that fails says why, never a default."""
    try:
        if dist is None:
            import importlib.metadata as md
            dist = md.distribution(DIST)
        mem = installed_members(dist)
    except Exception as e:  # noqa: BLE001
        return {"unread": f"{type(e).__name__}: {e}",
                "wheel_sha256_declared": os.environ.get(WHEEL_SHA_ENV) or "not_declared"}
    missing = sorted(n for n, h in mem.items() if h is None)
    return {"version": dist.version, "installed_members": len(mem),
            "installed_members_sha256": digest({n: h for n, h in mem.items() if h is not None}) if not missing
            else None, **({"missing": missing[:20]} if missing else {}),
            "wheel_sha256_declared": os.environ.get(WHEEL_SHA_ENV) or "not_declared"}
