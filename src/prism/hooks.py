"""Prism hook handlers for Claude Code events.

Silent by default — writes to disk via engine, no systemMessage output
UNLESS an anomaly is detected (consecutive errors, high error rate).

Entry point: prism-hook (console_scripts in pyproject.toml)
Protocol: stdin JSON → dispatch by event type → stdout JSON (empty = silent)
"""

import json
import os
import sys
from collections import Counter
from datetime import datetime
from typing import Any

from . import engine

# Anomaly thresholds
CONSECUTIVE_ERROR_THRESHOLD = 3
SESSION_ERROR_RATE_THRESHOLD = 0.20


def _session_id(data: dict) -> str:
    return data.get("session_id") or os.environ.get("CLAUDE_SESSION_ID") or "unknown"


def _project_from_cwd(data: dict) -> str:
    return data.get("cwd", os.getcwd())


def _check_consecutive_errors(events: list[dict]) -> int:
    """Count consecutive errors at tail of event stream."""
    count = 0
    for e in reversed(events):
        if e.get("event") != "tool_use":
            continue
        if e.get("error"):
            count += 1
        else:
            break
    return count


# ---------------------------------------------------------------------------
# Event handlers
# ---------------------------------------------------------------------------


def handle_post_tool_use(data: dict) -> dict:
    """Record tool execution. Emit warning on consecutive errors."""
    sid = _session_id(data)
    tool_name = data.get("tool_name", "unknown")

    event: dict[str, Any] = {
        "event": "tool_use",
        "tool": tool_name,
    }

    tool_output = data.get("tool_output", "")
    if isinstance(tool_output, str):
        event["output_bytes"] = len(tool_output.encode("utf-8", errors="replace"))
        lower = tool_output[:200].lower()
        event["error"] = "error" in lower or "traceback" in lower or "exception" in lower
    elif isinstance(tool_output, dict):
        event["error"] = bool(tool_output.get("error"))
    else:
        event["error"] = False

    engine.append_event(sid, event)

    # Anomaly: consecutive errors
    if event.get("error"):
        events = engine.read_events(sid)
        streak = _check_consecutive_errors(events)
        if streak >= CONSECUTIVE_ERROR_THRESHOLD:
            return {
                "systemMessage": (
                    f"[Prism] {streak} consecutive tool errors detected. "
                    "Consider pausing to diagnose before continuing."
                )
            }

    return {}


def handle_session_start(data: dict) -> dict:
    """Initialize session tracking. Silent."""
    sid = _session_id(data)
    engine.append_event(
        sid,
        {
            "event": "session_start",
            "project": _project_from_cwd(data),
        },
    )
    return {}


def _compute_efficiency(events: list[dict]) -> dict:
    """Compute session efficiency metrics from hook events."""
    tool_events = [e for e in events if e.get("event") == "tool_use"]
    if not tool_events:
        return {}

    tool_counts: Counter[str] = Counter(e.get("tool", "unknown") for e in tool_events)
    errors = sum(1 for e in tool_events if e.get("error"))
    total_output_bytes = sum(e.get("output_bytes", 0) for e in tool_events)
    compactions = sum(1 for e in events if e.get("event") == "pre_compact")

    # Duration
    timestamps = [e["ts"] for e in events if "ts" in e]
    duration_sec = None
    if len(timestamps) >= 2:
        try:
            start = datetime.fromisoformat(timestamps[0].replace("Z", "+00:00"))
            end = datetime.fromisoformat(timestamps[-1].replace("Z", "+00:00"))
            duration_sec = int((end - start).total_seconds())
        except (ValueError, TypeError):
            pass

    error_rate = errors / len(tool_events)

    # Efficiency score: 100 = perfect, penalize errors and compactions
    score = max(0, round(100 * (1 - error_rate) - (compactions * 5)))

    return {
        "tool_calls": len(tool_events),
        "tool_distribution": dict(tool_counts),
        "errors": errors,
        "error_rate": round(error_rate, 3),
        "total_output_bytes": total_output_bytes,
        "compactions": compactions,
        "duration_sec": duration_sec,
        "efficiency_score": score,
    }


def handle_stop(data: dict) -> dict:
    """Finalize session: compute efficiency, write summary + bridge file."""
    sid = _session_id(data)
    events = engine.read_events(sid)
    if not events:
        return {}

    efficiency = _compute_efficiency(events)
    if not efficiency:
        return {}

    # Project from session_start
    start_events = [e for e in events if e.get("event") == "session_start"]
    project = start_events[0].get("project", "") if start_events else ""

    summary = {"session_id": sid, "project": project, **efficiency}
    engine.append_daily_summary(summary)

    # Write bridge file for LintGate consumption
    engine.write_bridge(
        {
            "session_id": sid,
            "project": project,
            **efficiency,
        }
    )

    # Anomaly: high error rate
    if efficiency["error_rate"] > SESSION_ERROR_RATE_THRESHOLD:
        return {
            "systemMessage": (
                f"[Prism] Session error rate {efficiency['error_rate']:.0%} "
                f"exceeds threshold ({SESSION_ERROR_RATE_THRESHOLD:.0%}). "
                f"{efficiency['errors']}/{efficiency['tool_calls']} tool calls failed."
            )
        }

    return {}


def handle_session_end(data: dict) -> dict:
    """Same as Stop."""
    return handle_stop(data)


def handle_pre_compact(data: dict) -> dict:
    """Record compaction boundary. Silent."""
    sid = _session_id(data)
    events = engine.read_events(sid)
    tool_count = sum(1 for e in events if e.get("event") == "tool_use")

    engine.append_event(
        sid,
        {
            "event": "pre_compact",
            "tools_so_far": tool_count,
        },
    )
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
