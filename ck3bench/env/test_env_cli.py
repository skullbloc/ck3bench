"""`ck3bench env`: scenarios, run, check, and the refusals a user meets first."""
from __future__ import annotations

import json

from ck3bench.env import cli
from ck3bench.env.scenario import BUILTIN_DICTS, STATUSES


def _rows(out):
    return [json.loads(x) for x in out.strip().splitlines()]


def test_scenarios_lists_every_builtin_with_its_status(capsys):
    assert cli.main(["scenarios"]) == cli.EXIT_OK
    rows = _rows(capsys.readouterr().out)
    assert {r["id"] for r in rows} == set(BUILTIN_DICTS)
    assert all(r["status"] in STATUSES and r["why"] for r in rows)


def test_run_on_the_fake_backend_with_each_policy(tmp_path, capsys):
    for pol in ("noop", "random", "observe-then-gift"):
        out = tmp_path / f"{pol}.jsonl"
        assert cli.main(["run", "fake-duel", "--seed", "1", "--policy", pol, "--out", str(out)]) == cli.EXIT_OK
        row = _rows(capsys.readouterr().out)[0]
        assert row["ok"] and row["fake"] and row["end_reason"] == "horizon" and row["seed"] == 1
        assert cli.main(["check", str(out)]) == cli.EXIT_OK
        capsys.readouterr()


def test_a_ck3_task_without_a_saves_dir_is_refused_before_anything_launches(capsys):
    assert cli.main(["run", "france1066-treasury-2y", "--seed", "1", "--policy", "noop"]) == cli.EXIT_REFUSED
    row = _rows(capsys.readouterr().out)[0]
    assert row["ok"] is False and row["code"] == "start_save_missing"


def test_an_unknown_task_is_refused(capsys):
    assert cli.main(["run", "chess", "--seed", "1", "--policy", "noop"]) == cli.EXIT_REFUSED
    assert _rows(capsys.readouterr().out)[0]["code"] == "scenario_unknown"


def test_check_reds_on_a_broken_trajectory(tmp_path, capsys):
    out = tmp_path / "t.jsonl"
    cli.main(["run", "fake-solo", "--seed", "2", "--policy", "noop", "--out", str(out)])
    capsys.readouterr()
    lines = out.read_text().splitlines()
    out.write_text("\n".join(lines[:-1]) + "\n")          # drop the end record
    assert cli.main(["check", str(out)]) == cli.EXIT_BAD_TRAJECTORY
    assert "ends inside an episode" in " ".join(_rows(capsys.readouterr().out)[0]["problems"])


def test_serve_refuses_a_remote_bind_without_allow_remote(capsys):
    assert cli.main(["serve", "fake-solo", "--host", "0.0.0.0"]) == cli.EXIT_REFUSED
    assert _rows(capsys.readouterr().out)[0]["code"] == "remote_bind_refused"


# ── serve --turn-timeout-s, play (the mixed-policy runner), shim ──────────

def test_serve_declares_the_turn_deadline_and_refuses_a_bad_one(capsys, monkeypatch):
    from ck3bench.env import server
    seen = {}

    class Probe(server.EnvServer):
        def __init__(self, env, **kw):
            seen.update(kw)
            super().__init__(env, **kw)

        def serve_forever(self):
            self.httpd.server_close()
            raise KeyboardInterrupt
    monkeypatch.setattr(server, "EnvServer", Probe)
    assert cli.main(["serve", "fake-solo", "--port", "0", "--turn-timeout-s", "45"]) == cli.EXIT_OK
    assert seen["turn_timeout_s"] == 45.0 and _rows(capsys.readouterr().out)[0]["turn_timeout_s"] == 45.0
    assert cli.main(["serve", "fake-solo", "--port", "0", "--turn-timeout-s", "0"]) == cli.EXIT_REFUSED
    assert _rows(capsys.readouterr().out)[0]["code"] == "bad_request"


def test_play_runs_mixed_policies_with_the_scripted_model_agent(tmp_path, capsys):
    from ck3bench.env.trajectory import check_trajectory, read_trajectory
    out = tmp_path / "play.jsonl"
    argv = ["play", "fake-council", "--seed", "3", "--seat", "king=agent-scripted", "--seat", "duke1=noop",
            "--seat", "duke2=observe-then-gift", "--turn-timeout-s", "30", "--out", str(out)]
    assert cli.main(argv) == cli.EXIT_OK
    row = _rows(capsys.readouterr().out)[-1]
    assert row["ok"] and row["fake"] and row["end_reason"] == "horizon" and row["steps"] == 8
    assert row["seats"] == {"king": "agent-scripted", "duke1": "noop", "duke2": "observe-then-gift"}
    recs = read_trajectory(out)
    assert check_trajectory(recs) == []
    steps = [r for r in recs if r["kind"] == "step"]
    assert all(s["turns"]["king"]["status"] == "end_turn" for s in steps)
    assert all(s["usage"]["king"]["model_calls_unreported"] == 2 for s in steps), "a scripted model reports none"
    king = [r["call"]["tool_name"] for r in recs if r["kind"] == "call" and r["seat"] == "king"]
    assert king == ["observe"] * 8


def test_play_refuses_a_bad_seat_pair_and_a_seat_nobody_drives(capsys):
    base = ["play", "fake-council", "--seed", "1", "--turn-timeout-s", "5"]
    assert cli.main(base + ["--seat", "king=chess"]) == cli.EXIT_REFUSED
    assert _rows(capsys.readouterr().out)[-1]["code"] == "bad_request"
    assert cli.main(base + ["--seat", "king=noop", "--seat", "duke1=noop"]) == cli.EXIT_REFUSED
    assert _rows(capsys.readouterr().out)[-1]["code"] == "policy_unknown"


def test_play_with_an_external_seat_serves_it_for_a_shim(tmp_path):
    """The documented Claude Code route, end to end without Claude Code: `play`
    with --external prints the server's URL first; a SeatShim (the logic behind
    `ck3bench env shim`) plays that seat by ending its turns."""
    import os
    import subprocess
    import sys
    from pathlib import Path
    from ck3bench.env.mcp_shim import SeatShim
    from ck3bench.env.server import EnvClient
    repo = Path(__file__).resolve().parents[2]
    env = {**os.environ, "PYTHONPATH": str(repo) + os.pathsep + os.environ.get("PYTHONPATH", "")}
    p = subprocess.Popen([sys.executable, "-m", "ck3bench.cli", "env", "play", "fake-council", "--seed", "2",
                          "--seat", "duke1=noop", "--seat", "duke2=noop", "--external", "king",
                          "--turn-timeout-s", "20"], stdout=subprocess.PIPE, text=True, env=env, cwd=tmp_path)
    try:
        first = json.loads(p.stdout.readline())
        assert first["external"] == ["king"] and first["serving"].startswith("http://127.0.0.1:")
        cl = EnvClient(first["serving"], timeout=30)
        shim = SeatShim(cl, "king", 30)
        import time
        for _ in range(200):
            if cl.state()["open"]:
                break
            time.sleep(0.05)
        ended = 0
        while True:
            obs, err = shim.call("seat_observation", {})
            assert not err
            res, err = shim.call("end_turn", {"summary": "ok"})
            assert not err, res
            ended += 1
            if res["episode_over"]:
                break
        last = json.loads(p.stdout.readline())
        assert p.wait(30) == 0
    finally:
        if p.poll() is None:
            p.kill()
    assert ended == 8 and last["ok"] and last["end_reason"] == "horizon" and last["runner_timeouts"] == 0


def test_shim_hands_its_arguments_to_the_mcp_shim(monkeypatch):
    from ck3bench.env import mcp_shim
    seen = []
    monkeypatch.setattr(mcp_shim, "main", lambda argv: seen.append(argv) or 0)
    assert cli.main(["shim", "--url", "http://127.0.0.1:9", "--seat", "king", "--step-wait-s", "900"]) == 0
    assert seen == [["--url", "http://127.0.0.1:9", "--seat", "king", "--step-wait-s", "900.0"]]
