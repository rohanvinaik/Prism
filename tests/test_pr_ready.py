"""Tests for prism.pr_ready — prescriptive targets from mutation analysis.

Covers: VALUE, BOUNDARY for _git_status, _lintgate_status, assess.
"""

import json
from pathlib import Path
from unittest.mock import patch

from prism.pr_ready import _lintgate_status, assess

# =====================================================================
# _lintgate_status — VALUE
# =====================================================================


class TestLintgateStatus:
    def test_no_directory(self):
        with patch("prism.pr_ready.Path.home", return_value=Path("/nonexistent")):
            result = _lintgate_status("/some/path")
        assert result["available"] is False

    def test_with_run_file(self, tmp_path):
        lg_dir = tmp_path / ".claude" / "lintgate" / "analysis" / "controlplane_run"
        lg_dir.mkdir(parents=True)
        run_data = {
            "counts": {"blocking": 2, "warning": 10},
            "coherence": "stable",
        }
        (lg_dir / "run1.json").write_text(json.dumps(run_data))

        with patch("prism.pr_ready.Path.home", return_value=tmp_path):
            result = _lintgate_status("/some/path")
        assert result["available"] is True
        assert result["blocking"] == 2
        assert result["warnings"] == 10
        assert result["coherence"] == "stable"

    def test_corrupt_json(self, tmp_path):
        lg_dir = tmp_path / ".claude" / "lintgate" / "analysis" / "controlplane_run"
        lg_dir.mkdir(parents=True)
        (lg_dir / "run1.json").write_text("not json")

        with patch("prism.pr_ready.Path.home", return_value=tmp_path):
            result = _lintgate_status("/some/path")
        assert result["available"] is False


# =====================================================================
# assess — VALUE, BOUNDARY
# =====================================================================


class TestAssess:
    def _run(self, tmp_path, monkeypatch, git=None, health_score=80, bridge=None, lg=None):
        monkeypatch.setattr("prism.engine.PRISM_DIR", tmp_path / "prism")
        monkeypatch.setattr("prism.engine.SNAPSHOTS_DIR", tmp_path / "prism" / "snapshots")
        monkeypatch.setattr("prism.engine.SESSIONS_DIR", tmp_path / "prism" / "sessions")
        monkeypatch.setattr("prism.engine.DAILY_DIR", tmp_path / "prism" / "daily")
        monkeypatch.setattr("prism.engine.HEALTH_DIR", tmp_path / "prism" / "health")

        proj = tmp_path / "project"
        proj.mkdir()

        if git is None:
            git = {"branch": "feature", "clean": True, "uncommitted_files": 0}
        if lg is None:
            lg = {"available": False}

        health_checks = {
            "score": health_score,
            "lockfile": {"found": "uv.lock", "stale": False},
            "secrets": {"env_committed": False},
        }

        with (
            patch("prism.pr_ready._git_status", return_value=git),
            patch("prism.pr_ready._lintgate_status", return_value=lg),
            patch("prism.pr_ready.health.assess", return_value=health_checks),
            patch("prism.pr_ready.engine.read_bridge", return_value=bridge),
        ):
            return assess(str(proj))

    def test_all_pass(self, tmp_path, monkeypatch):
        result = self._run(tmp_path, monkeypatch)
        assert "**PASS**" in result
        assert "All checks passed" in result

    def test_dirty_git_blocks(self, tmp_path, monkeypatch):
        git = {"branch": "feature", "clean": False, "uncommitted_files": 5}
        result = self._run(tmp_path, monkeypatch, git=git)
        assert "**BLOCKED**" in result
        assert "Uncommitted changes: 5" in result

    def test_main_branch_warning(self, tmp_path, monkeypatch):
        git = {"branch": "main", "clean": True, "uncommitted_files": 0}
        result = self._run(tmp_path, monkeypatch, git=git)
        assert "main/master branch" in result

    def test_low_health_blocks(self, tmp_path, monkeypatch):
        """BOUNDARY: score < 40 blocks."""
        result = self._run(tmp_path, monkeypatch, health_score=39)
        assert "**BLOCKED**" in result
        assert "critical gaps" in result

    def test_boundary_health_40_warns(self, tmp_path, monkeypatch):
        """BOUNDARY: score == 40 should NOT block (< not <=)."""
        result = self._run(tmp_path, monkeypatch, health_score=40)
        assert "**PASS**" in result

    def test_medium_health_warns(self, tmp_path, monkeypatch):
        result = self._run(tmp_path, monkeypatch, health_score=60)
        assert "some gaps" in result

    def test_env_committed_blocks(self, tmp_path, monkeypatch):
        monkeypatch.setattr("prism.engine.PRISM_DIR", tmp_path / "prism")
        monkeypatch.setattr("prism.engine.SNAPSHOTS_DIR", tmp_path / "prism" / "snapshots")
        monkeypatch.setattr("prism.engine.SESSIONS_DIR", tmp_path / "prism" / "sessions")
        monkeypatch.setattr("prism.engine.DAILY_DIR", tmp_path / "prism" / "daily")
        monkeypatch.setattr("prism.engine.HEALTH_DIR", tmp_path / "prism" / "health")

        proj = tmp_path / "project"
        proj.mkdir()

        health_checks = {
            "score": 80,
            "lockfile": {"found": "uv.lock", "stale": False},
            "secrets": {"env_committed": True},
        }
        git = {"branch": "feature", "clean": True, "uncommitted_files": 0}

        with (
            patch("prism.pr_ready._git_status", return_value=git),
            patch("prism.pr_ready._lintgate_status", return_value={"available": False}),
            patch("prism.pr_ready.health.assess", return_value=health_checks),
            patch("prism.pr_ready.engine.read_bridge", return_value=None),
        ):
            result = assess(str(proj))
        assert "**BLOCKED**" in result
        assert "CRITICAL" in result

    def test_lintgate_blockers(self, tmp_path, monkeypatch):
        lg = {"available": True, "blocking": 3, "coherence": "stable"}
        result = self._run(tmp_path, monkeypatch, lg=lg)
        assert "**BLOCKED**" in result
        assert "3 blocking" in result

    def test_lintgate_degraded_warns(self, tmp_path, monkeypatch):
        lg = {"available": True, "blocking": 0, "coherence": "degraded"}
        result = self._run(tmp_path, monkeypatch, lg=lg)
        assert "coherence: degraded" in result

    def test_high_error_rate_warns(self, tmp_path, monkeypatch):
        bridge = {"error_rate": 0.25, "efficiency_score": 70}
        result = self._run(tmp_path, monkeypatch, bridge=bridge)
        assert "error rate" in result

    def test_low_efficiency_warns(self, tmp_path, monkeypatch):
        bridge = {"error_rate": 0.0, "efficiency_score": 40}
        result = self._run(tmp_path, monkeypatch, bridge=bridge)
        assert "efficiency 40/100" in result

    def test_empty_path_errors(self):
        assert "Error" in assess("")

    def test_nonexistent_path_errors(self):
        assert "Error" in assess("/nonexistent/path/xyz")

    def test_stale_lockfile_blocks(self, tmp_path, monkeypatch):
        monkeypatch.setattr("prism.engine.PRISM_DIR", tmp_path / "prism")
        monkeypatch.setattr("prism.engine.SNAPSHOTS_DIR", tmp_path / "prism" / "snapshots")
        monkeypatch.setattr("prism.engine.SESSIONS_DIR", tmp_path / "prism" / "sessions")
        monkeypatch.setattr("prism.engine.DAILY_DIR", tmp_path / "prism" / "daily")
        monkeypatch.setattr("prism.engine.HEALTH_DIR", tmp_path / "prism" / "health")

        proj = tmp_path / "project"
        proj.mkdir()

        health_checks = {
            "score": 80,
            "lockfile": {"found": "uv.lock", "stale": True},
            "secrets": {"env_committed": False},
        }
        git = {"branch": "feature", "clean": True, "uncommitted_files": 0}

        with (
            patch("prism.pr_ready._git_status", return_value=git),
            patch("prism.pr_ready._lintgate_status", return_value={"available": False}),
            patch("prism.pr_ready.health.assess", return_value=health_checks),
            patch("prism.pr_ready.engine.read_bridge", return_value=None),
        ):
            result = assess(str(proj))
        assert "**BLOCKED**" in result
        assert "stale" in result.lower()
