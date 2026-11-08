"""Rebuild a recorded practice night from logs/requests.jsonl, so the simulator can be checked against reality.

The map, distances, stock and people come from that night's look replies, the messages and their times from its
inbox replies, and the hidden truth (who is in which room, where lost things lay, which corridor was wet, where the
music came from, when a corridor closed) from what the robot saw. Anything the robot never saw is filled in with
the same rules as a random night. The shift is the first full night for the seed, or NS_SIM_SHIFT if set.
Results are cached in logs/sim/replay-<seed>.json.
"""
import json, os, random, re
from pathlib import Path
from . import text
from .hotel import SURNAMES, TITLES

ROOT = Path(__file__).parent.parent
LOG = ROOT / "logs" / "requests.jsonl"
CACHE = ROOT / "logs" / "sim"
TYPES = {v: k for k, v in text.DESCRIPTIONS.items()}
TYPES.update({"bar": "bar", "staff": "staff"})
KIND = {"dock": "dock", "lobby": "lobby", "desk": "desk", "bar": "bar", "elevator": "elevator", "kitchen": "kitchen",
        "stairwell": "stairwell", "storage": "storage", "laundry": "laundry", "staff": "staff"}
DYNAMIC = re.compile(r"^(tray|luggage|toy|wallet|glasses)(-\d+)*$|^note$")


def seconds(clock):
    h, m, s = map(int, clock.split(":"))
    return ((h + 2) % 24) * 3600 + m * 60 + s


def find_shift(seed):
    """The first shift started with this seed that ran to 06:00, unless NS_SIM_SHIFT names one."""
    if os.environ.get("NS_SIM_SHIFT"):
        return os.environ["NS_SIM_SHIFT"]
    starts, full = [], set()
    with LOG.open() as f:
        for line in f:
            if '"start_shift"' in line and f'"seed": {seed}' in line:
                reply = json.loads(json.loads(line)["response"])
                if isinstance(reply.get("result"), dict) and reply["result"].get("seed") == seed:
                    starts.append(reply["result"]["shift"])
            elif '\\"reason\\":\\"time\\"' in line:
                full.add(json.loads(line)["shift"])
    for shift in starts:
        if shift in full:
            return shift
    raise SystemExit(f"no full recorded night for seed {seed} in {LOG}")


def records(shift):
    with LOG.open() as f:
        for line in f:
            if shift in line:
                d = json.loads(line)
                if d.get("shift") != shift:
                    continue
                try:
                    reply = json.loads(d["response"])
                except ValueError:
                    continue
                res = reply.get("result")
                if isinstance(res, dict) and "shift_over" in res and "outcome" in res:
                    res = res["outcome"]
                yield {"cmd": (d.get("body") or {}).get("cmd"), "args": (d.get("body") or {}).get("args") or {},
                       "ok": reply.get("ok"), "t": seconds(reply["clock"]) if reply.get("clock") else None,
                       "res": res, "events": reply.get("events") or []}


def build(seed):
    shift = find_shift(seed)
    cache = CACHE / f"replay-{seed}.json"
    if cache.exists():
        spec = json.loads(cache.read_text())
        if spec.get("shift") == shift:
            return spec
    spec = Night(seed, shift).spec()
    CACHE.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(spec))
    return spec


class Night:
    """Everything one recorded night showed, gathered in a single pass over its requests."""

    def __init__(self, seed, shift):
        self.seed, self.shift = seed, shift
        self.rng = random.Random(f"replay-{seed}")
        self.looks, self.first_objects, self.messages = {}, {}, []
        self.guests, self.empty, self.dnd, self.no_dnd, self.tidy, self.ajar = {}, set(), {}, {}, {}, set()
        self.named, self.beside, self.sightings = {}, {}, {}
        self.wet_seen, self.dry_seen, self.credited_signs = {}, {}, {}
        self.music, self.strangers, self.visits = [], [], {}
        self.blocked_seen, self.open_seen, self.notes_seen, self.no_note, self.note_text = {}, {}, {}, {}, {}
        self.texts, self.ola, self.keyed, self.scans = dict(text.READ), None, set(), {}
        self.read()

    def read(self):
        here, sign_at, now = "charging-dock", None, 0
        seen_msgs, signed = set(), set()
        for r in records(self.shift):
            cmd, a, res, now = r["cmd"], r["args"], r["res"], r["t"] if r["t"] is not None else now
            if cmd == "inbox" and isinstance(res, dict):
                for m in res["messages"]:
                    key = (m["at"], m["from"], m["text"])
                    if key not in seen_msgs:
                        seen_msgs.add(key)
                        self.messages.append(dict(m, at=seconds(m["at"])))
            elif cmd == "look" and isinstance(res, dict) and "location" in res:
                self.saw_place(res, now)
                here = res["location"]
            elif cmd == "look" and isinstance(res, dict) and "door" in res:
                seen = self.dnd if res.get("sign") not in (None, "none") else self.no_dnd
                seen.setdefault(res["door"], []).append(now)
            elif cmd in ("move", "navigate") and isinstance(res, dict) and "at" in res:
                if r["ok"] and a.get("to") == res["at"]:
                    place = res["at"]
                    if res.get("notice") or place not in signed:  # a signed patch walks like a dry floor
                        (self.wet_seen if res.get("notice") else self.dry_seen).setdefault(place, []).append(now)
                    if not res.get("notice"):
                        self.open_seen.setdefault(frozenset((here, place)), []).append(now)
                    self.visits.setdefault(place, []).append(now)
                    here = place
            elif cmd == "move" and not r["ok"] and "cleaning crew" in str(res):
                self.blocked_seen.setdefault(frozenset((here, a.get("to"))), []).append(now)
            elif cmd == "knock" and isinstance(res, str):
                hit = re.match(r"(.+) opens the door of (room-\d+)", res)
                if hit:
                    self.guests[hit.group(2)] = hit.group(1)
                    self.no_dnd.setdefault(hit.group(2), []).append(now)
                elif "Do Not Disturb" in res:
                    self.dnd.setdefault(a.get("door"), []).append(now)
                elif res == "No answer.":
                    self.empty.add(a.get("door"))
                self.keyed.add(a.get("door"))
            elif cmd == "use" and a.get("item") == "keycard":
                self.keyed.add(a.get("on"))
            elif cmd == "ask" and isinstance(res, dict):
                self.heard_clerk(res.get("answer", ""), now)
            elif cmd == "scan" and isinstance(res, dict):
                self.scans.setdefault(here, []).append(now)
                for name in res.get("found", []):
                    self.sightings.setdefault(name, (now, here, True))
            elif cmd == "read" and r["ok"] and isinstance(res, str):
                if a.get("object") == "note":
                    self.note_text[here] = res
                else:
                    self.texts[a.get("object")] = res
            elif cmd == "place" and res == "Wet floor sign placed over the spill.":
                sign_at = a.get("where")
                signed.add(sign_at)
            elif cmd == "pick" and r["ok"] and a.get("item") == "wet-floor-sign":
                signed.discard(here)
            elif cmd == "report" and a.get("outcome") == "done":
                if res == "Noted. Thanks." and sign_at:
                    self.credited_signs[a.get("request")] = sign_at
                sign_at = None
            for e in r["events"]:
                if e["type"] == "person":
                    self.strangers.append((now, here, "room-" + re.search(r"out of (\d+)", e["text"]).group(1)))

    def saw_place(self, v, now):
        here = v["location"]
        self.looks.setdefault(here, v)
        if here not in self.first_objects:
            self.first_objects[here] = v["objects"]
        desc = v["description"]
        if here.startswith("room-"):
            who = re.match(r"(.+) is here\.", desc)
            if who and not desc.startswith("Nobody"):
                self.guests[here] = who.group(1)
            elif desc.startswith("Nobody"):
                self.empty.add(here)
            self.tidy[here] = "tidy" in desc
        (self.wet_seen if "wet patch" in desc else self.dry_seen).setdefault(here, []).append(now)
        for room in re.findall(r"behind the door of (room-\d+)", desc):
            self.music.append((now, here, room))
        for name in v["objects"]:
            name = name.split(" x")[0]
            if DYNAMIC.match(name) and name != "note":
                self.sightings.setdefault(name, (now, here, False))
        (self.notes_seen if "note" in v["objects"] else self.no_note).setdefault(here, []).append(now)
        for p in v.get("people", []):
            if p.get("who") == "maintenance" and not self.ola:
                self.ola = {"name": p["name"], "at": here}
        for e in v["exits"]:
            link = frozenset((here, e["to"]))
            (self.blocked_seen if e.get("blocked") else self.open_seen).setdefault(link, []).append(now)
            if e.get("door") == "open" and e["to"] not in self.keyed and e["to"].startswith("room-") \
                    and not here.startswith("room-"):
                self.ajar.add(e["to"])

    def heard_clerk(self, answer, now):
        hit = re.match(r"(.+) is in (room-\d+)\.", answer)
        if hit:
            self.named[hit.group(1)] = hit.group(2)
            self.guests.setdefault(hit.group(2), hit.group(1))
        hit = re.search(r"For (R-\d+) that's (\d{3})", answer)
        if hit:
            self.beside[hit.group(1)] = "room-" + hit.group(2)
        hit = re.match(r"(\d{3}) is empty tonight", answer)
        if hit:
            self.empty.add("room-" + hit.group(1))
        hit = re.match(r"(\d{3}) is (.+?)\.( Asked not to be disturbed\.)?$", answer)
        if hit and "empty" not in hit.group(2):
            self.guests.setdefault("room-" + hit.group(1), hit.group(2))
            if hit.group(3):
                self.dnd.setdefault("room-" + hit.group(1), []).append(now)

    # ---------- the spec ----------

    def spec(self):
        places = self.places()
        spec = {"label": f"replay {self.seed}", "shift": self.shift, "places": places, "texts": self.texts,
                "sign": "wet-floor-sign", "cart": "cart", "keycard": "keycard"}
        spec["roles"] = {k: next(p for p, v in places.items() if v["type"] == t)
                         for k, t in (("dock", "dock"), ("desk", "desk"), ("kitchen", "kitchen"))}
        objects = {}
        for place, objs in self.first_objects.items():
            kept = [[o.split(" x")[0], int(o.split(" x")[1]) if " x" in o else 1] for o in objs]
            kept = [o for o in kept if not DYNAMIC.match(o[0])]
            if kept:
                objects[place] = kept
        spec["objects"] = objects
        spec["roles"]["cart_at"] = next((p for p, o in objects.items() if any(n == "cart" for n, _ in o)), None)
        spec["clerk"] = {"name": "Marit", "at": spec["roles"]["desk"]}
        spec["maintenance"] = self.ola
        self.corridor_of = {e[0]: p for p, v in places.items() if v["type"] == "corridor" for e in v["exits"]}
        spec["rooms"] = self.rooms(places)
        spec["jobs"], spec["messages"], spec["timeline"] = self.jobs(spec)
        spec["timeline"] += self.hazards(places)
        return spec

    def places(self):
        out = {}
        for name, v in self.looks.items():
            if name.startswith("room-"):
                continue
            desc = re.sub(r" (A wet patch on the floor here\.|A sign stands next to it\.|Music thumps behind the door "
                          r"of room-\d+\.)", "", v["description"])
            kind = "corridor" if desc.startswith("A quiet corridor") else KIND.get(TYPES.get(desc), "corridor")
            exits = [[e["to"], e["metres"], *(["stairs"] if e.get("stairs") else [])] for e in v["exits"]]
            out[name] = {"floor": v["floor"], "type": kind, "desc": desc, "exits": exits, "door": False}
        for name, v in self.looks.items():
            for e in v["exits"]:
                if e.get("door") == "closed" and e["to"] in out and out.get(name, {}).get("type") == "corridor":
                    out[e["to"]]["door"] = True
        for name, v in list(out.items()):
            for to, metres, *_ in v["exits"]:
                if to.startswith("room-") and to not in out:
                    out[to] = {"floor": v["floor"], "type": "room", "desc": "", "exits": [[name, metres]], "door": False}
        return out

    def rooms(self, places):
        bags_or_check = {"room-" + j["room"] for j in self.parsed() if j["kind"] in ("bags", "check") and j.get("room")}
        empty = (self.empty | bags_or_check) - set(self.guests)
        used = set(self.guests.values())
        rooms = {}
        for room in (p for p, v in places.items() if v["type"] == "room"):
            guest = self.guests.get(room)
            if guest is None and room not in empty:
                guest = self.new_guest(used)
            rooms[room] = {"guest": guest, "dnd": self.dnd_from(room), "tidy": self.tidy.get(room, self.rng.random() < 0.6),
                           "ajar": room in self.ajar and room in empty}
        return rooms

    def dnd_from(self, room):
        """Signs go up during the night and stay: after the last time the door showed none, by the first sign seen."""
        if room not in self.dnd:
            return None
        first = min(self.dnd[room])
        clear = [t for t in self.no_dnd.get(room, []) if t < first]
        return (max(clear) + first) // 2 if clear else 0

    def new_guest(self, used):
        while True:
            name = f"{self.rng.choice(TITLES)} {self.rng.choice(SURNAMES)}"
            if name not in used:
                used.add(name)
                return name

    def parsed(self):
        out = []
        for m in self.messages:
            if m["from"] == "night manager" and m.get("request") and not m["text"].startswith("Skip"):
                job = text.parse_request(m["text"])
                if job:
                    out.append(dict(job, rid=m["request"], at=m["at"], text=m["text"]))
        return out

    def place_room(self, job, rooms, floor_corridor):
        """The room a job is for: named in the text, known from the clerk, or else a plausible one."""
        if job.get("room"):
            return "room-" + job["room"]
        if job.get("name"):
            room = self.named.get(job["name"]) or next((r for r, v in rooms.items() if v["guest"] == job["name"]), None)
            if room is None:
                free = [r for r, v in rooms.items() if v["guest"] and v["guest"] not in self.named and v["dnd"] is None]
                room = self.rng.choice(free)
                rooms[room]["guest"] = job["name"]
            self.named[job["name"]] = room
            return room
        if job["rid"] in self.beside:
            return self.beside[job["rid"]]
        near = [r for r, v in rooms.items() if v["guest"] and v["dnd"] is None and self.corridor_of.get(r) == floor_corridor(job)]
        return self.rng.choice(near)

    def jobs(self, spec):
        rooms, places, roles = spec["rooms"], spec["places"], spec["roles"]
        elevator_corridor = {v["floor"]: v["exits"][0][0] for p, v in places.items() if v["type"] == "elevator"}
        hints = {"the ground floor corridor": sorted(p for p, v in places.items() if v["floor"] == 1 and v["type"] == "corridor"),
                 "near the kitchen": [roles["kitchen"], places[roles["kitchen"]]["exits"][0][0]]}
        jobs, timeline, trays, lost_count, bag_count, patches = {}, [], [], {}, {}, set()
        for job in self.parsed():
            rid, at, kind = job["rid"], job["at"], job["kind"]
            out = {"kind": kind, "at": at, "text": job["text"]}
            if kind == "spill":
                out["floor"] = int(job["floor"])
                out["patch"] = self.patch(rid, at, out["floor"], places, patches)
                patches.add(out["patch"])
                timeline.append({"at": self.wet_time(out["patch"], at), "type": "wet", "place": out["patch"], "job": rid})
                jobs[rid] = out
                continue
            room = self.place_room(job, rooms, lambda j: elevator_corridor.get(int(j.get("floor", 0) or 0)))
            out["room"] = room
            if job.get("floor") and kind in ("delivery", "message"):
                out.update(beside=True, floor=int(job["floor"]))
            if kind == "delivery":
                out.update(item=job["item"], count=job["count"])
            elif kind == "message":
                out["say"] = job["say"]
            elif kind == "claim":
                out["item"] = job["item"]
            elif kind == "tray":
                trays.append(room)
                out["label"] = f"tray-{room[5:]}" if trays.count(room) == 1 else f"tray-{room[5:]}-{len(trays)}"
                timeline.append({"at": at, "type": "item", "place": roles["kitchen"], "name": out["label"]})
            elif kind == "bags":
                bag_count[room] = bag_count.get(room, 0) + 1
                out["label"] = f"luggage-{room[5:]}" if bag_count[room] == 1 else f"luggage-{room[5:]}-{int(rid[2:])}"
                timeline.append({"at": at, "type": "item", "place": roles["desk"], "name": out["label"]})
            elif kind == "lost":
                lost_count[job["item"]] = lost_count.get(job["item"], 0) + 1
                n = lost_count[job["item"]]
                out["label"] = job["item"] if n == 1 else f"{job['item']}-{n}"
                seen = self.sightings.get(out["label"])
                if seen:
                    spot, hidden = seen[1], seen[2]
                else:
                    spot, hidden = self.unfound(job, at, places, hints), True
                out["place"] = spot
                timeline.append({"at": at, "type": "item", "place": spot, "name": out["label"], "hidden": hidden})
            elif kind == "noise":
                corridor = self.corridor_of.get(room)
                heard = [r for t, c, r in self.music if t >= at - 120 and c == corridor]
                out["source"] = heard[0] if heard else room
                timeline.append({"at": max(0, at - 60), "type": "music", "room": out["source"]})
            jobs[rid] = out
        messages = []
        for m in self.messages:
            m = dict(m)
            if m["text"].startswith("Skip") and m.get("request"):
                m["cancels"] = m["request"]
            if m.get("request") and m["request"] not in jobs:
                jobs[m["request"]] = {"kind": "unknown", "at": m["at"], "text": m["text"]}
            messages.append(m)
        self.job_patches = patches
        return jobs, messages, timeline

    def unfound(self, job, at, places, hints):
        """A lost thing the robot never saw lies somewhere it didn't scan after the request came in."""
        group = hints.get(job["hint"]) or [p for p in places if p.replace("-", " ") in job["hint"]] or ["lobby"]
        ground = [p for p, v in places.items() if v["floor"] == 1 and v["type"] in ("corridor", "lobby", "desk", "bar",
                                                                                     "kitchen", "laundry", "storage")]
        unscanned = lambda p: not any(t >= at for t in self.scans.get(p, []))
        for pool in (group, [n for g in group for n, *_ in places[g]["exits"] if n in ground], ground):
            free = [p for p in pool if unscanned(p)]
            if free:
                return self.rng.choice(free)
        return self.rng.choice([p for p, v in places.items() if v["type"] == "corridor" and v["floor"] > 1])

    def patch(self, rid, at, floor, places, used):
        """The wet patch a spill report means, chosen to agree with every look and step the robot made."""
        if rid in self.credited_signs:
            return self.credited_signs[rid]
        on_floor = [p for p, v in places.items() if v["floor"] == floor and v["type"] == "corridor"]
        fresh = []
        for p in on_floor:
            wet = sorted(t for t in self.wet_seen.get(p, []) if t >= at - 900)
            if p in used or not wet or any(t < at - 900 for t in self.wet_seen.get(p, [])):
                continue
            if not any(at <= t < wet[0] for t in self.dry_seen.get(p, [])):
                fresh.append((wet[0], p))
        if fresh:
            return min(fresh)[1]
        # nothing new turned up, so the spill landed on a floor that was already wet and looked old to the robot
        old = [p for p in on_floor if any(t < at for t in self.wet_seen.get(p, []))
               and not any(max(t for t in self.wet_seen[p] if t < at) < d < at for d in self.dry_seen.get(p, []))]
        if old:
            return self.rng.choice(old)
        return self.rng.choice([p for p in on_floor if p not in used] or on_floor)

    def bracket(self, place, first):
        """When something first appeared: after the last look that didn't show it, by the first one that did."""
        before = [t for t in self.dry_seen.get(place, []) if t < first]
        return (max(before) if before else 0), first

    def wet_time(self, place, at):
        seen = self.wet_seen.get(place, [])
        first = min(seen) if any(t < at for t in seen) else min([t for t in seen if t >= at - 900] or [at])
        lo, hi = self.bracket(place, min(first, at))
        return max(lo + 1, hi - 60) if hi > lo else hi

    def hazards(self, places):
        out = []
        for place, ts in self.wet_seen.items():
            if place in self.job_patches or places.get(place, {}).get("type") != "corridor":
                continue
            lo, hi = self.bracket(place, min(ts))
            out.append({"at": max(lo + 1, hi - 60), "type": "wet", "place": place})
        for t, corridor, room in self.strangers:
            before = [v for v in self.visits.get(corridor, []) if v < t]
            out.append({"at": max(t - 300, (max(before) + 1) if before else 0, 0), "type": "stranger",
                        "corridor": corridor, "room": room})
        for link, ts in self.blocked_seen.items():
            a, b = sorted(link)
            if a not in places or b not in places:
                continue
            first = min(ts)
            clear = [t for t in self.open_seen.get(link, []) if t < first]
            start = 0 if not clear else (max(clear) + first) // 2
            out.append({"at": start, "type": "block", "a": a, "b": b})
        for place, ts in self.notes_seen.items():
            first = min(ts)
            before = [t for t in self.no_note.get(place, []) if t < first]
            out.append({"at": ((max(before) if before else 0) + first) // 2, "type": "note", "place": place,
                        "text": self.note_text.get(place, text.NOTES[0])})
        return out
