"""Records written to the disk whole: one declaration of how the kit's records reach the disk, read by the series
steps (ck3bench.harness.series `_write`), the seat runner's end record (ck3bench.harness.runner) and the trajectory's
end row (ck3bench.env.trajectory).

`write_whole` writes a record to a temporary file, flushes it to the disk, renames it over the record and flushes
the directory, so a writer stopped between two writes leaves the record as it was before or after, never half
written, and a power loss after it returns does not lose it. `sync_file` flushes one open file to the disk.

NO_FSYNC_FOR_TESTS names the kit's TEST-ONLY switch: set to `1`, the flushes to the disk are skipped (the rename
stays), so the kit's own tests can write thousands of records. `series start` records whether it was set
(`run.json` `records_flushed`), and the scorer refuses a series with an attempt started under it (docs/harness.md,
the environment-variable table)."""
from __future__ import annotations

import os
from pathlib import Path
from typing import IO

NO_FSYNC_FOR_TESTS = "CK3BENCH_TEST_NO_FSYNC"


def sync_wanted() -> bool:
    """Whether records are flushed to the disk (always, unless the test-only switch is set)."""
    return os.environ.get(NO_FSYNC_FOR_TESTS) != "1"


def sync_file(f: IO) -> None:
    """Flush an open file to the disk (Python's buffer, then the kernel's), unless the test-only switch is set."""
    f.flush()
    if sync_wanted():
        os.fsync(f.fileno())


def sync_dir(d: Path) -> None:
    """Flush a directory's entries (a rename, a new file) to the disk; a directory that cannot be opened is left."""
    if not sync_wanted():
        return
    try:
        fd = os.open(d, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def write_whole(p: Path, text: str) -> None:
    """Write `text` to `p` whole or not at all: a temporary file beside it, flushed to the disk, renamed over it, and
    the directory flushed."""
    p = Path(p)
    tmp = p.with_name(f".{p.name}.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
        sync_file(f)
    os.replace(tmp, p)
    sync_dir(p.parent)
