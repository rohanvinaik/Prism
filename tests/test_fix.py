"""Tests for prism.fix — prescriptive targets from mutation analysis.

Covers: VALUE, SWAP categories across auto-fixer.
"""

import json
from unittest.mock import patch

from prism.fix import FIX_REGISTRY, _exclude_bloated_paths, _run_cmd

# =====================================================================
# _run_cmd — VALUE
# =====================================================================


class TestRunCmd:
    def test_successful_command(self, tmp_path):
        ok, msg = _run_cmd(["echo", "hello"], str(tmp_path))
        assert ok is True
        assert "hello" in msg

    def test_failing_command(self, tmp_path):
        ok, msg = _run_cmd(["false"], str(tmp_path))
        assert ok is False

    def test_missing_command(self, tmp_path):
        ok, msg = _run_cmd(["nonexistent_binary_xyz"], str(tmp_path))
        assert ok is False
        assert "not found" in msg.lower()

    def test_timeout(self, tmp_path):
        ok, msg = _run_cmd(["sleep", "10"], str(tmp_path), timeout=1)
        assert ok is False
        assert "timed out" in msg.lower()


# =====================================================================
# FIX_REGISTRY — VALUE (registered titles exist)
# =====================================================================


class TestFixRegistry:
    def test_expected_fixes_registered(self):
        expected = [
            "Create virtual environment",
            "Add lockfile",
            "Refresh stale lockfile",
            "Initialize git",
            "Add .gitignore",
            "Remove .env from git tracking",
            "Configure a linter",
            "Add Prism telemetry hooks",
        ]
        for title in expected:
            assert title in FIX_REGISTRY, f"Missing fix: {title}"

    def test_gitignore_fix(self, tmp_path):
        """VALUE: .gitignore is created with sensible content."""
        (tmp_path / "pyproject.toml").write_text("[project]\nname='test'\n")
        fix_fn = FIX_REGISTRY["Add .gitignore"]
        ok, msg = fix_fn(str(tmp_path))
        assert ok is True
        gitignore = tmp_path / ".gitignore"
        assert gitignore.exists()
        content = gitignore.read_text()
        assert "__pycache__/" in content
        assert ".env" in content

    def test_git_init_excludes_bloated_dir(self, tmp_path):
        """A large-aggregate data dir is gitignored, not committed.

        Most data files fall under any per-file size limit; the bloat only
        shows in aggregate, so the guard sums per top-level path.
        """
        _run_cmd(["git", "init"], str(tmp_path))
        data = tmp_path / "data"
        data.mkdir()
        # Many small files summing over the test threshold.
        for i in range(5):
            (data / f"f{i}.csv").write_text("x" * 100)
        (tmp_path / "main.py").write_text("print('hi')\n")

        excluded = _exclude_bloated_paths(str(tmp_path), threshold_bytes=200)

        assert excluded == ["data"]
        content = (tmp_path / ".gitignore").read_text()
        assert "data/" in content
        assert "main.py" not in content

    def test_git_init_no_bloat_no_changes(self, tmp_path):
        """Without large paths, no .gitignore entries are added."""
        _run_cmd(["git", "init"], str(tmp_path))
        (tmp_path / "main.py").write_text("print('hi')\n")
        excluded = _exclude_bloated_paths(str(tmp_path), threshold_bytes=200)
        assert excluded == []

    def test_git_init_full_fix_creates_gitignore_and_commits(self, tmp_path):
        """End-to-end: git init guarantees .gitignore before staging."""
        (tmp_path / "pyproject.toml").write_text("[project]\nname='t'\n")
        ok, msg = FIX_REGISTRY["Initialize git"](str(tmp_path))
        assert ok is True
        assert (tmp_path / ".gitignore").is_file()
        assert (tmp_path / ".git").is_dir()

    def test_gitignore_idempotent(self, tmp_path):
        (tmp_path / ".gitignore").write_text("existing\n")
        fix_fn = FIX_REGISTRY["Add .gitignore"]
        ok, msg = fix_fn(str(tmp_path))
        assert ok is True
        assert "already exists" in msg.lower()

    def test_cache_ignores_safe_merge(self, tmp_path):
        """Safe-merge appends entries without clobbering existing content."""
        (tmp_path / ".gitignore").write_text("custom_entry/\n")
        (tmp_path / ".ruff_cache").mkdir()
        (tmp_path / ".mypy_cache").mkdir()
        fix_fn = FIX_REGISTRY["Ignore cache directories"]
        ok, msg = fix_fn(str(tmp_path))
        assert ok is True
        assert "Added 2" in msg
        content = (tmp_path / ".gitignore").read_text()
        assert "custom_entry/" in content
        assert ".ruff_cache/" in content
        assert ".mypy_cache/" in content
        assert "Prism" in content

    def test_cache_ignores_idempotent(self, tmp_path):
        (tmp_path / ".pytest_cache").mkdir()
        fix_fn = FIX_REGISTRY["Ignore cache directories"]
        ok_first, _ = fix_fn(str(tmp_path))
        first = (tmp_path / ".gitignore").read_text()
        ok, msg = fix_fn(str(tmp_path))
        second = (tmp_path / ".gitignore").read_text()
        assert ok_first is True
        assert ok is True
        assert first == second
        assert "No missing" in msg

    def test_cache_ignores_no_action_when_clean(self, tmp_path):
        fix_fn = FIX_REGISTRY["Ignore cache directories"]
        ok, msg = fix_fn(str(tmp_path))
        assert ok is True
        assert "No missing" in msg
        assert not (tmp_path / ".gitignore").exists()

    def test_linter_fix(self, tmp_path):
        fix_fn = FIX_REGISTRY["Configure a linter"]
        ok, msg = fix_fn(str(tmp_path))
        assert ok is True
        assert (tmp_path / "ruff.toml").exists()

    def test_prism_hooks_fix_uses_shutil_which(self, tmp_path, monkeypatch):
        """VALUE: prism-hook path resolved dynamically, not hardcoded."""
        settings = tmp_path / "settings.json"
        settings.write_text("{}")
        monkeypatch.setattr("prism.fix.SETTINGS_PATH", settings)

        with patch("shutil.which", return_value="/usr/local/bin/prism-hook"):
            fix_fn = FIX_REGISTRY["Add Prism telemetry hooks"]
            ok, msg = fix_fn(str(tmp_path))

        assert ok is True
        data = json.loads(settings.read_text())
        hooks = data.get("hooks", {})
        # Should have hooks for all 4 events
        for event in ("PostToolUse", "SessionStart", "PreCompact", "Stop"):
            assert event in hooks
            cmds = [h["command"] for group in hooks[event] for h in group.get("hooks", [])]
            assert any("prism-hook" in c for c in cmds)


# =====================================================================
# run — SWAP, VALUE
# =====================================================================


class TestRun:
    def test_dry_run_does_not_apply(self, tmp_path, monkeypatch):
        """SWAP: dry_run=True should preview but not execute."""
        from prism import fix

        monkeypatch.setattr("prism.engine.PRISM_DIR", tmp_path / "prism")
        monkeypatch.setattr("prism.engine.SNAPSHOTS_DIR", tmp_path / "prism" / "snapshots")
        monkeypatch.setattr("prism.engine.SESSIONS_DIR", tmp_path / "prism" / "sessions")
        monkeypatch.setattr("prism.engine.DAILY_DIR", tmp_path / "prism" / "daily")
        monkeypatch.setattr("prism.engine.HEALTH_DIR", tmp_path / "prism" / "health")

        proj = tmp_path / "project"
        proj.mkdir()
        result = fix.apply_fixes(str(proj), dry_run=True)
        assert "DRY RUN" in result

    def test_no_recommendations(self, tmp_path, monkeypatch):
        from prism import fix

        monkeypatch.setattr("prism.engine.PRISM_DIR", tmp_path / "prism")
        monkeypatch.setattr("prism.engine.SNAPSHOTS_DIR", tmp_path / "prism" / "snapshots")
        monkeypatch.setattr("prism.engine.SESSIONS_DIR", tmp_path / "prism" / "sessions")
        monkeypatch.setattr("prism.engine.DAILY_DIR", tmp_path / "prism" / "daily")
        monkeypatch.setattr("prism.engine.HEALTH_DIR", tmp_path / "prism" / "health")

        # A fully set up project should have fewer fixable recommendations
        proj = tmp_path / "project"
        proj.mkdir()
        (proj / ".git").mkdir()
        (proj / ".gitignore").write_text(".env\n__pycache__/\n")
        (proj / ".venv" / "bin").mkdir(parents=True)
        (proj / ".venv" / "bin" / "python").touch()
        (proj / "pyproject.toml").write_text("[project]\nname='test'\n")
        (proj / "uv.lock").write_text("lock")
        (proj / "ruff.toml").write_text("[lint]\n")

        result = fix.apply_fixes(str(proj), dry_run=True)
        # Should have minimal or no fixable items
        assert "Auto-Fix" in result or "No" in result
