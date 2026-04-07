"""Token Economics Lens.

RTK filtering savings + API token consumption + cache efficiency + subagent cost.
"""

from collections import defaultdict

from . import engine, sources


def analyze(period: str = "week", project: str = "") -> str:
    since = sources.period_to_since(period)
    proj: str | None = project or None

    sessions = list(sources.iter_sessions(since=since, project_filter=proj))
    rtk_cmds = sources.read_rtk(since=since, project_filter=proj)

    # Aggregate
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

    rtk_input = sum(c.get("input_tokens", 0) for c in rtk_cmds)
    rtk_saved = sum(c.get("saved_tokens", 0) for c in rtk_cmds)

    # -- Compact summary --
    lines = [f"# Token Economics — {period}", ""]
    lines.append(f"- Sessions: {len(sessions)} | API tokens: {total_usage.total:,}")
    lines.append(
        f"- Cache hit: {total_usage.cache_hit_rate:.1%} | Output: {total_usage.output_tokens:,}"
    )
    if total_subagents:
        pct = total_subagent.total / max(total_usage.total, 1) * 100
        lines.append(
            f"- Subagents: {total_subagents} ({total_subagent.total:,} tokens, {pct:.0f}%)"
        )
    if rtk_cmds:
        lines.append(
            f"- RTK: {len(rtk_cmds)} cmds, {rtk_saved:,} saved"
            f" ({rtk_saved / max(rtk_input, 1):.0%})"
        )
        if total_usage.total > 0 and rtk_saved > 0:
            total_saved = total_usage.cache_read + rtk_saved
            effective = total_usage.total + rtk_saved
            lines.append(
                f"- Combined efficiency: {total_saved:,} saved ({total_saved / effective:.0%})"
            )

    # Top 3 projects inline
    sorted_projs = sorted(by_project.items(), key=lambda x: x[1].total, reverse=True)[:3]
    if sorted_projs:
        proj_strs = [f"{p}({u.total:,})" for p, u in sorted_projs]
        lines.append(f"- Top projects: {', '.join(proj_strs)}")

    summary = "\n".join(lines)

    # -- Full data to disk --
    full_data = {
        "period": period,
        "project_filter": project,
        "api_usage": {
            "sessions": len(sessions),
            "input": total_usage.input_tokens,
            "cache_creation": total_usage.cache_creation,
            "cache_read": total_usage.cache_read,
            "output": total_usage.output_tokens,
            "total": total_usage.total,
            "cache_hit_rate": round(total_usage.cache_hit_rate, 3),
        },
        "subagents": {
            "count": total_subagents,
            "input": total_subagent.input_tokens,
            "cache_creation": total_subagent.cache_creation,
            "cache_read": total_subagent.cache_read,
            "output": total_subagent.output_tokens,
            "total": total_subagent.total,
        },
        "rtk": {
            "commands": len(rtk_cmds),
            "input_tokens": rtk_input,
            "saved_tokens": rtk_saved,
            "savings_rate": round(rtk_saved / max(rtk_input, 1), 3),
        },
        "by_project": {
            p: {
                "tokens": u.total,
                "cache_hit_rate": round(u.cache_hit_rate, 3),
                "sessions": proj_session_counts[p],
            }
            for p, u in sorted(by_project.items(), key=lambda x: x[1].total, reverse=True)
        },
        "costliest_sessions": [
            {
                "id": s.session_id[:12],
                "project": s.project,
                "tokens": s.usage.total,
                "prompts": s.prompt_count,
                "tool_calls": len(s.tool_calls),
                "started": s.timestamp_start,
            }
            for s in sorted(sessions, key=lambda s: s.usage.total, reverse=True)[:10]
        ],
    }

    aid = engine.save_snapshot("economics", summary, full_data)
    lines.append("")
    lines.append(f'_Details: prism_details("{aid}", section="by_project" or "costliest_sessions")_')

    return "\n".join(lines)
