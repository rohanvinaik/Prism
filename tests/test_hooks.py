"""Tests for prism.hooks — prescriptive targets from mutation analysis.

Covers: VALUE, TYPE categories across hook handlers and helpers.
"""

import json

from prism.hooks import (
    _check_consecutive_errors,
    _compute_efficiency,
    _project_from_cwd,
    _session_id,
    handle_post_tool_use,
    handle_pre_compact,
    handle_session_start,
    handle_stop,
    main,
)

# =====================================================================
# _session_id — VALUE
# =====================================================================


class TestSessionId:
    def test_from_data(self):
        assert _session_id({"session_id": "abc123"}) == "abc123"

    def test_from_env(self, monkeypatch):
        monkeypatch.setenv("CLAUDE_SESSION_ID", "env_sess")
        assert _session_id({}) == "env_sess"

    def test_fallback_unknown(self, monkeypatch):
        monkeypatch.delenv("CLAUDE_SESSION_ID", raising=False)
        assert _session_id({}) == "unknown"

    def test_data_takes_precedence_over_env(self, monkeypatch):
        monkeypatch.setenv("CLAUDE_SESSION_ID", "env_sess")
        assert _session_id({"session_id": "data_sess"}) == "data_sess"


# =====================================================================
# _project_from_cwd — VALUE
# =====================================================================


class TestProjectFromCwd:
    def test_from_data(self):
        assert _project_from_cwd({"cwd": "/some/path"}) == "/some/path"

    def test_fallback_to_cwd(self):
        import os

        result = _project_from_cwd({})
        assert result == os.getcwd()


# =====================================================================
# _check_consecutive_errors — VALUE
# =====================================================================


class TestCheckConsecutiveErrors:
    def test_no_errors(self):
        events = [
            {"event": "tool_use", "error": False},
            {"event": "tool_use", "error": False},
        ]
        assert _check_consecutive_errors(events) == 0

    def test_all_errors(self):
        events = [
            {"event": "tool_use", "error": True},
            {"event": "tool_use", "error": True},
            {"event": "tool_use", "error": True},
        ]
        assert _check_consecutive_errors(events) == 3

    def test_error_streak_broken(self):
        events = [
            {"event": "tool_use", "error": True},
            {"event": "tool_use", "error": False},
            {"event": "tool_use", "error": True},
            {"event": "tool_use", "error": True},
        ]
        assert _check_consecutive_errors(events) == 2

    def test_non_tool_events_ignored(self):
        events = [
            {"event": "tool_use", "error": True},
            {"event": "pre_compact"},
            {"event": "tool_use", "error": True},
        ]
        assert _check_consecutive_errors(events) == 2

    def test_empty_events(self):
        assert _check_consecutive_errors([]) == 0


# =====================================================================
# _compute_efficiency — VALUE
# =====================================================================


class TestComputeEfficiency:
    def test_basic_efficiency(self):
        events = [
            {
                "event": "tool_use",
                "error": False,
                "output_bytes": 100,
                "ts": "2026-04-07T12:00:00Z",
            },
            {
                "event": "tool_use",
                "error": False,
                "output_bytes": 200,
                "ts": "2026-04-07T12:01:00Z",
            },
            {"event": "tool_use", "error": True, "output_bytes": 50, "ts": "2026-04-07T12:02:00Z"},
        ]
        result = _compute_efficiency(events)
        assert result["tool_calls"] == 3
        assert result["errors"] == 1
        assert abs(result["error_rate"] - 0.333) < 0.01
        assert result["total_output_bytes"] == 350
        assert result["compactions"] == 0
        assert result["duration_sec"] == 120

    def test_perfect_session(self):
        events = [
            {
                "event": "tool_use",
                "error": False,
                "output_bytes": 100,
                "ts": "2026-04-07T12:00:00Z",
            },
            {
                "event": "tool_use",
                "error": False,
                "output_bytes": 100,
                "ts": "2026-04-07T12:00:30Z",
            },
        ]
        result = _compute_efficiency(events)
        assert result["error_rate"] == 0.0
        assert result["efficiency_score"] == 100

    def test_compactions_penalize_score(self):
        events = [
            {"event": "tool_use", "error": False, "ts": "2026-04-07T12:00:00Z"},
            {"event": "pre_compact", "ts": "2026-04-07T12:01:00Z"},
            {"event": "pre_compact", "ts": "2026-04-07T12:02:00Z"},
            {"event": "pre_compact", "ts": "2026-04-07T12:03:00Z"},
            {"event": "tool_use", "error": False, "ts": "2026-04-07T12:04:00Z"},
        ]
        result = _compute_efficiency(events)
        assert result["compactions"] == 3
        assert result["efficiency_score"] == 85  # 100 - (3 * 5)

    def test_empty_events(self):
        assert _compute_efficiency([]) == {}

    def test_no_tool_events(self):
        events = [{"event": "session_start"}, {"event": "pre_compact"}]
        assert _compute_efficiency(events) == {}


# =====================================================================
# handle_post_tool_use — TYPE, VALUE
# =====================================================================


class TestHandlePostToolUse:
    def test_records_event(self, tmp_path, monkeypatch):
        monkeypatch.setattr("prism.engine.PRISM_DIR", tmp_path)
        monkeypatch.setattr("prism.engine.SNAPSHOTS_DIR", tmp_path / "snapshots")
        monkeypatch.setattr("prism.engine.SESSIONS_DIR", tmp_path / "sessions")
        monkeypatch.setattr("prism.engine.DAILY_DIR", tmp_path / "daily")
        monkeypatch.setattr("prism.engine.HEALTH_DIR", tmp_path / "health")

        result = handle_post_tool_use(
            {
                "session_id": "test_sess",
                "tool_name": "Read",
                "tool_output": "file contents here",
            }
        )
        assert result == {}  # silent by default
        from prism.engine import read_events

        events = read_events("test_sess")
        assert len(events) == 1
        assert events[0]["tool"] == "Read"
        assert events[0]["error"] is False

    def test_detects_error_in_string_output(self, tmp_path, monkeypatch):
        """TYPE: string tool_output with error keyword."""
        monkeypatch.setattr("prism.engine.PRISM_DIR", tmp_path)
        monkeypatch.setattr("prism.engine.SNAPSHOTS_DIR", tmp_path / "snapshots")
        monkeypatch.setattr("prism.engine.SESSIONS_DIR", tmp_path / "sessions")
        monkeypatch.setattr("prism.engine.DAILY_DIR", tmp_path / "daily")
        monkeypatch.setattr("prism.engine.HEALTH_DIR", tmp_path / "health")

        handle_post_tool_use(
            {
                "session_id": "err_sess",
                "tool_name": "Bash",
                "tool_output": "Error: command not found",
            }
        )
        from prism.engine import read_events

        events = read_events("err_sess")
        assert events[0]["error"] is True

    def test_detects_error_in_dict_output(self, tmp_path, monkeypatch):
        """TYPE: dict tool_output with error key."""
        monkeypatch.setattr("prism.engine.PRISM_DIR", tmp_path)
        monkeypatch.setattr("prism.engine.SNAPSHOTS_DIR", tmp_path / "snapshots")
        monkeypatch.setattr("prism.engine.SESSIONS_DIR", tmp_path / "sessions")
        monkeypatch.setattr("prism.engine.DAILY_DIR", tmp_path / "daily")
        monkeypatch.setattr("prism.engine.HEALTH_DIR", tmp_path / "health")

        handle_post_tool_use(
            {
                "session_id": "dict_sess",
                "tool_name": "Bash",
                "tool_output": {"error": True},
            }
        )
        from prism.engine import read_events

        events = read_events("dict_sess")
        assert events[0]["error"] is True

    def test_consecutive_error_warning(self, tmp_path, monkeypatch):
        monkeypatch.setattr("prism.engine.PRISM_DIR", tmp_path)
        monkeypatch.setattr("prism.engine.SNAPSHOTS_DIR", tmp_path / "snapshots")
        monkeypatch.setattr("prism.engine.SESSIONS_DIR", tmp_path / "sessions")
        monkeypatch.setattr("prism.engine.DAILY_DIR", tmp_path / "daily")
        monkeypatch.setattr("prism.engine.HEALTH_DIR", tmp_path / "health")

        for _i in range(3):
            result = handle_post_tool_use(
                {
                    "session_id": "streak",
                    "tool_name": "Bash",
                    "tool_output": "Traceback (most recent call last)",
                }
            )
        assert "systemMessage" in result
        assert "consecutive" in result["systemMessage"].lower()


# =====================================================================
# handle_session_start — VALUE
# =====================================================================


class TestHandleSessionStart:
    def test_records_start_event(self, tmp_path, monkeypatch):
        monkeypatch.setattr("prism.engine.PRISM_DIR", tmp_path)
        monkeypatch.setattr("prism.engine.SNAPSHOTS_DIR", tmp_path / "snapshots")
        monkeypatch.setattr("prism.engine.SESSIONS_DIR", tmp_path / "sessions")
        monkeypatch.setattr("prism.engine.DAILY_DIR", tmp_path / "daily")
        monkeypatch.setattr("prism.engine.HEALTH_DIR", tmp_path / "health")

        result = handle_session_start({"session_id": "start_test", "cwd": "/tmp"})
        assert result == {}
        from prism.engine import read_events

        events = read_events("start_test")
        assert events[0]["event"] == "session_start"
        assert events[0]["project"] == "/tmp"


# =====================================================================
# handle_pre_compact — VALUE
# =====================================================================


class TestHandlePreCompact:
    def test_records_compaction(self, tmp_path, monkeypatch):
        monkeypatch.setattr("prism.engine.PRISM_DIR", tmp_path)
        monkeypatch.setattr("prism.engine.SNAPSHOTS_DIR", tmp_path / "snapshots")
        monkeypatch.setattr("prism.engine.SESSIONS_DIR", tmp_path / "sessions")
        monkeypatch.setattr("prism.engine.DAILY_DIR", tmp_path / "daily")
        monkeypatch.setattr("prism.engine.HEALTH_DIR", tmp_path / "health")

        handle_session_start({"session_id": "compact_test"})
        handle_post_tool_use(
            {"session_id": "compact_test", "tool_name": "Read", "tool_output": "ok"}
        )
        result = handle_pre_compact({"session_id": "compact_test"})
        assert result == {}
        from prism.engine import read_events

        events = read_events("compact_test")
        compact_events = [e for e in events if e["event"] == "pre_compact"]
        assert len(compact_events) == 1
        assert compact_events[0]["tools_so_far"] == 1


# =====================================================================
# handle_stop — VALUE
# =====================================================================


class TestHandleStop:
    def test_writes_summary_and_bridge(self, tmp_path, monkeypatch):
        monkeypatch.setattr("prism.engine.PRISM_DIR", tmp_path)
        monkeypatch.setattr("prism.engine.SNAPSHOTS_DIR", tmp_path / "snapshots")
        monkeypatch.setattr("prism.engine.SESSIONS_DIR", tmp_path / "sessions")
        monkeypatch.setattr("prism.engine.DAILY_DIR", tmp_path / "daily")
        monkeypatch.setattr("prism.engine.HEALTH_DIR", tmp_path / "health")
        monkeypatch.setattr("prism.engine.BRIDGE_FILE", tmp_path / "bridge.json")

        handle_session_start({"session_id": "stop_test", "cwd": "/project"})
        handle_post_tool_use({"session_id": "stop_test", "tool_name": "Read", "tool_output": "ok"})
        result = handle_stop({"session_id": "stop_test"})
        assert result == {}  # no anomaly

        bridge = json.loads((tmp_path / "bridge.json").read_text())
        assert bridge["session_id"] == "stop_test"
        assert bridge["tool_calls"] == 1
        assert bridge["error_rate"] == 0.0

    def test_high_error_rate_warning(self, tmp_path, monkeypatch):
        monkeypatch.setattr("prism.engine.PRISM_DIR", tmp_path)
        monkeypatch.setattr("prism.engine.SNAPSHOTS_DIR", tmp_path / "snapshots")
        monkeypatch.setattr("prism.engine.SESSIONS_DIR", tmp_path / "sessions")
        monkeypatch.setattr("prism.engine.DAILY_DIR", tmp_path / "daily")
        monkeypatch.setattr("prism.engine.HEALTH_DIR", tmp_path / "health")
        monkeypatch.setattr("prism.engine.BRIDGE_FILE", tmp_path / "bridge.json")

        handle_session_start({"session_id": "bad_sess"})
        # 3 errors, 1 success = 75% error rate
        for _ in range(3):
            handle_post_tool_use(
                {"session_id": "bad_sess", "tool_name": "Bash", "tool_output": "Error: fail"}
            )
        handle_post_tool_use({"session_id": "bad_sess", "tool_name": "Read", "tool_output": "ok"})
        result = handle_stop({"session_id": "bad_sess"})
        assert "systemMessage" in result
        assert "error rate" in result["systemMessage"].lower()

    def test_empty_session_silent(self, tmp_path, monkeypatch):
        monkeypatch.setattr("prism.engine.PRISM_DIR", tmp_path)
        monkeypatch.setattr("prism.engine.SESSIONS_DIR", tmp_path / "sessions")
        result = handle_stop({"session_id": "empty_sess"})
        assert result == {}


# =====================================================================
# main — VALUE (dispatch)
# =====================================================================


class TestMain:
    def test_dispatches_known_event(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr("prism.engine.PRISM_DIR", tmp_path)
        monkeypatch.setattr("prism.engine.SNAPSHOTS_DIR", tmp_path / "snapshots")
        monkeypatch.setattr("prism.engine.SESSIONS_DIR", tmp_path / "sessions")
        monkeypatch.setattr("prism.engine.DAILY_DIR", tmp_path / "daily")
        monkeypatch.setattr("prism.engine.HEALTH_DIR", tmp_path / "health")
        monkeypatch.setattr(
            "sys.stdin",
            __import__("io").StringIO(
                json.dumps({"type": "SessionStart", "session_id": "main_test"})
            ),
        )
        main()
        captured = capsys.readouterr()
        assert captured.out.strip() == "{}"

    def test_unknown_event_silent(self, monkeypatch, capsys):
        monkeypatch.setattr(
            "sys.stdin", __import__("io").StringIO(json.dumps({"type": "UnknownEvent"}))
        )
        main()
        assert capsys.readouterr().out.strip() == "{}"

    def test_empty_stdin_silent(self, monkeypatch, capsys):
        monkeypatch.setattr("sys.stdin", __import__("io").StringIO(""))
        main()
        assert capsys.readouterr().out.strip() == "{}"

    def test_invalid_json_silent(self, monkeypatch, capsys):
        monkeypatch.setattr("sys.stdin", __import__("io").StringIO("not json"))
        main()
        assert capsys.readouterr().out.strip() == "{}"
