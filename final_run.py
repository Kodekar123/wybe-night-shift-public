#!/usr/bin/env python3
"""Run many nights at once, unattended, and keep each one alive. Built for Sunday's final (10 nights at once).

  final_run.py --practice 832 833 834      dry run: the same code path on practice seeds (at most 4 at once)
  NS_ALLOW_FINAL=1 final_run.py --final 10 the real thing: starts 10 final shifts

Each night is its own worker.py process (champion flags, normal mode, language model on). A refused start is retried
with backoff (shift cap, final not open yet). If a process dies while its shift is still live, the agent is relaunched
on that shift (NS_RESUME) up to 3 times. Everything is logged under logs/final/<label>/.
"""
import json, os, subprocess, sys, time
from pathlib import Path

ROOT = Path(__file__).parent
FLAGS = os.environ.get("FINAL_WFLAGS", "wake,seek,dndsay,signnear,smith,lookahead")
DEADLINE = float(os.environ.get("FINAL_DEADLINE", "0"))  # epoch seconds after which nothing new is started


def live_shifts():
    out = subprocess.run([sys.executable, "ns.py", '{"cmd":"shifts"}'], cwd=ROOT, capture_output=True, text=True).stdout
    try:
        return {s["shift"]: s for s in json.loads(out.split(" ", 1)[1])["result"]["shifts"] if s["status"] == "live"}
    except Exception:
        return None


class Night:
    def __init__(self, name, folder, mode, seed=None):
        self.name, self.folder, self.mode, self.seed = name, folder, mode, seed
        self.proc, self.shift, self.resumes, self.starts, self.done, self.next_try = None, None, 0, 0, False, 0
        self.log = folder / f"{name}.txt"

    def env(self):
        e = {**os.environ, "NS_AGENT": "worker.py", "NS_WFLAGS": FLAGS, "NS_FAST": "0", "NS_LLM": "1",
             "NS_MODE": self.mode, "NS_BUDGET": "6000", "NS_RUN_FILE": str(self.folder / f"run-{self.name}.json"),
             "NS_STATE_FILE": str(self.folder / f"state-{self.name}.pkl")}
        e.pop("NS_RESUME", None)
        if self.shift:
            e["NS_RESUME"] = self.shift
        return e

    def launch(self):
        args = [sys.executable, "worker.py", str(self.seed or 0)]
        self.out = open(self.log, "a")
        self.out.write(f"\n=== {time.strftime('%H:%M:%S')} launch (shift {self.shift}, resume {self.resumes})\n")
        self.out.flush()
        self.proc = subprocess.Popen(args, cwd=ROOT, env=self.env(), stdout=self.out, stderr=subprocess.STDOUT)

    def poll(self, say):
        if self.done or self.proc is None or self.proc.poll() is None:
            return
        code = self.proc.returncode
        self.out.close()
        text = self.log.read_text()
        if not self.shift:
            m = [l for l in text.splitlines() if l.startswith("SHIFT w6-")]
            self.shift = m[-1].split()[1] if m else None
        end = [l for l in text.splitlines() if l.startswith("END ")]
        if code == 3 and not self.shift:  # start refused: try again later
            self.starts += 1
            self.next_try = time.time() + min(30 * self.starts, 120)
            self.proc = None
            say(f"{self.name}: start refused ({text.strip().splitlines()[-1][:120]}); retry {self.starts}")
            return
        if end and "crashed" not in end[-1]:
            self.done = True
            say(f"{self.name}: finished: {end[-1]}")
            return
        live = live_shifts()
        if self.shift and live is not None and self.shift in live and self.resumes < 3:
            self.resumes += 1
            say(f"{self.name}: process ended (code {code}) with shift {self.shift} still live at "
                f"{live[self.shift].get('clock')}: resuming ({self.resumes}/3)")
            self.launch()
            return
        self.done = True
        say(f"{self.name}: ended (code {code}); shift live: {bool(live and self.shift in live)}; {end[-1] if end else 'no END line'}")


def main():
    args = sys.argv[1:]
    if not args or args[0] not in ("--practice", "--final"):
        print(__doc__)
        return
    final = args[0] == "--final"
    if final and os.environ.get("NS_ALLOW_FINAL") != "1":
        sys.exit("refusing: set NS_ALLOW_FINAL=1 to start final shifts")
    label = time.strftime("%m%d-%H%M") + ("-final" if final else "-practice")
    folder = ROOT / "logs" / "final" / label
    folder.mkdir(parents=True, exist_ok=True)
    nights = ([Night(f"final-{i + 1:02d}", folder, "final") for i in range(int(args[1]))] if final else
              [Night(f"seed-{s}", folder, "practice", int(s)) for s in args[1:]])
    report = open(folder / "launcher.txt", "a")

    def say(msg):
        line = f"{time.strftime('%H:%M:%S')} {msg}"
        print(line, flush=True)
        report.write(line + "\n")
        report.flush()

    say(f"starting {len(nights)} nights, flags {FLAGS}, final={final}")
    for i, n in enumerate(nights):
        n.next_try = time.time() + 2 * i  # stagger the starts a little
    while not all(n.done for n in nights):
        for n in nights:
            if not n.done and n.proc is None and time.time() >= n.next_try:
                if DEADLINE and time.time() > DEADLINE:
                    n.done = True
                    say(f"{n.name}: not started, past the deadline")
                    continue
                n.launch()
            n.poll(say)
        time.sleep(2)
    scores = []
    for n in nights:
        f = folder / f"run-{n.name}.json"
        if f.exists():
            scores.append(json.loads(f.read_text())["end"].get("score", 0))
    s = sorted(scores)
    say(f"ALL DONE: {len(scores)} nights, scores {scores}, mean {sum(s) / max(len(s), 1):.0f}, "
        f"mean without worst {sum(s[1:]) / max(len(s) - 1, 1):.0f}")


if __name__ == "__main__":
    main()
