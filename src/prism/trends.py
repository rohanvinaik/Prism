"""Trends Lens — cross-session intelligence from hook daily summaries.

Reads from ~/.claude/prism/daily/*.jsonl (populated by Stop hooks).
This is the cheapest data source — already aggregated, one line per session.
No JSONL scanning required.

Detects: efficiency drift, error rate changes, tool distribution shifts,
subagent usage patterns, session duration trends.
"""

from collections import Counter, defaultdict

from . import engine


def _bucket_by_date(summaries: list[dict]) -> dict[str, list[dict]]:
    """Group summaries by date (from timestamp)."""
    by_date: dict[str, list[dict]] = defaultdict(list)
    for s in summaries:
        ts = s.get("ts", "")
        date = ts[:10] if len(ts) >= 10 else "unknown"
        by_date[date].append(s)
    return dict(sorted(by_date.items()))


def _trend_direction(values: list[float]) -> str:
    """Simple trend detection: compare first half to second half."""
    if len(values) < 4:
        return "insufficient data"
    mid = len(values) // 2
    first_avg = sum(values[:mid]) / mid
    second_avg = sum(values[mid:]) / (len(values) - mid)
    diff = second_avg - first_avg
    if abs(diff) < 0.05 * max(abs(first_avg), 1):
        return "stable"
    return "rising" if diff > 0 else "falling"


def _compute_trends(summaries: list[dict]) -> dict:
    """Compute cross-session trend metrics."""
    if not summaries:
        return {}

    efficiency_scores = [s["efficiency_score"] for s in summaries if "efficiency_score" in s]
    error_rates = [s["error_rate"] for s in summaries if "error_rate" in s]
    tool_call_counts = [s["tool_calls"] for s in summaries if "tool_calls" in s]
    durations = [s["duration_sec"] for s in summaries if s.get("duration_sec")]

    # Aggregate tool distribution across sessions
    all_tools: Counter[str] = Counter()
    for s in summaries:
        dist = s.get("tool_distribution", {})
        for tool, count in dist.items():
            all_tools[tool] += count

    trends: dict = {
        "sessions_analyzed": len(summaries),
    }

    if efficiency_scores:
        trends["efficiency"] = {
            "current": efficiency_scores[-1],
            "avg": round(sum(efficiency_scores) / len(efficiency_scores), 1),
            "min": min(efficiency_scores),
            "max": max(efficiency_scores),
            "trend": _trend_direction([float(x) for x in efficiency_scores]),
        }

    if error_rates:
        trends["error_rate"] = {
            "current": error_rates[-1],
            "avg": round(sum(error_rates) / len(error_rates), 3),
            "trend": _trend_direction(error_rates),
        }

    if tool_call_counts:
        trends["tool_calls"] = {
            "avg_per_session": round(sum(tool_call_counts) / len(tool_call_counts), 1),
            "total": sum(tool_call_counts),
            "trend": _trend_direction([float(x) for x in tool_call_counts]),
        }

    if durations:
        avg_min = sum(durations) / len(durations) / 60
        trends["duration"] = {
            "avg_minutes": round(avg_min, 1),
            "trend": _trend_direction([float(x) for x in durations]),
        }

    if all_tools:
        trends["top_tools"] = dict(all_tools.most_common(10))

    return trends


def analyze(days: int = 7, project: str = "") -> str:
    summaries = engine.read_daily_summaries(days=days)

    # Filter by project if specified
    if project:
        summaries = [s for s in summaries if project.lower() in (s.get("project") or "").lower()]

    trends = _compute_trends(summaries)

    if not trends:
        return (
            "# Trends — No Data Yet\n\n"
            "Hook daily summaries accumulate as you use Claude Code. "
            "After a few sessions, cross-session trends will appear here.\n\n"
            f"Daily summary files: ~/.claude/prism/daily/ ({len(summaries)} entries)"
        )

    # -- Compact summary --
    lines = [f"# Cross-Session Trends — last {days} days", ""]
    lines.append(f"- Sessions: {trends['sessions_analyzed']}")

    eff = trends.get("efficiency")
    if eff:
        lines.append(f"- Efficiency: {eff['current']}/100 (avg {eff['avg']}, {eff['trend']})")

    err = trends.get("error_rate")
    if err:
        lines.append(f"- Error rate: {err['current']:.0%} (avg {err['avg']:.0%}, {err['trend']})")

    tc = trends.get("tool_calls")
    if tc:
        lines.append(
            f"- Tool calls: {tc['total']} total, {tc['avg_per_session']}/session ({tc['trend']})"
        )

    dur = trends.get("duration")
    if dur:
        lines.append(f"- Session duration: {dur['avg_minutes']}min avg ({dur['trend']})")

    top = trends.get("top_tools")
    if top:
        top3 = ", ".join(f"{t}({c})" for t, c in list(top.items())[:3])
        lines.append(f"- Top tools: {top3}")

    summary = "\n".join(lines)

    # -- Full data to disk --
    full_data = {
        "days": days,
        "project_filter": project,
        "trends": trends,
        "by_date": _bucket_by_date(summaries),
    }

    aid = engine.save_snapshot("trends", summary, full_data)
    lines.append("")
    lines.append(f'_Details: prism_details("{aid}", section="trends" or "by_date")_')

    return "\n".join(lines)
