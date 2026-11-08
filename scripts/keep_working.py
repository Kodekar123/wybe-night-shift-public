#!/usr/bin/env python3
"""Stop hook: keep Claude working until our fresh-seed mean reaches a realistic share of the board's best night.

The board shows each team's best single night, which sits above its mean: ours about 10% above, and a team with
1,300 shifts more. So the target is TARGET_SHARE of the leader's best, about their real mean. A number in
logs/AUTOPILOT_TARGET overrides it.

Lets the stop through when: the goal is met, it's past Sun 8 Nov 11:30 Oslo time, logs/AUTOPILOT_OFF exists,
a run is in progress (its completion wakes Claude), logs/AUTOPILOT_PAUSE_UNTIL holds a later time, it has blocked 30
times in the last hour, or anything fails.
"""
import json, subprocess, sys, time, urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOGS = ROOT / "logs"
DEADLINE = datetime(2026, 11, 8, 11, 30, tzinfo=timezone(timedelta(hours=1)))
BLOCKS = LOGS / ".autopilot_blocks"
TARGET_SHARE = 0.85
MILESTONE = 150  # tell the user each time the mean passes another step of this size


def ours():
    best = None
    for f in LOGS.glob("summary-*.json"):
        d = json.loads(f.read_text())
        seeds = [r["seed"] for r in d.get("rows", [])]
        if len(seeds) >= 8 and all(20 <= s < 900 for s in seeds) and d.get("mean_minus_worst"):
            best = max(best or 0, d["mean_minus_worst"])
    return best or 0


def board_top():
    """The best night of any other team, fetched fresh each time, so the target follows the board."""
    cache = LOGS / ".board_top"
    try:
        req = urllib.request.Request("https://nightshift-board.wybe.me/data", headers={"User-Agent": "team-cobol/0.2"})
        data = json.loads(urllib.request.urlopen(req, timeout=8).read())
        top = max(p["best"] for p in data["practice"] if p["team"] != "Cobol")
        cache.write_text(str(top))
        return top
    except Exception:  # a network blip shouldn't end the autopilot: use the last value seen
        return int(cache.read_text())


def main():
    (LOGS / ".autopilot_last").write_text(time.strftime("%Y-%m-%d %H:%M:%S"))  # proof the hook is live
    if (LOGS / "AUTOPILOT_OFF").exists() or datetime.now(timezone.utc) > DEADLINE:
        return
    pause = LOGS / "AUTOPILOT_PAUSE_UNTIL"  # epoch seconds: a timed pause, e.g. while the user resets the context
    if pause.exists() and time.time() < float(pause.read_text()):
        return
    if subprocess.run(["pgrep", "-f", "run_set.py"], capture_output=True).returncode == 0:
        return  # a run is going; its waiter will wake Claude
    now = time.time()
    recent = [t for t in (json.loads(BLOCKS.read_text()) if BLOCKS.exists() else []) if now - t < 3600]
    if len(recent) >= 30:
        return
    mine, top = ours(), board_top()
    override = LOGS / "AUTOPILOT_TARGET"
    target = float(override.read_text()) if override.exists() else round(TARGET_SHARE * top)
    if mine >= target:
        return
    BLOCKS.write_text(json.dumps(recent + [now]))
    print(json.dumps({"decision": "block", "reason": (
        f"Autopilot: our best fresh-seed mean minus worst is {mine}; the target is {target:.0f} "
        f"({TARGET_SHARE:.0%} of the leader's best night, {top}, as an estimate of their mean). Keep working "
        "towards a higher mean on unseen nights: start the next real-server experiment (within 4 shifts, under 18 "
        "requests a second), with a harness-tracked background waiter so its end wakes you. Only send the user a "
        f"push notification for a major breakthrough: the mean passing another multiple of {MILESTONE} "
        f"(next {(mine // MILESTONE + 1) * MILESTONE}), or reaching the target. Touch logs/AUTOPILOT_OFF to stop the autopilot.")}))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass  # any failure lets the stop through
