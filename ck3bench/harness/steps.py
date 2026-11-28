"""python -m ck3bench.harness.steps TRAJECTORY.jsonl[.gz] [--json OUT]: one line per env step, read from the env's
own records.

Each line also says the game-days its step ran (`days=`, ck3bench's step_days: a wake task's `wake.days`, never
cycle_days) and, on a wake task, why the step stopped (`stop=`, with what fired), the ruler id, the open wars and
the step's call count.

Per step k: the calls of turn k (made at the date step k-1 ended on), then the date step k ended on, the de jure counties (the env's metric), gold / prestige / piety (the
seat's purse news: the value at the step's end and its change), the step's news (claims, wars, titles,
fabrication), and the seat's calls of that step (tool, the args that matter, refused gate). Nothing is
estimated: a missing purse prints "-".
"""
import gzip
import json
import sys
from pathlib import Path

from ck3bench.env.trajectory import step_days  # the one rule for a step's length; no copy here


def load(p: str):
    op = gzip.open if p.endswith(".gz") else open
    with op(p, "rt", encoding="utf-8") as f:
        return [json.loads(x) for x in f if x.strip()]


def short_args(tool, a):
    a = dict(a or {})
    if tool == "end_turn":
        return ""
    keep = {k: v for k, v in a.items() if k not in ("summary",)}
    s = json.dumps(keep, separators=(",", ":"), ensure_ascii=False)
    return s if len(s) <= 160 else s[:157] + "..."


def refused(r):
    env = r.get("result") or {}
    if env.get("ok") is not False:
        return None
    b = env.get("blocked") or (env.get("result") or {}).get("blocked") or {}
    g = b.get("gate") or env.get("code") or str(env.get("error"))[:60]
    return f"{g}/{b['raw_reason']}" if b.get("raw_reason") else g


def main() -> int:
    recs = load(sys.argv[1])
    head = next(r for r in recs if r["kind"] == "header")
    seats = list(head.get("seats") or {})
    if len(seats) != 1:
        raise SystemExit(f"one seat expected in the trajectory header, found {seats}")  # any one seat
    seat = seats[0]
    calls, rows, openw = {}, [], set()
    for r in recs:
        if r["kind"] == "call":
            c = r["call"]
            calls.setdefault(r["step"], []).append(
                {"tool": c["tool_name"], "args": short_args(c["tool_name"], c.get("arguments")), "refused": refused(r)})
    for r in recs:
        if r["kind"] != "step":
            continue
        o = r["observations"][seat]
        n = (o.get("world") or {}).get("news") or {}
        pu = n.get("purse") or {}
        val = lambda k: (pu.get(k) or {}).get("now")
        chg = lambda k: (pu.get(k) or {}).get("change")
        ev = []
        for c in n.get("claims_gained") or []:
            ev.append(f"claim+ {c.get('title_key') or c.get('title_name')}")
        for c in n.get("claims_lost") or []:
            ev.append(f"claim- {c.get('title_key') or c.get('title_name')}")
        for w in n.get("wars_started") or []:
            ev.append(f"war+ {w.get('name')}")
        for w in n.get("wars_ended") or []:
            ev.append(f"war- {w.get('name')} ({w.get('outcome')})")
        for t in n.get("titles_gained") or []:
            if str(t.get("key", "")).startswith(("c_", "d_", "k_")):
                ev.append(f"title+ {t.get('key')} ({t.get('how')})")
        for t in n.get("titles_lost") or []:
            if str(t.get("key", "")).startswith(("c_", "d_", "k_")):
                ev.append(f"title- {t.get('key')}")
        fab = n.get("fabrication") or {}
        for w in n.get("wars_started") or []:
            openw.add(w.get("war_id"))
        for w in n.get("wars_ended") or []:
            openw.discard(w.get("war_id"))
        w = r.get("wake") or {}
        stop = (w.get("stop") + ("(" + ",".join(f.get("condition", {}).get("event") or f.get("condition", {}).get("metric")
                                                for f in w.get("fired") or []) + ")" if w.get("fired") else "")
                ) if w else None
        rows.append({"step": r["step"], "date": r["game_date"], "days": step_days(head, r), "stop": stop,
                     "ruler": o.get("character_id"), "wars_open": len(openw), "counties": (o.get("metrics") or {}).get("dejure_counties"),
                     "reward": (r.get("rewards") or {}).get(seat), "gold": val("gold"), "d_gold": chg("gold"),
                     "prestige": val("prestige"), "d_prestige": chg("prestige"), "piety": val("piety"),
                     "d_piety": chg("piety"), "news": ev,
                     "fab": ((fab.get("target") if isinstance(fab.get("target"), dict) else {"title_key": fab.get("target")}) or {}).get("title_key") if fab.get("running") else None,
                     "calls": calls.get(r["step"], [])})
    f = lambda x: "-" if x is None else f"{x:.1f}"
    for x in rows:
        cs = [c for c in x["calls"] if c["tool"] != "end_turn"]
        print(f"{x['step']:>3} {x['date']} days={x['days']}" + (f" stop={x['stop']}" if x["stop"] else "") +
              f" ruler={x['ruler']} wars={x['wars_open']} n={len([c for c in x['calls'] if c['tool'] != 'end_turn'])} c={x['counties']} r={x['reward']} gold={f(x['gold'])}({f(x['d_gold'])}) "
              f"pres={f(x['prestige'])}({f(x['d_prestige'])}) piety={f(x['piety'])} fab={x['fab']} "
              f"{'; '.join(x['news'])}")
        for c in cs:
            print(f"      {c['tool']} {c['args']}" + (f"  REFUSED {c['refused']}" if c["refused"] else ""))
    end = next((r for r in recs if r["kind"] == "end"), None)
    if end:
        print("END", json.dumps({k: end.get(k) for k in ("reason", "steps", "returns")}))
    if "--json" in sys.argv:
        Path(sys.argv[sys.argv.index("--json") + 1]).write_text(json.dumps(rows, indent=1))
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print(__doc__)
        sys.exit(0 if len(sys.argv) > 1 else 2)
    sys.exit(main())
