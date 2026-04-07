"""Tests for prism.trajectory — prescriptive targets from mutation analysis.

Covers: VALUE, BOUNDARY, SWAP for _bucket_weekly, _activity_data,
_quality_data, _decisions_data, _filter_decisions_by_project, analyze.
"""

from datetime import UTC, datetime
from unittest.mock import patch

from prism.trajectory import (
    _activity_data,
    _bucket_weekly,
    _decisions_data,
    _filter_decisions_by_project,
    _quality_data,
    analyze,
)

# =====================================================================
# _bucket_weekly — VALUE
# =====================================================================


class TestBucketWeekly:
    def test_buckets_by_week(self):
        daily = {
            "2026-04-06": {"messageCount": 10, "sessionCount": 2, "toolCallCount": 30},
            "2026-04-07": {"messageCount": 5, "sessionCount": 1, "toolCallCount": 15},
        }
        result = _bucket_weekly(daily)
        assert len(result) == 1  # same week
        week_key = list(result.keys())[0]
        assert result[week_key]["messages"] == 15
        assert result[week_key]["sessions"] == 3

    def test_multiple_weeks(self):
        daily = {
            "2026-03-31": {"messageCount": 10, "sessionCount": 1, "toolCallCount": 5},
            "2026-04-07": {"messageCount": 20, "sessionCount": 2, "toolCallCount": 10},
        }
        result = _bucket_weekly(daily)
        assert len(result) == 2

    def test_empty(self):
        assert _bucket_weekly({}) == {}

    def test_invalid_date_skipped(self):
        daily = {"not-a-date": {"messageCount": 10}}
        assert _bucket_weekly(daily) == {}


# =====================================================================
# _activity_data — VALUE, BOUNDARY
# =====================================================================


class TestActivityData:
    def test_basic_aggregation(self):
        stats = {
            "2026-04-06": {"messageCount": 10, "sessionCount": 2, "toolCallCount": 30},
            "2026-04-07": {"messageCount": 5, "sessionCount": 0, "toolCallCount": 15},
        }
        since = datetime(2026, 4, 1, tzinfo=UTC)
        result = _activity_data(stats, since)
        assert result["total_days"] == 2
        assert result["active_days"] == 1  # only 2026-04-06 has sessions > 0
        assert result["messages"] == 15
        assert result["sessions"] == 2
        assert result["tool_calls"] == 45

    def test_empty_stats(self):
        assert _activity_data({}, None) == {}

    def test_all_filtered_out(self):
        stats = {"2026-01-01": {"messageCount": 10, "sessionCount": 1}}
        since = datetime(2026, 4, 1, tzinfo=UTC)
        assert _activity_data(stats, since) == {}

    def test_none_since_includes_all(self):
        stats = {
            "2020-01-01": {"messageCount": 5, "sessionCount": 1, "toolCallCount": 10},
            "2026-04-07": {"messageCount": 10, "sessionCount": 2, "toolCallCount": 20},
        }
        result = _activity_data(stats, None)
        assert result["total_days"] == 2
        assert result["messages"] == 15

    def test_includes_weekly(self):
        stats = {"2026-04-07": {"messageCount": 10, "sessionCount": 1, "toolCallCount": 5}}
        result = _activity_data(stats, None)
        assert "weekly" in result
        assert len(result["weekly"]) == 1


# =====================================================================
# _quality_data — VALUE
# =====================================================================


class TestQualityData:
    def test_counts_events(self):
        metrics = [
            {"event": "lint_run"},
            {"event": "lint_run"},
            {"event": "controlplane_run"},
        ]
        with patch("prism.trajectory.sources.read_lintgate_metrics", return_value=metrics):
            result = _quality_data(None)
        assert result["lint_runs"] == 2
        assert result["controlplane_runs"] == 1

    def test_purity_ratios(self):
        metrics = [
            {"event": "performance_analysis", "purity_ratio": 0.5},
            {"event": "performance_analysis", "purity_ratio": 0.8},
        ]
        with patch("prism.trajectory.sources.read_lintgate_metrics", return_value=metrics):
            result = _quality_data(None)
        assert result["purity_first"] == 0.5
        assert result["purity_last"] == 0.8

    def test_empty_metrics(self):
        with patch("prism.trajectory.sources.read_lintgate_metrics", return_value=[]):
            assert _quality_data(None) == {}

    def test_no_purity_events(self):
        metrics = [{"event": "lint_run"}]
        with patch("prism.trajectory.sources.read_lintgate_metrics", return_value=metrics):
            result = _quality_data(None)
        assert result["purity_first"] is None


# =====================================================================
# _decisions_data — VALUE
# =====================================================================


class TestDecisionsData:
    def test_counts_categories(self):
        decisions = [
            {"category": "architecture", "outcome": "accepted"},
            {"category": "architecture", "outcome": "rejected"},
            {"category": "naming", "outcome": "accepted"},
        ]
        with patch("prism.trajectory.sources.read_continuity_decisions", return_value=decisions):
            result = _decisions_data(None)
        assert result["total"] == 3
        assert result["by_category"]["architecture"] == 2
        assert result["by_outcome"]["accepted"] == 2

    def test_empty(self):
        with patch("prism.trajectory.sources.read_continuity_decisions", return_value=[]):
            assert _decisions_data(None) == {}


# =====================================================================
# _filter_decisions_by_project — VALUE, BOUNDARY
# =====================================================================


class TestFilterDecisionsByProject:
    def test_no_project_returns_as_is(self):
        data = {"total": 5, "by_category": {}, "by_outcome": {}}
        assert _filter_decisions_by_project(data, "", None) is data

    def test_empty_decisions_returns_as_is(self):
        assert _filter_decisions_by_project({}, "proj", None) == {}

    def test_filters_by_project(self):
        decisions = {"total": 3, "by_category": {"x": 3}, "by_outcome": {"y": 3}}
        raw = [
            {"category": "arch", "outcome": "ok", "project_name": "Prism"},
            {"category": "arch", "outcome": "ok", "project_name": "Other"},
        ]
        with patch("prism.trajectory.sources.read_continuity_decisions", return_value=raw):
            result = _filter_decisions_by_project(decisions, "Prism", None)
        assert result["total"] == 1
        assert result["by_category"]["arch"] == 1

    def test_no_matches_returns_empty(self):
        decisions = {"total": 1, "by_category": {}, "by_outcome": {}}
        raw = [{"category": "x", "outcome": "y", "project_name": "Other"}]
        with patch("prism.trajectory.sources.read_continuity_decisions", return_value=raw):
            result = _filter_decisions_by_project(decisions, "Prism", None)
        assert result == {}


# =====================================================================
# analyze — VALUE (integration)
# =====================================================================


class TestAnalyze:
    def test_with_activity(self, tmp_path, monkeypatch):
        monkeypatch.setattr("prism.engine.PRISM_DIR", tmp_path)
        monkeypatch.setattr("prism.engine.SNAPSHOTS_DIR", tmp_path / "snapshots")
        monkeypatch.setattr("prism.engine.SESSIONS_DIR", tmp_path / "sessions")
        monkeypatch.setattr("prism.engine.DAILY_DIR", tmp_path / "daily")
        monkeypatch.setattr("prism.engine.HEALTH_DIR", tmp_path / "health")

        stats = {"2026-04-07": {"messageCount": 10, "sessionCount": 2, "toolCallCount": 30}}
        with (
            patch("prism.trajectory.sources.period_to_since", return_value=None),
            patch("prism.trajectory.sources.read_stats_cache", return_value=stats),
            patch("prism.trajectory.sources.read_lintgate_metrics", return_value=[]),
            patch("prism.trajectory.sources.read_continuity_decisions", return_value=[]),
            patch("prism.trajectory.sources.read_mneme_recent", return_value={}),
        ):
            result = analyze("month")
        assert "# Trajectory" in result
        assert "Activity:" in result
        assert "2 sessions" in result

    def test_no_data(self, tmp_path, monkeypatch):
        monkeypatch.setattr("prism.engine.PRISM_DIR", tmp_path)
        monkeypatch.setattr("prism.engine.SNAPSHOTS_DIR", tmp_path / "snapshots")
        monkeypatch.setattr("prism.engine.SESSIONS_DIR", tmp_path / "sessions")
        monkeypatch.setattr("prism.engine.DAILY_DIR", tmp_path / "daily")
        monkeypatch.setattr("prism.engine.HEALTH_DIR", tmp_path / "health")

        with (
            patch("prism.trajectory.sources.period_to_since", return_value=None),
            patch("prism.trajectory.sources.read_stats_cache", return_value={}),
            patch("prism.trajectory.sources.read_lintgate_metrics", return_value=[]),
            patch("prism.trajectory.sources.read_continuity_decisions", return_value=[]),
            patch("prism.trajectory.sources.read_mneme_recent", return_value={}),
        ):
            result = analyze("month")
        assert "No data available" in result
