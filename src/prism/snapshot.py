"""Snapshot: quick multi-lens composite view."""

from collections import Counter

from . import sources


def run(period: str = "today") -> str:
    since = sources.period_to_since(period)
    label = {"today": "Today", "week": "This Week", "month": "This Month"}.get(period, period)

    sessions = list(sources.iter_sessions(since=since))
    rtk_cmds = sources.read_rtk(since=since)

    # Aggregate
    total_usage = sources.TokenUsage()
    total_tools = 0
    total_prompts = 0
    tool_counts: Counter[str] = Counter()
    projects_active: set[str] = set()
    proj_tokens: dict[str, int] = {}

    for s in sessions:
        total_usage = total_usage + s.usage
        total_tools += len(s.tool_calls)
        total_prompts += s.prompt_count
        projects_active.add(s.project)
        proj_tokens[s.project] = proj_tokens.get(s.project, 0) + s.usage.total
        for tc in s.tool_calls:
            tool_counts[tc.name] += 1

    rtk_saved = sum(c.get("saved_tokens", 0) for c in rtk_cmds)

    lines = [f"# Prism Snapshot — {label}", ""]

    lines.append("## At a Glance")
    lines.append(f"- Sessions: {len(sessions)}")
    lines.append(f"- Projects: {len(projects_active)}")
    lines.append(f"- Prompts: {total_prompts}")
    lines.append(f"- API tokens: {total_usage.total:,}")
    lines.append(f"- Cache hit rate: {total_usage.cache_hit_rate:.0%}")
    lines.append(f"- Tool calls: {total_tools}")
    lines.append(f"- RTK tokens saved: {rtk_saved:,}")
    lines.append("")

    # Top tools
    if tool_counts:
        lines.append("## Top Tools")
        for tool, count in tool_counts.most_common(5):
            lines.append(f"- {tool}: {count}")
        lines.append("")

    # Active projects
    if proj_tokens:
        sorted_projs = sorted(proj_tokens.items(), key=lambda x: -x[1])[:5]
        lines.append("## Active Projects")
        for proj, tokens in sorted_projs:
            lines.append(f"- {proj}: {tokens:,} tokens")
        lines.append("")

    # Behavioral signal
    reads = sum(tool_counts.get(t, 0) for t in ("Read", "Grep", "Glob"))
    edits = sum(tool_counts.get(t, 0) for t in ("Edit", "Write"))
    if edits > 0:
        lines.append("## Quick Signals")
        lines.append(f"- Read/Edit ratio: {reads / edits:.1f}:1")
        agent_count = tool_counts.get("Agent", 0)
        if agent_count:
            lines.append(f"- Agent spawns: {agent_count}")
        lines.append("")

    # Cognitive weather
    hours = 24 if period == "today" else 168
    mneme = sources.read_mneme_recent(hours=hours)
    if mneme and mneme.get("event_count", 0) > 0:
        lines.append("## Cognitive State")
        lines.append(f"- Events: {mneme['event_count']}")
        if mneme.get("top_anchors"):
            anchors = [a["label"] for a in mneme["top_anchors"][:3]]
            lines.append(f"- Focus: {', '.join(anchors)}")
        lines.append("")

    # LintGate coherence
    lg_sessions = sources.read_lintgate_sessions()
    if lg_sessions:
        for data in lg_sessions.values():
            trajectory = data.get("coherence_trajectory", [])
            if trajectory:
                lines.append("## Code Quality")
                lines.append(f"- Coherence: {trajectory[-1]}")
                break
        lines.append("")

    return "\n".join(lines)
