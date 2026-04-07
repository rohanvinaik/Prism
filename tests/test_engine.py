"""Tests for prism.engine — prescriptive targets from mutation analysis.

Covers: VALUE, STATE, SWAP categories across persistence layer.
"""

import json
from pathlib import Path

from prism import engine

# =====================================================================
# analysis_id — STATE, VALUE
# =====================================================================


class TestAnalysisId:
    def test_returns_12_hex_chars(self):
        aid = engine.analysis_id()
        assert len(aid) == 12
        assert all(c in "0123456789abcdef" for c in aid)

    def test_unique_on_successive_calls(self):
        """STATE: counter increments, so successive calls differ."""
        ids = {engine.analysis_id() for _ in range(100)}
        assert len(ids) == 100


# =====================================================================
# save_snapshot / load_snapshot — VALUE
# =====================================================================


class TestSnapshots:
    def test_roundtrip(self, tmp_path, monkeypatch):
        monkeypatch.setattr("prism.engine.PRISM_DIR", tmp_path)
        monkeypatch.setattr("prism.engine.SNAPSHOTS_DIR", tmp_path / "snapshots")
        monkeypatch.setattr("prism.engine.SESSIONS_DIR", tmp_path / "sessions")
        monkeypatch.setattr("prism.engine.DAILY_DIR", tmp_path / "daily")
        monkeypatch.setattr("prism.engine.HEALTH_DIR", tmp_path / "health")

        aid = engine.save_snapshot("test_tool", "summary text", {"key": "value"})
        loaded = engine.load_snapshot(aid)
        assert loaded is not None
        assert loaded["summary"] == "summary text"
        assert loaded["key"] == "value"
        assert loaded["_meta"]["tool"] == "test_tool"
        assert loaded["_meta"]["analysis_id"] == aid

    def test_load_missing_returns_none(self):
        assert engine.load_snapshot("nonexistent000") is None

    def test_list_snapshots(self, tmp_path, monkeypatch):
        monkeypatch.setattr("prism.engine.PRISM_DIR", tmp_path)
        monkeypatch.setattr("prism.engine.SNAPSHOTS_DIR", tmp_path / "snapshots")
        monkeypatch.setattr("prism.engine.SESSIONS_DIR", tmp_path / "sessions")
        monkeypatch.setattr("prism.engine.DAILY_DIR", tmp_path / "daily")
        monkeypatch.setattr("prism.engine.HEALTH_DIR", tmp_path / "health")

        engine.save_snapshot("a", "s1", {})
        engine.save_snapshot("b", "s2", {})
        result = engine.list_snapshots(limit=10)
        assert len(result) == 2
        tools = {m["tool"] for m in result}
        assert tools == {"a", "b"}


# =====================================================================
# query_snapshot — VALUE (drill-down navigation)
# =====================================================================


class TestQuerySnapshot:
    def test_section_navigation(self, tmp_path, monkeypatch):
        monkeypatch.setattr("prism.engine.PRISM_DIR", tmp_path)
        monkeypatch.setattr("prism.engine.SNAPSHOTS_DIR", tmp_path / "snapshots")
        monkeypatch.setattr("prism.engine.SESSIONS_DIR", tmp_path / "sessions")
        monkeypatch.setattr("prism.engine.DAILY_DIR", tmp_path / "daily")
        monkeypatch.setattr("prism.engine.HEALTH_DIR", tmp_path / "health")

        aid = engine.save_snapshot("t", "s", {"data": {"nested": 42}})
        result = engine.query_snapshot(aid, section="data")
        parsed = json.loads(result)
        assert parsed["nested"] == 42

    def test_dot_path_navigation(self, tmp_path, monkeypatch):
        monkeypatch.setattr("prism.engine.PRISM_DIR", tmp_path)
        monkeypatch.setattr("prism.engine.SNAPSHOTS_DIR", tmp_path / "snapshots")
        monkeypatch.setattr("prism.engine.SESSIONS_DIR", tmp_path / "sessions")
        monkeypatch.setattr("prism.engine.DAILY_DIR", tmp_path / "daily")
        monkeypatch.setattr("prism.engine.HEALTH_DIR", tmp_path / "health")

        aid = engine.save_snapshot("t", "s", {"data": {"deep": {"val": 99}}})
        result = engine.query_snapshot(aid, section="data", path="deep.val")
        assert "99" in result

    def test_missing_section_lists_available(self, tmp_path, monkeypatch):
        monkeypatch.setattr("prism.engine.PRISM_DIR", tmp_path)
        monkeypatch.setattr("prism.engine.SNAPSHOTS_DIR", tmp_path / "snapshots")
        monkeypatch.setattr("prism.engine.SESSIONS_DIR", tmp_path / "sessions")
        monkeypatch.setattr("prism.engine.DAILY_DIR", tmp_path / "daily")
        monkeypatch.setattr("prism.engine.HEALTH_DIR", tmp_path / "health")

        aid = engine.save_snapshot("t", "s", {"real_section": {}})
        result = engine.query_snapshot(aid, section="bogus")
        assert "not found" in result.lower()
        assert "real_section" in result

    def test_missing_snapshot(self):
        result = engine.query_snapshot("doesnotexist")
        assert "not found" in result.lower()


# =====================================================================
# append_event / read_events — SWAP, VALUE
# =====================================================================


class TestEvents:
    def test_roundtrip(self, tmp_path, monkeypatch):
        monkeypatch.setattr("prism.engine.PRISM_DIR", tmp_path)
        monkeypatch.setattr("prism.engine.SNAPSHOTS_DIR", tmp_path / "snapshots")
        monkeypatch.setattr("prism.engine.SESSIONS_DIR", tmp_path / "sessions")
        monkeypatch.setattr("prism.engine.DAILY_DIR", tmp_path / "daily")
        monkeypatch.setattr("prism.engine.HEALTH_DIR", tmp_path / "health")

        engine.append_event("sess1", {"event": "tool_use", "tool": "Read"})
        engine.append_event("sess1", {"event": "tool_use", "tool": "Edit"})
        events = engine.read_events("sess1")
        assert len(events) == 2
        assert events[0]["tool"] == "Read"
        assert events[1]["tool"] == "Edit"
        # SWAP: session_id routes to correct file
        assert engine.read_events("other_session") == []

    def test_event_gets_timestamp(self, tmp_path, monkeypatch):
        monkeypatch.setattr("prism.engine.PRISM_DIR", tmp_path)
        monkeypatch.setattr("prism.engine.SNAPSHOTS_DIR", tmp_path / "snapshots")
        monkeypatch.setattr("prism.engine.SESSIONS_DIR", tmp_path / "sessions")
        monkeypatch.setattr("prism.engine.DAILY_DIR", tmp_path / "daily")
        monkeypatch.setattr("prism.engine.HEALTH_DIR", tmp_path / "health")

        engine.append_event("s", {"event": "test"})
        events = engine.read_events("s")
        assert "ts" in events[0]

    def test_read_missing_session(self):
        assert engine.read_events("nonexistent_session_id") == []


# =====================================================================
# daily summaries — VALUE
# =====================================================================


class TestDailySummaries:
    def test_roundtrip(self, tmp_path, monkeypatch):
        monkeypatch.setattr("prism.engine.PRISM_DIR", tmp_path)
        monkeypatch.setattr("prism.engine.SNAPSHOTS_DIR", tmp_path / "snapshots")
        monkeypatch.setattr("prism.engine.SESSIONS_DIR", tmp_path / "sessions")
        monkeypatch.setattr("prism.engine.DAILY_DIR", tmp_path / "daily")
        monkeypatch.setattr("prism.engine.HEALTH_DIR", tmp_path / "health")

        engine.append_daily_summary({"session_id": "s1", "tool_calls": 10})
        engine.append_daily_summary({"session_id": "s2", "tool_calls": 20})
        result = engine.read_daily_summaries(days=1)
        assert len(result) == 2
        assert result[0]["session_id"] == "s1"

    def test_read_empty(self, tmp_path, monkeypatch):
        monkeypatch.setattr("prism.engine.DAILY_DIR", tmp_path / "daily")
        assert engine.read_daily_summaries() == []


# =====================================================================
# bridge — VALUE
# =====================================================================


class TestBridge:
    def test_roundtrip(self, tmp_path, monkeypatch):
        monkeypatch.setattr("prism.engine.PRISM_DIR", tmp_path)
        monkeypatch.setattr("prism.engine.SNAPSHOTS_DIR", tmp_path / "snapshots")
        monkeypatch.setattr("prism.engine.SESSIONS_DIR", tmp_path / "sessions")
        monkeypatch.setattr("prism.engine.DAILY_DIR", tmp_path / "daily")
        monkeypatch.setattr("prism.engine.HEALTH_DIR", tmp_path / "health")
        monkeypatch.setattr("prism.engine.BRIDGE_FILE", tmp_path / "bridge.json")

        engine.write_bridge({"efficiency_score": 85, "error_rate": 0.05})
        result = engine.read_bridge()
        assert result is not None
        assert result["efficiency_score"] == 85
        assert "_meta" in result

    def test_read_missing(self, monkeypatch):
        monkeypatch.setattr("prism.engine.BRIDGE_FILE", Path("/nonexistent/bridge.json"))
        assert engine.read_bridge() is None


# =====================================================================
# health state — VALUE
# =====================================================================


class TestHealthState:
    def test_roundtrip(self, tmp_path, monkeypatch):
        monkeypatch.setattr("prism.engine.PRISM_DIR", tmp_path)
        monkeypatch.setattr("prism.engine.SNAPSHOTS_DIR", tmp_path / "snapshots")
        monkeypatch.setattr("prism.engine.SESSIONS_DIR", tmp_path / "sessions")
        monkeypatch.setattr("prism.engine.DAILY_DIR", tmp_path / "daily")
        monkeypatch.setattr("prism.engine.HEALTH_DIR", tmp_path / "health")

        engine.write_health("abc123", {"score": 75, "checks": {}})
        result = engine.read_health("abc123")
        assert result is not None
        assert result["score"] == 75
        assert "_meta" in result

    def test_read_missing(self):
        assert engine.read_health("nonexistent_hash") is None
