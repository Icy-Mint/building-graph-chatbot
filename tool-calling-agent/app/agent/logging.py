"""JSONL turn logger -- one line per agent.ask() call: question, every tool
call with its arguments/status/latency, token usage (including cache hit/
miss counts, so prompt-caching effectiveness is directly visible in the
log, not just assumed), final answer, and total latency."""
import json
import os
from datetime import datetime, timezone

LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "logs", "agent_turns.jsonl")
LOG_PATH = os.path.normpath(LOG_PATH)


def log_turn(entry: dict) -> None:
    record = {"timestamp": datetime.now(timezone.utc).isoformat(), **entry}
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, default=str) + "\n")
