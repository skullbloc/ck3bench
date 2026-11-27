"""python -m ck3bench.harness series publish RUN_DIR DEST [--host-name NAME ...]: a publishable copy of one run directory.

DEST (a new directory) receives the files of RUN_DIR that a series publishes (PUBLISHED_FILES; a file the run did not
write is listed as absent), each rewritten so that it names no machine, no account and no private path. Before any
rule, a listed file that is a symbolic link (or resolves outside RUN_DIR) or that has more than one hard link refuses
the copy (rc 5, nothing written): what it holds is not read. A listed file that is DAMAGED (not UTF-8 text, a NUL
byte, a `.gz` that does not decompress, a run.json that is not one JSON object: what a host going down can leave) is not read and not published: DEST holds
in its place `<file>.damaged.json`, a stub naming the file, its size, the sha256 of its bytes and the damage, and
PUBLISHED.json lists it as damaged. run.json's top-level `host` must be a host label (score.HOST_LABEL: a, b, h1); a
machine's name there refuses the copy. Every JSON string (values and
keys) and every line of the other files then goes through these rules, in this order:

  limit text    a string the agent CLI wrote about an account limit (LIMIT_TEXT_RX: a usage-limit message, "limit
                reached", "hit your limit", a named limit (weekly, daily, 5-hour, session, a model's), how much of it
                was used ("used 92%", "92% of your"), its reset time (a digit, a weekday, a month, tomorrow), a
                `|<10 digits>` stamp, a rate limit's size, a per-minute token limit, an organisation's rate limit, a
                credit balance, "out of usage") is replaced WHOLE by `<limit text removed>`, followed by
                `(rate limit)` and/or `(api error)` when the scorer's own patterns (score.LIMIT_TEXT, score.API_TEXT)
                read the original that way, so the copy's resource evidence is the run's; a non-JSON line is replaced
                whole
  escapes       JSON's escaped slash (`\\/`), a unicode slash (`\\u002f`) and percent encoding (`%2F`) are read
                plain, so every rule below reads a path behind them
  paths         every absolute path of this host's configuration (the variables in PATH_VARS, read from the
                environment and --config as `series run` reads them, each also as its real path), the run directory,
                and the expectation and schedule files run.json names become `<NAME>` placeholders, longest first;
                then any user's home directory (under home, Users or var/home, or root's, a `~name/` home, or a
                Windows `X:\\Users\\name`) becomes `<HOME>`; a Windows network path (`\\\\server\\share`) becomes
                `<network path>`, and so does a share by URL (smb://, cifs://, afp://, nfs://) or spelled //server/share; a path under /mnt, /srv, /opt, /data, /media, /scratch, /nfs, /var/lib, /Volumes,
                /workspace, /net or /run/media becomes `<private path>`; a socket under /tmp becomes `<socket>`, any
                other path below a directory of /tmp, and any path under /var/tmp or /dev/shm, `<tmp path>`
  variables     `USER=`, `LOGNAME=`, `USERNAME=`, `SUDO_USER=`, `HOSTNAME=`, `HOST=` in any capitalisation (and the
                same keys in JSON, or as `"HOSTNAME": "value"` text inside a string) lose their value
  host name     the names `series start` and `series end` record (run.json `start.host`, `end.host`; when run.json
                does not read, every `"host": "NAME"` in its bytes that is not a label), the names the operator gives
                (`--host-name NAME`, repeated, and RATE_HOST_NAMES) and this host's own name (each also without its
                domain); a run.json that does not read with no name in it, or a series attempt's run.json that is
                absent, with none given refuses the copy (rc 5). The names, in any capitalisation, wherever they
                stand outside a run of letters and digits (so `HOST-ck3` and `host_backup` are caught), become
                `<host name>`; a user at a
                machine with no domain, as a login names it (after ssh, scp, rsync, sftp or mosh, or before
                `:/path`), becomes `<user>@<host>`. A host name that is also an ordinary word is replaced in the
                seat's and the game's text as well: the count is in PUBLISHED.json, and a host with a distinctive name
                avoids it
  user name     this account's name, USER/LOGNAME/USERNAME and HOME's last part, and every name a home path or a
                `~name/` in the files carries (three characters or more), wherever it stands outside letters and
                digits (a run directory's name, a value split from its path), becomes `<user name>`; the same
                ordinary-word caveat as host names
  addresses     an IPv4 address becomes `<ip address>` (a dotted version number after "version", "v", "build" or
                "CK3" whose first number is below 10 is kept), an IPv6 address too, a MAC address (also in dot
                notation, 001a.2b3c.4d5e) `<mac address>`;
                `uid=N(name)`, `gid=N`, `groups=...` lose their numbers and names; a claude.ai session link becomes
                `<session link>`, a Drive or Docs link `<drive link>`
  CLI events    in transcript.jsonl, a `rate_limit_event` keeps its type and `rate_limit_info.status` (what the
                scorer's resource rule reads) and nothing else; a `system`/`init` event keeps type, subtype, model,
                tools and the MCP servers' names and statuses; an account key (ACCOUNT_KEYS, and any key of the same
                family: a reset time, an overage field, a utilisation figure) is dropped wherever it stands, in every
                file
  ids           every `session_id` value becomes `session-N`, numbered by first appearance across the files before
                anything else is numbered (the same session is `session-N` wherever it stands, in a `session_id`
                key or in a text); every other UUID-shaped string becomes `id-N`

DEST/PUBLISHED.json lists every file (its sha256 before and after, or absent), the run directory's name rewritten by
the same rules, every placeholder with the rule it stands for (never the value) and how many times it was put in. The
copy, PUBLISHED.json included, is then read again for every pattern in the refusal list (REFUSAL_CLASSES and the host
names, user names and configured paths), each line as written, with its escapes read plain and with any base64 run
decoded (the standard and the URL-safe alphabet, a second level inside the first) and its hex runs
decoded: if any survives, nothing is published (rc 5: DEST is not written, and each survivor is printed by its class,
file and line, never its value). An e-mail address and a key or token of the shapes SECRET lists (a private key's
header, a provider's or a forge's token by its prefix, a cloud provider's key id, a bearer header, a JWT, a value of 12
characters or more after a name holding `secret`, `api_key`, `password`, `credential` or `private_key`, such as
`aws_secret_access_key = ...`, unquoted up to a space, quote, comma or `}`, or quoted (a name holding `passphrase`
or `pwd` too, a `pwd` value that is a path excepted: a working directory, rewritten as a path): read to the closing
quote of its own kind, spaces, an apostrophe, the other quote or its own kind
escaped with a backslash inside it included (`password = "correct horse battery
staple"`, `password = "don't tell anyone"`); the same quoted after `password =>` or a `--password` flag (or
`passphrase`); or one with a
letter after a name holding `token`; a value that is a placeholder whole (`"<redacted>"`), or exactly one ISO date
or time, is no key) are never rewritten: they refuse the copy. Not caught: a key of another shape, a machine's name that no run.json records and no variable carries, a dotted address after a version
word ("version 9.20.30.40", read as a version), a value split across fields or lines (a base64 run wrapped over two
lines) so that no rule reads it whole; a machine's name that a damaged run.json held only in its lost part, when
another name was read from the part left or given (the copy is then made with the names known). The scorer reads the copy as it reads the run (the resource rule's evidence is
kept), except a damaged file, which the copy holds as its stub only. The rewrite is a floor, not a review: the series' document (docs/prereg-munster-kingdom.md, section 11) has a
person read the copy before it is released.
"""
from __future__ import annotations

import base64
import binascii
import gzip
import hashlib
import json
import os
import pwd
import re
import shutil
import socket
import sys
import urllib.parse
import zlib
from pathlib import Path
from typing import Any, Callable, Optional

from ck3bench.harness.score import API_TEXT, HOST_LABEL, LIMIT_TEXT

#: what a series publishes of each attempt's run directory (docs/prereg-munster-kingdom.md, "What is published")
#: The driver's `done` and end marker and the operator's withdrawal or cancellation record are among them, so a reader
#: of a withdrawn attempt's copy reads what score.played_out reads.
PUBLISHED_FILES = ("run.json", "runner_start.json", "result.json", "end.json", "trajectory.jsonl", "turns.jsonl",
                   "accounting.json", "pressure.tsv", "post.tsv", "bridge_title_witness.json", "transcript.jsonl",
                   "session_stdin.jsonl", "done", "rate_run.done", "operator_withdrawn.txt", "operator_stopped.txt")
#: the host configuration's path variables (docs/harness.md, "Environment variables"), placeholders in this order of
#: preference when two name the same path
#: the operator's list of machine names to rewrite (beside run.json's and this host's): space- or comma-separated
HOST_NAMES_VAR = "RATE_HOST_NAMES"
#: a `"host": "NAME"` pair in run.json's text (read when run.json does not read as JSON)
RUN_JSON_HOST = re.compile(r'"host"\s*:\s*"([^"\\\n]{1,253})"')
PATH_VARS = ("RATE_RUNS", "RATE_LEDGER", "KIT_REPO", "RATE_SAVES", "RATE_GAME_SAVES", "RATE_SO", "RATE_WHEEL", "MAP_BIN",
             "RATE_GAME_BIN", "RATE_CLAUDE_BIN", "RATE_PROTECTED_DIR", "RATE_LOCK_DIR", "RATE_BRIDGE_LOG",
             "XDG_STATE_HOME", "XDG_DATA_HOME", "XDG_CONFIG_HOME", "XDG_RUNTIME_DIR", "HOME")
#: keys the agent CLI's events carry about the account or its limits: dropped wherever they stand
ACCOUNT_KEYS = frozenset({"apiKeySource", "api_key_source", "account", "accountUuid", "account_uuid", "organization",
                          "organizationUuid", "organization_uuid", "orgId", "email", "emailAddress", "user_id",
                          "userID", "resetsAt", "utilization", "rateLimitType", "overageStatus",
                          "overageDisabledReason", "isUsingOverage", "overageEnabled", "unifiedWindows",
                          "surpassedThreshold", "subscriptionType", "overageResetsAt"})
#: any other key of the same family (a reset time, an overage field, a utilisation figure) is dropped too
ACCOUNT_KEY_FAMILY = re.compile(r"(?i)resets?_?at$|overage|utili[sz]ation")
#: environment keys that name the user or the machine: their value is removed wherever they stand (JSON key or
#: `KEY=value` text)
IDENTITY_ENV_KEYS = ("USER", "LOGNAME", "USERNAME", "SUDO_USER", "HOSTNAME", "HOST", "COMPUTERNAME", "USERDOMAIN")
INIT_KEEP = ("type", "subtype", "model", "tools", "mcp_servers", "session_id")

UUID = re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")
HOME_DIR = re.compile(r"/(?:home|Users|var/home)/[^/\s\"'<>]+|/root(?=/|\b)")
#: a user's home by the shell's spelling, `~name/...` (not `~/`, which names no one; a `~` before a word with no
#: slash is a seat's "about")
TILDE_USER = re.compile(r"(?<![\w/~])~[a-z_][\w.-]*(?=/)")
WIN_HOME = re.compile(r"(?i)\b[a-z]:(?:\\\\|\\|/)(?:users|documents and settings)(?:\\\\|\\|/)[^\\/\s\"'<>]+")
#: a Windows network path, `\\server\share`
UNC_PATH = re.compile(r"(?<![\w:\\])\\\\(?![uUxX][0-9a-fA-F]{2})[\w.$-]{2,}\\[^\s\"'<>]*")
PRIVATE_PATH = re.compile(r"(?<![\w.~-])/(?:mnt|srv|opt|data|media|scratch|nfs|var/lib|Volumes|workspaces?|net|run/media)"
                          r"/[^\s\"'<>]*")
SOCKET = re.compile(r"/tmp/[\w.-]+\.sock\b")
#: any other path below a directory of /tmp (a per-user or per-session directory: /tmp/<dir>/<user>/...), and any
#: path under the other temporary directories, /var/tmp and /dev/shm
TMP_PATH = re.compile(r"(?<![\w.~-])/tmp/[\w.-]+/[^\s\"'<>]*|(?<![\w.~-])/(?:var/tmp|dev/shm)/[^\s\"'<>]+")
#: a network share by URL (smb://, cifs://, afp://, nfs://) or by the forward-slash spelling of a UNC path
#: (//server/share) where a token starts (after a space, a quote, `(`, `=`, `,` or `[`: never inside a base64 blob)
NET_SHARE = re.compile(r"(?i)\b(?:smb|cifs|afp|nfs):/{2}(?=[\w.-])[^\s\"'<>]+|(?<![^\s\"'(=,\[])/{2}[\w.-]{2,}/[\w.$-]+[^\s\"'<>]*")
IP_ADDRESS = re.compile(r"(?<![\w.])(?:(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)\.){3}(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)"
                        r"(?!\.?\d)(?!\w)")
#: what makes a dotted four-number a version, not an address: a version word in the 24 characters before it, and a
#: first number below 10 (an address of a private network, 10.x, 172.x, 192.168.x, is never read as a version)
VERSION_BEFORE = re.compile(r"(?i)(?:version|\bver|\bv|build|release|ck3|crusader kings iii)\W{0,3}$")
#: an IPv6 address (full, or compressed with `::`), and a MAC address
IPV6_ADDRESS = re.compile(r"(?i)(?<![\w:.])(?=[0-9a-f:]*::|(?:[0-9a-f]{1,4}:){7})(?:[0-9a-f]{0,4}:){2,7}[0-9a-f]{1,4}"
                          r"(?![\w:])")
MAC_ADDRESS = re.compile(r"(?i)(?<![\w:-])(?:[0-9a-f]{2}[:-]){5}[0-9a-f]{2}(?![\w:-])"
                         r"|(?<![\w.])[0-9a-f]{4}\.[0-9a-f]{4}\.[0-9a-f]{4}(?![\w.])")
#: a user at a machine with no domain, as a login names it: after ssh, scp, rsync, sftp or mosh, or before a remote
#: path (a name, `@`, a machine, then `:` and a path); an e-mail address has a domain and is refused instead; a name
#: and `@` and a word in other text (a CLI's plugin id, a game's text) is not a login
USER_AT_HOST = re.compile(r"(?i)(\b(?:ssh|scp|rsync|sftp|mosh)\s+(?:-\S+\s+)*)[a-z_][\w.-]*@[a-z][\w-]*\b(?!\.)"
                          r"|(?<![\w.+/-])[a-z_][\w.-]*@(?:localhost|[a-z][\w-]{1,62}):(?=[~/]|\s|$)")
#: the keys whose `"KEY": "value"` form is read in capitals only: `"user"` and `"host"` are ordinary keys of the run's
#: own records (run.json's host label)
JSON_KEY_CAPITALS_ONLY = ("USER", "HOST")
#: `KEY=value` in any capitalisation, and `"KEY": "value"` written as text (a JSON object inside a string): in any
#: capitalisation (`"hostname": ...`), USER and HOST in capitals only (JSON_KEY_CAPITALS_ONLY)
ENV_IDENTITY = re.compile(r"\b((?i:" + "|".join(IDENTITY_ENV_KEYS) + r"))=[\"']?(?!<)[^\s\"',;]+[\"']?"
                          r"|([\"'](?:" + "|".join(IDENTITY_ENV_KEYS) + "|(?i:" + "|".join(
                              k for k in IDENTITY_ENV_KEYS if k not in JSON_KEY_CAPITALS_ONLY)
                          + r"))[\"']\s*:\s*)[\"']?(?!<)[^\s\"',;}]+[\"']?")
DRIVE_LINK = re.compile(r"(?i)(?:https?://)?(?:drive|docs)\.google\.com/[^\s\"'<>]*")
UID = re.compile(r"\b(uid|gid|euid|egid)=\d+(\([^)\s]*\))?")
GROUPS = re.compile(r"\bgroups=[\w(),.-]+")
SESSION_LINK = re.compile(r"(?:https?://)?claude\.ai/code/session_\w+")
#: account-limit text the agent CLI writes: a usage-limit message, a named limit (weekly, 5-hour, a model's), how much
#: of it was used, its reset time (a digit, a weekday, a month, tomorrow), a rate limit's size, a credit balance
LIMIT_TEXT_RX = re.compile(
    r"(?i)usage limit|limit reached|hit your limit|\|\d{10}\b|rate limit of \d+|rate limit for your organi[sz]ation"
    r"|tokens per minute|credit balance|out of (?:extra )?usage"
    r"|\b(?:weekly|daily|hourly|monthly|\d+-hour|session|opus|sonnet|haiku|fable)[\s_-]+limit\b"
    r"|\bused\s+\d{1,3}(?:\.\d+)?\s*%|\b\d{1,3}(?:\.\d+)?\s*%\s+of\s+your\b"
    r"|\bresets?\s+(?:at\s+|on\s+)?(?:\d|(?:mon|tue|wed|thu|fri|sat|sun|jan|feb|mar|apr|may|jun|jul|aug|sep|oct|"
    r"nov|dec)[a-z]*\b|tomorrow|today|tonight)")
#: a key or a token: never in a run directory, so it is not rewritten: the copy is refused (a private key's header,
#: provider and forge tokens, a cloud access key id, a chat token, a bearer header, a JWT, a `secret=`/`token:` value)
#: what a key-name rule lets through as a value: a placeholder (`<...>`), or a whole ISO date or time (an expiry such
#: as `"credential_expires_at": "2026-12-21T09:49:00Z"`), the value ending there; a value that only begins with one is
#: read like any other
_ISO_TIME = r"\d{4}-\d\d-\d\d(?:[T ]\d\d:\d\d(?::\d\d(?:\.\d+)?)?)?(?:Z|[+-]\d\d:?\d\d)?"
_NOT_A_KEY_VALUE = r"(?!<|" + _ISO_TIME + r"(?=[\"',}\s]|$))"
#: a quoted value after a key's name, read whole to the closing quote of its own kind (spaces, an apostrophe or the
#: other quote inside it included, and a quote of its own kind escaped with a backslash): 12 characters or more; a
#: value that is a placeholder whole (`"<redacted>"`) or exactly one ISO date or time is no key
#: a value after a `pwd` name that is a path (it begins with `/`, `~`, a drive such as `C:\\`, or a path's placeholder
#: such as `<HOME>/`, or two placeholders a rewritten Windows path leaves) is a working directory, not a passphrase:
#: it is rewritten as any path is, never refused
_PWD_PATH = r"(?![/~]|[A-Za-z]:[\\/]|<[^\"'<>\n]{1,40}>[/\\<])"
QUOTED_KEY_VALUE = (r"|(?i:\b[\w-]*(?:api[_-]?key|secret|passw(?:or)?d|passphrase|credential|private[_-]?key)[\w-]*"
                    r"[\"']?\s*[=:]"
                    r"\s*(?:\"(?!<[^\"<>\n]{1,40}>\"|" + _ISO_TIME + r"\")(?:[^\"\\\n]|\\.){12,}\""
                    r"|'(?!<[^'<>\n]{1,40}>'|" + _ISO_TIME + r"')(?:[^'\\\n]|\\.){12,}'))"
                    r"|(?i:\b[\w-]*pwd[\w-]*[\"']?\s*[=:]"
                    r"\s*(?:\"(?!<[^\"<>\n]{1,40}>\")" + _PWD_PATH + r"(?:[^\"\\\n]|\\.){12,}\""
                    r"|'(?!<[^'<>\n]{1,40}>')" + _PWD_PATH + r"(?:[^'\\\n]|\\.){12,}'))"
                    # a passphrase after `password =>`, `passphrase =>` or a `--password` / `--passphrase` flag, quoted
                    r"|(?i:(?:--[\w-]*(?:passw(?:or)?d|passphrase)[\w-]*\s+|\b[\w-]*(?:passw(?:or)?d|passphrase)[\w-]*"
                    r"\s*=>\s*)"
                    r"(?:\"(?:[^\"\\\n]|\\.){12,}\"|'(?:[^'\\\n]|\\.){12,}'))")
SECRET = re.compile(
    r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY(?: BLOCK)?-----|\bsk-(?:ant-)?[A-Za-z0-9_-]{20,}"
    r"|\bgh[pousr]_[A-Za-z0-9]{20,}|\bAIza[0-9A-Za-z_-]{35}(?![0-9A-Za-z_-])|\b[rs]k_(?:live|test)_[0-9A-Za-z]{16,}"
    r"|\bgithub_pat_\w{20,}|\b(?:AKIA|ASIA)[0-9A-Z]{16}\b|\bxox[abposr]-[\w-]{10,}|(?i:\bbearer\s+[\w.~+/-]{16,})"
    r"|\beyJ[\w-]{8,}\.eyJ[\w-]{8,}\.|\bhf_[A-Za-z0-9]{30,}|\bnpm_[A-Za-z0-9]{36}|\bglpat-[\w-]{20,}"
    # a value after a key's name: 12 characters or more, digits only too; unquoted (up to a space, a quote, a comma,
    # `}`), or quoted whole (spaces included)
    r"|(?i:\b[\w-]*(?:api[_-]?key|secret|passw(?:or)?d|credential|private[_-]?key)[\w-]*[\"']?\s*[=:]\s*[\"']?"
    + _NOT_A_KEY_VALUE + r"[^\s\"',}]{12,})"
    + QUOTED_KEY_VALUE
    # after a token's name, with a letter in it: a token count (`"input_tokens": 123456789`) is not a key
    + r"|(?i:\b[\w-]*token[\w-]*[\"']?\s*[=:]\s*[\"']?" + _NOT_A_KEY_VALUE
    + r"(?=[^\s\"',}]*[A-Za-z])[^\s\"',}]{12,})")
EMAIL = re.compile(r"\b[\w.+-]+@[A-Za-z0-9-]+\.[A-Za-z0-9.-]+\b")
ACCOUNT_KEY_TEXT = re.compile(r"\"(?:" + "|".join(sorted(ACCOUNT_KEYS)) + r")\"\s*:")
LIMIT_PLACEHOLDER = "<limit text removed>"


def ipv4_addresses(s: str) -> list[re.Match]:
    """The IPv4-shaped runs of `s` that are not a version number (a version word before it and a first number
    below 10)."""
    return [m for m in IP_ADDRESS.finditer(s)
            if not (VERSION_BEFORE.search(s[max(0, m.start() - 24):m.start()]) and int(m.group(0).split(".")[0]) < 10)]


def unescaped(s: str) -> str:
    """S with the escapes a path hides behind undone: JSON's `\\/` and `\\u002f`, a doubled backslash, and percent
    encoding (`%2F`), so a rule written for the plain spelling reads it."""
    t = s.replace("\\/", "/")
    t = re.sub(r"(?i)\\u002f", "/", t)
    t = re.sub(r"(?i)\\u005c", "\\\\", t)
    for _ in range(3):                       # double (and triple) percent encoding: %252F is %2F is /
        if not re.search(r"(?i)%(?:2f|5c|40|3a|25)", t):
            break
        t = urllib.parse.unquote(t)
    return t


def escapes_read(s: str) -> str:
    """S with one level of string escapes read (a JSON string inside a JSON string: an escaped quote, a doubled
    backslash), so a Windows path stored that way is read as written; S itself when it holds a bad escape."""
    import warnings
    try:
        with warnings.catch_warnings():           # an unknown escape (`\\/`) is read as written, not warned of
            warnings.simplefilter("ignore", DeprecationWarning)
            return s.encode("utf-8", "surrogateescape").decode("unicode_escape")
    except (UnicodeDecodeError, ValueError):
        return s


#: a base64 run long enough to hide a path or a key (not hex alone: a sha256 is no base64 text)
B64_RUN = re.compile(r"(?<![\w+/=-])(?=[A-Za-z0-9+/_-]*[g-zG-Z+/_-])[A-Za-z0-9+/_-]{16,}={0,2}(?![\w+/=-])")
#: a hex run long enough to hide a path (a sha256 decodes to bytes that are not text, and is not read as one)
HEX_RUN = re.compile(r"(?<![0-9a-fA-F])(?:[0-9a-fA-F]{2}){8,}(?![0-9a-fA-F])")


def _texty(raw: bytes) -> Optional[str]:
    txt = raw.decode("utf-8", "replace")
    ok = sum((c.isprintable() and c != "\ufffd") or c in "\n\t" for c in txt)
    return txt if raw and ok >= 0.9 * len(txt) else None


def base64_texts(s: str, depth: int = 2) -> list[str]:
    """The readable texts hidden in base64 runs of S (standard or URL-safe alphabet; decoded, mostly printable; a
    base64 text inside one read again, DEPTH levels) and in hex runs, for the refusal to read too."""
    out = []
    for m in B64_RUN.finditer(s):
        tok = m.group(0)
        alt = "-_" if ("-" in tok or "_" in tok) else None
        try:
            raw = base64.b64decode(tok + "=" * (-len(tok) % 4), altchars=alt, validate=True)
        except (binascii.Error, ValueError):
            continue
        txt = _texty(raw)
        if txt is not None:
            out.append(txt)
            if depth > 1:
                out += base64_texts(txt, depth - 1)
    for m in HEX_RUN.finditer(s):
        txt = _texty(bytes.fromhex(m.group(0)))
        if txt is not None:
            out.append(txt)
    return out


def _rx_finder(rx: re.Pattern) -> Callable[[str], bool]:
    return lambda s: bool(rx.search(s))


#: the classes the copy is read for after the rewrite (with the run's host names and configured paths): any survivor
#: refuses the copy
REFUSAL_CLASSES: tuple[tuple[str, Callable[[str], bool]], ...] = (
    ("home directory", _rx_finder(HOME_DIR)), ("home directory", _rx_finder(TILDE_USER)),
    ("windows user directory", _rx_finder(WIN_HOME)), ("network path", _rx_finder(UNC_PATH)),
    ("network path", _rx_finder(NET_SHARE)),
    ("private path", _rx_finder(PRIVATE_PATH)), ("socket path", _rx_finder(SOCKET)), ("tmp path", _rx_finder(TMP_PATH)),
    ("ipv4 address", lambda s, find=ipv4_addresses: bool(find(s))), ("ipv6 address", _rx_finder(IPV6_ADDRESS)),
    ("mac address", _rx_finder(MAC_ADDRESS)), ("user id", _rx_finder(re.compile(r"\b[ug]id=\d+"))),
    ("user at host", _rx_finder(USER_AT_HOST)), ("user or host variable", _rx_finder(ENV_IDENTITY)),
    ("session link", _rx_finder(re.compile(r"claude\.ai/code/session_\w+"))), ("drive link", _rx_finder(DRIVE_LINK)),
    ("limit text", _rx_finder(LIMIT_TEXT_RX)), ("uuid", _rx_finder(UUID)), ("e-mail address", _rx_finder(EMAIL)),
    ("account key", _rx_finder(ACCOUNT_KEY_TEXT)), ("key or token", _rx_finder(SECRET)))


#: the rules in PUBLISHED.json, by class (never a sample: the record is read by the refusal too)
RULES = ("limit text (LIMIT_TEXT_RX) -> the whole string replaced, with (rate limit) / (api error) as the scorer read "
         "the original",
         "escapes (a JSON slash, a unicode slash, percent encoding) read plain before every rule",
         "configured paths -> <VARIABLE>", "home directories (also Windows user directories, a tilde home) -> <HOME>",
         "Windows network paths -> <network path>",
         "paths under mnt, srv, opt, data, media, scratch, nfs, var/lib, Volumes, workspace, net -> <private path>",
         "sockets under tmp -> <socket>; any path below a directory of tmp, or under var/tmp or dev/shm -> <tmp path>",
         "network shares (smb, cifs, afp, nfs URLs, a UNC path with forward slashes) -> <network path>",
         "user and host variables (USER, LOGNAME, HOSTNAME, ...) -> value removed",
         "a user at a machine with no domain, as a login names it -> <user>@<host>",
         "host names, any capitalisation, outside letters and digits -> <host name>",
         "user names (this account, the environment, every home path's) -> <user name>",
         "IPv4 (not a version number), IPv6 -> <ip address>; MAC -> <mac address>",
         "uid, gid and groups -> numbers and names removed", "session links -> <session link>",
         "Drive and Docs links -> <drive link>", "rate_limit_event -> type and rate_limit_info.status",
         "system/init -> type, subtype, model, tools, mcp_servers (name, status)",
         "account keys and their family (a reset time, an over-limit field, a utilisation figure) dropped",
         "session ids -> session-N (numbered first), every UUID left -> id-N",
         "refused, never rewritten: e-mail addresses, keys and tokens, symbolic links, hard links",
         "damaged files (not UTF-8 text, a NUL byte, a gzip that does not decompress) -> not published; a stub "
         "<file>.damaged.json names each (its size, the sha256 of its bytes, the damage)",
         "read by a person before release (docs/prereg-munster-kingdom.md, section 11)")


class PublishRefused(Exception):
    pass


def host_rx(name: str) -> re.Pattern:
    """A host name in any capitalisation, outside a run of letters and digits."""
    return re.compile(r"(?i)(?<![A-Za-z0-9])" + re.escape(name) + r"(?![A-Za-z0-9])")


def limit_placeholder(s: str) -> str:
    """What stands for a limit text: the scorer's reading of the original kept (score.LIMIT_TEXT, score.API_TEXT)."""
    out = LIMIT_PLACEHOLDER
    if LIMIT_TEXT.search(s):
        out += " (rate limit)"
    if API_TEXT.search(s):
        out += " (api error)"
    return out


class Rewriter:
    """The rewrite rules of one run, applied to every file in turn (one numbering of ids across them)."""

    def __init__(self, run: Path, env: dict, hosts: list[str], session_ids: Optional[list[str]] = None,
                 users: Optional[list[str]] = None):
        self.counts: dict[str, int] = {}
        self.ids: dict[str, str] = {}
        self.n_id = 0
        for i, sid in enumerate(dict.fromkeys(session_ids or []), 1):
            self.ids[sid] = f"session-{i}"
        if self.ids:
            self._count("session-N (a session id)", len(self.ids))
        roots: dict[str, str] = {}
        rj = _json(run / "run.json") or {}
        extra = {"run directory": str(run.resolve()), "expectation": ((rj.get("expectation") or {}) if
                                                                 isinstance(rj.get("expectation"), dict) else {}).get("path"),
                 "schedule": ((rj.get("series") or {}) if isinstance(rj.get("series"), dict) else {}).get("schedule")}
        for name, v in [*((k, env.get(k)) for k in PATH_VARS), *extra.items()]:
            if not v or not isinstance(v, str) or not os.path.isabs(v) or v == "/":
                continue
            for form in {v.rstrip("/"), os.path.realpath(v)}:
                if form and form != "/" and form not in roots:
                    roots[form] = name
        self.paths = sorted(roots.items(), key=lambda kv: -len(kv[0]))
        names = set()
        for h in hosts:
            if isinstance(h, str) and h.strip():
                names.add(h.strip())
                names.add(h.strip().split(".")[0])
        self.hosts = sorted({h for h in names if len(h) >= 2}, key=len, reverse=True)
        self.host_rxs = [host_rx(h) for h in self.hosts]
        # user names (this host's account, the environment's, and every name a home path in the files carries):
        # replaced like host names, outside letters and digits; a name of under three characters is not (it would
        # rewrite ordinary text), and the refusal cannot read it either
        self.users = sorted({u for u in users or [] if isinstance(u, str) and len(u) >= 3
                             and u.lower() not in {h.lower() for h in self.hosts}}, key=len, reverse=True)
        self.user_rxs = [host_rx(u) for u in self.users]

    def _count(self, what: str, n: int = 1) -> None:
        if n:
            self.counts[what] = self.counts.get(what, 0) + n

    def _sub(self, rx: re.Pattern, repl: Any, s: str, what: str) -> str:
        s, n = rx.subn(repl, s)
        self._count(what, n)
        return s

    def text(self, s: str) -> str:
        if LIMIT_TEXT_RX.search(s) or LIMIT_TEXT_RX.search(unescaped(s)):
            self._count(f"{LIMIT_PLACEHOLDER} (limit text, the whole string)")
            return limit_placeholder(s)
        u = self._plain(s)
        if u != s:
            self._count("escaped or percent-encoded text read plain")
            s = u
        for root, name in self.paths:
            if root in s:
                self._count(f"<{name}>", s.count(root))
                s = s.replace(root, f"<{name}>")
        s = self._sub(HOME_DIR, "<HOME>", s, "<HOME> (another home directory)")
        s = self._sub(TILDE_USER, "<HOME>", s, "<HOME> (a tilde home)")
        s = self._sub(WIN_HOME, "<HOME>", s, "<HOME> (a Windows user directory)")
        s = self._sub(UNC_PATH, "<network path>", s, "<network path> (a Windows share)")
        s = self._sub(NET_SHARE, "<network path>", s, "<network path> (a share by URL, or a UNC path with forward slashes)")
        s = self._sub(PRIVATE_PATH, "<private path>", s, "<private path> (/mnt, /srv, /opt, /data, /Volumes, ...)")
        s = self._sub(SOCKET, "<socket>", s, "<socket> (a socket under /tmp)")
        s = self._sub(TMP_PATH, "<tmp path>", s, "<tmp path> (below a directory of /tmp)")
        s = self._sub(ENV_IDENTITY, lambda m: f"{m.group(1)}=<removed>" if m.group(1) else f"{m.group(2)}<removed>", s,
                      "a user or host variable's value removed")
        s = self._sub(USER_AT_HOST, lambda m: (m.group(1) or "") + "<user>@<host>", s, "<user>@<host>")
        s = self._sub(UID, lambda m: f"{m.group(1)}=<id>" + ("(<name>)" if m.group(2) else ""), s,
                      "uid=<id>(<name>)")
        s = self._sub(GROUPS, "groups=<groups>", s, "groups=<groups>")
        s = self._names(s)
        s = self._sub(SESSION_LINK, "<session link>", s, "<session link>")
        s = self._sub(DRIVE_LINK, "<drive link>", s, "<drive link>")
        addrs = ipv4_addresses(s)
        for m in reversed(addrs):
            s = s[:m.start()] + "<ip address>" + s[m.end():]
        self._count("<ip address>", len(addrs))
        s = self._sub(IPV6_ADDRESS, "<ip address>", s, "<ip address> (IPv6)")
        s = self._sub(MAC_ADDRESS, "<mac address>", s, "<mac address>")
        return UUID.sub(lambda m: self._id(m.group(0)), s)

    def _plain(self, s: str) -> str:
        return unescaped(s)

    def _names(self, s: str) -> str:
        """Host and user names replaced outside e-mail addresses (an address is left whole, so the refusal sees it)."""
        out, last = [], 0
        for m in [*EMAIL.finditer(s), None]:
            seg = s[last:m.start()] if m else s[last:]
            for rx in self.host_rxs:
                seg = self._sub(rx, "<host name>", seg, "<host name>")
            for rx in self.user_rxs:
                seg = self._sub(rx, "<user name>", seg, "<user name>")
            out.append(seg)
            if m:
                out.append(m.group(0))
                last = m.end()
        return "".join(out)

    def _id(self, v: str) -> str:
        if v not in self.ids:
            self.n_id += 1
            self.ids[v] = f"id-{self.n_id}"
            self._count("id-N (another UUID)")
        return self.ids[v]

    def value(self, v: Any, key: str = "") -> Any:
        if isinstance(v, dict):
            return {(self.text(k) if isinstance(k, str) else k): self.value(x, k) for k, x in v.items()
                    if not self._drop(k)}
        if isinstance(v, list):
            return [self.value(x) for x in v]
        if isinstance(v, str):
            if key == "session_id" and v:
                return self.ids[v] if v in self.ids else self.text(v)
            if key in ("host", "hostname") and v in self.hosts:
                self._count("<host name>")
                return "<host name>"
            if key in IDENTITY_ENV_KEYS:
                self._count("a user or host variable's value removed")
                return "<removed>"
            return self.text(v)
        return v

    def _drop(self, k: Any) -> bool:
        if k in ACCOUNT_KEYS or (isinstance(k, str) and ACCOUNT_KEY_FAMILY.search(k)):
            self._count(f"dropped key {k}" if k in ACCOUNT_KEYS else "dropped key (a reset, overage or usage field)")
            return True
        return False

    def event(self, ev: Any) -> Any:
        """One agent CLI stream event (transcript.jsonl)."""
        if not isinstance(ev, dict):
            return self.value(ev)
        if ev.get("type") == "rate_limit_event":
            info = ev.get("rate_limit_info") if isinstance(ev.get("rate_limit_info"), dict) else {}
            self._count("rate_limit_event reduced to its status")
            out = {"type": "rate_limit_event", "rate_limit_info": {"status": self.value(info.get("status"))}}
            if ev.get("session_id"):
                out["session_id"] = self.value(str(ev["session_id"]), "session_id")
            return out
        if ev.get("type") == "system" and ev.get("subtype") == "init":
            self._count("init event reduced to type, subtype, model, tools and MCP servers")
            kept = {k: ev[k] for k in INIT_KEEP if k in ev}
            if isinstance(kept.get("mcp_servers"), list):
                kept["mcp_servers"] = [{k: s.get(k) for k in ("name", "status")} if isinstance(s, dict) else s
                                       for s in kept["mcp_servers"]]
            return self.value(kept)
        return self.value(ev)


#: a user's name where a path or a variable carries it (a home directory, /tmp/<dir>/<user>, a tilde home)
USER_IN_PATH = re.compile(r"/(?:home|Users|var/home)/([^/\s\"'<>]+)|(?<![\w/~])~([a-z_][\w.-]*)(?=/)")


def user_names(env: dict, raws: list[bytes]) -> list[str]:
    """The user names to replace: this process's account, USER/LOGNAME/USERNAME and HOME's last part from the
    environment the rewrite reads, and every name a home directory or a tilde home carries in the files."""
    names = set()
    try:
        names.add(pwd.getpwuid(os.getuid()).pw_name)
    except (KeyError, OSError):
        pass
    for k in ("USER", "LOGNAME", "USERNAME", "SUDO_USER"):
        if env.get(k):
            names.add(env[k])
    if env.get("HOME") and os.path.isabs(env["HOME"]) and env["HOME"].rstrip("/"):
        names.add(os.path.basename(env["HOME"].rstrip("/")))
    for raw in raws:
        text = unescaped(raw.decode("utf-8", "replace"))
        for m in USER_IN_PATH.finditer(text):
            names.add(m.group(1) or m.group(2))
    return sorted(n for n in names if n and n not in ("root", "shared", "Shared", "public", "Public"))


def _json(p: Path) -> Optional[dict]:
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return d if isinstance(d, dict) else None


def _json_object(raw: bytes) -> Optional[dict]:
    try:
        d = json.loads(raw.decode("utf-8"))
    except ValueError:
        return None
    return d if isinstance(d, dict) else None


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def session_ids(raws: list[tuple[str, bytes]]) -> list[str]:
    """Every `session_id` value of the files, in order of first appearance (the files in PUBLISHED_FILES order)."""
    out: list[str] = []

    def walk(v: Any) -> None:
        if isinstance(v, dict):
            for k, x in v.items():
                if k == "session_id" and isinstance(x, str) and x:
                    out.append(x)
                walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)
    for name, raw in raws:
        text = raw.decode("utf-8", "surrogateescape")
        chunks = [text] if name.endswith(".json") else text.splitlines() if ".jsonl" in name else []
        for c in chunks:
            try:
                walk(json.loads(c))
            except ValueError:
                pass
    return out


def rewrite_file(name: str, raw: bytes, rw: Rewriter) -> bytes:
    text = raw.decode("utf-8", "surrogateescape")
    if name.endswith(".json"):
        try:
            return (json.dumps(rw.value(json.loads(text)), indent=1) + "\n").encode()
        except ValueError:
            pass
    if ".jsonl" in name or name.endswith(".json"):
        out = []
        for ln in text.splitlines():
            if not ln.strip():
                out.append(ln)
                continue
            try:
                ev = json.loads(ln)
            except ValueError:
                out.append(rw.text(ln))
                continue
            out.append(json.dumps(rw.event(ev) if name.startswith("transcript") else rw.value(ev)))
        return ("\n".join(out) + ("\n" if text.endswith("\n") else "")).encode("utf-8", "surrogateescape")
    return ("\n".join(rw.text(ln) for ln in text.splitlines()) + ("\n" if text.endswith("\n") else "")).encode(
        "utf-8", "surrogateescape")


def findings(name: str, data: bytes, rw: Rewriter) -> list[str]:
    """What survives in an output that must not: every refusal class, by file and line (never the value)."""
    text = data.decode("utf-8", "surrogateescape")
    checks: list[tuple[str, Callable[[str], bool]]] = list(REFUSAL_CLASSES)
    checks += [("host name", _rx_finder(rx)) for rx in rw.host_rxs]
    checks += [(f"path of {n}", _rx_finder(re.compile(re.escape(r)))) for r, n in rw.paths]
    checks += [("user name", _rx_finder(rx)) for rx in rw.user_rxs]
    out = []
    for i, ln in enumerate(text.splitlines(), 1):
        plain = unescaped(ln)
        forms = [(ln, ""), (plain, " (escaped)"), (escapes_read(plain), " (escaped)"),
                 *((t, " (base64)") for t in base64_texts(ln))]
        for what, hit in checks:
            for form, how in forms:
                if hit(form):
                    out.append(f"{name}:{i}: {what}{how}")
                    break
    return list(dict.fromkeys(out))


def run_json_host_names(raw: bytes) -> list[str]:
    """The machine names a run.json that does not read still holds: every `"host": "NAME"` in its bytes whose NAME is
    not a host label (the top-level `host` is the schedule's label: a, b)."""
    text = raw.replace(b"\0", b"").decode("utf-8", "replace")
    return sorted({m.group(1) for m in RUN_JSON_HOST.finditer(text) if not HOST_LABEL.match(m.group(1))})


def publish(run: Path, dest: Path, env: dict, host_names: Optional[list] = None) -> dict:
    """Write the publishable copy of RUN into DEST (which must not exist); the record PUBLISHED.json holds. Raises
    PublishRefused (DEST not written) when a refusal-list pattern survives the rewrite, or when run.json does not
    read and no machine name is known (HOST_NAMES: the names given)."""
    run = Path(run)
    if not run.is_dir():
        raise PublishRefused(f"RUN_DIR {run} is not a directory")
    if dest.exists():
        raise PublishRefused(f"DEST {dest} exists (a publishable copy is written into a new directory)")
    rj = _json(run / "run.json")
    # the machine names: run.json's (read whatever the types of its fields: a field of another type names none), the
    # operator's (--host-name, RATE_HOST_NAMES) and this host's own
    named = list(host_names or []) + [h for h in re.split(r"[\s,]+", env.get(HOST_NAMES_VAR) or "") if h]
    if rj is None and (run / "run.json").is_file():
        # a run.json that does not read: the names it holds are read from its bytes (a `"host": "..."` that is not
        # a label); with none there and none named, the copy is refused, never made with the names unread
        found = run_json_host_names((run / "run.json").read_bytes())
        if not found and not named:
            raise PublishRefused("run.json does not read, so the machine names it records are not known and would "
                                 "not be rewritten: name every machine that played or ended this attempt with "
                                 f"--host-name NAME (or {HOST_NAMES_VAR}); nothing published")
        named += found
    elif not (run / "run.json").exists() and not named:
        from ck3bench.harness.score import parse_run_id
        if parse_run_id(run.name) is not None:
            # a series attempt whose run.json is gone (a documented crash shape, a counted attempt): the machine names
            # it would record are not known, so the copy is refused, as for one that does not read
            raise PublishRefused("run.json is absent from this series attempt's directory, so the machine names it "
                                 "would record are not known and would not be rewritten: name every machine that "
                                 f"played or ended this attempt with --host-name NAME (or {HOST_NAMES_VAR}); "
                                 "nothing published")
    rj = rj or {}
    hosts = [h for part in ("start", "end") for h in [(rj.get(part) if isinstance(rj.get(part), dict) else {}).get("host")]
             if isinstance(h, str)] + named + [socket.gethostname()]
    label = rj.get("host")
    if label is not None and not (isinstance(label, str) and HOST_LABEL.match(label)):
        # the schedule's label for the host stands in the run id and every file: a machine's name is refused
        raise PublishRefused(f"run.json `host` is not a host label ({HOST_LABEL.pattern}); a schedule names its "
                             "hosts by labels (a, b), never by the machine's name; nothing published")
    files, raws, bad, damaged = [], [], [], []
    for name in PUBLISHED_FILES:
        for cand in (run / name, run / (name + ".gz")):
            # a link is refused, whatever it points at (its target is outside what the run wrote)
            if cand.is_symlink() or (cand.exists() and run.resolve() not in cand.resolve().parents):
                bad.append(f"{cand.name}: a symbolic link or a file outside the run directory (not read)")
            elif cand.is_file() and cand.stat().st_nlink > 1:
                # a hard link is a second name of a file that may live outside the run: its content is not the run's
                bad.append(f"{cand.name}: a file with more than one hard link (not read)")
        src = run / name
        gz = run / (name + ".gz")
        if bad and bad[-1].startswith((src.name + ":", gz.name + ":")):
            continue
        if not src.is_file() and gz.is_file():
            packed_raw = gz.read_bytes()
            try:
                raws.append((name + ".gz", gzip.decompress(packed_raw), True))
            except (OSError, EOFError, zlib.error) as e:      # gzip.BadGzipFile is an OSError
                damaged.append((gz.name, packed_raw, f"not a readable gzip file ({type(e).__name__})"))
        elif src.is_file():
            raws.append((name, src.read_bytes(), False))
        else:
            files.append({"file": name, "state": "absent: the run wrote no such file"})
    for name, raw, packed in list(raws):
        why = []
        try:
            raw.decode("utf-8")
        except UnicodeDecodeError:
            why.append("not UTF-8 text")
        if b"\0" in raw:
            why.append("a NUL byte")
        if not why and name == "run.json" and _json_object(raw) is None:
            why.append("not one JSON object")          # a cut record: its names are read from its bytes (above)
        if why:
            # damage a host going down can leave (a NUL-filled tail, a cut multi-byte character): what it holds is not
            # read, and a stub stands for it
            raws.remove((name, raw, packed))
            damaged.append((name, (run / name).read_bytes(), "; ".join(why)))
    if bad:
        raise PublishRefused(f"{len(bad)} file(s) not read; nothing published:\n  " + "\n  ".join(bad))
    rw = Rewriter(run, env, [h for h in hosts if h], session_ids([(n.removesuffix(".gz"), r) for n, r, _ in raws]),
                  users=user_names(env, [r for _, r, _ in raws] + [run.name.encode()]))
    outputs = {}
    for name, raw, packed in raws:
        new = rewrite_file(name.removesuffix(".gz"), raw, rw)
        outputs[name] = (new, packed)
        files.append({"file": name, "source_sha256": _sha(raw), "published_sha256": _sha(new)})
    for name, raw, why in damaged:
        stub = (json.dumps({"schema": "ck3bench.series.damaged/1", "file": name, "bytes": len(raw), "sha256": _sha(raw),
                            "damage": why, "published": False}, indent=1) + "\n").encode()
        outputs[f"{name}.damaged.json"] = (stub, False)
        files.append({"file": name, "state": f"damaged ({why}): not published; {name}.damaged.json names it",
                      "source_sha256": _sha(raw)})
    run_name = rw.text(run.name)
    bad = [f for name, (b, _) in outputs.items() for f in findings(name, b, rw)]
    record = {"schema": "ck3bench.series.published/1", "run": run_name, "files": files,
                  "placeholders": dict(sorted(rw.counts.items())),
                  "rules": list(RULES)}
    rec_text = json.dumps(record, indent=1) + "\n"
    bad += findings("PUBLISHED.json", rec_text.encode(), rw)
    if bad:
        raise PublishRefused(f"{len(bad)} private pattern(s) survive the rewrite; nothing published:\n  "
                             + "\n  ".join(bad[:40]) + ("\n  ..." if len(bad) > 40 else ""))
    dest.mkdir(parents=True, mode=0o700)
    try:
        for name, (b, packed) in outputs.items():
            (dest / name).write_bytes(gzip.compress(b, mtime=0) if packed else b)
        (dest / "PUBLISHED.json").write_text(rec_text)
    except BaseException:
        shutil.rmtree(dest, ignore_errors=True)
        raise
    return record


def main(argv: list[str]) -> int:
    names = argv[3::2] if len(argv) > 2 else []
    if not argv or argv[0] in ("-h", "--help") or len(argv) < 2 or len(argv) % 2 or \
            any(f != "--host-name" for f in argv[2::2]) or not all(names):
        print(__doc__)
        return 0 if argv and argv[0] in ("-h", "--help") else 2
    try:
        rec = publish(Path(argv[0]), Path(argv[1]), dict(os.environ), host_names=names)
    except PublishRefused as e:
        print(f"REFUSE publish: {e}", file=sys.stderr)
        return 5
    print(json.dumps({"published": argv[1], "files": [f["file"] for f in rec["files"] if "published_sha256" in f],
                      "placeholders": rec["placeholders"]}))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
