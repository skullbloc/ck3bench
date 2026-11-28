"""`series publish` (ck3bench.harness.publish): the publishable copy of a run directory names no machine, no account
and no private path, keeps what the scorer reads, and refuses (writing nothing) when a private pattern survives.

The run directory here is written by hand with every kind of private value a real one carries: the host name
`series start` records, absolute paths under this host's configured directories and under a home directory, the
agent CLI's init event (working directory, key source) and its rate-limit events (account limits), session ids."""
import json
import os
import re
from pathlib import Path

import pytest

from ck3bench.harness import publish as P
from ck3bench.harness import score as S

HOST = "build-box-7"
SID = "deadbeef-dead-beef-dead-beefdeadbeef"          # synthetic ids
#: another user's home and an e-mail address, spelled in pieces so this file does not carry them whole
OTHER_HOME = "/var" + "/home/" + "other"
MAIL = "someone" + "@" + "example.org"


def _run(tmp: Path) -> tuple[Path, dict]:
    home = tmp / "home" / "someone"
    runs = home / "runs"
    run = runs / "demo-a01-a"
    run.mkdir(parents=True)
    env = {"HOME": str(home), "RATE_RUNS": str(runs), "RATE_SO": str(home / "stage" / "libck3_bridge.so"),
           "RATE_PROTECTED_DIR": str(home / ".local" / "share" / "game")}
    (run / "run.json").write_text(json.dumps({
        "schema": "ck3bench.series.run/1", "run_id": "demo-a01-a", "host": "a",
        "expectation": {"path": str(home / "series" / "expect.json"), "sha256": "0" * 64},
        "start": {"host": HOST, "bridge_so": {"path": env["RATE_SO"], "sha256": "1" * 64}},
        "end": {"host": HOST, "seen": {"x": f"read at {run}/trajectory.jsonl"}},
        "validity": {"valid": False, "reasons": ["witness_absent:game_start"]}}))
    (run / "end.json").write_text(json.dumps({"end_reason": "seat_stopped", "at": "2026-01-01T00:10:00Z",
                                              "note": f"{OTHER_HOME}/x and /root/y"}))
    (run / "turns.jsonl").write_text(json.dumps({"sent_utc": "2026-01-01T00:05:00Z", "session_id": SID}) + "\n")
    ev = [{"type": "system", "subtype": "init", "session_id": SID, "cwd": str(run), "apiKeySource": "none",
           "model": "claude-opus-5-5", "tools": ["mcp__ck3seat__observe"],
           "mcp_servers": [{"name": "ck3seat", "status": "connected", "path": str(home)}]},
          {"type": "rate_limit_event", "session_id": SID, "uuid": "cafebabe-cafe-babe-cafe-babecafebabe",
           "rate_limit_info": {"status": "allowed", "resetsAt": 1795518000, "utilization": 0.74,
                               "overageStatus": "rejected", "unifiedWindows": {"seven_day": {"utilization": 0.74}}}},
          {"type": "rate_limit_event", "session_id": SID,
           "rate_limit_info": {"status": "rejected", "rateLimitType": "seven_day"}},
          {"type": "result", "subtype": "success", "is_error": False, "session_id": SID, "total_cost_usd": 0.5}]
    (run / "transcript.jsonl").write_text("".join(json.dumps(e) + "\n" for e in ev))
    (run / "post.tsv").write_text(f"own_before\t0\t2026-01-01T00:00:00Z\troot={env['RATE_PROTECTED_DIR']} files=3\n")
    (run / "pressure.tsv").write_text("\t".join(("utc", "mem_full10", "io_full10", "cpu_some10", "memavail_kb"))
                                      + "\n2026-01-01T00:06:00Z\t0\t0\t1\t9000000\n")
    return run, env


def test_the_copy_names_no_host_no_path_no_account_and_no_session(tmp_path, monkeypatch):
    run, env = _run(tmp_path)
    monkeypatch.setattr(P.socket, "gethostname", lambda: "this-host")
    dest = tmp_path / "pub"
    rec = P.publish(run, dest, env)
    text = "\n".join(p.read_text() for p in dest.iterdir() if p.name != "PUBLISHED.json")    # the record names the rules
    for bad in (HOST, str(tmp_path), OTHER_HOME, "/root/y", SID, "apiKeySource", "resetsAt", "utilization",
                "unifiedWindows", "rateLimitType", "cafebabe"):
        assert bad not in text, bad
    rj = json.loads((dest / "run.json").read_text())
    assert rj["start"]["host"] == "<host name>" and rj["host"] == "a"
    assert rj["start"]["bridge_so"]["path"] == "<RATE_SO>" and rj["expectation"]["path"] == "<expectation>"
    ev = [json.loads(x) for x in (dest / "transcript.jsonl").read_text().splitlines()]
    assert ev[0] == {"type": "system", "subtype": "init", "session_id": "session-1", "model": "claude-opus-5-5",
                     "tools": ["mcp__ck3seat__observe"], "mcp_servers": [{"name": "ck3seat", "status": "connected"}]}
    assert ev[1] == {"type": "rate_limit_event", "rate_limit_info": {"status": "allowed"}, "session_id": "session-1"}
    assert ev[3]["total_cost_usd"] == 0.5
    assert json.loads((dest / "turns.jsonl").read_text())["session_id"] == "session-1"
    assert "<RATE_PROTECTED_DIR>" in (dest / "post.tsv").read_text()
    files = {f["file"]: f for f in rec["files"]}
    assert "absent" in files["trajectory.jsonl"]["state"] and files["run.json"]["published_sha256"]
    assert json.loads((dest / "PUBLISHED.json").read_text())["placeholders"]["<host name>"] >= 2
    assert not [v for v in env.values() if v in (dest / "PUBLISHED.json").read_text()]


def test_the_scorer_reads_the_same_resource_evidence_from_the_copy(tmp_path):
    """The resource rule's evidence (the rate-limit statuses, the stop window, the pressure samples) is kept: the
    copy's verdict is the run's."""
    run, env = _run(tmp_path)
    dest = tmp_path / "pub"
    P.publish(run, dest, env)
    a, b = S.resource_rule(run), S.resource_rule(dest)
    assert a["transcript"]["whole_run"] == b["transcript"]["whole_run"] and a["transcript"]["whole_run"]["limit_hits"] == 1
    assert (a["verdict"], a["reasons"]) == (b["verdict"], b["reasons"])


def test_the_scorer_judges_the_end_row_from_the_same_runner_start_in_the_copy(tmp_path):
    """The runner's recorded start (score.RUNNER_START) is published beside run.json and the trajectory, so the copy's
    end row is judged by the deadline the run's is (score.end_row_budget): here a row 99 s after a runner started 40 s
    after the launch, in time by the runner's start and late by the launch."""
    run, env = _run(tmp_path)
    rj = json.loads((run / "run.json").read_text())
    rj.update(launch_utc="2026-01-01T00:00:00Z", runner_flags=["--wall-budget-s", "100"])
    (run / "run.json").write_text(json.dumps(rj))
    (run / "trajectory.jsonl").write_text(json.dumps({"kind": "header"}) + "\n" + json.dumps(
        {"kind": "end", "reason": "horizon", "wall_utc": "2026-01-01T00:02:19Z"}) + "\n")
    (run / S.RUNNER_START).write_text(json.dumps({"start_s": S._utc("2026-01-01T00:00:40Z"),
                                                  "start_utc": "2026-01-01T00:00:40Z"}))
    dest = tmp_path / "pub"
    P.publish(run, dest, env)
    assert S.end_row_budget(run) == S.end_row_budget(dest) == "episode_over"


def test_CONTROL_a_private_value_no_rule_replaces_refuses_and_writes_nothing(tmp_path):
    run, env = _run(tmp_path)
    (run / "end.json").write_text(json.dumps({"end_reason": "x", "by": MAIL}))
    dest = tmp_path / "pub"
    with pytest.raises(P.PublishRefused, match=r"end.json:\d+: e-mail address"):
        P.publish(run, dest, env)
    assert not dest.exists()
    assert P.main([str(run), str(dest)]) == 5 and not dest.exists()


def test_CONTROL_the_check_runs_on_the_copy_it_writes(tmp_path, monkeypatch):
    """With the rewrite turned off, the same run is refused on every pattern the rewrite removes: the refusal list
    reads the output, not the input."""
    run, env = _run(tmp_path)
    monkeypatch.setattr(P, "rewrite_file", lambda name, raw, rw: raw)
    with pytest.raises(P.PublishRefused) as e:
        P.publish(run, tmp_path / "pub", env)
    for what in ("host name", "uuid", "account key", "home directory", "path of RATE_SO"):
        assert what in str(e.value), what
    assert not (tmp_path / "pub").exists()


def test_an_existing_destination_and_a_missing_run_are_refused(tmp_path):
    run, env = _run(tmp_path)
    (tmp_path / "pub").mkdir()
    with pytest.raises(P.PublishRefused, match="exists"):
        P.publish(run, tmp_path / "pub", env)
    with pytest.raises(P.PublishRefused, match="not a directory"):
        P.publish(tmp_path / "nope", tmp_path / "pub2", env)


# ── every class the rewrite catches, planted in the files it reaches ─────────────────────────────────────────────
#: the samples, spelled in pieces so this file does not carry a whole one
LIMIT_TEXTS = ("You've hit your " + "limit · resets 5pm (Europe/Paris)",
               "Claude AI usage " + "limit reached|" + "1795518000",
               "API Error: 429 This request would exceed the rate " + "limit for your organization (id) of 30,000 "
               "input tokens per minute")
PRIVATE = {"private path": ("/mnt" + "/data/runs/x", "/srv" + "/ck3/a", "/opt" + "/tools/bin"),
           "socket path": ("/tmp" + "/ck3-agent.sock",),
           "ipv4 address": ("10.0" + ".0.7", "192.168" + ".1.20"),
           "user id": ("uid=1000" + "(someone)",),
           "windows user directory": ("C:" + "\\Users\\winuser\\Documents",),
           "session link": ("https://claude" + ".ai/code/session_" + "0123ABCdef",)}
#: what must stay as it is: game dates, a game version, a price
KEPT = ("1066.9.15", "1096.09.15", "CK3 1.18.3.1", "version 1.18.0.2", "350 gold")


def _planted(tmp: Path) -> tuple[Path, dict, str]:
    run, env = _run(tmp)
    host_variants = (HOST.upper(), HOST + "-ck3", HOST + "_backup", HOST.title())
    other = "; ".join(v for vs in PRIVATE.values() for v in vs)
    with (run / "transcript.jsonl").open("a") as f:
        f.write(json.dumps({"type": "system", "subtype": "api_retry", "session_id": SID, "error": LIMIT_TEXTS[2],
                            "attempt": 1}) + "\n")
        f.write(json.dumps({"type": "assistant", "session_id": SID, "message": {
            "model": "<synthetic>", "content": [{"type": "text", "text": LIMIT_TEXTS[0]}]}}) + "\n")
        f.write(json.dumps({"type": "result", "subtype": "success", "is_error": True, "session_id": SID,
                            "result": LIMIT_TEXTS[1]}) + "\n")
        f.write(json.dumps({"type": "assistant", "session_id": SID, "message": {"content": [{"type": "text", "text":
            "on " + " and ".join(host_variants) + f" at {other}; kept: " + ", ".join(KEPT)}]}}) + "\n")
    with (run / "turns.jsonl").open("a") as f:
        f.write(json.dumps({"turn": 2, "session_id": SID, "result_text": LIMIT_TEXTS[1], "is_error": True}) + "\n")
    (run / "post.tsv").write_text((run / "post.tsv").read_text() + f"note\t0\t{other}\t{HOST.upper()}\n")
    return run, env, other


def _leaks(dest: Path) -> list[str]:
    """An independent reading of the copy (this test's own patterns, not the module's)."""
    text = "\n".join(p.read_text() for p in dest.iterdir() if p.name != "PUBLISHED.json")
    out = [v for vs in PRIVATE.values() for v in vs if v in text or v.replace("\\", "\\\\") in text]
    out += [h for h in ("build-box", "BUILD-BOX", "Build-Box") if h in text]
    out += [t for t in LIMIT_TEXTS if t in text or "1795518000" in text or "resets 5pm" in text]
    return out


def test_every_class_is_rewritten_and_the_copy_is_written(tmp_path, monkeypatch):
    run, env, _ = _planted(tmp_path)
    monkeypatch.setattr(P.socket, "gethostname", lambda: "this-host")
    dest = tmp_path / "pub"
    rec = P.publish(run, dest, env)
    assert _leaks(dest) == []
    text = (dest / "transcript.jsonl").read_text()
    assert all(k in text for k in KEPT), "a date, a version or a price was rewritten"
    assert text.count("<host name>") >= 4 and "<private path>" in text and "<socket>" in text
    assert "<ip address>" in text and "uid=<id>(<name>)" in text and "<session link>" in text
    # the scorer's reading of the limit texts is kept
    assert S.transcript_evidence(run) == S.transcript_evidence(dest)
    assert S.resource_rule(run) == S.resource_rule(dest)
    turns = [json.loads(x) for x in (dest / "turns.jsonl").read_text().splitlines()]
    assert turns[-1]["result_text"].startswith(P.LIMIT_PLACEHOLDER)
    assert {"<limit text removed> (limit text, the whole string)", "<ip address>"} <= set(rec["placeholders"])


def test_a_session_id_is_session_n_wherever_it_first_stands(tmp_path):
    """The session id first stands in a text (run.json, before any session_id key): it is still session-1."""
    run, env = _run(tmp_path)
    rj = json.loads((run / "run.json").read_text())
    rj["end"]["seen"]["log"] = f"session {SID} ended"
    (run / "run.json").write_text(json.dumps(rj))
    dest = tmp_path / "pub"
    P.publish(run, dest, env)
    assert json.loads((dest / "run.json").read_text())["end"]["seen"]["log"] == "session session-1 ended"
    assert "id-1" not in (dest / "run.json").read_text()


def test_a_host_name_that_is_a_word_is_replaced_in_text_too(tmp_path, monkeypatch):
    """Said in the series' document: an ordinary-word host name is replaced in the game's text as well, counted."""
    run, env = _run(tmp_path)
    rj = json.loads((run / "run.json").read_text())
    rj["start"]["host"] = rj["end"]["host"] = "castle"
    (run / "run.json").write_text(json.dumps(rj))
    (run / "end.json").write_text(json.dumps({"end_reason": "x", "note": "the Castle of Dublin; castles stay"}))
    monkeypatch.setattr(P.socket, "gethostname", lambda: "this-host")
    rec = P.publish(run, tmp_path / "pub", env)
    assert json.loads((tmp_path / "pub" / "end.json").read_text())["note"] == "the <host name> of Dublin; castles stay"
    assert rec["placeholders"]["<host name>"] >= 3


@pytest.mark.parametrize("what,attr", [("private path", "PRIVATE_PATH"), ("socket path", "SOCKET"),
                                       ("windows user directory", "WIN_HOME"), ("user id", "UID"),
                                       ("session link", "SESSION_LINK"), ("limit text", "LIMIT_TEXT_RX")])
def test_CONTROL_a_class_left_out_of_the_rewrite_refuses_by_its_name(tmp_path, monkeypatch, what, attr):
    """Mutant: the class's rewrite turned off (its check left on): the copy is refused, naming the class and the
    file, never the value; nothing is written."""
    run, env, other = _planted(tmp_path)
    monkeypatch.setattr(P, attr, re.compile(r"(?!x)x"))
    with pytest.raises(P.PublishRefused) as e:
        P.publish(run, tmp_path / "pub", env)
    assert f": {what}" in str(e.value) and not (tmp_path / "pub").exists()
    assert not [v for vs in PRIVATE.values() for v in vs if v in str(e.value)]
    assert not [t for t in LIMIT_TEXTS if t in str(e.value)] and "1795518000" not in str(e.value)


def test_CONTROL_ipv4_and_host_left_out_refuse(tmp_path, monkeypatch):
    run, env, _ = _planted(tmp_path)
    monkeypatch.setattr(P, "ipv4_addresses", lambda s: [])
    with pytest.raises(P.PublishRefused, match=r"transcript.jsonl:\d+: ipv4 address"):
        P.publish(run, tmp_path / "pub", env)
    monkeypatch.undo()

    class NoHost(P.Rewriter):
        def text(self, s):
            keep, self.host_rxs = self.host_rxs, []
            try:
                return super().text(s)
            finally:
                self.host_rxs = keep
    monkeypatch.setattr(P, "Rewriter", NoHost)
    with pytest.raises(P.PublishRefused, match=r"post.tsv:\d+: host name") as e:
        P.publish(run, tmp_path / "pub", env)
    assert HOST.upper() not in str(e.value)


def test_CONTROL_an_exact_spelling_rule_misses_the_variants():
    """A rule of the exact spelling (exact case, a name joined by - or _ skipped) leaves every variant: host_rx is
    what catches them."""
    old = re.compile(r"(?<![\w.-])" + re.escape(HOST) + r"(?![\w-])")
    variants = (HOST.upper(), HOST + "-ck3", HOST + "_backup", HOST.title())
    assert not [v for v in variants if old.search(v)]
    assert all(P.host_rx(HOST).search(v) for v in variants)


# ── the classes of the second set: each planted, each rewritten, each refused when its rule is left out ─────────
#: rewritten classes: (class, the refusal's name for it, samples), each spelled in pieces
REWRITTEN = (
    ("limit text", "LIMIT_TEXT_RX", ("You've used 92% of your weekly " + "limit · resets Thu",
                                     "Opus " + "limit · resets Nov 27", "Credit " + "balance is too low",
                                     "You hit the weekly" + "-limit")),
    ("ipv4 address", None, ("connected to 10.20" + ".30.40.", "ck3 192.168" + ".7.9")),
    ("ipv6 address", "IPV6_ADDRESS", ("fe80::1ff:fe23" + ":4567:890a", "2001:db8:85a3" + "::8a2e:370:7334")),
    ("mac address", "MAC_ADDRESS", ("ether 00:1a:2b" + ":3c:4d:5e", "ether 001a.2b3c" + ".4d5e")),
    ("user at host", "USER_AT_HOST", ("ssh deploy" + "@buildbox",)),
    ("user or host variable", "ENV_IDENTITY", ("USER=" + "deploy LOGNAME=" + "deploy", "HOSTNAME=" + "rack-07",
                                               "user=" + "deploy hostname=" + "rack-07", '{"HOSTNAME": ' + '"rack-07"}',
                                               '{"hostname": ' + '"rack-07"}')),
    ("tmp path", "TMP_PATH", ("/tmp" + "/ck3-shared/q/bridge.log", "/tmp" + "/claude-999/-proj/tasks/x.output",
                              "/var/tmp" + "/q/x", "/dev/shm" + "/q-ck3")),
    ("private path", "PRIVATE_PATH", ("/Volumes" + "/Data/q/ck3", "/workspace" + "/q/ck3")),
    ("network path", "UNC_PATH", ("\\\\" + "fileserver01\\share\\q",)),
    ("network path", "NET_SHARE", ("smb://" + "fileserver01/share", "//" + "fileserver01/share/x")),
    ("drive link", "DRIVE_LINK", ("https://drive.google" + ".com/drive/folders/1AbCdEf",)),
)
#: a home path behind an escape: JSON's slash, a unicode slash, percent encoding (the home directory class)
ESCAPED = ("\\/var\\/home\\/" + "q2\\/x", "\\u002fvar\\u002fhome\\u002f" + "q2", "file%3A%2F%2F%2Fvar%2Fhome%2F" + "q2",
           "%252Fvar%252Fhome%252F" + "q2")


def _planted2(tmp: Path) -> tuple[Path, dict]:
    run, env = _run(tmp)
    text = "; ".join(v for _, _, vs in REWRITTEN for v in vs if not v.startswith(("You", "Opus", "Credit")))
    with (run / "transcript.jsonl").open("a") as f:
        f.write(json.dumps({"type": "assistant", "session_id": SID, "message": {"content": [
            {"type": "text", "text": f"seen: {text}"}, *({"type": "text", "text": t} for t in REWRITTEN[0][2]),
            *({"type": "text", "text": t} for t in ESCAPED)]}}) + "\n")
    with (run / "turns.jsonl").open("a") as f:
        f.write(json.dumps({"turn": 2, "rate_limit_info": {"status": "rejected", "overageResetsAt": 1795519999,
                                                           "windowResetsAt": 1, "status_detail": "x"},
                            "result_excerpt": REWRITTEN[0][2][0], "env": {"USER": "deploy"}}) + "\n")
    (run / "pressure.tsv").write_text((run / "pressure.tsv").read_text() + f"#\t{ESCAPED[0]}\t{text}\n")
    return run, env


def test_every_class_of_the_second_set_is_rewritten_and_the_copy_is_written(tmp_path, monkeypatch):
    run, env = _planted2(tmp_path)
    monkeypatch.setattr(P.socket, "gethostname", lambda: "this-host")
    dest = tmp_path / "pub"
    rec = P.publish(run, dest, env)
    text = "\n".join(p.read_text() for p in dest.iterdir() if p.name != "PUBLISHED.json")   # the record names keys
    for _, _, vs in REWRITTEN:
        for v in vs:
            core = v.split(" ", 1)[-1] if v.startswith(("connected", "ck3 ", "ether", "ssh")) else v
            assert core.rstrip(".") not in text and core.replace("\\", "\\\\") not in text, v
    for v in ("q2", "overageResetsAt", "windowResetsAt", "92%", "1795519999", "deploy", "rack-07"):
        assert v not in text, v
    assert "status_detail" in text and '"status": "rejected"' in text           # what the scorer reads is kept
    for ph in ("<ip address>", "<mac address>", "<user>@<host>", "<tmp path>", "<private path>", "<network path>",
               "<drive link>", "<HOME>", "<removed>"):
        assert ph in text, ph
    assert S.resource_rule(run) == S.resource_rule(dest)
    assert any(k.startswith("escaped") for k in rec["placeholders"])


@pytest.mark.parametrize("what,attr", [(w, a) for w, a, _ in REWRITTEN if a])
def test_CONTROL_a_second_set_class_left_out_of_the_rewrite_refuses_by_its_name(tmp_path, monkeypatch, what, attr):
    run, env = _planted2(tmp_path)
    monkeypatch.setattr(P, attr, re.compile(r"(?!x)x"))
    with pytest.raises(P.PublishRefused) as e:
        P.publish(run, tmp_path / "pub", env)
    assert f": {what}" in str(e.value) and not (tmp_path / "pub").exists(), str(e.value)
    assert not [v for _, _, vs in REWRITTEN for v in vs if v in str(e.value)]


def test_CONTROL_escapes_left_unread_by_the_rewrite_are_refused_as_escaped(tmp_path):
    """Mutant: the rewrite reads no escape (its _plain is the identity); the refusal still reads each escaped
    spelling, so the home path behind it refuses the copy."""
    run, env = _planted2(tmp_path)

    class NoPlain(P.Rewriter):
        def _plain(self, s):
            return s
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(P, "Rewriter", NoPlain)
        with pytest.raises(P.PublishRefused, match=r"home directory \(escaped\)"):
            P.publish(run, tmp_path / "pub", env)


def test_CONTROL_account_keys_of_the_family_left_in_are_refused(tmp_path, monkeypatch):
    run, env = _planted2(tmp_path)
    monkeypatch.setattr(P, "ACCOUNT_KEYS", frozenset())
    monkeypatch.setattr(P, "ACCOUNT_KEY_FAMILY", re.compile(r"(?!x)x"))
    with pytest.raises(P.PublishRefused, match=r"turns.jsonl:\d+: account key"):
        P.publish(run, tmp_path / "pub", env)


def test_user_names_are_replaced_and_refused_when_left(tmp_path, monkeypatch):
    """The account's name (from HOME, USER and every home path in the files) where it stands alone: a run
    directory named with it, a value split from its path. Mutant: names left out of the rewrite refuse."""
    run, env = _run(tmp_path)
    named = run.parent / f"{HOST}-someone-demo-a01-a"
    run.rename(named)
    (named / "end.json").write_text(json.dumps({"end_reason": "x", "split": {"a": "/home", "b": "someone"}}))
    monkeypatch.setattr(P.socket, "gethostname", lambda: "this-host")
    rec = P.publish(named, tmp_path / "pub", env)
    assert rec["run"] == "<host name>-<user name>-demo-a01-a"
    pub = (tmp_path / "pub" / "PUBLISHED.json").read_text()
    assert HOST not in pub and "someone" not in pub
    assert json.loads((tmp_path / "pub" / "end.json").read_text())["split"]["b"] == "<user name>"

    class NoUsers(P.Rewriter):
        def _names(self, s):
            keep, self.user_rxs = self.user_rxs, []
            try:
                return super()._names(s)
            finally:
                self.user_rxs = keep
    monkeypatch.setattr(P, "Rewriter", NoUsers)
    with pytest.raises(P.PublishRefused, match=r"end.json:\d+: user name"):
        P.publish(named, tmp_path / "pub2", env)


def test_CONTROL_the_record_is_read_by_the_refusal(tmp_path, monkeypatch):
    """Mutant: the run directory's name written raw into PUBLISHED.json (its rewrite skipped): refused, naming the
    record; nothing is written."""
    run, env = _run(tmp_path)
    named = run.parent / f"{HOST}-demo-a01-a"
    run.rename(named)

    class RawName(P.Rewriter):
        def text(self, s):
            return s if s == named.name else super().text(s)
    monkeypatch.setattr(P, "Rewriter", RawName)
    with pytest.raises(P.PublishRefused, match=r"PUBLISHED.json:\d+: host name"):
        P.publish(named, tmp_path / "pub", env)
    assert not (tmp_path / "pub").exists()


#: keys and tokens: never rewritten, always refused (each spelled in pieces)
SECRETS = ("-----BEGIN OPENSSH " + "PRIVATE KEY-----", "sk-ant-" + "zzzzzzzzzzzzzzzzzzzzzzzz",
           "ghp_" + "Zzzzzzzzzzzzzzzzzzzzzzzzzzzzz", "AKIA" + "ZZZZZZZZZZZZZZZZ", "xoxb-" + "zzzzzzzzzz-zzz",
           "Authorization: Bearer " + "zzzzzzzzzzzzzzzzzzzz", "eyJzzzzzzzzzz" + ".eyJzzzzzzzzz.sig",
           "api_key=" + "Zzzzzzzzzzzzzzzz", "-----BEGIN PGP " + "PRIVATE KEY BLOCK-----", "AIza" + "Z" * 35,
           "aws_secret_access_key = " + "Zzzzzzzzzzzz/Zzzzzzzzz")
#: the shapes the key-name rule reads past a leading date, digits-only values, and the further key shapes: each
#: refused, and each one a narrower rule misses (KEY_RULE_NARROW: a value beginning with a date exempt, digits only
#: exempt, no `sk_live_`, no `*_private_key`)
SECRETS_NAMED = ("api_key=" + "2026-11-25T10:00:00Zzzzzzzzz1234", '{"client_secret": "' + '2026-01-01zZ9zZ7zZ2zZ4zQ"}',
                 "client_secret: " + "2026-01-01-z9Zz8Zz7Zz6Zz5Zz", "password=" + "123456789012345",
                 "sk_live_" + "Zzzzzzzzzzzzzzzzzzzzzz", "rk_test_" + "Zzzzzzzzzzzzzzzz9", "DEPLOY_PRIVATE_KEY=" + "Zx9zzzzzzzzzzzzz")
KEY_RULE_NARROW = re.compile(
    r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY(?: BLOCK)?-----|\bsk-(?:ant-)?[A-Za-z0-9_-]{20,}"
    r"|\bgh[pousr]_[A-Za-z0-9]{20,}|\bAIza[0-9A-Za-z_-]{35}(?![0-9A-Za-z_-])"
    r"|\bgithub_pat_\w{20,}|\b(?:AKIA|ASIA)[0-9A-Z]{16}\b|\bxox[abposr]-[\w-]{10,}|(?i:\bbearer\s+[\w.~+/-]{16,})"
    r"|\beyJ[\w-]{8,}\.eyJ[\w-]{8,}\.|(?i:\b[\w-]*(?:api[_-]?key|secret|token|passw(?:or)?d|credential)[\w-]*[\"']?\s*[=:]"
    r"\s*[\"']?(?!<|\d{4}-\d\d-\d\d)(?=[^\s\"',}]*[A-Za-z])[^\s\"',}]{12,})")
#: what is not a key: token counts, the census placeholder, a sha256, a short value
NOT_SECRETS = ('"input_tokens": 123456789', '"cache_read_input_tokens": 98765432', '"token": "<census id>"',
               '"credential_expires_at": "2026-12-21T09:49:00Z"', "sig zorkquux/zorkquux+//zorkquuxzork/quux/zorkquux",
               "https://example.org/a/b",
               "sha256 " + "ab" * 32, "token: short", '"secret_expires": "2026-12-21"',
               '"credential_rotated": "2026-12-21 09:49:00+01:00"')


@pytest.mark.parametrize("secret", SECRETS + SECRETS_NAMED)
def test_a_key_or_a_token_refuses_the_copy(tmp_path, secret):
    run, env = _run(tmp_path)
    (run / "end.json").write_text(json.dumps({"end_reason": "x", "note": secret}))
    with pytest.raises(P.PublishRefused, match=r"end.json:\d+: key or token") as e:
        P.publish(run, tmp_path / "pub", env)
    assert secret not in str(e.value) and not (tmp_path / "pub").exists()


@pytest.mark.parametrize("secret", SECRETS_NAMED)
def test_MUTANT_a_narrower_key_rule_misses_each_named_shape(secret):
    """Each named shape is SECRET's, and a narrower rule (a value that begins with a date exempt, digits only exempt,
    no `sk_live_`/`rk_test_`, no `*_private_key`) lets it through: each named shape needs SECRET's wider branch."""
    assert P.SECRET.search(secret) and not KEY_RULE_NARROW.search(secret)


#: a quoted value after a key's name, spaces in it: read whole (QUOTED_KEY_VALUE), and the further token shapes
SECRETS_QUOTED = ('password = "' + 'correct horse battery staple"', "{'db_password': '" + "a long phrase with spaces'}",
                  '"client_secret": "' + 'two words and more"')
#: a quote of the other kind inside the value, a value that begins with a date, the `=>` and `--flag` forms
SECRETS_QUOTED_INNER = ('password = "' + "don't tell anyone ever\"", "password = '" + "it\"s a long passphrase'",
                        'db_password: "' + '2026-01-01 then a long tail"', 'password => "' + 'a long pass phrase"',
                        '--password "' + 'a long pass phrase"')
#: a narrower quoted-value rule: its value class stops at either quote, and a date at its start exempts the value
OLD_QUOTED = re.compile(r"(?i:\b[\w-]*(?:api[_-]?key|secret|passw(?:or)?d|credential|private[_-]?key)[\w-]*[\"']?\s*[=:]"
                        r"\s*[\"'](?!<|\d{4}-\d\d-\d\d(?:[T ]\d\d:\d\d(?::\d\d(?:\.\d+)?)?)?(?:Z|[+-]\d\d:?\d\d)?"
                        r"(?=[\"',}\s]|$))[^\"'\n]{12,}[\"'])")
SECRETS_TOKENS = ("hf_" + "Zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz", "glpat-" + "Zzzzzzzzzzzzzzzzzzzz",
                  "npm_" + "Zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz")


@pytest.mark.parametrize("secret", SECRETS_QUOTED + SECRETS_QUOTED_INNER + SECRETS_TOKENS)
def test_a_quoted_passphrase_and_the_further_tokens_refuse_the_copy(tmp_path, secret):
    """A value of 12 characters or more after a key's name, quoted and holding spaces, refuses the copy in a JSON
    file and in a text file; so do the further token shapes. MUTANT: without QUOTED_KEY_VALUE a quoted value is
    read only up to its first space and passes. CONTROL: a short quoted value passes."""
    run, env = _run(tmp_path)
    (run / "post.tsv").write_text(f"note\t0\t2026-01-01T00:00:00Z\t{secret}\n")
    with pytest.raises(P.PublishRefused, match=r"post.tsv:\d+: key or token"):
        P.publish(run, tmp_path / "pub", env)
    if secret in SECRETS_QUOTED + SECRETS_QUOTED_INNER:
        narrow = re.compile(P.SECRET.pattern.replace(P.QUOTED_KEY_VALUE, ""))
        assert P.SECRET.search(secret) and not narrow.search(secret)
    if secret in SECRETS_QUOTED_INNER:
        # MUTANT: the narrower quoted-value rule (OLD_QUOTED) lets each of these through
        assert not re.compile(P.SECRET.pattern.replace(P.QUOTED_KEY_VALUE, "|" + OLD_QUOTED.pattern)).search(secret)
    (run / "post.tsv").write_text('note\t0\t2026-01-01T00:00:00Z\tpassword = "short one"\n')
    P.publish(run, tmp_path / "pub2", env)


def test_MUTANT_a_hostname_key_in_lower_case_is_read_and_capitals_only_misses_it():
    """`"hostname": "..."` as text in any capitalisation is a host variable; read in capitals only (the JSON-text
    branch as it was) it passes. `"host"` and `"user"` are ordinary keys and are read in capitals only."""
    caps = re.compile(r"[\"'](?:" + "|".join(P.IDENTITY_ENV_KEYS) + r")[\"']\s*:\s*[\"']?(?!<)[^\s\"',;}]+")
    for t in ('{"hostname": ' + '"otherbox-07"}', '{"Hostname": ' + '"otherbox-07"}', '{"username": ' + '"deploy"}'):
        assert P.ENV_IDENTITY.search(t) and not caps.search(t), t
    for t in ('{"host": "a"}', '{"user": "x"}'):
        assert not P.ENV_IDENTITY.search(t), t


def test_CONTROL_counts_and_placeholders_are_not_keys(tmp_path):
    run, env = _run(tmp_path)
    (run / "accounting.json").write_text("{" + ", ".join(x if ":" in x and x.startswith('"') else f'"n{i}": "{x}"'
                                                         for i, x in enumerate(NOT_SECRETS)) + "}")
    P.publish(run, tmp_path / "pub", env)
    # a `//` inside a base64 blob (a session's signature) and a web link are no network share: kept as written
    pub = (tmp_path / "pub" / "accounting.json").read_text()
    assert "<network path>" not in pub and "quux+//zorkquuxzork" in pub


def test_a_symbolic_link_is_refused_and_its_target_never_read(tmp_path):
    """A listed file that is a link (here to a key file outside the run): refused by name, nothing written.
    CONTROL: the same content as a regular file is read (and refused for what it is: a key)."""
    run, env = _run(tmp_path)
    target = tmp_path / "elsewhere.txt"
    target.write_text("plain\n")
    (run / "session_stdin.jsonl").symlink_to(target)
    with pytest.raises(P.PublishRefused, match=r"session_stdin.jsonl: a symbolic link"):
        P.publish(run, tmp_path / "pub", env)
    assert not (tmp_path / "pub").exists()
    (run / "session_stdin.jsonl").unlink()
    (run / "session_stdin.jsonl").write_text("plain\n")
    P.publish(run, tmp_path / "pub", env)


@pytest.mark.parametrize("name,raw", [("post.tsv", ("note\tx\t/ho" + "me/zorkuser/x\n").encode("utf-16")),
                                      ("trajectory.jsonl", b'{"kind": "header"}\n' + b"\0" * 64),
                                      ("end.json", b'{"end_reason": "x", "p": "/ho' + b'me/zorkuser/\xff\xfe"}')])
def test_a_damaged_file_is_published_as_a_stub_that_names_the_damage(tmp_path, name, raw):
    """A listed file that is not UTF-8 text or holds a NUL byte (UTF-16 hides every path from the rules; a power
    loss leaves a NUL-filled tail) is not read and not published: the copy holds `<file>.damaged.json` naming the
    file, its size, the sha256 of its bytes and the damage, PUBLISHED.json lists it as damaged, and no byte of it
    is in the copy. CONTROL: the same file as UTF-8 text is read and rewritten."""
    import hashlib
    run, env = _run(tmp_path)
    (run / name).write_bytes(raw)
    P.publish(run, tmp_path / "pub", env)
    pub = tmp_path / "pub"
    assert not (pub / name).exists()
    stub = json.loads((pub / f"{name}.damaged.json").read_text())
    assert stub == {"schema": "ck3bench.series.damaged/1", "file": name, "bytes": len(raw),
                    "sha256": hashlib.sha256(raw).hexdigest(), "published": False,
                    "damage": stub["damage"]} and stub["damage"] in ("not UTF-8 text", "a NUL byte",
                                                                     "not UTF-8 text; a NUL byte")
    rec = json.loads((pub / "PUBLISHED.json").read_text())
    assert any(f["file"] == name and f["state"].startswith("damaged (") for f in rec["files"])
    assert not any(b"zorkuser" in p.read_bytes() for p in pub.iterdir())
    (run / name).write_text(raw.decode("utf-16") if name == "post.tsv" else '{"end_reason": "x"}\n' if
                            name == "end.json" else '{"kind": "header"}\n')
    P.publish(run, tmp_path / "pub2", env)
    assert (tmp_path / "pub2" / name).is_file() and not (tmp_path / "pub2" / f"{name}.damaged.json").exists()


def test_a_path_in_base64_refuses_the_copy(tmp_path):
    """A home path base64-encoded: the refusal reads the decoded text. CONTROL: a sha256 and base64 of a plain word
    are not refused."""
    import base64
    run, env = _run(tmp_path)
    hidden = base64.b64encode(("/home/" + "someone/.ssh/id_rsa").encode()).decode()
    (run / "end.json").write_text(json.dumps({"end_reason": "x", "blob": hidden}))
    with pytest.raises(P.PublishRefused, match=r"end.json:\d+: home directory \(base64\)"):
        P.publish(run, tmp_path / "pub", env)
    (run / "end.json").write_text(json.dumps({"end_reason": "x", "blob": base64.b64encode(b"a plain word list").decode(),
                                              "sha": "ab" * 32}))
    P.publish(run, tmp_path / "pub", env)


@pytest.mark.parametrize("how", ["base64 twice", "url-safe base64", "hex"])
def test_a_path_in_a_second_level_or_another_alphabet_refuses_the_copy(tmp_path, how):
    """A home path in base64 inside base64, in URL-safe base64 and in hex: the refusal decodes each. MUTANT: with
    one level of the standard alphabet only, the copy is not refused."""
    import base64
    run, env = _run(tmp_path)
    raw = ("/home/" + "someone/?>?>?>/x~~~").encode()
    hidden = {"base64 twice": base64.b64encode(base64.b64encode(raw)).decode(),
              "url-safe base64": base64.urlsafe_b64encode(raw).decode(), "hex": raw.hex()}[how]
    (run / "end.json").write_text(json.dumps({"end_reason": "x", "blob": hidden}))
    with pytest.raises(P.PublishRefused, match=r"end.json:\d+: home directory \(base64\)"):
        P.publish(run, tmp_path / "pub", env)

    def one_level(s):
        out = []
        for m in P.B64_RUN.finditer(s):
            try:
                t = P._texty(base64.b64decode(m.group(0) + "=" * (-len(m.group(0)) % 4), validate=True))
            except ValueError:
                continue
            out += [t] if t else []
        return out
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(P, "base64_texts", one_level)
        P.publish(run, tmp_path / "pub2", env)


def test_a_hard_link_is_refused_and_a_gzip_that_does_not_read_is_a_stub(tmp_path):
    """A listed file with a second name (a hard link to a file outside the run) is not read and refuses the copy; a
    `.gz` that does not decompress (damage) is published as its stub, never a traceback. CONTROL: a single-named
    file and a whole gzip publish as files."""
    import gzip
    run, env = _run(tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("plain\n")
    os.link(outside, run / "session_stdin.jsonl")
    with pytest.raises(P.PublishRefused, match=r"session_stdin.jsonl: a file with more than one hard link"):
        P.publish(run, tmp_path / "pub", env)
    (run / "session_stdin.jsonl").unlink()
    (run / "session_stdin.jsonl").write_text("plain\n")
    turns = (run / "turns.jsonl").read_bytes()
    (run / "turns.jsonl").unlink()
    for i, bad in enumerate((b"not gzip at all", gzip.compress(turns * 40)[:40])):
        (run / "turns.jsonl.gz").write_bytes(bad)
        P.publish(run, tmp_path / f"pub{i}", env)
        stub = json.loads((tmp_path / f"pub{i}" / "turns.jsonl.gz.damaged.json").read_text())
        assert stub["damage"].startswith("not a readable gzip file") and stub["bytes"] == len(bad)
        assert not (tmp_path / f"pub{i}" / "turns.jsonl.gz").exists()
    (run / "turns.jsonl.gz").write_bytes(gzip.compress(turns))
    P.publish(run, tmp_path / "pub", env)
    assert (tmp_path / "pub" / "turns.jsonl.gz").is_file()


def test_a_host_label_that_is_a_machine_name_is_refused(tmp_path):
    """run.json `host` is the schedule's label (a, b); a machine's name there is refused (it stands in run ids).
    CONTROL: the label publishes."""
    run, env = _run(tmp_path)
    rj = json.loads((run / "run.json").read_text())
    (run / "run.json").write_text(json.dumps({**rj, "host": "rackbox"}))
    with pytest.raises(P.PublishRefused, match="not a host label"):
        P.publish(run, tmp_path / "pub", env)
    (run / "run.json").write_text(json.dumps(rj))
    P.publish(run, tmp_path / "pub", env)
    with pytest.raises(ValueError):
        p = tmp_path / "s.json"
        p.write_text(json.dumps({"schema": S.SERIES_SCHEMA, "series": "x", "task": "t", "pattern": ["rackbox"],
                                 "valid_target": 1, "attempt_cap": 1}))
        S.load_series(p)


def test_CONTROL_a_narrower_address_rule_keeps_a_sentence_end_and_a_private_address_after_ck3():
    """A rule that ends an address at no dot keeps an address before a full stop, and one that reads every
    dotted number after "ck3" as a version keeps a 192.168 address there; this one reads both."""
    old = re.compile(r"(?<![\w.])(?:(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)\.){3}(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)(?![\w.])")
    assert not old.search("connected to 10.20" + ".30.40.")
    assert P.ipv4_addresses("connected to 10.20" + ".30.40.") and P.ipv4_addresses("ck3 192.168" + ".7.9")
    assert not P.ipv4_addresses("CK3 1.18.3.1") and not P.ipv4_addresses("version 1.18.0.2")


#: a run.json that does not read, in three shapes: the machine name it records is still rewritten everywhere
RUN_JSON_DAMAGE = {"nul_tail": lambda b: b[:-8] + b"\0" * 8, "not_utf8": lambda b: b[:-2] + b"\xff\xfe}",
                   "cut": lambda b: b[:b.index(b'"end"')]}


@pytest.mark.parametrize("shape", sorted(RUN_JSON_DAMAGE))
def test_a_damaged_run_json_still_has_its_machine_names_rewritten(tmp_path, monkeypatch, shape):
    """run.json NUL-tailed, not UTF-8, or cut (on another machine than the one that played): the names its bytes
    hold (`"host": "NAME"`, not a label) are rewritten in every file, and run.json itself is published as a stub
    (it is not one JSON object). With no name in its bytes and none given, the copy is refused by name, nothing
    written; `--host-name NAME` then publishes it with that name rewritten."""
    run, env = _run(tmp_path)
    monkeypatch.setattr(P.socket, "gethostname", lambda: "this-host")
    (run / "transcript.jsonl").write_text((run / "transcript.jsonl").read_text()
                                          + json.dumps({"type": "result", "result": f"ran on {HOST} today"}) + "\n")
    raw = (run / "run.json").read_bytes()
    (run / "run.json").write_bytes(RUN_JSON_DAMAGE[shape](raw))
    P.publish(run, tmp_path / "pub", env)
    assert not any(HOST.encode() in p.read_bytes() for p in (tmp_path / "pub").iterdir())
    assert (tmp_path / "pub" / "run.json.damaged.json").is_file() and not (tmp_path / "pub" / "run.json").exists()
    # no name in its bytes: refused by name
    (run / "run.json").write_bytes(b'{"schema": "ck3bench.series.run/1", "run_id": "demo-a01-a", "host": "a", "sta')
    with pytest.raises(P.PublishRefused, match="--host-name"):
        P.publish(run, tmp_path / "pub2", env)
    assert not (tmp_path / "pub2").exists()
    rc = P.main([str(run), str(tmp_path / "pub3"), "--host-name", HOST])
    assert rc == 0 and not any(HOST.encode() in p.read_bytes() for p in (tmp_path / "pub3").iterdir())


def test_MUTANT_names_read_only_from_a_run_json_that_reads_leave_the_machine_in_the_copy(tmp_path, monkeypatch):
    """A narrower rule, the names read from run.json as JSON only: a damaged one gives none it recorded, and the
    transcript is published holding the machine's name. The test above is red on that rule."""
    run, env = _run(tmp_path)
    monkeypatch.setattr(P.socket, "gethostname", lambda: "this-host")
    (run / "transcript.jsonl").write_text(json.dumps({"type": "result", "result": f"ran on {HOST} today"}) + "\n")
    (run / "run.json").write_bytes(RUN_JSON_DAMAGE["nul_tail"]((run / "run.json").read_bytes()))
    monkeypatch.setattr(P, "run_json_host_names", lambda raw: ["unrelated-name"])
    P.publish(run, tmp_path / "pub", env)
    assert HOST in (tmp_path / "pub" / "transcript.jsonl").read_text()


def test_a_series_attempt_whose_run_json_is_absent_is_refused_until_its_machines_are_named(tmp_path, monkeypatch):
    """run.json gone from a series attempt's directory (a counted attempt the docs describe): the names it would
    record are unknown, so the copy is refused by name, nothing written, and `--host-name NAME` then publishes it with
    that name rewritten (the transcript held it). CONTROL: a directory of no series with no run.json is copied (it
    names no attempt and no host)."""
    run, env = _run(tmp_path)
    monkeypatch.setattr(P.socket, "gethostname", lambda: "this-host")
    (run / "transcript.jsonl").write_text(json.dumps({"type": "result", "result": f"ran on {HOST} today"}) + "\n")
    (run / "run.json").unlink()
    with pytest.raises(P.PublishRefused, match="run.json is absent.*--host-name"):
        P.publish(run, tmp_path / "pub", env)
    assert not (tmp_path / "pub").exists()
    assert P.main([str(run), str(tmp_path / "pub2"), "--host-name", HOST]) == 0
    assert not any(HOST.encode() in p.read_bytes() for p in (tmp_path / "pub2").iterdir())
    other = run.with_name("try-1")
    run.rename(other)
    P.publish(other, tmp_path / "pub3", env)
    assert (tmp_path / "pub3" / "PUBLISHED.json").is_file()


#: an escaped quote of the value's own kind, a placeholder that only begins the value, and two further key names
SECRETS_QUOTED_MORE = ('password = "' + 'he said \\"correct horse\\" ok ok"', 'password = "' + '<redacted> but then the real one"',
                       'passphrase = "' + 'correct horse battery"', 'db_pwd: "' + 'correct horse battery"',
                       'passphrase => "' + 'correct horse battery"', '--passphrase "' + 'correct horse battery"')


@pytest.mark.parametrize("secret", SECRETS_QUOTED_MORE)
def test_an_escaped_quote_a_leading_placeholder_and_a_passphrase_name_refuse_the_copy(tmp_path, secret):
    """Each refuses the copy; a narrower rule (OLD_ESCAPE: the value stops at its first quote of its kind, any value
    that begins with `<` exempt, `password` names only) lets each through. CONTROL: a value that is a placeholder
    whole (`"<redacted>"`) passes."""
    run, env = _run(tmp_path)
    (run / "post.tsv").write_text(f"note\t0\t2026-01-01T00:00:00Z\t{secret}\n")
    with pytest.raises(P.PublishRefused, match=r"post.tsv:\d+: key or token"):
        P.publish(run, tmp_path / "pub", env)
    assert not OLD_ESCAPE.search(secret), secret
    (run / "post.tsv").write_text('note\t0\t2026-01-01T00:00:00Z\tpassword = "<redacted placeholder>"\n')
    P.publish(run, tmp_path / "pub2", env)


OLD_ESCAPE = re.compile(r"(?i:\b[\w-]*(?:api[_-]?key|secret|passw(?:or)?d|credential|private[_-]?key)[\w-]*[\"']?\s*[=:]"
                        r"\s*(?:\"(?!<)[^\"\n]{12,}\"|'(?!<)[^'\n]{12,}'))")


@pytest.mark.parametrize("cwd", ("/home/" + "someone/projects/thing", "C:\\\\Users\\\\someone\\\\projects\\\\thing"))
def test_a_pwd_key_holding_a_path_is_a_working_directory_rewritten_as_a_path(tmp_path, cwd):
    """`"pwd": "<a path>"` is a working directory (a shell's or a tool's), not a passphrase: the copy is made and the
    path in it is rewritten as any path is. CONTROL: a `pwd` value that is not a path still refuses the copy."""
    run, env = _run(tmp_path)
    (run / "post.tsv").write_text(f'note\t0\t2026-01-01T00:00:00Z\t{{"pwd": "{cwd}"}}\n')
    P.publish(run, tmp_path / "pub", env)
    text = (tmp_path / "pub" / "post.tsv").read_text()
    assert '"pwd": "<HOME>' in text and "someone" not in text, text
    (run / "post.tsv").write_text('note\t0\t2026-01-01T00:00:00Z\t{"pwd": "' + 'correct horse battery"}\n')
    with pytest.raises(P.PublishRefused, match=r"post.tsv:\d+: key or token"):
        P.publish(run, tmp_path / "pub2", env)


def test_a_hosted_ci_account_is_not_a_user_name_and_the_copy_still_reads(tmp_path, monkeypatch):
    """`runner` (a hosted CI machine's account) names no person and is a word of the harness's own names
    (runner_start.json, runner_flags): it is not replaced, so the record lists its files and the scorer reads the
    copy's run.json as it reads the run's."""
    import types
    real = P.pwd.getpwuid
    monkeypatch.setattr(P.pwd, "getpwuid", lambda uid: types.SimpleNamespace(pw_name="runner", pw_dir=real(uid).pw_dir))
    run, env = _run(tmp_path)
    assert "runner" not in P.user_names(env, [])
    rj = json.loads((run / "run.json").read_text())
    rj.update(runner_flags=["--wall-budget-s", "100"])
    (run / "run.json").write_text(json.dumps(rj))
    rec = P.publish(run, tmp_path / "pub", env)
    assert "<user name>" not in json.dumps(rec["files"])
    assert json.loads((tmp_path / "pub" / "run.json").read_text())["runner_flags"] == ["--wall-budget-s", "100"]
