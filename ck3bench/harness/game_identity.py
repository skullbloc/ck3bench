"""Which game process is this run's: from what the run itself recorded, never a process name or command line.

It assumes the Linux game: the process's /proc/<pid>/exe ends in `/binaries/ck3` (the Steam layout of CK3's
native build); a game binary at another path is never recognised (docs/harness.md, "Requirements of the hooks").

    python -m ck3bench.harness.game_identity RUN_DIR   prints the run's game pid (exit 0) or `REFUSED why` (exit 1)

The series' start and end witnesses read the game's identity from /proc/<pid> of this process only.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def environ(pid: int) -> dict[str, str]:
    raw = Path(f"/proc/{pid}/environ").read_bytes().split(b"\0")
    return dict(kv.decode(errors="replace").split("=", 1) for kv in raw if b"=" in kv)


def _header(run: Path) -> dict:
    return json.loads((run / "trajectory.jsonl").open().readline())


def owned(run: Path, pid: int | None = None) -> tuple[int | None, str]:
    """(pid, "") when the game is this run's, else (None, why). From what the run itself recorded, on either host:
    the pid is the trajectory header's (the env's record of the game it launched); its /proc exe is a CK3 binary;
    it is not one of the CK3 processes alive before the run began (RUN_DIR/ck3.before, the series driver writes it); and its
    CK3_INSTANCE_ID is the instance this run declared: KIT_INSTANCE (the series driver exports the
    instance the run's --instance-id names), else the header's own instance_id; no declared instance means the game
    carries none (or `default`). Never a name or a command line. A fake-backend header (no game) takes its
    stand-in pid from the file KIT_STANDIN_GAME_PID_FILE names (a dry rehearsal starts the stand-in after
    ck3.before is written), and only there: every other check still applies."""
    try:
        head = _header(run)
    except (OSError, ValueError) as e:
        return None, f"no trajectory header ({type(e).__name__})"
    prov = head.get("provenance") or {}
    if pid is None:
        pid = prov.get("pid")
        if pid is None and prov.get("fake") is True and os.environ.get("KIT_STANDIN_GAME_PID_FILE"):
            try:
                pid = Path(os.environ["KIT_STANDIN_GAME_PID_FILE"]).read_text().strip()
            except OSError:
                pid = None
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return None, f"the trajectory header names no game pid ({pid!r})"
    if not _exe(pid).endswith("/binaries/ck3"):
        return None, f"pid {pid} is not a CK3 binary (exe {_exe(pid) or 'gone'!r})"
    try:
        before = (run / "ck3.before").read_text().split()
    except OSError:
        return None, "RUN_DIR/ck3.before is missing: cannot tell this run's game from an earlier one"
    if str(pid) in before:
        return None, f"pid {pid} was alive before this run began (ck3.before)"
    declared, recorded = os.environ.get("KIT_INSTANCE") or "", prov.get("instance_id") or ""
    if declared and recorded and declared != recorded:
        return None, f"the header's instance {recorded!r} is not the declared {declared!r}"
    want = declared or recorded
    try:
        got = environ(pid).get("CK3_INSTANCE_ID", "")
    except OSError as e:
        return None, f"pid {pid}: environ unreadable ({type(e).__name__})"
    if (got or "default") != (want or "default"):
        return None, f"pid {pid} carries instance {got!r}, this run's is {want!r}"
    return pid, ""


def ours(run: Path, pid: int) -> bool:
    return owned(run, pid)[0] == pid


def _exe(p: int) -> str:
    try:
        return os.readlink(f"/proc/{p}/exe")
    except OSError:
        return ""


if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print(__doc__)
        sys.exit(0 if len(sys.argv) > 1 else 2)
    p, why = owned(Path(sys.argv[1]))
    print(p if p is not None else f"REFUSED {why}")
    sys.exit(0 if p is not None else 1)
