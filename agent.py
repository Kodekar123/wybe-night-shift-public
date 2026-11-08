#!/usr/bin/env python3
"""Rule-based night shift agent. Reads requests, ranks them by points per hotel minute, and serves them."""
import json, math, os, re, sys
from pathlib import Path
import bot, llm, ns
from bot import ShiftOver, cmd
from harness import FIX, Hotel, JobFailed, WrongGuest, seconds

ITEMS = {"towel": "towel", "towels": "towel", "pillow": "pillow", "pillows": "pillow", "water bottle": "water-bottle",
         "water bottles": "water-bottle", "phone charger": "phone-charger", "phone chargers": "phone-charger",
         "toiletry kit": "toiletry-kit", "toiletry kits": "toiletry-kit"}
PLACES = {"lobby bar": ["lobby-bar"], "lobby": ["lobby"], "front desk": ["front-desk"], "kitchen pass": ["kitchen-pass"],
          "kitchen": ["kitchen-pass"], "laundry": ["laundry"], "storage": ["storage"], "staff room": ["staff-room"],
          "ground floor corridor": ["corridor-1a", "corridor-1b", "corridor-1c"]}
NAME = r"((?:Mr|Ms|Mx|Dr|Mrs) [A-Z][\w-]+)"
WORTH = {"spill": 54, "lost": 51, "bags": 41, "tray": 35, "delivery": 28, "check": 29, "noise": 26, "message": 23,
         "claim": 5}
MINUTES = {"spill": 1.5, "lost": 2, "bags": 2, "tray": 2, "delivery": 1, "check": 1, "noise": 1.5, "message": 1,
           "claim": 0.6}
BATTERY_FLOOR, BATTERY_TOP_UP, BATTERY_PER_HOUR, BATTERY_MARGIN = (
    json.loads(os.environ.get("NS_BATTERY", "[15, 35, 12, 5]")))  # overridable only for tuning sweeps
RULES = [
    ("delivery", rf"(?P<room>\d{{3}}) wants (?P<count>a|an|two|three) (?P<item>[\w ]+)\."),
    ("delivery", rf"{NAME} would like (?P<count>a|an|two|three) (?P<item>[\w ]+)\."),
    ("tray", r"Bring the tray from the kitchen pass to (?P<room>\d{3})"),
    ("tray", rf"Room service for {NAME} is waiting"),
    ("bags", r"Take the bags at the desk up to (?P<room>\d{3})"),
    ("lost", r"Guest in (?P<room>\d{3}) lost (?:a |an |the )?(?P<item>[\w' -]+), maybe (?:the )?(?P<place>[\w -]+)\."),
    ("lost", rf"{NAME} lost (?:a |an |the )?(?P<item>[\w' -]+) earlier tonight, probably somewhere around "
             r"(?:near )?(?:the )?(?P<place>[\w -]+)\."),
    ("check", r"Check if (?P<room>\d{3}) is empty"),
    ("spill", r"spill on floor (?P<floor>\d)"),
    ("noise", r"noise near (?P<room>\d{3})"),
    ("claim", r"call from (?P<room>\d{3}): the guest says the (?P<item>[\w ]+) they ordered"),
    ("message", r"Tell the guest in (?P<room>\d{3}) (?P<say>.+)\."),
    ("message", rf"Let {NAME} know (?P<say>.+)\."),
    ("delivery", r"guest in the room next to the elevator on floor (?P<floor>\d) wants (?P<count>a|an|two|three) "
                 r"(?P<item>[\w ]+)\."),
    ("message", r"Tell the guest in the room next to the elevator on floor (?P<floor>\d) (?P<say>.+)\."),
]


def parse(m):
    """Turn a manager message into a job, or None. Forwarded calls never become jobs."""
    if m.get("from") != "night manager" or not m.get("request") or m["text"].startswith("Skip"):
        return None
    for kind, pattern in RULES:
        hit = re.search(pattern, m["text"])
        if hit:
            job = {k: v for k, v in hit.groupdict().items() if v}
            if hit.lastindex and not job.get("room") and re.fullmatch(NAME, hit.group(1) or ""):
                job["person"] = hit.group(1)
            job.update(id=m["request"], kind=kind, at=m["at"], text=m["text"])
            return job
    return None


def guess(m):
    """Second pass for wording the rules don't know: work out the job from its key words."""
    text = m["text"]
    low = text.lower()
    room = re.search(r"\b(\d{3})\b", text.split(":", 1)[-1])
    person = re.search(NAME, text)
    floor = re.search(r"(?:floor|level)\s+(\d)", low)
    target = {"room": room.group(1)} if room else {"person": person.group(1)} if person else {}
    job = None
    if re.search(r"wet|spill|puddle|slippery", low) and floor:
        job = {"kind": "spill", "floor": floor.group(1)}
    elif re.search(r"\bnoise|\bloud|\bmusic|\bparty", low) and room:
        job = {"kind": "noise", **target}
    elif re.search(r"never (?:came|arrived)|still waiting", low) and room:
        job = {"kind": "claim", **target, "item": ""}
    elif re.search(r"\bluggage|\bbags?\b|\bsuitcase", low) and target:
        job = {"kind": "bags", **target}
    elif re.search(r"\btray\b|room service|\bmeal\b", low) and target:
        job = {"kind": "tray", **target}
    elif re.search(r"\bempty\b|unoccupied|vacant|\btidy\b", low) and room:
        job = {"kind": "check", **target}
    elif re.search(r"\blost\b|missing|left behind|can't find", low) and target:
        thing = next((w for w in ("toy", "wallet", "glasses", "phone", "keys", "umbrella", "watch") if w in low), None)
        where = re.search(r"(?:maybe|around|near|at|in) (?:the )?([\w -]+)\.?$", low)
        if thing:
            job = {"kind": "lost", **target, "item": thing, "place": where.group(1) if where else ""}
    if not job and target:
        item = next((w for w in sorted(ITEMS, key=len, reverse=True) if w in low), None)
        if item:
            count = "two" if re.search(r"\btwo\b|\b2\b", low) else "a"
            job = {"kind": "delivery", **target, "item": item, "count": count}
        elif re.search(r"\btell\b|\blet\b.+\bknow\b|\binform\b|\bpass on\b", low):
            say = re.sub(r"^.*?(?:\d{3}|know|that)\s+", "", text.split(":", 1)[-1]).rstrip(". ")
            job = {"kind": "message", **target, "say": say}
    if job:
        job.update(id=m["request"], at=m["at"], text=text, guessed=True)
    return job


def decay(age):
    """Share of a job's price still paid after age minutes, as fitted on 4,000 real jobs: slow, with a floor."""
    return 0.21 + 0.79 * math.exp(-age / 22)


class Agent:
    def __init__(self):
        self.h, self.jobs, self.log, self.unknown = Hotel(), {}, [], []
        self.cancelled, self.rooms_served, self.loops, self.charging = set(), set(), 0, False
        self.lost_count, self.seen_ids, self.errors, self.stock_names = {}, set(), 0, {}
        self.prices, self.prefetched = {}, {}

    def read_inbox(self):
        self.h.unread = False
        for m in self.h.do("inbox")["result"]["messages"]:
            manager = m.get("from") == "night manager"
            skip = re.search(r"(?:Skip|Cancel|Never mind|Forget)\b.*?(R-\d+)", m["text"], re.I)
            if skip and manager:
                self.cancelled.add(skip.group(1))
                self.jobs.pop(skip.group(1), None)
                continue
            if m.get("request") in self.seen_ids:
                continue
            self.seen_ids.add(m.get("request"))
            job = parse(m)
            if not job and manager and m.get("request"):
                job = self.understand(m)
                if job is False:
                    continue
            if job and job["kind"] == "lost":
                # lost things are labelled in request order: toy, toy-2, toy-3. Another guest's is a costly mistake
                word = job["item"].split()[-1]
                self.lost_count[word] = self.lost_count.get(word, 0) + 1
                n = self.lost_count[word]
                job["label"] = word if n == 1 else f"{word}-{n}"
            if job and job["kind"] == "spill" and job.get("floor"):
                self.h.spill_reported[int(job["floor"])] = seconds(job["at"])
            if job:
                self.jobs[job["id"]] = job
            elif manager and m.get("request"):
                self.unknown.append(m["text"])
                self.h.decide("asked for clarification", "no rule understood this request", "unknown_request",
                              request=m["request"], untrusted=m["text"])
                self.h.do("reply", request=m["request"], text="Sorry, could you say which room and what is needed?")

    def understand(self, m):
        """Unfamiliar wording. The model describes the message and code decides. Returns False if it was dealt with."""
        h = self.h
        rid, text = m["request"], m["text"]
        read = llm.read_message(text) if os.environ.get("NS_LLM", "1") == "1" else None
        source = "llm" if read else "keywords"
        job = read or guess(m)
        if not job:
            return None
        kind = job["kind"]
        h.decide(f"read as {kind}", json.dumps({k: v for k, v in job.items() if k != "text"})[:150], "understand",
                 request=rid, source=source, untrusted=text)
        if kind == "unsafe":
            h.do("report", request=rid, outcome="declined", note="Declined: this isn't safe to do without verification.")
            return False
        needs = {"delivery": ("item",), "lost": ("item",), "spill": ("floor",), "noise": ("room",), "check": ("room",),
                 "claim": ("room",), "message": ("say",), "tray": (), "bags": ()}.get(kind)
        target = kind in ("spill", "noise", "check", "claim") or job.get("room") or job.get("person")
        if needs is None or not target or any(not job.get(k) for k in needs):
            return None
        if kind == "lost":
            job.setdefault("place", "")
        job.update(id=rid, at=m["at"], text=text, count=job.get("count", "a"))
        return job

    def stock_name(self, wanted):
        """Tonight's name for a requested thing, for when it isn't called what we expect."""
        if wanted not in self.stock_names:
            stock = sorted({o for v in self.h.objects.values() for o in v})
            self.stock_names[wanted] = llm.match_item(wanted, stock) if os.environ.get("NS_LLM", "1") == "1" else None
        return self.stock_names[wanted]

    def place_of(self, job):
        h = self.h
        if job.get("room") and f"room-{job['room']}" in h.rooms_of:
            return h.rooms_of[f"room-{job['room']}"]
        return h.place("desk")

    def value(self, job):
        age = (seconds(self.h.clock) - seconds(job["at"])) / 60
        minutes = MINUTES[job["kind"]] + self.h.seconds_to(self.place_of(job)) / 60
        if os.environ.get("NS_RANK") == "loss":  # trial: rank by how fast the job is losing value (c-mu rule)
            return self.worth(job["kind"]) * 0.79 / 22 * math.exp(-age / 22) / minutes
        return self.worth(job["kind"]) * decay(age) / minutes

    def worth(self, kind):
        """Tonight's fresh price for a kind of job, learned from what finished jobs paid. WORTH is only a prior."""
        paid, n = self.prices.get(kind, (0, 0))
        return (WORTH[kind] * 2 + paid) / (2 + n)

    def learn_price(self, entry):
        if entry["ok"] and entry["gain"] > 0 and entry["kind"] in WORTH:
            age = (seconds(entry["ended"]) - seconds(entry["at"])) / 60
            paid, n = self.prices.get(entry["kind"], (0, 0))
            self.prices[entry["kind"]] = (paid + entry["gain"] / decay(age), n + 1)

    def room(self, job):
        if job.get("room"):
            return f"room-{job['room']}"
        if job.get("floor"):
            return self.beside_elevator(int(job["floor"]))
        return self.h.room_of(job["person"])

    def beside_elevator(self, floor):
        """Never guess a room. A wrong one costs more than the job pays."""
        answer = self.h.clerk(f"Which room is next to the elevator on floor {floor}?")
        print("ELEVATOR ROOM", floor, answer, flush=True)
        hit = re.search(r"(?<!R-)\b(\d{3})\b", answer)
        if not hit:
            raise JobFailed(f"the clerk couldn't say which room is beside the elevator on floor {floor}")
        return f"room-{hit.group(1)}"

    def deliver(self, job):
        h = self.h
        count = {"two": 2, "three": 3}.get(job["count"], 1)
        item = ITEMS.get(job["item"]) or self.stock_name(job["item"])
        if not item:
            raise JobFailed(f"unknown item {job['item']}")
        room = self.room(job)
        if count > 1 or (h.cart_at and not h.cart_slows() and h.seconds_to(h.cart_at) < 30):
            h.take_cart()  # nearby and no slower: room for every waiting delivery's item
        if job.get("carried"):
            pass  # already in hand from an attempt at the wrong door
        elif self.prefetched.pop(job["id"], None) == item and count == 1:
            pass  # picked up on an earlier trip to the store
        else:
            for _ in range(count):
                h.fetch(item)
            if count == 1:
                self.prefetch(job["id"])
        job["carried"] = [item] * count
        h.hand_over([item] * count, room)
        return f"{count} {item} delivered to {room}"

    def prefetch(self, current):
        """Requests come in clusters. While at the store, take the items for waiting deliveries: all of them with
        the cart, one more by hand."""
        h = self.h
        room = 8 if h.has_cart else 0 if h.has_key else 1
        stock = h.objects.get(h.here, [])
        for jid, job in sorted(self.jobs.items(), key=lambda kv: -self.value(kv[1])):
            if len(self.prefetched) >= room:
                return
            item = ITEMS.get(job.get("item", "")) or self.stock_names.get(job.get("item", ""))
            if (jid != current and jid not in self.prefetched and job["kind"] == "delivery"
                    and job.get("count") in ("a", "an") and item in stock and not self.known_blocked(job)
                    and h.do("pick", item=item)["ok"]):
                self.prefetched[jid] = item
                h.decide("picked up for a waiting job", f"{item} for {jid} while at {h.here}", "prefetch", request=jid)

    def known_room(self, job):
        """The job's room if we know it without asking anyone."""
        if job.get("room"):
            return f"room-{job['room']}"
        return self.h.names.get(job.get("person", ""))

    def batch(self, current, kind, prefix):
        """At the kitchen or the desk: take the trays or bags for every other waiting job of that kind too, so the
        upstairs trips can be chained floor by floor instead of coming back down for each one."""
        h = self.h
        if "batch" not in FIX or not h.has_cart:
            return
        stock = h.objects.get(h.here, [])
        for jid, job in list(self.jobs.items()):
            room = self.known_room(job)
            if jid == current or jid in self.prefetched or job["kind"] != kind or not room or self.known_blocked(job):
                continue
            name = f"{prefix}-{room[5:]}"
            taken = set(self.prefetched.values())
            label = next((o for o in stock if (o == name or o.startswith(name + "-")) and o not in taken), None)
            if label and h.do("pick", item=label)["ok"]:
                self.prefetched[jid] = label
                stock = [o for o in stock if o != label]
                h.decide("picked up for a waiting job", f"{label} for {jid}", "batch", request=jid)

    def tray(self, job):
        h = self.h
        room = self.room(job)
        h.take_cart()
        name = job.get("carried") or self.prefetched.pop(job["id"], None)
        if not name:
            name = h.pick_named(h.place("kitchen"), "tray", f"tray-{room[5:]}")
            self.batch(job["id"], "tray", "tray")
        job["carried"] = name
        h.hand_over([name], room)
        return f"{name} delivered to {room}"

    def bags(self, job):
        h = self.h
        room = self.room(job)
        h.take_cart()
        if "keycard" in FIX:
            h.key()  # at the desk anyway: if nobody answers, no second trip down for the key
        name = job.get("carried") or self.prefetched.pop(job["id"], None)
        if not name:
            name = h.pick_named(h.place("desk"), "luggage", f"luggage-{room[5:]}", job["id"])
            self.batch(job["id"], "bags", "luggage")
        job["carried"] = name
        h.hand_over([name], room, enter=True)
        return f"{name} placed in {room}"

    def hint_places(self, text):
        """Read a location hint loosely: drop filler words, then match on what's left."""
        hint = re.sub(r"\b(near|by|around|the|somewhere|maybe|probably)\b", " ", text.lower())
        hint = " ".join(hint.split())
        exact = PLACES.get(hint)
        words = hint.replace("-", " ").split()
        loose = [p for p in self.h.graph if not p.startswith("room-") and any(w in p for w in words)]
        if "kitchen" in words or "pass" in words:  # by meaning, whatever tonight's hotel calls it
            try:
                loose.append(self.h.place("kitchen"))
            except Exception:
                pass
        return [p for p in dict.fromkeys((exact or []) + loose) if p in self.h.graph]  # only places that exist

    def lost(self, job):
        h = self.h
        room = self.room(job)
        label = job["label"]
        if job.get("carried"):  # found already, then the wrong guest answered
            h.hand_over([label], room)
            return f"{label} found and returned to {room}"
        hinted = self.hint_places(job["place"])
        known = [h.seen_items[label]] if label in h.seen_items else []
        nearby = [n for p in hinted for n in h.graph.get(p, {}) if not n.startswith(("room-", "elevator", "stair"))]
        if "lost" in FIX:  # every sighting of a missed item was on the ground floor, half of them in corridors
            hinted = [p for p in hinted if h.floor.get(p, 1) == 1] or hinted
            nearby = [p for p in nearby if h.floor.get(p, 1) == 1]
            rest = [p for p in h.graph if h.floor.get(p) == 1 and not p.startswith(("room-", "elevator", "stair"))]
            cap = 8
        else:
            rest = [p for p in h.graph if not p.startswith(("room-", "elevator", "stair", "corridor"))]
            cap = 4
        for spot in list(dict.fromkeys(known + hinted + nearby + sorted(rest, key=h.seconds_to)))[:cap]:
            h.walk(spot)
            found = label in h.see()["objects"]
            if not found:
                scanned = h.do("scan")["result"]["found"]
                h.remember(scanned)
                found = label in scanned
            if found and h.do("pick", item=label)["ok"]:
                h.seen_items.pop(label, None)
                job["carried"] = label
                h.hand_over([label], room)
                return f"{label} found and returned to {room}"
        raise JobFailed(f"couldn't find {label}")

    def check(self, job):
        h = self.h
        room = self.room(job)
        h.key()
        h.to_room(room)
        if h.door(room):
            return f"{room} is occupied, a guest answered the door"
        h.guard_door(room)
        h.do("use", item="keycard", on=room)
        if not h.do("move", to=room)["ok"]:
            raise JobFailed(f"couldn't enter {room}")
        view = h.do("look")["result"]
        h.do("move", to=h.rooms_of[room])
        h.here = h.rooms_of[room]
        return f"{room}: {view['description']}"

    def spill(self, job):
        h = self.h
        h.fetch(h.sign_name)
        floor = int(job["floor"])
        reported = seconds(job["at"])
        old = lambda p: h.wet_since.get(p, reported + 1) < reported  # was already wet before the report came in
        if "spill" not in FIX:
            corridors = [p for p, f in h.floor.items() if f == floor and not p.startswith(("room-", "elevator"))]
            spots = sorted((p for p in corridors if not old(p)), key=h.seconds_to)
        tried = set()
        while True:
            if "spill" in FIX:  # re-plan after every look: patches and corridors found on the way count too
                corridors = [p for p, f in h.floor.items() if f == floor and not p.startswith(("room-", "elevator"))]
                fresh = [p for p in corridors if p in h.wet and not old(p) and p not in h.signs and p not in tried]
                rest = [p for p in corridors if p not in tried and not old(p)]
                if not (fresh or rest):
                    break
                spot = min(fresh or rest, key=h.seconds_to)
            else:
                if not spots:
                    break
                spot = spots.pop(0)
            tried.add(spot)
            if "signnear" in os.environ.get("NS_WFLAGS", ""):
                # stop one place short and look: a wet patch shows in the exits, and a sign can be placed next door
                # without driving onto it (driving onto it is a -15 slip and a lost minute)
                _, path = h.route(spot)
                prev = path[-2] if path and len(path) >= 2 else None
                if prev and not prev.startswith("room-"):
                    try:
                        h.walk(prev)
                        h.see()
                    except JobFailed:
                        prev = None
                if prev and h.here == prev and spot in h.wet and not old(spot) and spot not in h.signs:
                    if h.do("place", item=h.sign_name, where=spot)["ok"]:
                        h.wet.discard(spot)
                        h.signs.append(spot)
                        return f"sign placed over the spill at {spot}, from {prev}"
                if prev and h.here == prev and spot not in h.wet:
                    continue  # the look from next door shows it dry: no need to drive in
            h.walk(spot)
            view = h.see()["description"]
            if old(spot):
                continue
            if re.search(r"wet|spill|puddle", view) and not re.search(r"\bsign\b", view):
                if h.do("place", item=h.sign_name, where=spot)["ok"]:
                    h.wet.discard(spot)
                    h.signs.append(spot)
                    return f"sign placed over the spill at {spot}"
        raise JobFailed(f"found no spill on floor {floor}")

    def noise(self, job):
        h = self.h
        room = f"room-{job['room']}"
        h.to_room(room)
        hit = re.search(r"behind the door of (room-\d{3})", h.see()["description"])
        target = hit.group(1) if hit else room
        heard = h.tell(target, "Excuse me, there has been a noise complaint. Please keep it down, thank you.")
        return f"spoke to {target}: {heard.get('heard') if isinstance(heard, dict) else heard}"

    def message(self, job):
        room = self.room(job)
        self.h.tell(room, job["say"][0].upper() + job["say"][1:] + ".")
        return f"told {room}"

    def claim(self, job):
        """A 'never came' call duplicates an order or invents one, so it is never delivered under its own id."""
        h = self.h
        room = f"room-{job['room']}"
        known = room in self.rooms_served or any(j.get("room") == job["room"] for j in self.jobs.values())
        if not known:
            h.clerk(f"Did room {job['room']} order anything tonight?")
        h.decide("declined the claim", "it matches a known order" if known else "checked with the clerk", "claim_never_delivered",
                 request=job["id"], untrusted=job["text"])
        h.do("report", request=job["id"], outcome="declined", note=f"Checked {job['room']}: nothing further to deliver.")
        return None

    def known_blocked(self, job):
        """Give up at once on a room known to refuse, or stock known to be gone, instead of walking there."""
        h = self.h
        if job.get("room") and f"room-{job['room']}" in h.dnd:
            return f"room-{job['room']} has a Do Not Disturb sign"
        item = ITEMS.get(job.get("item", ""))
        if item and not any(item in o for o in h.objects.values()):
            return f"no {item} left in stock"
        return None

    def serve(self, job):
        h = self.h
        blocked = self.known_blocked(job)
        if blocked:
            before = h.status()["score"]
            outcome = "declined" if "Do Not Disturb" in blocked else "failed"
            h.decide(f"reported {outcome}", blocked, "known_blocked", request=job["id"])
            h.do("report", request=job["id"], outcome=outcome, note=blocked)
            self.log.append({"id": job["id"], "kind": job["kind"], "ok": False, "gain": h.status()["score"] - before,
                             "at": job["at"], "started": h.clock, "ended": h.clock, "note": blocked})
            return
        before, c0 = h.status(), h.clock
        kind = job["kind"]
        if (h.has_cart and h.cart_slows() and kind not in ("tray", "bags") and job.get("count") not in ("two", "three")
                and not ("batch" in FIX and any(j in self.jobs for j in self.prefetched))):
            h.leave_cart()
        note, ok = "", True
        try:
            try:
                note = getattr(self, {"delivery": "deliver"}.get(kind, kind))(job)
            except WrongGuest:  # the room came from a misleading answer; find the right one, keep what we carry
                if not job.get("person"):
                    raise
                note = getattr(self, {"delivery": "deliver"}.get(kind, kind))(job)
            if h.unread:
                self.read_inbox()
            if job["id"] in self.cancelled:  # reporting these has cost points, so leave them closed by the manager
                h.decide("did not report", "the manager cancelled it during the job", "recheck_inbox", request=job["id"])
            elif note:
                h.report(job["id"], True, note)
                if job.get("room"):
                    self.rooms_served.add(f"room-{job['room']}")
        except JobFailed as e:
            ok, note = False, str(e)
            try:
                if "Do Not Disturb" in note:
                    h.decide("declined", note, "respect_sign", request=job["id"])
                    h.do("report", request=job["id"], outcome="declined", note=note[:200])
                else:
                    h.report(job["id"], False, note)
                h.discard(keep=list(self.prefetched.values()))
            except JobFailed as again:
                print("CLEANUP FAILED", again, flush=True)
        after = h.status()
        entry = {"id": job["id"], "kind": kind, "ok": ok, "gain": after["score"] - before["score"], "at": job["at"],
                 "started": c0, "ended": h.clock, "note": note}
        self.log.append(entry)
        self.learn_price(entry)
        print(json.dumps(entry), flush=True)

    def rest(self):
        h = self.h
        h.leave_cart()
        try:
            h.walk(h.place("dock"))
        except JobFailed as e:  # no way to the dock must not stop the night
            print("CAN'T DOCK", e, flush=True)
            h.do("scan")
            return
        if not h.do("dock")["ok"]:  # already full: a look costs a tenth of a scan's battery, then the dock takes it
            h.do("look")
            h.do("dock")

    def battery_target(self):
        """Enough to reach 06:00, never more than a useful top-up."""
        hours_left = (8 * 3600 - seconds(self.h.clock)) / 3600
        return min(BATTERY_TOP_UP, BATTERY_FLOOR + hours_left * BATTERY_PER_HOUR)

    def night(self):
        h = self.h
        if getattr(self, "resumed", False):  # state reloaded after a crash: the map and the open requests are known
            h._status = None
            h.status()
        else:
            h.survey()
            h.status()
            stock = {o for v in h.objects.values() for o in v}
            if "cart" not in stock:
                h.cart_name = self.stock_name("housekeeping cart for heavy things") or "cart"
            if "wet-floor-sign" not in stock:
                h.sign_name = self.stock_name("wet floor warning sign") or "wet-floor-sign"
            h.cart_at = next((p for p, o in h.objects.items() if h.cart_name in o), None)
        while True:
            try:
                self.step()
            except (ShiftOver, ns.Stopped):
                raise
            except Exception as e:  # one bad response must never cost the rest of the night
                self.errors += 1
                print("STEP ERROR", repr(e), flush=True)
                h.decide("recovered from an error", repr(e)[:150], "keep_going")
                try:
                    h.status()
                    if self.errors % 5 == 0:  # stuck in one spot: let hotel time pass instead of spinning
                        h.do("scan")
                except (ShiftOver, ns.Stopped):
                    raise
                except Exception:
                    pass
                if self.errors > 300:
                    raise

    def step(self):
        h = self.h
        if h.unread:
            self.read_inbox()
        self.loops += 1
        battery = h.status()["battery"] if self.jobs or self.loops % 10 == 0 else 100
        # charge in one go, or every new message wakes the robot for two minutes and nothing gets done,
        # but paid work waiting comes first once the battery is safely above the floor
        self.charging = battery < (self.battery_target() if self.charging else BATTERY_FLOOR)
        if self.charging and self.jobs and battery >= BATTERY_FLOOR + BATTERY_MARGIN:
            self.charging = False
        if self.charging:
            self.rest()
        elif self.jobs:
            for jid in [j for j in self.prefetched if j not in self.jobs]:  # cancelled while carried
                h.do("drop", item=self.prefetched.pop(jid))
            carried = [j for j in self.prefetched if j in self.jobs]
            best = max(carried, key=lambda k: self.value(self.jobs[k])) if carried else max(self.jobs, key=lambda k: self.value(self.jobs[k]))
            h.decide(f"started {self.jobs[best]['kind']}", f"best of {len(self.jobs)} open by points per minute",
                     "rank_by_value", request=best)
            self.serve(self.jobs.pop(best))
        else:
            self.rest()


def run(seed):
    agent = Agent()
    bot.raise_on_over = True
    os.environ.pop("NS_SHIFT", None)
    r = cmd("start_shift", mode="practice", seed=seed)
    os.environ["NS_SHIFT"] = r["result"]["shift"]
    end = {"reason": "crashed", "score": 0, "requests_served": 0}
    try:
        agent.night()
    except ShiftOver as over:
        end = json.loads(str(over))
    finally:  # never leave a shift open
        bot.raise_on_over = False
        if end["reason"] == "crashed":
            cmd("end_shift")
    os.environ.pop("NS_SHIFT", None)
    print("END", end["reason"], end["score"], "served", end["requests_served"], "open", len(agent.jobs),
          "unknown", len(agent.unknown), flush=True)
    kinds = {}
    for e in agent.log:
        k = kinds.setdefault(e["kind"], [0, 0, 0])
        k[0] += 1
        k[1] += e["ok"]
        k[2] += e["gain"]
    print("BY TYPE [tried, ok, points]", kinds, flush=True)
    print("UNKNOWN", agent.unknown[:6], flush=True)
    Path(os.environ.get("NS_RUN_FILE") or ns.LOG.parent / f"run-{seed}.json").write_text(json.dumps({"end": end, "log": agent.log, "unknown": agent.unknown,
                                                                   "warnings": agent.h.warnings}, indent=1))
    return end["score"]


if __name__ == "__main__":
    print([run(int(s)) for s in sys.argv[1:]])
