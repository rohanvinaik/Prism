"""Token Economics Lens.

RTK filtering savings + API token consumption + cache efficiency + subagent cost.
"""

from collections import defaultdict
from typing import Optional

from . import sources


def run(period: str = "week", project: str = "") -> str:
    since = sources.period_to_since(period)
    proj: Optional[str] = project or None

    sessions = list(sources.iter_sessions(since=since, project_filter=proj))
    rtk_cmds = sources.read_rtk(since=since, project_filter=proj)

    # Aggregate API usage
    total_usage = sources.TokenUsage()
    total_subagent = sources.TokenUsage()
    total_subagents = 0
    by_project: dict[str, sources.TokenUsage] = defaultdict(sources.TokenUsage)
    proj_session_counts: dict[str, int] = defaultdict(int)

    for s in sessions:
        total_usage = total_usage + s.usage
        total_subagent = total_subagent + s.subagent_usage
        total_subagents += s.subagent_count
        by_project[s.project] = by_project[s.project] + s.usage
        proj_session_counts[s.project] += 1

    # RTK savings
    rtk_input = sum(c.get("input_tokens", 0) for c in rtk_cmds)
    rtk_saved = sum(c.get("saved_tokens", 0) for c in rtk_cmds)

    lines = [f"# Token Economics — {period}", ""]

    # API usage
    lines.append("## API Token Consumption")
    lines.append(f"- Sessions: {len(sessions)}")
    lines.append(f"- Total tokens: {total_usage.total:,}")
    lines.append(f"  - Input: {total_usage.input_tokens:,}")
    lines.append(f"  - Cache creation: {total_usage.cache_creation:,}")
    lines.append(f"  - Cache read: {total_usage.cache_read:,}")
    lines.append(f"  - Output: {total_usage.output_tokens:,}")
    lines.append(f"- Cache hit rate: {total_usage.cache_hit_rate:.1%}")
    lines.append("")

    # Subagents
    if total_subagents > 0:
        pct = total_subagent.total / max(total_usage.total, 1) * 100
        lines.append("## Subagent Cost")
        lines.append(f"- Subagent sessions: {total_subagents}")
        lines.append(f"- Subagent tokens: {total_subagent.total:,}")
        lines.append(f"- % of total: {pct:.1f}%")
        lines.append("")

    # RTK savings
    if rtk_cmds:
        lines.append("## RTK Filtering Savings")
        lines.append(f"- Commands tracked: {len(rtk_cmds)}")
        lines.append(f"- Tokens before filtering: {rtk_input:,}")
        lines.append(f"- Tokens saved: {rtk_saved:,}")
        if rtk_input > 0:
            lines.append(f"- Savings rate: {rtk_saved / rtk_input:.1%}")
        lines.append("")

    # Combined efficiency
    if total_usage.total > 0 and rtk_saved > 0:
        effective_total = total_usage.total + rtk_saved
        lines.append("## Combined Efficiency")
        lines.append(f"- Tokens if no caching or filtering: ~{effective_total:,}")
        lines.append(f"- Cache savings: {total_usage.cache_read:,}")
        lines.append(f"- RTK savings: {rtk_saved:,}")
        total_saved = total_usage.cache_read + rtk_saved
        lines.append(f"- Total saved: {total_saved:,} ({total_saved / effective_total:.0%})")
        lines.append("")

    # By project
    if by_project:
        sorted_projs = sorted(by_project.items(), key=lambda x: x[1].total, reverse=True)[:10]
        lines.append("## Top Projects by Token Consumption")
        lines.append("| Project | Tokens | Cache Hit | Sessions |")
        lines.append("|---------|--------|-----------|----------|")
        for p, usage in sorted_projs:
            lines.append(
                f"| {p} | {usage.total:,} | {usage.cache_hit_rate:.0%} | {proj_session_counts[p]} |"
            )
        lines.append("")

    # Costliest sessions
    costly = sorted(sessions, key=lambda s: s.usage.total, reverse=True)[:5]
    if costly:
        lines.append("## Costliest Sessions")
        for s in costly:
            ts = s.timestamp_start[:16] if s.timestamp_start else "?"
            lines.append(
                f"- **{s.project}** [{ts}]: {s.usage.total:,} tokens "
                f"({s.prompt_count} prompts, {len(s.tool_calls)} tool calls)"
            )
        lines.append("")

    return "\n".join(lines)
