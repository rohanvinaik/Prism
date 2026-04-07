"""Tests for prism.sources — prescriptive targets from mutation analysis.

Covers: VALUE, BOUNDARY, SWAP, TYPE categories across all prescribed functions.
"""

import json
import os
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from prism.sources import (
    SessionData,
    TokenUsage,
    _accumulate_assistant,
    _file_modified_since,
    _is_human_prompt,
    _safe_read_json,
    _safe_sqlite,
    _session_in_range,
    available_integrations,
    parse_session,
    parse_timestamp,
    period_to_since,
    project_name,
    read_stats_cache,
)

# =====================================================================
# TokenUsage — VALUE, BOUNDARY
# =====================================================================


class TestTokenUsage:
    """VALUE: __add__, total, cache_hit_rate. BOUNDARY: cache_hit_rate."""

    def test_add_combines_all_fields(self):
        a = TokenUsage(input_tokens=100, cache_creation=50, cache_read=200, output_tokens=30)
        b = TokenUsage(input_tokens=10, cache_creation=5, cache_read=20, output_tokens=3)
        result = a + b
        assert result.input_tokens == 110
        assert result.cache_creation == 55
        assert result.cache_read == 220
        assert result.output_tokens == 33

    def test_add_identity(self):
        a = TokenUsage(input_tokens=100, cache_creation=50, cache_read=200, output_tokens=30)
        zero = TokenUsage()
        result = a + zero
        assert result.total == a.total

    def test_add_is_not_commutative_in_identity(self):
        """SWAP: a + b should equal b + a (commutativity)."""
        a = TokenUsage(input_tokens=100, cache_creation=50, cache_read=200, output_tokens=30)
        b = TokenUsage(input_tokens=10, cache_creation=5, cache_read=20, output_tokens=3)
        assert (a + b).total == (b + a).total

    def test_total_sums_all_fields(self):
        t = TokenUsage(input_tokens=100, cache_creation=50, cache_read=200, output_tokens=30)
        assert t.total == 380

    def test_total_zero(self):
        assert TokenUsage().total == 0

    def test_cache_hit_rate_normal(self):
        t = TokenUsage(input_tokens=100, cache_creation=50, cache_read=350)
        # cache_read / (input + creation + read) = 350 / 500 = 0.7
        assert t.cache_hit_rate == 0.7

    def test_cache_hit_rate_boundary_zero_input(self):
        """BOUNDARY: division by zero when all input fields are 0."""
        t = TokenUsage()
        assert t.cache_hit_rate == 0.0

    def test_cache_hit_rate_boundary_all_cached(self):
        """BOUNDARY: 100% cache hit."""
        t = TokenUsage(input_tokens=0, cache_creation=0, cache_read=500)
        assert t.cache_hit_rate == 1.0

    def test_cache_hit_rate_boundary_no_cache(self):
        """BOUNDARY: 0% cache hit."""
        t = TokenUsage(input_tokens=500, cache_creation=0, cache_read=0)
        assert t.cache_hit_rate == 0.0


# =====================================================================
# project_name — VALUE, BOUNDARY
# =====================================================================


class TestProjectName:
    """VALUE: exact outputs. BOUNDARY: edge cases in prefix stripping."""

    def test_strips_user_prefix(self):
        home = str(Path.home()).lstrip("/").replace("/", "-")
        dir_name = f"-{home}-tools-Prism"
        assert project_name(dir_name) == "tools-Prism"

    def test_strips_generic_users_prefix(self):
        """BOUNDARY: -Users-someuser- pattern on another machine."""
        assert project_name("-Users-alice-my-project") == "my-project"

    def test_strips_home_prefix(self):
        """BOUNDARY: Linux -home-user- pattern."""
        assert project_name("-home-bob-code-repo") == "code-repo"

    def test_no_prefix_passthrough(self):
        assert project_name("plain-dir-name") == "plain-dir-name"

    def test_empty_string(self):
        assert project_name("") == ""

    def test_only_prefix(self):
        """BOUNDARY: dir_name is exactly the prefix with nothing after."""
        home = str(Path.home()).lstrip("/").replace("/", "-")
        dir_name = f"-{home}-"
        assert project_name(dir_name) == ""


# =====================================================================
# parse_timestamp — VALUE
# =====================================================================


class TestParseTimestamp:
    def test_iso_format(self):
        result = parse_timestamp("2026-04-07T12:00:00+00:00")
        assert result is not None
        assert result.year == 2026
        assert result.month == 4

    def test_z_suffix(self):
        result = parse_timestamp("2026-04-07T12:00:00Z")
        assert result is not None
        assert result.tzinfo is not None

    def test_none_input(self):
        assert parse_timestamp(None) is None

    def test_empty_string(self):
        assert parse_timestamp("") is None

    def test_invalid_format(self):
        assert parse_timestamp("not-a-date") is None


# =====================================================================
# period_to_since — VALUE
# =====================================================================


class TestPeriodToSince:
    def test_today(self):
        result = period_to_since("today")
        assert result is not None
        assert result.hour == 0 and result.minute == 0

    def test_week(self):
        result = period_to_since("week")
        assert result is not None
        now = datetime.now(UTC)
        diff = now - result
        assert 6 <= diff.days <= 7

    def test_month(self):
        result = period_to_since("month")
        assert result is not None
        now = datetime.now(UTC)
        diff = now - result
        assert 29 <= diff.days <= 30

    def test_quarter(self):
        result = period_to_since("quarter")
        assert result is not None
        now = datetime.now(UTC)
        diff = now - result
        assert 89 <= diff.days <= 90

    def test_all_returns_none(self):
        assert period_to_since("all") is None

    def test_unknown_defaults_to_week(self):
        result = period_to_since("garbage")
        assert result is not None
        now = datetime.now(UTC)
        diff = now - result
        assert 6 <= diff.days <= 7


# =====================================================================
# _is_human_prompt — TYPE, VALUE
# =====================================================================


class TestIsHumanPrompt:
    """TYPE: isinstance branches. VALUE: return values."""

    def test_string_content(self):
        assert _is_human_prompt("hello") is True

    def test_empty_string(self):
        """VALUE: empty string is not a prompt."""
        assert _is_human_prompt("") is False

    def test_whitespace_only(self):
        assert _is_human_prompt("   ") is False

    def test_list_with_text_block(self):
        """TYPE: list content with non-tool-result types."""
        assert _is_human_prompt([{"type": "text"}]) is True

    def test_list_all_tool_results(self):
        """TYPE: list where all blocks are tool_result."""
        content = [{"type": "tool_result"}, {"type": "tool_result"}]
        assert _is_human_prompt(content) is False

    def test_list_mixed_types(self):
        content = [{"type": "tool_result"}, {"type": "text"}]
        assert _is_human_prompt(content) is True

    def test_empty_list(self):
        assert _is_human_prompt([]) is True

    def test_non_string_non_list(self):
        """TYPE: integer input."""
        assert _is_human_prompt(42) is False


# =====================================================================
# _accumulate_assistant — SWAP, TYPE, VALUE
# =====================================================================


class TestAccumulateAssistant:
    """Tests that token fields accumulate correctly from assistant messages."""

    def test_accumulates_token_usage(self):
        """VALUE: verify all token fields are extracted."""
        session = SessionData(session_id="test", project="p")
        obj = {
            "message": {
                "usage": {
                    "input_tokens": 100,
                    "cache_creation_input_tokens": 50,
                    "cache_read_input_tokens": 200,
                    "output_tokens": 30,
                },
                "content": [],
            }
        }
        _accumulate_assistant(obj, session, "2026-04-07T00:00:00Z")
        assert session.usage.input_tokens == 100
        assert session.usage.cache_creation == 50
        assert session.usage.cache_read == 200
        assert session.usage.output_tokens == 30
        assert session.assistant_turns == 1

    def test_extracts_tool_calls(self):
        """VALUE: tool_use blocks become ToolCallRecords."""
        session = SessionData(session_id="test", project="p")
        obj = {
            "message": {
                "usage": {},
                "content": [
                    {"type": "tool_use", "name": "Read"},
                    {"type": "text", "text": "hello"},
                    {"type": "tool_use", "name": "Edit"},
                ],
            }
        }
        _accumulate_assistant(obj, session, "ts")
        assert len(session.tool_calls) == 2
        assert session.tool_calls[0].name == "Read"
        assert session.tool_calls[1].name == "Edit"

    def test_non_list_content_skipped(self):
        """TYPE: content that is not a list should not crash."""
        session = SessionData(session_id="test", project="p")
        obj = {"message": {"usage": {}, "content": "just a string"}}
        _accumulate_assistant(obj, session, "ts")
        assert len(session.tool_calls) == 0

    def test_non_dict_content_blocks_skipped(self):
        """TYPE: non-dict items in content list."""
        session = SessionData(session_id="test", project="p")
        obj = {"message": {"usage": {}, "content": ["string_block", 42]}}
        _accumulate_assistant(obj, session, "ts")
        assert len(session.tool_calls) == 0


# =====================================================================
# _file_modified_since — BOUNDARY, VALUE
# =====================================================================


class TestFileModifiedSince:
    def test_recent_file_returns_true(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("data")
        cutoff = datetime.now(UTC) - timedelta(seconds=10)
        assert _file_modified_since(f, cutoff) is True

    def test_old_cutoff_returns_true(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("data")
        cutoff = datetime(2020, 1, 1, tzinfo=UTC)
        assert _file_modified_since(f, cutoff) is True

    def test_future_cutoff_returns_false(self, tmp_path):
        """BOUNDARY: cutoff in the future."""
        f = tmp_path / "test.txt"
        f.write_text("data")
        cutoff = datetime.now(UTC) + timedelta(hours=1)
        assert _file_modified_since(f, cutoff) is False

    def test_missing_file_returns_false(self, tmp_path):
        f = tmp_path / "nonexistent.txt"
        cutoff = datetime(2020, 1, 1, tzinfo=UTC)
        assert _file_modified_since(f, cutoff) is False


# =====================================================================
# _session_in_range — BOUNDARY
# =====================================================================


class TestSessionInRange:
    def test_session_after_cutoff(self):
        s = SessionData(
            session_id="test",
            project="p",
            timestamp_start="2026-04-07T12:00:00+00:00",
        )
        cutoff = datetime(2026, 4, 7, 0, 0, tzinfo=UTC)
        assert _session_in_range(s, cutoff) is True

    def test_session_before_cutoff(self):
        s = SessionData(
            session_id="test",
            project="p",
            timestamp_start="2026-04-06T12:00:00+00:00",
        )
        cutoff = datetime(2026, 4, 7, 0, 0, tzinfo=UTC)
        assert _session_in_range(s, cutoff) is False

    def test_session_no_timestamp(self):
        """BOUNDARY: missing timestamp_start — should be treated as in range."""
        s = SessionData(session_id="test", project="p")
        cutoff = datetime(2026, 4, 7, 0, 0, tzinfo=UTC)
        assert _session_in_range(s, cutoff) is True


# =====================================================================
# _safe_read_json — VALUE
# =====================================================================


class TestSafeReadJson:
    def test_valid_json(self, tmp_path):
        f = tmp_path / "test.json"
        f.write_text('{"key": "value"}')
        assert _safe_read_json(f) == {"key": "value"}

    def test_invalid_json(self, tmp_path):
        f = tmp_path / "bad.json"
        f.write_text("not json")
        assert _safe_read_json(f) is None

    def test_missing_file(self, tmp_path):
        assert _safe_read_json(tmp_path / "missing.json") is None


# =====================================================================
# _safe_sqlite — SWAP, VALUE
# =====================================================================


class TestSafeSqlite:
    def test_valid_query(self, tmp_path):
        db = tmp_path / "test.db"
        conn = sqlite3.connect(str(db))
        conn.execute("CREATE TABLE t (a TEXT, b INT)")
        conn.execute("INSERT INTO t VALUES ('x', 1)")
        conn.commit()
        conn.close()
        result = _safe_sqlite(db, "SELECT * FROM t")
        assert result == [{"a": "x", "b": 1}]

    def test_missing_db(self, tmp_path):
        assert _safe_sqlite(tmp_path / "nope.db", "SELECT 1") == []

    def test_bad_query(self, tmp_path):
        db = tmp_path / "test.db"
        conn = sqlite3.connect(str(db))
        conn.execute("CREATE TABLE t (a TEXT)")
        conn.commit()
        conn.close()
        assert _safe_sqlite(db, "SELECT * FROM nonexistent") == []

    def test_params_passed(self, tmp_path):
        """SWAP: ensure params are bound to query placeholders, not ignored."""
        db = tmp_path / "test.db"
        conn = sqlite3.connect(str(db))
        conn.execute("CREATE TABLE t (a TEXT, b INT)")
        conn.execute("INSERT INTO t VALUES ('x', 1)")
        conn.execute("INSERT INTO t VALUES ('y', 2)")
        conn.commit()
        conn.close()
        result = _safe_sqlite(db, "SELECT * FROM t WHERE b = ?", (2,))
        assert len(result) == 1
        assert result[0]["a"] == "y"


# =====================================================================
# parse_session — SWAP, VALUE
# =====================================================================


class TestParseSession:
    def test_parses_minimal_session(self, tmp_path):
        """VALUE: session with one assistant message."""
        jsonl = tmp_path / "sess.jsonl"
        msg = {
            "type": "assistant",
            "timestamp": "2026-04-07T12:00:00Z",
            "message": {
                "usage": {"input_tokens": 100, "output_tokens": 10},
                "content": [{"type": "tool_use", "name": "Read"}],
            },
        }
        jsonl.write_text(json.dumps(msg) + "\n")
        result = parse_session(jsonl, "test-proj")
        assert result is not None
        assert result.project == "test-proj"
        assert result.usage.input_tokens == 100
        assert len(result.tool_calls) == 1

    def test_counts_human_prompts(self, tmp_path):
        lines = [
            json.dumps({"type": "user", "message": {"content": "hello"}}),
            json.dumps(
                {
                    "type": "assistant",
                    "timestamp": "2026-04-07T12:00:00Z",
                    "message": {"usage": {"input_tokens": 50}, "content": []},
                }
            ),
        ]
        jsonl = tmp_path / "sess.jsonl"
        jsonl.write_text("\n".join(lines) + "\n")
        result = parse_session(jsonl, "p")
        assert result is not None
        assert result.prompt_count == 1

    def test_empty_session_returns_none(self, tmp_path):
        """VALUE: session with no tokens returns None."""
        jsonl = tmp_path / "empty.jsonl"
        jsonl.write_text("")
        assert parse_session(jsonl, "p") is None

    def test_missing_file_returns_none(self, tmp_path):
        assert parse_session(tmp_path / "nope.jsonl", "p") is None


# =====================================================================
# read_stats_cache — TYPE, VALUE
# =====================================================================


class TestReadStatsCache:
    def test_valid_stats_cache(self, tmp_path, monkeypatch):
        data = {
            "dailyActivity": [
                {"date": "2026-04-06", "messageCount": 10, "sessionCount": 2},
                {"date": "2026-04-07", "messageCount": 5, "sessionCount": 1},
            ]
        }
        cache_file = tmp_path / "stats-cache.json"
        cache_file.write_text(json.dumps(data))
        monkeypatch.setattr("prism.sources.STATS_CACHE", cache_file)
        result = read_stats_cache()
        assert "2026-04-06" in result
        assert result["2026-04-06"]["messageCount"] == 10

    def test_missing_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr("prism.sources.STATS_CACHE", tmp_path / "nope.json")
        assert read_stats_cache() == {}

    def test_non_dict_returns_empty(self, tmp_path, monkeypatch):
        """TYPE: stats-cache is a list instead of dict."""
        f = tmp_path / "stats.json"
        f.write_text("[1, 2, 3]")
        monkeypatch.setattr("prism.sources.STATS_CACHE", f)
        assert read_stats_cache() == {}


# =====================================================================
# available_integrations — VALUE
# =====================================================================


class TestAvailableIntegrations:
    def test_all_absent(self, monkeypatch):
        """VALUE: when no optional tools are installed."""
        monkeypatch.setattr("prism.sources.RTK_DB", None)
        monkeypatch.setattr("prism.sources.MNEME_DB", None)
        monkeypatch.setattr("prism.sources.LINTGATE_METRICS_DIR", Path("/nonexistent"))
        monkeypatch.setattr("prism.sources.LINTGATE_SESSION_DIR", Path("/nonexistent"))
        monkeypatch.setattr("prism.sources.CONTINUITY_DB", Path("/nonexistent/db"))
        result = available_integrations()
        assert result == {
            "rtk": False,
            "lintgate": False,
            "continuity": False,
            "mneme": False,
        }

    def test_rtk_present(self, tmp_path, monkeypatch):
        db = tmp_path / "history.db"
        db.touch()
        monkeypatch.setattr("prism.sources.RTK_DB", db)
        monkeypatch.setattr("prism.sources.MNEME_DB", None)
        monkeypatch.setattr("prism.sources.LINTGATE_METRICS_DIR", Path("/nonexistent"))
        monkeypatch.setattr("prism.sources.LINTGATE_SESSION_DIR", Path("/nonexistent"))
        monkeypatch.setattr("prism.sources.CONTINUITY_DB", Path("/nonexistent/db"))
        result = available_integrations()
        assert result["rtk"] is True
        assert result["mneme"] is False


# =====================================================================
# _find_rtk_db / _find_mneme_db — VALUE (via integration test)
# =====================================================================


class TestFindDbs:
    def test_find_rtk_db_returns_none_when_absent(self):
        from prism.sources import _find_rtk_db

        with patch("prism.sources.HOME", Path("/nonexistent")):
            assert _find_rtk_db() is None

    def test_find_mneme_db_respects_env_var(self, tmp_path, monkeypatch):
        from prism.sources import _find_mneme_db

        db = tmp_path / "mneme.db"
        db.touch()
        monkeypatch.setenv("MNEME_DB", str(db))
        with patch("prism.sources.HOME", Path("/nonexistent")):
            result = _find_mneme_db()
        assert result == db

    def test_find_mneme_db_returns_none_when_absent(self):
        from prism.sources import _find_mneme_db

        with (
            patch("prism.sources.HOME", Path("/nonexistent")),
            patch.dict(os.environ, {}, clear=True),
        ):
            assert _find_mneme_db() is None
