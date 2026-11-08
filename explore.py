#!/usr/bin/env python3
"""Practice-only experiments, one stage at a time. A stage runs only once the gate before it has passed."""
import heapq, json, os, re, sys
from contextlib import contextmanager
from pathlib import Path
import bot, gates, ns
from bot import MAP, ShiftOver, cmd, goto, route

FINDINGS = Path(__file__).parent / "logs" / "findings.jsonl"


def log_lines():
    return sum(1 for _ in ns.LOG.open())


def finding(stage, key, value, start, answered=True):
    """Later entries for a key replace earlier ones. answered=False keeps the evidence but leaves the gate open."""
    entry = {"stage": stage, "key": key, "value": value, "from": start + 1, "to": log_lines(), "answered": answered}
    with FINDINGS.open("a") as f:
        f.write(json.dumps(entry) + "\n")
    print("FINDING", key, json.dumps(value)[:300], flush=True)


def seconds(clock):
    h, m, s = map(int, clock.split(":"))
    return ((h + 2) % 24) * 3600 + m * 60 + s  # 22:00 counts as 0


def stat():
    r = cmd("status", quiet=True)
    return r["clock"], r["result"]


def measure(name, fail=False, **args):
    """Run one command and return its reply with the time, battery and score it cost."""
    c0, s0 = stat()
    ns.expect_fail = fail
    try:
        r = cmd(name, **args)
    finally:
        ns.expect_fail = False
    if (r.get("result") or {}) and isinstance(r["result"], dict) and r["result"].get("shift_over"):
        return r, {"seconds": None, "battery": None, "score": None}
    c1, s1 = stat()
    return r, {"seconds": seconds(c1) - seconds(c0), "battery": round(s1["battery"] - s0["battery"], 2),
               "score": s1["score"] - s0["score"]}


@contextmanager
def shift(seed):
    """Start a practice shift on a seed and always end it, even if the experiment fails."""
    os.environ.pop("NS_SHIFT", None)
    r = cmd("start_shift", mode="practice", seed=seed)
    os.environ["NS_SHIFT"] = r["result"]["shift"]
    try:
        yield
    finally:
        try:
            if not ns.STOP.exists() and os.environ.get("NS_SHIFT"):
                ns.expect_fail = True
                cmd("end_shift")
        finally:
            ns.expect_fail = False
            os.environ.pop("NS_SHIFT", None)


def command_finding(name, start, *pairs):
    finding(1, f"command:{name}", [{"ok": r.get("ok"), "result": str(r.get("result"))[:200], "cost": c}
                                   for r, c in pairs], start)


def stage1():
    # team-level commands need no shift
    for name, args in [("help", {}), ("whoami", {}), ("shifts", {}), ("leaderboard", {})]:
        s = log_lines()
        r = cmd(name, quiet=True, **args)
        finding(1, f"command:{name}", {"ok": r["ok"], "cost": "no shift needed"}, s)
    s = log_lines()
    with shift(11):
        finding(1, "command:start_shift", {"seed": 11, "shift_started": True}, s)
        s = log_lines(); c = stat(); finding(1, "command:status", {"cost": "free", "sample": c[1]}, s)
        s = log_lines(); command_finding("inbox", s, measure("inbox"))
        s = log_lines(); command_finding("look", s, measure("look"), measure("look", at="sticky-note"),
                                         measure("look", fail=True, at="unicorn"))
        s = log_lines(); command_finding("read", s, measure("read", object="sticky-note"),
                                         measure("read", fail=True, object="unicorn"))
        s = log_lines(); a = measure("dock"); b = measure("say", text="Good evening"); command_finding("dock", s, a)
        s = log_lines(); command_finding("say", s, b, measure("say", fail=True))
        s = log_lines(); command_finding("move", s, measure("move", to="lobby"),
                                         measure("move", fail=True, to="storage"))
        s = log_lines(); command_finding("navigate", s, measure("navigate", to="charging-dock"),
                                         measure("navigate", fail=True, to="storage"))
        goto("storage")
        s = log_lines(); command_finding("scan", s, measure("scan"))
        s = log_lines(); p = measure("pick", item="towel"); bad = measure("pick", fail=True, item="unicorn")
        command_finding("pick", s, p, bad)
        s = log_lines(); command_finding("drop", s, measure("drop", item="towel"),
                                         measure("drop", fail=True, item="towel"))
        measure("pick", item="towel")
        goto("corridor-1a")
        s = log_lines(); k = measure("knock", door="room-101"); command_finding("knock", s, k)
        s = log_lines(); command_finding("place", s, measure("place", item="towel", where="corridor-1a"),
                                         measure("place", fail=True, item="towel", where="room-101"))
        goto("front-desk")
        s = log_lines(); command_finding("ask", s, measure("ask", person="clerk", question="Who is in room 101?"),
                                         measure("ask", fail=True, person="nobody", question="Hello?"))
        s = log_lines(); u = measure("use", fail=True, item="unicorn"); measure("pick", item="keycard")
        k = measure("use", item="keycard"); command_finding("use", s, u, k)
        goto("elevator-1")
        s = log_lines(); command_finding("call_elevator", s, measure("call_elevator", floor=2),
                                         measure("call_elevator", fail=True, floor=9))
        cmd("inbox")
        s = log_lines(); command_finding("reply", s, measure("reply", request="R-001", text="Which room, please?"),
                                         measure("reply", fail=True, request="R-999", text="Hello?"))
        s = log_lines(); command_finding("report", s, measure("report", fail=True, request="R-999", outcome="failed",
                                                              note="Testing an unknown id."))
        s = log_lines(); r, c = measure("end_shift")
        command_finding("end_shift", s, (r, c))
        os.environ.pop("NS_SHIFT", None)


class World:
    """This night's map, learned from look. Locked rooms are never walked into."""

    def __init__(self):
        self.graph, self.places = {}, {}

    def see(self):
        r = cmd("look", quiet=True)["result"]
        self.places[r["location"]] = r
        self.graph[r["location"]] = {e["to"]: e["metres"] for e in r["exits"]}
        return r

    def survey(self, skip=("room-",)):
        here = self.see()
        for e in here["exits"]:
            if e["to"] in self.graph or e["to"].startswith(skip):
                continue
            if cmd("move", quiet=True, to=e["to"])["ok"]:
                self.survey(skip)
                cmd("move", quiet=True, to=here["location"])

    def walk(self, target):
        here = stat()[1]["location"]
        seen, q = set(), [(0, here, [])]
        while q:
            d, n, path = heapq.heappop(q)
            if n == target:
                for step in path:
                    r = cmd("move", quiet=True, to=step)
                    if not r["ok"]:
                        print("WALK FAILED", step, r["result"], flush=True)
                        if "Closed off" in str(r["result"]):  # go the other way round
                            at = stat()[1]["location"]
                            self.graph.get(at, {}).pop(step, None)
                            self.graph.get(step, {}).pop(at, None)
                            return self.walk(target)
                        return False
                return True
            if n not in seen:
                seen.add(n)
                for to, m in self.graph.get(n, {}).items():
                    heapq.heappush(q, (d + m, to, path + [to]))
        print("NO ROUTE", here, target, flush=True)
        return False

    def corridor_of(self, room):
        return next(n for n, exits in self.graph.items() if room in exits)

    def has(self, thing):
        return next(n for n, p in self.places.items() if any(o.split(" x")[0] == thing for o in p["objects"]))


def stage2():
    with shift(12):
        w = World()
        s = log_lines()
        w.survey()
        print("MAP", {n: p["objects"] for n, p in w.places.items() if p["objects"]},
              {n: p["people"] for n, p in w.places.items() if p["people"]}, flush=True)
        print("ROOMS", sorted(r for ex in w.graph.values() for r in ex if r.startswith("room-")), flush=True)
        finding(2, "survey", {"places": len(w.places)}, s)

        s = log_lines()
        spots = [p for p in w.graph if p.startswith(("lobby-bar", "kitchen", "corridor-1", "laundry"))][:3]
        out = {}
        for spot in spots:
            w.walk(spot)
            r, c = measure("scan")
            out[spot] = {"result": r["result"], "cost": c}
        finding(2, "scan", out, s)

        s = log_lines()
        a, b = next((x, y) for x, ex in w.graph.items() for y, m in ex.items()
                    if m >= 12 and x.startswith("corridor") and y.startswith("corridor"))
        metres = w.graph[a][b]
        w.walk(a)
        _, mv = measure("move", to=b)
        _, nav = measure("navigate", to=a)
        finding(2, "move_vs_navigate", {"metres": metres, "move": mv, "navigate": nav}, s)

        s = log_lines()
        w.walk(w.has("towel"))
        picks = [measure("pick", fail=True, item="towel") for _ in range(4)]
        held = stat()[1]["hands"]
        for r, _ in picks:
            print(r["result"], flush=True)
        finding(2, "carry_limit", {"hands": held, "last": picks[-1][0]["result"]}, s)

        s = log_lines()
        w.walk(w.has("cart"))
        attempts = [measure("pick", fail=True, item="cart"), measure("use", fail=True, item="cart")]
        for t in list(held):
            measure("drop", item=t)
        attempts += [measure("pick", fail=True, item="cart"), measure("use", fail=True, item="cart")]
        print("STATUS WITH CART", stat()[1], flush=True)
        w.walk(w.has("towel"))
        loads = [measure("pick", fail=True, item="towel") for _ in range(9)]
        print("LOADS", [r["result"] for r, _ in loads], "STATUS", stat()[1], flush=True)
        corridor = next(iter(w.graph[stat()[1]["location"]]))
        _, cart_move = measure("move", to=corridor)
        w.walk(next(n for n in w.graph if n.startswith("stairwell")))
        up = next(n for n in w.graph[stat()[1]["location"]] if n.startswith("stairwell"))
        stair_cart = measure("move", fail=True, to=up)
        finding(2, "cart", {"attempts": [str(r["result"])[:120] for r, _ in attempts], "loads": sum(r["ok"] for r, _ in loads),
                            "status": stat()[1], "move": cart_move, "stairs": [str(stair_cart[0]["result"]), stair_cart[1]]}, s)

        s = log_lines()
        here = stat()[1]
        if here["cart"]:
            measure("drop", item="cart")
        w.walk(next(n for n in w.graph if n.startswith("stairwell") and n.endswith("-1")))
        stairs_up = measure("move", fail=True, to="stairwell-2")
        print("STAIRS", stairs_up, flush=True)
        w.walk("elevator-1") if stairs_up[0]["ok"] is False else None
        lift = None
        if stat()[1]["location"] == "stairwell-2":
            lift = "tested from floor 2"
            w.walk(next(n for n in w.graph if n.startswith("elevator-2")))
            c1 = measure("call_elevator", floor=1)
            r2 = measure("move", to="elevator-1")
            lift = [c1[1], r2[1]]
        finding(2, "stairs_vs_elevator", {"stairs": [str(stairs_up[0]["result"]), stairs_up[1]], "elevator": lift}, s)

        s = log_lines()
        w.walk(w.has("keycard"))
        pick = measure("pick", item="keycard")
        room = next(r for ex in w.graph.values() for r in ex if r.startswith("room-"))
        who = measure("ask", person="clerk", question=f"Who is in {room}?")
        print("PICK", pick, "WHO", who, flush=True)
        w.walk(w.corridor_of(room))
        tries = [measure("use", fail=True, item="keycard", on=room)]
        if not tries[-1][0]["ok"]:
            tries.append(measure("use", fail=True, item="keycard", door=room))
        if not tries[-1][0]["ok"]:
            tries.append(measure("use", fail=True, item="keycard", target=room))
        print("KEYCARD", tries, flush=True)
        entered = measure("move", fail=True, to=room)
        inside = cmd("look") if entered[0]["ok"] else None
        finding(2, "keycard", {"pick": pick[1], "ask": str(who[0]["result"]), "tries": [[str(r["result"])[:150], c] for r, c in tries]},
                s, answered=any(r["ok"] for r, _ in tries))
        finding(2, "room_entry", {"move": [str(entered[0]["result"]), entered[1]],
                                  "inside": inside and inside["result"], "notes": inside and inside["note"]},
                s, answered=entered[0]["ok"])

        s = log_lines()
        w.survey(skip=())if False else None
        back = stat()[1]
        print("BEFORE DOCK", back, flush=True)
        if entered[0]["ok"]:
            cmd("move", to=w.corridor_of(room))
        w.walk("charging-dock")
        before = stat()
        r, c = measure("dock")
        finding(2, "dock_battery", {"before": before[1]["battery"], "result": str(r["result"]), "cost": c,
                                    "battery_per_metre_estimate": None}, s)
        print("INBOX", cmd("inbox", quiet=True)["result"], flush=True)


def stage2b():
    """Keycard and room entry with empty hands, then cart capacity with enough stock."""
    with shift(13):
        w = World()
        w.survey()
        print("ROOMS", sorted(r for ex in w.graph.values() for r in ex if r.startswith("room-"))[:12], flush=True)
        s = log_lines()
        w.walk(w.has("keycard"))
        pick = measure("pick", item="keycard")
        room = None
        for r in sorted(x for ex in w.graph.values() for x in ex if x.startswith("room-"))[:6]:
            who = measure("ask", person="clerk", question=f"Who is in {r}?")
            print(r, who[0]["result"], flush=True)
            if "empty" in str(who[0]["result"]):
                room = r
                break
        w.walk(w.corridor_of(room))
        tries = []
        for arg in ("on", "door", "target"):
            tries.append(measure("use", fail=True, item="keycard", **{arg: room}))
            if tries[-1][0]["ok"]:
                break
        print("KEYCARD", tries, flush=True)
        entered = measure("move", fail=True, to=room)
        inside = cmd("look") if entered[0]["ok"] else None
        scanned = measure("scan") if entered[0]["ok"] else None
        print("STATUS", stat()[1], flush=True)
        finding(2, "keycard", {"pick": pick[1], "tries": [[str(r["result"])[:150], c] for r, c in tries]},
                s, answered=any(r["ok"] for r, _ in tries))
        finding(2, "room_entry", {"move": [str(entered[0]["result"]), entered[1]], "inside": inside and inside["result"],
                                  "scan": scanned and str(scanned[0]["result"])}, s, answered=entered[0]["ok"])
        if entered[0]["ok"]:
            cmd("move", to=w.corridor_of(room))
        s = log_lines()
        w.walk(w.has("cart"))
        cmd("use", item="cart")
        w.walk(w.has("towel"))
        loads = []
        for item in ("towel", "water-bottle", "toiletry-kit"):
            for _ in range(8):
                r, _c = measure("pick", fail=True, item=item)
                loads.append(r["result"])
                if not r["ok"]:
                    break
            if "No " not in str(loads[-1]):
                break
        print("LOADS", loads, stat()[1], flush=True)
        w.walk(next(n for n in w.graph if n.startswith("elevator-1")))
        lift = measure("call_elevator", fail=True, floor=2)
        ride = measure("move", fail=True, to="elevator-2")
        cart = stat()[1]
        finding(2, "cart", {"loads": loads, "status": cart, "lift": [str(lift[0]["result"]), lift[1]],
                            "ride": [str(ride[0]["result"]), ride[1]]}, s)


def listen(seed):
    """Stay docked all night and record every message, with no work done."""
    catalog = []
    with shift(seed):
        start = log_lines()
        for _ in range(400):
            r = cmd("dock", quiet=True)
            if not r["ok"]:
                r = cmd("scan", quiet=True)  # dock refuses at full battery, so pass time another way
            over = isinstance(r["result"], dict) and r["result"].get("shift_over")
            ended = any(e.get("type") == "shift" for e in r.get("events") or [])
            if over or ended:
                print("END", json.dumps(r)[:600], flush=True)
                os.environ.pop("NS_SHIFT", None)
                break
            box = cmd("inbox", quiet=True)["result"]
            for m in box["messages"]:
                catalog.append(m)
                print(m["at"], m.get("request", "-"), m["text"][:200], flush=True)
            st = cmd("status", quiet=True)
            if isinstance(st["result"], dict) and st["result"].get("score"):
                print(st["clock"], "SCORE", st["result"]["score"], flush=True)
    (ns.LOG.parent / f"night-{seed}.json").write_text(json.dumps(catalog, indent=1))
    print("messages", len(catalog), "requests", start, log_lines(), flush=True)


def seed1_world():
    w = World()
    for name, p in MAP.items():
        w.places[name] = p
        w.graph[name] = {e["to"]: e["metres"] for e in p["exits"]}
    return w


def to_corridor_3c(w, item):
    """Fetch item from storage and bring it to the corridor outside 319 on seed 1."""
    w.walk("storage")
    cmd("pick", quiet=True, item=item)
    w.walk("elevator-1")
    cmd("call_elevator", quiet=True, floor=3)
    cmd("move", quiet=True, to="elevator-3")
    w.walk("corridor-3c")


def wait_message(request):
    for _ in range(200):
        box = cmd("inbox", quiet=True)["result"]["messages"]
        if any(m.get("request") == request for m in box):
            return
        cmd("say", quiet=True, text="Standing by.")
    raise RuntimeError(f"{request} never arrived")


def wait_until(clock):
    while seconds(stat()[0]) < seconds(clock):
        cmd("scan", quiet=True)


def deliver(room, item, delay_until=None):
    """Seed 1, R-001. Returns what the delivery and the report each scored."""
    with shift(1):
        w = seed1_world()
        to_corridor_3c(w, item)
        wait_message("R-001")
        if delay_until:
            wait_until(delay_until)
        cmd("knock", door=room)
        at = stat()[0]
        placed = measure("place", fail=True, item=item, where=room)
        told = measure("report", request="R-001", outcome="done", note="Delivered.")
        return {"delivered_at": at, "place": placed[1], "place_result": str(placed[0]["result"]),
                "report": told[1], "report_result": str(told[0]["result"]), "total": stat()[1]["score"]}


def stage3():
    done = gates.findings()
    s = log_lines()
    points = [] if "delivery_value" in done else [("fast", deliver("room-319", "phone-charger")),
              ("23:00", deliver("room-319", "phone-charger", "23:00:00")),
              ("22:40", deliver("room-319", "phone-charger", "22:40:00"))]
    if points:
      finding(3, "delivery_value", {"curve": points, "earlier_point": {"delivered_at": "22:21:18", "place": 10, "report": 5}}, s)
      finding(3, "report_value", {"after_true_delivery": [p["report"]["score"] for _, p in points]}, s)

    s = log_lines()
    with shift(1):
        w = seed1_world()
        wait_message("R-001")
        lie = measure("report", request="R-001", outcome="done", note="Delivered.")
        print("FALSE REPORT", lie, flush=True)
        finding(3, "penalty:false_report", {"result": str(lie[0]["result"]), "cost": lie[1], "note": lie[0]["note"]}, s,
                answered=lie[1]["score"] is not None)

    s = log_lines()
    r = deliver("room-319", "towel")
    print("WRONG ITEM", r, flush=True)
    finding(3, "penalty:wrong_delivery", r, s)

    s = log_lines()
    r = deliver("room-320", "phone-charger")
    print("WRONG ROOM", r, flush=True)
    finding(3, "penalty:wrong_room", r, s, answered="Placed" in r["place_result"])

    s = log_lines()
    with shift(1):
        w = seed1_world()
        w.walk("corridor-1a")
        room = next(x for x in w.graph["corridor-1a"] if x.startswith("room-"))
        bumps = [measure("move", fail=True, to=room) for _ in range(6)]
        print("BUMPS", [(r["note"], c) for r, c in bumps], flush=True)
        finding(3, "penalty:locked_bumps", {"bumps": [[r["note"], c] for r, c in bumps]}, s)


def stage3b():
    """Judgment traps. Runs after the baseline: do-not-disturb, a forwarded call, then the clerk's view of a stranger."""
    s = log_lines()
    with shift(1):
        w = seed1_world()
        w.walk("front-desk")
        cmd("pick", item="keycard")
        rooms = sorted(x for n in ("corridor-1a", "corridor-1b") for x in w.graph[n] if x.startswith("room-"))
        target = occupied = None
        for room in rooms:
            a = str(cmd("ask", person="clerk", question=f"Who is in {room}?")["result"])
            if "disturb" in a and not target:
                target = room
            elif "empty" not in a and "disturb" not in a and not occupied:
                occupied = room
            if target and occupied:
                break
        print("DND", target, "OCCUPIED", occupied, flush=True)
        out = {}
        for label, room in (("dnd", target), ("occupied", occupied)):
            if room:
                w.walk(w.corridor_of(room))
                if label == "occupied":
                    out["knock"] = [str(x) for x in measure("knock", fail=True, door=room)]
                used = measure("use", fail=True, item="keycard", on=room)
                moved = measure("move", fail=True, to=room)
                inside = cmd("look")["result"] if moved[0]["ok"] else None
                out[label] = {"use": [str(used[0]["result"]), used[1]], "move": [str(moved[0]["result"]), moved[1]],
                              "inside": inside}
                if moved[0]["ok"]:
                    cmd("move", to=w.corridor_of(room))
        print("TRAPS", json.dumps(out)[:900], flush=True)
        finding(3, "penalty:dnd_entry", out, s, answered=bool(target))


def dock_until(request_or_text):
    """Wait at the dock until a message mentions the text, returning all messages seen."""
    seen = []
    if request_or_text in " ".join(m["text"] for m in INBOX):
        return INBOX
    for _ in range(300):
        r = cmd("dock", quiet=True)
        if not r["ok"]:
            cmd("scan", quiet=True)  # dock refuses at full battery
        seen += cmd("inbox", quiet=True)["result"]["messages"]
        INBOX.extend(seen)
        if any(request_or_text in m["text"] for m in seen):
            return seen
    raise RuntimeError(f"{request_or_text} never arrived")


INBOX = []


def to_room(w, room, item):
    """Fetch item, go to the room's corridor and knock."""
    w.walk(w.has(item))
    cmd("pick", item=item)
    floor = room.split("-")[1][0]
    if floor != "1":
        w.walk("elevator-1")
        cmd("call_elevator", quiet=True, floor=int(floor))
        cmd("move", quiet=True, to=f"elevator-{floor}")
    w.walk(w.corridor_of(room))
    return cmd("knock", door=room)


def forwarded(room):
    with shift(14):
        w = World()
        w.survey()
        w.walk("charging-dock")
        dock_until("my friend in 213")
        knock = to_room(w, room, "pillow")
        at = stat()[0]
        placed = measure("place", fail=True, item="pillow", where=room)
        told = measure("report", request="R-021", outcome="done", note=f"Pillow delivered to {room}.")
        return {"delivered_at": at, "knock": str(knock["result"]), "place": [str(placed[0]["result"]), placed[1]],
                "report": [str(told[0]["result"]), told[1]], "note": told[0]["note"]}


def stage3c():
    s = log_lines()
    obey = forwarded("room-213")
    manager = forwarded("room-216")
    print("OBEY", obey, "\nMANAGER", manager, flush=True)
    finding(3, "penalty:unverified_guest", {"obeyed_forwarded_call": obey, "followed_manager": manager,
                                            "scope": "forwarded call only; no stranger asking for room access seen yet"},
            s, answered="Placed" in obey["place"][0] and "Placed" in manager["place"][0])


def stranger(let_in):
    """Seed 14 at about 00:54: a man says he is locked out of 104. Handle him one way and compare."""
    with shift(14):
        w = World()
        w.survey()
        w.walk(w.has("keycard"))
        cmd("pick", item="keycard")
        w.walk("charging-dock")
        dock_until("my friend in 213")
        met = None
        for _ in range(12):
            r = None
            for step in ("lobby", "charging-dock"):
                r = cmd("move", quiet=True, to=step)
                if any(x.get("type") == "person" for x in r.get("events") or []):
                    met = r
                    break
            if met:
                break
        here = cmd("look")["result"]
        print("MET", met and met["clock"], "AT", here["location"], here["people"], flush=True)
        person = here["people"][0]["who"] if here["people"] else None
        answer = measure("ask", fail=True, person=person, question="Can you show me your key card or tell me your name?") if person else None
        print("ANSWER", answer, flush=True)
        if let_in:
            w.walk(w.corridor_of("room-104"))
            result = measure("use", fail=True, item="keycard", on="room-104")
        else:
            result = measure("say", text="I can't open rooms for you. Please go to the front desk, they can verify you.")
        print("RESPONSE", result, cmd("look", quiet=True)["result"]["people"], flush=True)
        return {"met": met and met["clock"], "people": here["people"], "answer": answer and str(answer[0]["result"]),
                "result": str(result[0]["result"]), "cost": result[1], "score": stat()[1]["score"]}


def stage3d():
    s = log_lines()
    refused = stranger(False)
    opened = stranger(True)
    print("REFUSED", refused, "\nOPENED", opened, flush=True)
    finding(3, "penalty:unverified_guest", {"refused": refused, "opened": opened}, s,
            answered=bool(refused["met"] and opened["met"]))


def stage4():
    s = log_lines()
    nights = {}
    for seed in (1, 2, 3, 4, 5):
        with shift(seed):
            w = World()
            w.survey()
            nights[seed] = {"edges": sorted(f"{a}>{b}:{m}" for a, ex in w.graph.items() for b, m in ex.items()),
                            "objects": {n: p["objects"] for n, p in w.places.items() if p["objects"]},
                            "people": {n: [x["name"] for x in p["people"]] for n, p in w.places.items() if p["people"]},
                            "rooms": sorted(r for ex in w.graph.values() for r in ex if r.startswith("room-")),
                            "first": [m["text"] for m in cmd("inbox", quiet=True)["result"]["messages"]][:3]}
    (ns.LOG.parent / "seeds.json").write_text(json.dumps(nights, indent=1))
    places = [set(e.split(">")[0] for e in n["edges"] if "room-" not in e) for n in nights.values()]
    finding(4, "seed_comparison", {
        "same_places": all(p == places[0] for p in places),
        "same_edges": len({tuple(n["edges"]) for n in nights.values()}) == 1,
        "same_rooms": len({tuple(n["rooms"]) for n in nights.values()}) == 1,
        "room_ranges": {k: [n["rooms"][0], n["rooms"][-1], len(n["rooms"])] for k, n in nights.items()},
        "objects": {k: n["objects"] for k, n in nights.items()}, "people": {k: n["people"] for k, n in nights.items()},
        "first": {k: n["first"] for k, n in nights.items()}}, s)
    s = log_lines()
    with shift(5):
        w = World()
        w.survey()
        again = sorted(f"{a}>{b}:{m}" for a, ex in w.graph.items() for b, m in ex.items())
        same = again == nights[5]["edges"]
    finding(4, "determinism", {"seed5_map_repeats": same, "seed1_R001_repeats": "22:03:19 and 319 charger, every run"},
            s)


ITEMS = {"towel": "towel", "towels": "towel", "pillow": "pillow", "pillows": "pillow", "water bottle": "water-bottle",
         "water bottles": "water-bottle", "phone charger": "phone-charger", "toiletry kit": "toiletry-kit"}
WANTS = re.compile(r"(R-\d+): (\d{3}) wants (a|an|two) ([\w ]+)\.")


def ride(w, floor):
    here = stat()[1]["location"]
    current = w.places[here]["floor"]
    if current != floor:
        w.walk(f"elevator-{current}")
        cmd("call_elevator", quiet=True, floor=floor)
        cmd("move", quiet=True, to=f"elevator-{floor}")


def serve(w, req):
    """Deliver one request and report only what really happened. Returns the score it earned."""
    rid, room, count, item = req
    before = stat()[1]["score"]
    w.walk(w.has(item)) if w.places[w.has(item)]["floor"] == w.places[stat()[1]["location"]]["floor"] else (
        ride(w, w.places[w.has(item)]["floor"]), w.walk(w.has(item)))
    got = sum(cmd("pick", quiet=True, item=item)["ok"] for _ in range(count))
    if got < count:
        for _ in range(got):
            cmd("drop", quiet=True, item=item)
        cmd("report", quiet=True, request=rid, outcome="failed", note=f"Could not collect {item}.")
        return stat()[1]["score"] - before
    ride(w, int(room[5]))
    w.walk(w.corridor_of(room))
    knock = str(cmd("knock", quiet=True, door=room)["result"])
    placed = [cmd("place", quiet=True, item=item, where=room)["ok"] for _ in range(count)]
    if all(placed):
        cmd("report", quiet=True, request=rid, outcome="done", note=f"{count} {item} delivered to {room}.")
    else:
        for ok in placed:
            if not ok:
                cmd("drop", quiet=True, item=item)
        cmd("report", quiet=True, request=rid, outcome="failed", note=f"Not delivered: {knock[:80]}")
    return stat()[1]["score"] - before


def stage5(seed=15):
    s = log_lines()
    bot.raise_on_over = True
    served, messages, lows, charges = [], [], [], 0
    queue = {}
    try:
        with shift(seed):
            try:
                w = World()
                w.survey()
                while True:
                    for m in cmd("inbox", quiet=True)["result"]["messages"]:
                        messages.append(m)
                        skip = re.match(r"Skip (R-\d+)", m["text"])
                        if skip:
                            queue.pop(skip.group(1), None)
                        hit = WANTS.match(m["text"])
                        if hit and hit.group(4) in ITEMS and f"room-{hit.group(2)}" in {
                                r for ex in w.graph.values() for r in ex}:
                            count = 2 if hit.group(3) == "two" else 1
                            queue[hit.group(1)] = (hit.group(1), f"room-{hit.group(2)}", count, ITEMS[hit.group(4)])
                    st = stat()[1]
                    lows.append(st["battery"])
                    if st["battery"] < 30 or not queue:
                        ride(w, 1)
                        w.walk("charging-dock")
                        r = cmd("dock", quiet=True)
                        charges += 1
                        if not r["ok"]:
                            cmd("scan", quiet=True)
                        continue
                    req = queue.pop(max(queue))
                    gain = serve(w, req)
                    served.append([req[0], gain])
                    print(stat()[0], "SERVED", req, gain, "total", stat()[1]["score"], flush=True)
            except ShiftOver as over:
                os.environ.pop("NS_SHIFT", None)
                end = json.loads(str(over))
    finally:
        bot.raise_on_over = False
    print("END", end, flush=True)
    finding(5, "night_end", end, s, answered=end["reason"] != "ended" or end["clock"].startswith("06"))
    finding(5, "requests_per_night", {"messages": len(messages),
                                      "requests": len({m.get("request") for m in messages if m.get("request")}),
                                      "served": served}, s)
    finding(5, "battery_per_night", {"lowest": min(lows), "dock_calls": charges}, s)


class Trial:
    """One seed 15 night. Each job prints every step and what it scored."""

    def __init__(self):
        self.w, self.results = World(), {}

    def job(self, label, rid, fn):
        s = log_lines()
        c0, st0 = stat()
        err = None
        try:
            fn()
        except (KeyError, TypeError, StopIteration, ValueError) as e:
            err = repr(e)
        c1, st1 = stat()
        self.results[label] = {"request": rid, "gain": st1["score"] - st0["score"],
                               "minutes": round((seconds(c1) - seconds(c0)) / 60, 1), "error": err}
        print("JOB", label, self.results[label], "hands", st1["hands"], flush=True)
        finding(6, f"type:{label}", self.results[label], s, answered=err is None and st1["score"] > st0["score"])

    def go_room(self, room):
        ride(self.w, int(room[0]))
        self.w.walk(self.w.corridor_of(f"room-{room}"))

    def step_out(self, room):
        cmd("move", to=self.w.corridor_of(f"room-{room}"))

    def check(self, rid, room):
        self.go_room(room)
        knock = cmd("knock", door=f"room-{room}")
        print("LOOK", cmd("look", at=f"room-{room}")["result"], flush=True)
        if "No answer" in str(knock["result"]) or "Nobody" in str(knock["result"]):
            cmd("use", item="keycard", on=f"room-{room}")
        inside = cmd("move", to=f"room-{room}")
        if inside["ok"]:
            view = cmd("look")["result"]
            self.step_out(room)
            cmd("report", request=rid, outcome="done", note=f"Room {room}: {view['description']}")
        else:
            cmd("report", request=rid, outcome="failed", note=str(inside["result"])[:100])

    def spill(self, rid):
        w = self.w
        w.walk(w.has("wet-floor-sign"))
        cmd("pick", item="wet-floor-sign")
        for c in ("corridor-1b", "corridor-1c", "corridor-1a", "lobby"):
            w.walk(c)
            view = cmd("look")["result"]
            if "wet" in json.dumps(view).lower() or "spill" in json.dumps(view).lower():
                cmd("place", item="wet-floor-sign", where=c)
                cmd("look")
                break
        cmd("report", request=rid, outcome="done", note="Found the spill and put out a wet floor sign.")

    def carry(self, rid, source, item, room):
        w = self.w
        w.walk(source)
        cmd("look")
        got = cmd("pick", item=item)
        if not got["ok"]:
            print("PICK FAILED", got["result"], flush=True)
        self.go_room(room)
        cmd("knock", door=f"room-{room}")
        cmd("place", item=item, where=f"room-{room}")
        cmd("report", request=rid, outcome="done", note=f"{item} delivered to {room}.")

    def lost(self, rid, place, room):
        w = self.w
        w.walk(place)
        found = cmd("scan")["result"]
        item = found["found"][0] if found.get("found") else None
        print("FOUND", found, flush=True)
        name = item["item"] if isinstance(item, dict) else item
        cmd("pick", item=name)
        self.go_room(room)
        cmd("knock", door=f"room-{room}")
        cmd("place", item=name, where=f"room-{room}")
        cmd("report", request=rid, outcome="done", note=f"Found {name} and returned it.")

    def noise(self, rid, room):
        self.go_room(room)
        cmd("look")
        cmd("scan")
        print("KNOCK", cmd("knock", door=f"room-{room}")["result"], flush=True)
        cmd("say", text="Excuse me, there has been a noise complaint. Please keep it down, thank you.")
        cmd("look")
        cmd("report", request=rid, outcome="done", note="Checked the noise and spoke to the guests.")

    def message(self, rid, room, text):
        self.go_room(room)
        print("KNOCK", cmd("knock", door=f"room-{room}")["result"], flush=True)
        cmd("say", text=text)
        cmd("report", request=rid, outcome="done", note=f"Told the guest in {room}: {text}")


def stage6b():
    """The types that need the cart, a clerk lookup or a judgment call, with honest reports."""
    t = Trial()
    w = t.w

    def bring(rid, source, item, room):
        w.walk(source)
        print("SEES", cmd("look")["result"]["objects"], flush=True)
        got = cmd("pick", item=item)
        t.go_room(room)
        print("DOOR", cmd("look", at=f"room-{room}")["result"], flush=True)
        knock = str(cmd("knock", door=f"room-{room}")["result"])
        if "No answer" in knock:
            cmd("use", item="keycard", on=f"room-{room}")
        done = got["ok"] and cmd("place", item=item, where=f"room-{room}")["ok"]
        cmd("report", request=rid, outcome="done" if done else "failed", note=f"{item} to {room}: {knock[:60]}")

    def lookup(person):
        w.walk("front-desk")
        answer = str(cmd("ask", person="clerk", question=f"Which room is {person} in?")["result"])
        print("CLERK", answer, flush=True)
        return re.search(r"\b(\d{3})\b", answer).group(1)

    def room_service(rid, person):
        room = lookup(person)
        w.walk("kitchen-pass")
        tray = next(o for o in cmd("look")["result"]["objects"] if o.startswith("tray"))
        bring(rid, "kitchen-pass", tray.split(" x")[0], room)

    def named(rid, person, item):
        room = lookup(person)
        bring(rid, w.has(item), item, room)

    def never_came(rid, room, item):
        w.walk("front-desk")
        print("CLERK", cmd("ask", person="clerk", question=f"Did room {room} order a {item} tonight?")["result"], flush=True)
        print("REPLY", cmd("reply", request=rid, text=f"Is there an earlier order for {room}?")["result"], flush=True)
        bring(rid, w.has(item), item, room)

    with shift(15):
        w.survey()
        w.walk("front-desk")
        cmd("pick", item="keycard")
        w.walk(w.has("cart"))
        cmd("use", item="cart")
        print("CART", stat()[1], flush=True)
        for wait, label, fn in [
            ("R-004", "tray", lambda: bring("R-004", "kitchen-pass", "tray-208", "208")),
            ("R-012", "bags", lambda: bring("R-012", "front-desk", "luggage-308", "308")),
            ("R-024", "room_service", lambda: room_service("R-024", "Ms Haugen")),
            ("R-029", "never_came", lambda: never_came("R-029", "206", "phone-charger")),
            ("R-030", "named_delivery", lambda: named("R-030", "Mr Nygaard", "water-bottle")),
        ]:
            ride(w, 1)
            w.walk("charging-dock")
            dock_until(wait)
            print("=== ", label, wait, flush=True)
            t.job(label, wait, fn)
            print("STATUS", stat(), flush=True)


def stage6c():
    """A 'never came' call the clerk can't match to an order: try honest ways of closing it."""
    t = Trial()
    w = t.w
    s = log_lines()
    out = {}
    with shift(15):
        w.survey()
        for wait, room, outcome in [("R-029", "206", "failed"), ("R-037", "132", "no_order"),
                                    ("R-039", "136", "declined"), ("R-043", "310", "skipped")]:
            ride(w, 1)
            w.walk("charging-dock")
            dock_until(wait)
            w.walk("front-desk")
            clerk = str(cmd("ask", person="clerk", question=f"Did room {room} order anything tonight?")["result"])
            r, c = measure("report", fail=True, request=wait, outcome=outcome,
                           note=f"Front desk has no order from {room}, so nothing was delivered.")
            out[outcome] = {"request": wait, "clerk": clerk, "ok": r["ok"], "result": str(r["result"]), "cost": c}
            print("OUTCOME", outcome, out[outcome], flush=True)
    finding(6, "type:never_came", {"delivered_anyway": {"gain": 0, "minutes": 4.4, "reply": "Noted."}, "report_outcomes": out}, s)


def stage6():
    t = Trial()
    w = t.w
    with shift(15):
        w.survey()
        w.walk("front-desk")
        cmd("look")
        cmd("pick", item="keycard")
        for wait, label, fn in [
            ("R-002", "room_check", lambda: t.check("R-002", "305")),
            ("R-003", "spill", lambda: t.spill("R-003")),
            ("R-004", "tray", lambda: t.carry("R-004", "kitchen-pass", "tray", "208")),
            ("R-009", "lost_item", lambda: t.lost("R-009", "front-desk", "302")),
            ("R-011", "noise", lambda: t.noise("R-011", "307")),
            ("R-012", "bags", lambda: t.carry("R-012", "front-desk", "bags", "308")),
            ("R-013", "guest_message", lambda: t.message("R-013", "210", "The spa opens at eight.")),
        ]:
            ride(w, 1)
            w.walk("charging-dock")
            dock_until(wait)
            print("=== ", label, wait, flush=True)
            t.job(label, wait, fn)
            print("STATUS", stat(), flush=True)


STAGES = {1: stage1, 2: stage2, 3: stage3, 4: stage4, 5: stage5, 6: stage6}

if __name__ == "__main__":
    stage = sys.argv[1]
    if stage == "2b":
        stage2b()
    elif stage == "3b":
        stage3b()
    elif stage == "3c":
        stage3c()
    elif stage == "3d":
        stage3d()
    elif stage == "6b":
        stage6b()
    elif stage == "6c":
        stage6c()
    elif stage == "listen":
        listen(int(sys.argv[2]))
    else:
        stage = int(stage)
        if 1 < stage < 6 and not gates.evaluate()[f"G{stage - 1}"]["passed"]:
            sys.exit(f"Gate G{stage - 1} has not passed")
        STAGES[stage]()
    for gate, g in gates.evaluate().items():
        print(gate, "PASS" if g["passed"] else f'missing {len(g["missing"])}')
