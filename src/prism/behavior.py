"""Behavioral Lens.

Tool choreography, call sequences, read/edit ratios,
and LintGate habit signals.
"""

from collections import Counter

from . import engine, sources


def _count_tools(sessions: list[sources.SessionData]) -> Counter[str]:
    """Count tool usage across all sessions."""
    counts: Counter[str] = Counter()
    for s in sessions:
        for tc in s.tool_calls:
            counts[tc.name] += 1
    return counts


def _count_transitions(sessions: list[sources.SessionData]) -> Counter[tuple[str, str]]:
    """Count tool-to-tool transition pairs across all sessions."""
    transitions: Counter[tuple[str, str]] = Counter()
    for s in sessions:
        prev = None
        for tc in s.tool_calls:
            if prev:
                transitions[(prev, tc.name)] += 1
            prev = tc.name
    return transitions


def _infer_workflow_mode(tool_counts: Counter[str], total_calls: int) -> str:
    if total_calls == 0:
        return "Idle"
    reads = sum(tool_counts.get(t, 0) for t in ("Read", "Grep", "Glob"))
    edits = sum(tool_counts.get(t, 0) for t in ("Edit", "Write"))
    read_pct = reads / total_calls
    edit_pct = edits / total_calls
    bash_pct = tool_counts.get("Bash", 0) / total_calls
    agent_pct = tool_counts.get("Agent", 0) / total_calls

    if read_pct > 0.5 and edit_pct < 0.1:
        return "Explore"
    if edit_pct > 0.3 and read_pct < 0.2:
        return "Surgical"
    if bash_pct > 0.3:
        return "Shell-heavy"
    if agent_pct > 0.1:
        return "Delegating"
    return "Balanced"


def _skew_warnings(edits, lines, reads, tool_counts, total_calls):
    if total_calls > 50:
        bash_pct = tool_counts.get("Bash", 0) / total_calls
        if bash_pct > 0.4 and reads > 0:
            bash_to_read = tool_counts.get("Bash", 0) / reads
            lines.append(
                f"- ⚠ Bash is {bash_to_read:.1f}x Read+Grep+Glob"
                " — consider dedicated tools for file ops"
            )
        if reads > 0 and edits > 0 and reads / edits < 1.5:
            lines.append(
                f"- ⚠ Read/Edit ratio is low ({reads / edits:.1f}:1)"
                " — more reading before editing may help"
            )
    return lines


def analyze(period: str = "week", project: str = "") -> str:
    since = sources.period_to_since(period)
    proj: str | None = project or None
    sessions = list(sources.iter_sessions(since=since, project_filter=proj))

    tool_counts = _count_tools(sessions)
    tool_transitions = _count_transitions(sessions)
    total_calls = sum(tool_counts.values())
    total_prompts = sum(s.prompt_count for s in sessions)
    total_turns = sum(s.assistant_turns for s in sessions)

    reads = sum(tool_counts.get(t, 0) for t in ("Read", "Grep", "Glob"))
    edits = sum(tool_counts.get(t, 0) for t in ("Edit", "Write"))
    mode = _infer_workflow_mode(tool_counts, total_calls)

    # -- Compact summary --
    lines = [f"# Behavioral Profile — {period}", ""]
    lines.append(f"- Sessions: {len(sessions)} | Prompts: {total_prompts} | Turns: {total_turns}")
    lines.append(
        f"- Tool calls: {total_calls} | Tools/turn: {total_calls / max(total_turns, 1):.1f}"
    )
    if edits > 0:
        lines.append(f"- Read/Edit: {reads / edits:.1f}:1 | Mode: **{mode}**")
    else:
        lines.append(f"- Reads: {reads}, Edits: {edits} | Mode: **{mode}**")

    top5 = ", ".join(f"{t}({c})" for t, c in tool_counts.most_common(5))
    if top5:
        lines.append(f"- Top tools: {top5}")

    top3_seq = ", ".join(f"{a}>{b}({c})" for (a, b), c in tool_transitions.most_common(3))
    if top3_seq:
        lines.append(f"- Top sequences: {top3_seq}")

    # Interpretive nudge when ratios are notably skewed
    lines = _skew_warnings(edits, lines, reads, tool_counts, total_calls)

    # LintGate signals (single line, only if LintGate is present)
    lg_sessions = sources.read_lintgate_sessions()
    if lg_sessions:
        for session_data in lg_sessions.values():
            compass = session_data.get("behavior_compass", {})
            signals = compass.get("signals_state", {}) if compass else {}
            if signals:
                hs = signals.get("habit_score", "?")
                lines.append(f"- LintGate habit score: {hs}")
                break

    summary = "\n".join(lines)

    # -- Full data to disk --
    full_data = {
        "period": period,
        "project_filter": project,
        "sessions": len(sessions),
        "prompts": total_prompts,
        "assistant_turns": total_turns,
        "tool_calls": total_calls,
        "workflow_mode": mode,
        "tool_distribution": dict(tool_counts.most_common()),
        "tool_transitions": {f"{a}>{b}": c for (a, b), c in tool_transitions.most_common(20)},
        "signals": {
            "reads": reads,
            "edits": edits,
            "read_edit_ratio": round(reads / max(edits, 1), 2),
            "bash_calls": tool_counts.get("Bash", 0),
            "agent_spawns": tool_counts.get("Agent", 0),
        },
        "lintgate_signals": {},
    }
    if lg_sessions:
        for session_data in lg_sessions.values():
            compass = session_data.get("behavior_compass", {})
            if compass:
                full_data["lintgate_signals"] = {
                    "signals_state": compass.get("signals_state", {}),
                    "coherence_trajectory": session_data.get("coherence_trajectory", []),
                }
                break

    aid = engine.save_snapshot("behavior", summary, full_data)
    lines.append("")
    lines.append(
        f'_Details: prism_details("{aid}", section="tool_distribution" or "tool_transitions")_'
    )

    return "\n".join(lines)
