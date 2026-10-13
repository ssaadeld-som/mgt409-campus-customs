"""Append cumulative, concise agent and MCP-call events to output/audit_trail.json."""
from __future__ import annotations

import fcntl
import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backend.models import AuditTrailEvent

PROJECT_ROOT = Path(__file__).resolve().parents[1]
AUDIT_PATH = PROJECT_ROOT / "output" / "audit_trail.json"
_LOCK = threading.Lock()


def _short(value: Any, limit: int = 600) -> str:
    try:
        text = json.dumps(value, default=str, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError):
        text = str(value)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def append_event(
    agent: str,
    step: str,
    tool_name: str = "",
    arguments: Any = None,
    result: Any = None,
    stop_reason: str = "",
) -> None:
    """Append one array item, leaving all previously recorded events untouched."""
    event = AuditTrailEvent(
        time=datetime.now(timezone.utc),
        agent=agent,
        step=step,
        tool_name=tool_name,
        short_arguments=_short(arguments),
        short_result=_short(result),
        stop_reason=stop_reason,
    ).model_dump(mode="json")
    AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
    AUDIT_PATH.touch(exist_ok=True)
    with _LOCK, AUDIT_PATH.open("r+b") as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        stream.seek(0)
        raw = stream.read()
        stripped = raw.rstrip(b" \t\r\n")
        try:
            events = json.loads(stripped)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise RuntimeError(f"Audit trail is invalid JSON; preserving it without writing: {AUDIT_PATH}") from exc
        if not isinstance(events, list) or not stripped.endswith(b"]"):
            raise RuntimeError(f"Audit trail must be a JSON array; preserving it without writing: {AUDIT_PATH}")

        # JSON arrays need a closing bracket, so extend the array by replacing only
        # that delimiter and any whitespace after it. Existing event bytes remain.
        prefix = stripped[:-1].rstrip()
        serialized = json.dumps(event, indent=2, ensure_ascii=False).encode("utf-8")
        indented = b"\n".join(b"  " + line for line in serialized.splitlines())
        separator = b"," if events else b""
        stream.seek(len(prefix))
        stream.truncate()
        stream.write(separator + b"\n" + indented + b"\n]\n")
        stream.flush()
        os.fsync(stream.fileno())
        fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
