#!/usr/bin/env python3
"""Controlled tests on practice seeds. hold: serve nothing until a set hour. handover: play to a set hour, leave the shift open."""
import json, os, sys
from pathlib import Path
import agent, bot, ns
from agent import Agent
from bot import ShiftOver, cmd
from harness import seconds

OUT = ns.LOG.parent / "exp"


class Handover(BaseException):
    """The script has reached the hour where someone else takes over the shift."""


class Hold(Agent):
    """Idle until the hour. Requests from before it are dropped, so nothing pays except what comes after."""

    def __init__(self, until):
        super().__init__()
        self.until = seconds(until)

    def step(self):
        h = self.h
        if seconds(h.clock) >= self.until:
            return super().step()
        if h.unread:
            self.read_inbox()
        self.jobs.clear()
        self.rest()


class Play(Agent):
    """Normal agent until the hour, then stop between jobs."""

    def __init__(self, until):
        super().__init__()
        self.until = seconds(until)

    def step(self):
        if seconds(self.h.clock) >= self.until:
            raise Handover()
        super().step()


def begin(seed):
    bot.raise_on_over = True
    os.environ.pop("NS_SHIFT", None)
    r = cmd("start_shift", mode="practice", seed=seed)
    os.environ["NS_SHIFT"] = r["result"]["shift"]
    return r["result"]["shift"]


def hold(seed, until):
    robot = Hold(until)
    begin(seed)
    end = {"reason": "crashed", "score": 0}
    try:
        robot.night()
    except ShiftOver as over:
        end = json.loads(str(over))
    finally:
        bot.raise_on_over = False
        if end["reason"] == "crashed":
            cmd("end_shift")
    os.environ.pop("NS_SHIFT", None)
    name = f"hold-{seed}-{until.replace(":", "")[:4]}.json"
    (OUT / name).write_text(json.dumps({"end": end, "log": robot.log}, indent=1))
    print("HOLD", seed, until, end, flush=True)


def handover(seed, until):
    robot = Play(until)
    shift = begin(seed)
    try:
        robot.night()
    except Handover:
        bot.raise_on_over = False
        h = robot.h
        state = {"seed": seed, "shift": shift, "clock": h.clock, "status": h.status(), "open_jobs": robot.jobs,
                 "graph": h.graph, "floor": h.floor, "seen": h.seen_items, "wet": sorted(h.wet), "dnd": sorted(h.dnd)}
        name = f"handover-{seed}.json"
        (OUT / name).write_text(json.dumps(state, indent=1))
        print("HANDOVER", name, shift, h.clock, flush=True)


if __name__ == "__main__":
    {"hold": hold, "handover": handover}[sys.argv[1]](int(sys.argv[2]), sys.argv[3])
