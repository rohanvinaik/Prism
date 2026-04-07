"""Trajectory Lens.

Quality, decision, and cognitive trends over time.
"""

from collections import defaultdict
from datetime import datetime

from . import engine, sources


def _bucket_weekly(daily: dict[str, dict]) -> dict[str, dict[str, int]]:
    """Bucket daily stats into weekly aggregates."""
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
    return dict(sorted(weekly.items()))


def _activity_data(stats: dict[str, dict], since: datetime | None) -> dict:
    """Compute activity metrics from stats-cache."""
    cutoff_str = since.strftime("%Y-%m-%d") if since else "0000-00-00"
    daily = {k: v for k, v in stats.items() if k >= cutoff_str}
    if not daily:
        return {}

    return {
        "total_days": len(daily),
        "active_days": len([d for d in daily.values() if d.get("sessionCount", 0) > 0]),
        "messages": sum(d.get("messageCount", 0) for d in daily.values()),
        "sessions": sum(d.get("sessionCount", 0) for d in daily.values()),
        "tool_calls": sum(d.get("toolCallCount", 0) for d in daily.values()),
        "weekly": _bucket_weekly(daily),
    }


def _quality_data(since: datetime | None) -> dict:
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


def _decisions_data(since: datetime | None) -> dict:
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


def _filter_decisions_by_project(
    all_decisions: dict, project: str, since: datetime | None
) -> dict:
    """Narrow decisions to a specific project, or return as-is."""
    if not project or not all_decisions:
        return all_decisions
    raw = sources.read_continuity_decisions(since=since)
    matched = [d for d in raw if project.lower() in (d.get("project_name") or "").lower()]
    if not matched:
        return {}
    categories: dict[str, int] = defaultdict(int)
    outcomes: dict[str, int] = defaultdict(int)
    for d in matched:
        categories[d.get("category", "unknown")] += 1
        outcomes[d.get("outcome", "unknown")] += 1
    return {
        "total": len(matched),
        "by_category": dict(categories),
        "by_outcome": dict(outcomes),
    }


def analyze(period: str = "month", project: str = "") -> str:
    since = sources.period_to_since(period)

    # stats-cache and LintGate metrics are global (not per-project).
    # Continuity decisions can be filtered by project name.
    stats = sources.read_stats_cache()
    activity = _activity_data(stats, since) if stats else {}
    quality = _quality_data(since)
    decisions = _filter_decisions_by_project(_decisions_data(since), project, since)

    hours = 720 if period in ("month", "quarter") else 168
    mneme = sources.read_mneme_recent(hours=hours)

    # -- Compact summary --
    lines = [f"# Trajectory — {period}", ""]

    if activity:
        lines.append(
            f"- Activity: {activity['active_days']}/{activity['total_days']} active days,"
            f" {activity['sessions']} sessions, {activity['messages']:,} messages"
        )
        weekly = activity.get("weekly", {})
        weeks = list(weekly.values())
        if len(weeks) >= 2:
            prev, curr = weeks[-2], weeks[-1]
            for key in ("sessions", "messages", "tools"):
                p, c = prev.get(key, 0), curr.get(key, 0)
                if p > 0:
                    pct = (c - p) / p * 100
                    arrow = "↑" if pct > 10 else "↓" if pct < -10 else "→"
                    lines.append(f"  {key}: {arrow} {abs(pct):.0f}% week-over-week")
    if quality:
        lines.append(
            f"- LintGate: {quality['lint_runs']} lint, {quality['controlplane_runs']} CP runs"
        )
        if quality.get("purity_first") is not None:
            lines.append(f"- Purity: {quality['purity_first']:.2f} -> {quality['purity_last']:.2f}")
    if decisions:
        top3 = ", ".join(
            f"{k}={v}"
            for k, v in sorted(decisions["by_category"].items(), key=lambda x: -x[1])[:3]
        )
        lines.append(f"- Decisions: {decisions['total']} ({top3})")
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
    lines.append(
        f'_Details: prism_details("{aid}", section="activity.weekly" or "quality" or "decisions")_'
    )

    return "\n".join(lines)
