"""`ck3bench`: the version, and an episode end to end through the command."""
from __future__ import annotations

import json

from ck3bench import cli


def test_env_subcommand_runs_a_fake_episode_end_to_end(tmp_path, capsys):
    """`ck3bench env run` on the fake backend: one episode, a well-formed trajectory, every record fake."""
    out = tmp_path / "t.jsonl"
    rc = cli.main(["env", "run", "fake-solo", "--seed", "3", "--policy", "observe-then-gift", "--out", str(out)])
    assert rc == cli.EXIT_OK
    row = json.loads(capsys.readouterr().out)
    assert row["ok"] and row["fake"] is True and row["backend"] == "fake" and row["steps"] == 12
    assert cli.main(["env", "check", str(out)]) == cli.EXIT_OK
    assert json.loads(capsys.readouterr().out)["fake"] == [True]
