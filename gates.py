#!/usr/bin/env python3
"""Evaluate the coverage gates from logs/findings.jsonl and write logs/gates.json."""
import json
from pathlib import Path

ROOT = Path(__file__).parent
FINDINGS = ROOT / "logs" / "findings.jsonl"
REQUESTS = ROOT / "logs" / "requests.jsonl"
OUT = ROOT / "logs" / "gates.json"

COMMANDS = ["help", "whoami", "shifts", "leaderboard", "start_shift", "status", "inbox", "look", "move", "pick",
            "place", "report", "end_shift", "read", "navigate", "call_elevator", "knock", "ask", "reply", "say",
            "scan", "dock", "use", "drop"]
GATES = {
    "G1": [f"command:{c}" for c in COMMANDS],
    "G2": ["room_entry", "keycard", "cart", "carry_limit", "stairs_vs_elevator", "move_vs_navigate", "scan",
           "dock_battery"],
    "G3": ["delivery_value", "report_value", "penalty:false_report", "penalty:wrong_delivery", "penalty:wrong_room",
           "penalty:unverified_guest", "penalty:locked_bumps"],
    "G4": ["seed_comparison", "determinism"],
    "G5": ["night_end", "requests_per_night", "battery_per_night"],
    "G6": ["type:room_check", "type:spill", "type:lost_item", "type:noise", "type:guest_message", "type:tray",
           "type:bags", "type:room_service", "type:named_delivery", "type:never_came"],
    "G7": ["unseen_mean", "no_stranded", "no_stranger_doors", "test:renamed_items", "test:reworded_messages",
           "test:changed_map", "test:moved_stock", "test:no_signals", "test:injected_note", "test:fake_manager",
           "test:duplicate_names", "test:bait_request", "test:missing_command", "final:restart", "final:clean_close",
           "final:concurrency"],
}


def findings():
    if not FINDINGS.exists():
        return {}
    return {f["key"]: f for f in map(json.loads, FINDINGS.read_text().splitlines())}


def evaluate():
    found = findings()
    total = sum(1 for _ in REQUESTS.open()) if REQUESTS.exists() else 0
    gates = {}
    for gate, keys in GATES.items():
        # a finding only counts if the log range it cites really exists
        good = [k for k in keys if k in found and found[k].get("answered", True)
                and 0 < found[k]["from"] <= found[k]["to"] <= total]
        gates[gate] = {"passed": len(good) == len(keys), "missing": [k for k in keys if k not in good]}
    OUT.write_text(json.dumps(gates, indent=1))
    return gates


if __name__ == "__main__":
    for gate, g in evaluate().items():
        print(gate, "PASS" if g["passed"] else f'missing {len(g["missing"])}: {", ".join(g["missing"])}')
