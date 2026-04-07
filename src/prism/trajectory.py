"""Trajectory Lens.

Quality, decision, and cognitive trends over time.
"""

from collections import defaultdict
from datetime import datetime
from typing import Optional

from . import engine, sources


def _activity_data(stats: dict[str, dict], since: Optional[datetime]) -> dict:
    """Compute activity metrics from stats-cache."""
    cutoff_str = since.strftime("%Y-%m-%d") if since else "0000-00-00"
    daily = {k: v for k, v in stats.items() if k >= cutoff_str}
    if not daily:
        return {}

    total_msgs = sum(d.get("messageCount", 0) for d in daily.values())
    total_sess = sum(d.get("sessionCount", 0) for d in daily.values())
    total_tools = sum(d.get("toolCallCount", 0) for d in daily.values())
    active_days = len([d for d in daily.values() if d.get("sessionCount", 0) > 0])

    weekly: dict[str, dict[str, int]] = defaultdict(
        lambda: {"messages": 0, "sessions": 0, "tools": 0}
    )
    for date_str, data in sorted(daily.items()):
        try:
            dt = datetime.strptime(date_str, "%Y-%m-%d")
            week_key = dt.strftime("%Y-W%W")
        except ValueError:
            continue
        weekly[week_key]["messages"] += data.get("messageCount", 0)
        weekly[week_key]["sessions"] += data.get("sessionCount", 0)
        weekly[week_key]["tools"] += data.get("toolCallCount", 0)

    return {
        "total_days": len(daily),
        "active_days": active_days,
        "messages": total_msgs,
        "sessions": total_sess,
        "tool_calls": total_tools,
        "weekly": dict(sorted(weekly.items())),
    }


def _quality_data(since: Optional[datetime]) -> dict:
    """Compute LintGate quality metrics."""
    lg_metrics = sources.read_lintgate_metrics(since=since)
    if not lg_metrics:
        return {}

    event_counts: dict[str, int] = defaultdict(int)
    for m in lg_metrics:
        event = m.get("event", "")
        if event:
            event_counts[event] += 1

    perf_events = [m for m in lg_metrics if m.get("event") == "performance_analysis"]
    ratios = [e["purity_ratio"] for e in perf_events if "purity_ratio" in e]

    return {
        "lint_runs": event_counts.get("lint_run", 0),
        "controlplane_runs": event_counts.get("controlplane_run", 0),
        "purity_first": round(ratios[0], 3) if ratios else None,
        "purity_last": round(ratios[-1], 3) if ratios else None,
        "event_counts": dict(sorted(event_counts.items(), key=lambda x: -x[1])[:10]),
    }


def _decisions_data(since: Optional[datetime]) -> dict:
    """Compute Continuity decision metrics."""
    decisions = sources.read_continuity_decisions(since=since)
    if not decisions:
        return {}

    categories: dict[str, int] = defaultdict(int)
    outcomes: dict[str, int] = defaultdict(int)
    for d in decisions:
        categories[d.get("category", "unknown")] += 1
        outcomes[d.get("outcome", "unknown")] += 1

    return {
        "total": len(decisions),
        "by_category": dict(categories),
        "by_outcome": dict(outcomes),
    }


def run(period: str = "month") -> str:
    since = sources.period_to_since(period)

    stats = sources.read_stats_cache()
    activity = _activity_data(stats, since) if stats else {}
    quality = _quality_data(since)
    decisions = _decisions_data(since)

    hours = 720 if period in ("month", "quarter") else 168
    mneme = sources.read_mneme_recent(hours=hours)

    # -- Compact summary --
    lines = [f"# Trajectory — {period}", ""]

    if activity:
        lines.append(f"- Activity: {activity['active_days']}/{activity['total_days']} active days, {activity['sessions']} sessions, {activity['messages']:,} messages")
    if quality:
        lines.append(f"- LintGate: {quality['lint_runs']} lint, {quality['controlplane_runs']} CP runs")
        if quality.get("purity_first") is not None:
            lines.append(f"- Purity: {quality['purity_first']:.2f} -> {quality['purity_last']:.2f}")
    if decisions:
        lines.append(f"- Decisions: {decisions['total']} ({', '.join(f'{k}={v}' for k, v in sorted(decisions['by_category'].items(), key=lambda x: -x[1])[:3])})")
    if mneme and mneme.get("event_count", 0) > 0:
        anchors = [a["label"] for a in mneme.get("top_anchors", [])[:3]]
        lines.append(f"- Cognitive: {mneme['event_count']} events, focus: {', '.join(anchors)}")

    if len(lines) <= 2:
        lines.append("No data available for this period.")

    summary = "\n".join(lines)

    # -- Full data to disk --
    full_data = {
        "period": period,
        "activity": activity,
        "quality": quality,
        "decisions": decisions,
        "cognitive": mneme if mneme else {},
    }

    aid = engine.save_snapshot("trajectory", summary, full_data)
    lines.append("")
    lines.append(f"_Details: prism_details(\"{aid}\", section=\"activity.weekly\" or \"quality\" or \"decisions\")_")

    return "\n".join(lines)
