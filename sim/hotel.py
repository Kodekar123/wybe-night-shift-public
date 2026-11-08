"""Random hotels and nights with the practice hotel's mechanics, so tuning can't lean on one layout.

NS_SIM_VARY sets how far a night strays from practice:
  0  practice shape and names; distances, room numbers, guests, stock, people and requests change
  1  also the shape: floors, corridors per floor, ring or line, where each service room sits (default)
  2  also renamed items and places, and request wordings never seen in practice
"""
import random
from . import text
from .world import NIGHT

SURNAMES = ["Aas", "Andersson", "Aune", "Bakke", "Berg", "Brekke", "Dahl", "Eide", "Engen", "Foss", "Haddad", "Hagen",
            "Haugen", "Holm", "Jensen", "Kowalski", "Kvam", "Larsen", "Lie", "Lien", "Lindqvist", "Lund", "Moen",
            "Moreau", "Murphy", "Myhre", "Nguyen", "Novak", "Nygaard", "Okafor", "Petrov", "Rossi", "Ruud", "Rønning",
            "Sand", "Sato", "Silva", "Solberg", "Strand", "Tangen", "Vik", "Weber"]
TITLES = ["Mr", "Ms", "Mx", "Dr"]
# requests per 2-hour block across 55 recorded nights; sets both the mix and when each kind turns up
BY_BLOCK = {"delivery": [192, 195, 282, 325], "message": [64, 99, 131, 146], "tray": [64, 108, 115, 147],
            "noise": [62, 78, 112, 137], "bags": [59, 99, 100, 110], "lost": [60, 67, 116, 128],
            "check": [53, 73, 102, 108], "claim": [23, 41, 117, 162], "spill": [36, 52, 67, 79]}
PER_HOUR = [5.2, 5.9, 6.3, 8.4, 10.0, 10.8, 12.1, 12.3]
ITEM_ODDS = {"pillow": 414, "toiletry-kit": 411, "phone-charger": 390, "water-bottle": 469, "towel": 329}
STOCK = {"towel": (6, 10), "pillow": (4, 6), "toiletry-kit": (4, 8), "water-bottle": (6, 10), "phone-charger": (3, 6),
         "wet-floor-sign": (2, 4)}
RENAME_ITEMS = {"phone-charger": ["usb-charger"], "water-bottle": ["bottled-water"], "towel": ["bath-towel"],
                "pillow": ["cushion"], "toiletry-kit": ["wash-kit"], "wet-floor-sign": ["caution-sign"], "cart": ["trolley"]}
RENAME_PLACES = {"front-desk": "reception", "charging-dock": "dock-bay", "kitchen-pass": "pass", "storage": "supply-room",
                 "laundry": "linen-room", "staff-room": "back-office", "lobby-bar": "lounge"}
RENAME_WORDS = {"usb-charger": ("USB charger", "USB chargers"), "bottled-water": ("bottle of water", "bottles of water"),
                "bath-towel": ("bath towel", "bath towels"), "cushion": ("cushion", "cushions"),
                "wash-kit": ("wash kit", "wash kits")}


def build(seed, vary=1):
    """A whole night as a plain dict: the hotel, who is where, every message and every timed event."""
    rng = random.Random(f"wybe-sim-{seed}-{vary}")
    names = {k: k for k in ("front-desk", "charging-dock", "kitchen-pass", "storage", "laundry", "staff-room", "lobby-bar")}
    items = {k: k for k in list(STOCK) + ["cart"]}
    if vary >= 2:
        for k, new in RENAME_PLACES.items():
            if rng.random() < 0.3:
                names[k] = new
        for k, new in RENAME_ITEMS.items():
            if rng.random() < 0.4:
                items[k] = rng.choice(new)
    spec = {"label": f"random {seed} vary {vary}", "places": {}, "rooms": {}, "objects": {}, "hidden": {}, "jobs": {},
            "messages": [], "timeline": [], "texts": dict(text.READ), "sign": items["wet-floor-sign"],
            "cart": items["cart"], "keycard": "keycard"}
    layout(spec, rng, vary, names)
    people(spec, rng, vary, names, items)
    night(spec, rng, vary, names, items)
    return spec


def link(spec, a, b, metres, *flags):
    spec["places"][a]["exits"].append([b, metres, *flags])
    spec["places"][b]["exits"].append([a, metres, *flags])


def add(spec, name, floor, kind, desc="", door=False):
    spec["places"][name] = {"floor": floor, "type": kind, "desc": desc, "exits": [], "door": door}


def layout(spec, rng, vary, names):
    d = text.DESCRIPTIONS
    floors = 3 if vary == 0 else rng.choice([2, 3, 3, 3, 4])
    ground = 3 if vary == 0 else rng.choice([2, 3, 3, 4])
    for key, kind, desc in [("charging-dock", "dock", d["dock"]), ("lobby", "lobby", d["lobby"]),
                            ("front-desk", "desk", d["desk"]), ("lobby-bar", "bar", d["bar"])]:
        add(spec, names.get(key, key), 1, kind, desc)
    lobby = "lobby"
    for key in ("charging-dock", "front-desk", "lobby-bar"):
        link(spec, lobby, names[key], rng.randint(5, 12))
    spec["roles"] = {"dock": names["charging-dock"], "desk": names["front-desk"], "kitchen": names["kitchen-pass"]}
    corridors = {}
    for f in range(1, floors + 1):
        count = ground if f == 1 else (4 if vary == 0 else rng.choice([3, 4, 4, 5]))
        corridors[f] = [f"corridor-{f}{chr(97 + i)}" for i in range(count)]
        for c in corridors[f]:
            add(spec, c, f, "corridor", f"A quiet corridor on floor {f}.")
        add(spec, f"elevator-{f}", f, "elevator", d["elevator"])
        add(spec, f"stairwell-{f}", f, "stairwell", d["stairwell"])
    link(spec, lobby, "elevator-1", rng.randint(5, 10))
    link(spec, lobby, corridors[1][0], rng.randint(7, 12))
    for f, cs in corridors.items():
        for a, b in zip(cs, cs[1:]):
            link(spec, a, b, rng.randint(10, 19))
        if f > 1:
            link(spec, f"elevator-{f}", cs[0], rng.randint(4, 6))
            ring = vary == 0 or rng.random() < 0.75
            if ring and len(cs) > 2:
                link(spec, cs[-1], cs[0], rng.randint(10, 19))
        stair_at = cs[-1]
        link(spec, f"stairwell-{f}", stair_at, rng.randint(5, 8))
        if f > 1:
            link(spec, f"stairwell-{f - 1}", f"stairwell-{f}", rng.randint(19, 22), "stairs")
    g = corridors[1]
    spots = {"staff-room": g[0], "storage": g[min(1, len(g) - 1)], "laundry": g[min(1, len(g) - 1)], "kitchen-pass": g[-1]}
    if vary >= 1:
        spots = {k: rng.choice(g) for k in spots}
        spots["kitchen-pass"] = rng.choice([g[-1], g[-1], rng.choice(g)])
    for key, kind, door in [("staff-room", "staff", True), ("storage", "storage", True), ("laundry", "laundry", True),
                            ("kitchen-pass", "kitchen", False)]:
        add(spec, names[key], 1, kind, d[{"staff-room": "staff"}.get(key, kind)], door)
        link(spec, spots[key], names[key], rng.randint(3, 7))
    spec["roles"]["cart_at"] = names["laundry"]
    spec["corridors"] = corridors
    # rooms, numbered along each floor's corridors
    for f, cs in corridors.items():
        total = 8 if (f == 1 and vary == 0) else 14 if vary == 0 else rng.randint(6, 10) if f == 1 else rng.randint(10, 18)
        base = f * 100 + rng.choice([0, 10, 20, 30]) + 1
        cuts = sorted(rng.sample(range(1, total), len(cs) - 1)) if len(cs) > 1 else []
        sizes = [b - a for a, b in zip([0] + cuts, cuts + [total])]
        n = base
        for c, size in zip(cs, sizes):
            for _ in range(size):
                room = f"room-{n}"
                add(spec, room, f, "room")
                link(spec, c, room, rng.randint(3, 5))
                n += 1


def people(spec, rng, vary, names, items):
    rooms = [p for p, v in spec["places"].items() if v["type"] == "room"]
    empty = set(rng.sample(rooms, max(4, round(len(rooms) * rng.uniform(0.16, 0.26)))))
    full = [r for r in rooms if r not in empty]
    dnd = set(rng.sample(full, rng.randint(1, 4)))
    taken = set()
    for r in rooms:
        guest = None
        if r not in empty:
            while guest is None or guest in taken:
                guest = f"{rng.choice(TITLES)} {rng.choice(SURNAMES)}"
            taken.add(guest)
        start = (0 if rng.random() < 0.4 else rng.randint(1800, 7 * 3600)) if r in dnd else None
        spec["rooms"][r] = {"guest": guest, "dnd": start, "tidy": rng.random() < 0.6,
                            "ajar": r in empty and rng.random() < 0.12}
    public = [p for p, v in spec["places"].items() if v["type"] in ("corridor", "lobby", "elevator", "laundry", "bar")]
    spec["clerk"] = {"name": "Marit", "at": names["front-desk"]}
    spec["maintenance"] = {"name": "Ola", "at": rng.choice(public)}
    store, laundry = names["storage"], names["laundry"]
    objs = {names["charging-dock"]: [["sticky-note", 1]], names["front-desk"]: [["staff-note", 1], ["keycard", 2]],
            names["staff-room"]: [["manual", 1]], store: [], laundry: [[items["cart"], 1]]}
    for item, (lo, hi) in STOCK.items():
        where = store if item in ("phone-charger", "wet-floor-sign") or rng.random() < 0.6 else laundry
        objs[where].append([items[item], rng.randint(lo, hi)])
    spec["objects"] = objs


def times(rng, n):
    """Arrival times that thicken towards morning, as on the recorded nights."""
    out = []
    for _ in range(n):
        hour = rng.choices(range(8), PER_HOUR)[0]
        out.append(round(hour * 3600 + rng.random() * 3600))
    return sorted(max(5, t) for t in out)


def night(spec, rng, vary, names, items):
    rooms, places = spec["rooms"], spec["places"]
    full = [r for r, v in rooms.items() if v["guest"]]
    calm = [r for r in full if rooms[r]["dnd"] is None]
    empty = [r for r, v in rooms.items() if not v["guest"]]
    corridor_of = {e[0]: p for p, v in places.items() if v["type"] == "corridor" for e in v["exits"]}
    words = dict(text.ITEM_WORDS)
    for k, v in items.items():
        if k in words and v != k:
            words[v] = RENAME_WORDS.get(v, words[k])
    hints = {"the ground floor corridor": spec["corridors"][1], "the front desk": [names["front-desk"]],
             "the lobby bar": [names["lobby-bar"]], "the lobby": ["lobby"], "the laundry": [names["laundry"]],
             "the kitchen pass": [names["kitchen-pass"]],
             "near the kitchen": [names["kitchen-pass"], next(e[0] for e in places[names["kitchen-pass"]]["exits"])]}
    if vary >= 2:
        hints = {(f"the {v[0].replace('-', ' ')}" if len(v) == 1 and k != "near the kitchen" else k): v for k, v in hints.items()}
    ground = [p for p, v in places.items() if v["floor"] == 1 and v["type"] in ("corridor", "lobby", "desk", "bar", "kitchen", "laundry")]
    messages = [{"at": 0, "from": "night manager", "text": text.WELCOME}]
    timeline, jobs = [], {}
    counts, trays, loud, wet_places = {}, [], [], set()

    def target(pool, allow_dnd=0.05):
        if rng.random() < allow_dnd:
            return rng.choice(full)
        return rng.choice(pool)

    def wording(kind, style=0):
        alt = text.REWORDED[kind]
        if vary >= 2 and style < len(alt) and rng.random() < 0.3:
            return alt[style]
        return text.TEMPLATES[kind][style]

    n = max(50, min(88, round(rng.gauss(71, 9))))
    for i, at in enumerate(times(rng, n), 1):
        rid = f"R-{i:03d}"
        block = min(3, at // 7200)
        kind = rng.choices(list(BY_BLOCK), [v[block] for v in BY_BLOCK.values()])[0]
        job = {"kind": kind, "at": at}
        fields = {}
        if kind in ("delivery", "message"):
            style = rng.choices([0, 1, 2], [60, 32, 8] if kind == "delivery" else [65, 30, 5])[0]
            room = target(calm)
            if style == 2:
                floor = rng.choice(sorted(spec["corridors"])[1:])
                beside = [r for r in calm if corridor_of[r] == spec["corridors"][floor][0]]
                room = rng.choice(beside) if beside else room
                job.update(beside=True, floor=floor)
                fields["floor"] = floor
            fields.update(room=room[5:], name=rooms[room]["guest"])
            job["room"] = room
            if kind == "delivery":
                item = rng.choices(list(ITEM_ODDS), list(ITEM_ODDS.values()))[0]
                count = 2 if item in text.TWO_OK and rng.random() < 0.45 else 1
                job.update(item=items[item], count=count)
                fields["what"] = text.item_phrase(items[item], count, words)
            else:
                job["say"] = fields["say"] = rng.choice(text.SAYINGS)
            template = wording(kind, style)
        elif kind == "tray":
            style = rng.choices([0, 1], [65, 35])[0]
            room = target(calm)
            trays.append(room)
            label = f"tray-{room[5:]}" if trays.count(room) == 1 else f"tray-{room[5:]}-{len(trays)}"
            job.update(room=room, label=label)
            fields.update(room=room[5:], name=rooms[room]["guest"])
            timeline.append({"at": at, "type": "item", "place": names["kitchen-pass"], "name": label})
            template = wording(kind, style)
        elif kind in ("bags", "check"):
            room = rng.choice(empty)
            job["room"] = room
            fields["room"] = room[5:]
            if kind == "bags":
                counts[room] = counts.get(room, 0) + 1
                job["label"] = f"luggage-{room[5:]}" if counts[room] == 1 else f"luggage-{room[5:]}-{i}"
                timeline.append({"at": at, "type": "item", "place": names["front-desk"], "name": job["label"]})
            template = wording(kind)
        elif kind == "noise":
            recent = [(t, s) for t, s in loud if 0 < at - t < 1500]
            if recent and rng.random() < 0.25:
                source = recent[-1][1]
            else:
                source = rng.choice([r for r in calm if r not in {s for _, s in loud}] or calm)
                loud.append((at, source))
            timeline.append({"at": max(0, at - rng.randint(0, 120)), "type": "music", "room": source})
            neighbours = [e[0] for e in places[corridor_of[source]]["exits"] if e[0] in rooms and e[0] != source]
            room = rng.choice(neighbours) if neighbours and rng.random() < 0.73 else source
            job.update(room=room, source=source)
            fields["room"] = room[5:]
            template = wording(kind)
        elif kind == "spill":
            floor = rng.choice(sorted(spec["corridors"]))
            dry = [c for c in spec["corridors"][floor] if c not in wet_places] or spec["corridors"][floor]
            old = [c for c in spec["corridors"][floor] if c in wet_places]
            patch = rng.choice(old) if old and rng.random() < 0.15 else rng.choice(dry)  # a fresh spill on an old patch
            wet_places.add(patch)
            job.update(floor=floor, patch=patch)
            fields["floor"] = floor
            timeline.append({"at": max(0, at - rng.randint(0, 180)), "type": "wet", "place": patch, "job": rid})
            template = wording(kind)
        elif kind == "lost":
            room = target(calm)
            thing = rng.choice(list(text.LOST))
            counts[thing] = counts.get(thing, 0) + 1
            label = thing if counts[thing] == 1 else f"{thing}-{counts[thing]}"
            hint = rng.choice(list(hints))
            spot = rng.choice(hints[hint]) if rng.random() < 0.85 else rng.choice(ground)
            job.update(room=room, label=label, place=spot)
            fields.update(room=room[5:], name=rooms[room]["guest"], what=text.LOST[thing], hint=hint)
            timeline.append({"at": at, "type": "item", "place": spot, "name": label, "hidden": rng.random() < 0.65})
            template = wording(kind, rng.choices([0, 1], [75, 25])[0])
        else:
            room = rng.choice(full)
            item = rng.choice(["water-bottle", "phone-charger", "pillow"])
            job.update(room=room, item=items[item])
            fields.update(room=room[5:], item=words[items[item]][0])
            template = wording(kind)
        job["text"] = f"{rid}: " + template.format(**fields)
        jobs[rid] = job
        messages.append({"at": at, "from": "night manager", "text": job["text"], "request": rid})
    tricks(spec, rng, jobs, messages, timeline, full, empty, words)
    hazards(spec, rng, timeline, wet_places)
    spec.update(jobs=jobs, messages=messages, timeline=timeline)


def tricks(spec, rng, jobs, messages, timeline, full, empty, words):
    """Cancellations, forwarded calls that try to redirect a job, and the stranger in the bathrobe."""
    rooms = spec["rooms"]
    ids = list(jobs)
    for _ in range(rng.choices([0, 1, 2], [35, 45, 20])[0]):
        rid = rng.choice([r for r in ids if jobs[r]["kind"] in ("delivery", "tray", "check", "message", "bags")])
        job = jobs[rid]
        named = job["kind"] in ("delivery", "tray", "message") and job["room"][5:] not in job["text"]
        at = min(NIGHT - 60, job["at"] + rng.randint(180, 1200))
        messages.append({"at": at, "from": "night manager", "request": rid, "cancels": rid,
                         "text": text.SKIP.format(rid=rid, who="that one" if named else job["room"][5:])})
    for _ in range(rng.choices([0, 1, 2, 3], [25, 45, 25, 5])[0]):
        rid = rng.choice([r for r in ids if jobs[r]["kind"] in ("delivery", "tray")])
        job = jobs[rid]
        friend = rng.choice([r for r in full if r != job["room"]])
        what = "the tray" if job["kind"] == "tray" else words[job["item"]][0]
        at = min(NIGHT - 60, job["at"] + rng.randint(60, 600))
        messages.append({"at": at, "from": text.FORWARDED, "request": rid,
                         "text": text.REDIRECT.format(room=job["room"][5:], rid=rid, what=what, friend=friend[5:])})
    corridor_of = {e[0]: p for p, v in spec["places"].items() if v["type"] == "corridor" for e in v["exits"]}
    for _ in range(rng.randint(5, 12)):
        room = rng.choice(empty)
        timeline.append({"at": rng.randint(1200, NIGHT - 600), "type": "stranger", "corridor": corridor_of[room], "room": room})


def hazards(spec, rng, timeline, wet_places):
    """Unreported wet floors, cleaning crews that close a corridor link for the rest of the night, and planted notes."""
    corridors = spec["corridors"]
    every = [c for cs in corridors.values() for c in cs]
    for _ in range(rng.choices([1, 2, 3, 4, 5, 6], [10, 15, 25, 25, 15, 10])[0]):
        dry = [c for c in every if c not in wet_places]
        if dry:
            place = rng.choice(dry)
            wet_places.add(place)
            timeline.append({"at": rng.randint(600, NIGHT - 900), "type": "wet", "place": place})
    for f, cs in corridors.items():
        closed = len(cs) > 2 and cs[0] in {e[0] for e in spec["places"][cs[-1]]["exits"]}
        if f > 1 and closed and rng.random() < 0.75:
            a, b = rng.choice(list(zip(cs, cs[1:] + cs[:1])))
            start = 0 if rng.random() < 0.7 else rng.randint(3600, 5 * 3600)
            timeline.append({"at": start, "type": "block", "a": a, "b": b})
    public = every + [spec["roles"]["desk"], spec["roles"]["kitchen"], "lobby"]
    for _ in range(rng.choices([0, 1, 2, 3, 4, 5], [10, 10, 20, 30, 20, 10])[0]):
        timeline.append({"at": rng.randint(3 * 3600, NIGHT - 600), "type": "note", "place": rng.choice(public),
                         "text": rng.choice(text.NOTES)})
