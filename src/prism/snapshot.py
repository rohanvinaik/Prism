"""Snapshot: quick multi-lens composite view."""

from collections import Counter

from . import engine, sources


def run(period: str = "today", project: str = "") -> str:
    since = sources.period_to_since(period)
    label = {"today": "Today", "week": "This Week", "month": "This Month"}.get(period, period)
    proj = project or None

    sessions = list(sources.iter_sessions(since=since, project_filter=proj))
    rtk_cmds = sources.read_rtk(since=since, project_filter=proj)

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

    # -- Compact summary (always returned) --
    lines = [f"# Prism Snapshot — {label}", ""]
    lines.append("## At a Glance")
    lines.append(f"- Sessions: {len(sessions)} | Projects: {len(projects_active)}")
    lines.append(f"- Prompts: {total_prompts} | Tool calls: {total_tools}")
    lines.append(f"- API tokens: {total_usage.total:,} (cache hit: {total_usage.cache_hit_rate:.0%})")
    lines.append(f"- RTK saved: {rtk_saved:,}")

    if tool_counts:
        top3 = ", ".join(f"{t}({c})" for t, c in tool_counts.most_common(3))
        lines.append(f"- Top tools: {top3}")

    reads = sum(tool_counts.get(t, 0) for t in ("Read", "Grep", "Glob"))
    edits = sum(tool_counts.get(t, 0) for t in ("Edit", "Write"))
    if edits > 0:
        lines.append(f"- Read/Edit: {reads / edits:.1f}:1")

    # Cognitive + quality (single-line each)
    hours = 24 if period == "today" else 168
    mneme = sources.read_mneme_recent(hours=hours)
    if mneme and mneme.get("event_count", 0) > 0:
        anchors = [a["label"] for a in mneme.get("top_anchors", [])[:3]]
        if anchors:
            lines.append(f"- Cognitive focus: {', '.join(anchors)}")

    lg_sessions = sources.read_lintgate_sessions()
    if lg_sessions:
        for data in lg_sessions.values():
            traj = data.get("coherence_trajectory", [])
            if traj:
                lines.append(f"- Code coherence: {traj[-1]}")
                break

    # Real-time hook data (bridge file has latest session efficiency)
    bridge = engine.read_bridge()
    if bridge:
        eff = bridge.get("efficiency_score")
        err_rate = bridge.get("error_rate", 0)
        if eff is not None:
            lines.append(f"- Session efficiency: {eff}/100 (error rate: {err_rate:.0%})")

    summary = "\n".join(lines)

    # -- Full data (written to disk) --
    full_data = {
        "period": period,
        "sessions": len(sessions),
        "projects": sorted(projects_active),
        "prompts": total_prompts,
        "tool_calls_total": total_tools,
        "tokens": {
            "input": total_usage.input_tokens,
            "cache_creation": total_usage.cache_creation,
            "cache_read": total_usage.cache_read,
            "output": total_usage.output_tokens,
            "total": total_usage.total,
            "cache_hit_rate": round(total_usage.cache_hit_rate, 3),
        },
        "rtk_saved": rtk_saved,
        "tool_distribution": dict(tool_counts.most_common()),
        "project_tokens": dict(sorted(proj_tokens.items(), key=lambda x: -x[1])),
        "cognitive": mneme if mneme else {},
        "lintgate_coherence": None,
    }
    if lg_sessions:
        for data in lg_sessions.values():
            traj = data.get("coherence_trajectory", [])
            if traj:
                full_data["lintgate_coherence"] = traj
                break

    aid = engine.save_snapshot("snapshot", summary, full_data)
    lines.append("")
    lines.append(f"_snapshot: {aid}_")

    return "\n".join(lines)
