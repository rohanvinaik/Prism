"""Tests for prism.forensics — prescriptive targets from mutation analysis.

Covers: VALUE for _session_duration_str, _session_error_str, _session_compact,
_enrich_hook_data, _enrich_bridge_data, _session_full, analyze.
"""

from unittest.mock import patch

from prism.forensics import (
    _enrich_bridge_data,
    _enrich_hook_data,
    _session_compact,
    _session_duration_str,
    _session_error_str,
    _session_full,
    analyze,
)
from prism.sources import SessionData, TokenUsage, ToolCallRecord


def _session(**kw) -> SessionData:
    defaults = {
        "session_id": "sess1",
        "project": "test-proj",
        "timestamp_start": "2026-04-07T12:00:00+00:00",
        "timestamp_end": "2026-04-07T12:30:00+00:00",
        "usage": TokenUsage(input_tokens=500, cache_read=200, output_tokens=100),
        "tool_calls": [ToolCallRecord(name="Read"), ToolCallRecord(name="Edit")],
        "prompt_count": 3,
        "assistant_turns": 5,
    }
    defaults.update(kw)
    return SessionData(**defaults)


# =====================================================================
# _session_duration_str — VALUE
# =====================================================================


class TestSessionDurationStr:
    def test_with_valid_timestamps(self):
        s = _session()
        assert _session_duration_str(s) == ", 30min"

    def test_missing_end(self):
        s = _session(timestamp_end=None)
        assert _session_duration_str(s) == ""

    def test_missing_start(self):
        s = _session(timestamp_start=None)
        assert _session_duration_str(s) == ""

    def test_same_time(self):
        s = _session(
            timestamp_start="2026-04-07T12:00:00+00:00",
            timestamp_end="2026-04-07T12:00:00+00:00",
        )
        assert _session_duration_str(s) == ", 0min"


# =====================================================================
# _session_error_str — VALUE
# =====================================================================


class TestSessionErrorStr:
    def test_no_events(self):
        with patch("prism.forensics.engine.read_events", return_value=[]):
            assert _session_error_str(_session()) == ""

    def test_no_errors(self):
        events = [
            {"event": "tool_use", "error": False},
            {"event": "tool_use", "error": False},
        ]
        with patch("prism.forensics.engine.read_events", return_value=events):
            assert _session_error_str(_session()) == ""

    def test_with_errors(self):
        events = [
            {"event": "tool_use", "error": True},
            {"event": "tool_use", "error": False},
        ]
        with patch("prism.forensics.engine.read_events", return_value=events):
            result = _session_error_str(_session())
            assert "50%" in result
            assert "errors" in result

    def test_all_errors(self):
        events = [{"event": "tool_use", "error": True}]
        with patch("prism.forensics.engine.read_events", return_value=events):
            result = _session_error_str(_session())
            assert "100%" in result


# =====================================================================
# _session_compact — VALUE
# =====================================================================


class TestSessionCompact:
    def test_basic_output(self):
        with patch("prism.forensics.engine.read_events", return_value=[]):
            result = _session_compact(_session())
        assert "test-proj" in result
        assert "800" in result  # total tokens
        assert "3 prompts" in result
        assert "2 calls" in result
        assert "30min" in result

    def test_missing_timestamp(self):
        s = _session(timestamp_start=None)
        with patch("prism.forensics.engine.read_events", return_value=[]):
            result = _session_compact(s)
        assert "[?]" in result


# =====================================================================
# _enrich_hook_data — VALUE
# =====================================================================


class TestEnrichHookData:
    def test_adds_realtime_data(self):
        data: dict = {}
        events = [
            {"event": "tool_use", "error": False, "output_bytes": 100},
            {"event": "tool_use", "error": True, "output_bytes": 50},
            {"event": "pre_compact"},
        ]
        with patch("prism.forensics.read_events", return_value=events):
            _enrich_hook_data(data, _session())
        assert data["realtime"]["hook_events"] == 3
        assert data["realtime"]["tool_calls_observed"] == 2
        assert data["realtime"]["errors_detected"] == 1
        assert data["realtime"]["total_output_bytes"] == 150
        assert data["realtime"]["compactions"] == 1

    def test_no_events_does_nothing(self):
        data: dict = {}
        with patch("prism.forensics.read_events", return_value=[]):
            _enrich_hook_data(data, _session())
        assert "realtime" not in data


# =====================================================================
# _enrich_bridge_data — VALUE
# =====================================================================


class TestEnrichBridgeData:
    def test_matching_session(self):
        data: dict = {}
        bridge = {"session_id": "sess1", "efficiency_score": 85}
        with patch("prism.forensics.read_bridge", return_value=bridge):
            _enrich_bridge_data(data, _session())
        assert data["efficiency_score"] == 85

    def test_different_session(self):
        data: dict = {}
        bridge = {"session_id": "other", "efficiency_score": 85}
        with patch("prism.forensics.read_bridge", return_value=bridge):
            _enrich_bridge_data(data, _session())
        assert "efficiency_score" not in data

    def test_no_bridge(self):
        data: dict = {}
        with patch("prism.forensics.read_bridge", return_value=None):
            _enrich_bridge_data(data, _session())
        assert "efficiency_score" not in data


# =====================================================================
# _session_full — VALUE
# =====================================================================


class TestSessionFull:
    def test_basic_fields(self):
        with (
            patch("prism.forensics.read_events", return_value=[]),
            patch("prism.forensics.read_bridge", return_value=None),
            patch("prism.forensics.sources.read_rtk", return_value=[]),
        ):
            result = _session_full(_session())
        assert result["session_id"] == "sess1"
        assert result["project"] == "test-proj"
        assert result["duration_min"] == 30
        assert result["tokens"]["total"] == 800
        assert result["interaction"]["prompts"] == 3
        assert result["signals"]["reads"] == 1
        assert result["signals"]["edits"] == 1

    def test_includes_subagent_data(self):
        s = _session(subagent_count=2, subagent_usage=TokenUsage(input_tokens=300))
        with (
            patch("prism.forensics.read_events", return_value=[]),
            patch("prism.forensics.read_bridge", return_value=None),
            patch("prism.forensics.sources.read_rtk", return_value=[]),
        ):
            result = _session_full(s)
        assert result["subagents"]["count"] == 2
        assert result["subagents"]["tokens"] == 300


# =====================================================================
# analyze — VALUE (integration)
# =====================================================================


class TestAnalyze:
    def test_returns_forensics_header(self, tmp_path, monkeypatch):
        monkeypatch.setattr("prism.engine.PRISM_DIR", tmp_path)
        monkeypatch.setattr("prism.engine.SNAPSHOTS_DIR", tmp_path / "snapshots")
        monkeypatch.setattr("prism.engine.SESSIONS_DIR", tmp_path / "sessions")
        monkeypatch.setattr("prism.engine.DAILY_DIR", tmp_path / "daily")
        monkeypatch.setattr("prism.engine.HEALTH_DIR", tmp_path / "health")

        with (
            patch("prism.forensics.sources.iter_sessions", return_value=iter([_session()])),
            patch("prism.forensics.engine.read_events", return_value=[]),
            patch("prism.forensics.read_events", return_value=[]),
            patch("prism.forensics.read_bridge", return_value=None),
            patch("prism.forensics.sources.read_rtk", return_value=[]),
        ):
            result = analyze(project="test")
        assert "# Session Forensics" in result
        assert "1 session" in result

    def test_no_sessions(self, tmp_path, monkeypatch):
        monkeypatch.setattr("prism.engine.PRISM_DIR", tmp_path)
        monkeypatch.setattr("prism.engine.SNAPSHOTS_DIR", tmp_path / "snapshots")
        monkeypatch.setattr("prism.engine.SESSIONS_DIR", tmp_path / "sessions")
        monkeypatch.setattr("prism.engine.DAILY_DIR", tmp_path / "daily")
        monkeypatch.setattr("prism.engine.HEALTH_DIR", tmp_path / "health")

        with patch("prism.forensics.sources.iter_sessions", return_value=iter([])):
            result = analyze(project="nonexistent")
        assert "No sessions found" in result

    def test_session_id_filter(self, tmp_path, monkeypatch):
        monkeypatch.setattr("prism.engine.PRISM_DIR", tmp_path)
        monkeypatch.setattr("prism.engine.SNAPSHOTS_DIR", tmp_path / "snapshots")
        monkeypatch.setattr("prism.engine.SESSIONS_DIR", tmp_path / "sessions")
        monkeypatch.setattr("prism.engine.DAILY_DIR", tmp_path / "daily")
        monkeypatch.setattr("prism.engine.HEALTH_DIR", tmp_path / "health")

        s1 = _session(session_id="abc123")
        s2 = _session(session_id="xyz999")
        with (
            patch("prism.forensics.sources.iter_sessions", return_value=iter([s1, s2])),
            patch("prism.forensics.engine.read_events", return_value=[]),
            patch("prism.forensics.read_events", return_value=[]),
            patch("prism.forensics.read_bridge", return_value=None),
            patch("prism.forensics.sources.read_rtk", return_value=[]),
        ):
            result = analyze(session_id="abc")
        assert "1 session" in result

    def test_no_match_for_session_id(self):
        with patch("prism.forensics.sources.iter_sessions", return_value=iter([])):
            result = analyze(session_id="nonexistent")
        assert "No session found" in result
