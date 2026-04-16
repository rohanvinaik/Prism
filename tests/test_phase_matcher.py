"""Tests for prism.phase_matcher — focus on recent additions."""

import pytest

from prism import phase_matcher as pm


@pytest.fixture
def tmp_state(tmp_path, monkeypatch):
    monkeypatch.setattr("prism.engine.PRISM_DIR", tmp_path)
    monkeypatch.setattr("prism.engine.SESSIONS_DIR", tmp_path / "sessions")
    monkeypatch.setattr("prism.phase_matcher._STATE_DIR", tmp_path / "phase_state")
    return tmp_path


# =====================================================================
# _suggest_next_action — heuristic hints
# =====================================================================


class TestSuggestNextAction:
    def test_too_few_symbols(self):
        assert pm._suggest_next_action(["R", "R"]) == ""

    def test_read_burst_suggests_edit(self):
        symbols = ["R", "R", "R", "R", "R"]
        hint = pm._suggest_next_action(symbols)
        assert "edit" in hint.lower()

    def test_edit_without_test_suggests_run(self):
        symbols = ["R", "W", "W", "W"]
        hint = pm._suggest_next_action(symbols)
        assert "test" in hint.lower() or "run" in hint.lower()

    def test_execution_burst_suggests_review(self):
        symbols = ["X", "X", "X", "X"]
        hint = pm._suggest_next_action(symbols)
        assert "review" in hint.lower()

    def test_errors_override_all(self):
        symbols = ["X", "X", "X"]
        hint = pm._suggest_next_action(symbols, recent_errors=2)
        assert "diagnose" in hint.lower() or "error" in hint.lower()

    def test_healthy_rwx_rhythm_silent(self):
        symbols = ["R", "W", "X", "R", "W", "X"]
        assert pm._suggest_next_action(symbols) == ""


# =====================================================================
# record_user_text — classification integration
# =====================================================================


class TestRecordUserText:
    def test_directive_updates_state(self, tmp_state):
        result = pm.record_user_text("s1", "implement the feature")
        assert result["label"] == "directive"
        summary = pm.get_phase_summary("s1")
        assert summary["user_text_class"] == "directive"
        assert summary["user_text_confidence"] > 0.3

    def test_continuation_does_not_reset_directive_marker(self, tmp_state):
        pm.record_user_text("s1", "implement the feature")
        # Simulate some tool calls by updating state manually
        internal = pm._load_state("s1")
        internal.tool_count = 10
        pm._save_state("s1", internal)

        pm.record_user_text("s1", "yes")
        state2 = pm.get_phase_summary("s1")
        # tools_at_last_directive preserved (continuation shouldn't bump it)
        assert state2["user_text_class"] == "continuation"
        assert state2["tools_since_directive"] == 10


# =====================================================================
# build_narrative_frame — labeled user intent + next hint
# =====================================================================


class TestBuildNarrativeFrame:
    def test_directive_labeled(self, tmp_state):
        pm.record_user_text("s1", "implement the feature")
        frame = pm.build_narrative_frame("s1")
        assert "User directive:" in frame

    def test_continuation_labeled(self, tmp_state):
        pm.record_user_text("s1", "yes")
        frame = pm.build_narrative_frame("s1")
        assert "User followup:" in frame

    def test_clarification_labeled(self, tmp_state):
        pm.record_user_text("s1", "why did you do that?")
        frame = pm.build_narrative_frame("s1")
        assert "User question:" in frame

    def test_next_hint_on_edit_burst(self, tmp_state):
        # Simulate a read+edit pattern — should suggest running tests
        pm.record_tool("s1", "Read", "/a.py")
        pm.record_tool("s1", "Read", "/b.py")
        pm.record_tool("s1", "Edit", "/a.py")
        pm.record_tool("s1", "Edit", "/b.py")
        pm.record_tool("s1", "Write", "/c.py")
        frame = pm.build_narrative_frame("s1")
        assert "Next:" in frame

    def test_empty_session_no_frame(self, tmp_state):
        # No user text, no tools → minimal frame
        frame = pm.build_narrative_frame("new_sess")
        # Only "Tools used: 0" survives
        assert "Tools used: 0" in frame
        assert "Next:" not in frame  # No hint without tool history
