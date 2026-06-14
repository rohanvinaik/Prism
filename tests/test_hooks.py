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
    handle_user_prompt,
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

    def test_includes_workflow_mode_for_lintgate_bridge(self):
        # Read-heavy session → "Explore"; surfaced so LintGate can adopt it.
        events = [
            {"event": "tool_use", "tool": "Read", "error": False} for _ in range(6)
        ]
        result = _compute_efficiency(events)
        assert result["workflow_mode"] == "Explore"


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


class TestHandleStopBridge:
    """handle_stop enriches the LintGate bridge with workflow_mode + effect."""

    def _patch_dirs(self, tmp_path, monkeypatch):
        monkeypatch.setattr("prism.engine.PRISM_DIR", tmp_path)
        monkeypatch.setattr("prism.engine.SNAPSHOTS_DIR", tmp_path / "snapshots")
        monkeypatch.setattr("prism.engine.SESSIONS_DIR", tmp_path / "sessions")
        monkeypatch.setattr("prism.engine.DAILY_DIR", tmp_path / "daily")
        monkeypatch.setattr("prism.engine.HEALTH_DIR", tmp_path / "health")
        monkeypatch.setattr("prism.engine.BRIDGE_FILE", tmp_path / "bridge.json")

    def test_bridge_includes_workflow_mode(self, tmp_path, monkeypatch):
        self._patch_dirs(tmp_path, monkeypatch)
        from prism.engine import append_event, read_bridge

        append_event("s1", {"event": "session_start", "project": "/p"})
        for _ in range(6):
            append_event("s1", {"event": "tool_use", "tool": "Read", "error": False})
        handle_stop({"session_id": "s1"})
        bridge = read_bridge()
        assert bridge["workflow_mode"] == "Explore"
        assert "compaction_effect" not in bridge  # no boundaries

    def test_bridge_includes_compaction_effect(self, tmp_path, monkeypatch):
        self._patch_dirs(tmp_path, monkeypatch)
        from prism.engine import append_event, read_bridge

        append_event("s2", {"event": "session_start", "project": "/p"})
        # pre-compact context, then a boundary, then >=5 post tool_use events
        for _ in range(3):
            append_event("s2", {"event": "tool_use", "tool": "Edit", "error": False})
        append_event("s2", {"event": "pre_compact", "frame_injected": True, "tools_so_far": 3})
        for i in range(6):
            append_event(
                "s2",
                {"event": "tool_use", "tool": "Edit", "error": i < 2, "file_path": f"f{i}.py"},
            )
        handle_stop({"session_id": "s2"})
        effect = read_bridge()["compaction_effect"]
        assert effect["boundaries"] == 1
        assert "mean_error_rate_delta" in effect
        assert "mean_re_read_rate" in effect


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
        # Force injection path (disable random baseline) for deterministic assertions
        monkeypatch.setenv("PRISM_BASELINE_FRACTION", "0")

        handle_session_start({"session_id": "compact_test"})
        handle_post_tool_use(
            {"session_id": "compact_test", "tool_name": "Read", "tool_output": "ok"}
        )
        result = handle_pre_compact({"session_id": "compact_test"})
        assert "error" not in result
        from prism.engine import read_events

        events = read_events("compact_test")
        compact_events = [e for e in events if e["event"] == "pre_compact"]
        assert len(compact_events) == 1
        e = compact_events[0]
        assert e["tools_so_far"] == 1
        # New validation fields
        assert "frame" in e
        assert "frame_length" in e
        assert "frame_injected" in e
        assert "frame_disabled" in e
        assert "baseline_reason" in e
        assert e["frame_disabled"] is False
        assert e["baseline_reason"] == "none"
        assert e["frame_length"] == len(e["frame"])

    def test_disable_frame_via_env(self, tmp_path, monkeypatch):
        monkeypatch.setattr("prism.engine.PRISM_DIR", tmp_path)
        monkeypatch.setattr("prism.engine.SNAPSHOTS_DIR", tmp_path / "snapshots")
        monkeypatch.setattr("prism.engine.SESSIONS_DIR", tmp_path / "sessions")
        monkeypatch.setattr("prism.engine.DAILY_DIR", tmp_path / "daily")
        monkeypatch.setattr("prism.engine.HEALTH_DIR", tmp_path / "health")
        monkeypatch.setenv("PRISM_DISABLE_FRAME", "1")

        handle_session_start({"session_id": "nf_test"})
        handle_post_tool_use({"session_id": "nf_test", "tool_name": "Read", "tool_output": "ok"})
        result = handle_pre_compact({"session_id": "nf_test"})
        assert result == {}

        from prism.engine import read_events

        events = read_events("nf_test")
        e = events[-1]
        assert e["frame_disabled"] is True
        assert e["frame_injected"] is False
        assert e["baseline_reason"] == "env_disabled"

    def test_auto_baseline_via_random_roll(self, tmp_path, monkeypatch):
        monkeypatch.setattr("prism.engine.PRISM_DIR", tmp_path)
        monkeypatch.setattr("prism.engine.SNAPSHOTS_DIR", tmp_path / "snapshots")
        monkeypatch.setattr("prism.engine.SESSIONS_DIR", tmp_path / "sessions")
        monkeypatch.setattr("prism.engine.DAILY_DIR", tmp_path / "daily")
        monkeypatch.setattr("prism.engine.HEALTH_DIR", tmp_path / "health")
        # Force the random roll to always select baseline
        import prism.hooks as hooks_mod

        monkeypatch.setattr(hooks_mod, "_roll_baseline", lambda frac: True)

        handle_session_start({"session_id": "auto_baseline"})
        handle_post_tool_use(
            {"session_id": "auto_baseline", "tool_name": "Read", "tool_output": "ok"}
        )
        result = handle_pre_compact({"session_id": "auto_baseline"})
        assert result == {}

        from prism.engine import read_events

        e = read_events("auto_baseline")[-1]
        assert e["frame_disabled"] is True
        assert e["baseline_reason"] == "random_baseline"

    def test_baseline_fraction_zero_always_injects(self, tmp_path, monkeypatch):
        monkeypatch.setattr("prism.engine.PRISM_DIR", tmp_path)
        monkeypatch.setattr("prism.engine.SNAPSHOTS_DIR", tmp_path / "snapshots")
        monkeypatch.setattr("prism.engine.SESSIONS_DIR", tmp_path / "sessions")
        monkeypatch.setattr("prism.engine.DAILY_DIR", tmp_path / "daily")
        monkeypatch.setattr("prism.engine.HEALTH_DIR", tmp_path / "health")
        monkeypatch.setenv("PRISM_BASELINE_FRACTION", "0")

        handle_session_start({"session_id": "no_baseline"})
        handle_post_tool_use(
            {"session_id": "no_baseline", "tool_name": "Read", "tool_output": "ok"}
        )
        handle_pre_compact({"session_id": "no_baseline"})

        from prism.engine import read_events

        e = read_events("no_baseline")[-1]
        assert e["frame_disabled"] is False
        assert e["baseline_reason"] == "none"

    def test_baseline_fraction_invalid_falls_back(self, tmp_path, monkeypatch):
        monkeypatch.setattr("prism.engine.PRISM_DIR", tmp_path)
        monkeypatch.setattr("prism.engine.SNAPSHOTS_DIR", tmp_path / "snapshots")
        monkeypatch.setattr("prism.engine.SESSIONS_DIR", tmp_path / "sessions")
        monkeypatch.setattr("prism.engine.DAILY_DIR", tmp_path / "daily")
        monkeypatch.setattr("prism.engine.HEALTH_DIR", tmp_path / "health")
        monkeypatch.setenv("PRISM_BASELINE_FRACTION", "not_a_number")

        from prism import hooks as hooks_mod

        assert hooks_mod._baseline_fraction() == hooks_mod.DEFAULT_BASELINE_FRACTION


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
# handle_user_prompt — VALUE (classification + compaction nudge)
# =====================================================================


class TestHandleUserPrompt:
    def _setup_paths(self, tmp_path, monkeypatch):
        monkeypatch.setattr("prism.engine.PRISM_DIR", tmp_path)
        monkeypatch.setattr("prism.engine.SESSIONS_DIR", tmp_path / "sessions")
        monkeypatch.setattr("prism.engine.SNAPSHOTS_DIR", tmp_path / "snapshots")
        monkeypatch.setattr("prism.engine.DAILY_DIR", tmp_path / "daily")
        monkeypatch.setattr("prism.engine.HEALTH_DIR", tmp_path / "health")
        monkeypatch.setattr("prism.phase_matcher._STATE_DIR", tmp_path / "phase_state")

    def test_records_user_prompt_event_with_classification(self, tmp_path, monkeypatch):
        self._setup_paths(tmp_path, monkeypatch)
        handle_user_prompt({"session_id": "up_test", "userMessage": "implement the feature"})
        from prism.engine import read_events

        events = read_events("up_test")
        prompt_events = [e for e in events if e.get("event") == "user_prompt"]
        assert len(prompt_events) == 1
        assert prompt_events[0]["label"] == "directive"
        assert "confidence" in prompt_events[0]
        assert "signals" in prompt_events[0]

    def test_empty_message_silent(self, tmp_path, monkeypatch):
        self._setup_paths(tmp_path, monkeypatch)
        result = handle_user_prompt({"session_id": "empty", "userMessage": ""})
        assert result == {}
        from prism.engine import read_events

        assert read_events("empty") == []

    def test_compaction_nudge_on_directive_after_many_tools(self, tmp_path, monkeypatch):
        self._setup_paths(tmp_path, monkeypatch)
        handle_session_start({"session_id": "nudge"})
        # Mixed tool history → avoids the edit-burst guard
        seq = ["Read", "Bash", "Read", "Bash", "Grep"] * 5
        for tool in seq:
            handle_post_tool_use({"session_id": "nudge", "tool_name": tool, "tool_output": "ok"})
        result = handle_user_prompt(
            {"session_id": "nudge", "userMessage": "now implement the next feature"}
        )
        assert "systemMessage" in result
        assert "/compact" in result["systemMessage"]

    def test_no_nudge_on_continuation(self, tmp_path, monkeypatch):
        self._setup_paths(tmp_path, monkeypatch)
        handle_session_start({"session_id": "nonudge"})
        for _ in range(25):
            handle_post_tool_use(
                {"session_id": "nonudge", "tool_name": "Read", "tool_output": "ok"}
            )
        result = handle_user_prompt({"session_id": "nonudge", "userMessage": "yes"})
        # Continuation shouldn't trigger compaction nudge
        assert "systemMessage" not in result


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
