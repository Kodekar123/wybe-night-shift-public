"""What a credited report pays, fitted on 3,018 real jobs (rows with n == 1 in the scratch rows.json).

pts = base[kind] * (1 + RISE * hours since 22:00) * (FLOOR + (1 - FLOOR) * exp(-minutes late / tau[kind]))

Mean absolute error 2.75 points. Lateness runs from the message arriving to the report, and the clock is the report
time. A hold test showed pay doesn't depend on earlier work, so nothing else goes in.
"""
import math

BASE = {"delivery": 24.6, "message": 19.9, "noise": 25.6, "check": 32.3, "tray": 38.8, "bags": 39.9, "lost": 44.0,
        "spill": 50.7}
TAU = {"delivery": 25.3, "message": 23.9, "noise": 20.3, "check": 18.3, "tray": 17.1, "bags": 22.6, "lost": 23.6,
       "spill": 27.4}
RISE, FLOOR = 0.075, 0.211

FALSE_DONE, WRONG_PLACE, SLIP, STRANGER, STRANDED, HONEST_DECLINE = -20, -10, -15, -80, -200, 5


def price(kind, arrived, reported):
    """Points for a true 'done' report. Times are seconds after 22:00."""
    late = max(0.0, reported - arrived) / 60
    value = BASE[kind] * (1 + RISE * reported / 3600) * (FLOOR + (1 - FLOOR) * math.exp(-late / TAU[kind]))
    return int(round(value))
