"""Tests for prism.health — direct test file to satisfy test channel mapping.

health.py already has 100% mutation kill rate (tested transitively via
test_recommend.py), but LintGate's test channel wants a direct test file.
"""

from prism.health import assess, check


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
