"""Per-turn accounting of a seated run, read from its transcript (the session's own stream-json events).

    python -m ck3bench.harness.accounting RUN_DIR   -> a table on stdout and RUN_DIR/accounting.json

A turn is everything between two user messages the runner sent (transcript events of type `user`
whose content is a string; tool results come back as `user` events whose content is a list). Per
turn: the tool calls by name, the tokens each API message reported (an API message that arrives as
several events is counted once, by its id), the reply's `result` (its usage, duration and the
session's own cost figure, recorded as reported) and the wall seconds from the runner's turns.jsonl.
Nothing is estimated: a turn with no `result` says so.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

KEYS = ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")


def turns_from_transcript(path: Path) -> list[dict]:
    turns: list[dict] = []
    cur = None
    seen: set = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        t = ev.get("type")
        if t == "result":
            if cur is not None:
                cur["result"] = {k: ev.get(k) for k in ("subtype", "is_error", "duration_ms", "num_turns",
                                                         "total_cost_usd", "usage")}
                turns.append(cur)
            cur = None
            continue
        if cur is None:
            cur = {"calls": {}, "usage": dict.fromkeys(KEYS, 0), "api_messages": 0, "result": None}
        if t == "assistant":
            m = ev.get("message") or {}
            for b in m.get("content") or []:
                if isinstance(b, dict) and b.get("type") == "tool_use":
                    n = str(b.get("name", "")).split("__")[-1]
                    cur["calls"][n] = cur["calls"].get(n, 0) + 1
            if m.get("id") not in seen:
                seen.add(m.get("id"))
                cur["api_messages"] += 1
                for k in KEYS:
                    cur["usage"][k] += int((m.get("usage") or {}).get(k) or 0)
    if cur is not None and (cur["calls"] or cur["api_messages"]):
        turns.append(cur)               # the last reply, cut off before its result
    return turns


def run_cost(d: Path, turns: list[dict]) -> dict:
    """The run's cost as the sessions reported it (`total_cost_usd` of each `result`). A fresh-turn
    run has one session per turn, each reporting its own total: summed. A continuous run has one session whose
    total grows (a recorded single-session run): its last report. The turn mode is end.json's; unread, or a turn with no result, and the cost is
    absent with why (never a partial sum presented as the whole)."""
    try:
        mode = json.loads((d / "end.json").read_text()).get("turn_mode")
    except (OSError, ValueError):
        mode = None
    costs = [(t.get("result") or {}).get("total_cost_usd") for t in turns]
    if mode not in ("fresh", "continuous"):
        return {"cost_usd_reported": None, "cost_why": f"turn mode unread from end.json ({mode!r})"}
    if not costs or any(not isinstance(c, (int, float)) for c in costs):
        return {"cost_usd_reported": None,
                "cost_why": f"{sum(1 for c in costs if not isinstance(c, (int, float)))} of {len(costs)} turns "
                            "report no total_cost_usd"}
    return {"cost_usd_reported": round(sum(costs) if mode == "fresh" else costs[-1], 6), "cost_rule": mode}


def main(run_dir: str) -> int:
    d = Path(run_dir)
    turns = turns_from_transcript(d / "transcript.jsonl")
    walls = [json.loads(x).get("wall_s") for x in (d / "turns.jsonl").read_text().splitlines()] \
        if (d / "turns.jsonl").is_file() else []
    tot = dict.fromkeys(KEYS, 0)
    print(f"{'turn':>4} {'calls':>5} {'in':>9} {'out':>7} {'cache_rd':>10} {'cache_wr':>9} {'wall_s':>8}  tools")
    for i, t in enumerate(turns, 1):
        u = t["usage"]
        for k in KEYS:
            tot[k] += u[k]
        w = walls[i - 1] if i - 1 < len(walls) else None
        t["wall_s"] = w
        print(f"{i:>4} {sum(t['calls'].values()):>5} {u['input_tokens']:>9} {u['output_tokens']:>7} "
              f"{u['cache_read_input_tokens']:>10} {u['cache_creation_input_tokens']:>9} {w if w is not None else '-':>8}  "
              f"{','.join(f'{k}x{v}' for k, v in sorted(t['calls'].items()))}"
              + ("" if t["result"] else "  (no result)"))
    out = {"turns": turns, "totals": tot, "n_turns": len(turns),
           "calls": sum(sum(t["calls"].values()) for t in turns),
           "wall_s": sum(w for w in walls if isinstance(w, (int, float))), **run_cost(d, turns)}
    (d / "accounting.json").write_text(json.dumps(out, indent=1))
    print("totals", json.dumps({k: v for k, v in out.items() if k != "turns"}))
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print(__doc__)
        sys.exit(0 if len(sys.argv) > 1 else 2)
    sys.exit(main(sys.argv[1]))
