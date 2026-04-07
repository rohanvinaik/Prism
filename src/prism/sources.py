"""Data source readers for all Prism analytics lenses.

Reads from: Claude Code JSONL sessions, RTK SQLite, stats-cache,
usage-data facets, LintGate metrics/sessions, Continuity, Mneme.
All reads are read-only. Graceful degradation if a source is missing.
"""

import json
import sqlite3
from pathlib import Path
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass, field
from typing import Optional, Iterator

HOME = Path.home()
CLAUDE_DIR = HOME / ".claude"
PROJECTS_DIR = CLAUDE_DIR / "projects"
RTK_DB = HOME / "Library" / "Application Support" / "rtk" / "history.db"
STATS_CACHE = CLAUDE_DIR / "stats-cache.json"
FACETS_DIR = CLAUDE_DIR / "usage-data" / "facets"
LINTGATE_METRICS_DIR = CLAUDE_DIR / "lintgate" / "metrics"
LINTGATE_SESSION_DIR = CLAUDE_DIR / "lintgate" / "session"
CONTINUITY_DB = CLAUDE_DIR / "continuity" / "learning.db"
MNEME_DB = HOME / "MentalAtlas" / "mneme" / "data" / "mneme.db"

USER_PREFIX = "-Users-rohanvinaik-"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class TokenUsage:
    input_tokens: int = 0
    cache_creation: int = 0
    cache_read: int = 0
    output_tokens: int = 0

    @property
    def total(self) -> int:
        return self.input_tokens + self.cache_creation + self.cache_read + self.output_tokens

    @property
    def cache_hit_rate(self) -> float:
        total_input = self.input_tokens + self.cache_creation + self.cache_read
        return self.cache_read / total_input if total_input > 0 else 0.0

    def __add__(self, other: "TokenUsage") -> "TokenUsage":
        return TokenUsage(
            input_tokens=self.input_tokens + other.input_tokens,
            cache_creation=self.cache_creation + other.cache_creation,
            cache_read=self.cache_read + other.cache_read,
            output_tokens=self.output_tokens + other.output_tokens,
        )


@dataclass
class ToolCallRecord:
    name: str
    timestamp: Optional[str] = None


@dataclass
class SessionData:
    session_id: str
    project: str
    project_dir: str = ""
    timestamp_start: Optional[str] = None
    timestamp_end: Optional[str] = None
    usage: TokenUsage = field(default_factory=TokenUsage)
    tool_calls: list[ToolCallRecord] = field(default_factory=list)
    prompt_count: int = 0
    assistant_turns: int = 0
    subagent_count: int = 0
    subagent_usage: TokenUsage = field(default_factory=TokenUsage)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def project_name(dir_name: str) -> str:
    """Convert project directory name to readable form."""
    for prefix in (USER_PREFIX, "Users-rohanvinaik-"):
        if dir_name.startswith(prefix):
            return dir_name[len(prefix):]
    return dir_name


def parse_timestamp(ts: Optional[str]) -> Optional[datetime]:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def period_to_since(period: str) -> Optional[datetime]:
    """Convert a period name to a UTC cutoff datetime."""
    now = datetime.now(timezone.utc)
    match period:
        case "today":
            return now.replace(hour=0, minute=0, second=0, microsecond=0)
        case "week":
            return now - timedelta(days=7)
        case "month":
            return now - timedelta(days=30)
        case "quarter":
            return now - timedelta(days=90)
        case "all":
            return None
        case _:
            return now - timedelta(days=7)


def _safe_read_json(path: Path) -> Optional[dict | list]:
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def _safe_sqlite(db_path: Path, query: str, params: tuple = ()) -> list[dict]:
    if not db_path.is_file():
        return []
    try:
        conn = sqlite3.connect(str(db_path), timeout=5)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(query, params).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except sqlite3.OperationalError:
        return []


# ---------------------------------------------------------------------------
# Claude Code JSONL sessions
# ---------------------------------------------------------------------------

def _accumulate_assistant(obj: dict, session: SessionData, ts: Optional[str]) -> None:
    """Extract token usage and tool calls from an assistant message."""
    session.assistant_turns += 1
    usage = obj.get("message", {}).get("usage", {})
    session.usage.input_tokens += usage.get("input_tokens", 0)
    session.usage.cache_creation += usage.get("cache_creation_input_tokens", 0)
    session.usage.cache_read += usage.get("cache_read_input_tokens", 0)
    session.usage.output_tokens += usage.get("output_tokens", 0)

    content = obj.get("message", {}).get("content", [])
    if not isinstance(content, list):
        return
    for block in content:
        if isinstance(block, dict) and block.get("type") == "tool_use":
            session.tool_calls.append(
                ToolCallRecord(name=block.get("name", "unknown"), timestamp=ts)
            )


def _is_human_prompt(content: object) -> bool:
    """Return True if message content represents a human prompt (not tool results)."""
    if isinstance(content, str):
        return bool(content.strip())
    if isinstance(content, list):
        types = [b.get("type") for b in content if isinstance(b, dict)]
        return not (types and all(t == "tool_result" for t in types))
    return False


def _collect_subagents(path: Path, proj: str, session: SessionData) -> None:
    """Scan for subagent session files and accumulate their usage."""
    session_subdir = path.parent / path.stem
    if not session_subdir.is_dir():
        return
    for sub_jsonl in session_subdir.rglob("*.jsonl"):
        sub = parse_session(sub_jsonl, proj)
        if sub and sub.usage.total > 0:
            session.subagent_count += 1
            session.subagent_usage = session.subagent_usage + sub.usage


def parse_session(path: Path, proj: str) -> Optional[SessionData]:
    """Parse a single session JSONL file."""
    try:
        text = path.read_text(errors="replace")
    except OSError:
        return None

    session = SessionData(
        session_id=path.stem, project=proj, project_dir=path.parent.name,
    )
    last_ts = None

    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue

        ts = obj.get("timestamp")
        if ts:
            if not session.timestamp_start:
                session.timestamp_start = ts
            last_ts = ts

        msg_type = obj.get("type")
        if msg_type == "assistant":
            _accumulate_assistant(obj, session, ts)
        elif msg_type == "user" and not obj.get("isSidechain"):
            content = obj.get("message", {}).get("content", "")
            if _is_human_prompt(content):
                session.prompt_count += 1

    session.timestamp_end = last_ts
    _collect_subagents(path, proj, session)

    if session.usage.total == 0 and session.subagent_usage.total == 0:
        return None
    return session


def iter_sessions(
    since: Optional[datetime] = None,
    project_filter: Optional[str] = None,
) -> Iterator[SessionData]:
    """Iterate over all Claude Code sessions, optionally filtered."""
    if not PROJECTS_DIR.is_dir():
        return

    for proj_dir in sorted(PROJECTS_DIR.iterdir()):
        if not proj_dir.is_dir():
            continue
        proj = project_name(proj_dir.name)
        if project_filter and project_filter.lower() not in proj.lower():
            continue

        for jsonl_file in sorted(proj_dir.glob("*.jsonl")):
            session = parse_session(jsonl_file, proj)
            if not session:
                continue
            if since:
                ts = parse_timestamp(session.timestamp_start)
                if ts and ts < since:
                    continue
            yield session


# ---------------------------------------------------------------------------
# RTK command history
# ---------------------------------------------------------------------------

def read_rtk(
    since: Optional[datetime] = None,
    project_filter: Optional[str] = None,
    limit: int = 1000,
) -> list[dict]:
    if not RTK_DB.is_file():
        return []
    try:
        conn = sqlite3.connect(str(RTK_DB), timeout=5)
        conn.row_factory = sqlite3.Row
        query = "SELECT * FROM commands"
        params: list = []
        conditions = []
        if since:
            conditions.append("timestamp >= ?")
            params.append(since.isoformat())
        if project_filter:
            conditions.append("project_path GLOB ?")
            params.append(f"*{project_filter}*")
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(query, params).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except sqlite3.OperationalError:
        return []


# ---------------------------------------------------------------------------
# Claude Code stats-cache (daily rollups)
# ---------------------------------------------------------------------------

def read_stats_cache() -> dict[str, dict]:
    """Returns {date_str: {messageCount, sessionCount, toolCallCount}}."""
    raw = _safe_read_json(STATS_CACHE)
    if not isinstance(raw, dict):
        return {}
    # Stats-cache v2: dailyActivity is a list of {date, messageCount, ...}
    daily_list = raw.get("dailyActivity", [])
    if isinstance(daily_list, list):
        return {entry["date"]: entry for entry in daily_list if isinstance(entry, dict) and "date" in entry}
    return {}


# ---------------------------------------------------------------------------
# Claude Code usage-data facets (session outcomes)
# ---------------------------------------------------------------------------

def read_facets() -> list[dict]:
    if not FACETS_DIR.is_dir():
        return []
    results = []
    for f in FACETS_DIR.glob("*.json"):
        data = _safe_read_json(f)
        if isinstance(data, dict):
            results.append(data)
    return results


# ---------------------------------------------------------------------------
# LintGate metrics + session memory
# ---------------------------------------------------------------------------

def read_lintgate_metrics(since: Optional[datetime] = None) -> list[dict]:
    if not LINTGATE_METRICS_DIR.is_dir():
        return []
    results = []
    for f in sorted(LINTGATE_METRICS_DIR.glob("lintgate_*.jsonl")):
        date_str = f.stem.replace("lintgate_", "")
        if since:
            try:
                file_date = datetime.strptime(date_str, "%Y%m%d").replace(tzinfo=timezone.utc)
                if file_date < since:
                    continue
            except ValueError:
                pass
        try:
            for line in f.read_text().splitlines():
                if line.strip():
                    results.append(json.loads(line))
        except (json.JSONDecodeError, OSError):
            continue
    return results


def read_lintgate_sessions() -> dict[str, dict]:
    if not LINTGATE_SESSION_DIR.is_dir():
        return {}
    results = {}
    for f in LINTGATE_SESSION_DIR.glob("*.json"):
        data = _safe_read_json(f)
        if isinstance(data, dict):
            results[f.stem] = data
    return results


# ---------------------------------------------------------------------------
# Continuity (decisions)
# ---------------------------------------------------------------------------

def read_continuity_decisions(since: Optional[datetime] = None) -> list[dict]:
    if not CONTINUITY_DB.is_file():
        return []
    query = """
        SELECT d.*, s.project_path, s.project_name, s.outcome,
               s.created_at AS session_created
        FROM decisions d
        JOIN sessions s ON d.session_id = s.id
    """
    params: tuple = ()
    if since:
        query += " WHERE s.created_at >= ?"
        params = (since.isoformat(),)
    query += " ORDER BY d.created_at DESC"
    return _safe_sqlite(CONTINUITY_DB, query, params)


# ---------------------------------------------------------------------------
# Mneme (cognitive state)
# ---------------------------------------------------------------------------

def read_mneme_recent(hours: int = 24) -> dict:
    if not MNEME_DB.is_file():
        return {}
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    try:
        conn = sqlite3.connect(str(MNEME_DB), timeout=5)
        conn.row_factory = sqlite3.Row

        event_row = conn.execute(
            "SELECT COUNT(*) AS n FROM events WHERE timestamp >= ?", (cutoff,)
        ).fetchone()
        event_count = dict(event_row)["n"] if event_row else 0

        anchors = conn.execute(
            """SELECT a.label, a.category, COUNT(*) AS freq
               FROM event_anchors ea
               JOIN anchors a ON ea.anchor_id = a.id
               JOIN events e ON ea.event_id = e.id
               WHERE e.timestamp >= ?
               GROUP BY a.id ORDER BY freq DESC LIMIT 10""",
            (cutoff,),
        ).fetchall()

        dims = conn.execute(
            """SELECT d.dimension, d.path, d.sign, d.depth
               FROM dimension_positions d
               JOIN events e ON d.event_id = e.id
               WHERE e.timestamp >= ?
               ORDER BY e.timestamp DESC LIMIT 20""",
            (cutoff,),
        ).fetchall()

        conn.close()
        return {
            "event_count": event_count,
            "top_anchors": [dict(r) for r in anchors],
            "recent_dimensions": [dict(r) for r in dims],
        }
    except sqlite3.OperationalError:
        return {}
