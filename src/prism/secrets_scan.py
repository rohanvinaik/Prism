"""Secrets scan wrapper — defers to gitleaks when available, falls back to grep.

Don't reimplement gitleaks — wrap it. Adds value by surfacing results in
Prism's structured format and integrating with prism_health and prism_pr_ready,
so a single Prism call can report secrets alongside other channels.

The fallback regex set is intentionally conservative: high-precision prefixes
that almost never appear outside actual credentials. Avoids false positives
on documentation/test fixtures.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

# High-precision credential prefixes. Each pattern is anchored so we match
# what looks like an actual token, not the prefix in prose.
FALLBACK_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("openai_api_key", re.compile(r"\bsk-[A-Za-z0-9]{20,}\b")),
    ("anthropic_api_key", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}\b")),
    ("github_personal_token", re.compile(r"\bghp_[A-Za-z0-9]{36}\b")),
    ("github_oauth_token", re.compile(r"\bgho_[A-Za-z0-9]{36}\b")),
    ("github_user_token", re.compile(r"\bghu_[A-Za-z0-9]{36}\b")),
    ("github_server_token", re.compile(r"\bghs_[A-Za-z0-9]{36}\b")),
    ("github_refresh_token", re.compile(r"\bghr_[A-Za-z0-9]{36}\b")),
    ("aws_access_key_id", re.compile(r"\b(AKIA|ASIA)[A-Z0-9]{16}\b")),
    ("slack_token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
)


def _staged_files(root: Path) -> list[Path]:
    if not (root / ".git").exists():
        return []
    try:
        proc = subprocess.run(
            ["git", "diff", "--cached", "--name-only", "--diff-filter=AM"],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return []
    if proc.returncode != 0:
        return []
    out = []
    for line in proc.stdout.splitlines():
        rel = line.strip()
        if not rel:
            continue
        full = root / rel
        if full.is_file():
            out.append(full)
    return out


def _scan_with_gitleaks(root: Path) -> dict:
    """Run `gitleaks protect --staged --no-banner --report-format json -r -`.

    Returns a structured dict matching the fallback shape so callers don't
    branch on which scanner ran.
    """
    try:
        proc = subprocess.run(
            [
                "gitleaks",
                "protect",
                "--staged",
                "--no-banner",
                "--report-format",
                "json",
                "--report-path",
                "/dev/stdout",
            ],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return {"backend": "gitleaks", "available": True, "findings": [], "error": "timeout"}

    findings: list[dict] = []
    # gitleaks exits non-zero when leaks found. Output JSON is on stdout
    # via /dev/stdout; tolerate either single-object or array form.
    raw = proc.stdout.strip()
    if raw:
        import json

        try:
            parsed = json.loads(raw)
            items = parsed if isinstance(parsed, list) else [parsed]
            for item in items:
                if not isinstance(item, dict):
                    continue
                findings.append(
                    {
                        "rule": item.get("RuleID") or item.get("Description", "unknown"),
                        "file": item.get("File", ""),
                        "line": item.get("StartLine"),
                        "match": item.get("Match", "")[:80],
                    }
                )
        except (json.JSONDecodeError, ValueError):
            pass
    return {"backend": "gitleaks", "available": True, "findings": findings}


def _scan_with_fallback(root: Path) -> dict:
    """Plain regex scan over staged files. Used when gitleaks is absent."""
    findings: list[dict] = []
    for path in _staged_files(root):
        try:
            text = path.read_text(errors="ignore")
        except OSError:
            continue
        for rule, pattern in FALLBACK_PATTERNS:
            for match in pattern.finditer(text):
                start = match.start()
                # Compute line number once per hit; cheaper than splitlines per pattern.
                line_no = text.count("\n", 0, start) + 1
                findings.append(
                    {
                        "rule": rule,
                        "file": str(path.relative_to(root)),
                        "line": line_no,
                        "match": match.group(0)[:8] + "...",
                    }
                )
    return {"backend": "fallback", "available": False, "findings": findings}


def scan(project_path: str) -> dict:
    """Run the best-available secrets scan over staged files."""
    root = Path(project_path)
    if shutil.which("gitleaks"):
        return _scan_with_gitleaks(root)
    return _scan_with_fallback(root)
