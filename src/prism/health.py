"""Project Health Lens.

Setup maturity scoring: venv, lockfile, git, CI, toolchain, secrets hygiene.
Writes state to ~/.claude/prism/health/{project_hash}.json for LintGate
consumption via its controlplane and getting_started channels.
"""

import hashlib
import os
import subprocess
from pathlib import Path

from . import engine


def _project_hash(path: str) -> str:
    return hashlib.sha256(path.encode()).hexdigest()[:12]


def _file_exists(root: Path, *candidates: str) -> str | None:
    """Return the first matching filename, or None."""
    for c in candidates:
        if (root / c).exists():
            return c
    return None


def _detect_venv(root: Path) -> dict:
    """Check for virtual environment presence and activation."""
    venv_dirs = (".venv", "venv", "env", ".env")
    for d in venv_dirs:
        venv_path = root / d
        if venv_path.is_dir() and (venv_path / "bin" / "python").exists():
            return {"found": True, "path": d}
    # Conda?
    if os.environ.get("CONDA_PREFIX"):
        return {"found": True, "path": "conda"}
    return {"found": False, "path": None}


def _detect_lockfile(root: Path) -> dict:
    """Check for lockfile presence and freshness relative to manifest."""
    lock_manifest_pairs = [
        ("uv.lock", "pyproject.toml"),
        ("poetry.lock", "pyproject.toml"),
        ("Pipfile.lock", "Pipfile"),
        ("requirements.txt", "pyproject.toml"),
        ("package-lock.json", "package.json"),
        ("yarn.lock", "package.json"),
        ("pnpm-lock.yaml", "package.json"),
        ("Cargo.lock", "Cargo.toml"),
        ("go.sum", "go.mod"),
    ]
    for lockfile, manifest in lock_manifest_pairs:
        lock_path = root / lockfile
        manifest_path = root / manifest
        if lock_path.is_file():
            stale = False
            if manifest_path.is_file():
                stale = lock_path.stat().st_mtime < manifest_path.stat().st_mtime
            return {"found": lockfile, "stale": stale}
    return {"found": None, "stale": False}


def _detect_git(root: Path) -> dict:
    """Check git init, .gitignore, and clean working tree."""
    git_dir = root / ".git"
    if not git_dir.exists():
        return {"initialized": False, "gitignore": False, "clean": False}
    gitignore = (root / ".gitignore").is_file()
    clean = False
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=5,
        )
        clean = result.returncode == 0 and not result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return {"initialized": True, "gitignore": gitignore, "clean": clean}


def _detect_ci(root: Path) -> dict:
    """Check for CI configuration."""
    ci_paths = [
        ".github/workflows",
        ".gitlab-ci.yml",
        ".circleci/config.yml",
        "Jenkinsfile",
        ".travis.yml",
    ]
    for p in ci_paths:
        target = root / p
        if target.exists():
            return {"found": True, "type": p}
    return {"found": False, "type": None}


def _detect_secrets_hygiene(root: Path) -> dict:
    """Check for .env in .gitignore, no committed secrets."""
    gitignore_path = root / ".gitignore"
    env_ignored = False
    if gitignore_path.is_file():
        content = gitignore_path.read_text()
        env_ignored = ".env" in content
    env_committed = (root / ".env").is_file() and not env_ignored
    return {"env_in_gitignore": env_ignored, "env_committed": env_committed}


def _detect_toolchain(root: Path) -> dict:
    """Check for linter/formatter configuration."""
    tools = {
        "ruff": _file_exists(root, "ruff.toml", ".ruff.toml")
        or _has_pyproject_section(root, "ruff"),
        "mypy": _file_exists(root, "mypy.ini", ".mypy.ini") or _has_pyproject_section(root, "mypy"),
        "prettier": _file_exists(root, ".prettierrc", ".prettierrc.json", ".prettierrc.yml"),
        "eslint": _file_exists(
            root, ".eslintrc", ".eslintrc.json", ".eslintrc.yml", "eslint.config.js"
        ),
        "lintgate": _file_exists(root, ".claude/lintgate.yaml", "lintgate.yaml"),
    }
    return {k: bool(v) for k, v in tools.items()}


def _has_pyproject_section(root: Path, tool: str) -> bool:
    pyproject = root / "pyproject.toml"
    if not pyproject.is_file():
        return False
    try:
        return f"[tool.{tool}" in pyproject.read_text()
    except OSError:
        return False


def _compute_score(checks: dict) -> int:
    """Compute setup maturity as 0-100 score."""
    points = 0
    total = 0

    # Venv (15 points)
    total += 15
    if checks["venv"]["found"]:
        points += 15

    # Lockfile (15 points)
    total += 15
    if checks["lockfile"]["found"]:
        points += 10 if checks["lockfile"]["stale"] else 15

    # Git (20 points)
    total += 20
    if checks["git"]["initialized"]:
        points += 10
    if checks["git"]["gitignore"]:
        points += 5
    if checks["git"]["clean"]:
        points += 5

    # CI (15 points)
    total += 15
    if checks["ci"]["found"]:
        points += 15

    # Secrets (15 points)
    total += 15
    if checks["secrets"]["env_in_gitignore"]:
        points += 10
    if not checks["secrets"]["env_committed"]:
        points += 5

    # Toolchain (20 points — any tool configured)
    total += 20
    configured = sum(1 for v in checks["toolchain"].values() if v)
    if configured >= 2:
        points += 20
    elif configured == 1:
        points += 10

    return round(points / total * 100)


def assess(project_path: str) -> dict:
    """Run all health checks and compute maturity score."""
    root = Path(project_path)
    checks = {
        "venv": _detect_venv(root),
        "lockfile": _detect_lockfile(root),
        "git": _detect_git(root),
        "ci": _detect_ci(root),
        "secrets": _detect_secrets_hygiene(root),
        "toolchain": _detect_toolchain(root),
    }
    score = _compute_score(checks)
    return {"score": score, **checks}


def check(project_path: str) -> str:
    """MCP tool entry point. Returns compact summary + persists for LintGate."""
    root = Path(project_path)
    if not root.is_dir():
        return f"Not a directory: {project_path}"

    checks = assess(project_path)
    score = checks["score"]
    phash = _project_hash(project_path)

    # Persist for LintGate
    engine.write_health(
        phash,
        {
            "project": project_path,
            "score": score,
            "checks": checks,
        },
    )

    # Compact summary
    lines = [f"# Project Health — {root.name}", ""]
    lines.append(f"**Setup maturity: {score}/100**")
    lines.append("")

    status_map = {
        "venv": ("Venv", checks["venv"]["found"], checks["venv"].get("path", "")),
        "lockfile": (
            "Lockfile",
            bool(checks["lockfile"]["found"]),
            f"{checks['lockfile']['found'] or 'none'}"
            + (" (STALE)" if checks["lockfile"]["stale"] else ""),
        ),
        "git": (
            "Git",
            checks["git"]["initialized"],
            ("clean" if checks["git"]["clean"] else "dirty")
            + (", .gitignore" if checks["git"]["gitignore"] else ""),
        ),
        "ci": ("CI", checks["ci"]["found"], checks["ci"]["type"] or "none"),
        "secrets": (
            "Secrets",
            checks["secrets"]["env_in_gitignore"] and not checks["secrets"]["env_committed"],
            ".env ignored" if checks["secrets"]["env_in_gitignore"] else "unconfigured",
        ),
    }

    for _key, (label, ok, detail) in status_map.items():
        icon = "ok" if ok else "MISSING"
        lines.append(f"- {label}: {icon} ({detail})")

    tools = [t for t, v in checks["toolchain"].items() if v]
    lines.append(f"- Toolchain: {', '.join(tools) if tools else 'none configured'}")

    summary = "\n".join(lines)
    aid = engine.save_snapshot("health", summary, {"project": project_path, **checks})
    lines.append("")
    lines.append(f'_Details: prism_details("{aid}")_')

    return "\n".join(lines)
