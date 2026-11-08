#!/usr/bin/env python3
"""Log the public leaderboard once a minute. Stops by itself when the results are revealed."""
import json, sys, time, urllib.request
from pathlib import Path

URL = "https://nightshift-board.wybe.me/data"
OUT = Path(__file__).parent / "logs" / "board.jsonl"
INTERVAL = 60
GRACE = 300  # keep logging briefly after the reveal time, in case the reveal runs late


def fetch():
    req = urllib.request.Request(URL, headers={"User-Agent": "team-cobol-board-log/0.1"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)


def main(once=False):
    while True:
        try:
            d = fetch()
        except Exception as e:  # a dropped connection shouldn't end the log
            print("fetch failed:", e, flush=True)
            time.sleep(INTERVAL)
            continue
        with OUT.open("a") as f:
            f.write(json.dumps(d) + "\n")
        if once:
            return
        if d["phase"].get("revealed") or time.time() > d["reveal_at"] + GRACE:
            print("results revealed, stopping", flush=True)
            return
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main("--once" in sys.argv)
