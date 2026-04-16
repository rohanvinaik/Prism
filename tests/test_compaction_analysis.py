"""Tests for prism.compaction_analysis."""

from prism import compaction_analysis as ca


def _tu(error: bool = False, file_path: str | None = None) -> dict:
    e = {"event": "tool_use", "tool": "Read", "error": error}
    if file_path is not None:
        e["file_path"] = file_path
    return e


def _compact(frame: str = "x", injected: bool = True, disabled: bool = False) -> dict:
    return {
        "event": "pre_compact",
        "tools_so_far": 10,
        "frame": frame,
        "frame_length": len(frame),
        "frame_injected": injected,
        "frame_disabled": disabled,
        "pattern_before": "RWX",
        "mode_before": "implementing",
    }


class TestAnalyzeBoundary:
    def test_basic_error_rates(self):
        events = [
            _tu(error=False),
            _tu(error=False),
            _tu(error=True),
            _tu(error=False),
            _compact(),
            _tu(error=True),
            _tu(error=True),
            _tu(error=False),
            _tu(error=False),
        ]
        m = ca.analyze_boundary("s1", events, boundary_idx=4, window=4)
        assert m.pre_tools == 4
        assert m.post_tools == 4
        assert m.pre_errors == 1
        assert m.post_errors == 2
        assert m.pre_error_rate == 0.25
        assert m.post_error_rate == 0.5
        assert m.error_rate_delta == 0.25

    def test_re_read_detection(self):
        events = [
            _tu(file_path="/a.py"),
            _tu(file_path="/b.py"),
            _compact(),
            _tu(file_path="/a.py"),
            _tu(file_path="/c.py"),
        ]
        m = ca.analyze_boundary("s", events, 2, window=5)
        assert set(m.files_pre) == {"/a.py", "/b.py"}
        assert set(m.files_post) == {"/a.py", "/c.py"}
        assert m.re_read_count == 1
        assert m.re_read_rate == 0.5

    def test_no_post_window(self):
        events = [_tu(), _tu(), _compact()]
        m = ca.analyze_boundary("s", events, 2)
        assert m.post_tools == 0
        assert m.post_error_rate == 0.0
        assert m.re_read_rate == 0.0

    def test_window_cap(self):
        events = [_tu()] * 50 + [_compact()] + [_tu()] * 50
        m = ca.analyze_boundary("s", events, 50, window=10)
        assert m.pre_tools == 10
        assert m.post_tools == 10

    def test_flags_extracted(self):
        events = [_compact(injected=False, disabled=True)]
        m = ca.analyze_boundary("s", events, 0)
        assert m.frame_injected is False
        assert m.frame_disabled is True
        assert m.pattern_before == "RWX"
        assert m.mode_before == "implementing"

    def test_pre_window_ignores_prior_compactions(self):
        events = [_tu(), _tu(), _compact(), _tu(error=True), _compact()]
        m = ca.analyze_boundary("s", events, 4, window=5)
        assert m.pre_tools == 1
        assert m.pre_errors == 1


class TestAggregate:
    def _boundary(self, injected: bool, post_err: float, post_n: int = 10) -> ca.BoundaryMetrics:
        return ca.BoundaryMetrics(
            session_id="s",
            boundary_idx=0,
            tools_so_far=10,
            frame_injected=injected,
            frame_disabled=not injected,
            frame_length=100,
            pattern_before="RWX",
            mode_before="implementing",
            pre_tools=10,
            post_tools=post_n,
            pre_errors=1,
            post_errors=int(post_err * post_n),
            files_pre=[],
            files_post=[],
            re_read_count=0,
        )

    def test_splits_by_frame_injected(self):
        bounds = [
            self._boundary(injected=True, post_err=0.1),
            self._boundary(injected=True, post_err=0.2),
            self._boundary(injected=False, post_err=0.4),
            self._boundary(injected=False, post_err=0.5),
        ]
        agg = ca.aggregate(bounds)
        assert agg["with_frame"]["n"] == 2
        assert agg["without_frame"]["n"] == 2
        with_mean = agg["with_frame"]["mean_post_error_rate"]
        without_mean = agg["without_frame"]["mean_post_error_rate"]
        assert with_mean < without_mean

    def test_drops_boundaries_with_short_post_window(self):
        bounds = [
            self._boundary(injected=True, post_err=0.1, post_n=3),
            self._boundary(injected=True, post_err=0.1, post_n=10),
        ]
        agg = ca.aggregate(bounds, min_post_tools=5)
        assert agg["total_boundaries"] == 2
        assert agg["eligible_boundaries"] == 1

    def test_empty(self):
        agg = ca.aggregate([])
        assert agg["total_boundaries"] == 0
        assert agg["with_frame"]["n"] == 0
        assert agg["without_frame"]["n"] == 0


class TestAnalyzeSession:
    def test_reads_from_engine(self, tmp_path, monkeypatch):
        monkeypatch.setattr("prism.engine.PRISM_DIR", tmp_path)
        monkeypatch.setattr("prism.engine.SESSIONS_DIR", tmp_path / "sessions")

        from prism import engine

        engine.append_event("test", {"event": "tool_use", "tool": "Read", "error": False})
        engine.append_event("test", _compact())
        engine.append_event("test", {"event": "tool_use", "tool": "Read", "error": True})

        results = ca.analyze_session("test", window=5)
        assert len(results) == 1
        assert results[0].frame_injected is True
        assert results[0].post_errors == 1

    def test_no_events_returns_empty(self, tmp_path, monkeypatch):
        monkeypatch.setattr("prism.engine.PRISM_DIR", tmp_path)
        monkeypatch.setattr("prism.engine.SESSIONS_DIR", tmp_path / "sessions")
        assert ca.analyze_session("nonexistent") == []


class TestAggregateRecent:
    def test_handles_missing_dir(self, tmp_path, monkeypatch):
        monkeypatch.setattr("prism.engine.SESSIONS_DIR", tmp_path / "no_sessions")
        agg = ca.aggregate_recent(days=7)
        assert agg["total_boundaries"] == 0
