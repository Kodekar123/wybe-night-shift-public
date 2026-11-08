#!/usr/bin/env python3
"""Shared helpers: send a command, print it, and route across logs/map.json."""
import heapq, json, sys
from pathlib import Path
from ns import call

MAP = json.loads((Path(__file__).parent / "logs" / "map.json").read_text())["places"]


raise_on_over = False


class ShiftOver(Exception):
    pass


def cmd(name, quiet=False, **args):
    r = json.loads(call({"cmd": name, "args": args})[1])
    if not quiet:
        print(f'{r.get("clock")} {name} {json.dumps(args)} -> ok={r.get("ok")} {json.dumps(r.get("result"))[:1800]}', flush=True)
    for e in r.get("events") or []:
        print(r.get("clock"), "EVENT", json.dumps(e), flush=True)
    if r.get("note"):
        print(r.get("clock"), "NOTE", r["note"], flush=True)
    if raise_on_over and isinstance(r.get("result"), dict) and r["result"].get("shift_over"):
        raise ShiftOver(json.dumps(r["result"]))
    return r


def route(a, b):
    seen, q = set(), [(0, a, [])]
    while q:
        d, n, path = heapq.heappop(q)
        if n == b:
            return path
        if n in seen:
            continue
        seen.add(n)
        for e in MAP.get(n, {}).get("exits", []):
            heapq.heappush(q, (d + e["metres"], e["to"], path + [e["to"]]))
    raise ValueError(f"no route {a} -> {b}")


def goto(target):
    here = cmd("status", quiet=True)["result"]["location"]
    for step in route(here, target):
        r = cmd("move", quiet=True, to=step)
        if not r["ok"]:
            print("MOVE FAILED", step, r["result"], flush=True)
            return False
    print("at", target, flush=True)
    return True


if __name__ == "__main__":
    # usage: bot.py goto <place> | bot.py <cmd> '<args json>'
    if sys.argv[1] == "goto":
        goto(sys.argv[2])
    else:
        cmd(sys.argv[1], **json.loads(sys.argv[2] if len(sys.argv) > 2 else "{}"))
