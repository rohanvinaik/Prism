"""Prism MCP server — holographic Claude Code usage analytics.

Compact-first pattern: each tool returns a tight summary + snapshot_id.
Use prism_details(id) to drill into full results on demand.
"""

from mcp.server.fastmcp import FastMCP

from . import behavior, economics, engine, forensics, health, snapshot, trajectory

mcp = FastMCP(
    "Prism",
    instructions=(
        "Holographic Claude Code usage analytics. "
        "6 tools: snapshot (quick composite), economics (token costs), "
        "behavior (tool choreography), trajectory (trends), "
        "forensics (session deep-dive), details (drill into any snapshot). "
        "Each tool returns a compact summary + snapshot_id. "
        "Call prism_details(id, section) to drill into full data on demand. "
        "All reads are read-only across RTK, Claude Code sessions, "
        "LintGate, Continuity, and Mneme."
    ),
)


@mcp.tool()
def prism_snapshot(period: str = "today") -> str:
    """Quick multi-lens summary. Returns compact view + snapshot_id for drill-down.

    Args:
        period: today, week, month.
    """
    return snapshot.run(period)


@mcp.tool()
def prism_economics(period: str = "week", project: str = "") -> str:
    """Token economics: API consumption, cache efficiency, RTK savings, subagent costs.

    Args:
        period: today, week, month, quarter, all.
        project: Filter to project name (substring match). Empty for all.
    """
    return economics.run(period, project)


@mcp.tool()
def prism_behavior(period: str = "week", project: str = "") -> str:
    """Tool choreography, call sequences, behavioral signals, workflow mode detection.

    Args:
        period: today, week, month, quarter, all.
        project: Filter to project name (substring match). Empty for all.
    """
    return behavior.run(period, project)


@mcp.tool()
def prism_trajectory(period: str = "month") -> str:
    """Quality, decision, and cognitive trends over time.

    Args:
        period: week, month, quarter.
    """
    return trajectory.run(period)


@mcp.tool()
def prism_forensics(session_id: str = "", project: str = "", last_n: int = 1) -> str:
    """Deep dive into specific session(s) across all data sources.

    Args:
        session_id: Session ID prefix. If empty, uses last_n most recent.
        project: Filter to project (substring match).
        last_n: Number of most recent sessions to analyze (default 1).
    """
    return forensics.run(session_id, project, last_n)


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
def prism_health(project_path: str = "") -> str:
    """Project setup maturity: venv, lockfile, git, CI, secrets, toolchain.

    Scores 0-100 and persists state for LintGate consumption.

    Args:
        project_path: Absolute path to project root. Defaults to cwd.
    """
    import os
    path = project_path or os.getcwd()
    return health.run(path)


def main():
    mcp.run()
