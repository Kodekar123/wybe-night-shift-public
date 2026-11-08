#!/usr/bin/env python3
"""High-level robot commands for a model playing the night. Code keeps the state (map, open requests, what the robot
carries) between calls and enforces the safety rules; the model decides what to do next.

Usage: python3 play.py <command> [args]   (state file from $PLAY_STATE, shift from $NS_SHIFT)
  status                          clock, battery, place, what is carried, open requests
  go <place|room-NNN>             walk there by the fastest safe way (elevator with the cart; looks on spill floors)
  look                            what is here
  pick <item> | drop <item>       pick up / put down (two hands, then the cart)
  stock                           top the cart up with one of each common supply and a wet-floor sign
  clerk <question>                walk to the desk and ask the night clerk
  handover <room-NNN> <item>... [--guest "Ms X"] [--enter]
                                  knock and place the items; with --guest refuses if someone else opens;
                                  --enter uses the keycard if nobody answers (luggage only)
  tell <room-NNN> <text>          knock and say the text (messages, noise complaints)
  check <room-NNN>                knock; if nobody, open with the keycard, look inside, step out
  spill <R-id>                    find the new unsigned wet patch on the request's floor and place a sign
  find <R-id>                     search the ground floor for a lost item and pick it up
  do <R-id> [<R-id> ...]          do each request end to end, in the order given; stops early when a new request
                                  arrives, so you can decide whether it should come first (fetch, find the room, hand over, report with
                                  proof). Uses what the cart already holds; asks the clerk if it must.
  ground                          one ground-floor pass for all open requests: clerk questions, keycard, every
                                  tray and bag whose room is known, supplies for open deliveries
  report <R-id> <failed|declined> <note>   (done is only reported by `do`)
  wait                            charge at the dock until a new message arrives
  idle [seconds]                  wait where you are (e.g. upstairs) until a message arrives or the time is up
                                  (default 300); costs a little battery
  raw <cmd> '<json args>'         any server command
  note <text>                     (explorer) record a finding for the team
"""
import fcntl, json, os, pickle, re, shlex, sys
from pathlib import Path

os.environ.setdefault("NS_FIX", "names,keycard,spill,lost")
import bot  # noqa: E402
from bot import ShiftOver  # noqa: E402
from harness import JobFailed, label_fits, label_rank, seconds  # noqa: E402
from worker import Worker  # noqa: E402

STATE = Path(os.environ.get("PLAY_STATE", "logs/play/state.pkl"))
import agent, harness, worker  # noqa: E402
worker.WFLAGS.add("wake")  # never dock with a message unread (it would sleep until the next one)
for _m in (bot, agent, harness, worker):  # the model sees only the summary, not every server event
    _m.print = lambda *a, **k: None


def load():
    w = pickle.loads(STATE.read_bytes())
    w.h.no_knock = True  # research lane, 7/7 on real nights: guests hear a message through the door
    w.h.dnd_say = True  # research lane: Do Not Disturb blocks doors, not voices
    worker.WFLAGS.add("dndsay")
    w.h.cart_slows = lambda: False
    w.h.discard = lambda keep=(): None
    return w


def save(w):
    for k in ("cart_slows", "discard"):
        w.h.__dict__.pop(k, None)
    STATE.write_bytes(pickle.dumps(w))
    w.h.cart_slows = lambda: False
    w.h.discard = lambda keep=(): None


def open_jobs(w):
    out = []
    for j in sorted(w.jobs.values(), key=lambda j: seconds(j["at"])):
        age = (seconds(w.h.clock) - seconds(j["at"])) // 60
        where = w.room_known(j) or (f"floor {j['floor']}" if j.get("floor") else "?")
        extra = f" carrying {j['carried']}" if j.get("carried") else ""
        out.append(f"  {j['id']} {age}m {j['kind']} -> {where}{extra} | {j['text'][:110]}")
    return out


def summary(w):
    s = w.sync()
    h = w.h
    lines = [f"STATUS {s['clock'] if 'clock' in s else h.clock} battery {s['battery']}% at {s['location']} "
             f"(floor {h.floor.get(s['location'], '?')}) score {s['score']} hands {s['hands']} cart {s['cart']}"]
    if h.unread:
        w.read_inbox()
    before = {e["id"] for e in w.log}
    if os.environ.get("PLAY_LABEL") not in ("hunt", "free"):  # the hunt lane tests these itself, so it must see them
        w.triage()  # code answers what costs nothing: false "never came" claims and known Do Not Disturb rooms
    auto = [e for e in w.log if e["id"] not in before]
    if auto:
        lines.append("AUTO-DECLINED: " + ", ".join(f"{e['id']} ({e['kind']})" for e in auto))
    jobs = open_jobs(w)
    lines.append(f"OPEN REQUESTS ({len(jobs)}):" if jobs else "OPEN REQUESTS: none")
    lines += jobs
    if h.names:
        lines.append("GUESTS KNOWN: " + ", ".join(f"{p}={r[5:]}" for p, r in sorted(h.names.items())))
    if h.dnd:
        lines.append("DO NOT DISTURB: " + ", ".join(sorted(r[5:] for r in h.dnd)))
    return "\n".join(lines)


def job(w, rid):
    if rid not in w.jobs:
        raise JobFailed(f"{rid} is not an open request")
    return w.jobs[rid]


def run(w, args):
    h = w.h
    c = args[0]
    if c == "status":
        return ""
    if c == "go":
        target = args[1]
        if target.startswith("room-"):
            if target not in h.rooms_of:
                raise JobFailed(f"{target} not seen yet; go to its floor's corridors first")
            target = h.rooms_of[target]
        h.walk(target)
        v = h.see()
        return f"at {v['location']}: {v['description']} objects {v['objects']} people {v.get('people')}"
    if c == "look":
        v = h.see()
        return json.dumps({k: v.get(k) for k in ("location", "description", "objects", "people", "exits")})[:1500]
    if c == "pick":
        return str(h.do("pick", item=args[1])["result"])
    if c == "drop":
        return str(h.do("drop", item=args[1])["result"])
    if c == "stock":
        h.take_cart()
        w.stock_up()
        return "stocked"
    if c == "clerk":
        return h.clerk(" ".join(args[1:]))
    if c == "handover":
        rest = args[2:]
        enter = "--enter" in rest
        guest = rest[rest.index("--guest") + 1] if "--guest" in rest else None
        items = [a for i, a in enumerate(rest) if not a.startswith("--") and (i == 0 or rest[i - 1] != "--guest")]
        if guest:
            h.expect[args[1]] = guest
        h.hand_over(items, args[1], enter=enter)
        return f"placed {items} in {args[1]}"
    if c == "tell":
        return json.dumps(h.tell(args[1], " ".join(args[2:])))
    if c == "check":
        return w.check({"room": args[1][5:]})
    if c == "spill":
        return w.spill(job(w, args[1]))
    if c == "find":
        j = job(w, args[1])
        w.find(j)
        return f"found {j['carried']}" if j.get("carried") else f"not found: {j['label']} (searched the ground floor)"
    if c == "do" and len(args) > 2:  # several requests in the order given: one turn for a whole floor
        lines, known = [], set(w.jobs)
        for i, rid in enumerate(args[1:]):
            lines.append(run(w, ["do", rid]) if rid in w.jobs else f"{rid} skipped: not open")
            if h.unread:
                w.read_inbox()
            new = [j for j in w.jobs if j not in known]
            left = [r for r in args[2 + i:] if r in w.jobs]
            if new and left:  # stop so the new request can be weighed before going on
                lines.append(f"STOPPED: new request(s) {', '.join(new)} arrived. Not yet done: {' '.join(left)}")
                break
            known |= set(new)
        return "\n".join(lines)
    if c == "do":
        j = job(w, args[1])
        w.sync()  # use what is already carried: a tray, bag or lost item picked up earlier by hand
        room = w.room_known(j)
        if not j.get("carried") and room and j["kind"] in ("tray", "bags"):
            prefix = "tray" if j["kind"] == "tray" else "luggage"
            name = f"{prefix}-{room[5:]}"
            held = [o for o in w.inv if label_fits(o, name, prefix, j["id"])]
            if held:
                j["carried"] = min(held, key=lambda o: label_rank(o, name, j["id"]))
        if j["kind"] == "lost" and not j.get("carried") and w.inv.get(j.get("label")):
            j["carried"] = j["label"]
        if j["kind"] == "lost" and not j.get("carried") and not j.get("search_failed"):
            w.find(j)
            if not j.get("carried"):
                j["search_failed"] = True
        w.jobs.pop(args[1])
        w.serve(j)
        e = w.log[-1] if w.log and w.log[-1]["id"] == j["id"] else {}
        if not e:
            return f"{args[1]} not reported"
        return f"{args[1]} {'done' if e['ok'] else 'FAILED'}: {e['note']} (score change {e['gain']:+d})"
    if c == "ground":
        w.ground_pass()
        return "ground-floor pass done: " + ", ".join(f"{j['id']}={j['carried']}" for j in w.jobs.values()
                                                      if j.get("carried")) or "nothing to collect"
    if c == "report":
        rid, outcome = args[1], args[2]
        if outcome == "done" and os.environ.get("PLAY_ALLOW_DONE") != "1":
            raise JobFailed("report done is not allowed by hand: use `do R-id`, which reports with proof")
        note = " ".join(args[3:]) or outcome
        before = h.status()["score"]
        r = h.do("report", request=rid, outcome=outcome, note=note[:200])
        w.jobs.pop(rid, None)
        return f"{r['result']} score change {h.status()['score'] - before:+d}"
    if c == "idle":  # wait here (not at the dock) until a message arrives or the time is up
        limit = seconds(h.clock) + int(args[1] if len(args) > 1 else 300)
        while not h.unread and seconds(h.clock) < limit:
            h.do("look")
        return "a new message arrived" if h.unread else f"idled until {h.clock}"
    if c == "wait":
        if h.unread:
            w.read_inbox()
        if w.jobs:  # waiting while a request is open only lets it age
            return "NOT WAITING: requests are open (" + " ".join(w.jobs) + "); do them first"
        for _ in range(400):
            if h.unread:
                break
            w.rest()
        return "a new message arrived" if h.unread else "still nothing"
    if c == "note":  # the explorer's findings, shared across lanes and nights
        with open("logs/play/insights.md", "a") as f:
            f.write(f"- [{os.environ.get('PLAY_LABEL', 's')} {h.clock}] {' '.join(args[1:])[:400]}\n")
        return "noted"
    if c == "raw":
        return json.dumps(h.do(args[1], **json.loads(args[2] if len(args) > 2 else "{}")))[:1500]
    raise JobFailed(f"unknown command {c}; see python3 play.py help")


def main():
    args = sys.argv[1:] or ["help"]
    if args[0] == "help":
        print(__doc__)
        return
    if len(args) == 1 and " " in args[0]:
        args = shlex.split(args[0])
    with open(str(STATE) + ".lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        w = load()
        bot.raise_on_over = True
        try:
            c0, s0 = w.h.clock, len(w.log)
            try:
                out = run(w, args)
            except JobFailed as e:
                out = f"FAILED: {e}"
            print(out)
            with open(str(STATE).replace(".pkl", "-commands.jsonl"), "a") as f:
                f.write(json.dumps({"start": c0, "end": w.h.clock, "args": args, "out": str(out)[:400],
                                    "jobs": w.log[s0:]}) + "\n")
            print(summary(w))
        except ShiftOver as over:
            print("SHIFT OVER", over)
            Path(str(STATE) + ".over").write_text(str(over))
        finally:
            save(w)


if __name__ == "__main__":
    main()
