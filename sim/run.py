#!/usr/bin/env python3
"""Run the unchanged agent on simulated nights, one process per night, and summarise.

python3 -m sim.run validate [seeds]       replay recorded nights and compare with logs/v6-val (default 40-45)
python3 -m sim.run random <first> <last>  random hotels; --vary 0|1|2, --jobs N, --log to keep logs/sim/requests.jsonl
"""
import argparse, collections, json, os, statistics, subprocess, sys, time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).parent.parent
OUT = ROOT / "logs" / "sim"
KINDS = ["delivery", "tray", "bags", "lost", "check", "noise", "message", "spill", "claim"]
# agent.py as is, with its decision log kept out of the real logs/decisions.jsonl
AGENT = """import os, sys, harness, agent
from pathlib import Path
harness.DECISIONS = Path("logs/sim/decisions.jsonl") if os.environ.get("NS_SIM_LOG") == "1" else Path(os.devnull)
print([__import__(os.environ.get("NS_AGENT_MOD", "agent")).run(int(sys.argv[1]))])"""


def night(label, seed, sim, env=None):
    """One agent run in its own process. Returns the agent's run file, moved under logs/sim/<label>/."""
    env = {**os.environ, "NS_SIM": sim, "NS_LLM": os.environ.get("NS_SIM_LLM", "0"), **(env or {})}
    out = subprocess.run([sys.executable, "-c", AGENT, str(seed)], cwd=ROOT, env=env, capture_output=True, text=True)
    folder = OUT / label
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"out-{seed}.txt").write_text(out.stdout + out.stderr)
    produced = OUT / f"run-{seed}.json"
    if out.returncode or not produced.exists():
        return None
    produced.replace(folder / f"run-{seed}.json")
    return json.loads((folder / f"run-{seed}.json").read_text())


def by_kind(run):
    pts = collections.Counter()
    for e in run["log"]:
        pts[e["kind"]] += e["gain"]
    return pts


def validate(seeds):
    print("seed  real   sim   diff  served real/sim  points by kind real/sim")
    diffs = []
    for seed in seeds:
        real = json.loads((ROOT / "logs" / "v6-val" / f"run-{seed}.json").read_text())
        sim = night("replay", seed, f"replay:{seed}", {"NS_SIM_LOG": "1"})
        if sim is None:
            print(seed, "crashed, see", OUT / "replay" / f"out-{seed}.txt")
            continue
        a, b = real["end"]["score"], sim["end"]["score"]
        diffs.append((b - a) / a)
        ka, kb = by_kind(real), by_kind(sim)
        kinds = " ".join(f"{k} {ka[k]}/{kb[k]}" for k in KINDS)
        print(f"{seed:4} {a:5} {b:5} {100 * (b - a) / a:+5.1f}%  {real['end']['requests_served']:3}/{sim['end']['requests_served']:<3}"
              f"        {kinds}")
    if diffs:
        print(f"mean difference {100 * statistics.mean(diffs):+.1f}%, mean absolute {100 * statistics.mean(map(abs, diffs)):.1f}%")


def sweep(first, last, vary, jobs, log):
    label = f"random-v{vary}"
    env = {"NS_SIM_VARY": str(vary), "NS_SIM_LOG": "1" if log else "0"}
    started = time.time()
    with ThreadPoolExecutor(jobs) as pool:
        runs = dict(zip(range(first, last + 1), pool.map(lambda s: night(label, s, "auto", env), range(first, last + 1))))
    took = time.time() - started
    good = {s: r for s, r in runs.items() if r}
    scores = [r["end"]["score"] for r in good.values()]
    kinds = collections.Counter()
    for r in good.values():
        kinds.update(by_kind(r))
    summary = {"label": label, "nights": len(good), "crashed": [s for s, r in runs.items() if not r],
               "mean": round(statistics.mean(scores)), "sd": round(statistics.pstdev(scores)), "min": min(scores),
               "max": max(scores), "served": round(statistics.mean(r["end"]["requests_served"] for r in good.values()), 1),
               "seconds": round(took, 1), "per_kind": {k: round(kinds[k] / len(good)) for k in KINDS},
               "ends": collections.Counter(r["end"]["reason"] for r in good.values()),
               "scores": {s: r["end"]["score"] for s, r in good.items()}}
    (OUT / f"summary-{label}-{first}-{last}.json").write_text(json.dumps(summary, indent=1))
    print(json.dumps({k: v for k, v in summary.items() if k != "scores"}))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["validate", "random"])
    ap.add_argument("seeds", nargs="*", type=int)
    ap.add_argument("--vary", type=int, default=1)
    ap.add_argument("--jobs", type=int, default=os.cpu_count())
    ap.add_argument("--log", action="store_true")
    a = ap.parse_args()
    if a.mode == "validate":
        validate(a.seeds or range(40, 46))
    else:
        sweep(a.seeds[0], a.seeds[1], a.vary, a.jobs, a.log)
