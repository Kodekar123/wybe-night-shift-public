#!/usr/bin/env python3
"""Night Shift client. Every request passes through call(), which paces, logs and honours logs/STOP."""
import fcntl, json, os, sys, time, urllib.error, urllib.parse, urllib.request
import chaos
from pathlib import Path

ROOT = Path(__file__).parent
SIM = os.environ.get("NS_SIM")  # a seed or replay:<seed>: answer from sim/ in-process, never over the network
LOG = ROOT / "logs" / ("sim" if SIM else "") / "requests.jsonl"
STOP = ROOT / "logs" / "STOP"
PACE = ROOT / "logs" / ".last_call"
MIN_INTERVAL = 0.055  # about 18 a second across all our processes; the server refuses above about 20
BUDGET = int(os.environ.get("NS_BUDGET", "4000"))
if not SIM:
    for line in (ROOT / ".env").read_text().splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k, v)

sent = 0
last_score = {}
expect_fail = False  # set by experiments that fail on purpose, so the watcher can ignore them


class Stopped(RuntimeError):
    pass


def halt(reason):
    STOP.write_text(reason + "\n")
    raise Stopped(reason)


def pace():
    """One gap between any two of our requests, across every process: the check and the touch happen under a lock."""
    with open(PACE, "a") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            wait = PACE.stat().st_mtime + MIN_INTERVAL - time.time()
            if wait > 0:
                time.sleep(wait)
            PACE.touch()
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


_conn = None


def send(req):
    """One request over a kept-alive HTTPS connection (a new handshake per call cost about half of each call's time).
    Falls back to a fresh connection once if the kept one has gone stale."""
    global _conn
    import http.client
    u = urllib.parse.urlsplit(req.full_url)
    path = (u.path or "/") + (f"?{u.query}" if u.query else "")
    for attempt in (1, 2):
        try:
            if _conn is None:
                _conn = http.client.HTTPSConnection(u.netloc, timeout=30)
            _conn.request(req.get_method(), path, body=req.data, headers=dict(req.header_items()))
            r = _conn.getresponse()
            return r.status, r.read().decode()
        except (http.client.HTTPException, OSError) as e:
            try:
                _conn.close()
            except Exception:
                pass
            _conn = None
            if attempt == 2:
                return 599, str(e)


def simulate(body, path, method):
    """The NS_SIM path: same logging and chaos layer, no pacing, budget or network."""
    import sim
    global sent
    body = chaos.outgoing(body)
    shift = os.environ.get("NS_SHIFT")
    status, text = sim.handle(body, path, method, shift)
    sent += 1
    if os.environ.get("NS_SIM_LOG", "1") == "1":
        record(time.time(), shift, method, path, body, status, text)
    return status, chaos.incoming(body, text)


def call(body, path="", method="POST"):
    global sent
    if SIM:
        return simulate(body, path, method)
    if STOP.exists():
        raise Stopped(STOP.read_text().strip())
    if sent >= BUDGET and (body or {}).get("cmd") != "end_shift":
        # a runaway night stops itself (and still ends its shift), without stopping every other run
        raise Stopped(f"request budget of {BUDGET} reached in this process")
    if body and body.get("args", {}).get("mode") == "final" and not os.environ.get("NS_ALLOW_FINAL"):
        halt("refused to send a final-mode request")
    body = chaos.outgoing(body)
    url = os.environ["NIGHTSHIFT_URL"].rstrip("/") + "/" + path.lstrip("/")
    headers = {
        "Authorization": "Bearer " + os.environ["NIGHTSHIFT_KEY"],
        "Content-Type": "application/json",
        "User-Agent": "team-cobol/0.2",
    }
    shift = os.environ.get("NS_SHIFT")
    if shift:
        headers["X-Shift"] = shift
    req = urllib.request.Request(url, method=method, headers=headers,
                                 data=None if body is None else json.dumps(body).encode())
    for delay in (2, 4, 8, None):
        pace()
        t = time.time()
        status, text = send(req)
        sent += 1
        record(t, shift, method, path, body, status, text)
        text = chaos.incoming(body, text)
        if status != 429 and status < 500:
            return status, text
        if delay is None:
            halt(f"server returned {status} repeatedly")
        time.sleep(delay)


def record(t, shift, method, path, body, status, text):
    try:
        reply = json.loads(text)
    except ValueError:
        reply = {}
    result = reply.get("result")
    delta = None
    if isinstance(result, dict) and isinstance(result.get("score"), (int, float)):
        delta = result["score"] - last_score.get(shift, 0)
        last_score[shift] = result["score"]
    LOG.parent.mkdir(exist_ok=True)
    with LOG.open("a") as f:
        f.write(json.dumps({"t": t, "shift": shift, "method": method, "path": path, "body": body,
                            "cmd": (body or {}).get("cmd"), "clock": reply.get("clock"), "status": status,
                            "score_delta": delta, "expect_fail": expect_fail, "response": text}) + "\n")


if __name__ == "__main__":
    # usage: ns.py '<json>' [path]   or   ns.py GET <path>
    if sys.argv[1] == "GET":
        s, t = call(None, sys.argv[2], "GET")
    else:
        s, t = call(json.loads(sys.argv[1]), sys.argv[2] if len(sys.argv) > 2 else "")
    print(s, t)
