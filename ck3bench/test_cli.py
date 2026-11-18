"""The CLI's subcommands exist by name; the unimplemented ones refuse by name;
`experiment check` passes the three examples and refuses a disagreeing block."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from ck3bench import cli
from ck3bench.experiment import load_experiment, to_conditions_block

EXAMPLES = Path(__file__).resolve().parent / "experiments"
DESIGN_SUBCOMMANDS = ("census", "freeze", "pilot", "run", "collect", "report", "verify", "experiment")


def test_every_design_subcommand_is_registered():
    sub = next(a for a in cli.build_parser()._actions if a.dest == "cmd")
    assert set(sub.choices) == set(DESIGN_SUBCOMMANDS)


@pytest.mark.parametrize("name", sorted(cli.UNIMPLEMENTED))
def test_unimplemented_subcommands_refuse_by_name(name, capsys):
    rc = cli.main([name, "anything", "--else"])
    assert rc == cli.EXIT_NOT_IMPLEMENTED
    row = json.loads(capsys.readouterr().out)
    assert row == {"ok": False, "reason": "not_implemented", "subcommand": name,
                   "milestone": cli.UNIMPLEMENTED[name][1]}
    assert row["milestone"] in cli.MILESTONES


def test_every_milestone_brings_a_subcommand_and_no_lane_label_survives():
    assert {m for _, m in cli.UNIMPLEMENTED.values()} == set(cli.MILESTONES)
    src = Path(cli.__file__).read_text(encoding="utf-8")
    assert not re.search(r"\bK[1-6]\b", src) and "lane" not in src.lower()


@pytest.mark.parametrize("name", ("scale0", "budget0", "orgdesign-v4"))
def test_experiment_check_passes_each_example(name, capsys):
    rc = cli.main(["experiment", "check", str(EXAMPLES / f"{name}.toml")])
    assert rc == cli.EXIT_OK
    rep = json.loads(capsys.readouterr().out)
    assert rep["ok"] and rep["name"] == name and rep["runner_host"] == "absent"
    assert rep["toolkit"]["declared"] == load_experiment(EXAMPLES / f"{name}.toml").data["toolkit"]["ck3agent"]
    assert rep["toolkit"]["state"] in ("installed_matches", "installed_differs", "not_installed")
    assert rep["registration"]["state"] in ("not_declared", "registration_document_not_shipped",
                                            "present_sha_not_declared", "present_sha_agrees")


def test_experiment_check_reports_the_registration_document_against_the_tree(tmp_path, capsys):
    """orgdesign-v4 names a document of the research tree that this version
    does not ship: under a tree without it the state is
    registration_document_not_shipped and the check passes; under a tree
    that carries it, present_sha_not_declared (no sha declared)."""
    toml = EXAMPLES / "orgdesign-v4.toml"
    rc = cli.main(["experiment", "check", str(toml), "--tree", str(tmp_path)])
    assert rc == cli.EXIT_OK
    reg = json.loads(capsys.readouterr().out)["registration"]
    assert reg == {"document": "docs/pre-registration-v4-orgdesign.md", "sha256_declared": "not_declared",
                   "looked_in": str(tmp_path), "state": "registration_document_not_shipped",
                   "sha256_observed": "unwitnessed"}
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs/pre-registration-v4-orgdesign.md").write_text("# v4\n")
    rc = cli.main(["experiment", "check", str(toml), "--tree", str(tmp_path)])
    assert rc == cli.EXIT_OK
    reg = json.loads(capsys.readouterr().out)["registration"]
    assert reg["state"] == "present_sha_not_declared" and len(reg["sha256_observed"]) == 64


def test_experiment_check_refuses_a_template(tmp_path, capsys):
    exp = load_experiment(EXAMPLES / "scale0.toml")
    doc = tmp_path / "doc.md"
    doc.write_text("# TEMPLATE - NOT FROZEN\n\n" + to_conditions_block(exp))
    rc = cli.main(["experiment", "check", str(exp.path), "--block", str(doc)])
    assert rc == cli.EXIT_REFUSED
    assert json.loads(capsys.readouterr().out)["reason"] == "template_not_frozen"


def test_experiment_check_against_a_document(tmp_path, capsys):
    exp = load_experiment(EXAMPLES / "scale0.toml")
    doc = tmp_path / "doc.md"
    doc.write_text("# frozen\n\n" + to_conditions_block(exp))
    rc = cli.main(["experiment", "check", str(exp.path), "--block", str(doc)])
    assert rc == cli.EXIT_OK
    assert json.loads(capsys.readouterr().out)["mirror"]["verdict"] == "agreed"
    doc.write_text(doc.read_text().replace("CK3_BUDGET_ENFORCE=0", "CK3_BUDGET_ENFORCE=1"))
    rc = cli.main(["experiment", "check", str(exp.path), "--block", str(doc)])
    assert rc == cli.EXIT_REFUSED
    row = json.loads(capsys.readouterr().out)
    assert row["reason"] == "block_disagrees"
    assert row["disagreements"][0]["key"] == "CK3_BUDGET_ENFORCE"


def test_experiment_check_refuses_a_bad_file(tmp_path, capsys):
    p = tmp_path / "x.toml"
    p.write_text('[experiment]\nname = "x"\nintent = "pilot"\n')
    rc = cli.main(["experiment", "check", str(p)])
    assert rc == cli.EXIT_REFUSED
    assert json.loads(capsys.readouterr().out)["reason"] == "missing_key"


def test_print_block(capsys):
    rc = cli.main(["experiment", "check", str(EXAMPLES / "budget0.toml"), "--print-block"])
    assert rc == cli.EXIT_OK
    out = capsys.readouterr().out
    assert out == to_conditions_block(load_experiment(EXAMPLES / "budget0.toml"))


def test_version_is_the_dev_marker():
    from ck3bench import __version__
    assert __version__ == "0.0.0-dev"
