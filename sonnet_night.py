#!/usr/bin/env python3
"""Play one practice night with a model deciding and play.py doing the work.

usage: sonnet_night.py <seed> [model]
Starts the shift, learns the map and stocks the cart in code, then runs `claude -p` with the guidelines below until
the shift is over, restarting the model with a fresh summary if its session ends early.
"""
import json, os, pickle, subprocess, sys, time
from pathlib import Path

os.environ.setdefault("NS_FIX", "names,keycard,spill,lost")
import bot, ns  # noqa: E402
from bot import cmd  # noqa: E402
from worker import Worker  # noqa: E402

GUIDE = """You are the night-shift service robot in a hotel, 22:00 to 06:00. YOUR GOAL: the highest possible score
tonight. You are competing against other teams' robots on the same nights; every point counts, so play to win.

How points work (measured over thousands of jobs):
- You earn points only for requests from the night manager that you really complete; `do` reports them with proof.
- Fresh pay by kind: spill 54, lost item 51, bags 44, tray 40, room check 38, noise 30, delivery 28, message 23,
  declining a false "never came" claim 5. Pay rises 7.5% for each hour after 22:00.
- Pay falls with the time from arrival to report: about 5% lost after 1 minute, a fifth after 5, half after 20, and
  never below a fifth. So speed matters, and so does not leaving any request undone.
- Penalties: false or wrong done -20 to -40, wrong guest/item -10 to -90, letting a stranger into a room -80 or more,
  slipping on an unseen wet floor -15, a dead battery -200 and the night ends.
- Hotel time only moves when you act, and every command costs hotel time: wasted moves are lost points.
After every `do`, look at the score change it prints. If a job cost points, work out why and don't repeat it.

You control the robot ONLY through the shell command `python3 play.py <command>`. Run `python3 play.py help` once
to see the commands. Every command prints what happened, then the robot's status and the open requests. Code keeps
the map, the request list and what you carry; trust its STATUS and OPEN REQUESTS lines over your memory.

You decide the strategy: which request next, in what order, when to batch, when to go down, what to carry, when to
give up on something. Code carries out each job correctly. Main commands:
- `do R-id [R-id ...]`: does each request end to end and reports it with proof (fetches what it needs, asks the clerk,
  checks the guest's name at the door, keycard for luggage, wet-floor signs, lost-item search). A list runs in your
  order and stops early when a new request arrives, so you can re-plan.
- `ground`: one pass over the ground floor that collects what all open requests need (clerk answers, keycard, trays,
  luggage, supplies). Requests then show "carrying ...".
- `stock`: top the cart up with common supplies. `wait`: charge until a new message (refused while requests are open).
- `report R-id failed|declined <why>`, and the lower-level commands in `python3 play.py help`.
Code already declines false "never came" calls and requests for rooms with a Do Not Disturb sign (AUTO-DECLINED), and
drops cancelled requests. Every other request earns nothing until it is done.

Facts that matter for strategy (measured on the real hotel):
- Floors are joined by elevators; each floor change costs about a minute (45 s waiting + 15 s ride) whatever the
  distance. Walking is about 1 s per metre; corridors are 5-20 m apart. Trays (kitchen) and luggage (front desk) are
  on the ground floor and need the cart; supplies are in ground-floor storage and laundry; the cart holds 8 things
  plus 2 in hand. Lost items have always turned up on the ground floor.
- Requests come in clusters: about 60% arrive while you are busy, 50-88 per night.
- A failed lost-item search can cost 10+ minutes. Some spill reports are phantoms (no patch; 0 points, no penalty).
- One approach that scored well (our rule-based worker, about 2,200 a night): collect everything for the open requests
  in one ground-floor pass, then do the floors one at a time, every job on a floor before leaving it, with a cart
  kept stocked. It is a baseline to BEAT, not a rule: think about where it wastes time (e.g. idle waiting, keeping
  trays waiting while finishing upstairs, long searches) and do better.
Keep going until a command prints SHIFT OVER. Don't stop to summarise; act. Keep your thinking short."""


def extra_guide():
    """A lane's own addition to the guidelines, e.g. the explorer's brief, from $PLAY_GUIDE_EXTRA."""
    f = os.environ.get("PLAY_GUIDE_EXTRA")
    if not f:
        return ""
    if os.environ.get("PLAY_GUIDE_ONLY") == "1":  # the unframed explorer gets no earlier beliefs either
        return Path(f).read_text()
    found = Path("logs/play/insights.md")
    notes = "\n".join(found.read_text().splitlines()[-60:]) if found.exists() else ""
    return "\n\n" + Path(f).read_text() + "\nEARLIER FINDINGS:\n" + (notes or "(none yet)")


def main(seed, model="claude-sonnet-5-5", resume=None):
    folder = Path("logs/play")
    folder.mkdir(parents=True, exist_ok=True)
    state = folder / f"{os.environ.get('PLAY_LABEL', 's')}{seed}.pkl"
    if resume:
        return play(seed, model, folder, state, resume)
    w = Worker()
    os.environ.pop("NS_SHIFT", None)
    r = cmd("start_shift", mode="practice", seed=seed)
    shift = r["result"]["shift"]
    os.environ["NS_SHIFT"] = shift
    h = w.h
    h.survey()
    h.status()
    stock = {o for v in h.objects.values() for o in v}
    if "cart" not in stock:
        h.cart_name = w.stock_name("housekeeping cart for heavy things") or "cart"
    if "wet-floor-sign" not in stock:
        h.sign_name = w.stock_name("wet floor warning sign") or "wet-floor-sign"
    h.cart_at = next((p for p, o in h.objects.items() if h.cart_name in o), None)
    try:
        h.take_cart()
        w.stock_up()
    except Exception as e:
        print("SETUP", e, flush=True)
    for k in ("cart_slows", "discard"):
        w.h.__dict__.pop(k, None)
    state.write_bytes(pickle.dumps(w))
    play(seed, model, folder, state, shift)


def play(seed, model, folder, state, shift):
    env = {**os.environ, "PLAY_STATE": str(state), "NS_SHIFT": shift}
    over = Path(str(state) + ".over")
    t0 = time.time()
    for attempt in range(8):
        if over.exists():
            break
        status = subprocess.run([sys.executable, "play.py", "status"], env=env, capture_output=True, text=True).stdout
        if over.exists():
            break
        prompt = ("The night is under way. Current state:\n" + status +
                  "\nContinue working. Start with the triage of any open requests.")
        out = subprocess.run(
            ["claude", "-p", prompt, "--model", model, "--append-system-prompt",
             (extra_guide() if os.environ.get("PLAY_GUIDE_ONLY") == "1" else GUIDE + extra_guide()),
             "--allowedTools", "Bash(python3 play.py:*)", "--setting-sources", "", "--strict-mcp-config",
             "--no-session-persistence", "--output-format", "stream-json", "--verbose"],
            env=env, capture_output=True, text=True, timeout=3 * 3600)
        (folder / f"{state.stem}-session{attempt}.jsonl").write_text(out.stdout + out.stderr)
        print(time.strftime("%H:%M"), "session", attempt, "ended", round((time.time() - t0) / 60, 1), "min",
              flush=True)
    os.environ["NS_SHIFT"] = shift
    end = over.read_text() if over.exists() else None
    if not end:
        cmd("end_shift")
    print("END", end, "wall", round((time.time() - t0) / 60, 1), "min", flush=True)
    write_run(seed, state, end)


def write_run(seed, state, end):
    """The night's job log in the same format as agent.py and worker.py, for the same analysis scripts."""
    w = pickle.loads(Path(state).read_bytes())
    end = json.loads(end) if end else {"reason": "crashed", "score": 0, "requests_served": 0}
    Path(os.environ.get("NS_RUN_FILE") or ns.LOG.parent / f"run-{seed}.json").write_text(json.dumps(
        {"end": end, "log": w.log, "unknown": w.unknown, "warnings": w.h.warnings}, indent=1))


if __name__ == "__main__":
    # usage: sonnet_night.py <seed> [--model M] [--resume SHIFT]
    a = sys.argv[1:]
    opt = lambda k, d=None: a[a.index(k) + 1] if k in a else d
    main(int(a[0]), opt("--model", os.environ.get("PLAY_MODEL", "claude-sonnet-5-5")), opt("--resume"))
