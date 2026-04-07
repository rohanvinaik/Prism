"""Tests for prism.server — thin MCP wrappers dispatch to module functions."""

from prism.server import mcp


class TestServerTools:
    def test_all_tools_registered(self):
        """VALUE: all 11 MCP tools are registered."""
        tool_names = {t.name for t in mcp._tool_manager.list_tools()}
        expected = {
            "prism_snapshot",
            "prism_economics",
            "prism_behavior",
            "prism_trajectory",
            "prism_forensics",
            "prism_details",
            "prism_trends",
            "prism_health",
            "prism_recommend",
            "prism_fix",
            "prism_pr_ready",
        }
        assert expected <= tool_names
