#!/usr/bin/env python3
"""Skim seeds: play each night to 06:00 doing nothing but letting time pass, and record every message. Cheap: scans
drain the battery once, then each dock lasts until the next message. Score stays 0 (nothing is reported).

usage: skim.py <first> <last> [step]     (never seeds 900-999: those are kept unseen)
Writes logs/skim/<seed>.json: the messages, with arrival times.
"""
import json, os, sys, time
from pathlib import Path
import bot, ns
from bot import ShiftOver, cmd

OUT = Path(os.environ.get("SKIM_OUT", "logs/skim"))


def visit_far():
    """Walk once from the dock up the stairs to a top-floor corridor, so navigate can go there later. Uses only exits
    the server shows. Returns the place, or None (then scans are used instead)."""
    seen = set()
    for _ in range(20):
        v = cmd("look", quiet=True)["result"]
        here, floor = v["location"], v["floor"]
        seen.add(here)
        if here.startswith("corridor") and floor >= 2:
            # keep going: the far end of the top floor makes each navigate there and back as long as possible
            tail = [e for e in (x["to"] for x in v.get("exits", [])) if e.startswith(("corridor", "stairwell"))
                    and e not in seen and not e.startswith("room-")]
            if not tail:
                return here
            ups = [e for e in tail if e.startswith("stairwell")]
            if not cmd("move", quiet=True, to=(ups or tail)[0]).get("ok"):
                return here
            continue
        seen.add(here)
        exits = [e["to"] for e in v.get("exits", []) if not e.get("blocked") and not e["to"].startswith("room-")
                 and e["to"] not in seen]
        up = [e for e in exits if e.startswith("stairwell") and e[-1:] > here[-1:]] if here.startswith("stairwell") else []
        stairs = [e for e in exits if e.startswith("stairwell")]
        corridors = [e for e in exits if e.startswith("corridor")]
        nxt = (up or ([] if here.startswith("stairwell") and floor < 3 else []) or stairs or corridors or exits or [None])[0]
        if here.startswith("stairwell") and floor >= 3:
            nxt = (corridors or exits or [None])[0]
        if not nxt or not cmd("move", quiet=True, to=nxt).get("ok"):
            return None
    return None


def skim(seed):
    os.environ.pop("NS_SHIFT", None)
    r = cmd("start_shift", quiet=True, mode="practice", seed=seed)
    if not r.get("ok"):
        return str(r.get("result"))
    shift = r["result"]["shift"]
    os.environ["NS_SHIFT"] = shift
    msgs, calls = {}, 0
    try:
        bot.raise_on_over = True
        far = visit_far()  # a distant place: one navigate there and back drains the battery like 3+ scans
        calls += 12
        cmd("navigate", quiet=True, to="charging-dock")  # start the loop at the dock, whatever happened above
        calls += 1
        here_far, wakes = False, 0
        while calls < 900:
            r = cmd("dock", quiet=True) if not here_far else {"ok": False}
            calls += 1
            if not r.get("ok") and "No dock here" in str(r.get("result")):  # never strand away from the dock
                cmd("navigate", quiet=True, to="charging-dock")
                here_far = False
                calls += 1
                continue
            if not r.get("ok"):  # full: a long walk passes minutes and makes room to dock again
                if far:
                    r = cmd("navigate", quiet=True, to="charging-dock" if here_far else far)
                    here_far = not here_far and r.get("ok", False)
                else:
                    r = cmd("scan", quiet=True)
                calls += 1
            # messages wait unread in the inbox: read them every 10 wake-ups and near the end, not after each one
            if any(e.get("type") == "inbox" for e in r.get("events") or []) or "message" in str(r.get("result")):
                wakes += 1
                if wakes % 10 == 0 or (r.get("clock") or "") >= "05:40" and (r.get("clock") or "") < "12":
                    for m in cmd("inbox", quiet=True)["result"]["messages"]:
                        msgs[(m["at"], m["text"])] = m
                    calls += 1
    except ShiftOver:
        pass
    finally:
        bot.raise_on_over = False
        try:
            for m in cmd("inbox", quiet=True)["result"]["messages"]:
                msgs[(m["at"], m["text"])] = m
        except Exception:
            pass
        cmd("end_shift", quiet=True)
    OUT.mkdir(parents=True, exist_ok=True)
    rows = sorted(msgs.values(), key=lambda m: m["at"] if m["at"] >= "22" else "~" + m["at"])
    (OUT / f"{seed}.json").write_text(json.dumps({"seed": seed, "shift": shift, "calls": calls, "messages": rows}))
    return f"{len(rows)} messages, {calls} calls"


if __name__ == "__main__":
    first, last = int(sys.argv[1]), int(sys.argv[2])
    step = int(sys.argv[3]) if len(sys.argv) > 3 else 1
    for seed in range(first, last + 1, step):
        if 900 <= seed <= 999 or (OUT / f"{seed}.json").exists():
            continue
        for attempt in range(30):
            res = skim(seed)
            if "already running" not in res:
                break
            time.sleep(60)
        print(time.strftime("%H:%M"), seed, res, flush=True)
