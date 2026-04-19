"""Tests for cross-project pattern learning."""

import json

from prism.cross_project import SCHEMA_VERSION, load_state, patterns, summarize, update


def _make_attrs(**overrides):
    base = {
        "type": "library",
        "has_ci": True,
        "has_tests": True,
        "has_claude_md": True,
        "has_lockfile": True,
        "has_readme": True,
        "last_scanned": 0,
        "name": "p",
    }
    base.update(overrides)
    return base


class TestStateRoundTrip:
    def test_load_missing_returns_empty(self, tmp_path):
        state = load_state(tmp_path / "missing.json")
        assert state == {"schema_version": SCHEMA_VERSION, "projects": {}}

    def test_load_corrupt_returns_empty(self, tmp_path):
        path = tmp_path / "corrupt.json"
        path.write_text("not json")
        state = load_state(path)
        assert state["projects"] == {}

    def test_old_schema_treated_empty(self, tmp_path):
        path = tmp_path / "old.json"
        path.write_text(json.dumps({"schema_version": 0, "projects": {"x": {}}}))
        state = load_state(path)
        assert state["projects"] == {}

    def test_update_persists_then_loads(self, tmp_path):
        path = tmp_path / "state.json"
        checks = {"ci": {"found": True}, "lockfile": {"found": "uv.lock"}}
        update(str(tmp_path / "proj"), checks, "library", path=path)
        reloaded = load_state(path)
        assert len(reloaded["projects"]) == 1


class TestPatterns:
    def test_no_peers_no_findings(self, tmp_path):
        state = {"schema_version": SCHEMA_VERSION, "projects": {}}
        # Add only the target — no peers
        path = tmp_path / "p"
        state["projects"][str(path.resolve())] = _make_attrs()
        result = patterns(state, str(path))
        assert result == []

    def test_minimum_three_peers_required(self, tmp_path):
        """With only 2 peers (under the floor), no pattern should fire."""
        state = {"schema_version": SCHEMA_VERSION, "projects": {}}
        target = tmp_path / "target"
        state["projects"][str(target.resolve())] = _make_attrs(has_ci=False)
        for i in range(2):
            p = tmp_path / f"peer_{i}"
            state["projects"][str(p.resolve())] = _make_attrs(has_ci=True)
        assert patterns(state, str(target)) == []

    def test_pattern_fires_when_majority_peers_have_attribute(self, tmp_path):
        state = {"schema_version": SCHEMA_VERSION, "projects": {}}
        target = tmp_path / "target"
        state["projects"][str(target.resolve())] = _make_attrs(has_ci=False)
        for i in range(3):
            p = tmp_path / f"peer_{i}"
            state["projects"][str(p.resolve())] = _make_attrs(has_ci=True)
        result = patterns(state, str(target))
        attrs = [r["attribute"] for r in result]
        assert "has_ci" in attrs

    def test_pattern_skips_when_below_two_thirds(self, tmp_path):
        state = {"schema_version": SCHEMA_VERSION, "projects": {}}
        target = tmp_path / "target"
        state["projects"][str(target.resolve())] = _make_attrs(has_ci=False)
        # 1/3 peers have CI — below threshold
        for i in range(3):
            p = tmp_path / f"peer_{i}"
            state["projects"][str(p.resolve())] = _make_attrs(has_ci=(i == 0))
        attrs = [r["attribute"] for r in patterns(state, str(target))]
        assert "has_ci" not in attrs

    def test_only_same_type_peers_compared(self, tmp_path):
        """A library target shouldn't compare against MCP peers."""
        state = {"schema_version": SCHEMA_VERSION, "projects": {}}
        target = tmp_path / "lib"
        state["projects"][str(target.resolve())] = _make_attrs(type="library", has_ci=False)
        # 3 MCP servers with CI — different type, should be ignored
        for i in range(3):
            p = tmp_path / f"mcp_{i}"
            state["projects"][str(p.resolve())] = _make_attrs(type="mcp_server", has_ci=True)
        # Only 1 library peer (without CI) — under floor
        p = tmp_path / "libpeer"
        state["projects"][str(p.resolve())] = _make_attrs(type="library", has_ci=False)
        assert patterns(state, str(target)) == []

    def test_target_having_attribute_skipped(self, tmp_path):
        state = {"schema_version": SCHEMA_VERSION, "projects": {}}
        target = tmp_path / "target"
        state["projects"][str(target.resolve())] = _make_attrs(has_ci=True)
        for i in range(3):
            p = tmp_path / f"peer_{i}"
            state["projects"][str(p.resolve())] = _make_attrs(has_ci=True)
        attrs = [r["attribute"] for r in patterns(state, str(target))]
        assert "has_ci" not in attrs


class TestSummarize:
    def test_empty_state_friendly_message(self, tmp_path, monkeypatch):
        monkeypatch.setattr("prism.cross_project.PROJECTS_STATE_PATH", tmp_path / "x.json")
        out = summarize(str(tmp_path / "fresh"))
        assert "No gaps detected" in out

    def test_findings_render_with_ratio(self, tmp_path):
        state = {"schema_version": SCHEMA_VERSION, "projects": {}}
        target = tmp_path / "target"
        state["projects"][str(target.resolve())] = _make_attrs(has_ci=False)
        for i in range(3):
            p = tmp_path / f"peer_{i}"
            state["projects"][str(p.resolve())] = _make_attrs(has_ci=True)
        out = summarize(str(target), state=state)
        assert "CI" in out
        assert "100%" in out
