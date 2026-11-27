"""The records that decide whether an attempt was played out reach the disk before their writer goes on
(ck3bench.env.durable): a record written whole is flushed (file and directory) before the rename returns, the
trajectory's end row is flushed as it is written and its other rows are not, and the test-only switch skips every
flush. MUTANT: a writer whose end row is only flushed to the kernel makes no flush to the disk."""
import os

import pytest

from ck3bench.env import durable
from ck3bench.env import trajectory as T


@pytest.fixture
def fsyncs(monkeypatch):
    monkeypatch.delenv(durable.NO_FSYNC_FOR_TESTS, raising=False)
    calls = []
    real = os.fsync

    def counted(fd):
        calls.append(os.path.realpath(f"/proc/self/fd/{fd}"))
        real(fd)
    monkeypatch.setattr(durable.os, "fsync", counted)
    return calls


def test_a_record_written_whole_is_flushed_with_its_directory_before_it_returns(tmp_path, fsyncs):
    durable.write_whole(tmp_path / "end.json", "{}")
    assert (tmp_path / "end.json").read_text() == "{}" and not list(tmp_path.glob(".*.tmp"))
    assert len(fsyncs) == 2 and fsyncs[-1] == str(tmp_path)


def test_the_trajectory_s_end_row_is_flushed_and_its_other_rows_are_not(tmp_path, fsyncs):
    w = T.TrajectoryWriter(tmp_path / "trajectory.jsonl")
    w.begin(backend="fake", fake=True)
    w.write("step", step=1)
    assert fsyncs == []
    w.write("end", reason="all_seats_done", steps=1, returns={})
    assert len(fsyncs) == 2 and fsyncs[0].endswith("trajectory.jsonl") and fsyncs[1] == str(tmp_path)
    w.close()


def test_the_test_only_switch_skips_every_flush_and_keeps_the_rename(tmp_path, fsyncs, monkeypatch):
    monkeypatch.setenv(durable.NO_FSYNC_FOR_TESTS, "1")
    durable.write_whole(tmp_path / "x.json", "1")
    w = T.TrajectoryWriter(tmp_path / "trajectory.jsonl")
    w.write("end", reason="closed", steps=0, returns={})
    w.close()
    assert fsyncs == [] and (tmp_path / "x.json").read_text() == "1"


def test_MUTANT_an_end_row_flushed_to_the_kernel_only_reaches_no_disk(tmp_path, fsyncs, monkeypatch):
    monkeypatch.setattr(T.TrajectoryWriter, "sync", lambda self: self._fh.flush())
    w = T.TrajectoryWriter(tmp_path / "trajectory.jsonl")
    w.write("end", reason="all_seats_done", steps=1, returns={})
    w.close()
    assert fsyncs == []


def test_a_stream_with_no_file_is_written_and_not_flushed(fsyncs):
    import io
    s = io.StringIO()
    T.TrajectoryWriter(stream=s).write("end", reason="closed", steps=0, returns={})
    assert '"kind": "end"' in s.getvalue() and fsyncs == []
