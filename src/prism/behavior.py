"""Behavioral Lens.

Tool choreography, call sequences, read/edit ratios,
and LintGate habit signals.
"""

from collections import Counter
from typing import Optional

from . import sources


def _aggregate_tools(
    sessions: list[sources.SessionData],
) -> tuple[Counter[str], Counter[tuple[str, str]]]:
    """Count tool usage and pairwise transitions across sessions."""
    counts: Counter[str] = Counter()
    transitions: Counter[tuple[str, str]] = Counter()
    for s in sessions:
        prev = None
        for tc in s.tool_calls:
            counts[tc.name] += 1
            if prev:
                transitions[(prev, tc.name)] += 1
            prev = tc.name
    return counts, transitions


def _tool_tables(
    tool_counts: Counter[str],
    tool_transitions: Counter[tuple[str, str]],
    total_calls: int,
) -> list[str]:
    """Format tool distribution and transition tables."""
    lines: list[str] = []
    if tool_counts:
        lines.append("## Tool Usage Distribution")
        lines.append("| Tool | Count | % |")
        lines.append("|------|-------|---|")
        for tool, count in tool_counts.most_common(15):
            pct = count / total_calls * 100
            lines.append(f"| {tool} | {count:,} | {pct:.1f}% |")
        lines.append("")

    if tool_transitions:
        lines.append("## Common Tool Sequences")
        lines.append("| From > To | Count |")
        lines.append("|-----------|-------|")
        for (a, b), count in tool_transitions.most_common(10):
            lines.append(f"| {a} > {b} | {count} |")
        lines.append("")
    return lines


def _infer_workflow_mode(tool_counts: Counter[str], total_calls: int) -> str:
    """Heuristic workflow mode classification from tool distribution."""
    if total_calls == 0:
        return "Idle"
    reads = sum(tool_counts.get(t, 0) for t in ("Read", "Grep", "Glob"))
    edits = sum(tool_counts.get(t, 0) for t in ("Edit", "Write"))
    read_pct = reads / total_calls
    edit_pct = edits / total_calls
    bash_pct = tool_counts.get("Bash", 0) / total_calls
    agent_pct = tool_counts.get("Agent", 0) / total_calls

    if read_pct > 0.5 and edit_pct < 0.1:
        return "Explore (read-heavy, few edits)"
    if edit_pct > 0.3 and read_pct < 0.2:
        return "Surgical (edit-heavy, targeted)"
    if bash_pct > 0.3:
        return "Shell-heavy (execution-oriented)"
    if agent_pct > 0.1:
        return "Delegating (high subagent usage)"
    return "Balanced"


def _signals_section(tool_counts: Counter[str], total_calls: int) -> list[str]:
    """Compute and format behavioral signals."""
    reads = sum(tool_counts.get(t, 0) for t in ("Read", "Grep", "Glob"))
    edits = sum(tool_counts.get(t, 0) for t in ("Edit", "Write"))
    bash_count = tool_counts.get("Bash", 0)
    agent_count = tool_counts.get("Agent", 0)

    lines = ["## Behavioral Signals"]
    if edits > 0:
        lines.append(f"- Read/Edit ratio: {reads / edits:.1f}:1")
    else:
        lines.append(f"- Reads: {reads}, Edits: {edits}")
    lines.append(f"- Bash calls: {bash_count} ({bash_count / max(total_calls, 1) * 100:.0f}%)")
    lines.append(f"- Agent spawns: {agent_count} ({agent_count / max(total_calls, 1) * 100:.0f}%)")
    lines.append(f"- Inferred workflow mode: **{_infer_workflow_mode(tool_counts, total_calls)}**")
    lines.append("")
    return lines


def _lintgate_section() -> list[str]:
    """Read and format LintGate behavioral compass signals."""
    lg_sessions = sources.read_lintgate_sessions()
    if not lg_sessions:
        return []

    lines = ["## LintGate Behavioral Signals"]
    for session_data in lg_sessions.values():
        compass = session_data.get("behavior_compass", {})
        if not compass:
            continue
        signals = compass.get("signals_state", {})
        if signals:
            lines.append(f"- Habit score: {signals.get('habit_score', 'n/a')}")
            lines.append(f"- Read/edit ratio (LG): {signals.get('read_edit_ratio', 'n/a')}")
            lines.append(f"- Execute %: {signals.get('execute_pct', 'n/a')}")
            lines.append(f"- Same-file ratio: {signals.get('same_file_ratio', 'n/a')}")
        trajectory = session_data.get("coherence_trajectory", [])
        if trajectory:
            lines.append(f"- Coherence: {' > '.join(trajectory[-5:])}")
        break  # Most recent only
    lines.append("")
    return lines


def run(period: str = "week", project: str = "") -> str:
    since = sources.period_to_since(period)
    proj: Optional[str] = project or None
    sessions = list(sources.iter_sessions(since=since, project_filter=proj))

    tool_counts, tool_transitions = _aggregate_tools(sessions)
    total_calls = sum(tool_counts.values())
    total_prompts = sum(s.prompt_count for s in sessions)
    total_turns = sum(s.assistant_turns for s in sessions)

    lines = [f"# Behavioral Profile — {period}", ""]

    lines.append("## Session Activity")
    lines.append(f"- Sessions: {len(sessions)}")
    lines.append(f"- Total prompts: {total_prompts}")
    lines.append(f"- Total assistant turns: {total_turns}")
    if total_prompts > 0:
        lines.append(f"- Turns per prompt: {total_turns / total_prompts:.1f}")
    lines.append(f"- Tool calls: {total_calls}")
    if total_turns > 0:
        lines.append(f"- Tools per turn: {total_calls / total_turns:.1f}")
    lines.append("")

    lines.extend(_tool_tables(tool_counts, tool_transitions, total_calls))
    lines.extend(_signals_section(tool_counts, total_calls))
    lines.extend(_lintgate_section())

    return "\n".join(lines)
