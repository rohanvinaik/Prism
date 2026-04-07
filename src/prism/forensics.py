"""Forensics Lens.

Deep dive into specific session(s) across all data sources.
"""

from collections import Counter

from . import engine, sources
from .engine import read_events, read_bridge


def _session_compact(s: sources.SessionData) -> str:
    """One-line session summary for compact output."""
    ts = s.timestamp_start[:16] if s.timestamp_start else "?"
    top3 = Counter(tc.name for tc in s.tool_calls).most_common(3)
    tools_str = ", ".join(f"{t}({c})" for t, c in top3)
    return (
        f"[{ts}] **{s.project}**: {s.usage.total:,} tokens, "
        f"{s.prompt_count} prompts, {len(s.tool_calls)} calls ({tools_str})"
    )


def _session_full(s: sources.SessionData) -> dict:
    """Full session data for disk persistence."""
    tool_counts = Counter(tc.name for tc in s.tool_calls)

    start = sources.parse_timestamp(s.timestamp_start)
    end = sources.parse_timestamp(s.timestamp_end)
    duration_min = int((end - start).total_seconds() / 60) if start and end else None

    reads = sum(1 for tc in s.tool_calls if tc.name in ("Read", "Grep", "Glob"))
    edits = sum(1 for tc in s.tool_calls if tc.name in ("Edit", "Write"))

    data: dict = {
        "session_id": s.session_id,
        "project": s.project,
        "started": s.timestamp_start,
        "ended": s.timestamp_end,
        "duration_min": duration_min,
        "tokens": {
            "input": s.usage.input_tokens,
            "cache_creation": s.usage.cache_creation,
            "cache_read": s.usage.cache_read,
            "output": s.usage.output_tokens,
            "total": s.usage.total,
            "cache_hit_rate": round(s.usage.cache_hit_rate, 3),
            "per_turn": s.usage.total // max(s.assistant_turns, 1),
        },
        "interaction": {
            "prompts": s.prompt_count,
            "assistant_turns": s.assistant_turns,
            "tool_calls": len(s.tool_calls),
            "turns_per_prompt": round(s.assistant_turns / max(s.prompt_count, 1), 1),
            "tools_per_prompt": round(len(s.tool_calls) / max(s.prompt_count, 1), 1),
        },
        "tool_distribution": dict(tool_counts.most_common()),
        "tool_sequence": [tc.name for tc in s.tool_calls[:50]],
        "signals": {
            "reads": reads,
            "edits": edits,
            "read_edit_ratio": round(reads / max(edits, 1), 1),
        },
    }

    if s.subagent_count > 0:
        data["subagents"] = {
            "count": s.subagent_count,
            "tokens": s.subagent_usage.total,
            "pct_of_session": round(s.subagent_usage.total / max(s.usage.total, 1) * 100, 1),
        }

    # RTK commands overlapping this session
    start_dt = sources.parse_timestamp(s.timestamp_start)
    if start_dt:
        rtk_cmds = sources.read_rtk(since=start_dt, limit=200)
        if rtk_cmds and s.timestamp_end:
            in_range = [c for c in rtk_cmds if c.get("timestamp", "") <= s.timestamp_end]
            if in_range:
                data["rtk"] = {
                    "commands": len(in_range),
                    "saved": sum(c.get("saved_tokens", 0) for c in in_range),
                }

    # Hook-captured real-time data (richer than JSONL parsing)
    hook_events = read_events(s.session_id)
    if hook_events:
        tool_hook_events = [e for e in hook_events if e.get("event") == "tool_use"]
        hook_errors = sum(1 for e in tool_hook_events if e.get("error"))
        total_output_bytes = sum(e.get("output_bytes", 0) for e in tool_hook_events)
        compactions = sum(1 for e in hook_events if e.get("event") == "pre_compact")

        data["realtime"] = {
            "hook_events": len(hook_events),
            "tool_calls_observed": len(tool_hook_events),
            "errors_detected": hook_errors,
            "error_rate": round(hook_errors / max(len(tool_hook_events), 1), 3),
            "total_output_bytes": total_output_bytes,
            "compactions": compactions,
        }

    # Bridge efficiency data (latest session metrics)
    bridge = read_bridge()
    if bridge and bridge.get("session_id") == s.session_id:
        data["efficiency_score"] = bridge.get("efficiency_score")

    return data


def run(session_id: str = "", project: str = "", last_n: int = 1) -> str:
    if session_id:
        targets = [
            s for s in sources.iter_sessions()
            if s.session_id.startswith(session_id)
        ]
        if not targets:
            return f"No session found matching '{session_id}'"
    else:
        all_sessions = list(sources.iter_sessions(project_filter=project or None))
        all_sessions.sort(key=lambda s: s.timestamp_start or "", reverse=True)
        targets = all_sessions[:last_n]

    if not targets:
        return "No sessions found."

    # -- Compact summary --
    lines = [f"# Session Forensics ({len(targets)} session{'s' if len(targets) > 1 else ''})", ""]
    for s in targets:
        lines.append(f"- {_session_compact(s)}")

    summary = "\n".join(lines)

    # -- Full data to disk --
    full_data = {
        "sessions": [_session_full(s) for s in targets],
    }

    aid = engine.save_snapshot("forensics", summary, full_data)
    lines.append("")
    lines.append(f"_Details: prism_details(\"{aid}\", section=\"sessions\", path=\"0.tool_distribution\")_")

    return "\n".join(lines)
