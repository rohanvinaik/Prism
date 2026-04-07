"""Prism MCP server — holographic Claude Code usage analytics.

Compact-first pattern: each tool returns a tight summary + snapshot_id.
Use prism_details(id) to drill into full results on demand.

Project scoping: all analytics tools accept an optional `project` param
(substring match on project name). Pass the current project name to scope
results; leave empty for cross-project aggregate view.
"""

from mcp.server.fastmcp import FastMCP

from . import (
    behavior,
    economics,
    engine,
    fix,
    forensics,
    health,
    pr_ready,
    recommend,
    snapshot,
    trajectory,
    trends,
)

mcp = FastMCP(
    "Prism",
    instructions=(
        "Holographic Claude Code usage analytics. "
        "11 tools: snapshot, economics, behavior, trajectory, forensics, "
        "trends, details (drill-down), health, recommend, fix, pr_ready. "
        "Each tool returns a compact summary + snapshot_id. "
        "Call prism_details(id, section) to drill into full data on demand. "
        "IMPORTANT: Pass the current project name in the `project` param "
        "to scope results to this project. Leave empty for global view."
    ),
)


@mcp.tool()
def prism_snapshot(period: str = "today", project: str = "") -> str:
    """Quick multi-lens summary. Returns compact view + snapshot_id for drill-down.

    Args:
        period: today, week, month.
        project: Filter to project name (substring match). Empty for all projects.
    """
    return snapshot.analyze(period, project)


@mcp.tool()
def prism_economics(period: str = "week", project: str = "") -> str:
    """Token economics: API consumption, cache efficiency, RTK savings, subagent costs.

    Args:
        period: today, week, month, quarter, all.
        project: Filter to project name (substring match). Empty for all.
    """
    return economics.analyze(period, project)


@mcp.tool()
def prism_behavior(period: str = "week", project: str = "") -> str:
    """Tool choreography, call sequences, behavioral signals, workflow mode detection.

    Args:
        period: today, week, month, quarter, all.
        project: Filter to project name (substring match). Empty for all.
    """
    return behavior.analyze(period, project)


@mcp.tool()
def prism_trajectory(period: str = "month", project: str = "") -> str:
    """Quality, decision, and cognitive trends over time.

    Args:
        period: week, month, quarter.
        project: Filter to project name (substring match). Empty for all.
    """
    return trajectory.analyze(period, project)


@mcp.tool()
def prism_forensics(session_id: str = "", project: str = "", last_n: int = 1) -> str:
    """Deep dive into specific session(s) across all data sources.

    Args:
        session_id: Session ID prefix. If empty, uses last_n most recent.
        project: Filter to project (substring match).
        last_n: Number of most recent sessions to analyze (default 1).
    """
    return forensics.analyze(session_id, project, last_n)


@mcp.tool()
def prism_details(snapshot_id: str, section: str = "", path: str = "", max_items: int = 20) -> str:
    """Drill into a Prism snapshot saved by any other tool.

    Every Prism tool returns a snapshot_id at the bottom of its output.
    Use this tool to navigate into the full data without re-running analysis.

    Args:
        snapshot_id: The ID from a previous Prism tool response.
        section: Top-level section to focus on (e.g. "by_project", "tool_distribution").
        path: Dot-separated path for deeper navigation (e.g. "weekly.2026-W14").
        max_items: Max list items to return (default 20).
    """
    return engine.query_snapshot(snapshot_id, section, path, max_items)


@mcp.tool()
def prism_trends(days: int = 7, project: str = "") -> str:
    """Cross-session trends from hook daily summaries.

    Detects efficiency drift, error rate changes, tool distribution shifts.
    Reads from pre-aggregated hook data — no JSONL scanning, near-instant.

    Args:
        days: Number of days to analyze (default 7).
        project: Filter to project name (substring match). Empty for all.
    """
    return trends.analyze(days, project)


@mcp.tool()
def prism_health(project_path: str = "") -> str:
    """Project setup maturity: venv, lockfile, git, CI, secrets, toolchain.

    Scores 0-100 and persists state for LintGate consumption.

    Args:
        project_path: Absolute path to project root.
    """
    if not project_path:
        return "Error: project_path is required (MCP server cwd is not project-specific)."
    return health.check(project_path)


@mcp.tool()
def prism_recommend(project_path: str = "", period: str = "week", min_confidence: int = 0) -> str:
    """Automation recommendations from Prism signals. No LLM inference.

    Each recommendation has a confidence score (0-100) based on signal strength.

    Args:
        project_path: Absolute path to project root (for setup recommendations).
        period: Time window for behavioral analysis — today, week, month.
        min_confidence: Only show recommendations above this threshold (0-100).
    """
    return recommend.analyze(project_path, period, min_confidence)


@mcp.tool()
def prism_fix(project_path: str, dry_run: bool = True) -> str:
    """Auto-fix issues from prism_recommend. Deterministic, no LLM inference.

    Supports: venv creation, lockfile sync, .gitignore generation,
    git init, secrets cleanup, linter config, hook patching.
    Dry-run by default — set dry_run=False to apply.

    Args:
        project_path: Absolute path to project root.
        dry_run: Preview fixes without applying (default True).
    """
    return fix.apply_fixes(project_path, dry_run)


@mcp.tool()
def prism_pr_ready(project_path: str) -> str:
    """PR readiness gate — composite go/no-go from on-disk state.

    Checks: git clean, LintGate blockers, health score, lockfile freshness,
    secrets safety, session error rate. Zero agent spawns, zero inference.

    Args:
        project_path: Absolute path to project root.
    """
    return pr_ready.assess(project_path)


def main():
    mcp.run()
