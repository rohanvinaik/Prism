"""Tests for prism.behavior — prescriptive targets from mutation analysis.

Covers: VALUE, BOUNDARY, SWAP for _count_tools, _count_transitions,
_infer_workflow_mode, _skew_warnings, and analyze.
"""

from collections import Counter
from unittest.mock import patch

from prism.behavior import (
    _count_tools,
    _count_transitions,
    _infer_workflow_mode,
    _skew_warnings,
    analyze,
)
from prism.sources import SessionData, ToolCallRecord


def _session(tools: list[str], **kw) -> SessionData:
    return SessionData(
        session_id="s",
        project="p",
        tool_calls=[ToolCallRecord(name=t) for t in tools],
        **kw,
    )


# =====================================================================
# _count_tools — VALUE
# =====================================================================


class TestCountTools:
    def test_counts_tool_names(self):
        sessions = [_session(["Read", "Edit", "Read"])]
        result = _count_tools(sessions)
        assert result["Read"] == 2
        assert result["Edit"] == 1

    def test_multiple_sessions(self):
        sessions = [_session(["Read"]), _session(["Read", "Bash"])]
        result = _count_tools(sessions)
        assert result["Read"] == 2
        assert result["Bash"] == 1

    def test_empty_sessions(self):
        assert _count_tools([]) == Counter()

    def test_session_with_no_tools(self):
        assert _count_tools([_session([])]) == Counter()


# =====================================================================
# _count_transitions — VALUE
# =====================================================================


class TestCountTransitions:
    def test_counts_pairs(self):
        sessions = [_session(["Read", "Edit", "Read"])]
        result = _count_transitions(sessions)
        assert result[("Read", "Edit")] == 1
        assert result[("Edit", "Read")] == 1

    def test_single_tool_no_transitions(self):
        assert _count_transitions([_session(["Read"])]) == Counter()

    def test_empty(self):
        assert _count_transitions([]) == Counter()

    def test_repeated_pair(self):
        sessions = [_session(["Read", "Edit", "Read", "Edit"])]
        result = _count_transitions(sessions)
        assert result[("Read", "Edit")] == 2
        assert result[("Edit", "Read")] == 1


# =====================================================================
# _infer_workflow_mode — VALUE, BOUNDARY, SWAP
# =====================================================================


class TestInferWorkflowMode:
    def test_idle_on_zero_calls(self):
        assert _infer_workflow_mode(Counter(), 0) == "Idle"

    def test_explore_mode(self):
        counts = Counter({"Read": 60, "Grep": 10, "Edit": 5})
        assert _infer_workflow_mode(counts, 75) == "Explore"

    def test_surgical_mode(self):
        counts = Counter({"Edit": 40, "Write": 10, "Read": 10})
        assert _infer_workflow_mode(counts, 60) == "Surgical"

    def test_shell_heavy_mode(self):
        counts = Counter({"Bash": 40, "Read": 10})
        assert _infer_workflow_mode(counts, 50) == "Shell-heavy"

    def test_delegating_mode(self):
        counts = Counter({"Agent": 15, "Read": 30, "Edit": 30})
        assert _infer_workflow_mode(counts, 75) == "Delegating"

    def test_balanced_mode(self):
        counts = Counter({"Read": 25, "Edit": 25, "Bash": 10, "Grep": 10})
        assert _infer_workflow_mode(counts, 70) == "Balanced"

    def test_boundary_explore_threshold(self):
        """BOUNDARY: read_pct exactly 0.5 should NOT be Explore (> not >=)."""
        counts = Counter({"Read": 50, "Edit": 5, "Bash": 45})
        assert _infer_workflow_mode(counts, 100) != "Explore"

    def test_boundary_surgical_threshold(self):
        """BOUNDARY: edit_pct exactly 0.3 should NOT be Surgical (> not >=)."""
        counts = Counter({"Edit": 30, "Read": 10, "Bash": 60})
        assert _infer_workflow_mode(counts, 100) != "Surgical"


# =====================================================================
# _skew_warnings — VALUE, BOUNDARY
# =====================================================================


class TestSkewWarnings:
    def test_no_warnings_below_50_calls(self):
        lines = []
        result = _skew_warnings(10, lines, 20, Counter({"Bash": 30}), 49)
        assert result == []

    def test_bash_heavy_warning(self):
        lines = []
        counts = Counter({"Bash": 30, "Read": 5})
        result = _skew_warnings(5, lines, 5, counts, 60)
        assert any("Bash" in line for line in result)

    def test_low_read_edit_warning(self):
        lines = []
        counts = Counter({"Read": 10, "Edit": 10, "Bash": 5})
        result = _skew_warnings(10, lines, 10, counts, 60)
        assert any("Read/Edit" in line for line in result)

    def test_no_bash_warning_when_ratio_ok(self):
        lines = []
        counts = Counter({"Bash": 10, "Read": 30, "Edit": 20})
        result = _skew_warnings(20, lines, 30, counts, 60)
        assert not any("Bash" in line for line in result)


# =====================================================================
# analyze — VALUE (integration)
# =====================================================================


class TestAnalyze:
    def test_returns_markdown_with_header(self, tmp_path, monkeypatch):
        monkeypatch.setattr("prism.engine.PRISM_DIR", tmp_path)
        monkeypatch.setattr("prism.engine.SNAPSHOTS_DIR", tmp_path / "snapshots")
        monkeypatch.setattr("prism.engine.SESSIONS_DIR", tmp_path / "sessions")
        monkeypatch.setattr("prism.engine.DAILY_DIR", tmp_path / "daily")
        monkeypatch.setattr("prism.engine.HEALTH_DIR", tmp_path / "health")

        session = _session(
            ["Read", "Edit", "Read", "Bash"],
            prompt_count=3,
            assistant_turns=5,
        )
        with (
            patch("prism.behavior.sources.iter_sessions", return_value=iter([session])),
            patch("prism.behavior.sources.period_to_since", return_value=None),
            patch("prism.behavior.sources.read_lintgate_sessions", return_value={}),
        ):
            result = analyze("week")

        assert "# Behavioral Profile" in result
        assert "Sessions: 1" in result
        assert "Prompts: 3" in result
        assert "Tool calls: 4" in result
        assert "Mode: **Balanced**" in result

    def test_no_sessions(self, tmp_path, monkeypatch):
        monkeypatch.setattr("prism.engine.PRISM_DIR", tmp_path)
        monkeypatch.setattr("prism.engine.SNAPSHOTS_DIR", tmp_path / "snapshots")
        monkeypatch.setattr("prism.engine.SESSIONS_DIR", tmp_path / "sessions")
        monkeypatch.setattr("prism.engine.DAILY_DIR", tmp_path / "daily")
        monkeypatch.setattr("prism.engine.HEALTH_DIR", tmp_path / "health")

        with (
            patch("prism.behavior.sources.iter_sessions", return_value=iter([])),
            patch("prism.behavior.sources.period_to_since", return_value=None),
            patch("prism.behavior.sources.read_lintgate_sessions", return_value={}),
        ):
            result = analyze("today")

        assert "Sessions: 0" in result
        assert "Mode: **Idle**" in result
