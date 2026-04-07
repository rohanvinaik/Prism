"""Prism hook handlers for Claude Code events.

Silent by default — writes to disk via engine, no systemMessage output.
This gives Prism real-time telemetry that JSONL post-hoc parsing can't:
per-tool timing, success/failure rates, output sizes, session boundaries.

Entry point: prism-hook (console_scripts in pyproject.toml)
Protocol: stdin JSON → dispatch by event type → stdout JSON (empty = silent)
"""

import json
import os
import sys
from collections import Counter
from typing import Any

from . import engine


def _session_id(data: dict) -> str:
    """Extract session ID from hook data or environment."""
    return (
        data.get("session_id")
        or os.environ.get("CLAUDE_SESSION_ID")
        or "unknown"
    )


def _project_from_cwd(data: dict) -> str:
    return data.get("cwd", os.getcwd())


# ---------------------------------------------------------------------------
# Event handlers (all return dict for stdout; empty dict = silent)
# ---------------------------------------------------------------------------

def handle_post_tool_use(data: dict) -> dict:
    """Record tool execution. Silent."""
    sid = _session_id(data)
    tool_name = data.get("tool_name", "unknown")

    event: dict[str, Any] = {
        "event": "tool_use",
        "tool": tool_name,
    }

    # Detect failure from tool output
    tool_output = data.get("tool_output", "")
    if isinstance(tool_output, str):
        event["output_bytes"] = len(tool_output.encode("utf-8", errors="replace"))
        # Heuristic: errors often start with "Error" or contain tracebacks
        lower = tool_output[:200].lower()
        event["error"] = (
            "error" in lower
            or "traceback" in lower
            or "exception" in lower
        )
    elif isinstance(tool_output, dict):
        event["error"] = bool(tool_output.get("error"))

    engine.append_event(sid, event)
    return {}


def handle_session_start(data: dict) -> dict:
    """Initialize session tracking. Silent."""
    sid = _session_id(data)
    engine.append_event(sid, {
        "event": "session_start",
        "project": _project_from_cwd(data),
    })
    return {}


def handle_session_end(data: dict) -> dict:
    """Finalize session: compute summary, write to daily JSONL. Silent."""
    sid = _session_id(data)
    events = engine.read_events(sid)
    if not events:
        return {}

    tool_events = [e for e in events if e.get("event") == "tool_use"]
    tool_counts: Counter[str] = Counter(e.get("tool", "unknown") for e in tool_events)
    errors = sum(1 for e in tool_events if e.get("error"))
    total_output_bytes = sum(e.get("output_bytes", 0) for e in tool_events)

    # Timestamps for duration
    timestamps = [e.get("ts", "") for e in events if e.get("ts")]
    duration_sec = None
    if len(timestamps) >= 2:
        try:
            from datetime import datetime
            start = datetime.fromisoformat(timestamps[0].replace("Z", "+00:00"))
            end = datetime.fromisoformat(timestamps[-1].replace("Z", "+00:00"))
            duration_sec = int((end - start).total_seconds())
        except (ValueError, TypeError):
            pass

    # Find project from session_start event
    start_events = [e for e in events if e.get("event") == "session_start"]
    project = start_events[0].get("project", "") if start_events else ""

    summary = {
        "session_id": sid,
        "project": project,
        "tool_calls": len(tool_events),
        "tool_distribution": dict(tool_counts),
        "errors": errors,
        "error_rate": errors / max(len(tool_events), 1),
        "total_output_bytes": total_output_bytes,
        "compactions": sum(1 for e in events if e.get("event") == "pre_compact"),
    }
    if duration_sec is not None:
        summary["duration_sec"] = duration_sec

    engine.append_daily_summary(summary)
    return {}


def handle_stop(data: dict) -> dict:
    """Same as session_end — Claude Code fires Stop on exit."""
    return handle_session_end(data)


def handle_pre_compact(data: dict) -> dict:
    """Record compaction boundary. Silent."""
    sid = _session_id(data)
    events = engine.read_events(sid)
    tool_count = sum(1 for e in events if e.get("event") == "tool_use")

    engine.append_event(sid, {
        "event": "pre_compact",
        "tools_so_far": tool_count,
    })
    return {}


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

HANDLERS = {
    "PostToolUse": handle_post_tool_use,
    "SessionStart": handle_session_start,
    "SessionEnd": handle_session_end,
    "Stop": handle_stop,
    "PreCompact": handle_pre_compact,
}


def main() -> None:
    """Entry point for prism-hook console script."""
    try:
        raw = sys.stdin.read()
        if not raw.strip():
            print("{}")
            return

        data = json.loads(raw)

        # Claude Code hooks pass event type differently depending on config.
        # The hook command is registered per-event, so we check multiple fields.
        event_type = data.get("type", "") or data.get("event", "")

        handler = HANDLERS.get(event_type)
        if handler:
            result = handler(data)
            print(json.dumps(result))
        else:
            print("{}")
    except Exception:
        # Hooks must never crash — silent failure, exit 0
        print("{}")


if __name__ == "__main__":
    main()
