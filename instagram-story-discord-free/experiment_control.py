"""Bounded experiment control. Contains no credentials or Instagram requests."""
import json
import os
import sys
import time
from pathlib import Path

STATE = Path(__file__).with_name("monitor_control.json")
MIN_INTERVAL_SECONDS = 1800
MAX_ATTEMPTS = 3

def read_state():
    state = json.loads(STATE.read_text(encoding="utf-8"))
    if not isinstance(state, dict):
        raise ValueError("Invalid control state")
    for key in ("enabled", "paused"):
        if type(state.get(key)) is not bool:
            raise ValueError("Invalid control flag")
    if type(state.get("attempts")) is not int or state["attempts"] < 0:
        raise ValueError("Invalid attempt count")
    if not isinstance(state.get("last_attempt"), (int, float)):
        raise ValueError("Invalid attempt timestamp")
    return state

def save(state):
    temporary = STATE.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    temporary.replace(STATE)

def prepare(mode, now=None):
    now = time.time() if now is None else now
    state = read_state()
    if mode not in ("normal", "single-check"):
        return False, "Unsupported mode"
    if state["paused"]:
        return False, "Paused after an interrupted or failed check; review before resuming"
    if mode == "normal" and not state["enabled"]:
        return False, "Automatic monitoring is disabled"
    if state["attempts"] >= MAX_ATTEMPTS:
        return False, "Three-check experiment is complete; review results"
    if now - state["last_attempt"] < MIN_INTERVAL_SECONDS:
        return False, "Minimum 30-minute interval has not elapsed"
    state.update(paused=True, reason="Check in progress or interrupted",
                 last_attempt=now, attempts=state["attempts"] + 1)
    save(state)
    return True, "One check reserved; pause must be persisted before network access"

def finish(success):
    state = read_state()
    if success:
        state.update(paused=False, reason="Last check succeeded")
    else:
        state.update(paused=True, enabled=False,
                     reason="Check failed or was interrupted; manual review required")
    save(state)

if __name__ == "__main__":
    if sys.argv[1] == "prepare":
        allowed, message = prepare(os.environ.get("MONITOR_MODE", "normal"))
        print(message)
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
            output.write("allowed=" + str(allowed).lower() + "\n")
    elif sys.argv[1] == "finish":
        finish(os.environ.get("CHECK_OUTCOME") == "success")
    else:
        raise SystemExit("Unknown command")
