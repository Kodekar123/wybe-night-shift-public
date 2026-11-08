#!/usr/bin/env python3
"""Walk the hotel from the robot's location and save what each place contains to logs/map.json."""
import json, sys
from pathlib import Path
from ns import call

LIMIT = int(sys.argv[1]) if len(sys.argv) > 1 else 400
OUT = Path(__file__).parent / "logs" / "map.json"
used = 0


def cmd(name, **args):
    global used
    used += 1
    r = json.loads(call({"cmd": name, "args": args})[1])
    for e in r.get("events") or []:
        print(r.get("clock"), "EVENT", json.dumps(e), flush=True)
    if r.get("note"):
        print(r.get("clock"), "NOTE", r["note"], flush=True)
    return r


places, blocked = {}, {}


def go(to):
    r = cmd("move", to=to)
    if not r["ok"]:
        blocked[to] = r["result"]
        print("BLOCKED", to, r["result"], flush=True)
    return r["ok"]


def walk(here):
    r = cmd("look")
    places[here] = r["result"]
    OUT.write_text(json.dumps({"places": places, "blocked": blocked}, indent=1))
    for ex in r["result"]["exits"]:
        to = ex["to"]
        if to in places or to in blocked or used >= LIMIT:
            continue
        if go(to):
            walk(to)
            if not go(here):
                print("STUCK returning to", here, flush=True)
                return


start = cmd("status")["result"]["location"]
walk(start)
print("places", len(places), "blocked", len(blocked), "calls", used)
print(json.dumps(cmd("status")["result"]))
print(json.dumps(cmd("inbox")["result"]))
