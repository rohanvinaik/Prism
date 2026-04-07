"""Tests for prism.trends — prescriptive targets from mutation analysis.

Covers: VALUE, BOUNDARY for _bucket_by_date, _trend_direction,
_compute_trends, analyze.
"""

from unittest.mock import patch

from prism.trends import _bucket_by_date, _compute_trends, _trend_direction, analyze

# =====================================================================
# _bucket_by_date — VALUE
# =====================================================================


class TestBucketByDate:
    def test_groups_by_date(self):
        summaries = [
            {"ts": "2026-04-06T12:00:00Z", "x": 1},
            {"ts": "2026-04-06T13:00:00Z", "x": 2},
            {"ts": "2026-04-07T10:00:00Z", "x": 3},
        ]
        result = _bucket_by_date(summaries)
        assert len(result) == 2
        assert len(result["2026-04-06"]) == 2
        assert len(result["2026-04-07"]) == 1

    def test_empty(self):
        assert _bucket_by_date([]) == {}

    def test_missing_ts(self):
        result = _bucket_by_date([{"x": 1}])
        assert "unknown" in result

    def test_short_ts(self):
        result = _bucket_by_date([{"ts": "2026"}])
        assert "unknown" in result

    def test_sorted_output(self):
        summaries = [
            {"ts": "2026-04-07T00:00:00Z"},
            {"ts": "2026-04-05T00:00:00Z"},
        ]
        result = _bucket_by_date(summaries)
        keys = list(result.keys())
        assert keys == ["2026-04-05", "2026-04-07"]


# =====================================================================
# _trend_direction — VALUE, BOUNDARY
# =====================================================================


class TestTrendDirection:
    def test_rising(self):
        assert _trend_direction([1.0, 2.0, 5.0, 8.0]) == "rising"

    def test_falling(self):
        assert _trend_direction([8.0, 6.0, 2.0, 1.0]) == "falling"

    def test_stable(self):
        assert _trend_direction([5.0, 5.0, 5.0, 5.0]) == "stable"

    def test_insufficient_data(self):
        """BOUNDARY: fewer than 4 values."""
        assert _trend_direction([1.0, 2.0, 3.0]) == "insufficient data"

    def test_boundary_exactly_4(self):
        """BOUNDARY: exactly 4 values should produce a result."""
        result = _trend_direction([1.0, 1.0, 10.0, 10.0])
        assert result in ("rising", "falling", "stable")

    def test_empty(self):
        assert _trend_direction([]) == "insufficient data"


# =====================================================================
# _compute_trends — VALUE
# =====================================================================


class TestComputeTrends:
    def test_basic_trends(self):
        summaries = [
            {
                "efficiency_score": 80,
                "error_rate": 0.1,
                "tool_calls": 20,
                "duration_sec": 600,
                "tool_distribution": {"Read": 10, "Edit": 5},
            },
            {
                "efficiency_score": 90,
                "error_rate": 0.05,
                "tool_calls": 25,
                "duration_sec": 900,
                "tool_distribution": {"Read": 15, "Bash": 3},
            },
        ]
        result = _compute_trends(summaries)
        assert result["sessions_analyzed"] == 2
        assert result["efficiency"]["current"] == 90
        assert result["error_rate"]["current"] == 0.05
        assert result["tool_calls"]["total"] == 45
        assert result["top_tools"]["Read"] == 25

    def test_empty(self):
        assert _compute_trends([]) == {}

    def test_partial_data(self):
        """VALUE: summaries with missing fields should not crash."""
        summaries = [{"tool_calls": 10}, {"error_rate": 0.1}]
        result = _compute_trends(summaries)
        assert result["sessions_analyzed"] == 2
        assert "tool_calls" in result
        assert "error_rate" in result
        assert "efficiency" not in result


# =====================================================================
# analyze — VALUE (integration)
# =====================================================================


class TestAnalyze:
    def test_with_data(self, tmp_path, monkeypatch):
        monkeypatch.setattr("prism.engine.PRISM_DIR", tmp_path)
        monkeypatch.setattr("prism.engine.SNAPSHOTS_DIR", tmp_path / "snapshots")
        monkeypatch.setattr("prism.engine.SESSIONS_DIR", tmp_path / "sessions")
        monkeypatch.setattr("prism.engine.DAILY_DIR", tmp_path / "daily")
        monkeypatch.setattr("prism.engine.HEALTH_DIR", tmp_path / "health")

        summaries = [
            {
                "ts": "2026-04-07T12:00:00Z",
                "efficiency_score": 85,
                "error_rate": 0.05,
                "tool_calls": 30,
                "project": "Prism",
            }
        ]
        with patch("prism.trends.engine.read_daily_summaries", return_value=summaries):
            result = analyze(days=7)
        assert "# Cross-Session Trends" in result
        assert "Sessions: 1" in result

    def test_no_data(self, tmp_path, monkeypatch):
        monkeypatch.setattr("prism.engine.PRISM_DIR", tmp_path)
        monkeypatch.setattr("prism.engine.SNAPSHOTS_DIR", tmp_path / "snapshots")
        monkeypatch.setattr("prism.engine.SESSIONS_DIR", tmp_path / "sessions")
        monkeypatch.setattr("prism.engine.DAILY_DIR", tmp_path / "daily")
        monkeypatch.setattr("prism.engine.HEALTH_DIR", tmp_path / "health")

        with patch("prism.trends.engine.read_daily_summaries", return_value=[]):
            result = analyze(days=7)
        assert "No Data Yet" in result

    def test_project_filter(self, tmp_path, monkeypatch):
        monkeypatch.setattr("prism.engine.PRISM_DIR", tmp_path)
        monkeypatch.setattr("prism.engine.SNAPSHOTS_DIR", tmp_path / "snapshots")
        monkeypatch.setattr("prism.engine.SESSIONS_DIR", tmp_path / "sessions")
        monkeypatch.setattr("prism.engine.DAILY_DIR", tmp_path / "daily")
        monkeypatch.setattr("prism.engine.HEALTH_DIR", tmp_path / "health")

        summaries = [
            {"ts": "2026-04-07T12:00:00Z", "project": "Prism", "tool_calls": 10},
            {"ts": "2026-04-07T13:00:00Z", "project": "Other", "tool_calls": 20},
        ]
        with patch("prism.trends.engine.read_daily_summaries", return_value=summaries):
            result = analyze(days=7, project="Prism")
        assert "Sessions: 1" in result
