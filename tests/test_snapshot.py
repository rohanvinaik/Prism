"""Tests for prism.snapshot — prescriptive targets from mutation analysis.

Covers: VALUE, BOUNDARY, SWAP for analyze.
"""

from unittest.mock import patch

from prism.snapshot import analyze
from prism.sources import SessionData, TokenUsage, ToolCallRecord


def _session(tools=None, project="proj", **kw) -> SessionData:
    if tools is None:
        tools = ["Read", "Edit", "Read", "Bash"]
    return SessionData(
        session_id="s",
        project=project,
        usage=TokenUsage(input_tokens=500, cache_read=200, output_tokens=100),
        tool_calls=[ToolCallRecord(name=t) for t in tools],
        prompt_count=3,
        assistant_turns=5,
        **kw,
    )


class TestAnalyze:
    def _run(self, tmp_path, monkeypatch, sessions=None, rtk=None):
        monkeypatch.setattr("prism.engine.PRISM_DIR", tmp_path)
        monkeypatch.setattr("prism.engine.SNAPSHOTS_DIR", tmp_path / "snapshots")
        monkeypatch.setattr("prism.engine.SESSIONS_DIR", tmp_path / "sessions")
        monkeypatch.setattr("prism.engine.DAILY_DIR", tmp_path / "daily")
        monkeypatch.setattr("prism.engine.HEALTH_DIR", tmp_path / "health")
        monkeypatch.setattr("prism.engine.BRIDGE_FILE", tmp_path / "bridge.json")

        if sessions is None:
            sessions = [_session()]
        if rtk is None:
            rtk = []

        with (
            patch("prism.snapshot.sources.iter_sessions", return_value=iter(sessions)),
            patch("prism.snapshot.sources.period_to_since", return_value=None),
            patch("prism.snapshot.sources.read_rtk", return_value=rtk),
            patch("prism.snapshot.sources.read_mneme_recent", return_value={}),
            patch("prism.snapshot.sources.read_lintgate_sessions", return_value={}),
            patch("prism.snapshot.engine.read_bridge", return_value=None),
        ):
            return analyze("today")

    def test_header(self, tmp_path, monkeypatch):
        result = self._run(tmp_path, monkeypatch)
        assert "# Prism Snapshot — Today" in result

    def test_session_count(self, tmp_path, monkeypatch):
        result = self._run(tmp_path, monkeypatch)
        assert "Sessions: 1" in result

    def test_project_count(self, tmp_path, monkeypatch):
        result = self._run(tmp_path, monkeypatch)
        assert "Projects: 1" in result

    def test_token_total(self, tmp_path, monkeypatch):
        result = self._run(tmp_path, monkeypatch)
        assert "800" in result  # total tokens

    def test_tool_distribution(self, tmp_path, monkeypatch):
        result = self._run(tmp_path, monkeypatch)
        assert "Top tools:" in result
        assert "Read(2)" in result

    def test_read_edit_ratio(self, tmp_path, monkeypatch):
        result = self._run(tmp_path, monkeypatch)
        assert "Read/Edit:" in result

    def test_no_read_edit_when_no_edits(self, tmp_path, monkeypatch):
        sessions = [_session(tools=["Read", "Grep", "Bash"])]
        result = self._run(tmp_path, monkeypatch, sessions=sessions)
        assert "Read/Edit:" not in result

    def test_rtk_present(self, tmp_path, monkeypatch):
        rtk = [{"saved_tokens": 500}]
        result = self._run(tmp_path, monkeypatch, rtk=rtk)
        assert "RTK saved:" in result

    def test_rtk_absent(self, tmp_path, monkeypatch):
        result = self._run(tmp_path, monkeypatch, rtk=[])
        assert "RTK saved:" not in result

    def test_no_sessions(self, tmp_path, monkeypatch):
        result = self._run(tmp_path, monkeypatch, sessions=[])
        assert "Sessions: 0" in result

    def test_multiple_projects(self, tmp_path, monkeypatch):
        sessions = [_session(project="alpha"), _session(project="beta")]
        result = self._run(tmp_path, monkeypatch, sessions=sessions)
        assert "Projects: 2" in result

    def test_bridge_efficiency(self, tmp_path, monkeypatch):
        monkeypatch.setattr("prism.engine.PRISM_DIR", tmp_path)
        monkeypatch.setattr("prism.engine.SNAPSHOTS_DIR", tmp_path / "snapshots")
        monkeypatch.setattr("prism.engine.SESSIONS_DIR", tmp_path / "sessions")
        monkeypatch.setattr("prism.engine.DAILY_DIR", tmp_path / "daily")
        monkeypatch.setattr("prism.engine.HEALTH_DIR", tmp_path / "health")

        bridge = {"efficiency_score": 85, "error_rate": 0.05}
        with (
            patch("prism.snapshot.sources.iter_sessions", return_value=iter([_session()])),
            patch("prism.snapshot.sources.period_to_since", return_value=None),
            patch("prism.snapshot.sources.read_rtk", return_value=[]),
            patch("prism.snapshot.sources.read_mneme_recent", return_value={}),
            patch("prism.snapshot.sources.read_lintgate_sessions", return_value={}),
            patch("prism.snapshot.engine.read_bridge", return_value=bridge),
        ):
            result = analyze("today")
        assert "efficiency: 85/100" in result
