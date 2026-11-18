"""The experiment file: ck3bench's single declaration.

The TOML experiment file is the single declaration, and the registration's
conditions block (the ```registered-conditions fence the episode launcher
parses) is GENERATED from it by
`to_conditions_block`, frozen as today, with `check_block` refusing when a
frozen block and the TOML disagree. One declaration, several readers, the
mirror checked.

Three readers of one TOML:
  - `load_experiment`      every key named in SCHEMA, typed; an unknown key is
                           `unknown_key`, a missing required key is
                           `missing_key` naming the key, a wrong type is
                           `wrong_type`. Nothing defaults.
  - `to_conditions_block`  the subset BLOCK_MAP names, rendered one KEY=VALUE
                           per line inside the fence, in BLOCK_MAP order.
  - `check_block`          the fence of a frozen document read back (its own
                           small reader, `read_block`; the launcher's parser
                           does not ship with ck3bench) and
                           compared key by key with what the TOML declares.

Host-specific runner values (the run lock's path, the display, the ollama unit,
the save directory, the tarball) NEVER live in a tracked experiment file: they
come from an untracked `<name>.local.toml` beside it, whose only table is
`[runner]` and whose only keys are RUNNER_HOST_KEYS (plus `hosts_forbidden`,
the overlay's own list of host names). A tracked file carrying one of those
keys, a path under /home, /mnt or /tmp anywhere in its bytes, or a name the
overlay forbids, is refused. This module names no host itself: it ships.

The block's vocabulary is the launcher's at pre-registration v4 (its
scalar and list keys, the channel's and the notepad's). Every key the
TOML declares is emitted: the census of the named title (the title declared,
the census run's lines written from the start save), the two per-role model
ids, the billing accounts and the episode's account, the decision cycle and
the horizon's length, one topology and one notepad line per arm the launcher
knows (BLOCK_TOPOLOGY_ARMS; a cell outside that list is reported as not
emitted, never dropped silently), the bounds, the run-order seed and the
added tools. A line the registration still owes is declared in `[pending]`
(block key -> who fills it) and rendered `KEY=<pending_<filler>>`, which the
launcher refuses at parse: a pending registration cannot be frozen.
The launcher is not part of this distribution; a round-trip test beside it
pins BLOCK_KEYS and BLOCK_TOPOLOGY_ARMS against its tables, so a parser that
grows reds there rather than leaving this generator silently short.
"""
from __future__ import annotations

import hashlib
import importlib.metadata
import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

# ── refusals ────────────────────────────────────────────────────────────────

R_FILE_MISSING = "file_missing"
R_FILE_UNREADABLE = "file_unreadable"
R_TOML_INVALID = "toml_invalid"
R_UNKNOWN_KEY = "unknown_key"
R_MISSING_KEY = "missing_key"
R_WRONG_TYPE = "wrong_type"
R_INVALID_VALUE = "invalid_value"
R_HOST_PATH_IN_TRACKED_FILE = "host_path_in_tracked_file"
R_HOST_KEY_IN_TRACKED_FILE = "host_key_in_tracked_file"
R_OVERLAY_KEY_NOT_HOST = "overlay_key_not_host"
R_BLOCK_DISAGREES = "block_disagrees"
R_NO_BLOCK = "no_conditions_block"
R_MULTIPLE_BLOCKS = "multiple_conditions_blocks"
R_MALFORMED_LINE = "malformed_line"
R_DUPLICATE_KEY = "duplicate_key"
R_TEMPLATE_NOT_FROZEN = "template_not_frozen"


class ExperimentRefusal(ValueError):
    """A typed refusal. `reason` is one of the R_* codes; `key` names the leg."""

    def __init__(self, reason: str, detail: str, *, key: Optional[str] = None,
                 path: Optional[str] = None, **extra: Any):
        self.reason = reason
        self.key = key
        self.path = path
        self.detail = detail
        self.extra = dict(extra)
        where = f"{path}: " if path else ""
        which = f"{key}: " if key else ""
        super().__init__(f"{where}{reason}: {which}{detail}")

    def as_dict(self) -> dict:
        row = {"reason": self.reason, "key": self.key, "path": self.path,
               "detail": self.detail}
        row.update(self.extra)
        return row


# ── host paths ──────────────────────────────────────────────────────────────

#: A path under a per-user, per-mount or scratch root (/home, /mnt, /tmp)
#: names somebody's machine. A tracked experiment file is shared, so one
#: carrying such a path anywhere in its bytes (comments included: a comment
#: value is a mapping input to any later scan) is refused. The names of the
#: machines an operator actually uses are theirs to declare, in the untracked
#: overlay's `[runner].hosts_forbidden`; this module names none, since it ships.
HOST_PATH_RE = re.compile(rb"/(?:home|mnt|tmp)/\S")

#: `[runner]` keys that name a host. Overlay-only; refused in a tracked file.
RUNNER_HOST_KEYS = ("lock", "display", "xauthority_glob", "ollama_unit",
                    "saves_dir", "tarball", "root", "game_install")

#: The overlay's list of names (hostnames, accounts, paths) that the tracked
#: file must not carry, matched as byte substrings. Overlay-only, and not a
#: runner value: it is not merged into `data`.
HOSTS_FORBIDDEN_KEY = "hosts_forbidden"

OVERLAY_SUFFIX = ".local.toml"

# ── the schema ──────────────────────────────────────────────────────────────

#: Key types. `number` is int or float (never bool); `int` excludes bool.
#: `choice:a|b` is a str from the set; `date` is a CK3 Y.M.D; `sha256` is 64
#: lowercase hex; `date_pair` is a list of two dates; `table[str]` is a table
#: of free identifier keys to str values.
_DATE_RE = re.compile(r"^\d{3,4}\.\d{1,2}\.\d{1,2}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_IDENT_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
_TOOL_RE = re.compile(r"^[a-z][a-z0-9_]*$")


@dataclass(frozen=True)
class Key:
    type: str
    required: bool = True


SCHEMA: dict[str, dict[str, Key]] = {
    "experiment": {
        "name": Key("str"),
        "intent": Key("choice:smoke|pilot|registered"),
        "description": Key("str", required=False),
    },
    "registration": {
        "version": Key("str"),
        "document": Key("str", required=False),
        "sha256": Key("sha256", required=False),
    },
    "toolkit": {
        "ck3agent": Key("str"),
        "tarball_sha256": Key("sha256", required=False),
        "claude_code": Key("str", required=False),
        # the wrapper a seat runs under, read off the pinned wheel; a cell
        # that is not dry refuses an installed toolkit whose bytes differ
        # (`ck3bench run`: `wrapper_not_registered`)
        "run_prompt_md_sha256": Key("sha256", required=False),
        "continuation_text_sha256": Key("sha256", required=False),
        # the wheel's cli.py and _bridge.py, whose unnamed stop literals the
        # cell runner copies; other bytes are refused
        "cli_py_sha256": Key("sha256", required=False),
        "bridge_py_sha256": Key("sha256", required=False),
        # the cell's declared bound on a seat's stop (`ck3bench run`)
        "seat_stop_wait_s": Key("int", required=False),
    },
    "save": {
        "basename": Key("str"),
        "sha256": Key("sha256"),
    },
    "episode": {
        "mode": Key("choice:experiment|free_play"),
        "strict_build_id": Key("bool"),
        # v4: the horizon is [start, end], or its start alone while the end is
        # PENDING ([pending] lists CK3_EPISODE_HORIZON_END); its length in
        # game-years is its own declared line (horizon.game_years), and the
        # end must be the start + that length (`_cross_rules`)
        "horizon": Key("date_pair", required=False),
        "horizon_start": Key("date", required=False),
        "horizon_game_years": Key("int", required=False),
        "game_speed": Key("int"),
        "unavail_exclusion_fraction": Key("float"),
        "observer_cadence_min_days": Key("int", required=False),
        "flush_period_ms": Key("int", required=False),
        # v4: the standing-policy mod; the cell runner installs it into its own
        # game dir and witnesses it
        "policy_mod": Key("bool", required=False),
        # the incoming-proposal mod (ck3agent's held AI offers); absent = not
        # installed. Declaring it is a pre-registration change: no registered
        # document carries it yet.
        "proposal_mod": Key("bool", required=False),
    },
    "seats": {
        "source": Key("choice:census|list"),
        "rule": Key("str", required=False),
        "n_dukes": Key("int", required=False),
        # v4: the NAMED title (census.title_key, e.g. k_france)
        "title_key": Key("str", required=False),
        # the census's lines, written from the census run on the start save;
        # all four or none (`_cross_rules`), never one guessed.
        "king_id": Key("int", required=False),
        "king_title_id": Key("int", required=False),
        "duke_ids": Key("list[int]", required=False),
        "realm_counties": Key("int", required=False),
        # the registered census FREEZE a cell plans from,
        # repo-relative, and its sha256; the cell's census phase re-elects live
        # against it (`--expect`) and refuses a difference
        "census_freeze": Key("str", required=False),
        "census_freeze_sha256": Key("sha256", required=False),
        "ids": Key("list[int]", required=False),
        "model": Key("str", required=False),
        "cycles": Key("int", required=False),
        "cycle_game_days": Key("int", required=False),
        "wait_seconds": Key("int", required=False),
        "wait_game_days": Key("int", required=False),
        "max_turns": Key("int", required=False),
        "wall_seconds": Key("int", required=False),
        "goals": Key("list[str]", required=False),
    },
    # the roles a cell's `king` / `duke` may name, each the TOML spelling of
    # one block line (ROLE_BLOCK_KEYS); a role nothing renders is unknown_key
    "seats.model_id": {
        "king": Key("str", required=False),
        "duke": Key("str", required=False),
    },
    "seats.billing": {
        "accounts": Key("list[choice:subscription|api_key]"),
        # the episode's account, one of `accounts`
        "account": Key("choice:subscription|api_key", required=False),
        # emitted only when `account` is api_key
        "key_file": Key("str", required=False),
    },
    "channel": {
        "max_text_bytes": Key("int"),
        "quota_per_cycle": Key("int"),
    },
    # v4: the notepads' bounds, in bytes. Optional
    # as a section (a v3 experiment has none); present, all three are required.
    "notepad": {
        "private_max_bytes": Key("int"),
        "council_max_bytes": Key("int"),
        "max_write_bytes": Key("int"),
    },
    # v4: the guide every seat is served, declared by its manifest's sha256
    # (ck3agent's guide); one for every arm;
    # REQUIRED for a version-4 block (`_cross_rules`).
    "guide": {
        "manifest_sha256": Key("sha256"),
    },
    "budget": {
        "enforce": Key("bool"),
        "per_agent_per_year": Key("int"),
        "ceiling_basis": Key("str"),
        "cap_usd": Key("number", required=False),
        "opus_ratio": Key("number", required=False),
        "deadline": Key("str", required=False),
    },
    "tool_surface": {
        "held_constant": Key("list[tool]"),
        "channel_arms_add": Key("list[tool]", required=False),
        # v4: the two tools the notepad arms add (note_read, note_write)
        "notepad_arms_add": Key("list[tool]", required=False),
        # v4: the tool the guide adds (guide_read)
        "guide_arms_add": Key("list[tool]", required=False),
    },
    "runner": {
        "memory_floor_gb": Key("number", required=False),
        "per_seat_gb": Key("number", required=False),
        "growth_gb": Key("number", required=False),
        "timeout_pct_max": Key("number", required=False),
        # host keys: overlay only (RUNNER_HOST_KEYS)
        "lock": Key("str", required=False),
        "display": Key("str", required=False),
        "xauthority_glob": Key("str", required=False),
        "ollama_unit": Key("str", required=False),
        "saves_dir": Key("str", required=False),
        "tarball": Key("str", required=False),
        "root": Key("str", required=False),
        # the CK3 install the proposal mod's copies are generated from
        "game_install": Key("str", required=False),
    },
    "sweep": {
        "n": Key("list[int]"),
    },
    "passes[]": {
        "model": Key("str"),
        "episodes": Key("int"),
    },
    "cells.*": {
        "arm": Key("str"),
        "topology": Key("choice:complete|none"),
        # v4: the arm's pads (`notepad.pads.<arm>`); absent: the
        # experiment declares no notepad for the cell
        "notepad": Key("choice:none|private|private,council", required=False),
        # v4: N per arm is fixed at signing from the unsteered set's
        # spread; a cell without it says so (check_report)
        "episodes": Key("int", required=False),
        "king": Key("str"),
        "duke": Key("str"),
        "king_steered": Key("bool"),
        "dukes_steered": Key("bool"),
        # v4: a steered seat boots its ROLE's play file and goal text, both
        # declared by the guide's manifest ([guide]); a cell names no file
        "exploratory": Key("bool"),
        "n_dukes": Key("int", required=False),
        "description": Key("str", required=False),
    },
    "run_order": {
        "seed": Key("int", required=False),
        "blocks": Key("int", required=False),
    },
}

#: Sections a file must carry. The rest are optional as a whole; present, every
#: required key inside them is required.
REQUIRED_SECTIONS = ("experiment", "registration", "toolkit", "save", "episode",
                     "seats", "budget", "tool_surface")

#: The arms whose `channel.topology.<arm>` line the block carries: the
#: launcher's arms (v4's three: e0, k1, council), MIRRORED here because
#: ck3bench ships without the launcher; a round-trip test beside the launcher
#: checks the mirror. A `[cells.<name>]`
#: outside this list is a cell the launcher has no arm for: its topology is
#: reported by `check_report` as not emitted, never written into the block.
BLOCK_TOPOLOGY_ARMS: tuple[str, ...] = ("e0", "k1", "council")

#: The arms whose `notepad.pads.<arm>` line the block may carry: the
#: launcher's notepad arms (the same three), MIRRORED here; the round-trip
#: test checks the mirror.
BLOCK_NOTEPAD_ARMS: tuple[str, ...] = BLOCK_TOPOLOGY_ARMS

#: `[seats.model_id]` role -> the block line it renders (the launcher's model
#: keys: v4, an Opus king and Sonnet dukes, one line per role). A cell's `king` /
#: `duke` names one of these roles.
ROLE_BLOCK_KEYS: dict[str, str] = {
    "king": "seat.model_id.king",
    "duke": "seat.model_id.duke",
}

#: The `[seats]` keys the census run writes, together or not at all.
CENSUS_ID_KEYS = ("king_id", "king_title_id", "duke_ids", "realm_counties")

#: v4: who fills a PENDING block line (the launcher's fillers, MIRRORED; the
#: round-trip test checks the mirror). `[pending]` maps a block key to one of
#: these; the block then carries `KEY=<pending_<filler>>`, which the launcher
#: refuses at parse, so a registration with a pending line cannot be frozen.
PENDING_FILLERS: tuple[str, ...] = ("census", "pilot", "save", "freeze")


def pending_placeholder(filler: str) -> str:
    """`<pending_census>`: the launcher's placeholder spelling."""
    return f"<pending_{filler}>"

#: Block key -> (section, key); a dotted section is a sub-table. Order is the
#: block's order. The vocabulary is the launcher's; see the module docstring.
BLOCK_MAP: tuple[tuple[str, str, str], ...] = (
    ("registration.version", "registration", "version"),
    ("CK3_EPISODE_MODE", "episode", "mode"),
    ("CK3_STRICT_BUILD_ID", "episode", "strict_build_id"),
    ("CK3_BUDGET_ENFORCE", "budget", "enforce"),
    ("CK3_BUDGET_PER_AGENT_PER_YEAR", "budget", "per_agent_per_year"),
    ("budget.ceiling_basis", "budget", "ceiling_basis"),
    ("CK3_UNAVAIL_EXCLUSION_FRACTION", "episode", "unavail_exclusion_fraction"),
    ("CK3_EPISODE_HORIZON_START", "episode", "horizon"),
    ("CK3_EPISODE_HORIZON_END", "episode", "horizon"),
    ("CK3_EPISODE_GAME_SPEED", "episode", "game_speed"),
    ("CK3_EPISODE_OBSERVER_CADENCE_MIN_DAYS", "episode", "observer_cadence_min_days"),
    ("CK3_EPISODE_FLUSH_PERIOD_MS", "episode", "flush_period_ms"),
    ("start_save.sha256", "save", "sha256"),
    ("start_save.basename", "save", "basename"),
    # v4: the horizon's length, in game-years
    ("horizon.game_years", "episode", "horizon_game_years"),
    # v4: the census of the named title, in the launcher's order
    ("census.rule", "seats", "rule"),
    ("census.title_key", "seats", "title_key"),
    ("census.king_id", "seats", "king_id"),
    ("census.king_title_id", "seats", "king_title_id"),
    ("census.duke_ids", "seats", "duke_ids"),
    ("census.n_dukes", "seats", "n_dukes"),
    ("census.realm_counties", "seats", "realm_counties"),
    # v4: the seat model ids
    *((bkey, "seats.model_id", role) for role, bkey in ROLE_BLOCK_KEYS.items()),
    # the billing accounts and the episode's account
    ("seat.billing.accounts", "seats.billing", "accounts"),
    ("seat.billing.account", "seats.billing", "account"),
    ("seat.billing.key_file", "seats.billing", "key_file"),
    # the decision cycle
    ("cycle.game_days", "seats", "cycle_game_days"),
    # the channel, one line per arm, then the bounds
    *((f"channel.topology.{arm}", f"cells.{arm}", "topology") for arm in BLOCK_TOPOLOGY_ARMS),
    ("channel.max_text_bytes", "channel", "max_text_bytes"),
    ("channel.quota_per_cycle", "channel", "quota_per_cycle"),
    # v4: the notepads, one line per arm, then the bounds
    *((f"notepad.pads.{arm}", f"cells.{arm}", "notepad") for arm in BLOCK_NOTEPAD_ARMS),
    ("notepad.private_max_bytes", "notepad", "private_max_bytes"),
    ("notepad.council_max_bytes", "notepad", "council_max_bytes"),
    ("notepad.max_write_bytes", "notepad", "max_write_bytes"),
    # v4: the guide, one line for every arm
    ("guide.manifest_sha256", "guide", "manifest_sha256"),
    # the run order
    ("run_order.seed", "run_order", "seed"),
    # the tool surface
    ("tool_surface.held_constant", "tool_surface", "held_constant"),
    ("tool_surface.channel_arms_add", "tool_surface", "channel_arms_add"),
    ("tool_surface.notepad_arms_add", "tool_surface", "notepad_arms_add"),
    ("tool_surface.guide_arms_add", "tool_surface", "guide_arms_add"),
)
BLOCK_LIST_KEYS = ("tool_surface.held_constant", "tool_surface.channel_arms_add",
                   "tool_surface.notepad_arms_add", "tool_surface.guide_arms_add")
BLOCK_SCALAR_KEYS = tuple(k for k, _, _ in BLOCK_MAP if k not in BLOCK_LIST_KEYS)
BLOCK_KEYS = BLOCK_SCALAR_KEYS + BLOCK_LIST_KEYS

#: Comments the generated block carries above each group, keyed by the first
#: block key of the group. Comments only; the parser and `read_block` skip them.
_BLOCK_COMMENTS = {
    "registration.version": "the registration",
    "CK3_EPISODE_MODE": "the episode mode and the strictness leg",
    "CK3_BUDGET_ENFORCE": "the budget lines",
    "CK3_UNAVAIL_EXCLUSION_FRACTION": "the single constant and the horizon",
    "horizon.game_years": "the horizon's length, in game-years",
    "CK3_EPISODE_GAME_SPEED": "the game speed pin and the cadences",
    "start_save.sha256": "the start save",
    "census.rule": "the census of the named title (its run's lines written from the start save)",
    "seat.model_id.king": "the seat model ids",
    "seat.billing.accounts": "the billing accounts (a record, never a condition)",
    "cycle.game_days": "the decision cycle, in game-days",
    f"channel.topology.{BLOCK_TOPOLOGY_ARMS[0]}": "the channel, one line per arm, then its bounds",
    f"notepad.pads.{BLOCK_NOTEPAD_ARMS[0]}": "the notepads, one line per arm, then their bounds",
    "guide.manifest_sha256": "the guide every seat is served (its manifest's sha256)",
    "run_order.seed": "the run order",
    "tool_surface.held_constant": "the tool surface",
}

#: The launcher's template marker, MIRRORED here because ck3bench ships
#: without the launcher (the round-trip test pins it): a document carrying it is a template, never a frozen registration,
#: and `read_block` refuses it before it reads a fence, as the launcher does.
TEMPLATE_MARKER = "TEMPLATE - NOT FROZEN"

_FENCE_OPEN = re.compile(r"^\s*```registered-conditions\s*$")
_FENCE_CLOSE = re.compile(r"^\s*```\s*$")
_BLOCK_KEY_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.]*$")


# ── the loaded experiment ───────────────────────────────────────────────────

@dataclass
class Experiment:
    """A loaded, checked experiment. `data` is the TOML with the overlay's
    `[runner]` host keys merged in; `runner_host` says where they came from:
    `overlay` (the untracked file beside the tracked one) or `absent`."""
    name: str
    path: Path
    data: dict
    tracked_sha256: str
    overlay_path: Optional[Path] = None
    runner_host: str = "absent"
    hosts_forbidden: tuple[str, ...] = ()

    @property
    def intent(self) -> str:
        return self.data["experiment"]["intent"]

    @property
    def cells(self) -> dict[str, dict]:
        return dict(self.data.get("cells", {}))

    @property
    def pending(self) -> dict[str, str]:
        """v4: block key -> who fills it (`[pending]`), the lines still owed."""
        return dict(self.data.get("pending", {}))

    def block_values(self) -> tuple[dict[str, str], dict[str, list[str]]]:
        """What the block must say: scalars and lists, rendered as the block
        renders them. Optional keys the TOML omits are omitted."""
        scalars: dict[str, str] = {}
        lists: dict[str, list[str]] = {}
        pending = self.pending
        for bkey, section, key in BLOCK_MAP:
            if bkey in pending:
                scalars[bkey] = pending_placeholder(pending[bkey])
                continue
            table = _table(self.data, section)
            if bkey == "CK3_EPISODE_HORIZON_START" and key not in table and "horizon_start" in table:
                scalars[bkey] = table["horizon_start"]
                continue
            if key not in table:
                continue
            value = table[key]
            if bkey in BLOCK_LIST_KEYS:
                lists[bkey] = list(value)
            elif bkey == "CK3_EPISODE_HORIZON_START":
                scalars[bkey] = value[0]
            elif bkey == "CK3_EPISODE_HORIZON_END":
                scalars[bkey] = value[1]
            elif bkey in ("census.duke_ids", "seat.billing.accounts"):
                scalars[bkey] = ",".join(_render(v) for v in value)
            elif bkey == "seat.billing.key_file":
                # declared only when the episode's account is api_key
                if table.get("account") == "api_key":
                    scalars[bkey] = value
            else:
                scalars[bkey] = _render(value)
        return scalars, lists


def _table(data: dict, section: str) -> dict:
    """The (sub-)table a dotted section names, or {} when any step is absent."""
    cur: Any = data
    for part in section.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return {}
        cur = cur[part]
    return cur if isinstance(cur, dict) else {}


def _render(value: Any) -> str:
    """One rendering for every scalar the block carries. bool -> '1'/'0' (the
    bridge reads exactly those two spellings); float -> repr (shortest
    round-tripping form, so 0.05 renders as '0.05')."""
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    return str(value)


# ── typing ──────────────────────────────────────────────────────────────────

def _check_type(path: str, dotted: str, spec: Key, value: Any) -> None:
    t = spec.type

    def bad(expected: str) -> ExperimentRefusal:
        return ExperimentRefusal(
            R_WRONG_TYPE, f"expected {expected}, got {type(value).__name__} "
            f"({value!r})", key=dotted, path=path)

    if t == "str":
        if not isinstance(value, str):
            raise bad("a string")
    elif t == "bool":
        if not isinstance(value, bool):
            raise bad("a boolean")
    elif t == "int":
        if isinstance(value, bool) or not isinstance(value, int):
            raise bad("an integer")
    elif t == "float":
        if isinstance(value, bool) or not isinstance(value, float):
            raise bad("a float (write 1.0, not 1)")
    elif t == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise bad("a number")
    elif t == "sha256":
        if not isinstance(value, str):
            raise bad("a lowercase sha256 string")
        if not _SHA256_RE.match(value):
            raise ExperimentRefusal(R_INVALID_VALUE, f"{value!r} is not a lowercase sha256",
                                    key=dotted, path=path)
    elif t == "date":
        if not isinstance(value, str) or not _DATE_RE.match(value):
            raise bad("a CK3 date Y.M.D as a string")
    elif t == "date_pair":
        if not (isinstance(value, list) and len(value) == 2
                and all(isinstance(v, str) for v in value)):
            raise bad("a list of two CK3 dates [start, end]")
        for v in value:
            if not _DATE_RE.match(v):
                raise ExperimentRefusal(R_INVALID_VALUE, f"{v!r} is not a CK3 date (Y.M.D)",
                                        key=dotted, path=path)
    elif t.startswith("choice:"):
        choices = t.split(":", 1)[1].split("|")
        if not isinstance(value, str):
            raise bad(f"one of {choices}")
        if value not in choices:
            raise ExperimentRefusal(R_INVALID_VALUE, f"{value!r} is not one of {choices}",
                                    key=dotted, path=path)
    elif t.startswith("list["):
        inner = t[5:-1]
        if not isinstance(value, list):
            raise bad(f"a list of {inner}")
        for i, v in enumerate(value):
            if inner == "tool":
                if not isinstance(v, str) or not _TOOL_RE.match(v):
                    raise ExperimentRefusal(R_INVALID_VALUE, f"{v!r} is not an action name",
                                            key=f"{dotted}[{i}]", path=path)
            else:
                _check_type(path, f"{dotted}[{i}]", Key(inner), v)
    else:  # pragma: no cover - a schema typo, not a document defect
        raise AssertionError(f"unknown schema type {t!r} at {dotted}")


def _check_table(path: str, dotted: str, spec: dict[str, Key], table: Any,
                 pending: frozenset = frozenset()) -> None:
    if not isinstance(table, dict):
        raise ExperimentRefusal(R_WRONG_TYPE, f"expected a table, got {type(table).__name__}",
                                key=dotted, path=path)
    free = spec.get("*")
    for key, value in table.items():
        if free is not None:
            if not _IDENT_RE.match(key):
                raise ExperimentRefusal(R_INVALID_VALUE, f"{key!r} is not an identifier",
                                        key=f"{dotted}.{key}", path=path)
            _check_type(path, f"{dotted}.{key}", free, value)
            continue
        if key not in spec:
            raise ExperimentRefusal(
                R_UNKNOWN_KEY, f"{dotted}.{key} is not a key this schema knows; "
                "an experiment may not declare what nothing reads",
                key=f"{dotted}.{key}", path=path)
        _check_type(path, f"{dotted}.{key}", spec[key], value)
    for key, kspec in spec.items():
        if key != "*" and kspec.required and key not in table and key not in pending:
            raise ExperimentRefusal(
                R_MISSING_KEY, f"{dotted}.{key} is not declared; an omitted key is a "
                "refusal, never a default", key=f"{dotted}.{key}", path=path)


def _pending_fields(path: str, data: dict) -> dict[str, frozenset]:
    """v4: `[pending]` (block key -> filler), checked: every key a scalar block
    key, every filler one of PENDING_FILLERS, and the key's own TOML field
    ABSENT (a value and a pending mark for one line are two declarations).
    Returns section -> the fields whose absence the pending mark accounts for."""
    pend = data.get("pending", {})
    if not isinstance(pend, dict):
        raise ExperimentRefusal(R_WRONG_TYPE, "expected a table [pending]", key="pending", path=path)
    out: dict[str, set] = {}
    for bkey, filler in pend.items():
        rows = [(sec, k) for b, sec, k in BLOCK_MAP if b == bkey]
        if not rows or bkey in BLOCK_LIST_KEYS:
            raise ExperimentRefusal(R_UNKNOWN_KEY, f"pending.{bkey} is not a scalar block line",
                                    key=f"pending.{bkey}", path=path)
        if filler not in PENDING_FILLERS:
            raise ExperimentRefusal(R_INVALID_VALUE, f"pending.{bkey}={filler!r} is not one of "
                                    f"{list(PENDING_FILLERS)}", key=f"pending.{bkey}", path=path)
        (section, key), = rows
        if key in _table(data, section):
            raise ExperimentRefusal(R_INVALID_VALUE, f"pending.{bkey} and {section}.{key} are both "
                                    "declared: a line is pending or written, never both",
                                    key=f"pending.{bkey}", path=path)
        out.setdefault(section, set()).add(key)
    return {k: frozenset(v) for k, v in out.items()}


def _validate(path: str, data: dict) -> None:
    """Every key in SCHEMA, typed; the cross-key rules after."""
    if not isinstance(data, dict):  # pragma: no cover - tomllib always gives a dict
        raise ExperimentRefusal(R_TOML_INVALID, "not a table", path=path)
    top_known = {s.split(".")[0].rstrip("[]") for s in SCHEMA} | {"pending"}
    for key in data:
        if key not in top_known:
            raise ExperimentRefusal(R_UNKNOWN_KEY, f"[{key}] is not a section this schema knows",
                                    key=key, path=path)
    for section in REQUIRED_SECTIONS:
        if section not in data:
            raise ExperimentRefusal(R_MISSING_KEY, f"[{section}] is not declared",
                                    key=section, path=path)
    pending = _pending_fields(path, data)
    # plain sections
    for section, spec in SCHEMA.items():
        if "." in section or section.endswith("[]") or section == "seats":
            continue                # seats: checked below without its sub-tables
        if section in data:
            _check_table(path, section, spec, data[section], pending.get(section, frozenset()))
    # sub-tables of seats
    seats = data["seats"]
    for sub in ("model_id", "billing"):
        if sub in seats:
            _check_table(path, f"seats.{sub}", SCHEMA[f"seats.{sub}"], seats[sub])
    # the seats table's own keys, minus the sub-tables
    _check_table(path, "seats", SCHEMA["seats"],
                 {k: v for k, v in seats.items() if k not in ("model_id", "billing")})
    # passes: an array of tables
    if "passes" in data:
        passes = data["passes"]
        if not isinstance(passes, list):
            raise ExperimentRefusal(R_WRONG_TYPE, "expected an array of tables [[passes]]",
                                    key="passes", path=path)
        for i, p in enumerate(passes):
            _check_table(path, f"passes[{i}]", SCHEMA["passes[]"], p)
    # cells: a table of tables
    if "cells" in data:
        cells = data["cells"]
        if not isinstance(cells, dict):
            raise ExperimentRefusal(R_WRONG_TYPE, "expected tables [cells.<name>]",
                                    key="cells", path=path)
        for name, cell in cells.items():
            if not _IDENT_RE.match(name):
                raise ExperimentRefusal(R_INVALID_VALUE, f"{name!r} is not an identifier",
                                        key=f"cells.{name}", path=path)
            _check_table(path, f"cells.{name}", SCHEMA["cells.*"], cell)
    _cross_rules(path, data)


def _cross_rules(path: str, data: dict) -> None:
    reg = data["registration"]
    if "sha256" in reg and "document" not in reg:
        raise ExperimentRefusal(
            R_MISSING_KEY, "registration.sha256 is declared and registration.document is not; "
            "a hash names a document", key="registration.document", path=path)
    seats = data["seats"]
    if seats["source"] == "list" and "ids" not in seats:
        raise ExperimentRefusal(R_MISSING_KEY, "seats.source=list declares no seats.ids",
                                key="seats.ids", path=path)
    if seats["source"] == "census":
        for key in ("rule", "n_dukes", "title_key"):
            if key not in seats:
                raise ExperimentRefusal(R_MISSING_KEY,
                                        f"seats.source=census declares no seats.{key}",
                                        key=f"seats.{key}", path=path)
    # v4: the horizon, whole or by its start while the end is pending; its
    # length and its end are one declaration
    ep = data["episode"]
    pend = data.get("pending", {})
    if "horizon" not in ep and "horizon_start" not in ep:
        raise ExperimentRefusal(R_MISSING_KEY, "episode.horizon (or episode.horizon_start, with "
                                "CK3_EPISODE_HORIZON_END pending) is not declared",
                                key="episode.horizon", path=path)
    if "horizon" not in ep and "CK3_EPISODE_HORIZON_END" not in pend:
        raise ExperimentRefusal(R_MISSING_KEY, "episode.horizon_start without episode.horizon: the "
                                "end is neither declared nor pending ([pending])",
                                key="episode.horizon", path=path)
    if "horizon" in ep and "horizon_start" in ep and ep["horizon"][0] != ep["horizon_start"]:
        raise ExperimentRefusal(R_INVALID_VALUE, f"episode.horizon_start={ep['horizon_start']!r} and "
                                f"episode.horizon[0]={ep['horizon'][0]!r} disagree",
                                key="episode.horizon_start", path=path)
    if "horizon" in ep and "horizon_game_years" in ep:
        y0, m0, d0 = (int(x) for x in ep["horizon"][0].split("."))
        y1, m1, d1 = (int(x) for x in ep["horizon"][1].split("."))
        if (y1 - y0, m1, d1) != (ep["horizon_game_years"], m0, d0):
            raise ExperimentRefusal(R_INVALID_VALUE, f"episode.horizon {ep['horizon']} is not "
                                    f"{ep['horizon_game_years']} game-years (episode.horizon_game_years)",
                                    key="episode.horizon_game_years", path=path)
    # the census lines the run writes: all or none (one alone is a seat
    # somebody picked by hand; pending ones count as absent), and the count is n
    ids_present = [k for k in CENSUS_ID_KEYS if k in seats]
    if ids_present and len(ids_present) != len(CENSUS_ID_KEYS):
        missing = next(k for k in CENSUS_ID_KEYS if k not in seats)
        raise ExperimentRefusal(
            R_MISSING_KEY, f"seats.{ids_present[0]} is declared and seats.{missing} is not; "
            f"the census writes {list(CENSUS_ID_KEYS)} together or none", key=f"seats.{missing}", path=path)
    if ids_present:
        if "n_dukes" in seats and len(seats["duke_ids"]) != seats["n_dukes"]:
            raise ExperimentRefusal(
                R_INVALID_VALUE, f"seats.duke_ids lists {len(seats['duke_ids'])} ids and "
                f"seats.n_dukes={seats['n_dukes']}", key="seats.duke_ids", path=path)
        if seats["king_id"] in seats["duke_ids"]:
            raise ExperimentRefusal(
                R_INVALID_VALUE, f"seats.king_id={seats['king_id']} is also in seats.duke_ids; "
                "one character cannot hold two seats", key="seats.king_id", path=path)
    billing = seats.get("billing", {})
    if "account" in billing:
        if billing["account"] not in billing["accounts"]:
            raise ExperimentRefusal(
                R_INVALID_VALUE, f"seats.billing.account={billing['account']!r} is not one of "
                f"seats.billing.accounts {billing['accounts']}", key="seats.billing.account",
                path=path)
        if billing["account"] == "api_key" and "key_file" not in billing:
            raise ExperimentRefusal(
                R_MISSING_KEY, "seats.billing.account=api_key declares no seats.billing.key_file",
                key="seats.billing.key_file", path=path)
    # a version-4 declaration carries the channel's lines for every arm
    # (the launcher requires the bounds and the added tool at parse)
    if data["registration"]["version"] == "4":
        if "channel" not in data:
            raise ExperimentRefusal(
                R_MISSING_KEY, "registration.version=4 and no [channel] table declares the "
                "bounds (channel.max_text_bytes, channel.quota_per_cycle)", key="channel",
                path=path)
        if not data["tool_surface"].get("channel_arms_add"):
            raise ExperimentRefusal(
                R_MISSING_KEY, "registration.version=4 and tool_surface.channel_arms_add names "
                "no tool (COUNCIL's complete topology adds one)",
                key="tool_surface.channel_arms_add", path=path)
        # v4: every seated arm is served the guide, declared by its
        # manifest's sha (the launcher refuses a version-4 block without it)
        if "guide" not in data:
            raise ExperimentRefusal(
                R_MISSING_KEY, "registration.version=4 and no [guide] table declares the guide's "
                "manifest_sha256", key="guide", path=path)
    if not data["tool_surface"]["held_constant"]:
        raise ExperimentRefusal(R_INVALID_VALUE, "tool_surface.held_constant names no tools; "
                                "an empty surface makes the held-constant claim uncheckable",
                                key="tool_surface.held_constant", path=path)
    roles = set(seats.get("model_id", {}))          # declared roles (ROLE_BLOCK_KEYS' names)
    for name, cell in data.get("cells", {}).items():
        dotted = f"cells.{name}"
        for role_key, steered_key in (("king", "king_steered"), ("duke", "dukes_steered")):
            role = cell[role_key]
            if cell[steered_key]:
                if role == "native":
                    raise ExperimentRefusal(
                        R_INVALID_VALUE, f"{steered_key}=true with {role_key}=native",
                        key=f"{dotted}.{role_key}", path=path)
                if role not in roles:
                    raise ExperimentRefusal(
                        R_INVALID_VALUE, f"{role_key}={role!r} is not a role in "
                        f"[seats.model_id] ({sorted(roles)})",
                        key=f"{dotted}.{role_key}", path=path)
                if "guide" not in data:
                    raise ExperimentRefusal(
                        R_MISSING_KEY, f"{steered_key}=true and no [guide] declares the role's "
                        "play file and goal text (v4 §6: a steered seat boots its role's files, "
                        "declared by the guide manifest's sha256)", key="guide", path=path)
            else:
                if role != "native":
                    raise ExperimentRefusal(
                        R_INVALID_VALUE, f"{steered_key}=false with {role_key}={role!r} "
                        "(a seat nobody steers has no model)",
                        key=f"{dotted}.{role_key}", path=path)
        if cell["topology"] != "none" and "channel" not in data:
            raise ExperimentRefusal(
                R_MISSING_KEY, f"{dotted}.topology={cell['topology']} and no [channel] "
                "table declares the bounds", key="channel", path=path)
        # v4: a cell with a pad needs the bounds and the two tools
        if cell.get("notepad", "none") != "none":
            if "notepad" not in data:
                raise ExperimentRefusal(
                    R_MISSING_KEY, f"{dotted}.notepad={cell['notepad']} and no [notepad] table "
                    "declares the bounds", key="notepad", path=path)
            if not data["tool_surface"].get("notepad_arms_add"):
                raise ExperimentRefusal(
                    R_MISSING_KEY, f"{dotted}.notepad={cell['notepad']} and "
                    "tool_surface.notepad_arms_add names no tool",
                    key="tool_surface.notepad_arms_add", path=path)
    # v4: a declared guide needs its tool on the surface
    if "guide" in data and not data["tool_surface"].get("guide_arms_add"):
        raise ExperimentRefusal(
            R_MISSING_KEY, "[guide] is declared and tool_surface.guide_arms_add names no tool",
            key="tool_surface.guide_arms_add", path=path)


# ── host checks ─────────────────────────────────────────────────────────────

def host_path_problems(raw: bytes, hosts_forbidden: tuple[str, ...] = ()) -> list[str]:
    """Every line of `raw` naming a host, as 'line N: ...': a path under
    HOST_PATH_RE's roots, or one of `hosts_forbidden` (the overlay's names)
    as a substring. Bytes, comments included: a comment value is a mapping
    input to any later scan of the file."""
    probs = []
    forbidden = [h.encode("utf-8") for h in hosts_forbidden]
    for n, line in enumerate(raw.splitlines(), 1):
        m = HOST_PATH_RE.search(line)
        if m:
            probs.append(f"line {n}: names a host path {m.group(0).decode(errors='replace')!r}")
            continue
        for h in forbidden:
            if h in line:
                probs.append(f"line {n}: names a forbidden host {h.decode()!r}")
                break
    return probs


# ── loading ─────────────────────────────────────────────────────────────────

def _read_toml(path: Path) -> tuple[dict, bytes]:
    if not path.is_file():
        raise ExperimentRefusal(R_FILE_MISSING, f"{path} does not exist", path=str(path))
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ExperimentRefusal(R_FILE_UNREADABLE, str(exc), path=str(path)) from None
    try:
        return tomllib.loads(raw.decode("utf-8")), raw
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise ExperimentRefusal(R_TOML_INVALID, str(exc), path=str(path)) from None


def overlay_path_for(path: Path) -> Path:
    """`<name>.local.toml` beside `<name>.toml`."""
    return path.with_name(path.name[:-len(".toml")] + OVERLAY_SUFFIX)


def load_experiment(path: os.PathLike | str, *, overlay: Optional[os.PathLike | str] = None,
                    use_overlay: bool = True) -> Experiment:
    """The tracked file, checked; the overlay's `[runner]` host keys merged in
    when one exists beside it (or at `overlay`). Raises ExperimentRefusal."""
    p = Path(os.path.expanduser(str(path)))
    if p.name.endswith(OVERLAY_SUFFIX):
        raise ExperimentRefusal(R_INVALID_VALUE, f"{p.name} is an overlay, not an experiment; "
                                "load the tracked <name>.toml", path=str(p))
    data, raw = _read_toml(p)
    # The overlay is read first: its hosts_forbidden is an input to the scan
    # of the tracked file. It is merged last, after the tracked file is checked.
    op: Optional[Path] = None
    runner_overlay: dict[str, str] = {}
    hosts_forbidden: tuple[str, ...] = ()
    if use_overlay:
        op = Path(os.path.expanduser(str(overlay))) if overlay is not None else overlay_path_for(p)
        if overlay is not None and not op.is_file():
            raise ExperimentRefusal(R_FILE_MISSING, f"overlay {op} does not exist", path=str(op))
        if op.is_file():
            runner_overlay, hosts_forbidden = _read_overlay(op)
        else:
            op = None
    probs = host_path_problems(raw, hosts_forbidden)
    if probs:
        raise ExperimentRefusal(
            R_HOST_PATH_IN_TRACKED_FILE, "; ".join(probs[:5]) + (
                f" (+{len(probs) - 5} more)" if len(probs) > 5 else "") +
            f" — host values belong in the untracked {overlay_path_for(p).name}",
            path=str(p))
    runner = data.get("runner", {})
    for key in (*RUNNER_HOST_KEYS, HOSTS_FORBIDDEN_KEY):
        if isinstance(runner, dict) and key in runner:
            raise ExperimentRefusal(
                R_HOST_KEY_IN_TRACKED_FILE, f"runner.{key} is a host value; it belongs in "
                f"the untracked {overlay_path_for(p).name}", key=f"runner.{key}", path=str(p))
    _validate(str(p), data)
    exp = Experiment(name=data["experiment"]["name"], path=p, data=data,
                     tracked_sha256=hashlib.sha256(raw).hexdigest())
    if op is not None:
        exp.data.setdefault("runner", {}).update(runner_overlay)
        exp.overlay_path = op
        exp.runner_host = "overlay"
        exp.hosts_forbidden = hosts_forbidden
    return exp


def _read_overlay(op: Path) -> tuple[dict[str, str], tuple[str, ...]]:
    """The overlay's `[runner]` host keys (str each) and its `hosts_forbidden`
    (a list of str, kept apart from the runner values). Any other table or key
    is a refusal: the experiment's rules live in the tracked file."""
    odata, _ = _read_toml(op)
    for section in odata:
        if section != "runner":
            raise ExperimentRefusal(
                R_OVERLAY_KEY_NOT_HOST, f"[{section}] in an overlay; an overlay carries "
                "only [runner] host keys", key=section, path=str(op))
    runner = odata.get("runner", {})
    if not isinstance(runner, dict):
        raise ExperimentRefusal(R_WRONG_TYPE, "[runner] is not a table", key="runner",
                                path=str(op))
    values: dict[str, str] = {}
    hosts_forbidden: tuple[str, ...] = ()
    for key, value in runner.items():
        if key == HOSTS_FORBIDDEN_KEY:
            if not isinstance(value, list) or not all(isinstance(h, str) and h for h in value):
                raise ExperimentRefusal(
                    R_WRONG_TYPE, "expected a list of non-empty strings",
                    key=f"runner.{key}", path=str(op))
            hosts_forbidden = tuple(value)
            continue
        if key not in RUNNER_HOST_KEYS:
            raise ExperimentRefusal(
                R_OVERLAY_KEY_NOT_HOST, f"runner.{key} is not a host key "
                f"({list(RUNNER_HOST_KEYS)}); the experiment's rules live in the tracked file",
                key=f"runner.{key}", path=str(op))
        if not isinstance(value, str):
            raise ExperimentRefusal(R_WRONG_TYPE, f"expected a string, got {type(value).__name__}",
                                    key=f"runner.{key}", path=str(op))
        values[key] = value
    return values, hosts_forbidden


# ── the block ───────────────────────────────────────────────────────────────

def to_conditions_block(exp: Experiment) -> str:
    """The ```registered-conditions fence, generated. Deterministic: the same
    TOML gives the same bytes. The text is what a registration document
    carries and what `check_block` reads back."""
    scalars, lists = exp.block_values()
    out = ["```registered-conditions",
           f"# GENERATED by ck3bench from {exp.path.name} (sha256 {exp.tracked_sha256[:12]}).",
           "# Do not hand-edit: `ck3bench experiment check <exp> --block <document>` compares."]
    pending: Optional[str] = None       # a group comment waits for its first line
    for bkey, _, _ in BLOCK_MAP:
        if bkey in _BLOCK_COMMENTS:
            pending = _BLOCK_COMMENTS[bkey]
        if bkey not in lists and bkey not in scalars:
            continue
        if pending is not None:
            out.append("")
            out.append(f"# -- {pending}")
            pending = None
        if bkey in lists:
            for name in lists[bkey]:
                out.append(f"{bkey}={name}")
        else:
            out.append(f"{bkey}={scalars[bkey]}")
    out.append("```")
    return "\n".join(out) + "\n"


def read_block(text: str) -> tuple[dict[str, str], dict[str, list[str]]]:
    """The one fence of a document -> (scalars, lists), verbatim strings. A
    small reader of the SYNTAX only (KEY=VALUE, '#' comments, blank lines, a
    repeated key is a list only for BLOCK_LIST_KEYS). It does not judge values:
    the launcher does. Refuses no block, two blocks, a malformed line, a
    repeated scalar, and a document carrying TEMPLATE_MARKER (a template is
    not a frozen registration: the launcher's rule, read here too)."""
    if TEMPLATE_MARKER in text:
        raise ExperimentRefusal(
            R_TEMPLATE_NOT_FROZEN, f"the document carries the marker {TEMPLATE_MARKER!r}; a "
            "template is not a pre-registration: freeze one and remove the marker")
    blocks: list[list[tuple[int, str]]] = []
    cur: Optional[list[tuple[int, str]]] = None
    for n, raw in enumerate(text.splitlines(), 1):
        if cur is None:
            if _FENCE_OPEN.match(raw):
                cur = []
            continue
        if _FENCE_CLOSE.match(raw):
            blocks.append(cur)
            cur = None
            continue
        cur.append((n, raw))
    if cur is not None:
        raise ExperimentRefusal(R_NO_BLOCK, "a ```registered-conditions fence was opened and never closed")
    if not blocks:
        raise ExperimentRefusal(R_NO_BLOCK, "no ```registered-conditions block")
    if len(blocks) > 1:
        raise ExperimentRefusal(R_MULTIPLE_BLOCKS, f"{len(blocks)} ```registered-conditions blocks")
    scalars: dict[str, str] = {}
    lists: dict[str, list[str]] = {}
    for n, raw in blocks[0]:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ExperimentRefusal(R_MALFORMED_LINE, f"line {n}: {line!r} is not KEY=VALUE")
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if not _BLOCK_KEY_RE.match(key):
            raise ExperimentRefusal(R_MALFORMED_LINE, f"line {n}: {key!r} is not a key")
        if key in BLOCK_LIST_KEYS:
            bucket = lists.setdefault(key, [])
            if value in bucket:
                raise ExperimentRefusal(R_DUPLICATE_KEY, f"line {n}: {key}={value} listed twice", key=key)
            bucket.append(value)
            continue
        if key in scalars:
            raise ExperimentRefusal(R_DUPLICATE_KEY, f"line {n}: {key} declared twice", key=key)
        scalars[key] = value
    return scalars, lists


def block_disagreements(exp: Experiment, block_text: str) -> list[dict]:
    """Every way the block and the TOML disagree. Empty means agreed. Each row
    names the key and the two readings; absence on either side is a row, never
    a pass ('not_declared' / 'not_in_block')."""
    want_s, want_l = exp.block_values()
    have_s, have_l = read_block(block_text)
    rows: list[dict] = []
    for key in BLOCK_SCALAR_KEYS:
        w, h = want_s.get(key), have_s.get(key)
        if w == h:
            continue
        rows.append({"key": key, "declared": w if w is not None else "not_declared",
                     "block": h if h is not None else "not_in_block",
                     "verdict": ("missing_in_block" if h is None else
                                 "extra_in_block" if w is None else "value_differs")})
    for key in BLOCK_LIST_KEYS:
        w, h = want_l.get(key), have_l.get(key)
        if w == h:
            continue
        rows.append({"key": key, "declared": w if w is not None else "not_declared",
                     "block": h if h is not None else "not_in_block",
                     "verdict": ("missing_in_block" if h is None else
                                 "extra_in_block" if w is None else "list_differs")})
    for key in have_s:
        if key not in BLOCK_SCALAR_KEYS:
            rows.append({"key": key, "declared": "not_a_generated_key", "block": have_s[key],
                         "verdict": "extra_in_block"})
    for key in have_l:
        if key not in BLOCK_LIST_KEYS:
            rows.append({"key": key, "declared": "not_a_generated_key", "block": have_l[key],
                         "verdict": "extra_in_block"})
    return rows


def check_block(exp: Experiment | os.PathLike | str, block_text: str) -> None:
    """The mirror check: refuses (`block_disagrees`) on ANY disagreement
    between the TOML and `block_text`'s fence, naming every key."""
    if not isinstance(exp, Experiment):
        exp = load_experiment(exp)
    rows = block_disagreements(exp, block_text)
    if rows:
        raise ExperimentRefusal(
            R_BLOCK_DISAGREES,
            "; ".join(f"{r['key']}: {r['verdict']} (declared {r['declared']!r}, block {r['block']!r})"
                      for r in rows),
            path=str(exp.path), disagreements=rows)


def installed_toolkit_version() -> Optional[str]:
    """The ck3agent distribution installed beside this interpreter, by its
    metadata (never an import: the toolkit is not loaded to be named). None
    when no distribution is found."""
    try:
        return importlib.metadata.version("ck3agent")
    except importlib.metadata.PackageNotFoundError:
        return None


def toolkit_witness(exp: Experiment) -> dict:
    """What the TOML declares as `[toolkit].ck3agent` beside what is installed.
    A witness, not a refusal: `state` is `installed_matches`,
    `installed_differs` or `not_installed`, never agreed by default."""
    declared = exp.data["toolkit"]["ck3agent"]
    installed = installed_toolkit_version()
    if installed is None:
        state = "not_installed"
    elif installed == declared:
        state = "installed_matches"
    else:
        state = "installed_differs"
    return {"declared": declared,
            "installed": installed if installed is not None else "not_installed",
            "state": state}


def registration_document_witness(exp: Experiment, tree_root: Path) -> dict:
    """`[registration].document` is a path in the research tree the TOML was
    written in; this version of ck3bench does not ship it. Looked for under
    `tree_root` (relative paths; the CLI passes --tree, default the current
    directory): absent is the state `registration_document_not_shipped`,
    never an error. Present with a declared sha that disagrees is a refusal
    (`invalid_value` on registration.sha256): a hash that can be checked and
    is wrong is not a witness."""
    reg = exp.data["registration"]
    doc, sha = reg.get("document"), reg.get("sha256")
    row: dict[str, Any] = {"document": doc if doc is not None else "not_declared",
                           "sha256_declared": sha if sha is not None else "not_declared",
                           "looked_in": str(tree_root)}
    if doc is None:
        row["state"] = "not_declared"
        row["sha256_observed"] = "unwitnessed"
        return row
    p = Path(doc)
    if not p.is_absolute():
        p = tree_root / p
    if not p.is_file():
        row["state"] = "registration_document_not_shipped"
        row["sha256_observed"] = "unwitnessed"
        return row
    observed = hashlib.sha256(p.read_bytes()).hexdigest()
    row["sha256_observed"] = observed
    if sha is None:
        row["state"] = "present_sha_not_declared"
    elif sha == observed:
        row["state"] = "present_sha_agrees"
    else:
        raise ExperimentRefusal(
            R_INVALID_VALUE, f"registration.sha256 declares {sha} and {doc} (under {tree_root}) "
            f"hashes {observed}", key="registration.sha256", path=str(exp.path))
    return row


def check_report(exp: Experiment, tree_root: Optional[Path] = None) -> dict:
    """What `ck3bench experiment check` prints for one loaded experiment.
    `tree_root` is where a relative `[registration].document` is looked for
    (default: the current directory)."""
    block = to_conditions_block(exp)
    cells = exp.cells
    root = Path.cwd() if tree_root is None else Path(tree_root)
    return {
        "ok": True,
        "name": exp.name,
        "intent": exp.intent,
        "path": str(exp.path),
        "tracked_sha256": exp.tracked_sha256,
        "runner_host": exp.runner_host,
        "overlay": str(exp.overlay_path) if exp.overlay_path else None,
        "hosts_forbidden": len(exp.hosts_forbidden),
        "toolkit": toolkit_witness(exp),
        "registration": registration_document_witness(exp, root),
        "cells": len(cells),
        "episodes": sum(c["episodes"] for c in cells.values() if "episodes" in c),
        "episodes_undeclared": sorted(n for n, c in cells.items() if "episodes" not in c),
        "pending": dict(sorted(exp.pending.items())),
        "block_sha256": hashlib.sha256(block.encode("utf-8")).hexdigest(),
        "block_keys": [k for k in BLOCK_KEYS
                       if k in exp.block_values()[0] or k in exp.block_values()[1]],
        "block_keys_not_emitted": sorted(_declared_not_emitted(exp)),
    }


def _declared_not_emitted(exp: Experiment) -> set[str]:
    """Registration-shaped declarations the TOML carries that the block does
    not render: a cell's topology when the cell is not an arm the launcher
    knows (BLOCK_TOPOLOGY_ARMS), and the billing key
    file when the episode's account is not api_key. Reported,
    never dropped silently."""
    out: set[str] = set()
    d = exp.data
    for name, cell in d.get("cells", {}).items():
        if name not in BLOCK_TOPOLOGY_ARMS:
            out.add(f"channel.topology.{name}")
        if "notepad" in cell and name not in BLOCK_NOTEPAD_ARMS:
            out.add(f"notepad.pads.{name}")
    billing = d["seats"].get("billing", {})
    if "key_file" in billing and billing.get("account") != "api_key":
        out.add("seat.billing.key_file")
    return out
