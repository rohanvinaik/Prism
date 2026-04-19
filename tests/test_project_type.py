"""Tests for project_type fingerprint and type-specific recommendations."""

from prism.project_type import detect, type_specific_recommendations


class TestDetect:
    def test_unknown_when_no_manifest(self, tmp_path):
        result = detect(str(tmp_path))
        assert result["type"] == "unknown"

    def test_library_when_project_only(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text(
            "[project]\nname = 'libfoo'\nversion = '0.1.0'\n"
        )
        result = detect(str(tmp_path))
        assert result["type"] == "library"

    def test_cli_when_scripts_present(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text(
            "[project]\nname = 'cli'\n\n[project.scripts]\nfoo = 'foo:main'\n"
        )
        result = detect(str(tmp_path))
        assert result["type"] == "cli"

    def test_mcp_via_dependency(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text(
            "[project]\nname = 'srv'\ndependencies = ['mcp', 'click']\n"
        )
        result = detect(str(tmp_path))
        assert result["type"] == "mcp_server"

    def test_mcp_via_fastmcp(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text(
            "[project]\nname = 'srv'\ndependencies = ['fastmcp>=0.3']\n"
        )
        result = detect(str(tmp_path))
        assert result["type"] == "mcp_server"

    def test_mcp_via_source_import(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text(
            "[project]\nname = 'srv'\ndependencies = ['something_else']\n"
        )
        src = tmp_path / "src" / "srv"
        src.mkdir(parents=True)
        (src / "server.py").write_text("from mcp.server.fastmcp import FastMCP\n")
        result = detect(str(tmp_path))
        assert result["type"] == "mcp_server"

    def test_cli_beats_library_when_both_signals(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text(
            "[project]\nname = 'tool'\n\n[project.scripts]\ntool = 'tool:main'\n"
        )
        result = detect(str(tmp_path))
        assert result["type"] == "cli"


class TestTypeSpecificRecommendations:
    def test_mcp_recs_suggest_claude_md(self, tmp_path):
        recs = type_specific_recommendations("mcp_server", tmp_path)
        titles = [r["title"] for r in recs]
        assert any("CLAUDE.md" in t for t in titles)

    def test_mcp_recs_skip_claude_md_when_present(self, tmp_path):
        (tmp_path / "CLAUDE.md").write_text("# notes\n")
        recs = type_specific_recommendations("mcp_server", tmp_path)
        titles = [r["title"] for r in recs]
        assert not any("Add CLAUDE.md" in t for t in titles)

    def test_mcp_recs_skip_claude_md_when_in_dot_claude(self, tmp_path):
        (tmp_path / ".claude").mkdir()
        (tmp_path / ".claude" / "CLAUDE.md").write_text("# notes\n")
        recs = type_specific_recommendations("mcp_server", tmp_path)
        titles = [r["title"] for r in recs]
        assert not any("Add CLAUDE.md" in t for t in titles)

    def test_cli_recs_suggest_readme(self, tmp_path):
        recs = type_specific_recommendations("cli", tmp_path)
        titles = [r["title"] for r in recs]
        assert any("README" in t for t in titles)

    def test_library_no_extra_recs(self, tmp_path):
        recs = type_specific_recommendations("library", tmp_path)
        # Library currently emits no type-specific extras
        assert recs == []

    def test_unknown_no_recs(self, tmp_path):
        recs = type_specific_recommendations("unknown", tmp_path)
        assert recs == []
