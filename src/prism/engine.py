"""On-disk analytics engine for Prism.

Manages persistent state: analysis snapshots, session event streams,
daily summaries, and project health state files. Implements the
compact-first pattern (write heavy results to disk, return run_id +
tight summary to MCP tools, drill down on demand).

Directory layout:
    ~/.claude/prism/
    ├── snapshots/{id}.json      # Analysis results (drill-down via prism_details)
    ├── sessions/{session_id}.jsonl  # Real-time tool call events from hooks
    ├── daily/{YYYYMMDD}.jsonl   # Session summaries (one line per session)
    └── health/{project_hash}.json   # Project setup health (LintGate reads this)
"""

import json
import time
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional

PRISM_DIR = Path.home() / ".claude" / "prism"
SNAPSHOTS_DIR = PRISM_DIR / "snapshots"
SESSIONS_DIR = PRISM_DIR / "sessions"
DAILY_DIR = PRISM_DIR / "daily"
HEALTH_DIR = PRISM_DIR / "health"

SCHEMA_VERSION = 1
RESPONSE_CHAR_LIMIT = 2048
LIST_TRUNCATION = 20


# ---------------------------------------------------------------------------
# Directory setup
# ---------------------------------------------------------------------------

def _ensure_dirs() -> None:
    for d in (SNAPSHOTS_DIR, SESSIONS_DIR, DAILY_DIR, HEALTH_DIR):
        d.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Analysis IDs
# ---------------------------------------------------------------------------

_counter = 0


def analysis_id() -> str:
    """Generate a 12-char hex ID from nanosecond time + counter."""
    global _counter
    _counter += 1
    raw = (int(time.time_ns()) + _counter) % (16**12)
    return f"{raw:012x}"


# ---------------------------------------------------------------------------
# Snapshots (compact-first pattern)
# ---------------------------------------------------------------------------

def save_snapshot(tool: str, summary: str, data: dict) -> str:
    """Persist full analysis results. Returns analysis_id.

    The MCP tool returns *summary* (compact text) + the ID.
    Claude calls prism_details(id) to drill into *data* on demand.
    """
    _ensure_dirs()
    aid = analysis_id()
    envelope = {
        "_meta": {
            "analysis_id": aid,
            "tool": tool,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "schema_version": SCHEMA_VERSION,
        },
        "summary": summary,
        **data,
    }
    path = SNAPSHOTS_DIR / f"{aid}.json"
    path.write_text(json.dumps(envelope, default=str, indent=2))
    return aid


def load_snapshot(aid: str) -> Optional[dict]:
    path = SNAPSHOTS_DIR / f"{aid}.json"
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def list_snapshots(limit: int = 10) -> list[dict]:
    """List recent snapshots (meta only)."""
    _ensure_dirs()
    files = sorted(SNAPSHOTS_DIR.glob("*.json"), key=lambda f: f.stat().st_mtime, reverse=True)
    results = []
    for f in files[:limit]:
        try:
            data = json.loads(f.read_text())
            results.append(data.get("_meta", {}))
        except (json.JSONDecodeError, OSError):
            continue
    return results


def query_snapshot(
    aid: str,
    section: str = "",
    path: str = "",
    max_items: int = LIST_TRUNCATION,
) -> str:
    """Navigate into a snapshot via section + dot-separated path.

    Returns formatted text, capped at RESPONSE_CHAR_LIMIT.
    """
    data = load_snapshot(aid)
    if not data:
        return f"Snapshot {aid} not found."

    target = data
    if section:
        if section in data:
            target = data[section]
        else:
            available = [k for k in data if not k.startswith("_")]
            return f"Section '{section}' not found. Available: {', '.join(available)}"

    if path:
        for key in path.split("."):
            if isinstance(target, dict) and key in target:
                target = target[key]
            elif isinstance(target, list):
                try:
                    target = target[int(key)]
                except (ValueError, IndexError):
                    return f"Path key '{key}' not found."
            else:
                return f"Path key '{key}' not found."

    result = json.dumps(target, indent=2, default=str)
    if len(result) > RESPONSE_CHAR_LIMIT:
        if isinstance(target, list) and len(target) > max_items:
            target = target[:max_items]
            result = json.dumps(target, indent=2, default=str)
            result += f"\n\n... truncated to {max_items}/{len(data.get(section, target))} items"
        else:
            result = result[:RESPONSE_CHAR_LIMIT] + "\n\n... truncated"

    return result


# ---------------------------------------------------------------------------
# Session events (hooks write here)
# ---------------------------------------------------------------------------

def append_event(session_id: str, event: dict) -> None:
    """Append a single event to the session's JSONL stream."""
    _ensure_dirs()
    event["ts"] = datetime.now(timezone.utc).isoformat()
    path = SESSIONS_DIR / f"{session_id}.jsonl"
    with open(path, "a") as f:
        f.write(json.dumps(event, default=str) + "\n")


def read_events(session_id: str) -> list[dict]:
    path = SESSIONS_DIR / f"{session_id}.jsonl"
    if not path.is_file():
        return []
    events = []
    for line in path.read_text().splitlines():
        if line.strip():
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return events


# ---------------------------------------------------------------------------
# Daily summaries (SessionEnd hook writes here)
# ---------------------------------------------------------------------------

def append_daily_summary(summary: dict) -> None:
    """Append a session summary to today's daily JSONL."""
    _ensure_dirs()
    summary["schema_version"] = SCHEMA_VERSION
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    path = DAILY_DIR / f"{today}.jsonl"
    with open(path, "a") as f:
        f.write(json.dumps(summary, default=str) + "\n")


def read_daily_summaries(days: int = 7) -> list[dict]:
    if not DAILY_DIR.is_dir():
        return []
    results = []
    for f in sorted(DAILY_DIR.glob("*.jsonl"))[-days:]:
        for line in f.read_text().splitlines():
            if line.strip():
                try:
                    results.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return results


# ---------------------------------------------------------------------------
# Project health state (LintGate reads these)
# ---------------------------------------------------------------------------

def write_health(project_hash: str, health: dict) -> None:
    """Write project health state for LintGate consumption."""
    _ensure_dirs()
    health["_meta"] = {
        "updated": datetime.now(timezone.utc).isoformat(),
        "schema_version": SCHEMA_VERSION,
    }
    path = HEALTH_DIR / f"{project_hash}.json"
    path.write_text(json.dumps(health, indent=2, default=str))


def read_health(project_hash: str) -> Optional[dict]:
    path = HEALTH_DIR / f"{project_hash}.json"
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None
