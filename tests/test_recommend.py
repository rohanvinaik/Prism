"""Tests for prism.recommend — prescriptive targets from mutation analysis.

Covers: VALUE, BOUNDARY, SWAP categories across recommendation logic.
"""

from pathlib import Path
from unittest.mock import patch

from prism.recommend import (
    _efficiency_recommendations,
    _has_hook,
    _hook_recommendations,
    _rec,
    _setup_recommendations,
    _subagent_recommendations,
)
from prism.sources import SessionData, TokenUsage, ToolCallRecord

# =====================================================================
# _rec — SWAP, VALUE
# =====================================================================


class TestRec:
    def test_builds_all_fields(self):
        r = _rec("setup", "high", "Create venv", "No venv", "uv venv", 95)
        assert r["category"] == "setup"
        assert r["priority"] == "high"
        assert r["title"] == "Create venv"
        assert r["reason"] == "No venv"
        assert r["action"] == "uv venv"
        assert r["confidence"] == 95

    def test_confidence_clamped_high(self):
        """BOUNDARY: confidence above 100 is clamped."""
        r = _rec("x", "low", "t", "r", "a", 150)
        assert r["confidence"] == 100

    def test_confidence_clamped_low(self):
        """BOUNDARY: confidence below 0 is clamped."""
        r = _rec("x", "low", "t", "r", "a", -10)
        assert r["confidence"] == 0

    def test_swap_category_priority(self):
        """SWAP: category and priority are distinct fields, not interchangeable."""
        r = _rec("hooks", "medium", "t", "r", "a", 50)
        assert r["category"] == "hooks"
        assert r["priority"] == "medium"
        r2 = _rec("medium", "hooks", "t", "r", "a", 50)
        assert r2["category"] == "medium"
        assert r2["priority"] == "hooks"


# =====================================================================
# _has_hook — SWAP, VALUE
# =====================================================================


class TestHasHook:
    def test_finds_matching_hook(self):
        hooks = {
            "PostToolUse": [{"hooks": [{"type": "command", "command": "/path/to/lintgate-hook"}]}]
        }
        assert _has_hook(hooks, "PostToolUse", "lintgate") is True

    def test_no_match(self):
        hooks = {
            "PostToolUse": [{"hooks": [{"type": "command", "command": "/path/to/prism-hook"}]}]
        }
        assert _has_hook(hooks, "PostToolUse", "lintgate") is False

    def test_wrong_event(self):
        """SWAP: event name matters — PostToolUse hook not found under PreToolUse."""
        hooks = {
            "PostToolUse": [{"hooks": [{"type": "command", "command": "/path/to/lintgate-hook"}]}]
        }
        assert _has_hook(hooks, "PreToolUse", "lintgate") is False

    def test_empty_hooks(self):
        assert _has_hook({}, "PostToolUse", "anything") is False

    def test_empty_event_list(self):
        hooks = {"PostToolUse": []}
        assert _has_hook(hooks, "PostToolUse", "lintgate") is False


# =====================================================================
# _hook_recommendations — BOUNDARY, SWAP, VALUE
# =====================================================================


def _make_sessions(tool_calls: list[str]) -> list[SessionData]:
    """Helper: build sessions with specified tool call names."""
    calls = [ToolCallRecord(name=t) for t in tool_calls]
    return [SessionData(session_id="s", project="p", tool_calls=calls)]


class TestHookRecommendations:
    def test_recommends_lint_hook_when_many_edits_no_lintgate(self):
        """VALUE: generic lint recommendation when LintGate absent."""
        sessions = _make_sessions(["Edit"] * 15)
        with patch(
            "prism.recommend.available_integrations",
            return_value={
                "rtk": False,
                "lintgate": False,
                "continuity": False,
                "mneme": False,
            },
        ):
            recs = _hook_recommendations({}, sessions)
        lint_recs = [r for r in recs if "lint" in r["title"].lower()]
        assert len(lint_recs) == 1
        assert (
            "generic" not in lint_recs[0]["action"].lower()
            or "LintGate" not in lint_recs[0]["action"]
        )

    def test_recommends_lintgate_hook_when_present(self):
        """VALUE: specific LintGate recommendation when installed."""
        sessions = _make_sessions(["Edit"] * 15)
        with patch(
            "prism.recommend.available_integrations",
            return_value={
                "rtk": False,
                "lintgate": True,
                "continuity": False,
                "mneme": False,
            },
        ):
            recs = _hook_recommendations({}, sessions)
        lint_recs = [r for r in recs if "lint" in r["title"].lower()]
        assert len(lint_recs) == 1
        assert "LintGate" in lint_recs[0]["action"]

    def test_no_lint_rec_when_few_edits(self):
        """BOUNDARY: edits <= 10 should not trigger lint recommendation."""
        sessions = _make_sessions(["Edit"] * 10)
        with patch(
            "prism.recommend.available_integrations",
            return_value={
                "rtk": False,
                "lintgate": False,
                "continuity": False,
                "mneme": False,
            },
        ):
            recs = _hook_recommendations({}, sessions)
        lint_recs = [r for r in recs if "lint" in r["title"].lower()]
        assert len(lint_recs) == 0

    def test_boundary_edit_threshold(self):
        """BOUNDARY: exactly 11 edits should trigger."""
        sessions = _make_sessions(["Edit"] * 11)
        with patch(
            "prism.recommend.available_integrations",
            return_value={
                "rtk": False,
                "lintgate": False,
                "continuity": False,
                "mneme": False,
            },
        ):
            recs = _hook_recommendations({}, sessions)
        lint_recs = [r for r in recs if "lint" in r["title"].lower()]
        assert len(lint_recs) == 1

    def test_rtk_rec_only_when_rtk_present(self):
        """VALUE: RTK recommendation only appears when RTK is installed."""
        sessions = _make_sessions(["Bash"] * 15)
        with patch(
            "prism.recommend.available_integrations",
            return_value={
                "rtk": False,
                "lintgate": False,
                "continuity": False,
                "mneme": False,
            },
        ):
            recs = _hook_recommendations({}, sessions)
        rtk_recs = [r for r in recs if "rtk" in r["title"].lower()]
        assert len(rtk_recs) == 0

    def test_rtk_rec_appears_when_present(self):
        sessions = _make_sessions(["Bash"] * 15)
        with patch(
            "prism.recommend.available_integrations",
            return_value={
                "rtk": True,
                "lintgate": False,
                "continuity": False,
                "mneme": False,
            },
        ):
            recs = _hook_recommendations({}, sessions)
        rtk_recs = [r for r in recs if "rtk" in r["title"].lower()]
        assert len(rtk_recs) == 1

    def test_prism_hook_always_recommended(self):
        """VALUE: Prism telemetry hook recommended regardless."""
        with patch(
            "prism.recommend.available_integrations",
            return_value={
                "rtk": False,
                "lintgate": False,
                "continuity": False,
                "mneme": False,
            },
        ):
            recs = _hook_recommendations({}, [])
        prism_recs = [r for r in recs if "prism" in r["title"].lower()]
        assert len(prism_recs) == 1

    def test_no_prism_rec_when_already_installed(self):
        hooks = {
            "PostToolUse": [{"hooks": [{"type": "command", "command": "/path/to/prism-hook"}]}]
        }
        with patch(
            "prism.recommend.available_integrations",
            return_value={
                "rtk": False,
                "lintgate": False,
                "continuity": False,
                "mneme": False,
            },
        ):
            recs = _hook_recommendations(hooks, [])
        prism_recs = [r for r in recs if "prism" in r["title"].lower()]
        assert len(prism_recs) == 0


# =====================================================================
# _efficiency_recommendations — BOUNDARY, VALUE
# =====================================================================


class TestEfficiencyRecommendations:
    def test_high_error_rate(self, monkeypatch):
        monkeypatch.setattr("prism.engine.BRIDGE_FILE", Path("/nonexistent"))
        with patch(
            "prism.recommend.engine.read_bridge",
            return_value={
                "error_rate": 0.25,
                "tool_calls": 20,
                "compactions": 0,
            },
        ):
            recs = _efficiency_recommendations()
        assert any("error rate" in r["reason"].lower() for r in recs)

    def test_boundary_error_rate_at_threshold(self, monkeypatch):
        """BOUNDARY: exactly 0.15 should NOT trigger (> not >=)."""
        with patch(
            "prism.recommend.engine.read_bridge",
            return_value={
                "error_rate": 0.15,
                "tool_calls": 20,
                "compactions": 0,
            },
        ):
            recs = _efficiency_recommendations()
        assert not any("error rate" in r["reason"].lower() for r in recs)

    def test_boundary_error_rate_just_above(self):
        """BOUNDARY: 0.16 should trigger."""
        with patch(
            "prism.recommend.engine.read_bridge",
            return_value={
                "error_rate": 0.16,
                "tool_calls": 20,
                "compactions": 0,
            },
        ):
            recs = _efficiency_recommendations()
        assert any("error rate" in r["reason"].lower() for r in recs)

    def test_many_compactions(self):
        with patch(
            "prism.recommend.engine.read_bridge",
            return_value={
                "error_rate": 0.0,
                "tool_calls": 10,
                "compactions": 3,
            },
        ):
            recs = _efficiency_recommendations()
        assert any("compaction" in r["reason"].lower() for r in recs)

    def test_no_bridge_data(self):
        with patch("prism.recommend.engine.read_bridge", return_value=None):
            assert _efficiency_recommendations() == []


# =====================================================================
# _subagent_recommendations — BOUNDARY, VALUE
# =====================================================================


class TestSubagentRecommendations:
    def test_high_subagent_cost(self):
        """VALUE: subagent tokens > 2x main tokens triggers recommendation."""
        sessions = [
            SessionData(
                session_id="s",
                project="p",
                usage=TokenUsage(input_tokens=100),
                subagent_usage=TokenUsage(input_tokens=300),
            )
        ]
        recs = _subagent_recommendations(sessions)
        assert len(recs) == 1
        assert "subagent" in recs[0]["title"].lower()

    def test_boundary_subagent_at_2x(self):
        """BOUNDARY: exactly 2x should NOT trigger (> not >=)."""
        sessions = [
            SessionData(
                session_id="s",
                project="p",
                usage=TokenUsage(input_tokens=100),
                subagent_usage=TokenUsage(input_tokens=200),
            )
        ]
        recs = _subagent_recommendations(sessions)
        assert len(recs) == 0

    def test_no_subagents(self):
        sessions = [SessionData(session_id="s", project="p")]
        assert _subagent_recommendations(sessions) == []


# =====================================================================
# _setup_recommendations — VALUE
# =====================================================================


class TestSetupRecommendations:
    def test_empty_path(self):
        assert _setup_recommendations("") == []

    def test_nonexistent_path(self):
        assert _setup_recommendations("/nonexistent/path") == []

    def test_detects_missing_venv(self, tmp_path, monkeypatch):
        monkeypatch.delenv("CONDA_PREFIX", raising=False)
        (tmp_path / "pyproject.toml").write_text("[project]\nname='test'\n")
        recs = _setup_recommendations(str(tmp_path))
        titles = [r["title"] for r in recs]
        assert "Create virtual environment" in titles

    def test_detects_missing_git(self, tmp_path):
        recs = _setup_recommendations(str(tmp_path))
        titles = [r["title"] for r in recs]
        assert "Initialize git" in titles

    def test_detects_committed_env(self, tmp_path):
        (tmp_path / ".git").mkdir()
        (tmp_path / ".env").write_text("SECRET=x")
        recs = _setup_recommendations(str(tmp_path))
        titles = [r["title"] for r in recs]
        assert "Remove .env from git tracking" in titles
