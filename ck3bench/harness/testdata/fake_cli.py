"""A stand-in for the agent CLI, for the harness's own tests and dry runs: NO MODEL. It takes the same flags
the runner gives the real session, speaks the same stream-json on stdin and
stdout, starts the MCP server named in --mcp-config with the `mcp` library's real
client, and plays each turn by a script: seat_observation, observe (when the
seat has it), end_turn. Its events have the shapes the runner reads (system/init,
assistant messages with tool_use blocks and usage, result); the usage numbers are
made up and say so (`"fake": true`).

FAKE_CLAUDE_READS=1 adds the reads a seat would make each turn (inspect title k_ireland, cbs on
Athlone, plan_war), each reply's size in the event's `bytes`. FAKE_CLAUDE_MODE picks a failure to prove the watchdog: normal | stop_at:N (from
turn N it never calls end_turn) | rate_at:N (turns N and N+1 answer a usage
limit) | exit_at:N (the process exits at turn N) | extra_tool (init lists a
built-in tool beside the shim's) | one_reply:N (every turn in ONE reply, N/10 s apart).

FRESH sessions (the runner's turns; the shim's end_turn lists `notes`): one turn per process. It
reads the turn number and the notes pad from the first message, calls observe (when listed) and end_turn with
`notes` = "fake turn K; prev: <the pad it was shown>" (and FAKE_CLAUDE_WAKE, a JSON wake request, or a list
of them cycled by turn, when set),
emitting `notes_seen` (the pad it read; `note_seen`: a NOTE line above it) on its end_turn event. Modes there:
notes_over:N (turn N first sends notes over the pad's bound, then short ones; the first ends the turn) | after_end:N (turn N calls observe after
end_turn and reads the refusal) | stop_at:N (from turn N no end_turn) | exit_at:N | notes_edit:0 (--notes-edit:
from turn 2 the same pad sent as `notes_edit`, a replace of its line 1). A pad shown numbered (`n|`) is read
without its numbers.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import sys


#: with FAKE_CLAUDE_READS=1, the per-turn reads, with the arguments a seat of the Ireland task would pass
READ_ARGS = {"inspect": {"subject": "title", "title_key": "k_ireland"}, "cbs": {"target_id": 29585},
             "plan_war": {}}


def emit(ev: dict) -> None:
    sys.stdout.write(json.dumps(ev) + "\n"); sys.stdout.flush()


def arg(name: str):
    a = sys.argv
    return a[a.index(name) + 1] if name in a else None


async def main() -> None:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    cfg = json.loads(open(arg("--mcp-config")).read())["mcpServers"]
    (name, srv), = cfg.items()
    mode = os.environ.get("FAKE_CLAUDE_MODE", "normal")
    kind, _, n = mode.partition(":")
    n = int(n) if n else 0
    p = StdioServerParameters(command=srv["command"], args=srv["args"], env={**os.environ, **srv.get("env", {})})
    loop = asyncio.get_running_loop()
    async with stdio_client(p) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            listed = (await s.list_tools()).tools
            tools = [t.name for t in listed]
            fresh = any(t.name == "end_turn" and "notes" in ((t.inputSchema or {}).get("properties") or {})
                        for t in listed)
            if fresh:
                return await fresh_session(s, name, tools, kind, n, loop)
            turn = 0
            while True:
                line = await loop.run_in_executor(None, sys.stdin.readline)
                if not line:
                    return
                msg = json.loads(line)
                if msg.get("type") != "user":
                    continue
                turn += 1
                if turn == 1:
                    listed = [f"mcp__{name}__{t}" for t in tools] + (["Bash"] if kind == "extra_tool" else [])
                    emit({"type": "system", "subtype": "init", "tools": listed, "model": "fake-no-model",
                          "mcp_servers": [{"name": name, "status": "connected"}], "fake": True})
                if kind == "exit_at" and turn == n:
                    os._exit(3)
                if kind == "one_reply":            # plays every turn in this one reply, n/10 s apart
                    while True:
                        await asyncio.sleep(n / 10 if n else 0)
                        res = await s.call_tool("end_turn", {"summary": "fake, one reply"})
                        emit({"type": "assistant", "fake": True, "message": {
                            "id": f"fake-r-{turn}", "role": "assistant", "usage": {"output_tokens": 1},
                            "content": [{"type": "tool_use", "id": f"tu-r-{turn}", "name": f"mcp__{name}__end_turn",
                                         "input": {}}]}})
                        txt = "\n".join(c.text for c in res.content if getattr(c, "type", "") == "text")
                        turn += 1
                        if res.isError or '"episode_over": true' in txt or '"done": true' in txt:
                            break
                    emit({"type": "result", "subtype": "success", "is_error": False, "fake": True,
                          "result": "fake: all turns in one reply", "duration_ms": 1})
                    continue
                if kind == "rate_at" and turn in (n, n + 1):
                    emit({"type": "result", "subtype": "error", "is_error": True, "fake": True,
                          "result": "Claude AI usage limit reached (fake)", "duration_ms": 10})
                    continue
                plan = ["seat_observation"] + (["observe"] if "observe" in tools else [])
                if os.environ.get("FAKE_CLAUDE_READS"):     # the reads a seat would make each turn
                    plan += [t for t in ("inspect", "cbs", "plan_war") if t in tools]
                if not (kind == "stop_at" and turn >= n):
                    plan.append("end_turn")
                for i, tool in enumerate(plan):
                    args = {"summary": f"fake turn {turn}"} if tool == "end_turn" else \
                        READ_ARGS.get(tool, {})
                    emit({"type": "assistant", "fake": True, "message": {
                        "id": f"fake-{turn}-{i}", "role": "assistant",
                        "content": [{"type": "tool_use", "id": f"tu-{turn}-{i}", "name": f"mcp__{name}__{tool}",
                                     "input": args}],
                        "usage": {"input_tokens": 1000, "output_tokens": 50, "cache_read_input_tokens": 0,
                                  "cache_creation_input_tokens": 0}}})
                    res = await s.call_tool(tool, args)
                    text = "\n".join(c.text for c in res.content if getattr(c, "type", "") == "text")
                    emit({"type": "user", "fake": True, "tool": tool, "bytes": len(text.encode()),
                          "message": {"role": "user", "content": [
                              {"type": "tool_result", "tool_use_id": f"tu-{turn}-{i}", "is_error": res.isError,
                               "content": text[:2000]}]}})
                emit({"type": "result", "subtype": "success", "is_error": False, "fake": True,
                      "result": f"fake turn {turn} done", "num_turns": len(plan), "duration_ms": 1,
                      "usage": {"input_tokens": 1000 * len(plan), "output_tokens": 50 * len(plan)}})


async def fresh_session(s, name: str, tools: list, kind: str, n: int, loop) -> None:
    """One turn per session (a new session every turn)."""
    first, seq = True, 0
    while True:
        line = await loop.run_in_executor(None, sys.stdin.readline)
        if not line:
            return
        msg = json.loads(line)
        if msg.get("type") != "user":
            continue
        text = msg["message"]["content"]
        text = text if isinstance(text, str) else json.dumps(text)
        m = re.search(r"Turn (\d+):", text)
        k = int(m.group(1)) if m else 0
        nm = re.search(r"NOTE: [^\n]*", text)            # a turn's notes left unwritten, said in the next turn
        pm = re.search(r"<<<\n(.*?)\n>>>", text, re.S)
        prev = pm.group(1) if pm else None
        if prev is not None and "n| numbers each line" in text:     # --notes-edit: the pad shown numbered
            prev = "\n".join(re.sub(r"^\d+\|", "", ln) for ln in prev.split("\n"))
        if first:
            emit({"type": "system", "subtype": "init", "tools": [f"mcp__{name}__{t}" for t in tools],
                  "model": "fake-no-model", "mcp_servers": [{"name": name, "status": "connected"}], "fake": True})
            first = False
            if kind == "exit_at" and k == n:
                os._exit(3)
        plan: list = [("observe", {})] if "observe" in tools and m else []
        notes = f"fake turn {k}; prev: {prev}"[:200]
        wake = json.loads(os.environ["FAKE_CLAUDE_WAKE"]) if os.environ.get("FAKE_CLAUDE_WAKE") else None
        if isinstance(wake, list):           # a list: turn k sends request (k-1) mod its length (a scripted mix)
            wake = wake[(k - 1) % len(wake)] if wake else None
        end = {"summary": f"fake turn {k}", "notes": notes, **({"wake": wake} if wake else {})}
        if kind == "notes_edit" and prev:      # --notes-edit: the same pad, sent as a patch of its one line
            end = {"summary": end["summary"], "notes_edit": [{"op": "replace", "line": 1, "text": notes}],
                   **({"wake": wake} if wake else {})}
        if not (kind == "stop_at" and k >= n):
            if kind == "notes_over" and k == n and m:
                plan.append(("end_turn", dict(end, notes="x" * 5000)))
            plan.append(("end_turn", end))
            if kind == "after_end" and k == n:
                plan.append(("observe", {}))
        for tool, args in plan:
            seq += 1
            res = await s.call_tool(tool, args)
            body = "\n".join(c.text for c in res.content if getattr(c, "type", "") == "text")
            emit({"type": "assistant", "fake": True, **({"notes_seen": prev} if tool == "end_turn" else {}),
                  **({"note_seen": nm.group(0)} if tool == "end_turn" and nm else {}),
                  "message": {"id": f"fake-{k}-{seq}", "role": "assistant",
                              "content": [{"type": "tool_use", "id": f"tu-{k}-{seq}", "name": f"mcp__{name}__{tool}",
                                           "input": args}],
                              "usage": {"input_tokens": 1000, "output_tokens": 50, "cache_read_input_tokens": 0,
                                        "cache_creation_input_tokens": 0}}})
            emit({"type": "user", "fake": True, "tool": tool, "bytes": len(body.encode()),
                  "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": f"tu-{k}-{seq}",
                                                           "is_error": res.isError, "content": body[:2000]}]}})
        emit({"type": "result", "subtype": "success", "is_error": False, "fake": True,
              "result": f"fake fresh turn {k} done", "num_turns": len(plan), "duration_ms": 1,
              "usage": {"input_tokens": 1000 * max(1, len(plan)), "output_tokens": 50 * max(1, len(plan))}})


if __name__ == "__main__":
    asyncio.run(main())
