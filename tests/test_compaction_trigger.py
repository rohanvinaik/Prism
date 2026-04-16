"""Tests for prism.compaction_trigger."""

import pytest

from prism import compaction_trigger as ct


@pytest.fixture
def tmp_state(tmp_path, monkeypatch):
    """Redirect prism state dirs to a tmp path."""
    monkeypatch.setattr("prism.engine.PRISM_DIR", tmp_path)
    monkeypatch.setattr("prism.engine.SESSIONS_DIR", tmp_path / "sessions")
    monkeypatch.setattr("prism.engine.SNAPSHOTS_DIR", tmp_path / "snapshots")
    monkeypatch.setattr("prism.engine.DAILY_DIR", tmp_path / "daily")
    monkeypatch.setattr("prism.engine.HEALTH_DIR", tmp_path / "health")
    monkeypatch.setattr(
        "prism.phase_matcher._STATE_DIR", tmp_path / "phase_state"
    )
    return tmp_path


def _populate(events: list[dict], session_id: str):
    """Append events via engine to the session."""
    from prism import engine
    for e in events:
        engine.append_event(session_id, e)


def _tu(tool: str = "Read", error: bool = False) -> dict:
    return {"event": "tool_use", "tool": tool, "error": error}


class TestShouldCompact:
    def test_empty_session(self, tmp_state):
        rec = ct.should_compact("empty")
        assert rec.recommend is False
        assert "no events" in rec.reason

    def test_insufficient_tools(self, tmp_state):
        _populate([_tu()] * 5, "sid")
        rec = ct.should_compact("sid", directive_just_arrived=True)
        assert rec.recommend is False
        assert any("tools_since_compact" in f for f in rec.conditions_failed)

    def test_all_conditions_pass_with_directive(self, tmp_state):
        _populate([_tu()] * 25, "sid")
        rec = ct.should_compact("sid", directive_just_arrived=True)
        assert rec.recommend is True
        assert "directive_boundary" in rec.conditions_passed
        assert rec.confidence == 1.0

    def test_no_directive_no_idle_stretch(self, tmp_state):
        # 25 tools, no directive, <30 since "directive" (PhaseState defaults to 0
        # so tools_since_directive = tool_count = 0, treated as "just had one").
        # Use tool count above 30 via tool_count to simulate idle stretch.
        _populate([_tu()] * 25, "sid")
        rec = ct.should_compact("sid", directive_just_arrived=False)
        # phase state tool_count is 0 (no hook wiring in test) → tools_since_directive
        # defaults to 0, so no_phase_boundary fails.
        assert rec.recommend is False
        assert "no_phase_boundary" in rec.conditions_failed

    def test_edit_burst_blocks(self, tmp_state):
        events = [_tu()] * 20 + [_tu("Edit"), _tu("Edit"), _tu("Edit")]
        _populate(events, "sid")
        rec = ct.should_compact("sid", directive_just_arrived=True)
        assert rec.recommend is False
        assert "in_edit_burst" in rec.conditions_failed

    def test_cooldown_after_recent_compact(self, tmp_state):
        # 25 tools, then compact, then only 3 more tools — cooldown violation
        events = [_tu()] * 25 + [{"event": "pre_compact"}] + [_tu()] * 3
        _populate(events, "sid")
        rec = ct.should_compact("sid", directive_just_arrived=True)
        assert rec.recommend is False
        assert any(f.startswith("cooldown_active") for f in rec.conditions_failed)
        assert any(f.startswith("tools_since_compact") for f in rec.conditions_failed)

    def test_cooldown_ok_after_many_tools(self, tmp_state):
        events = [_tu()] * 5 + [{"event": "pre_compact"}] + [_tu()] * 25
        _populate(events, "sid")
        rec = ct.should_compact("sid", directive_just_arrived=True)
        assert rec.recommend is True
        assert "cooldown_ok" in rec.conditions_passed


class TestFormatNudge:
    def test_nudge_contains_tool_count(self, tmp_state):
        _populate([_tu()] * 25, "sid")
        rec = ct.should_compact("sid", directive_just_arrived=True)
        msg = ct.format_nudge(rec)
        assert "[Prism]" in msg
        assert "25" in msg
        assert "/compact" in msg

    def test_nudge_mentions_pattern(self, tmp_state):
        _populate([_tu()] * 25, "sid")
        rec = ct.should_compact("sid", directive_just_arrived=True)
        rec.metrics["current_pattern"] = "RWX"
        msg = ct.format_nudge(rec)
        assert "RWX" in msg


class TestRecommendationSerialization:
    def test_to_dict_shape(self, tmp_state):
        _populate([_tu()] * 25, "sid")
        rec = ct.should_compact("sid", directive_just_arrived=True)
        d = rec.to_dict()
        assert set(d.keys()) == {
            "recommend", "reason", "confidence",
            "conditions_passed", "conditions_failed", "metrics"
        }
