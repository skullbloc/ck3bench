"""The seat prompt: one template, filled from the task. No game, no model.

PINS are the sha256 of every built-in task's prompt as the runner renders it, at both pad sizes. For the Lorraine
and Carinthia tasks they are the bytes their recorded runs were told. The de jure tasks' reward lines state the
dynasty rule the env applies on every task (a line passing to a ruler of another dynasty ends the episode as a
loss), and the title term names the holder as the env reads it (the seat's ruler); for the Dublin, Francia, Sweden and
decision tasks RECORDED_BEFORE_DYNASTY_RULE keeps the bytes their recorded runs were told, which the prompt renders
with the old lines in those places.
"""
import dataclasses
import hashlib
import re
import subprocess
import sys

import pytest

from ck3bench.env.scenario import BUILTIN_DICTS, TITLE_ENDS, TITLE_ONCE, resolve
from ck3bench.harness import prompt as P

PINS = {
    "ireland1066-kingdom-15y@4096": "472d8fcc9034bb0ea42ccd535942d5866e3fed8014faaf100a12e6dfbc586e82",
    "ireland1066-kingdom-15y@6144": "7c49fc7075b6aa72c04fb7e787deee7fed17aa7b0f0b14219175e375a6bd9e6e",
    "ireland1066-kingdom-30y@4096": "a5231d80811109e3b9cd5087160b317aae2929a7c846d65c1b18812153d9885a",
    "ireland1066-kingdom-30y@6144": "ed3adfd3944f8128795655a43f301e1294a754386ea875d68d0cd1e749c2bc7c",
    "ireland1066-kingdom-30y-wake@4096": "dccaf737aed76e6086cca731646d7998de31f1b2d49322b097a13ecb54e99fd1",
    "ireland1066-kingdom-30y-wake@6144": "757a923237288ac75bf42505f678df71b3a8e8fb7e032bac1a2cb6591f073629",
    "ireland1066-kingdom-30y-wake180@4096": "6b0e873360dacbe045f2d6279e438d91f17f314e8866c0303b7d355e13dba226",
    "ireland1066-kingdom-30y-wake180@6144": "341bb108867404657a3336ca8366c3333543597af89461da4eb62950e2edda6c",
    "munster1066-kingdom-30y-wake@4096": "070afa2e021133ee67c142131100c194ed9c51df4d1153f8d4c2644a4af82626",
    "munster1066-kingdom-30y-wake@6144": "5c355bdf342821be6584fa9952396e28350e5ada95ebd446215d0206153469d8",
    "munster1066-kingdom-once-30y-wake@4096": "181b087195ee7c4484a53a11349da6d9b1ac1038e58b7ff7918ea06658b1f601",
    "munster1066-kingdom-once-30y-wake@6144": "aae99360e3ec039ac111e68e74c6b733783c334e8bafa60ff81911dfacf62093",
    "francia867-empire-40y-wake@4096": "d33a64638be15ba700706ff6bb84c7957a5bc5adcf0726d3e54e01f68c7a8d16",
    "francia867-empire-40y-wake@6144": "2a58b2463182dd579af0bcc76182d995c8a81e0f29eb4db835dcde51c416dc81",
    "sweden867-kingdom-20y-wake@4096": "4d73a89da46421be51e066bcea23519ef9f08367460d2220555bd801e79ebfd1",
    "sweden867-kingdom-20y-wake@6144": "36081130f618a2df56abb553442a0552189011621dda5282f4256c832453a364",
    "lorraine1066-line-25y-wake@4096": "ea19ca15c712c5433da7052b19aeecf97c79b4886d0b726e2e9ac72a09bcc4c6",
    "lorraine1066-line-25y-wake@6144": "d33b80fb141d0e82c81ca8f3cf3b8c82d4421f9d2595b4352f6e88d40d34f79c",
    "carinthia1066-line-25y-wake@4096": "2a481baa17bf603a5259f71ffa47b131874ef6b85037bf5837c113f5d061ccac",
    "carinthia1066-line-25y-wake@6144": "9b520875b4969aab85a640699f5cbed67ce7ad44d9d76e5459b0cac36a4b7015",
    "ireland1070-target-choice-3y@4096": "cd4c6db4734ba64fd2a3a5b5ee9952090d2d466a0865fadc04e75f6c874b5345",
    "ireland1070-target-choice-3y@6144": "14534bd39d6781cabec96869e532fa9a7254bb8ee85a473451459cb7365f0022",
    "ireland1075-succession-5y@4096": "72dfee2a9530cdaee026a9cb1d95e05f46f6e46511bf3629b4dc572759674d56",
    "ireland1075-succession-5y@6144": "0f0aa399011eb3aa925cf3df48b1bf083c05eb16d37c6a2cca0b78c060176412",
    "ireland1075-faction-3y@4096": "6968ece5086833e085e4fd139ab093a5cc1a53cfcb7a613e13e2688a3b8482c2",
    "ireland1075-faction-3y@6144": "0e71a9cf5177edc94a3d3bcf540a97852797097382837cacb2e61f0d64f66f2d",
    "ireland1076-succession-death-4y@4096": "3c836a41020c78dc4b73cd77554a9104a20d7d801c1a09b6f082f5da43ea3272",
    "ireland1076-succession-death-4y@6144": "3398878a66990060dedca7075f8f14889ee640d09b66f8c07ea02a9de1628345",
}

#: the bytes the recorded runs of these tasks were told, before their reward lines stated the dynasty rule the env
#: applies on every task (the line passing to a ruler of another dynasty ends the episode as a loss): the prompt now
#: renders them with the old line back in its place (test_the_recorded_prompts_are_the_new_ones_without_the_rule)
RECORDED_BEFORE_DYNASTY_RULE = {
    "francia867-empire-40y-wake@4096": "39281c2439b0ebbc1af891c52d51e5ce86eaf5fdedc6696ea0082dd8f8b2f62f",
    "francia867-empire-40y-wake@6144": "55319aac32570075eb0e25f8abe27a377d1ca95102b351874b7af3c0647e1f6f",
    "ireland1066-kingdom-15y@4096": "d0a7223b14acdfa3776832194b98efd252610205db8d4e574c3ff9dcd24b665c",
    "ireland1066-kingdom-15y@6144": "6d2679d69f5a6d40ceda35f87bba7776eb64880e098b94bf018035dc78b8614c",
    "ireland1066-kingdom-30y-wake180@4096": "d263a4f50b56d9e60f554208296121a8ef9317e3f4ae3b7969b33fa30c04b56a",
    "ireland1066-kingdom-30y-wake180@6144": "7b249f62d8337755a430a3ccb0057291abf35502dc76180a5ddec98a32b2692d",
    "ireland1066-kingdom-30y-wake@4096": "c60e3a91d69fdafe7b151fb4116d3253e4b8f430a91c296fc0f2432f86a31b41",
    "ireland1066-kingdom-30y-wake@6144": "26eec88aed37ee72c4b8f5e001258d84da2de0e267a8486162992de1ffdbb97f",
    "ireland1066-kingdom-30y@4096": "58675dc6bffb3d1a74f0eff7e178237922e4e9a2c02d743bad09a7ab7423ed54",
    "ireland1066-kingdom-30y@6144": "823e26db9d298223696b5e223c21b3730f94e8ee0e6bedc8262fba3495c5917c",
    "ireland1070-target-choice-3y@4096": "d107a22a736627f3cf3072f6288309b380404b712437b56b86433f9a30aefaa5",
    "ireland1070-target-choice-3y@6144": "29a3e931720f4bd8ac95aa5b189cf1ae473b50f3f57a7f943e3b935ffc798d95",
    "ireland1075-faction-3y@4096": "6f7c3f8ff75d47efaf1b4bf3636812835d51c3fb0776b74573c70b46289db1a5",
    "ireland1075-faction-3y@6144": "15dfdbfbe9571ac8c329252f72dba8a6dcbbeac30485140b4584f0adfc470aa2",
    "ireland1075-succession-5y@4096": "adf28662ef034c8720e8cfdd304998e800a1d11317c1396a5ede33bb638dc5fd",
    "ireland1075-succession-5y@6144": "4f92461f57b107fd90ab1943add3318a372ddb50d9248cc27aa636a788950735",
    "ireland1076-succession-death-4y@4096": "811488a686ed5e0a2b26489a8ad94b26d87655a2556690fbc4c91d5b84d924de",
    "ireland1076-succession-death-4y@6144": "265304a5f1cdb0a104e805aba5072b568e8108f8f650fd0d72357c084020fd53",
    "sweden867-kingdom-20y-wake@4096": "4fdc58af062ab31c09541822d5133964aeb7c6c8b32eb8f301ea5f78d946d34d",
    "sweden867-kingdom-20y-wake@6144": "4ce54e146e443b3d1dc1a4f90c444df36a3701fc8ae5df6f2488ec973fa202ec",
}
OLD_LINE_RULE = "- -{w_line} if your dynasty's line ends (no heir).\n"
#: the title term's holder in the two spellings the test compares (NEW is the template's)
OLD_HOLDER, NEW_HOLDER = "on the step your character first holds ", "on the step your ruler first holds "


def sha(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def render(task: str, notes: int = 4096) -> str:
    sc = resolve(task)
    return P.render_prompt(sc, sc.n_steps, notes)


@pytest.mark.parametrize("key", sorted(PINS))
def test_every_task_renders_the_bytes_its_seats_were_told(key):
    task, notes = key.split("@")
    assert sha(render(task, int(notes))) == PINS[key]


def test_the_recorded_prompts_are_the_new_ones_without_the_rule():
    """Every de jure task's prompt states the dynasty rule (LINE_RULE_DYNASTY); with the old line put back in its
    place each renders the bytes its recorded runs were told. CONTROL: the new pins are not those bytes."""
    w = {"w_line": "5"}
    new, old = P.LINE_RULE_DYNASTY.format(**w), OLD_LINE_RULE.format(**w)
    for key, recorded in RECORDED_BEFORE_DYNASTY_RULE.items():
        task, notes = key.split("@")
        p = render(task, int(notes))
        assert p.count(new) == 1 and "passes to a ruler of another" not in p.replace(new, ""), key
        assert p.count(NEW_HOLDER) == 1 and OLD_HOLDER not in p, key
        assert sha(p.replace(new, old).replace(NEW_HOLDER, OLD_HOLDER)) == recorded and PINS[key] != recorded, key
    for k in PINS:
        t = resolve(k.split("@")[0])
        if dict(t.reward.terms).get("dejure_counties_delta"):
            assert new in render(t.id), t.id


def test_the_pins_cover_every_task_a_briefing_describes_and_nothing_else_renders():
    rendered = set()
    for k in BUILTIN_DICTS:
        try:
            render(k)
            rendered.add(k)
        except SystemExit as e:
            assert "no task briefing" in str(e), (k, e)
    assert rendered == {k.split("@")[0] for k in PINS}
    assert len(rendered) == 14


def test_the_template_leaves_no_field_unfilled_and_names_every_tool_with_the_prefix():
    for task in {k.split("@")[0] for k in PINS}:
        p = render(task)
        assert not re.search(r"\{[a-z_]+\}", p), (task, re.search(r"\{[a-z_]+\}", p))
        bare = [m.group(0) for m in re.finditer(r"(?<![\w}])(end_turn|seat_observation|await_step)\b", p)]
        assert bare == [], (task, bare)
        assert f"{P.TOOL_PREFIX}end_turn" in p


def test_the_task_numbers_come_from_the_scenario():
    sc = resolve("munster1066-kingdom-once-30y-wake")
    p = render(sc.id)
    assert "Thirty game-years (to 1096.09.15)" in p and sc.end_date == "1096.09.15"
    w = dict(sc.reward.terms)
    assert f"+{int(w['target_title_first_gained'])} on the step" in p and f"-{int(w['line_survival'])} if your" in p
    assert TITLE_ONCE in p and TITLE_ENDS not in p
    assert f"checked every {sc.wake.check_days} game-days" in p and f"at most {sc.wake.max_days} days" in p
    assert "A private pad of at most 4096 bytes" in p and "at most 6144 bytes" in render(sc.id, 6144)


def test_the_once_task_differs_from_its_twin_by_the_title_clause_only():
    assert render("munster1066-kingdom-30y-wake").replace(TITLE_ENDS, TITLE_ONCE) == \
        render("munster1066-kingdom-once-30y-wake")


def test_a_seat_s_words_are_its_own():
    p = render("munster1066-kingdom-30y-wake")
    assert "Murchad ua Briain, Duke of Munster\n(character 31762)" in p and "Dublin" not in p
    f = render("francia867-empire-40y-wake")
    assert "Charles, King of West Francia" in f and not re.search(r"Ireland|k_ireland|Murchad|1066", f)


def test_a_decision_task_is_told_its_game_is_already_under_way():
    p = render("ireland1070-target-choice-3y")
    assert "in a game already under way, on 1070.01.01" in p and P.START_1066_PHRASE not in p


def test_a_census_seat_gets_its_id_at_open_and_is_refused_without_one():
    p = render("sweden867-kingdom-20y-wake")
    assert p.count(P.CENSUS_TOKEN) == 1
    assert "(character 12330)" in P.fill_census(p, 12330) and P.CENSUS_TOKEN not in P.fill_census(p, 12330)
    for bad in (None, 0, -1, True, "12330"):
        with pytest.raises(SystemExit, match="did not read"):
            P.fill_census(p, bad)
    fr = render("francia867-empire-40y-wake")
    assert P.fill_census(fr, None) == fr


def test_a_task_no_briefing_describes_is_refused():
    with pytest.raises(SystemExit, match="no task briefing"):
        render("france1066-hold-10y")
    sc = resolve("munster1066-kingdom-30y-wake")
    other = dataclasses.replace(sc, seats=(dataclasses.replace(sc.seats[0], character_id=29585),))
    with pytest.raises(SystemExit, match="no task briefing"):
        P.render_prompt(other, other.n_steps)


def test_a_census_task_refuses_a_fixed_seat():
    sc = resolve("lorraine1066-line-25y-wake")
    fixed = dataclasses.replace(sc, seats=(dataclasses.replace(sc.seats[0], character_id=28101),))
    with pytest.raises(SystemExit, match="census-seat task"):
        P.render_prompt(fixed, fixed.n_steps)


def test_disagreeing_steps_terms_horizon_or_fields_are_refused():
    sc = resolve("ireland1066-kingdom-30y-wake")
    with pytest.raises(SystemExit, match="n-steps 75 but"):
        P.render_prompt(sc, 75)
    other = dataclasses.replace(sc, reward=dataclasses.replace(sc.reward, terms=(("dejure_counties_delta", 1.0),)))
    with pytest.raises(SystemExit, match="reward terms"):
        P.render_prompt(other, other.n_steps)
    odd = dataclasses.replace(sc, horizon_days=sc.horizon_days + 1)
    with pytest.raises(SystemExit, match="not whole years"):
        P.render_prompt(odd, odd.n_steps)
    with pytest.raises(SystemExit, match="does not fill"):
        P.render_prompt(sc, sc.n_steps, template=P.TEMPLATE_PATH.read_text(encoding="utf-8") + "{nonsense}")


def test_a_stand_in_on_the_fake_backend_renders_the_dublin_seat_s_words():
    from pathlib import Path
    sc = resolve(str(Path(P.__file__).with_name("testdata") / "standin-ireland-fake.toml"))
    assert sc.backend == "fake"
    assert "Murchad, Count of Dublin" in P.render_prompt(sc, sc.n_steps)
    with pytest.raises(SystemExit, match="no task briefing"):
        render("fake-kingdom")


def test_the_prompt_verb_prints_what_the_runner_sends():
    r = subprocess.run([sys.executable, "-m", "ck3bench.harness", "prompt", "munster1066-kingdom-once-30y-wake"],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    assert sha(r.stdout) == PINS["munster1066-kingdom-once-30y-wake@4096"]
