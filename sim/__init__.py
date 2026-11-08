"""Offline Night Shift server. ns.call() hands each body here when NS_SIM is set, and nothing goes over the network.

NS_SIM=<seed>          a random hotel built from that seed (see hotel.py, NS_SIM_VARY sets how varied)
NS_SIM=auto            a random hotel from the seed sent with start_shift
NS_SIM=replay:<seed>   the recorded practice night for that seed (see replay.py); replay:auto uses start_shift's seed
"""
import json, os, pickle, uuid
from pathlib import Path
from . import hotel, replay, text
from .world import World, clock

shifts, played = {}, [0]
SAVED = Path(__file__).parent.parent / "logs" / "sim" / "shifts"  # lets one-off commands (bot.py, ns.py) span processes


def saved(shift):
    path = SAVED / f"{shift}.pkl"
    if shift and path.exists():
        world = pickle.loads(path.read_bytes())
        world.from_disk = True
        shifts[shift] = world
        return world
    return None


def save(shift, world):
    SAVED.mkdir(parents=True, exist_ok=True)
    (SAVED / f"{shift}.pkl").write_bytes(pickle.dumps(world))


def spec_for(setting, seed):
    mode, _, value = setting.partition(":") if setting.startswith("replay") else ("random", "", setting)
    n = seed if value in ("", "auto") else int(value)
    if mode == "replay":
        return replay.build(int(n))
    return hotel.build(int(n), int(os.environ.get("NS_SIM_VARY", "1")))


def reply(ok, result, clock=None):
    return {"ok": ok, "clock": clock, "result": result, "events": [], "note": None}


def dump(reply):
    return json.dumps(reply, ensure_ascii=False, separators=(",", ":"))


def handle(body, path="", method="POST", shift=None):
    """Answer one request the way the real server would. Returns (status, json text)."""
    if method == "GET" or body is None:
        return 200, dump({"ok": True, "result": text.BANNER})
    cmd, args = body.get("cmd"), body.get("args") or {}
    world = shifts.get(shift) or saved(shift)
    if cmd == "start_shift":
        if args.get("mode") != "practice":
            return 200, dump(reply(False, "The simulator only runs practice shifts."))
        seed = args.get("seed", 1)
        sid = "sim-" + uuid.uuid4().hex[:24]
        shifts[sid] = World(spec_for(os.environ.get("NS_SIM", "auto"), seed))
        save(sid, shifts[sid])
        played[0] += 1
        return 200, dump(reply(True, {"shift": sid, "mode": "practice", "clock": "22:00:00",
                                      "text": "Send this id as the X-Shift header on every call during the shift.",
                                      "seed": seed}))
    if cmd == "whoami":
        live = sum(1 for w in shifts.values() if not w.over)
        return 200, dump(reply(True, {"team": "Cobol", "live_shifts": live, "shifts_played": played[0]}))
    if cmd == "shifts":
        rows = [{"shift": k, "mode": "practice", "status": "ended" if w.over else "live",
                 **({"score": w.score} if w.over else {"clock": clock(w.t)})} for k, w in shifts.items()]
        return 200, dump(reply(True, {"shifts": rows}))
    if cmd == "leaderboard":
        return 200, dump(reply(True, {"board": "practice (unofficial)", "teams": []}))
    if world is None:
        if cmd == "help":
            topic = args.get("topic")
            return 200, dump(reply(True, text.TEAM_HELP) if topic is None else
                             reply(True, text.TOPICS[topic]) if topic in text.TOPICS else
                             reply(False, f"No help on '{topic}'. Try help."))
        if shift:
            return 200, dump(reply(False, "No shift with that id."))
        return 200, dump(reply(False, text.OFF_DUTY))
    answer = world.handle(cmd, args)
    if getattr(world, "from_disk", False):
        save(shift, world)
    return 200, dump(answer)
