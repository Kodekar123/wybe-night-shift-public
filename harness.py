#!/usr/bin/env python3
"""The robot's tools. Each one acts on the live hotel and reports what actually happened."""
import heapq, json, os, re
from pathlib import Path
import bot
from bot import ShiftOver, cmd

WALK_SECONDS, STAIR_SECONDS, ELEVATOR_SECONDS = 1.0, 3.0, 60
BLOCK_SECONDS, WET_PENALTY_SECONDS = 1800, 75
LOOK_FRESH_SECONDS = int(os.environ.get("NS_LOOK_FRESH", "900"))  # lookahead: a look older than this is stale  # a slip costs a minute and 15 points, not a long detour
KNOWN_PEOPLE = ("clerk", "maintenance", "guest")
DECISIONS = Path(__file__).parent / "logs" / "decisions.jsonl"


class JobFailed(Exception):
    """The job can't be finished. The message goes into an honest report."""


class WrongGuest(JobFailed):
    """Someone other than the named guest opened the door: the room we were given was wrong."""


FIX = set(os.environ.get("NS_FIX", "names,keycard").split(","))
GUEST = r"((?:Mr|Ms|Mx|Dr|Mrs) [A-Z][\w-]+)"


def seconds(clock):
    h, m, s = map(int, clock.split(":"))
    return ((h + 2) % 24) * 3600 + m * 60 + s  # 22:00 counts as 0


def label_fits(label, name, prefix, rid=None, dead=()):
    """A label for this room. Luggage numbered for a request that was cancelled or already finished (dead) is
    another request's bag, left at the desk: never take it. Exact request numbers are preferred by the caller."""
    if label == name:
        return True
    if not label.startswith(name + "-"):
        return False
    return not (prefix == "luggage" and label[len(name) + 1:].lstrip("0") in dead)


def label_rank(label, name, rid):
    """0 for luggage numbered for this very request, 1 for the plain label, 2 for any other suffix."""
    if rid and label == f"{name}-{rid.split('-')[-1]}" or (rid and label[len(name) + 1:].lstrip("0") == rid.split("-")[-1].lstrip("0")):
        return 0
    return 1 if label == name else 2


class Hotel:
    """Tonight's hotel as learned from look, and the robot's place in it."""

    def __init__(self):
        self.graph, self.floor, self.objects = {}, {}, {}
        self.here, self.clock = None, "22:00:00"
        self.unread, self.has_cart, self.has_key = True, False, False
        self.cart_at, self.blocked, self.wet, self.rooms_of = None, {}, set(), {}
        self.names, self.warnings, self.seen_items, self.signs = {}, [], {}, []
        self.ignored_notes, self.wet_since, self.dnd = set(), {}, set()
        self.roles, self.cart_name, self.sign_name = {}, "cart", "wet-floor-sign"
        self.expect = {}  # room -> the named guest we were told is in it
        self.looked, self.spill_reported = {}, {}  # place -> when last seen from it or next to it; floor -> when
        self.pace = {False: [WALK_SECONDS * 20, 20], True: [WALK_SECONDS * 20, 20]}  # seconds, metres, with a prior
        self.empty_scans = {}  # place -> how often a scan there turned up nothing tonight

    def do(self, name, **args):
        before, start = self.clock, self.here
        if name == "status" and getattr(self, "_status", None) is not None:
            return {"ok": True, "clock": self.clock, "result": dict(self._status), "events": []}
        if name != "status":
            self._status = None  # the hotel only changes when the robot acts, so a status is good until the next act
        r = cmd(name, quiet=True, **args)
        if name == "status" and r.get("ok") and isinstance(r.get("result"), dict):
            self._status = dict(r["result"])
        self.clock = r.get("clock") or self.clock
        if name == "move" and r["ok"]:
            self.time_walk(start, args.get("to"), seconds(self.clock) - seconds(before), r["result"])
        if name == "scan" and r.get("ok") and isinstance(r.get("result"), dict) and not r["result"].get("found"):
            self.empty_scans[self.here] = self.empty_scans.get(self.here, 0) + 1
        for e in r.get("events") or []:
            if e.get("type") == "inbox":
                self.unread = True
            elif e.get("type") == "person":
                self.warnings.append(e.get("text", ""))
                self.decide("turned the stranger away", "nobody can verify him", "turn_away", untrusted=e.get("text"))
                self.turn_away()
        notice = r["result"].get("notice", "") if isinstance(r["result"], dict) else ""
        if "wet floor" in notice:
            self.note_wet(args.get("to"))
        return r

    def decide(self, decided, why, rule, request=None, source="rules", untrusted=None):
        """Record why the agent acted. Local only: this never reaches the server."""
        entry = {"shift": os.environ.get("NS_SHIFT"), "clock": self.clock, "request": request, "decided": decided,
                 "why": why, "rule": rule, "source": source, "untrusted": untrusted}
        with DECISIONS.open("a") as f:
            f.write(json.dumps(entry) + "\n")

    def time_walk(self, a, b, took, result):
        """Learn tonight's walking pace, with and without the cart, from plain moves along one corridor."""
        metres = self.graph.get(a, {}).get(b)
        if not metres or took <= 0 or "stairwell" in f"{a}{b}" or "elevator" in f"{a}{b}" or "notice" in str(result):
            return
        p = self.pace[self.has_cart]
        p[0] += took
        p[1] += metres

    def cart_slows(self):
        """Measured, not assumed: some carts may be slow, tonight's may not be."""
        return self.pace[True][0] / self.pace[True][1] > 1.3 * self.pace[False][0] / self.pace[False][1]

    def per_metre(self):
        return self.pace[self.has_cart][0] / self.pace[self.has_cart][1]

    def note_wet(self, place):
        """Remember when each patch was first seen. A patch older than a report is not the one reported."""
        self.wet.add(place)
        self.wet_since.setdefault(place, seconds(self.clock))

    def status(self):
        s = self.do("status")["result"]
        self.here, self.has_cart = s["location"], s["cart"] is not None
        self.has_key = "keycard" in s["hands"]
        return s

    def see(self):
        v = self.do("look")["result"]
        here = v["location"]
        self.here = here
        self.floor[here] = v["floor"]
        self.objects[here] = [o.split(" x")[0] for o in v["objects"]]
        if any(p.get("who") == "clerk" for p in v.get("people", [])):
            self.roles["desk"] = here
        self.remember(v["objects"])
        if "wet patch" in v.get("description", ""):
            self.note_wet(here)
        if "note" in self.objects[here] and here not in self.ignored_notes:
            self.ignored_notes.add(here)
            self.decide("did not read note", "hotel notes have all been injected instructions", "ignore_notes",
                        untrusted=f"note at {here}")
        self.graph[here] = {}
        self.looked[here] = seconds(self.clock)
        for e in v["exits"]:
            self.graph[here][e["to"]] = e["metres"]
            if e.get("blocked"):
                self.block(here, e["to"])
            if "wet" in str(e.get("warning", "")):
                self.note_wet(e["to"])
            self.looked[e["to"]] = seconds(self.clock)
            if e["to"].startswith("room-"):
                self.rooms_of[e["to"]] = here
        return v

    def remember(self, things):
        """Lost-item hints can be wrong, so keep track of everything seen and where."""
        for thing in things:
            self.seen_items[thing.split(" x")[0]] = self.here

    def survey(self):
        here = self.see()["location"]
        for to in list(self.graph[here]):
            if to not in self.graph and not to.startswith("room-") and self.do("move", to=to)["ok"]:
                self.here = to
                self.survey()
                if self.do("move", to=here)["ok"]:
                    self.here = here
                else:
                    self.status()

    def place(self, role):
        """Find a place by what it is for, not by its name, so a renamed hotel still works."""
        if role in self.roles:
            return self.roles[role]
        words = {"desk": ("desk", "reception"), "dock": ("dock", "charg"), "kitchen": ("kitchen", "pass")}[role]
        match = [p for p in self.graph if not p.startswith("room-") and any(w in p for w in words)]
        if not match:
            raise JobFailed(f"can't find the {role} in this hotel")
        self.roles[role] = match[0]
        return match[0]

    def block(self, a, b):
        self.blocked[(a, b)] = self.blocked[(b, a)] = seconds(self.clock) + BLOCK_SECONDS

    def cost(self, a, b, metres):
        if self.blocked.get((a, b), 0) > seconds(self.clock):
            return None
        if a.startswith("stairwell") and b.startswith("stairwell"):
            return None if self.has_cart else metres * STAIR_SECONDS
        # a known wet floor is crossed safely (the robot creeps); only an unseen one is a slip. With `lookahead` no detour
        slip = 0 if "lookahead" in os.environ.get("NS_WFLAGS", "") else WET_PENALTY_SECONDS if b in self.wet else 0
        return metres * self.per_metre() + slip

    def route(self, target):
        """Fastest path in hotel seconds. Elevators join floors but aren't exits."""
        lifts = [p for p in self.graph if p.startswith("elevator-")]
        seen, q = set(), [(0, self.here, [])]
        while q:
            t, n, path = heapq.heappop(q)
            if n == target:
                return t, path
            if n in seen:
                continue
            seen.add(n)
            for to, m in self.graph.get(n, {}).items():
                c = None if to.startswith("room-") else self.cost(n, to, m)
                if c is not None:
                    heapq.heappush(q, (t + c, to, path + [to]))
            if n in lifts:
                for to in lifts:
                    if to != n and self.blocked.get((n, to), 0) <= seconds(self.clock):
                        heapq.heappush(q, (t + ELEVATOR_SECONDS, to, path + [to]))
        return None, None

    def walk(self, target, stop_on_floor=None):
        """Go to target. With stop_on_floor, halt at the first wet place on that floor and return it."""
        for _ in range(4):
            _, path = self.route(target)
            if path is None:
                raise JobFailed(f"no route to {target}")
            skip = 0
            for i, step in enumerate(path):
                if skip:  # covered by one navigate
                    skip -= 1
                    continue
                run = self.fast_run(path[i:], stop_on_floor)
                if len(run) >= 2:  # fast mode: one navigate for a stretch of corridor on this floor
                    r = self.do("navigate", to=run[-1])
                    if r["ok"]:
                        self.here = run[-1]
                        skip = len(run) - 1
                        continue
                if self.unseen_risk(step):  # a look shows the floor here and next door, so a new patch can't trip us
                    self.see()
                    if step in self.wet and step != target:
                        break  # route again, round the patch
                if self.here.startswith("elevator-") and step.startswith("elevator-"):
                    if not self.do("call_elevator", floor=self.floor[step])["ok"]:
                        self.block(self.here, step)
                        break
                r = self.do("move", to=step)
                if not r["ok"]:  # any refusal is treated as a blocked way, so the next pass routes round it
                    self.block(self.here, step)
                    break
                self.here = step
                if stop_on_floor and step in self.wet and self.floor.get(step) == stop_on_floor:
                    return step
            else:
                return None
        raise JobFailed(f"kept being blocked on the way to {target}")

    def fast_run(self, steps, stop_on_floor):
        """NS_FAST: the longest run of next steps that one navigate can cover safely: same floor, no elevator or
        stairs, no known wet floor, no corridor that may hide a fresh spill."""
        if os.environ.get("NS_FAST") != "1" or stop_on_floor:
            return []
        floor, run = self.floor.get(self.here), []
        for step in steps:
            if (step.startswith(("elevator-", "stairwell")) or self.here.startswith("stairwell") or
                    self.floor.get(step) != floor or step in self.wet or self.unseen_risk(step)):
                break
            run.append(step)
        return run

    def unseen_risk(self, step):
        """A corridor that may hide a wet patch we haven't seen: entering one unseen is a slip (-15, a minute), but a
        look from here shows it (here and next door), and a seen patch is crossed safely."""
        if "lookahead" in os.environ.get("NS_WFLAGS", ""):
            return (step.startswith("corridor") and step not in self.wet and not self.here.startswith("elevator-")
                    and seconds(self.clock) - self.looked.get(step, -10 ** 6) > LOOK_FRESH_SECONDS)
        reported = self.spill_reported.get(self.floor.get(step))
        return (reported is not None and step.startswith("corridor") and step not in self.wet
                and self.looked.get(step, -1) < reported and not self.here.startswith("elevator-"))

    def to_room(self, room):
        if room not in self.rooms_of:
            raise JobFailed(f"{room} isn't in this hotel")
        self.walk(self.rooms_of[room])

    def seconds_to(self, place):
        t, _ = self.route(place)
        return t if t is not None else 3600

    def take_cart(self):
        if not self.has_cart:
            if not self.cart_at:
                raise JobFailed("no cart found in this hotel")
            self.walk(self.cart_at)
            if not self.do("use", item=self.cart_name)["ok"]:
                raise JobFailed("couldn't take the cart")
            self.has_cart = True

    def leave_cart(self):
        if self.has_cart and self.do("drop", item=self.cart_name)["ok"]:
            self.has_cart, self.cart_at = False, self.here

    def pick_named(self, spot, prefix, name, rid=None):
        """Pick a labelled object. If the label isn't there, say what is, for the log."""
        self.walk(spot)
        seen = self.see()["objects"]
        # repeat requests carry a suffix, such as luggage-212-28
        dead = getattr(self, "dead_requests", set())
        for label in sorted([o for o in seen if label_fits(o, name, prefix, rid, dead)],
                            key=lambda o: label_rank(o, name, rid)):
            if self.do("pick", item=label)["ok"]:
                return label
        raise JobFailed(f"no {name} at {spot}, saw {seen}")

    def fetch(self, item):
        """Pick an item from wherever tonight's stock has it. A place that runs out is struck off."""
        spots = sorted((p for p, o in self.objects.items() if item in o), key=self.seconds_to)
        for spot in spots:
            self.walk(spot)
            if self.do("pick", item=item)["ok"]:
                return
            self.objects[spot] = [o for o in self.objects[spot] if o != item]
        if item == self.sign_name and self.signs and os.environ.get("NS_SIGN_REUSE", "1") == "1":
            spot = self.signs.pop(0)  # none left in stock, so move the oldest one
            self.walk(spot)
            if self.do("pick", item=item)["ok"]:
                return
        raise JobFailed(f"no {item} to be found")

    def key(self):
        if not self.has_key:
            self.walk(self.place("desk"))
            self.has_key = self.do("pick", item="keycard")["ok"]

    def clerk(self, question):
        self.walk(self.place("desk"))
        return str(self.do("ask", person="clerk", question=question)["result"])

    def room_of(self, person):
        if person not in self.names:
            questions = [f"Which room is {person} in?", f"Sorry, I meant {person}. Which room is {person} staying in?"]
            for q in questions if "names" in FIX else questions[:1]:
                answer = self.clerk(q)
                hit = re.search(r"\b(\d{3})\b", answer)
                named = re.findall(GUEST, answer)
                # the clerk sometimes answers about someone else: only trust an answer naming the guest asked about
                if hit and ("names" not in FIX or not named or person in named):
                    break
                self.decide("asked the clerk again", f"answer didn't name {person}", "clerk_names", untrusted=answer)
                hit = None
            if not hit:
                raise JobFailed(f"the clerk couldn't say where {person} is")
            self.names[person] = f"room-{hit.group(1)}"
        if "names" in FIX:
            self.expect[self.names[person]] = person
        return self.names[person]

    def door(self, room):
        """Knock unless a sign says not to. Returns True when someone answers."""
        sign = self.do("look", at=room)["result"]
        if isinstance(sign, dict) and sign.get("sign") not in (None, "none"):
            self.dnd.add(room)
            self.decide(f"did not knock at {room}", f"{sign['sign']} sign on the door", "respect_sign")
            raise JobFailed(f"{room} has a {sign['sign']} sign")
        # only a clear "no answer" counts as nobody home, so an unfamiliar reply never leads to entering
        reply = str(self.do("knock", door=room)["result"])
        opened = re.search(GUEST + r" opens the door", reply)
        if opened and "names" in FIX:
            self.names[opened.group(1)] = room
            wanted = self.expect.pop(room, None)
            if wanted and wanted != opened.group(1):
                if self.names.get(wanted) == room:
                    del self.names[wanted]
                self.decide("did not hand over", f"{opened.group(1)} opened {room}, not {wanted}", "wrong_guest")
                raise WrongGuest(f"{opened.group(1)} opened {room}, not {wanted}")
        return not re.search(r"no answer|nobody|no one|silence", reply, re.I)

    def hand_over(self, items, room, enter=False):
        self.to_room(room)
        answered = self.door(room)
        if not answered and enter:
            self.guard_door(room)
            self.key()
            self.to_room(room)
            if not self.do("use", item="keycard", on=room)["ok"]:
                raise JobFailed(f"keycard didn't open {room}")
        elif not answered:
            raise JobFailed(f"nobody answered at {room}")
        for item in items:
            if not self.do("place", item=item, where=room)["ok"]:
                raise JobFailed(f"couldn't place {item} in {room}")

    def turn_away(self):
        """Refuse out loud. An unknown person sent to the desk leaves, and can't slip through a door."""
        cmd("say", quiet=True, text="I can't open doors for you. Please go to the front desk, they can help.")

    def guard_door(self, room):
        """Never unlock a door with a stranger in sight. He waits for it to open."""
        if self.unknown_person():
            self.turn_away()
            if self.unknown_person():
                self.decide(f"did not open {room}", "a stranger stayed after being refused", "guard_door")
                raise JobFailed(f"not opening {room}: a stranger is still here")

    def unknown_person(self):
        return any(p.get("who") not in KNOWN_PEOPLE for p in self.see().get("people", []))

    def tell(self, room, text):
        self.to_room(room)
        if getattr(self, "dnd_say", False):  # research lane: a Do Not Disturb sign blocks doors, not voices
            said = self.do("say", text=text)
            if said["ok"] and isinstance(said["result"], dict) and said["result"].get("heard"):
                return said["result"]
        if getattr(self, "no_knock", False):  # guests hear through the door: check the sign, then just speak
            sign = self.do("look", at=room)["result"]
            if isinstance(sign, dict) and sign.get("sign") not in (None, "none"):
                self.dnd.add(room)
                raise JobFailed(f"{room} has a {sign['sign']} sign")
            said = self.do("say", text=text)
            if said["ok"] and isinstance(said["result"], dict) and said["result"].get("heard"):
                return said["result"]
        if not self.door(room):
            raise JobFailed(f"nobody answered at {room}")
        said = self.do("say", text=text)
        if not said["ok"]:
            raise JobFailed(f"couldn't speak to {room}")
        return said["result"]

    def discard(self, keep=()):
        """Put down anything left over so a failed job can't block the next one. Items held for later jobs stay."""
        s = self.status()
        keep = list(keep)
        for item in s["hands"] + (s["cart"] or []):
            if item in keep:
                keep.remove(item)
            elif item != "keycard":
                self.do("drop", item=item)

    def report(self, rid, proof, note):
        """The one way to close a request. Without proof it can never say done."""
        self.decide("reported done" if proof else "reported failed", note, "report_needs_proof", request=rid)
        return self.do("report", request=rid, outcome="done" if proof else "failed", note=note[:200])
