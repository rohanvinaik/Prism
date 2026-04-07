"""Forensics Lens.

Deep dive into specific session(s) across all data sources.
"""

from collections import Counter

from . import sources


def _session_detail(s: sources.SessionData) -> list[str]:
    """Format a single session's forensic report."""
    lines = [f"# Session: {s.session_id[:12]}…", ""]
    lines.append(f"- **Project**: {s.project}")
    lines.append(f"- **Started**: {s.timestamp_start or 'unknown'}")
    lines.append(f"- **Ended**: {s.timestamp_end or 'unknown'}")

    # Duration
    start = sources.parse_timestamp(s.timestamp_start)
    end = sources.parse_timestamp(s.timestamp_end)
    if start and end:
        mins = int((end - start).total_seconds() / 60)
        lines.append(f"- **Duration**: {mins} min")
    lines.append("")

    # Token breakdown
    lines.append("## Tokens")
    lines.append(f"- Total: {s.usage.total:,}")
    lines.append(f"- Input: {s.usage.input_tokens:,}")
    lines.append(f"- Cache creation: {s.usage.cache_creation:,}")
    lines.append(f"- Cache read: {s.usage.cache_read:,}")
    lines.append(f"- Output: {s.usage.output_tokens:,}")
    lines.append(f"- Cache hit rate: {s.usage.cache_hit_rate:.1%}")
    if s.assistant_turns > 0:
        lines.append(f"- Tokens/turn: {s.usage.total // s.assistant_turns:,}")
    lines.append("")

    # Interaction shape
    lines.append("## Interaction Shape")
    lines.append(f"- Prompts: {s.prompt_count}")
    lines.append(f"- Assistant turns: {s.assistant_turns}")
    lines.append(f"- Tool calls: {len(s.tool_calls)}")
    if s.prompt_count > 0:
        lines.append(f"- Turns/prompt: {s.assistant_turns / s.prompt_count:.1f}")
        lines.append(f"- Tools/prompt: {len(s.tool_calls) / s.prompt_count:.1f}")
    lines.append("")

    # Tool breakdown
    if s.tool_calls:
        tool_counts = Counter(tc.name for tc in s.tool_calls)
        lines.append("## Tool Calls")
        lines.append("| Tool | Count | % |")
        lines.append("|------|-------|---|")
        for tool, count in tool_counts.most_common():
            pct = count / len(s.tool_calls) * 100
            lines.append(f"| {tool} | {count} | {pct:.0f}% |")
        lines.append("")

        # Tool sequence
        seq = [tc.name for tc in s.tool_calls[:40]]
        lines.append("## Tool Sequence (first 40)")
        lines.append(" -> ".join(seq))
        lines.append("")

        # Behavioral signals for this session
        reads = sum(1 for tc in s.tool_calls if tc.name in ("Read", "Grep", "Glob"))
        edits = sum(1 for tc in s.tool_calls if tc.name in ("Edit", "Write"))
        if edits > 0:
            lines.append(f"- Read/Edit ratio: {reads / edits:.1f}:1")

    # Subagents
    if s.subagent_count > 0:
        lines.append("## Subagents")
        lines.append(f"- Count: {s.subagent_count}")
        lines.append(f"- Subagent tokens: {s.subagent_usage.total:,}")
        pct = s.subagent_usage.total / max(s.usage.total, 1) * 100
        lines.append(f"- % of session: {pct:.1f}%")
        lines.append("")

    # RTK commands overlapping this session
    start_dt = sources.parse_timestamp(s.timestamp_start)
    if start_dt:
        rtk_cmds = sources.read_rtk(since=start_dt, limit=200)
        if rtk_cmds and s.timestamp_end:
            in_range = [c for c in rtk_cmds if c.get("timestamp", "") <= s.timestamp_end]
            if in_range:
                total_saved = sum(c.get("saved_tokens", 0) for c in in_range)
                lines.append("## RTK Activity (overlapping)")
                lines.append(f"- Commands: {len(in_range)}")
                lines.append(f"- Tokens saved: {total_saved:,}")
                lines.append("")

    return lines


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

    parts = []
    for s in targets:
        parts.extend(_session_detail(s))
        parts.append("---")
        parts.append("")

    return "\n".join(parts)
