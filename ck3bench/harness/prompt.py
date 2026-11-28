"""The seat prompt: ONE template (prompts/seat_prompt.md), filled from the task the seat plays.

What a seat is told is reproducible from the task definition. The numbers come from the scenario itself: the
horizon in years and its end date, the steps and their cycle, the wake cadence (its check interval, its longest
step, its forced wakes, standing orders), the reward weights, and what holding the target title does to the episode
(ck3bench.env.scenario.title_rule). The words a scenario declaration does not carry come from the task's BRIEFING
below: who the seat is, where in the game it starts, the task sentence and the reward lines (which name the same
terms as the scenario's reward, checked).

A briefing is found by the scenario's id (a task whose seat is found by the census at open, its character id
unknown until then) or by the seat it plays (character id, start date, target title): any scenario with that seat,
start and title gets that seat's words, a test stand-in on the fake backend included. A decision task (the scenario
declares an `outcome`) that starts later than its seat's start plays the same words from a game already under way.
A task with no briefing is refused before anything starts; adding a task to the harness is adding its briefing.

    python -m ck3bench.harness prompt TASK [--notes-pad-bytes N]    (prints the rendered prompt)
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

#: the MCP server name the runner gives the seat's shim; every tool the seat calls is mcp__<SERVER>__<tool>
SERVER = "ck3seat"
TOOL_PREFIX = f"mcp__{SERVER}__"
TEMPLATE_PATH = Path(__file__).with_name("prompts") / "seat_prompt.md"
#: the turn-one notes pad bound the env carries by default (env play --notes-pad-bytes)
NOTES_PAD_BYTES = 4096

#: where a census seat's character id goes until the env has read the save (fill_census puts it in)
CENSUS_TOKEN = "<census id>"
#: the 1066 start, and the words a decision task's mid-game save is described with
START_1066 = "1066.09.15"
MID_GAME_PHRASE = ("in a game already under way, on {start_date} (the game began at its 1066 start, 1066.09.15; "
                   "your realm, family, claims, wars and treasury are as the game reads them now)")

#: --quiet-prompt on: the two lines the seat prompt ends with
QUIET_LINES = ("\n\nWrite no text between your tool calls: no narration and no plan in prose (your thinking is "
               "not shown and costs less).\nAfter end_turn write nothing: this conversation ends there and no one "
               "reads a closing message.\n")

_YEAR_WORDS = {2: "Two", 3: "Three", 5: "Five", 10: "Ten", 15: "Fifteen", 20: "Twenty", 25: "Twenty-five", 30: "Thirty",
               40: "Forty"}

#: the line a wake task with standing orders on (ck3bench.env.orders; a task's `wake.orders`) adds to the cadence
ORDERS_LINE = ("{tool_prefix}end_turn's `orders` may also leave up to 4 declare_war or do calls that the env sends, "
               "as you wrote them, at the first check where their conditions hold; each stands until it fires, you "
               "cancel it, your ruler changes or its `steps` pass (default 10), and each turn lists those standing. ")


@dataclass(frozen=True)
class Briefing:
    """The words of one task the scenario declaration does not carry, and the facts they are true of."""
    seat: str                      # who the seat is (a census seat's id is CENSUS_TOKEN)
    start: str                     # where in the game the seat starts
    task: str                      # the task sentence(s), after "N game-years (to END)."
    reward: str                    # the reward lines; {w_<term>} and {title_rule} are filled from the scenario
    terms: tuple[str, ...]         # the reward terms those lines describe (the scenario's must be these)
    start_date: str                # the scenario's start the words describe
    character_id: Optional[int] = None   # the seat's character; None for a census seat (found at open)
    target_title: Optional[str] = None


#: the line rule every task scores by (ck3bench.env.env: `line_ended`, `line_lost_to_other_dynasty`: the env judges
#: the new ruler's dynasty at every ruler change, on every task), as every de jure task's seat is told it
LINE_RULE_DYNASTY = ("- -{w_line} if your dynasty's line ends (no heir) or your titles pass to a ruler of another "
                     "dynasty;\n  either ends the episode there, as a loss.\n")


#: who must hold the target title for its term (the env's latch reads the seat's current ruler: the line's
#: character at that step, the heir after a succession); the scenario descriptions and the Munster note say the same
TITLE_HOLDER = "your ruler"


def _dejure_reward(region: str, title: str) -> str:
    return ("- +{w_dejure} for each county of de jure " + region + " that came into your realm during the step, "
            "-{w_dejure} for each that\n  left it (held by you, or by a vassal of yours at any depth);\n"
            "- +{w_title} on the step " + TITLE_HOLDER + " first holds " + title + "; {title_rule};\n"
            + LINE_RULE_DYNASTY + "Your return is the sum. Nothing else is scored.")


DEJURE_TERMS = ("dejure_counties_delta", "target_title_gained", "line_survival")
#: a once goal pays its title term under this name (ck3bench.env.rewards ONCE_GOALS)
ONCE_TITLE_TERM = "target_title_first_gained"

IRELAND_TASK = ("Bring as many counties of de jure Ireland (the\nkingdom title k_ireland: 14 counties) as you can "
                "into your realm, and create the Kingdom of Ireland.")
START_1066_PHRASE = "at the game's 1066 start (1066.09.15)"
START_867_PHRASE = "at the game's 867 start (867.01.01)"

#: the turn-one vetoes the line tasks need: the wars the ruler's AI would declare for him, and the factions it would
#: join or start for him (a faction he leads declares its war as his), fail the line goal
WAR_VETO_CALL = 'do(intent="ai_veto", args={"op": "set", "what": "war"})'
FACTION_VETO_CALL = 'do(intent="ai_veto", args={"op": "set", "what": "faction"})'
LINE_TASK = ("Keep your line ruling, in peace. The goal is judged once, at the end date,\n"
             "on four conditions that must all hold then:\n"
             "1. your line still has a ruler of your dynasty;\n"
             "2. the line has passed from one ruler to the next at least once (each death and succession counts once);\n"
             "3. no ruler of your line declared an offensive war: by your call or by the game's AI for him, and the war "
             "of a\n   faction he leads counts (he is its attacker); a war declared on you, or joining an ally's war, "
             "does not;\n"
             "4. your realm holds at least as many counties as at the start (held by you, or by a vassal of yours at "
             "any depth).\n"
             "Meeting them early does not end the episode: it runs to the end date. On your first turn call\n"
             + WAR_VETO_CALL + ": it drops every war your ruler's AI would declare for him\n"
             "(news ai_vetoed); a declare_war of your own is never dropped. Then call\n"
             + FACTION_VETO_CALL + ": it drops your ruler's AI joining or starting a faction\n"
             "(built, not proven live); a do faction of your own is never dropped.")
LINE_REWARD = ("- +{w_counties} for each county that came into your realm during the step, -{w_counties} for each that "
               "left it\n  (held by you, or by a vassal of yours at any depth);\n"
               "- -{w_line} if your line ends (no heir) or passes to another dynasty, which ends the episode.\n"
               "Your return is the sum. The goal above is judged apart from it, at the end date.")
LINE_TERMS = ("realm_counties_delta", "line_survival")

#: by the seat played: (character id, start date, target title). Names are the game's own (its history and
#: English localisation); county counts are the game's de jure counts at that start.
BY_SEAT: dict[tuple[int, str, Optional[str]], Briefing] = {}
for _b in (
    Briefing(seat="Murchad, Count of Dublin\n(character 31506)", start=START_1066_PHRASE, task=IRELAND_TASK,
             reward=_dejure_reward("Ireland", "k_ireland"), terms=DEJURE_TERMS, start_date=START_1066,
             character_id=31506, target_title="k_ireland"),
    Briefing(seat="Murchad ua Briain, Duke of Munster\n(character 31762)", start=START_1066_PHRASE, task=IRELAND_TASK,
             reward=_dejure_reward("Ireland", "k_ireland"), terms=DEJURE_TERMS, start_date=START_1066,
             character_id=31762, target_title="k_ireland"),
    Briefing(seat="Charles, King of West Francia\n(character 12015)", start=START_867_PHRASE,
             task=("Bring as many counties of de jure Francia (the\nempire title e_france: 117 counties in the kingdoms "
                   "of France, Aquitaine, Brittany and\nBurgundy) as you can into your realm, and create the Empire of "
                   "Francia: the game asks for more than\n80% of its counties (94 of 117) in your realm and two kingdom "
                   "titles held."),
             reward=_dejure_reward("Francia", "e_france"), terms=DEJURE_TERMS, start_date="0867.01.01",
             character_id=12015, target_title="e_france"),
):
    BY_SEAT[(_b.character_id, _b.start_date, _b.target_title)] = _b

#: by task id: the census-seat tasks (the seat named by the title its ruler holds at the start)
BY_TASK: dict[str, Briefing] = {
    "sweden867-kingdom-20y-wake": Briefing(
        seat="Björn, the holder of the duchy of Svealand (d_svealand)\n(character " + CENSUS_TOKEN + ")",
        start=START_867_PHRASE,
        task=("Bring as many counties of de jure Sweden (the\nkingdom title k_sweden: 25 counties) as you can into your "
              "realm, and create the Kingdom of Sweden: the game asks for at least half of its counties (13 of 25) in "
              "your realm and two duchy titles held."),
        reward=_dejure_reward("Sweden", "k_sweden"), terms=DEJURE_TERMS, start_date="0867.01.01"),
    "lorraine1066-line-25y-wake": Briefing(
        seat=("Gottfried, Duke of Lower Lorraine (d_lower_lorraine), a vassal in the Holy Roman\nEmpire "
              "(character " + CENSUS_TOKEN + ")"),
        start=START_1066_PHRASE, task=LINE_TASK, reward=LINE_REWARD, terms=LINE_TERMS, start_date=START_1066),
    "carinthia1066-line-25y-wake": Briefing(
        seat=("Berthold, Duke of Carinthia (d_carinthia), Carniola and Verona, a vassal in the\nHoly Roman Empire "
              "(character " + CENSUS_TOKEN + ")"),
        start=START_1066_PHRASE, task=LINE_TASK, reward=LINE_REWARD, terms=LINE_TERMS, start_date=START_1066),
}

#: the {w_<name>} fields the reward lines may name, by reward term
WEIGHT_FIELDS = (("w_dejure", "dejure_counties_delta"), ("w_title", "target_title_gained"),
                 ("w_title", ONCE_TITLE_TERM), ("w_counties", "realm_counties_delta"), ("w_line", "line_survival"))


class PromptRefused(SystemExit):
    """The prompt cannot be rendered for this task; nothing has started."""


def _refuse(msg: str):
    raise PromptRefused(f"seat prompt: {msg}")


def _num(w: float) -> str:
    return str(int(w)) if float(w).is_integer() else str(w)


def cadence_text(sc) -> str:
    """The template's {cadence}: the fixed cycle, or the wake task's cadence as declared."""
    if sc.wake is None:
        return (f"The game is divided into {sc.n_steps} steps of {sc.cycle_days} game-days: when you end your "
                f"turn the game runs {sc.cycle_days} days and pauses, and your next turn opens.")
    forced = {"ruler_changed": "your ruler changes (a death and succession)",
              "war_declared_on": "a war is declared on you"}
    f = [forced.get(e, e) for e in sc.wake.forced]
    return (f"Each step runs until something you asked to be woken for happens, then pauses: {TOOL_PREFIX}end_turn's `wake` "
            f"{{max_days, when}} names up to 8 conditions from your observation's `wake.conditions` (events, and "
            f"thresholds on gold, prestige, piety or your realm's counties), checked every {sc.wake.check_days} "
            f"game-days while the clock runs, at most {sc.wake.max_days} days. If you ask nothing the step runs "
            f"{sc.cycle_days} days. "
            + (f"Whatever you asked, you are also woken when {' or when '.join(f)}. " if f else "")
            + (ORDERS_LINE.format(tool_prefix=TOOL_PREFIX) if getattr(sc.wake, "orders", False) else "")
            + f"The episode ends on {sc.end_date}; how many turns that takes depends on what you ask for.")


def briefing_for(sc) -> tuple[Briefing, str]:
    """(the task's briefing, the start words this scenario is told) or a refusal naming why there is none."""
    from ck3bench.env import calendar
    b = BY_TASK.get(getattr(sc, "id", None))
    if b is not None:
        if sc.start_date != b.start_date or len(sc.seats) != 1 or getattr(sc.seats[0], "character_id", None):
            _refuse(f"scenario {sc.id} is not the census-seat task its text describes (start {b.start_date}, one "
                    "seat found by the census)")
        return b, b.start
    seat = sc.seats[0] if len(sc.seats) == 1 else None
    cid = getattr(seat, "character_id", None)
    mid = getattr(sc, "outcome", None) is not None and calendar.ordinal(sc.start_date) > calendar.ordinal(START_1066)
    key = (cid, START_1066 if mid else sc.start_date, sc.target_title)
    b = BY_SEAT.get(key)
    if b is None or (mid and b.start_date != START_1066):
        _refuse(f"scenario {getattr(sc, 'id', '?')} is {{'character_id': {cid!r}, 'start_date': {sc.start_date!r}, "
                f"'target_title': {sc.target_title!r}}}; no task briefing describes that seat, start and title "
                f"(the briefings: {sorted(BY_TASK)} by task, {sorted(BY_SEAT)} by seat)")
    return b, (MID_GAME_PHRASE.format(start_date=sc.start_date) if mid else b.start)


def render_prompt(sc, n_steps: int, notes_bytes: int = NOTES_PAD_BYTES, template: Optional[str] = None) -> str:
    """The seat prompt for scenario `sc` (a ck3bench Scenario). Refuses (PromptRefused, a SystemExit) when no
    briefing describes the task, when the scenario's reward terms are not the ones its reward lines describe, when
    --n-steps disagrees with the scenario, when the horizon is not whole years, or when the template names a field
    this does not fill."""
    from ck3bench.env import calendar
    from ck3bench.env.scenario import TITLE_ENDS, title_rule
    if template is None:
        template = TEMPLATE_PATH.read_text(encoding="utf-8")
    b, start = briefing_for(sc)
    rule = title_rule(sc)
    want = tuple(ONCE_TITLE_TERM if t == "target_title_gained" and rule not in (None, TITLE_ENDS) else t
                 for t in b.terms)
    terms = dict(sc.reward.terms)
    if tuple(sorted(terms)) != tuple(sorted(want)):
        _refuse(f"scenario {sc.id} reward terms {sorted(terms)}, the prompt describes {sorted(want)}")
    if n_steps != sc.n_steps:
        _refuse(f"--n-steps {n_steps} but scenario {sc.id} has {sc.n_steps} steps")
    if sc.horizon_days % calendar.DAYS_PER_YEAR:
        _refuse(f"horizon {sc.horizon_days} days is not whole years")
    if b.character_id is None and sc.wake is None:
        _refuse(f"scenario {sc.id} is a census-seat task and needs a wake cadence")
    if sc.wake is not None and "{cadence}" not in template:
        _refuse(f"scenario {sc.id} is a wake task and the template has no {{cadence}}")
    weights = {f: _num(terms[name]) for f, name in WEIGHT_FIELDS if name in terms}
    if "{title_rule}" in b.reward and rule is None:
        _refuse(f"scenario {sc.id}: its reward lines say what holding the title does, and its goal "
                f"{sc.goal!r} has no title rule")
    reward = b.reward.format(title_rule=rule or "", **weights)
    years = sc.horizon_days // calendar.DAYS_PER_YEAR
    fields = {"seat": b.seat, "start": start, "task": b.task, "reward": reward,
              "years_words": _YEAR_WORDS.get(years, str(years)), "end_date": sc.end_date,
              "n_steps": str(sc.n_steps), "cycle_days": str(sc.cycle_days), "cadence": cadence_text(sc),
              "notes_bytes": str(notes_bytes), "tool_prefix": TOOL_PREFIX}
    try:
        return template.format(**fields)
    except (KeyError, IndexError, ValueError) as e:
        _refuse(f"the template names a field this does not fill: {e!r}")


def fill_census(prompt: str, census_id) -> str:
    """The rendered prompt of a census-seat task with the seat's character id put in (read at open from the
    trajectory header). A prompt with no CENSUS_TOKEN is returned as it is; a token with no id is refused."""
    if CENSUS_TOKEN not in prompt:
        return prompt
    if not isinstance(census_id, int) or isinstance(census_id, bool) or census_id <= 0:
        raise SystemExit(f"seat prompt: the census seat's character id did not read at open ({census_id!r})")
    return prompt.replace(CENSUS_TOKEN, str(census_id))
