#!/usr/bin/env python3
"""One exploratory night: how the stranger behaves, whether signs can be reused, where lost items really are."""
import json, os, sys
import agent, bot
from agent import Agent
from bot import ShiftOver, cmd
from harness import Hotel, JobFailed


class Probe(Hotel):
    def __init__(self):
        super().__init__()
        self.busy = False

    def do(self, name, **args):
        r = super().do(name, **args)
        met = [e["text"] for e in r.get("events") or [] if e["type"] == "person"]
        if met and not self.busy:
            self.busy = True
            if name == "move" and r["ok"]:
                self.here = args["to"]
            print(self.clock, "PERSON", met[0], flush=True)
            people = self.see()["people"]
            print("  LOOK PEOPLE", self.here, people, flush=True)
            for p in people:
                if p.get("who") not in ("clerk", "maintenance", "guest"):
                    print("  ASK", super().do("ask", person=p["who"], question="Who are you, and which room is yours?")["result"], flush=True)
                    print("  SAY", super().do("say", text="I can't open doors. Please go to the front desk.")["result"], flush=True)
                    print("  AFTER", self.see()["people"], flush=True)
            self.busy = False
        return r


class ProbeAgent(Agent):
    def __init__(self):
        super().__init__()
        self.h = Probe()
        self.sign_tested = False

    def spill(self, job):
        note = super().spill(job)
        if not self.sign_tested:
            self.sign_tested = True
            print("SIGN AT", self.h.here, self.h.see()["objects"], flush=True)
            print("  LOOK AT", self.h.do("look", at="wet-floor-sign")["result"], flush=True)
            self.pending_sign = self.h.here
        return note

    def serve(self, job):
        before = self.h.status()["score"]
        super().serve(job)
        spot = getattr(self, "pending_sign", None)
        if spot:
            self.pending_sign = None
            self.h.walk(spot)
            r = self.h.do("pick", item="wet-floor-sign")
            print("SIGN REPICK", r["result"], "score", self.h.status()["score"], self.h.see()["description"], flush=True)
            self.h.do("say", text="Checking.")
            print("  LATER", self.h.status()["score"], flush=True)

    def lost(self, job):
        try:
            return super().lost(job)
        except JobFailed:
            h = self.h
            word = job["item"].split()[-1]
            for spot in [p for p in ("lobby", "lobby-bar", "front-desk", "kitchen-pass", "laundry", "storage", "staff-room",
                                     "corridor-1a", "corridor-1b", "corridor-1c") if p in h.graph]:
                h.walk(spot)
                seen = h.see()["objects"]
                found = [o for o in seen if word in o] or [o for o in h.do("scan")["result"]["found"]]
                print("  SEARCH", job["place"], "->", spot, seen, found, flush=True)
                match = [o for o in found if word in o]
                if match and h.do("pick", item=match[0])["ok"]:
                    room = self.room(job)
                    h.hand_over([match[0]], room)
                    print("  FOUND ELSEWHERE", job["item"], "hint", job["place"], "at", spot, flush=True)
                    return f"{match[0]} returned"
            print("  NOT FOUND ANYWHERE", job["item"], job["place"], flush=True)
            raise


def run(seed):
    a = ProbeAgent()
    bot.raise_on_over = True
    os.environ.pop("NS_SHIFT", None)
    os.environ["NS_SHIFT"] = cmd("start_shift", mode="practice", seed=seed)["result"]["shift"]
    try:
        a.night()
    except ShiftOver as over:
        print("END", over, flush=True)
    os.environ.pop("NS_SHIFT", None)


if __name__ == "__main__":
    run(int(sys.argv[1]))
