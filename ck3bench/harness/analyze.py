"""Turn-by-turn reading of a seated run's transcript (the session's stream-json).

    python -m ck3bench.harness.analyze RUN_DIR      -> RUN_DIR/analysis.json and a table on stdout

Per turn (the events up to and including an `end_turn` call's reply, or a `result` event: a session may
play several turns in one reply): the tool calls in order (name, input), each reply's
bytes and whether it was an error (`is_error`, or a JSON reply with `ok: false`, with its gate), the
assistant's text blocks (its reasoning as it wrote it), tokens per API message counted once by message id,
and the runner's wall seconds. Repeated identical calls within a turn are flagged. Nothing is estimated.

Turn k is env step k, and each row carries the game-days that step's clock ran (`days`, ck3bench's step_days: a
wake task's `wake.days`, never cycle_days) and the session it was played in (`session`: the count of init events
so far; the runner opens one per turn).
"""
from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path

KEYS = ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")


def _text(c) -> str:
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return "\n".join(x.get("text", "") for x in c if isinstance(x, dict))
    return ""


def gate_of(text: str):
    try:
        d = json.loads(text)
    except ValueError:
        return None
    if isinstance(d, dict) and isinstance(d.get("result"), dict) and "tool_name" in d:
        d = {**d["result"], "ok": d.get("ok") if d["result"].get("ok") is None else d["result"].get("ok"),
             "error": d.get("error")}          # the env's call record wraps the tool's envelope
    if isinstance(d, dict) and d.get("ok") is False:
        b = d.get("blocked") or {}
        return {"gate": b.get("gate") or d.get("code"), "summary": (b.get("summary") or d.get("detail") or
                                                                    d.get("error") or "")[:300]}
    return None


#: What did not read in the run's files, said as named lines (main prints them) instead of a traceback
PROBLEMS: list = []


def turns(path: Path) -> list[dict]:
    """The turns of a transcript, read through the scorer's tolerant reader (score.read_jsonl): a file that is
    absent, cut or not UTF-8 gives the turns of the lines that read, and an event whose fields are of another type
    than the stream writes is skipped; each is said in PROBLEMS."""
    from ck3bench.harness.score import read_jsonl
    if not path.is_file():
        PROBLEMS.append(f"{path.name}: absent; no turns read")
        return []
    evs, why = read_jsonl(path)
    if why:
        PROBLEMS.append(f"{path.name}: {why}; the lines that read are used")
    out, cur, seen, pending, session = [], None, set(), {}, 0
    skipped = 0
    for ev in evs:
        try:
            cur, session = _event(ev, out, cur, seen, pending, session)
        except (AttributeError, TypeError, KeyError, ValueError):
            skipped += 1
    if skipped:
        PROBLEMS.append(f"{path.name}: {skipped} events with a field of another type than the stream writes, skipped")
    if cur is not None and (cur["calls"] or cur["texts"]):
        out.append(cur)
    return out


def _event(ev: dict, out: list, cur, seen: set, pending: dict, session: int):
    """One stream event into the turns being read (turns); returns (the current turn, the session count)."""
    t = ev.get("type")
    if t == "system" and ev.get("subtype") == "init":
        session += 1
    if cur is None:
        cur = {"calls": [], "texts": [], "usage": dict.fromkeys(KEYS, 0), "api_messages": 0, "session": session}
    if t == "assistant":
        m = ev.get("message") or {}
        for b in m.get("content") or []:
            if not isinstance(b, dict):
                continue
            if b.get("type") == "tool_use":
                row = {"tool": str(b.get("name", "")).split("__")[-1], "input": b.get("input")}
                pending[b.get("id")] = row
                cur["calls"].append(row)
            elif b.get("type") == "text" and b.get("text", "").strip():
                cur["texts"].append(b["text"].strip())
        if m.get("id") not in seen:
            seen.add(m.get("id"))
            cur["api_messages"] += 1
            for k in KEYS:
                cur["usage"][k] += int((m.get("usage") or {}).get(k) or 0)
    elif t == "user":
        for b in (ev.get("message") or {}).get("content") or []:
            if isinstance(b, dict) and b.get("type") == "tool_result" and b.get("tool_use_id") in pending:
                row = pending.pop(b["tool_use_id"])
                txt = _text(b.get("content"))
                row["bytes"] = len(txt.encode())
                row["is_error"] = bool(b.get("is_error"))
                g = gate_of(txt)
                if g:
                    row["refused"] = g
                if row["tool"] == "end_turn":       # the turn ends here, whatever the reply does next
                    cur["result"] = None
                    out.append(cur)
                    cur = {"calls": [], "texts": [], "usage": dict.fromkeys(KEYS, 0), "api_messages": 0,
                           "session": session}
    elif t == "result":
        res = {k: ev.get(k) for k in ("subtype", "is_error", "duration_ms", "num_turns",
                                      "total_cost_usd", "usage")}
        if cur["calls"]:
            cur["result"] = res
            out.append(cur)
        elif out:                                  # text only after the last end_turn: that turn's report
            out[-1]["texts"] += cur["texts"]
            for k in KEYS:
                out[-1]["usage"][k] += cur["usage"][k]
            out[-1]["api_messages"] += cur["api_messages"]
            out[-1]["result"] = res                # a reply's closing text after its last end_turn
        cur = None
    return cur, session


def main(run_dir: str) -> int:
    PROBLEMS.clear()
    d = Path(run_dir)
    ts = turns(d / "transcript.jsonl")
    import datetime as _dt
    walls, days = [], []    # per env step: seconds between its record and the previous one; its game-days
    tj = d / "trajectory.jsonl"
    if tj.is_file():
        from ck3bench.env.trajectory import step_days  # the one rule for a step's length; no copy here
        from ck3bench.harness.score import read_jsonl  # the tolerant reader: a cut file reads
        recs, why = read_jsonl(tj)
        if why:
            PROBLEMS.append(f"trajectory.jsonl: {why}; the rows that read are used")
        try:
            stamps = [r["wall_utc"] for r in recs if r.get("kind") in ("header", "step")]
            P = lambda x: _dt.datetime.fromisoformat(x.replace("Z", "+00:00"))
            walls = [(P(b) - P(a)).total_seconds() for a, b in zip(stamps, stamps[1:])]
            head = next(r for r in recs if r.get("kind") == "header")
            days = [step_days(head, r) for r in recs if r.get("kind") == "step"]
        except ValueError as e:
            if "wake.days" in str(e):
                # a wake step without its length: no reader takes it from cycle_days (trajectory.step_days); refused
                print(f"analyze: trajectory.jsonl: {e}; nothing written", file=sys.stderr)
                return 2
            walls, days = [], []
            PROBLEMS.append(f"trajectory.jsonl: the steps' wall times and game-days did not read (ValueError: "
                            "a field of another type than the env writes); not given")
        except (KeyError, TypeError, AttributeError, StopIteration) as e:
            walls, days = [], []
            PROBLEMS.append(f"trajectory.jsonl: the steps' wall times and game-days did not read ({type(e).__name__}: "
                            "no header row, or a field of another type than the env writes); not given")
    else:
        PROBLEMS.append("trajectory.jsonl: absent; the steps' wall times and game-days not given")
    rows = []
    for i, t in enumerate(ts, 1):
        seen, rep = {}, []
        for c in t["calls"]:
            k = json.dumps([c["tool"], c["input"]], sort_keys=True)
            if k in seen:
                rep.append(c["tool"])
            seen[k] = 1
        by = {}
        for c in t["calls"]:
            by[c["tool"]] = by.get(c["tool"], 0) + 1
        rows.append({"turn": i, "wall_s": walls[i - 1] if i - 1 < len(walls) else None,
                     "days": days[i - 1] if i - 1 < len(days) else None, "session": t.get("session"),
                     "n_calls": len(t["calls"]), "by_tool": by, "bytes": sum(c.get("bytes", 0) for c in t["calls"]),
                     "usage": t["usage"], "api_messages": t["api_messages"], "repeated": rep,
                     "refused": [{"tool": c["tool"], "input": c["input"], **c["refused"]} for c in t["calls"]
                                 if c.get("refused")],
                     "errors": [{"tool": c["tool"], "input": c["input"]} for c in t["calls"] if c.get("is_error")],
                     "calls": [{k: c.get(k) for k in ("tool", "input", "bytes")} for c in t["calls"]],
                     "texts": t["texts"], "result": t.get("result")})
    print(f"{'turn':>4} {'calls':>5} {'bytes':>8} {'in':>6} {'out':>6} {'cache_rd':>9} {'cache_wr':>8} {'wall':>7}  tools")
    for r in rows:
        u = r["usage"]
        print(f"{r['turn']:>4} {r['n_calls']:>5} {r['bytes']:>8} {u['input_tokens']:>6} {u['output_tokens']:>6} "
              f"{u['cache_read_input_tokens']:>9} {u['cache_creation_input_tokens']:>8} {r['wall_s'] or 0:>7.1f}  "
              f"{','.join(f'{k}x{v}' for k, v in sorted(r['by_tool'].items()))}"
              + (f"  REFUSED {[x['tool'] + ':' + str(x['gate']) for x in r['refused']]}" if r["refused"] else ""))
    tot = {k: sum(r["usage"][k] for r in rows) for k in KEYS}
    summ = {"turns": len(rows), "sessions": max([r["session"] or 0 for r in rows] or [0]),
            "game_days": sum(x for x in days), "totals": tot, "calls": sum(r["n_calls"] for r in rows),
            "bytes": sum(r["bytes"] for r in rows), "wall_s": sum(r["wall_s"] or 0 for r in rows)}
    for k in ("n_calls", "bytes", "wall_s"):
        v = [r[k] or 0 for r in rows]
        summ[f"{k}_median"], summ[f"{k}_max"] = (statistics.median(v), max(v)) if v else (None, None)
    for k in KEYS:
        v = [r["usage"][k] for r in rows]
        summ[f"{k}_median"], summ[f"{k}_max"] = (statistics.median(v), max(v)) if v else (None, None)
    if PROBLEMS:
        summ["not_read"] = list(PROBLEMS)
        for x in PROBLEMS:
            print(f"analyze: {x}")
    (d / "analysis.json").write_text(json.dumps({"summary": summ, "turns": rows}, indent=1))
    print(json.dumps(summ))
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print(__doc__)
        sys.exit(0 if len(sys.argv) > 1 else 2)
    sys.exit(main(sys.argv[1]))
