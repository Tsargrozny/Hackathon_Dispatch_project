"""Append-only decision log: one JSON line per action (what, why, who, UTC time, unique action ID).

The file is only ever appended to, so it survives resets and restarts. Set DECISION_LOG to move it.
"""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

PATH = Path(os.getenv("DECISION_LOG") or Path(__file__).resolve().parent.parent / "decision_log.jsonl")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_action_id() -> str:
    return "A-" + uuid.uuid4().hex[:8]


def write(**record) -> None:
    try:
        with PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError as e:  # logging must never take the demo down
        print(f"[decision_log] could not write: {e}")
