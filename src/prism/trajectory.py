"""Trajectory Lens.

Quality, decision, and cognitive trends over time.
"""

from collections import defaultdict
from datetime import datetime
from typing import Optional

from . import sources


def _activity_section(stats: dict[str, dict], since: Optional[datetime]) -> list[str]:
    """Stats-cache daily rollups and weekly aggregation."""
    cutoff_str = since.strftime("%Y-%m-%d") if since else "0000-00-00"
    daily = {k: v for k, v in stats.items() if k >= cutoff_str}
    if not daily:
        return []

    total_msgs = sum(d.get("messageCount", 0) for d in daily.values())
    total_sess = sum(d.get("sessionCount", 0) for d in daily.values())
    total_tools = sum(d.get("toolCallCount", 0) for d in daily.values())
    active_days = len([d for d in daily.values() if d.get("sessionCount", 0) > 0])

    lines = ["## Activity Trend"]
    lines.append(f"- Active days: {active_days}/{len(daily)}")
    lines.append(f"- Total messages: {total_msgs:,}")
    lines.append(f"- Total sessions: {total_sess:,}")
    lines.append(f"- Total tool calls: {total_tools:,}")
    if active_days > 0:
        lines.append(f"- Avg sessions/active day: {total_sess / active_days:.1f}")
        lines.append(f"- Avg messages/active day: {total_msgs / active_days:.1f}")
    lines.append("")
    lines.extend(_weekly_table(daily))
    return lines


def _weekly_table(daily: dict[str, dict]) -> list[str]:
    """Aggregate daily data into weekly trend table."""
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

    if len(weekly) <= 1:
        return []

    lines = ["## Weekly Trend"]
    lines.append("| Week | Messages | Sessions | Tool Calls |")
    lines.append("|------|----------|----------|------------|")
    for week, data in sorted(weekly.items()):
        lines.append(
            f"| {week} | {data['messages']:,} "
            f"| {data['sessions']:,} | {data['tools']:,} |"
        )
    lines.append("")
    return lines


def _decisions_section(since: Optional[datetime]) -> list[str]:
    """Continuity decision activity."""
    decisions = sources.read_continuity_decisions(since=since)
    if not decisions:
        return []

    categories: dict[str, int] = defaultdict(int)
    outcomes: dict[str, int] = defaultdict(int)
    for d in decisions:
        categories[d.get("category", "unknown")] += 1
        outcomes[d.get("outcome", "unknown")] += 1

    lines = ["## Decision Activity (Continuity)"]
    lines.append(f"- Decisions recorded: {len(decisions)}")
    lines.append(
        "- By category: "
        + ", ".join(f"{k}={v}" for k, v in sorted(categories.items(), key=lambda x: -x[1]))
    )
    lines.append(
        "- By outcome: "
        + ", ".join(f"{k}={v}" for k, v in sorted(outcomes.items(), key=lambda x: -x[1]))
    )
    lines.append("")
    return lines


def _quality_section(since: Optional[datetime]) -> list[str]:
    """LintGate quality metrics trend."""
    lg_metrics = sources.read_lintgate_metrics(since=since)
    if not lg_metrics:
        return []

    lint_runs = [m for m in lg_metrics if m.get("event") == "lint_run"]
    cp_runs = [m for m in lg_metrics if m.get("event") == "controlplane_run"]

    lines = ["## Code Quality Trend (LintGate)"]
    lines.append(f"- Lint runs: {len(lint_runs)}")
    lines.append(f"- Controlplane runs: {len(cp_runs)}")

    perf_events = [m for m in lg_metrics if m.get("event") == "performance_analysis"]
    if perf_events:
        ratios = [e["purity_ratio"] for e in perf_events if "purity_ratio" in e]
        if ratios:
            lines.append(f"- Purity ratio: {ratios[0]:.2f} (earliest) -> {ratios[-1]:.2f} (latest)")

    feature_counts: dict[str, int] = defaultdict(int)
    for m in lg_metrics:
        event = m.get("event", "")
        if event:
            feature_counts[event] += 1
    if feature_counts:
        top_features = sorted(feature_counts.items(), key=lambda x: -x[1])[:8]
        lines.append("- Top events: " + ", ".join(f"{k}({v})" for k, v in top_features))
    lines.append("")
    return lines


def _cognitive_section(period: str) -> list[str]:
    """Mneme cognitive state."""
    hours = 720 if period in ("month", "quarter") else 168
    mneme = sources.read_mneme_recent(hours=hours)
    if not mneme or mneme.get("event_count", 0) == 0:
        return []

    lines = ["## Cognitive State (Mneme)"]
    lines.append(f"- Events in window: {mneme['event_count']}")
    if mneme.get("top_anchors"):
        anchor_strs = [f"{a['label']} ({a['freq']})" for a in mneme["top_anchors"][:5]]
        lines.append(f"- Top concepts: {', '.join(anchor_strs)}")

    dims = mneme.get("recent_dimensions", [])
    if dims:
        dim_summary: dict[str, list[str]] = defaultdict(list)
        for d in dims:
            dim_summary[d["dimension"]].append(d.get("path", ""))
        for dim, paths in list(dim_summary.items())[:3]:
            unique = list(set(paths))[:3]
            lines.append(f"- {dim}: {', '.join(unique)}")
    lines.append("")
    return lines


def run(period: str = "month") -> str:
    since = sources.period_to_since(period)
    lines = [f"# Trajectory — {period}", ""]

    stats = sources.read_stats_cache()
    if stats:
        lines.extend(_activity_section(stats, since))

    lines.extend(_decisions_section(since))
    lines.extend(_quality_section(since))
    lines.extend(_cognitive_section(period))

    if len(lines) <= 2:
        lines.append("No data available for this period.")

    return "\n".join(lines)
