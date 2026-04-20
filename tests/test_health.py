"""Tests for prism.health — direct test file to satisfy test channel mapping.

health.py already has 100% mutation kill rate (tested transitively via
test_recommend.py), but LintGate's test channel wants a direct test file.
"""

from prism.health import (
    _detect_large_staged_files,
    _detect_lockfile,
    _detect_missing_cache_ignores,
    _extract_resolver_inputs,
    _resolver_hash,
    assess,
    check,
)


class TestAssess:
    def test_detects_venv(self, tmp_path):
        (tmp_path / ".venv" / "bin").mkdir(parents=True)
        (tmp_path / ".venv" / "bin" / "python").touch()
        result = assess(str(tmp_path))
        assert result["venv"]["found"] is True

    def test_no_venv(self, tmp_path, monkeypatch):
        monkeypatch.delenv("CONDA_PREFIX", raising=False)
        result = assess(str(tmp_path))
        assert result["venv"]["found"] is False

    def test_detects_git(self, tmp_path):
        (tmp_path / ".git").mkdir()
        result = assess(str(tmp_path))
        assert result["git"]["initialized"] is True

    def test_detects_gitignore(self, tmp_path):
        (tmp_path / ".git").mkdir()
        (tmp_path / ".gitignore").write_text(".env\n")
        result = assess(str(tmp_path))
        assert result["git"]["gitignore"] is True

    def test_detects_lockfile(self, tmp_path):
        (tmp_path / "uv.lock").write_text("lock")
        result = assess(str(tmp_path))
        assert result["lockfile"]["found"] == "uv.lock"

    def test_detects_ci(self, tmp_path):
        (tmp_path / ".github" / "workflows").mkdir(parents=True)
        (tmp_path / ".github" / "workflows" / "ci.yml").write_text("on: push")
        result = assess(str(tmp_path))
        assert result["ci"]["found"] is True

    def test_score_range(self, tmp_path):
        result = assess(str(tmp_path))
        assert 0 <= result["score"] <= 100

    def test_full_setup_high_score(self, tmp_path):
        (tmp_path / ".git").mkdir()
        (tmp_path / ".gitignore").write_text(".env\n")
        (tmp_path / ".venv" / "bin").mkdir(parents=True)
        (tmp_path / ".venv" / "bin" / "python").touch()
        (tmp_path / "pyproject.toml").write_text("[project]\nname='t'\n")
        (tmp_path / "uv.lock").write_text("lock")
        (tmp_path / ".github" / "workflows").mkdir(parents=True)
        (tmp_path / ".github" / "workflows" / "ci.yml").write_text("on: push")
        (tmp_path / "ruff.toml").write_text("[lint]\n")
        result = assess(str(tmp_path))
        assert result["score"] >= 80


class TestCheck:
    def test_returns_markdown(self, tmp_path, monkeypatch):
        monkeypatch.setattr("prism.engine.PRISM_DIR", tmp_path / "prism")
        monkeypatch.setattr("prism.engine.SNAPSHOTS_DIR", tmp_path / "prism" / "snapshots")
        monkeypatch.setattr("prism.engine.SESSIONS_DIR", tmp_path / "prism" / "sessions")
        monkeypatch.setattr("prism.engine.DAILY_DIR", tmp_path / "prism" / "daily")
        monkeypatch.setattr("prism.engine.HEALTH_DIR", tmp_path / "prism" / "health")

        result = check(str(tmp_path))
        assert "# Project Health" in result
        assert "Setup maturity:" in result

    def test_includes_score(self, tmp_path, monkeypatch):
        monkeypatch.setattr("prism.engine.PRISM_DIR", tmp_path / "prism")
        monkeypatch.setattr("prism.engine.SNAPSHOTS_DIR", tmp_path / "prism" / "snapshots")
        monkeypatch.setattr("prism.engine.SESSIONS_DIR", tmp_path / "prism" / "sessions")
        monkeypatch.setattr("prism.engine.DAILY_DIR", tmp_path / "prism" / "daily")
        monkeypatch.setattr("prism.engine.HEALTH_DIR", tmp_path / "prism" / "health")

        result = check(str(tmp_path))
        assert "/100" in result


class TestCacheIgnoreDetection:
    def test_no_caches_present(self, tmp_path):
        result = _detect_missing_cache_ignores(tmp_path)
        assert result["present"] == []
        assert result["missing"] == []

    def test_cache_present_not_ignored(self, tmp_path):
        (tmp_path / ".ruff_cache").mkdir()
        result = _detect_missing_cache_ignores(tmp_path)
        assert ".ruff_cache/" in result["missing"]

    def test_cache_present_and_ignored(self, tmp_path):
        (tmp_path / ".ruff_cache").mkdir()
        (tmp_path / ".gitignore").write_text(".ruff_cache/\n")
        result = _detect_missing_cache_ignores(tmp_path)
        assert ".ruff_cache/" in result["present"]
        assert ".ruff_cache/" not in result["missing"]

    def test_bare_name_match(self, tmp_path):
        """gitignore entry without trailing slash should also count as ignored."""
        (tmp_path / ".mypy_cache").mkdir()
        (tmp_path / ".gitignore").write_text(".mypy_cache\n")
        result = _detect_missing_cache_ignores(tmp_path)
        assert ".mypy_cache/" in result["present"]

    def test_ignores_comments(self, tmp_path):
        (tmp_path / ".ruff_cache").mkdir()
        (tmp_path / ".gitignore").write_text("# .ruff_cache/\n")
        result = _detect_missing_cache_ignores(tmp_path)
        assert ".ruff_cache/" in result["missing"]

    def test_ds_store_file_detected(self, tmp_path):
        (tmp_path / ".DS_Store").touch()
        result = _detect_missing_cache_ignores(tmp_path)
        assert ".DS_Store" in result["missing"]

    def test_assess_includes_cache_ignores(self, tmp_path):
        (tmp_path / ".pytest_cache").mkdir()
        result = assess(str(tmp_path))
        assert "cache_ignores" in result
        assert ".pytest_cache/" in result["cache_ignores"]["missing"]

    def test_redundant_entries_irrelevant(self, tmp_path):
        """Entries listed but absent from disk shouldn't count as 'missing'."""
        (tmp_path / ".gitignore").write_text(".ruff_cache/\n.mypy_cache/\n")
        result = _detect_missing_cache_ignores(tmp_path)
        assert result["missing"] == []


class TestComputeScore:
    """Direct exact-value tests for _compute_score — pin weight constants.

    Class name matches LintGate's test-to-function discovery heuristic so
    mutation profiling links these tests to _compute_score.
    """

    BASE_CHECKS_FULL = {
        "venv": {"found": True, "path": ".venv"},
        "lockfile": {"found": "uv.lock", "stale": False, "stale_reason": None},
        "git": {"initialized": True, "gitignore": True, "clean": True},
        "ci": {"found": True, "type": ".github/workflows"},
        "secrets": {"env_in_gitignore": True, "env_committed": False},
        "toolchain": {"ruff": True, "mypy": True},
    }
    BASE_CHECKS_EMPTY = {
        "venv": {"found": False, "path": None},
        "lockfile": {"found": None, "stale": False, "stale_reason": None},
        "git": {"initialized": False, "gitignore": False, "clean": False},
        "ci": {"found": False, "type": None},
        "secrets": {"env_in_gitignore": False, "env_committed": True},
        "toolchain": {},
    }

    def test_compute_score_solo_dev_full_setup_is_100(self):
        from prism.health import _compute_score

        assert _compute_score(self.BASE_CHECKS_FULL, profile="solo_dev") == 100

    def test_compute_score_production_full_setup_is_100(self):
        from prism.health import _compute_score

        assert _compute_score(self.BASE_CHECKS_FULL, profile="production") == 100

    def test_compute_score_shared_repo_full_setup_is_100(self):
        from prism.health import _compute_score

        assert _compute_score(self.BASE_CHECKS_FULL, profile="shared_repo") == 100

    def test_compute_score_empty_setup_minimum(self):
        """Empty setup still scores secrets_not_committed (no .env file present)."""
        from prism.health import _compute_score

        # Empty fixture has env_committed=True so even secrets_not_committed is 0
        assert _compute_score(self.BASE_CHECKS_EMPTY, profile="solo_dev") == 0

    def test_compute_score_only_venv_solo_dev(self):
        from prism.health import _compute_score

        c = dict(self.BASE_CHECKS_EMPTY, venv={"found": True, "path": ".venv"})
        # solo_dev venv weight = 15, total weights sum = 100 → 15
        assert _compute_score(c, profile="solo_dev") == 15

    def test_compute_score_only_venv_production(self):
        from prism.health import _compute_score

        c = dict(self.BASE_CHECKS_EMPTY, venv={"found": True, "path": ".venv"})
        # production venv weight = 5, total = 100 → 5
        assert _compute_score(c, profile="production") == 5

    def test_compute_score_only_ci_production(self):
        from prism.health import _compute_score

        c = dict(self.BASE_CHECKS_EMPTY, ci={"found": True, "type": ".github/workflows"})
        # production CI weight = 25 → 25
        assert _compute_score(c, profile="production") == 25

    def test_compute_score_only_ci_solo_dev(self):
        from prism.health import _compute_score

        c = dict(self.BASE_CHECKS_EMPTY, ci={"found": True, "type": ".github/workflows"})
        # solo_dev CI weight = 5 → 5
        assert _compute_score(c, profile="solo_dev") == 5

    def test_compute_score_stale_lockfile_yields_half_credit(self):
        from prism.health import _compute_score

        fresh = dict(
            self.BASE_CHECKS_EMPTY,
            lockfile={"found": "uv.lock", "stale": False, "stale_reason": None},
        )
        stale = dict(
            self.BASE_CHECKS_EMPTY,
            lockfile={"found": "uv.lock", "stale": True, "stale_reason": "x"},
        )
        # solo_dev lockfile weight = 10 → fresh 10, stale 5
        assert _compute_score(fresh, profile="solo_dev") == 10
        assert _compute_score(stale, profile="solo_dev") == 5

    def test_compute_score_toolchain_zero_one_two_tiers(self):
        from prism.health import _compute_score

        zero = dict(self.BASE_CHECKS_EMPTY, toolchain={"ruff": False, "mypy": False})
        one = dict(self.BASE_CHECKS_EMPTY, toolchain={"ruff": True, "mypy": False})
        two = dict(self.BASE_CHECKS_EMPTY, toolchain={"ruff": True, "mypy": True})
        # solo_dev toolchain = 15: 0 / 7 (15//2) / 15
        assert _compute_score(zero, profile="solo_dev") == 0
        assert _compute_score(one, profile="solo_dev") == 7
        assert _compute_score(two, profile="solo_dev") == 15

    def test_compute_score_git_components_split(self):
        """git_init / git_ignore / git_clean each contribute independently."""
        from prism.health import _compute_score

        only_init = dict(
            self.BASE_CHECKS_EMPTY,
            git={"initialized": True, "gitignore": False, "clean": False},
        )
        init_and_ignore = dict(
            self.BASE_CHECKS_EMPTY,
            git={"initialized": True, "gitignore": True, "clean": False},
        )
        all_three = dict(
            self.BASE_CHECKS_EMPTY,
            git={"initialized": True, "gitignore": True, "clean": True},
        )
        # solo_dev git weights: init=15, ignore=5, clean=10
        assert _compute_score(only_init, profile="solo_dev") == 15
        assert _compute_score(init_and_ignore, profile="solo_dev") == 20
        assert _compute_score(all_three, profile="solo_dev") == 30

    def test_compute_score_secrets_components_split(self):
        from prism.health import _compute_score

        # env_in_gitignore = True only
        ign_only = dict(
            self.BASE_CHECKS_EMPTY,
            secrets={"env_in_gitignore": True, "env_committed": True},
        )
        # not committed only (no ignore)
        not_committed_only = dict(
            self.BASE_CHECKS_EMPTY,
            secrets={"env_in_gitignore": False, "env_committed": False},
        )
        # both
        both = dict(
            self.BASE_CHECKS_EMPTY,
            secrets={"env_in_gitignore": True, "env_committed": False},
        )
        # solo_dev: secrets_ignore=15, secrets_not_committed=10
        assert _compute_score(ign_only, profile="solo_dev") == 15
        assert _compute_score(not_committed_only, profile="solo_dev") == 10
        assert _compute_score(both, profile="solo_dev") == 25

    def test_compute_score_unknown_profile_falls_back_to_solo_dev(self):
        from prism.health import _compute_score

        c = dict(self.BASE_CHECKS_EMPTY, venv={"found": True, "path": ".venv"})
        bogus = _compute_score(c, profile="bogus_profile")
        default = _compute_score(c, profile="solo_dev")
        assert bogus == default

    def test_compute_score_env_var_selects_profile(self, monkeypatch):
        from prism.health import _compute_score

        monkeypatch.setenv("PRISM_WEIGHT_PROFILE", "production")
        c = dict(self.BASE_CHECKS_EMPTY, venv={"found": True, "path": ".venv"})
        # profile=None → falls through to env var → production
        assert _compute_score(c, profile=None) == 5

    def test_compute_score_explicit_profile_beats_env_var(self, monkeypatch):
        from prism.health import _compute_score

        monkeypatch.setenv("PRISM_WEIGHT_PROFILE", "production")
        c = dict(self.BASE_CHECKS_EMPTY, venv={"found": True, "path": ".venv"})
        # Explicit solo_dev should override env var
        assert _compute_score(c, profile="solo_dev") == 15


class TestLockfileResolverHash:
    PYPROJECT_BASE = (
        "[project]\n"
        "name = 'demo'\n"
        "version = '0.1.0'\n"
        "dependencies = ['requests', 'click']\n"
        "\n"
        "[tool.ruff]\n"
        "line-length = 100\n"
    )

    def test_extract_resolver_inputs_includes_dependencies(self):
        out = _extract_resolver_inputs(self.PYPROJECT_BASE)
        assert "requests" in out
        assert "click" in out

    def test_extract_resolver_inputs_excludes_ruff(self):
        out = _extract_resolver_inputs(self.PYPROJECT_BASE)
        assert "line-length" not in out
        assert "[tool.ruff]" not in out

    def test_resolver_hash_stable_across_cosmetic_changes(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text(self.PYPROJECT_BASE)
        h1 = _resolver_hash(tmp_path, "pyproject.toml")
        # Cosmetic change to non-resolver section
        (tmp_path / "pyproject.toml").write_text(
            self.PYPROJECT_BASE.replace("line-length = 100", "line-length = 120")
        )
        h2 = _resolver_hash(tmp_path, "pyproject.toml")
        assert h1 == h2
        assert h1 is not None

    def test_resolver_hash_changes_when_dep_added(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text(self.PYPROJECT_BASE)
        h1 = _resolver_hash(tmp_path, "pyproject.toml")
        (tmp_path / "pyproject.toml").write_text(
            self.PYPROJECT_BASE.replace("'click'", "'click', 'rich'")
        )
        h2 = _resolver_hash(tmp_path, "pyproject.toml")
        assert h1 != h2

    def test_lockfile_fresh_records_hash(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text(self.PYPROJECT_BASE)
        lock = tmp_path / "uv.lock"
        lock.write_text("lock\n")
        # Make lock newer than manifest
        import os
        import time

        old = time.time() - 10
        os.utime(tmp_path / "pyproject.toml", (old, old))
        result = _detect_lockfile(tmp_path)
        assert result["found"] == "uv.lock"
        assert result["stale"] is False
        # Sidecar should now exist
        assert (tmp_path / ".prism" / ".prism_lockfile_hash").is_file()

    def test_lockfile_mtime_stale_but_hash_unchanged(self, tmp_path):
        """Cosmetic pyproject edit after lock should NOT report stale."""
        import os
        import time

        (tmp_path / "pyproject.toml").write_text(self.PYPROJECT_BASE)
        lock = tmp_path / "uv.lock"
        lock.write_text("lock\n")
        # First run: lock fresh — records hash
        old = time.time() - 100
        os.utime(tmp_path / "pyproject.toml", (old, old))
        first = _detect_lockfile(tmp_path)
        assert first["stale"] is False

        # Cosmetic edit: bump pyproject mtime, only change ruff section
        (tmp_path / "pyproject.toml").write_text(
            self.PYPROJECT_BASE.replace("line-length = 100", "line-length = 120")
        )
        # Force pyproject newer than lock
        future = time.time() + 100
        os.utime(tmp_path / "pyproject.toml", (future, future))
        second = _detect_lockfile(tmp_path)
        assert second["stale"] is False
        assert second["stale_reason"] == "mtime_only"

    def test_sqlite_disabled_by_default(self, tmp_path, monkeypatch):
        from prism.health import _detect_sqlite_hygiene

        monkeypatch.delenv("PRISM_ENABLE_SQLITE_CHECK", raising=False)
        result = _detect_sqlite_hygiene(tmp_path)
        assert result["enabled"] is False
        assert result["warnings"] == []

    def test_sqlite_enabled_no_db(self, tmp_path, monkeypatch):
        from prism.health import _detect_sqlite_hygiene

        monkeypatch.setenv("PRISM_ENABLE_SQLITE_CHECK", "1")
        result = _detect_sqlite_hygiene(tmp_path)
        assert result["enabled"] is True
        assert result["warnings"] == []

    def test_sqlite_warns_when_fresh_db_and_dirty_schema(self, tmp_path, monkeypatch):
        import subprocess as sp

        from prism.health import _detect_sqlite_hygiene

        monkeypatch.setenv("PRISM_ENABLE_SQLITE_CHECK", "1")
        sp.run(["git", "init"], cwd=tmp_path, capture_output=True, check=True)
        sp.run(["git", "config", "user.email", "t@t"], cwd=tmp_path, capture_output=True)
        sp.run(["git", "config", "user.name", "t"], cwd=tmp_path, capture_output=True)
        # Create an initial commit so schema.sql edits show as modified
        (tmp_path / "schema.sql").write_text("CREATE TABLE t (id INT);\n")
        sp.run(["git", "add", "schema.sql"], cwd=tmp_path, capture_output=True, check=True)
        sp.run(
            ["git", "commit", "-m", "init"],
            cwd=tmp_path,
            capture_output=True,
            check=True,
        )
        # Modify schema (dirty) and create a fresh DB
        (tmp_path / "schema.sql").write_text("CREATE TABLE t (id INT, name TEXT);\n")
        (tmp_path / "data.sqlite").write_bytes(b"\x00")
        result = _detect_sqlite_hygiene(tmp_path)
        assert any(w["db"] == "data.sqlite" for w in result["warnings"])

    def test_sqlite_clean_schema_no_warning(self, tmp_path, monkeypatch):
        import subprocess as sp

        from prism.health import _detect_sqlite_hygiene

        monkeypatch.setenv("PRISM_ENABLE_SQLITE_CHECK", "1")
        sp.run(["git", "init"], cwd=tmp_path, capture_output=True, check=True)
        sp.run(["git", "config", "user.email", "t@t"], cwd=tmp_path, capture_output=True)
        sp.run(["git", "config", "user.name", "t"], cwd=tmp_path, capture_output=True)
        (tmp_path / "data.sqlite").write_bytes(b"\x00")
        # No dirty schema → no warning even with fresh DB
        result = _detect_sqlite_hygiene(tmp_path)
        assert result["warnings"] == []

    def test_remote_state_no_repo(self, tmp_path):
        from prism.health import _detect_remote_state

        result = _detect_remote_state(tmp_path)
        assert result["origin_url"] is None
        assert result["remote_name_collision"] is None

    def test_remote_state_with_origin_skips_gh(self, tmp_path, monkeypatch):
        """When origin exists, gh probe should be skipped (no collision check)."""
        import subprocess as sp

        from prism.health import _detect_remote_state

        sp.run(["git", "init"], cwd=tmp_path, capture_output=True, check=True)
        sp.run(
            ["git", "remote", "add", "origin", "https://example.com/foo.git"],
            cwd=tmp_path,
            capture_output=True,
            check=True,
        )
        result = _detect_remote_state(tmp_path)
        assert result["origin_url"] == "https://example.com/foo.git"
        assert result["remote_name_collision"] is None

    def test_remote_state_no_gh_returns_clean(self, tmp_path, monkeypatch):
        """Without gh installed, return cleanly without crashing."""
        import subprocess as sp

        from prism.health import _detect_remote_state

        sp.run(["git", "init"], cwd=tmp_path, capture_output=True, check=True)
        # Force shutil.which("gh") to return None
        monkeypatch.setattr("prism.health.shutil.which", lambda _name: None)
        result = _detect_remote_state(tmp_path)
        assert result["gh_available"] is False
        assert result["remote_name_collision"] is None

    def test_large_staged_files_no_repo(self, tmp_path):
        """Without a git repo, the check should report not-checked, not crash."""
        result = _detect_large_staged_files(tmp_path)
        assert result["checked"] is False
        assert result["files"] == []

    def test_large_staged_files_detects_oversized(self, tmp_path):
        import subprocess as sp

        sp.run(["git", "init"], cwd=tmp_path, capture_output=True, check=True)
        sp.run(
            ["git", "config", "user.email", "test@test"], cwd=tmp_path, capture_output=True
        )
        sp.run(["git", "config", "user.name", "test"], cwd=tmp_path, capture_output=True)
        big = tmp_path / "weights.bin"
        big.write_bytes(b"x" * (2 * 1024 * 1024))
        sp.run(["git", "add", "weights.bin"], cwd=tmp_path, capture_output=True, check=True)
        result = _detect_large_staged_files(tmp_path, threshold_bytes=1024 * 1024)
        assert result["checked"] is True
        paths = [f["path"] for f in result["files"]]
        assert "weights.bin" in paths

    def test_large_staged_files_under_threshold_ignored(self, tmp_path):
        import subprocess as sp

        sp.run(["git", "init"], cwd=tmp_path, capture_output=True, check=True)
        sp.run(
            ["git", "config", "user.email", "test@test"], cwd=tmp_path, capture_output=True
        )
        sp.run(["git", "config", "user.name", "test"], cwd=tmp_path, capture_output=True)
        small = tmp_path / "small.txt"
        small.write_text("hi")
        sp.run(["git", "add", "small.txt"], cwd=tmp_path, capture_output=True, check=True)
        result = _detect_large_staged_files(tmp_path, threshold_bytes=1024)
        assert result["checked"] is True
        assert result["files"] == []

    def test_weight_profiles_differ_on_same_checks(self, tmp_path):
        """Same project state, different profile, different score."""
        from prism.health import _compute_score

        checks = {
            "venv": {"found": True, "path": ".venv"},
            "lockfile": {"found": "uv.lock", "stale": False, "stale_reason": None},
            "git": {"initialized": True, "gitignore": True, "clean": True},
            "ci": {"found": False, "type": None},  # CI MISSING
            "secrets": {"env_in_gitignore": True, "env_committed": False},
            "toolchain": {"ruff": True, "mypy": True},
        }
        solo = _compute_score(checks, profile="solo_dev")
        prod = _compute_score(checks, profile="production")
        # Without CI, production should score lower than solo_dev (CI weight differs)
        assert solo > prod
        assert solo >= 90  # solo_dev only docks 5 for missing CI
        assert prod <= 80  # production docks 25

    def test_unknown_profile_falls_back_to_default(self, tmp_path):
        from prism.health import _compute_score

        checks = {
            "venv": {"found": True, "path": ".venv"},
            "lockfile": {"found": "uv.lock", "stale": False, "stale_reason": None},
            "git": {"initialized": True, "gitignore": True, "clean": True},
            "ci": {"found": True, "type": ".github/workflows"},
            "secrets": {"env_in_gitignore": True, "env_committed": False},
            "toolchain": {"ruff": True},
        }
        bogus = _compute_score(checks, profile="not_a_real_profile")
        default = _compute_score(checks, profile="solo_dev")
        assert bogus == default

    def test_assess_records_profile(self, tmp_path):
        result = assess(str(tmp_path), profile="production")
        assert result["profile"] == "production"

    def test_env_var_selects_profile(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PRISM_WEIGHT_PROFILE", "shared_repo")
        result = assess(str(tmp_path))
        assert result["profile"] == "shared_repo"

    def test_lockfile_truly_stale_when_dep_changes(self, tmp_path):
        import os
        import time

        (tmp_path / "pyproject.toml").write_text(self.PYPROJECT_BASE)
        lock = tmp_path / "uv.lock"
        lock.write_text("lock\n")
        # Bootstrap hash
        old = time.time() - 100
        os.utime(tmp_path / "pyproject.toml", (old, old))
        _detect_lockfile(tmp_path)

        # Real dependency change
        (tmp_path / "pyproject.toml").write_text(
            self.PYPROJECT_BASE.replace("'click'", "'click', 'rich'")
        )
        future = time.time() + 100
        os.utime(tmp_path / "pyproject.toml", (future, future))
        result = _detect_lockfile(tmp_path)
        assert result["stale"] is True
        assert result["stale_reason"] == "resolver_input_changed"

