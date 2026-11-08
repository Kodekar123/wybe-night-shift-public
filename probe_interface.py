#!/usr/bin/env python3
"""One practice shift to look for interface we haven't found: verbs sent directly (help doesn't list hidden ones),
every readable object, and argument variants of known commands. Prints only what differs from 'unknown command'.
Notes are read and recorded, never followed. Usage: probe_interface.py <seed>"""
import json, os, sys
import bot
from bot import cmd
from worker import Worker

VERBS = """wait sleep rest idle pause hold stay listen watch search find fetch get take grab bring carry deliver give
hand serve return collect gather load unload stock restock refill open close unlock lock enter exit leave go walk run
sprint hurry rush ride call summon page radio phone message notify broadcast announce tell ask_manager ack acknowledge
accept claim assign queue plan route map where locate track check inspect clean mop wipe sign tidy fix repair
charge recharge plug unplug sync reset restart time clock schedule tasks jobs requests list todo history score
undo cancel skip delegate request deliver_all batch multi""".split()


def show(label, r):
    res = r.get("result")
    text = json.dumps(res)[:400] if not isinstance(res, str) else res[:400]
    if isinstance(res, str) and "doesn't know" in res:
        return
    print(f"{label} -> ok={r.get('ok')} {text}", flush=True)


def main(seed):
    w = Worker()
    bot.raise_on_over = False
    os.environ.pop("NS_SHIFT", None)
    r = cmd("start_shift", quiet=True, mode="practice", seed=seed)
    if not r.get("ok"):
        print("START", json.dumps(r.get("result")), flush=True)  # e.g. "...already running..." makes the launcher retry
        sys.exit(1)
    os.environ["NS_SHIFT"] = r["result"]["shift"]
    print("SHIFT", r["result"]["shift"], flush=True)
    h = w.h
    try:
        print("== verbs sent directly (only non-unknown answers shown)")
        for v in VERBS:
            show(v, h.do(v))
        print("== help without topic and for some verbs")
        show("help", h.do("help"))
        for t in ("commands", "hidden", "elevator", "cart", "dock", "say", "ask", "reply", "navigate"):
            show(f"help {t}", h.do("help", topic=t))
        h.survey()
        h.status()
        print("== readable objects (recorded, never followed)")
        for place, objs in sorted(h.objects.items()):
            for o in objs:
                if o.startswith(("luggage", "tray", "towel", "pillow", "water", "phone", "toiletry", "cart")):
                    continue
                h.walk(place)
                show(f"read {o} @ {place}", h.do("read", object=o))
        print("== argument variants")
        desk = h.place("desk")
        h.walk(desk)
        show("ask manager", h.do("ask", person="night manager", question="Is there anything else that needs doing?"))
        show("ask clerk tasks", h.do("ask", person="clerk", question="Is there anything that needs doing tonight?"))
        show("ask clerk tips", h.do("ask", person="clerk", question="How can I work faster tonight?"))
        lobby = next((p for p in h.graph if p == "lobby"), desk)
        h.walk(lobby)
        show("call_elevator from lobby", h.do("call_elevator", floor=3))
        lift = next(p for p in h.graph if p.startswith("elevator-") and h.floor.get(p) == 1)
        upper = [p for p in h.graph if p.startswith("corridor") and h.floor.get(p, 1) > 1]
        if upper:
            show("navigate to upper floor", h.do("navigate", to=upper[0]))
            h.status()
            h.walk(lift if h.floor.get(h.here) == 1 else h.here)
        stock = next((p for p, o in h.objects.items() if "towel" in o), None)
        if stock:
            h.walk(stock)
            show("pick count 2", h.do("pick", item="towel", count=2))
            show("pick items list", h.do("pick", items=["towel", "pillow"]))
            show("pick 2 towels", h.do("pick", item="2 towels"))
        show("say to room", h.do("say", text="Good evening", to="room-101"))
        show("status", h.do("status"))
        show("inbox all", h.do("inbox", all=True))
    finally:
        cmd("end_shift", quiet=True)
        print("ENDED", flush=True)


if __name__ == "__main__":
    main(int(sys.argv[1]))
