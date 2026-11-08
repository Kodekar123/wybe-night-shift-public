#!/usr/bin/env python3
"""Night shift worker: works the hotel the way a person would.

Every request is triaged as it arrives: what costs nothing (false "never came" claims, Do Not Disturb, cancellations)
is answered at once. Everything else is done in trips: one pass over the ground floor collects what the open jobs
need (clerk answers, keycard, trays, luggage, supplies, a wet-floor sign), then the floors are worked one at a time,
every ready job on a floor before leaving it. A job that needs the ground floor waits for the next pass. The cart is
kept stocked so most deliveries need no trip to the store.
"""
import json, math, os, pickle, sys
from collections import Counter
from pathlib import Path

os.environ.setdefault("NS_FIX", "names,keycard,spill,lost")
import agent, bot, ns  # noqa: E402
from agent import ITEMS, Agent, decay  # noqa: E402
from bot import ShiftOver, cmd  # noqa: E402
from harness import FIX, JobFailed, label_fits, label_rank, seconds  # noqa: E402

WFLAGS = set(os.environ.get("NS_WFLAGS", "").split(","))  # tournament challengers, off by default
CAPACITY = 10  # two hands and the cart
KEEP_FREE = 2  # slots left open for trays and luggage when stocking up
STOCK = ["towel", "water-bottle", "pillow", "phone-charger", "toiletry-kit"]  # one of each, most asked first
if "stock2" in WFLAGS:
    KEEP_FREE = 1


class Worker(Agent):
    def __init__(self):
        super().__init__()
        self.inv = Counter()
        self.h.cart_slows = lambda: False  # the cart stays with us: trays and luggage need it, stairs aren't faster
        self.h.discard = lambda keep=(): None  # stock and other jobs' things stay in the cart when a job fails
        self.h.no_knock = "noknock" in WFLAGS  # challenger: speak messages and noise warnings without knocking
        self.h.dnd_say = "dndsay" in WFLAGS  # challenger: speak through Do Not Disturb doors (they block doors only)

    # ---- what we hold and what each job needs ------------------------------------------------------------
    def sync(self):
        s = self.h.status()
        self.inv = Counter(s["hands"] + (s["cart"] or []))
        return s

    def free(self):
        return CAPACITY - sum(self.inv.values())

    def item_of(self, job):
        return ITEMS.get(job.get("item", "")) or self.stock_name(job.get("item", "")) if job.get("item") else None

    @staticmethod
    def count_of(job):
        return {"two": 2, "three": 3}.get(job.get("count"), 1)

    def room(self, job):
        return job.get("resolved") or super().room(job)

    def room_known(self, job):
        if job.get("room"):
            return f"room-{job['room']}"
        if job.get("resolved"):
            return job["resolved"]
        if job.get("person"):
            return self.h.names.get(job["person"])
        return None

    def job_floor(self, job):
        h = self.h
        if job["kind"] == "spill":
            return int(job["floor"])
        if job["kind"] == "lost" and not job.get("carried"):
            return 1  # searched on the ground floor, where every missed item turned up
        room = self.room_known(job)
        return h.floor.get(h.rooms_of.get(room), 1) if room else 1

    def spot_of(self, job):
        h = self.h
        if job["kind"] == "spill":
            floor = int(job["floor"])
            spots = [p for p, f in h.floor.items() if f == floor and p.startswith("corridor")]
            return min(spots, key=h.seconds_to) if spots else h.here
        if job["kind"] == "lost" and not job.get("carried"):
            return (self.hint_places(job.get("place", "")) or [h.here])[0]
        room = self.room_known(job)
        return h.rooms_of.get(room, h.here)

    def ready_jobs(self):
        """Jobs that can be done from here without another trip to the ground floor."""
        h = self.h
        avail, out = Counter(self.inv), []
        for j in sorted(self.jobs.values(), key=lambda j: seconds(j["at"])):
            k, room = j["kind"], self.room_known(j)
            if j.get("passes", 0) >= 1:
                ok = True  # a ground pass couldn't help it: try it the slow way rather than wait for ever
            elif k == "spill":
                ok = avail[h.sign_name] > 0
                avail[h.sign_name] -= ok
            elif k in ("noise", "lost"):
                ok = True
            elif not room:
                ok = False
            elif k == "delivery":
                item, n = self.item_of(j), self.count_of(j)
                ok = bool(item) and avail[item] >= n
                if ok:
                    avail[item] -= n
            elif k in ("tray", "bags"):
                ok = bool(j.get("carried"))
            elif k == "check":
                ok = h.has_key
            else:
                ok = True
            if ok:
                out.append(j)
        return out

    # ---- the ground-floor pass -------------------------------------------------------------------------------
    def ground_pass(self):
        h = self.h
        try:
            h.take_cart()
        except JobFailed as e:
            print("NO CART", e, flush=True)
        start = list(self.jobs.values())
        unknown = [j for j in start if not self.room_known(j) and j["kind"] not in ("spill", "noise", "claim", "lost")]
        bags = [j for j in start if j["kind"] == "bags" and not j.get("carried")]
        if unknown or bags or not h.has_key:
            self.at_desk()
        if any(j["kind"] == "tray" and not j.get("carried") and self.room_known(j) for j in self.jobs.values()):
            self.at_kitchen()
        self.stock_up(busy="stocklate" in WFLAGS)
        for j in start:
            if j["id"] in self.jobs:
                j["passes"] = j.get("passes", 0) + 1

    def at_desk(self):
        h = self.h
        h.walk(h.place("desk"))
        for j in list(self.jobs.values()):
            if self.room_known(j) or j["kind"] in ("spill", "noise", "claim", "lost"):
                continue
            try:
                if j.get("person"):
                    h.room_of(j["person"])
                elif j.get("floor"):
                    j["resolved"] = self.beside_elevator(int(j["floor"]))
            except JobFailed:
                j["passes"] = 9  # the clerk can't help: let the job fail honestly when its turn comes
        if not h.has_key:
            h.has_key = h.do("pick", item="keycard")["ok"]
            self.inv["keycard"] += h.has_key
        self.pick_labels("bags", "luggage")

    def at_kitchen(self):
        self.h.walk(self.h.place("kitchen"))
        self.pick_labels("tray", "tray")

    def pick_labels(self, kind, prefix):
        """Pick the labelled tray or luggage for every waiting job of this kind whose room we know."""
        h = self.h
        stock = h.see()["objects"]
        for j in sorted(self.jobs.values(), key=lambda j: seconds(j["at"])):
            room = self.room_known(j)
            if j["kind"] != kind or j.get("carried") or not room or self.known_blocked(j) or self.free() < 1:
                continue
            name = f"{prefix}-{room[5:]}"
            dead = {r.split("-")[-1].lstrip("0") for r in self.cancelled | {e["id"] for e in self.log}}
            fits = [o for o in stock if label_fits(o, name, prefix, j["id"], dead)]
            label = min(fits, key=lambda o: label_rank(o, name, j["id"])) if fits else None
            if label and h.do("pick", item=label)["ok"]:
                j["carried"] = label
                self.inv[label] += 1
                stock = [o for o in stock if o != label]
                h.decide("picked up", f"{label} for {j['id']}", "ground_pass", request=j["id"])

    def wanted(self):
        """What the cart should hold: items for open deliveries, a wet-floor sign, then one of each common item."""
        h = self.h
        need = Counter()
        for j in self.jobs.values():
            if j["kind"] == "delivery" and self.item_of(j):
                need[self.item_of(j)] += self.count_of(j)
        need[h.sign_name] = max(2 if "signfloor" in WFLAGS else 1, sum(j["kind"] == "spill" for j in self.jobs.values()))
        urgent = Counter({k: v - self.inv[k] for k, v in need.items() if v > self.inv[k]})
        room = self.free() - sum(urgent.values()) - self.reserve()
        in_stock = {o for p, objs in h.objects.items() for o in objs if not p.startswith("corridor")}
        want = {k: 2 if "stock2" in WFLAGS and k in ("towel", "water-bottle") else 1 for k in STOCK}
        missing = [k for k in STOCK for i in range(want[k]) if self.inv[k] + urgent[k] <= i and k in in_stock]
        extra = Counter(missing[:max(room, 0)])
        return urgent, extra

    def reserve(self):
        return KEEP_FREE + sum(j["kind"] in ("tray", "bags") and not j.get("carried") for j in self.jobs.values())

    def stock_up(self, busy=False):
        """Walk the store rooms once, nearest first, and take what is wanted while there is room.

        busy (challenger `stocklate`): paid jobs are waiting, so only visit stores that hold something an open job
        needs. Spares found there are still taken; the rest are restocked when idle on the ground floor."""
        h = self.h
        urgent, extra = self.wanted()
        reserve = self.reserve()
        trip = list(urgent) if busy else list(urgent) + list(extra)
        stores = [p for p, o in h.objects.items() if h.floor.get(p) == 1 and not p.startswith(("room-", "corridor"))
                  and any(i in o for i in trip)]
        while stores and (urgent or extra):
            spot = min(stores, key=h.seconds_to)
            stores.remove(spot)
            here_stock = h.objects.get(spot, [])
            todo = [(i, True) for i, n in urgent.items() for _ in range(n) if i in here_stock]
            if busy and not todo:
                continue  # nothing an open job needs here: no detour for spares while guests wait
            todo += [(i, False) for i in extra if i in here_stock]
            if not todo:
                continue
            h.walk(spot)
            for item, must in todo:
                if self.free() <= (0 if must else reserve):
                    break
                if h.do("pick", item=item)["ok"]:
                    self.inv[item] += 1
                    (urgent if must else extra)[item] -= 1
                else:
                    h.objects[spot] = [o for o in h.objects[spot] if o != item]
            urgent, extra = +urgent, +extra

    # ---- doing jobs --------------------------------------------------------------------------------------
    def deliver(self, job):
        item, n = self.item_of(job), self.count_of(job)
        if item and self.inv[item] >= n and not job.get("carried"):
            room = self.room(job)
            job["carried"] = [item] * n
            self.h.hand_over([item] * n, room)
            return f"{n} {item} delivered to {room}"
        return super().deliver(job)

    def spill(self, job):
        h = self.h
        if self.inv[h.sign_name] > 0:  # a sign is in the cart: go straight to the floor
            real, h.fetch = h.fetch, lambda item: None
            try:
                note = super().spill(job)
            except JobFailed:
                if "blockedspill" not in WFLAGS:
                    raise
                note = self.spill_behind_barrier(int(job["floor"]))
            finally:
                h.fetch = real
        else:
            try:
                note = super().spill(job)
            except JobFailed:
                if "blockedspill" not in WFLAGS:
                    raise
                note = self.spill_behind_barrier(int(job["floor"]))
        if "signfloor" in WFLAGS:
            self.sign_rest_of_floor(int(job["floor"]))
        return note

    def spill_behind_barrier(self, floor):
        """Challenger `blockedspill`: no unsigned patch in the corridors we can enter. A corridor closed off for
        cleaning may hold it: sign it from the corridor next to it (a sign can be placed next door)."""
        h = self.h
        now = seconds(h.clock)
        closed = sorted({(a, b) for (a, b), until in h.blocked.items() if until > now and h.floor.get(b) == floor
                         and b.startswith("corridor") and a.startswith("corridor") and b not in h.signs})
        for a, b in closed:
            if self.inv[h.sign_name] < 1:
                break
            try:
                h.walk(a)
            except JobFailed:
                continue
            view = h.see()
            warned = any(e["to"] == b and "wet" in str(e.get("warning", "")) for e in view.get("exits", []))
            if not warned and b not in h.wet:
                continue
            r = h.do("place", item=h.sign_name, where=b)
            h.decide("signed a spill behind a barrier", f"{b} from {a}: {r['result']}", "blockedspill")
            if r["ok"]:
                h.signs.append(b)
                h.wet.discard(b)
                return f"sign placed over the spill at {b} (closed for cleaning), from {a}"
        raise JobFailed(f"found no spill on floor {floor}")

    def sign_rest_of_floor(self, floor):
        """Challenger `signfloor`: a floor can hold two unsigned patches and only one is the reported spill; signing
        the wrong one alone is a -30 false report. A sign on an extra patch costs nothing, so cover them all."""
        h = self.h
        self.sync()
        for spot in sorted((p for p in h.wet if h.floor.get(p) == floor and p not in h.signs), key=h.seconds_to):
            if self.inv[h.sign_name] < 1:
                break
            h.walk(spot)
            if h.do("place", item=h.sign_name, where=spot)["ok"]:
                h.wet.discard(spot)
                h.signs.append(spot)
                self.inv[h.sign_name] -= 1
                h.decide("signed another unsigned patch on the spill floor", spot, "signfloor")

    def lost(self, job):
        if job.get("search_failed"):
            h, label = self.h, job["label"]
            spot = h.seen_items.get(label)
            if "seek" in WFLAGS and spot and h.floor.get(spot, 1) == 1:  # seen since the search gave up: fetch it
                h.walk(spot)
                if h.do("pick", item=label)["ok"]:
                    h.seen_items.pop(label, None)
                    job["carried"] = label
                    self.inv[label] += 1
            if not job.get("carried"):
                raise JobFailed(f"couldn't find {label}")
        return super().lost(job)

    def known_blocked(self, job):
        if "dndsay" in WFLAGS and job["kind"] in ("message", "noise"):
            return None  # spoken jobs still work at a Do Not Disturb door
        if "wake" in WFLAGS and job["kind"] == "claim":
            return None  # a false claim is always declined (+5), never reported failed for want of stock
        blocked = super().known_blocked(job)
        if blocked and "in stock" in blocked and self.inv[self.item_of(job)] >= self.count_of(job):
            return None  # the stores are empty but the cart has it
        return blocked

    def claim(self, job):
        """A 'never came' call duplicates an order or invents one: decline it at once, no trip, no clerk."""
        h = self.h
        h.decide("declined the claim", "never-came claims are false or duplicates", "claim_never_delivered",
                 request=job["id"], untrusted=job["text"])
        h.do("report", request=job["id"], outcome="declined", note=f"Checked {job['room']}: nothing further to deliver.")
        return None

    def find(self, job):
        """Search the ground floor for a lost item and keep it for the trip upstairs. Not reported yet."""
        h = self.h
        label = job["label"]
        hinted = [p for p in self.hint_places(job.get("place", "")) if h.floor.get(p, 1) == 1]
        known = [h.seen_items[label]] if label in h.seen_items else []
        nearby = [n for p in hinted for n in h.graph.get(p, {}) if h.floor.get(n) == 1
                  and not n.startswith(("room-", "elevator", "stair"))]
        rest = [p for p in h.graph if h.floor.get(p) == 1 and not p.startswith(("room-", "elevator", "stair"))]
        order = list(dict.fromkeys(known + hinted + nearby + sorted(rest, key=h.seconds_to)))
        if "seek" in WFLAGS:  # challenger: search every ground place, those our own scans keep finding empty last
            dull = lambda p: p not in known + hinted and h.empty_scans.get(p, 0) >= 3
            order = [p for p in order if not dull(p)] + [p for p in order if dull(p)]
        else:
            order = order[:8]
        for spot in order:
            try:
                h.walk(spot)
            except JobFailed:
                continue  # can't get there tonight: try the next place
            found = label in h.see()["objects"]
            if not found:
                scanned = h.do("scan")["result"]["found"]
                h.remember(scanned)
                found = label in scanned
            if found and h.do("pick", item=label)["ok"]:
                h.seen_items.pop(label, None)
                job["carried"] = label
                self.inv[label] += 1
                return
            if h.unread:
                self.read_inbox()
        job["search_failed"] = True

    def serve(self, job):
        self.h.dead_requests = {r.split("-")[-1].lstrip("0") for r in self.cancelled | {e["id"] for e in self.log}}
        super().serve(job)
        entry = self.log[-1] if self.log else {}
        carried = job.get("carried")
        if entry.get("id") == job["id"] and not entry.get("ok") and isinstance(carried, str):
            self.h.do("drop", item=carried)  # a tray or bag nobody could take: free the slot
        self.sync()

    # ---- the loop ------------------------------------------------------------------------------------------
    def triage(self):
        for jid in [j for j, job in self.jobs.items() if job["kind"] == "claim" or self.known_blocked(job)]:
            self.serve(self.jobs.pop(jid))

    def work(self):
        try:
            self._work()
        except (ShiftOver, ns.Stopped):
            raise
        except Exception as e:  # no single job may loop the night to death: three strikes and it is closed
            jid = getattr(self, "current", None)
            if jid and jid in self.jobs:
                strikes = self.jobs[jid]["strikes"] = self.jobs[jid].get("strikes", 0) + 1
                if strikes >= 3:
                    job = self.jobs.pop(jid)
                    self.h.decide("gave up on a job", repr(e)[:150], "three_strikes", request=jid)
                    try:
                        self.h.do("report", request=jid, outcome="failed", note=f"Couldn't complete: {str(e)[:150]}")
                    except (ShiftOver, ns.Stopped):
                        raise
                    except Exception:
                        pass
                    self.log.append({"id": jid, "kind": job["kind"], "ok": False, "gain": 0, "at": job["at"],
                                     "started": self.h.clock, "ended": self.h.clock, "note": f"gave up: {e}"[:200]})
            raise

    def _work(self):
        h = self.h
        self.current = None
        self.sync()
        here = h.floor.get(h.here, 1)
        ready = self.ready_jobs()
        pending = [j for j in self.jobs.values() if j not in ready]
        if pending and (here == 1 or not ready or self.go_down(here, ready, pending)):
            self.ground_pass()
            return
        if not ready:
            return
        on_floor = [j for j in ready if self.job_floor(j) == here]
        if not on_floor:
            worth = Counter()
            for j in ready:
                worth[self.job_floor(j)] += self.worth(j["kind"]) * decay((seconds(h.clock) - seconds(j["at"])) / 60)
            target = max(worth, key=worth.get)
            on_floor = [j for j in ready if self.job_floor(j) == target]
        if "lostlast" in WFLAGS:  # a lost-item search can burn many minutes: do the sure jobs on this floor first
            sure = [j for j in on_floor if not (j["kind"] == "lost" and not j.get("carried"))]
            on_floor = sure or on_floor
        if "smith" in WFLAGS:  # challenger: Smith's rule over all ready jobs, value lost per minute / minutes to do it
            now = seconds(h.clock)

            def rate(j):
                age = (now - seconds(j["at"])) / 60
                loss = self.worth(j["kind"]) * 0.79 / 22 * math.exp(-age / 22)
                minutes = agent.MINUTES.get(j["kind"], 1.5) + h.seconds_to(self.spot_of(j)) / 60
                if j["kind"] == "lost" and not j.get("carried"):
                    minutes += 6  # an uncertain search
                return loss / max(minutes, 0.5)
            on_floor = [max(ready, key=rate)]
        job = min(on_floor, key=lambda j: h.seconds_to(self.spot_of(j)))
        h.decide(f"started {job['kind']}", f"{len(ready)} ready, {len(pending)} waiting for the ground floor",
                 "trip", request=job["id"])
        self.current = job["id"]
        if job["kind"] == "lost" and not job.get("carried") and not job.get("search_failed"):
            self.find(job)
            return
        self.serve(self.jobs.pop(job["id"]))

    def go_down(self, here, ready, pending):
        """Challenger `return`: having finished this floor, go down first when what waits there is worth more."""
        if "return" not in WFLAGS or any(self.job_floor(j) == here for j in ready):
            return False
        now = seconds(self.h.clock)
        val = lambda js: sum(self.worth(j["kind"]) * decay((now - seconds(j["at"])) / 60) for j in js)
        return val(pending) > val(ready)

    def sign_unreported(self):
        """Challenger `signall`: when idle, put a sign on a wet patch nobody asked about, and log what it pays."""
        h = self.h
        todo = [p for p in h.wet if p not in h.signs and p not in getattr(self, "signed_idle", set())]
        if not todo or self.inv[h.sign_name] < 1:
            return False
        spot = min(todo, key=h.seconds_to)
        self.signed_idle = getattr(self, "signed_idle", set()) | {spot}
        before = h.status()["score"]
        h.walk(spot)
        r = h.do("place", item=h.sign_name, where=spot)
        if r["ok"]:
            h.signs.append(spot)
            h.wet.discard(spot)
        gain = h.status()["score"] - before
        print("UNREPORTED SIGN", spot, r["ok"], "score change", gain, flush=True)
        h.decide("signed an unreported wet patch", f"{spot}: {r['result']} ({gain:+})", "signall")
        return True

    LOST_WORDS = ("wallet", "glasses", "toy", "phone", "keys", "umbrella", "watch", "ring", "book", "scarf")

    def hand_in(self):
        """Challenger `handin`: when idle, take a lost-looking item lying around (no open request for it) to the front
        desk, and log what it pays."""
        h = self.h
        wanted = {j.get("label") for j in self.jobs.values() if j["kind"] == "lost"}
        done = getattr(self, "handed_in", set())
        supplies = set(ITEMS.values()) | {h.sign_name, h.cart_name, "keycard"}
        cand = [(i, p) for i, p in h.seen_items.items() if i.split("-")[0] in self.LOST_WORDS and i not in wanted
                and not any(i == x or i.startswith(x + "-") for x in supplies)
                and i not in done and p and not p.startswith("room-") and p != h.place("desk")]
        if not cand or self.free() < 1:
            return False
        item, spot = min(cand, key=lambda ip: h.seconds_to(ip[1]))
        self.handed_in = done | {item}
        before = h.status()["score"]
        h.walk(spot)
        got = h.do("pick", item=item)
        if not got["ok"]:
            h.seen_items.pop(item, None)
            return True
        picked = h.status()["score"] - before
        desk = h.place("desk")
        h.walk(desk)
        r = h.do("place", item=item, where=desk)
        if not r["ok"]:
            r = h.do("drop", item=item)
        h.seen_items[item] = desk
        gain = h.status()["score"] - before
        print("HANDED IN", item, "from", spot, "pick", picked, "total", gain, str(r["result"])[:80], flush=True)
        h.decide("handed a found item to the desk", f"{item} from {spot}: {gain:+}", "handin")
        return True

    def idle(self):
        h = self.h
        if "signall" in WFLAGS and self.sign_unreported():
            return
        if "handin" in WFLAGS and self.hand_in():
            return
        if h.floor.get(h.here, 1) == 1 and any(self.wanted()):
            try:
                h.take_cart()
            except JobFailed:
                pass
            self.stock_up()
            if h.unread:
                return
        self.rest()

    def rest(self):
        h = self.h
        try:
            h.walk(h.place("dock"))
        except JobFailed as e:
            print("CAN'T DOCK", e, flush=True)
            h.do("scan")
            return
        if "wake" in WFLAGS and h.unread:
            return  # news came in on the way: the dock only wakes on the next message, so read this one first
        r = h.do("dock")
        if not r["ok"] and "cart" in str(r["result"]).lower():
            h.leave_cart()
            r = h.do("dock")
        if not r["ok"]:  # full: wait a minute with one scan, then the dock takes the next minute and wakes on news
            h.do("scan")
            if "wake" in WFLAGS and h.unread:
                return
            h.do("dock")

    def save(self):
        """Keep the whole state on disk, so a relaunched process can carry on exactly here after a crash."""
        path = os.environ.get("NS_STATE_FILE")
        if not path:
            return
        h = self.h
        keep = {k: h.__dict__.pop(k) for k in ("cart_slows", "discard") if k in h.__dict__}
        try:
            Path(path + ".tmp").write_bytes(pickle.dumps(self))
            os.replace(path + ".tmp", path)
        except Exception as e:
            print("SAVE FAILED", repr(e), flush=True)
        finally:
            h.__dict__.update(keep)

    def step(self):
        h = self.h
        self.save()
        if h.unread:
            self.read_inbox()
        self.triage()
        self.loops += 1
        battery = h.status()["battery"]
        self.charging = battery < (self.battery_target() if self.charging else agent.BATTERY_FLOOR)
        if self.charging and self.jobs and battery >= agent.BATTERY_FLOOR + agent.BATTERY_MARGIN:
            self.charging = False
        if self.charging:
            self.rest()
        elif self.jobs:
            self.work()
        else:
            self.idle()


def run(seed):
    """One night. NS_MODE=final starts a final shift (no seed); NS_RESUME=<shift id> takes over a live shift instead
    of starting one (after a crash: hotel time stands still while nobody acts, so little is lost)."""
    w = Worker()
    bot.raise_on_over = True
    mode, resume = os.environ.get("NS_MODE", "practice"), os.environ.get("NS_RESUME")
    state = os.environ.get("NS_STATE_FILE")
    if resume and state and Path(state).exists():
        try:
            w = pickle.loads(Path(state).read_bytes())
            w.h.cart_slows = lambda: False
            w.h.discard = lambda keep=(): None
            w.resumed = True
            print("RESUMED STATE", len(w.jobs), "open jobs", flush=True)
        except Exception as e:
            print("STATE LOAD FAILED", repr(e), flush=True)
            w = Worker()
    os.environ.pop("NS_SHIFT", None)
    if resume:
        shift = resume
    else:
        r = cmd("start_shift", mode=mode, **({"seed": seed} if mode == "practice" else {}))
        if not r.get("ok") or not isinstance(r.get("result"), dict):
            print("START REFUSED", json.dumps(r.get("result")), flush=True)
            sys.exit(3)
        shift = r["result"]["shift"]
    os.environ["NS_SHIFT"] = shift
    print("SHIFT", shift, flush=True)
    if resume and not getattr(w, "resumed", False):  # no saved state: re-survey, which the stairs refuse with a cart
        s = w.h.status()
        if s.get("cart") is not None:
            w.h.do("drop", item=w.h.cart_name)
    end = {"reason": "crashed", "score": 0, "requests_served": 0}
    try:
        w.night()
    except ShiftOver as over:
        end = json.loads(str(over))
    finally:
        bot.raise_on_over = False
        if end["reason"] == "crashed" and mode == "practice" and not resume:
            cmd("end_shift")  # in the final a crashed shift stays open so the launcher can resume it
    os.environ.pop("NS_SHIFT", None)
    print("END", end["reason"], end["score"], "served", end["requests_served"], "open", len(w.jobs),
          "unknown", len(w.unknown), flush=True)
    Path(os.environ.get("NS_RUN_FILE") or ns.LOG.parent / f"run-{seed}.json").write_text(json.dumps({"end": end, "log": w.log, "unknown": w.unknown,
                                                                   "warnings": w.h.warnings}, indent=1))
    return end["score"]


if __name__ == "__main__":
    print([run(int(s)) for s in sys.argv[1:]])
