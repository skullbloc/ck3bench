"""No shipped byte and no shipped file name carries the vocabulary of the work that made this repository.

test_public_tree.py refuses machines, home directories, addresses, run ids and unfinished-work markers. This file
refuses the rest of that vocabulary, by class: a work unit's label (a two-letter-and-number tag, a capitalised code
name), a run's label (a run named by host and number, a private save's file name), a path into material that does
not ship, a person named by role, a commit pinned by sha, an account's usage figures, and a test that reads outside
the package, references to a registration document that does not ship, a claim that something is not proven on the live game outside the one
pinned prompt line that says it,
a model-cost figure (either order of number and currency), an experiment's arm named instead of what an option does,
a registration document or a count of builds cited as history, a save named by its batch, how a text came to be
instead of what it says, measured seats of tasks that had no measured series, and the shapes a text takes when a
word is cut out of it. A comment that explains code says
what the code does and why; how a fix was found is not shipped.

Every file of the checkout is read as bytes, and every file NAME is read too (a test file named by its work unit is
the same label). The walk is test_public_tree's (from the tree, never a list of names; an unreadable file is a
finding, so it fails open). The allowlist is small and reviewed, and every entry must excuse at least one hit (a
stale entry reds). The copyright owner of the license text is a legal term. Recorded identifiers the scorer reads
(the recorded schema names and the series' end marker, docs/harness.md "Recorded identifiers") are ordinary
identifiers here. Each pattern is spelled in pieces so this file does not match itself, and each has a control that
RUNS: a planted instance of every class is refused and ordinary words are not.

The classes are SHAPES (a tag and a number, a capitalised name and a number, an `env`-glued run name), never a list
of the names the work used: a public list of those names would itself publish them. The step that builds this
repository from its upstream refuses the names themselves in every shipped byte, from a list that does not ship.
"""
import re

from test_public_tree import ROOT, _files, folded_hits

CLASSES = {
    # a work unit's tag: two letters and a number, or a test or helper named by one
    "work_label": re.compile(rb"(?<![A-Za-z0-9])(?:T" rb"K|t" rb"k)[0-9]{1,3}(?![0-9])|\bpre_t" rb"k[0-9]+"),
    # the word this work used for a unit of work
    "unit_word": re.compile(rb"\b[Ll]a" rb"nes?\b"),
    # a work unit's or a run's capitalised name with its number (a name and a number glued: the code's own words of
    # that shape are listed in the pattern, reviewed; a task id's start year is not a run number)
    "code_name": re.compile(rb"\b(?!(?:BLE001|TASK180|HEX64|SAV0103|SAV0)\b)(?![A-Za-z]+(?:867|1066|1178)\b)"
                            rb"[A-Z]{3,}[0-9]+\b"),
    # a run named by its host or number, and a run's own save
    "run_label": re.compile(rb"\benv(?!iron|elope|s?\b)[a-z]\w*|"
                            rb"\b(?:w" rb"s|no" rb"de)[0-9]{1,2}\b|\bmun" rb"ster[0-9]{1,2}\b|\ba[0-9]{2}(?= [0-9]{4})|"
                            rb"_auto" rb"save\.ck3|\bru" rb"n (?:one|two|three|four|five|six|seven|eight|nine|ten) on\b"),
    # a path into material that does not ship
    "private_tree": re.compile(rb"\bclean" rb"up-20[0-9]{2}\.[0-9]+|\"evi" rb"dence\"\s*/|/\s*\"evi" rb"dence\"|"
                               rb"\bE/en" rb"v\w+|\[R" rb"R[0-9]\]|\bwork" rb"station\b"),
    # a person named by role (the license's "copyright owner" is the legal term, not a person here)
    "role": re.compile(rb"(?i)(?<!copyright )\b(?:own" rb"er|orches" rb"trator|coordi" rb"nator)(?:'s)?\b(?!_)"),
    # a commit or blob pinned by its sha
    "commit_pin": re.compile(rb"(?i)\b(?:base(?:_sha|_blob)?|blob|pins?|commit)\b[^\n]{0,40}?\b[0-9a-f]{8,40}\b"),
    # an account's usage figures
    "usage": re.compile(rb"rate_limit_ev" rb"ent|\butiliz" rb"ation\b|\bover" rb"age\w*|seven_d" rb"ay|resets" rb"At|"
                        rb"org_level_dis" rb"abled"),
    # a test reading beside the package instead of the installed toolkit
    "private_read": re.compile(rb"(?:parents\[[0-9]\]|\bROOT|\bREPO)\s*/\s*\"(?:ck3" rb"agent|rel" rb"ease|"
                               rb"scr" rb"ipts|bri" rb"dge|re)\""),
    # a registration document that does not ship, cited instead of the rule it fixes
    "registration": re.compile(rb"(?i)\bpre-?reg" rb"ist\w*|\bregis" rb"tered (?:amend" rb"ment|const" rb"ants?)\b"),
    # the harness's measurement word for a series, in prose
    "series_word": re.compile(rb"\brate ru" rb"ns?\b"),
    # a claim of what was not run, outside the one pinned prompt line
    "not_live": re.compile(rb"\bnot (?:yet )?pro" rb"ven live\b|\bbuilt, not pro" rb"ven\b"),
    # a comment or a docstring opened by a finding's number, or a finding's number after `;` in a sentence
    "finding_label": re.compile(rb"^\s*#:?\s*[A-Z][0-9]{1,2}:|# [A-Z][0-9]{1,2}:|(?:\"\"\"|''')[ \t]*[A-Z]{1,2}[0-9]{1,2}: "
                                rb"|(?<=[a-z0-9,)])[ \t]*; [A-Z]{1,2}[0-9]{1,2}: "),
    # the step that builds this repository, named as an actor in it
    "export_step": re.compile(rb"(?i)\b(?:when|if|unless) the ex" rb"port\b|\bthe ex" rb"port(?:'s)? (?:states|ships|"
                              rb"stamps|writes|builds|copies|refuses|records|runs)\b"),
    # how a text came to be: the run it was read in, a change told as "now <does>"
    "narration": re.compile(rb"(?i)\b(?:toolkit|build|version|prompt|notes|tree|kit|bridge) of that ru" rb"n\b|"
                            rb"\bthe def" rb"ect\b|\bbefore it (?:applied|read|wrote|held|na" rb"med)\b|"
                            rb"\b(?:rev" rb"iew|au" rb"dit) round\b|"
                            rb"(?i:\b(?:first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth)\s+"
                            rb"(?:au" rb"dit|fi" rb"x)\b)|"
                            rb"\b(?:it|which|that|toolkit|env|bridge|server|scorer|driver|runner|reader) no" rb"w "
                            rb"(?:passes|writes|reads|says|refuses|serves|returns|names|sends|takes|keeps|gives)\b"),
    # a key or a token: a private key's header (a PGP key block's too), a provider's or forge's token, a cloud
    # provider's key or key id, a chat token, a JWT
    "secret": re.compile(rb"-----BEGIN (?:[A-Z0-9]+ )*PRIV" rb"ATE KEY(?: BLOCK)?-----|\bAI" rb"za[0-9A-Za-z_-]{35}(?![0-9A-Za-z_-])|"
                         rb"\bsk-(?:ant-)?[A-Za-z0-9_-]{20,}|"
                         rb"\bgh[pousr]_[A-Za-z0-9]{20,}|\bgithub_p" rb"at_\w{20,}|\b(?:AK" rb"IA|AS" rb"IA)[0-9A-Z]{16}\b|"
                         rb"\bxox[abposr]-[\w-]{10,}|\beyJ[\w-]{8,}\.eyJ[\w-]{8,}\.|"
                         rb"\bhf_[A-Za-z0-9]{30,}|\bglp" rb"at-[\w-]{20,}|\b[rs]k_(?:li" rb"ve|te" rb"st)_[0-9A-Za-z]{16,}"),
    # a per-user or per-session directory under /tmp
    "tmp_session": re.compile(rb"/tmp/(?:cla" rb"ude|[\w.]+-)[0-9]{3,}/"),
    # a user at a machine as a login names it (after ssh, scp, rsync, sftp or mosh, or before a remote path)
    "login": re.compile(rb"(?i)\b(?:ssh|scp|rsync|sftp|mosh)\s+(?:-\S+\s+)*[a-z_][\w.-]*@[a-z][\w-]*\b(?!\.)"
                        rb"|(?<![\w.+/-])[a-z_][\w.-]*@(?:localhost|[a-z][\w-]{1,62}):(?:[~/]|\s|$)"),
    # a model-cost figure, in either order of number and currency
    "cost_figure": re.compile(rb"\bUS" rb"D [0-9]+|\b[0-9]+k tok" rb"ens\b|\b[0-9]+(?:\.[0-9]+)? ?US" rb"D\b|"
                              rb"(?i:\bcost\w*|\babout|\bspent|\bpaid)\W{0,3}\$ ?[0-9]"),
    # an experiment's arm, named in help or prose instead of what the option does
    "arm_word": re.compile(rb"\b(?:fresh-prompt|ablation|baseline|model|treatment|control) ar" rb"ms?\b"),
    # a registration document cited by name
    "registration_cite": re.compile(rb"(?i)\bthe regis" rb"tration(?:'s)?\b"),
    # a count of builds that shared a version: the history of one, not what the code does
    "build_count": re.compile(rb"(?i)\b(?:five|four|three|several) bu" rb"ilds\b"),
    # a save named by the batch that made it
    "save_batch": re.compile(rb"\bscen_b[0-9]+" rb"_"),
    # how a text came to be, instead of what it says
    "history": re.compile(rb"(?i)\bkept as it w" rb"as\b|\bno longer na" rb"mes\b|\bfrom mem" rb"ory\b"),
    # measured seats of tasks that had no measured series
    "measured_seats": re.compile(rb"(?i)\bmeasured seats? of (?:those|the line) ta" rb"sks\b"),
    # a word cut out of a text: a module path that lost its package after -m or from, empty backticks
    "cut_word": re.compile(rb"(?:-m|from)\.(?:env|harness)\b|\(\.(?:env|harness)\.|(?<!`)`" rb"`(?!`)"),
}
#: a capitalised word opening a comment as a label ("WORD:"): only these are vocabulary of the code itself
CAPS_LABEL = re.compile(rb"^\s*(?:#:?|\"\"\"|\*|//)\s*([A-Z][A-Z0-9_]{3,}):")
CAPS_OK = {b"CONTROL", b"MUTANT", b"LIFETIME", b"ONCE_GOALS"}
#: (file, class) pairs exempt by review; each must excuse at least one hit (test_every_allowance_excuses_a_hit)
ALLOW: set[tuple[str, str]] = set()
#: the line tasks' seat prompt says of the faction veto that it was built and not run live: model-facing text whose
#: rendered bytes are pinned (ck3bench/harness/test_prompt.py) and kept as the seats that played those tasks were sent it
ALLOW |= {("ck3bench/harness/prompt.py", "not_live")}
#: the README's model-cost example for a six-seat game, and the cost of a check run (docs/harness.md and the
#: driver's --check help), kept until the maintainers decide on usage figures
ALLOW |= {("README.md", "cost_figure"), ("docs/harness.md", "cost_figure"), ("ck3bench/harness/driver.py", "cost_figure")}
#: the decision tasks' declared save names: the file names a user is told to supply (each task also declares the
#: save's sha256, which is its identity), kept until the maintainers rename them
ALLOW |= {("README.md", "save_batch"), ("ck3bench/env/scenario.py", "save_batch")}
#: the scorer's resource rule reads the agent CLI's own stream-json event of that type (an account limit voids a
#: stopped run instead of failing it), and its count is a field of recorded score files: the event's name is the
#: CLI's vocabulary, not an account's figures. Its test plants one such event. Reviewed: no figure is shipped.
ALLOW |= {("ck3bench/harness/score.py", "usage"), ("ck3bench/harness/test_score.py", "usage")}
#: `series publish` names the agent CLI's account and limit keys and its rate-limit event in order to drop them from a
#: publishable copy, and refuses a copy in which one survives; its test plants them. Reviewed: no figure is shipped.
ALLOW |= {("ck3bench/harness/publish.py", "usage"), ("ck3bench/harness/test_publish.py", "usage")}
#: the stopping rule's tests plant a rate-limit event to drive the resource rule, as test_score does. Reviewed: no
#: figure is shipped.
ALLOW |= {("ck3bench/harness/test_series_stop.py", "usage")}


#: classes anchored to a line's start (a comment's label), which a folded text cannot carry
LINE_ANCHORED = {"finding_label"}


def _hits(rx, name="?"):
    out = []
    for p in _files():
        rel = p.relative_to(ROOT).as_posix()
        if (rel, name) in ALLOW:
            continue
        # a file name in any case, and its words split at `_` too (`\b` sees no edge inside `test_x`)
        rxi = re.compile(rx.pattern, rx.flags | re.I)
        names = {m.group(0) for form in (rel, rel.replace("_", " ")) for m in rxi.finditer(form.encode())}
        out += [(rel, "name", t.decode()) for t in sorted(names)]
        try:
            data = p.read_bytes()
        except OSError as e:
            out.append((rel, 0, f"unreadable: {e}"))
            continue
        tokens = []
        for n, line in enumerate(data.split(b"\n"), 1):
            for m in rx.finditer(line):
                tokens.append(m.group(0))
                out.append((rel, n, m.group(0).decode("utf-8", "replace")))
        if name not in LINE_ANCHORED:       # a phrase a line break splits, read with the breaks folded
            out += [(rel, "folded", t.decode("utf-8", "replace")) for t in folded_hits(rx, data, tokens)]
    return out


def test_no_shipped_byte_or_file_name_carries_a_label_of_any_class():
    found = {k: _hits(rx, k) for k, rx in CLASSES.items()}
    assert {k: v for k, v in found.items() if v} == {}


def test_every_capitalised_comment_label_is_code_vocabulary():
    bad = []
    for p in _files():
        rel = p.relative_to(ROOT).as_posix()
        for n, line in enumerate(p.read_bytes().split(b"\n"), 1):
            m = CAPS_LABEL.match(line)
            if m and m.group(1) not in CAPS_OK:
                bad.append((rel, n, m.group(1).decode()))
    assert bad == []


# The planted instances: each is assembled from pieces, so this file carries none whole.
PLANTED = {
    "work_label": [b"T" b"K99 item 9", b"# T" b"K99: x", b"test_t" b"k99_goal.py", b"pre_t" b"k99(specs)"],
    "unit_word": [b"La" b"ne Z in the seat shim", b"the la" b"ne's base commit", b"plus the speed la" b"ne"],
    "code_name": [b"ZQXNA" b"ME99 Z9t: x", b"# ZORK" b"99: x", b"QUUX" b"99's run", b"ZZZ" b"9"],
    "run_label": [b"env" b"w" b"s99", b"env" b"zork99ws99", b"env" b"zork item 9", b"on w" b"s99", b"no" b"de99",
                  b"mun" b"ster99 a" b"99 9999", b"w" b"s99_9999_99_99_auto" b"save.ck3", b"ru" b"n nine on the host"],
    "private_tree": [b"do" b"cs/clean" b"up-2099.99/x", b'ROOT / "do" b"cs" / "evi' b'dence" / "x"', b"E/en" b"v99",
                     b"[R" b"R9]", b"the work" b"station"],
    "role": [b"own" b"er ruling 2099-99-99", b"the own" b"er's login", b"the orches" b"trator", b"the coordi" b"nator"],
    "commit_pin": [b"BASE = \"dead" b"beef" b"cafe" b"babe\"", b"(blob dead" b"beef)", b"base pin cafe" b"babe"],
    "usage": [b"99 rate_limit_ev" b"ents", b"seven-day utiliz" b"ation 9.99", b"over" b"ageStatus", b"resets" b"At"],
    "private_read": [b'parents[9] / "ck3' b'agent" / "server"', b'REPO / "rel' b'ease"', b'ROOT / "scr' b'ipts"'],
    "registration": [b"(pre-reg" b"istration 9b)", b"a regis" b"tered amend" b"ment", b"FALLBACK (pre-reg" b"istered)",
                     b"the regis" b"tered constants"],
    "series_word": [b"a rate ru" b"n plays one", b"rate ru" b"ns hold 999"],
    "not_live": [b"(built, not pro" b"ven live)", b"not ye" b"t pro" b"ven live"],
    "finding_label": [b"    # Z" b"9: the pad", b"x = 9  # Z" b"99: why", b'"""Z' b'9: an attempt the ledger names',
                      b"the ledger refuses; Z" b"9: two files"],
    "cost_figure": [b"cost 999k tok" b"ens", b"and US" b"D 99", b"about 99 US" b"D of model use", b"cost $" b"99",
                    b"9.99 US" b"D a run"],
    "arm_word": [b"(the fresh-prompt ar" b"m)", b"the ablation ar" b"m", b"(the baseline ar" b"m)", b"A model ar" b"m's path"],
    "registration_cite": [b"The regis" b"tration's secondary", b"as the regis" b"tration says"],
    "build_count": [b"five bu" b"ilds all reported", b"several bu" b"ilds"],
    "save_batch": [b"scen_b" b"9_9999_99_99.ck3"],
    "history": [b"is kept as it w" b"as", b"none is restated from mem" b"ory", b"no longer na" b"mes a label"],
    "measured_seats": [b"what the measured seats of those ta" b"sks were sent"],
    "cut_word": [b"python -m" b".harness VERB", b"from" b".env import x", b"The `" b"` command.", b"(" b".env.scenario)"],
    "export_step": [b"when the ex" b"port states one", b"the ex" b"port writes it"],
    "narration": [b"the toolkit of that ru" b"n had none", b"which no" b"w passes", b"(the def" b"ect: it counted)",
                  b"what it wrote before it app" b"lied it", b"the third rev" b"iew round",
                  b"merge the seventh au" b"dit fix round", b"the eighth fi" b"x"],
    "secret": [b"-----BEGIN OPENSSH PRIV" b"ATE KEY-----", b"sk-ant-" b"zzzzzzzzzzzzzzzzzzzzzzzz",
               b"-----BEGIN PGP PRIV" b"ATE KEY BLOCK-----", b"AI" b"za" + b"Z" * 35,
               b"ghp_" b"zzzzzzzzzzzzzzzzzzzzzzzzzz", b"AK" b"IAZZZZZZZZZZZZZZZZ",
               b"hf_" b"Zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz", b"glp" b"at-" b"Zzzzzzzzzzzzzzzzzzzz",
               b"sk_li" b"ve_" b"Zzzzzzzzzzzzzzzz"],
    "tmp_session": [b"/tmp/cla" b"ude-999/-proj/x", b"/tmp/zork-" b"999/x"],
    "login": [b"ssh zork" b"@quux", b"zork@" b"quux:/srv/x"],
}
CLEAN = {
    "work_label": [b"tkinter", b"TKO", b"ck3bench", b"stk1"],
    "unit_word": [b"a plane", b"planet", b"elan"],
    "code_name": [b"LINE_LOST_TEXT", b"FREEZE", b"the series", b"IRELAND_DUBLIN", b"KIT", b"BLE001", b"HEX64",
                  b"SAV0103", b"IRELAND1066", b"the C2 row", b"UTF-8"],
    "run_label": [b"envelope", b"environment", b"run one episode", b"node.js", b"ws_url", b"env", b"envs",
                  b"environ", b"ck3bench/env/env.py"],
    "private_tree": [b"evidence", b"a release", b"cleanup", b"workflow"],
    # owner_tree_differs: a validity code of recorded series results (docs/harness.md gives its neutral meaning);
    # an identifier, which the role class leaves alone by design (reviewed, kept as recorded)
    "role": [b"the copyright own" b"er", b"ownership", b"owned", b"owner_id", b"own" b"er_tree_differs"],
    "commit_pin": [b"sha256 of its sorted JSON", b"a pin of the version", b"commit sha, not a moving ref"],
    "usage": [b"usage as reported", b"the utility", b"reset_at"],
    "private_read": [b'tmp_path / "site" / "ck3agent"', b'Path(__file__).parent / "recorded"',
                     b'ROOT / "docs" / "reference.md"'],
    "registration": [b"registered trademarks", b"a series declared in advance", b"registers"],
    "series_word": [b"the rate of runs", b"a series run plays one", b"separate runs"],
    "not_live": [b"not run live", b"proven on the fake backend"],
    "finding_label": [b"# a comment: C2 is fine mid-line", b"#: the constant", b"# CONTROL: x"],
    "cost_figure": [b"cost_usd_reported", b"USD", b"tokens as reported", b'"$1.list"', b"echo \"$!\"", b"cost $W"],
    "arm_word": [b"the army", b"an alarm", b"a model seat", b"the baseline (no model)"],
    "registration_cite": [b"registered trademarks", b"a series declared in advance", b"registration of a tool"],
    "build_count": [b"a build of the same version", b"builds the wheel"],
    "save_batch": [b"_1066_09_15.ck3", b"dublin_1076_01_01.ck3", b"scen_b"],
    "history": [b"kept as the measured seats were sent it", b"left unchanged", b"memory: off"],
    "measured_seats": [b"the model seats that have played those tasks", b"measured series"],
    "cut_word": [b"python -m ck3bench.harness", b"from ck3bench.env import x", b"```bash", b"`ck3bench` command",
                 b"python -m pytest"],
    "export_step": [b"the exported tree", b"export TURN_MODE=fresh", b"the export table"],
    "narration": [b"the game of that run", b"it reads now", b"the env reads the war", b"a defect is recorded",
                  b"before it applies", b"a round of reads"],
    "secret": [b"sk-short", b"a token count", b"the Bearer scheme"],
    "tmp_session": [b"/tmp/ck3-agent.sock", b"/tmp/ck3bench-UID"],
    "login": [b"actions/upload-artifact@v9", b"the @ sign"],
}


def test_each_class_refuses_its_planted_labels_and_passes_ordinary_words():
    """CONTROL: every class fires on a planted instance (none is vacuous), and none fires on ordinary words."""
    for k, rx in CLASSES.items():
        for planted in PLANTED[k]:
            assert rx.search(planted), (k, planted)
        for clean in CLEAN[k]:
            assert not rx.search(clean), (k, clean)
    assert set(PLANTED) == set(CLEAN) == set(CLASSES)


def test_every_allowance_excuses_a_hit():
    """CONTROL: an allowance that excuses nothing is stale (its file or its text changed) and reds here."""
    stale = []
    for rel, name in sorted(ALLOW):
        p = ROOT / rel
        data = p.read_bytes() if p.is_file() else b""
        if not any(CLASSES[name].search(line) for line in data.split(b"\n")):
            stale.append((rel, name))
    assert stale == []


def test_a_planted_file_name_is_refused(tmp_path, monkeypatch):
    """CONTROL: the file-name leg runs: a test file named by its work unit is a hit, though its bytes are clean."""
    import sys
    bad = tmp_path / "ck3bench" / ("test_t" + "k99_goal.py")
    bad.parent.mkdir()
    bad.write_bytes(b"def test_x():\n    pass\n")
    me = sys.modules[__name__]
    monkeypatch.setattr(me, "ROOT", tmp_path)
    monkeypatch.setattr(me, "_files", lambda: [bad])
    assert me._hits(CLASSES["work_label"], "work_label") == [("ck3bench/" + "test_t" + "k99_goal.py", "name", "t" + "k99")]


def test_a_planted_lower_case_code_name_file_is_refused(tmp_path, monkeypatch):
    """CONTROL: the file-name leg reads a name in any case: a code name spelled in lower case in a file name is a
    hit, though the same class is upper case in text."""
    import sys
    bad = tmp_path / "ck3bench" / ("test_zork" + "99.py")
    bad.parent.mkdir()
    bad.write_bytes(b"def test_x():\n    pass\n")
    me = sys.modules[__name__]
    monkeypatch.setattr(me, "ROOT", tmp_path)
    monkeypatch.setattr(me, "_files", lambda: [bad])
    assert me._hits(CLASSES["code_name"], "code_name") == [("ck3bench/" + "test_zork" + "99.py", "name", "zork" + "99")]
    assert not CLASSES["code_name"].search(b"the zork" b"99 of a file")        # in text the class stays upper case


def test_a_caps_label_outside_the_vocabulary_is_refused():
    """CONTROL: the comment-label review list fires on a code name and passes the code's own words."""
    assert CAPS_LABEL.match(b"    # ZQX" b"NAME: x").group(1) not in CAPS_OK
    assert CAPS_LABEL.match(b'    """CONTROL: x').group(1) in CAPS_OK


# ── the planted values are synthetic ─────────────────────────────────────────
#: A control needs a label's SHAPE, never a real label. Every planted value is spelled from this vocabulary: each
#: number in it is made of nines (a year or a date may open with 20: 2099, 2099-99-99), and each hex-looking token
#: of 8 to 40 digits is spelled from the words below (deadbeef, cafebabe). A commit, a run, a date or a figure of
#: real work is neither, so a plant copied from real work reds here. The values are read as Python assembles them
#: (the AST's constants: pieces spelled apart are one value there), so a label split into pieces is read whole.
#: `ck3` (the game's name, in ck3agent, ck3bench and a save's `.ck3`) is the one number-bearing word that is not a
#: number.
SYNTHETIC_NUMBER = re.compile(r"(?:20)?9+")
SYNTHETIC_HEX = re.compile(r"(?:dead|beef|cafe|babe|face|feed|fade|deaf)+")
HEX_TOKEN = re.compile(r"(?<![0-9A-Za-z])[0-9a-f]{8,40}(?![0-9A-Za-z])")
#: a commit-sized hex token anywhere in shipped Python (a sha256 is 64 digits and is not one); it must hold a digit
#: and a letter, as a commit's abbreviation does
COMMIT_HEX = re.compile(r"(?<![0-9A-Za-z])(?=[0-9a-f]*[0-9])(?=[0-9a-f]*[a-f])[0-9a-f]{12,40}(?![0-9A-Za-z])")


#: the one set of values of real shape (made up, never copied): the control below must see the rule fire on them,
#: so the walk of shipped constants leaves exactly these out
REAL_SHAPED = ("T" "K123 item 8", "own" "er ruling 2031-01-31", "base pin 0a1b" "2c3d", "4.21 US" "D", "a1" "2 1088",
               "test_t" "k123_goal.py", "0a1b2c" "3d4e5f")


#: plants of a pattern that names one fixed thing by its number (an old format's name): the plant is that name
FIXED_NAME_PLANTS = {"ck3bench.trajectory/" "1"}
#: files whose constants hold content digests of commit size by declaration, with how many (reviewed; a count that
#: no longer matches reds): a notes set's version is the first 12 hex digits of its content's sha256
HEX_DIGESTS = {"ck3bench/env/test_wake_ck3.py": (2, "a stand-in notes version spelled 1-2-3 and 4-5-6 (abc, def)")}


def synthetic_problems(value: str) -> list[str]:
    """Why `value` is not spelled from the synthetic vocabulary ([] when it is)."""
    out = []
    for m in HEX_TOKEN.finditer(value):
        if not SYNTHETIC_HEX.fullmatch(m.group(0)):
            out.append(f"hex {m.group(0)!r}")
    rest = re.sub(r"(?i)ck3", "", HEX_TOKEN.sub("", value))
    out += [f"number {n!r}" for n in re.findall(r"[0-9]+", rest) if not SYNTHETIC_NUMBER.fullmatch(n)]
    return out


def _label_patterns() -> dict:
    import test_public_tree as T
    return {**CLASSES, **{f"tree:{k}": getattr(T, k) for k in ("HOST_PATH", "PRIVATE", "INTERNAL", "RUN_LABEL",
                                                              "RUN_ID", "WIP", "HISTORY")}}


def _folded(node):
    """The value of a str or bytes constant, or of a `+` join of such constants (`"test_t" + "k99.py"`), as
    Python assembles it; None for anything else."""
    import ast
    if isinstance(node, ast.Constant) and isinstance(node.value, (str, bytes)):
        return node.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        a, b = _folded(node.left), _folded(node.right)
        if a is not None and b is not None and type(a) is type(b):
            return a + b
    return None


def _constants(path) -> list[str]:
    """Every str or bytes constant of a Python file as the parser assembles it (pieces spelled apart, and pieces
    joined with `+`, are one value), the patterns given to re.compile left out (a pattern's own text is not a
    plant)."""
    import ast
    tree = ast.parse(path.read_bytes())
    skip = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Call) and getattr(n.func, "attr", getattr(n.func, "id", None)) == "compile" and n.args:
            skip |= {id(c) for c in ast.walk(n.args[0])}
    out = []
    for n in ast.walk(tree):
        if id(n) in skip or not isinstance(n, (ast.Constant, ast.BinOp)):
            continue
        v = _folded(n)
        if v is not None:
            out.append(v.decode("utf-8", "replace") if isinstance(v, bytes) else v)
    return out


def test_every_planted_value_is_synthetic_and_fires_only_its_class():
    """CONTROL of the controls: each plant, joined, is spelled from the synthetic vocabulary, and of every label
    class here and in test_public_tree only its own fires on it (a plant that fires another class is two plants)."""
    pats = _label_patterns()
    bad = []
    for k, plants in PLANTED.items():
        for v in plants:
            s = v.decode()
            bad += [(k, s, p) for p in synthetic_problems(s)]
            fired = {name for name, rx in pats.items() if name in CLASSES and rx.search(v)}
            if fired != {k}:
                bad.append((k, s, f"fires {sorted(fired)}"))
    assert bad == []


def test_every_label_shaped_constant_in_shipped_python_is_synthetic():
    """Wherever a test plants a label (here, test_public_tree, the package's own tests), the assembled value is
    synthetic; and no shipped Python constant carries a commit-sized hex token."""
    from test_public_tree import _files
    pats = _label_patterns()
    bad = []
    for p in _files():
        if p.suffix != ".py":
            continue
        rel = p.relative_to(ROOT).as_posix()
        hexes = 0
        for s in _constants(p):
            if s in REAL_SHAPED or s in FIXED_NAME_PLANTS:
                continue
            b = s.encode("utf-8", "replace")
            found = [m.group(0) for m in COMMIT_HEX.finditer(s)]
            hexes += len(found)
            if len(found) and hexes > HEX_DIGESTS.get(rel, (0, ""))[0]:
                bad += [(rel, s[:80], f"commit-sized hex {h!r}") for h in found]
            if any(rx.search(b) for k, rx in pats.items() if (rel, k) not in ALLOW):
                bad += [(rel, s[:80], why) for why in synthetic_problems(s)]
        if hexes < HEX_DIGESTS.get(rel, (0, ""))[0]:
            bad.append((rel, "", f"HEX_DIGESTS allows {HEX_DIGESTS[rel][0]}, found {hexes}: a stale allowance"))
    assert bad == []


def test_CONTROL_a_label_joined_with_plus_is_read_whole(tmp_path):
    """The walk of shipped constants reads a `+` join as one value: a work label of real shape split by `+` is
    refused by the synthetic rule, and its synthetic twin passes."""
    f = tmp_path / "x.py"
    f.write_text('bad = "test_t" + "k123_goal.py"\nok = "test_t" + "k99_goal.py"\nn = 1 + 2\n')
    got = _constants(f)
    assert "test_t" "k123_goal.py" in got and "test_t" "k99_goal.py" in got
    assert synthetic_problems("test_t" "k123_goal.py") and not synthetic_problems("test_t" "k99_goal.py")
    assert CLASSES["work_label"].search(("test_t" "k123_goal.py").encode())


def test_CONTROL_a_real_looking_plant_is_refused():
    """The synthetic rule fires on a number, a date and a commit prefix of real shape, and passes their synthetic
    twins; the commit-sized hex rule fires on a 12-digit abbreviation and not on a sha256."""
    for real in REAL_SHAPED[:-1]:
        assert synthetic_problems(real), real
    for fake in ("T" "K99 item 9", "own" "er ruling 2099-99-99", "base pin dead" "beef", "9.99 US" "D",
                 "a9" "9 9999", "ck3bench", "x.ck3"):
        assert synthetic_problems(fake) == [], fake
    assert COMMIT_HEX.search(REAL_SHAPED[-1]) and not COMMIT_HEX.search("e" * 3 + "0" * 61)
    assert not COMMIT_HEX.search("deadbeefcafe") and not COMMIT_HEX.search("123456789012")
