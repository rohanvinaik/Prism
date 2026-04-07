"""Tests for prism.economics — prescriptive targets from mutation analysis.

Covers: VALUE, BOUNDARY, SWAP for analyze.
"""

from unittest.mock import patch

from prism.economics import analyze
from prism.sources import SessionData, TokenUsage, ToolCallRecord


def _session(tokens=1000, cache_read=400, output=100, project="proj", **kw) -> SessionData:
    return SessionData(
        session_id="s",
        project=project,
        usage=TokenUsage(input_tokens=tokens, cache_read=cache_read, output_tokens=output),
        tool_calls=[ToolCallRecord(name="Read")],
        prompt_count=2,
        assistant_turns=3,
        **kw,
    )


class TestAnalyze:
    def _run(self, tmp_path, monkeypatch, sessions=None, rtk=None):
        monkeypatch.setattr("prism.engine.PRISM_DIR", tmp_path)
        monkeypatch.setattr("prism.engine.SNAPSHOTS_DIR", tmp_path / "snapshots")
        monkeypatch.setattr("prism.engine.SESSIONS_DIR", tmp_path / "sessions")
        monkeypatch.setattr("prism.engine.DAILY_DIR", tmp_path / "daily")
        monkeypatch.setattr("prism.engine.HEALTH_DIR", tmp_path / "health")

        if sessions is None:
            sessions = [_session()]
        if rtk is None:
            rtk = []

        with (
            patch("prism.economics.sources.iter_sessions", return_value=iter(sessions)),
            patch("prism.economics.sources.period_to_since", return_value=None),
            patch("prism.economics.sources.read_rtk", return_value=rtk),
        ):
            return analyze("week")

    def test_basic_output(self, tmp_path, monkeypatch):
        result = self._run(tmp_path, monkeypatch)
        assert "# Token Economics" in result
        assert "Sessions: 1" in result
        assert "1,500" in result  # total tokens

    def test_cache_hit_rate(self, tmp_path, monkeypatch):
        result = self._run(tmp_path, monkeypatch)
        assert "Cache hit:" in result

    def test_no_sessions(self, tmp_path, monkeypatch):
        result = self._run(tmp_path, monkeypatch, sessions=[])
        assert "Sessions: 0" in result
        assert "API tokens: 0" in result

    def test_rtk_section_present(self, tmp_path, monkeypatch):
        rtk = [{"input_tokens": 1000, "saved_tokens": 600}]
        result = self._run(tmp_path, monkeypatch, rtk=rtk)
        assert "RTK:" in result
        assert "600" in result

    def test_rtk_section_absent_when_empty(self, tmp_path, monkeypatch):
        result = self._run(tmp_path, monkeypatch, rtk=[])
        assert "RTK:" not in result

    def test_subagent_section(self, tmp_path, monkeypatch):
        s = _session(subagent_count=2, subagent_usage=TokenUsage(input_tokens=500))
        result = self._run(tmp_path, monkeypatch, sessions=[s])
        assert "Subagents: 2" in result

    def test_multiple_projects(self, tmp_path, monkeypatch):
        sessions = [_session(project="alpha"), _session(project="beta", tokens=2000)]
        result = self._run(tmp_path, monkeypatch, sessions=sessions)
        assert "Top projects:" in result
        assert "beta" in result

    def test_combined_efficiency_with_rtk(self, tmp_path, monkeypatch):
        """VALUE: combined efficiency line appears when both cache and RTK save."""
        s = _session(tokens=500, cache_read=300, output=100)
        rtk = [{"input_tokens": 1000, "saved_tokens": 400}]
        result = self._run(tmp_path, monkeypatch, sessions=[s], rtk=rtk)
        assert "Combined efficiency:" in result
