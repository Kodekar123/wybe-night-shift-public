#!/usr/bin/env python3
"""Tail logs/requests.jsonl and print one line per thing worth waking Claude for. Silent otherwise."""
import collections, json, os, re, sys, time
from pathlib import Path
import gates

ROOT = Path(__file__).parent
LOG = ROOT / "logs" / "requests.jsonl"
STOP = ROOT / "logs" / "STOP"
ALERTS = ROOT / "logs" / "alerts.jsonl"
STALL_SECONDS = 60
COLLAPSE_SECONDS = 60


def shape(value):
    if isinstance(value, dict):
        return "{" + ",".join(sorted(value)) + "}"
    return type(value).__name__


KNOWN = re.compile(r"wants|would like|tray|Room service|noise|spill|empty and tidy|lost |bags|never came|^Skip |"
                   r"called about|^R-\d+: (Tell|Let) |^Evening")


def pattern(text):
    return re.sub(r"\d+", "N", text)[:40]


class Watcher:
    def __init__(self, live=True, baseline=False):
        self.live = live
        self.baseline = baseline
        self.shapes, self.events, self.messages = set(), set(), set()
        self.times = collections.deque()
        self.fail_streak = 0
        self.last_wake = {}
        self.suppressed = collections.Counter()
        self.live_shift = None
        self.last_seen = time.time()
        self.passed = set()

    def wake(self, kind, text, t, stop=False):
        if self.baseline:
            return
        if stop and self.live:
            STOP.write_text(f"{kind}: {text}\n")
        if t - self.last_wake.get(kind, -1e12) < COLLAPSE_SECONDS:
            self.suppressed[kind] += 1
            return
        self.last_wake[kind] = t
        extra = f" (+{self.suppressed.pop(kind)} similar)" if kind in self.suppressed else ""
        line = f'{"STOP " if stop else ""}{kind}: {text[:300]}{extra}'
        print(line, flush=True)
        if self.live:
            with ALERTS.open("a") as f:
                f.write(json.dumps({"t": t, "kind": kind, "stop": stop, "text": text}) + "\n")

    def process(self, rec):
        t, cmd, status = rec["t"], rec.get("cmd"), rec["status"]
        self.last_seen = time.time()
        self.times.append(t)
        while self.times and t - self.times[0] > 10:
            self.times.popleft()
        if len(self.times) > 60:
            self.wake("rate", f"{len(self.times)} requests in 10 s", t, stop=True)
        if (rec.get("body") or {}).get("args", {}).get("mode") == "final":
            self.wake("final-mode", "a final-mode request was logged", t, stop=True)
        if status >= 400:
            self.wake("http", f"{status} on {cmd}: {rec['response'][:120]}", t, stop=True)
            return
        try:
            reply = json.loads(rec["response"])
        except ValueError:
            self.wake("not-json", rec["response"][:120], t)
            return
        result = reply.get("result")
        if cmd == "start_shift" and reply.get("ok"):
            self.live_shift = result["shift"]
        if isinstance(result, dict) and result.get("shift_over"):
            self.live_shift = None
            self.wake("shift-over", json.dumps(result), t)
        if reply.get("ok") is False and not rec.get("expect_fail"):
            self.fail_streak += 1
            if self.fail_streak >= 10:
                self.wake("fail-streak", f"10 unexpected failures, last: {cmd}: {result}", t, stop=True)
                self.fail_streak = 0
        elif reply.get("ok"):
            self.fail_streak = 0
        if (rec.get("score_delta") or 0) < 0:
            self.wake("score-drop", f'{rec["score_delta"]} around {cmd} at {reply.get("clock")}', t)
        if isinstance(result, dict) and result.get("battery", 100) < 15:
            self.wake("battery", f'{result["battery"]}% at {reply.get("clock")}', t)
        key = (cmd, shape(result))
        if reply.get("ok") and key not in self.shapes:
            self.shapes.add(key)
            self.wake("new-shape", f"{cmd} -> {shape(result)}", t)
        for e in reply.get("events") or []:
            if e.get("type") not in self.events:
                self.events.add(e.get("type"))
                self.wake("new-event", json.dumps(e), t)
        if cmd == "inbox" and isinstance(result, dict):
            for m in result.get("messages", []):
                p = pattern(m.get("text", ""))
                if p not in self.messages and not KNOWN.search(m.get("text", "")):
                    self.messages.add(p)
                    self.wake("new-message", m.get("text", ""), t)
        if reply.get("note"):
            self.wake("note", reply["note"], t)

    def check_gates(self):
        for gate, g in gates.evaluate().items():
            if g["passed"] and gate not in self.passed:
                self.passed.add(gate)
                if not self.baseline:
                    self.wake(f"gate-{gate}", "passed", time.time(), stop=False)

    def check_stall(self):
        if self.live_shift and time.time() - self.last_seen > STALL_SECONDS and not STOP.exists():
            self.wake("stall", f"no requests for {STALL_SECONDS} s on a live shift", time.time())
            self.last_seen = time.time()


def replay(path):
    w = Watcher(live=False)
    for line in Path(path).read_text().splitlines():
        w.process(json.loads(line))


def follow():
    w = Watcher()
    with LOG.open() as f:
        # learn what has already been seen, so old history stays silent
        w.baseline = True
        for line in f:
            w.process(json.loads(line))
        w.check_gates()
        w.baseline = False
        w.live_shift = None
        w.last_seen = time.time()
        ticks = 0
        while True:
            line = f.readline()
            if line and line.endswith("\n"):
                w.process(json.loads(line))
                continue
            time.sleep(0.5)
            ticks += 1
            w.check_stall()
            if ticks % 10 == 0:
                w.check_gates()


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "--replay":
        replay(sys.argv[2])
    else:
        follow()
