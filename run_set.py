#!/usr/bin/env python3
"""Run the agent on a list of seeds, one process per night, and write a summary. Safe to leave unattended."""
import json, re, statistics, subprocess, sys, time, urllib.request
from pathlib import Path

ROOT = Path(__file__).parent
LOGS = ROOT / "logs"
STOP = LOGS / "STOP"


def online():
    try:
        req = urllib.request.Request("https://nightshift-board.wybe.me/data", headers={"User-Agent": "team-cobol/0.2"})
        urllib.request.urlopen(req, timeout=10).read()
        return True
    except Exception:
        return False


def close_if_live(shift):
    """End the shift this script just ran if it is still open. Other tools on the same key run their own shifts.
    A network failure here must never stop the run."""
    try:
        _close_if_live(shift)
    except Exception as e:
        print(time.strftime("%H:%M"), "couldn't check shift", shift, repr(e)[:80], flush=True)


def _close_if_live(shift):
    out = subprocess.run([sys.executable, "ns.py", '{"cmd":"shifts"}'], cwd=ROOT, capture_output=True, text=True).stdout
    for s in json.loads(out.split(" ", 1)[1])["result"]["shifts"]:
        if s["shift"] == shift and s["status"] == "live":
            subprocess.run([sys.executable, "ns.py", '{"cmd":"end_shift"}'], cwd=ROOT, capture_output=True,
                           env={**__import__("os").environ, "NS_SHIFT": shift})


def summarise(label, seeds):
    rows = []
    for s in seeds:
        path = LOGS / label / f"run-{s}.json"
        if path.exists():
            d = json.loads(path.read_text())
            jobs = len(d["log"])
            rows.append({"seed": s, "score": d["end"]["score"], "jobs": jobs, "served": d["end"]["requests_served"],
                         "per_job": round(d["end"]["score"] / max(jobs, 1), 1), "end": d["end"]["reason"],
                         "failed": sum(not e["ok"] for e in d["log"]), "losses": sum(e["gain"] < 0 for e in d["log"]),
                         "stranded": d["end"]["reason"] == "stranded", "unknown": len(d["unknown"])})
    scores = sorted(r["score"] for r in rows)
    out = {"label": label, "nights": len(rows), "mean": round(statistics.mean(scores)) if scores else None,
           "mean_minus_worst": round(statistics.mean(scores[1:])) if len(scores) > 1 else None,
           "worst": scores[0] if scores else None, "best": scores[-1] if scores else None, "rows": rows}
    (LOGS / f"summary-{label}.json").write_text(json.dumps(out, indent=1))
    return out


def main(label, seeds):
    (LOGS / label).mkdir(exist_ok=True)
    for seed in seeds:
        done = LOGS / label / f"run-{seed}.json"
        for attempt in range(3):
            if done.exists():
                break
            while not online():  # asleep, or between networks
                print(time.strftime("%H:%M"), "offline, waiting", flush=True)
                time.sleep(60)
            if STOP.exists():
                # only a network failure may be cleared automatically; anything else needs a person
                if "599" not in STOP.read_text():
                    print("STOP is set, not continuing:", STOP.read_text(), flush=True)
                    return
                STOP.unlink()
            os = __import__("os")
            produced = LOGS / label / f"tmp-run-{seed}.json"  # each run writes its own file: lanes share seeds
            r = subprocess.run([sys.executable, os.environ.get("NS_AGENT", "agent.py"), str(seed)], cwd=ROOT,
                               capture_output=True, text=True, env={**os.environ, "NS_RUN_FILE": str(produced)})
            started = re.search(r'"shift": "(w6-[0-9a-f]+)"', r.stdout)
            if started:
                close_if_live(started.group(1))
            (LOGS / label / f"out-{seed}.txt").write_text(r.stdout + r.stderr)
            if r.returncode == 0 and produced.exists():
                produced.replace(done)
            print(time.strftime("%H:%M"), "seed", seed, "exit", r.returncode, flush=True)
            if r.returncode and "already running" in r.stdout:
                time.sleep(60)  # the server's cap on live shifts: wait for a slot instead of skipping the seed
        s = summarise(label, seeds)
        print(time.strftime("%H:%M"), "nights", s["nights"], "mean", s["mean"], "mean-worst", s["mean_minus_worst"], flush=True)
    print("FINISHED", flush=True)


if __name__ == "__main__":
    # usage: run_set.py <label> <first> <last>
    main(sys.argv[1], list(range(int(sys.argv[2]), int(sys.argv[3]) + 1)))
