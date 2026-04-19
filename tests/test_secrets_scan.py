"""Tests for prism.secrets_scan — wrapper around gitleaks with regex fallback."""

import subprocess as sp

from prism.secrets_scan import _scan_with_fallback, scan

# Build credential-shaped fixtures at runtime so the literal source code
# doesn't trip our own (or anyone else's) secrets scanner. Each prefix is
# split + concatenated; the resulting string still matches the regex.
_FAKE_ANTHROPIC = "sk-" + "ant-" + "abcdefghijk1234567890XYZ"
_FAKE_GITHUB = "g" + "hp_" + "abcdefghijklmnopqrstuvwxyz0123456789"
_FAKE_AWS = "AK" + "IA" + "IOSFODNN7EXAMPLE"


def _init_git(tmp_path):
    sp.run(["git", "init"], cwd=tmp_path, capture_output=True, check=True)
    sp.run(["git", "config", "user.email", "t@t"], cwd=tmp_path, capture_output=True)
    sp.run(["git", "config", "user.name", "t"], cwd=tmp_path, capture_output=True)


class TestFallbackScan:
    def test_no_findings_in_clean_file(self, tmp_path):
        _init_git(tmp_path)
        (tmp_path / "clean.py").write_text("print('hello')\n")
        sp.run(["git", "add", "clean.py"], cwd=tmp_path, capture_output=True, check=True)
        result = _scan_with_fallback(tmp_path)
        assert result["findings"] == []

    def test_finds_anthropic_key(self, tmp_path):
        _init_git(tmp_path)
        (tmp_path / "leak.txt").write_text(f"token = '{_FAKE_ANTHROPIC}'\n")
        sp.run(["git", "add", "leak.txt"], cwd=tmp_path, capture_output=True, check=True)
        result = _scan_with_fallback(tmp_path)
        assert len(result["findings"]) == 1
        assert result["findings"][0]["rule"] == "anthropic_api_key"
        assert result["findings"][0]["file"] == "leak.txt"

    def test_finds_aws_key(self, tmp_path):
        _init_git(tmp_path)
        (tmp_path / "creds").write_text(f"{_FAKE_AWS}\n")
        sp.run(["git", "add", "creds"], cwd=tmp_path, capture_output=True, check=True)
        result = _scan_with_fallback(tmp_path)
        rules = [f["rule"] for f in result["findings"]]
        assert "aws_access_key_id" in rules

    def test_finds_github_token(self, tmp_path):
        _init_git(tmp_path)
        (tmp_path / "envfile").write_text(f"GH={_FAKE_GITHUB}\n")
        sp.run(["git", "add", "envfile"], cwd=tmp_path, capture_output=True, check=True)
        result = _scan_with_fallback(tmp_path)
        rules = [f["rule"] for f in result["findings"]]
        assert "github_personal_token" in rules

    def test_unstaged_file_ignored(self, tmp_path):
        """Files not in the staging index should not be scanned."""
        _init_git(tmp_path)
        (tmp_path / "secret.txt").write_text(f"{_FAKE_ANTHROPIC}\n")
        # Don't git add — file unstaged
        result = _scan_with_fallback(tmp_path)
        assert result["findings"] == []

    def test_finding_includes_line_number(self, tmp_path):
        _init_git(tmp_path)
        (tmp_path / "f").write_text(f"line1\nline2\n{_FAKE_ANTHROPIC}\n")
        sp.run(["git", "add", "f"], cwd=tmp_path, capture_output=True, check=True)
        result = _scan_with_fallback(tmp_path)
        assert result["findings"][0]["line"] == 3


class TestScanDispatch:
    def test_dispatch_returns_a_backend(self, tmp_path):
        _init_git(tmp_path)
        result = scan(str(tmp_path))
        assert result["backend"] in {"gitleaks", "fallback"}
        assert "findings" in result
