"""Prism MCP server — holographic Claude Code usage analytics."""

from mcp.server.fastmcp import FastMCP

from . import behavior, economics, forensics, snapshot, trajectory

mcp = FastMCP(
    "Prism",
    instructions=(
        "Holographic Claude Code usage analytics. "
        "5 lenses: snapshot (quick composite), economics (token costs), "
        "behavior (tool choreography), trajectory (trends over time), "
        "forensics (session deep-dive). All reads are read-only across "
        "RTK, Claude Code sessions, LintGate, Continuity, and Mneme."
    ),
)


@mcp.tool()
def prism_snapshot(period: str = "today") -> str:
    """Quick multi-lens summary across all data sources.

    Args:
        period: Time window — today, week, month.
    """
    return snapshot.run(period)


@mcp.tool()
def prism_economics(period: str = "week", project: str = "") -> str:
    """Token economics: API consumption, cache efficiency, RTK savings, subagent costs.

    Args:
        period: Time window — today, week, month, quarter, all.
        project: Filter to project name (substring match). Empty for all.
    """
    return economics.run(period, project)


@mcp.tool()
def prism_behavior(period: str = "week", project: str = "") -> str:
    """Tool choreography, call sequences, behavioral signals, workflow mode detection.

    Args:
        period: Time window — today, week, month, quarter, all.
        project: Filter to project name (substring match). Empty for all.
    """
    return behavior.run(period, project)


@mcp.tool()
def prism_trajectory(period: str = "month") -> str:
    """Quality, decision, and cognitive trends over time.

    Combines stats-cache daily rollups, Continuity decisions,
    LintGate quality metrics, and Mneme cognitive state.

    Args:
        period: Time window — week, month, quarter.
    """
    return trajectory.run(period)


@mcp.tool()
def prism_forensics(session_id: str = "", project: str = "", last_n: int = 1) -> str:
    """Deep dive into specific session(s) across all data sources.

    Args:
        session_id: Session ID prefix to look up. If empty, uses last_n.
        project: Filter to project (substring match).
        last_n: Number of most recent sessions to analyze (default 1).
    """
    return forensics.run(session_id, project, last_n)


def main():
    mcp.run()
