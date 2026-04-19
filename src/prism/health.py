"""Project Health Lens.

Setup maturity scoring: venv, lockfile, git, CI, toolchain, secrets hygiene.
Writes state to ~/.claude/prism/health/{project_hash}.json for LintGate
consumption via its controlplane and getting_started channels.
"""

import hashlib
import os
import shutil
import subprocess
import time
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


# Sections of pyproject.toml that influence dependency resolution.
# Touching any other section (e.g. [tool.ruff], [project.urls]) won't make
# the lockfile genuinely stale even though it bumps the mtime.
RESOLVER_RELEVANT_SECTIONS: tuple[str, ...] = (
    "[project]",
    "[project.dependencies]",
    "[project.optional-dependencies]",
    "[build-system]",
    "[tool.uv]",
    "[tool.uv.sources]",
    "[tool.poetry]",
    "[tool.poetry.dependencies]",
    "[tool.poetry.group",  # poetry group prefix
)

LOCKFILE_HASH_SIDECAR = ".prism_lockfile_hash"


def _extract_resolver_inputs(pyproject_text: str) -> str:
    """Concatenate only the resolver-relevant sections of pyproject.

    Used to detect whether a pyproject change actually altered dependency
    inputs versus a cosmetic edit (e.g. ruff config tweak).
    """
    lines = pyproject_text.splitlines()
    in_section = False
    keep: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("["):
            in_section = any(stripped.startswith(s) for s in RESOLVER_RELEVANT_SECTIONS)
        # Skip blank lines for stability across whitespace edits
        if in_section and stripped:
            keep.append(stripped)
    return "\n".join(keep)


def _resolver_hash(root: Path, manifest_name: str) -> str | None:
    """Hash the resolver-relevant subset of the manifest. Returns None if N/A."""
    if manifest_name != "pyproject.toml":
        # Hash strategy currently only defined for pyproject; other manifests
        # fall back to mtime-only detection.
        return None
    manifest_path = root / manifest_name
    if not manifest_path.is_file():
        return None
    try:
        text = manifest_path.read_text()
    except OSError:
        return None
    inputs = _extract_resolver_inputs(text)
    return hashlib.sha256(inputs.encode()).hexdigest()[:16]


def _read_stored_hash(root: Path) -> str | None:
    sidecar = root / ".prism" / LOCKFILE_HASH_SIDECAR
    if not sidecar.is_file():
        return None
    try:
        return sidecar.read_text().strip() or None
    except OSError:
        return None


def _write_stored_hash(root: Path, value: str) -> None:
    prism_dir = root / ".prism"
    try:
        prism_dir.mkdir(exist_ok=True)
        (prism_dir / LOCKFILE_HASH_SIDECAR).write_text(value)
    except OSError:
        pass


def _detect_lockfile(root: Path) -> dict:
    """Check for lockfile presence and freshness relative to manifest.

    Staleness logic:
      1. Pure mtime comparison gives a baseline answer.
      2. For pyproject-based projects, a resolver-input hash refines it:
         if pyproject was touched but the resolver-relevant sections are
         unchanged, the lockfile is NOT stale (mtime false positive).
      3. When mtime says fresh, we record the current resolver hash so
         future stale detections have a baseline to compare against.
    """
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
        if not lock_path.is_file():
            continue
        result: dict = {"found": lockfile, "stale": False, "stale_reason": None}
        if not manifest_path.is_file():
            return result
        mtime_stale = lock_path.stat().st_mtime < manifest_path.stat().st_mtime
        current_hash = _resolver_hash(root, manifest)
        if not mtime_stale:
            # Lockfile fresh by mtime — record resolver hash for future runs
            if current_hash:
                _write_stored_hash(root, current_hash)
            return result
        # mtime says stale: check whether resolver inputs actually changed
        stored_hash = _read_stored_hash(root)
        if current_hash and stored_hash and current_hash == stored_hash:
            # Resolver inputs unchanged — pyproject was touched cosmetically
            result["stale_reason"] = "mtime_only"
            return result
        result["stale"] = True
        result["stale_reason"] = "resolver_input_changed" if stored_hash else "mtime_only"
        return result
    return {"found": None, "stale": False, "stale_reason": None}


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


def _detect_remote_state(root: Path) -> dict:
    """Probe git origin and (if gh installed) check for name collision on user's account.

    Used to warn before `gh repo create` that something with the same name
    already exists locally or remotely. Degrades gracefully when gh missing.

    Returns:
        {
          "origin_url": str | None,
          "gh_available": bool,
          "remote_name_collision": str | None,  # full repo name when collision detected
        }
    """
    info: dict = {
        "origin_url": None,
        "gh_available": False,
        "remote_name_collision": None,
    }
    if not (root / ".git").exists():
        return info
    try:
        r = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=3,
        )
        if r.returncode == 0 and r.stdout.strip():
            info["origin_url"] = r.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    if not shutil.which("gh"):
        return info
    info["gh_available"] = True

    # Only probe remote when no origin is set (otherwise origin tells us
    # whatever we need). Use the project directory name as the would-be repo.
    if info["origin_url"]:
        return info
    candidate_name = root.name
    try:
        owner_proc = subprocess.run(
            ["gh", "api", "user", "--jq", ".login"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if owner_proc.returncode != 0:
            return info
        owner = owner_proc.stdout.strip()
        if not owner:
            return info
        full_name = f"{owner}/{candidate_name}"
        view_proc = subprocess.run(
            ["gh", "repo", "view", full_name, "--json", "name"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if view_proc.returncode == 0:
            info["remote_name_collision"] = full_name
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return info


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


DEFAULT_LARGE_FILE_BYTES = 10 * 1024 * 1024  # 10 MB


def _detect_large_staged_files(root: Path, threshold_bytes: int = DEFAULT_LARGE_FILE_BYTES) -> dict:
    """Flag staged files (added or modified) that exceed the size threshold.

    Catches DBs, model weights, vendor bundles, anything that would bloat
    the repo and probably belongs in .gitignore or git-lfs.
    """
    if not (root / ".git").exists():
        return {"checked": False, "files": [], "threshold": threshold_bytes}
    try:
        result = subprocess.run(
            ["git", "diff", "--cached", "--name-only", "--diff-filter=AM"],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return {"checked": False, "files": [], "threshold": threshold_bytes}
    if result.returncode != 0:
        return {"checked": False, "files": [], "threshold": threshold_bytes}

    over: list[dict] = []
    for line in result.stdout.splitlines():
        rel = line.strip()
        if not rel:
            continue
        full = root / rel
        try:
            size = full.stat().st_size
        except OSError:
            continue
        if size > threshold_bytes:
            over.append({"path": rel, "size": size})
    return {"checked": True, "files": over, "threshold": threshold_bytes}


SQLITE_FRESH_WINDOW_SECONDS = 60


def _detect_sqlite_hygiene(root: Path, fresh_window: int = SQLITE_FRESH_WINDOW_SECONDS) -> dict:
    """Flag a recently-mutated DB whose schema source has uncommitted edits.

    Heuristic: if a *.sqlite/*.db file was modified within `fresh_window`
    seconds AND a related schema file (schema.sql, migrations/) has
    uncommitted edits, suggest a backup before the next risky operation.

    Off by default — opt in via env PRISM_ENABLE_SQLITE_CHECK=1. Niche,
    but cheap to ship since it reuses git status from _detect_git.
    """
    enabled = os.environ.get("PRISM_ENABLE_SQLITE_CHECK", "").strip() in {"1", "true", "yes"}
    if not enabled:
        return {"enabled": False, "warnings": []}

    now = time.time()
    warnings: list[dict] = []
    db_files: list[Path] = []
    try:
        for pat in ("*.sqlite", "*.sqlite3", "*.db"):
            db_files.extend(root.rglob(pat))
    except (OSError, PermissionError):
        return {"enabled": True, "warnings": []}
    fresh_dbs = []
    for db in db_files:
        # Skip stuff inside virtualenvs and node_modules
        if any(part in {".venv", "venv", "node_modules", ".git"} for part in db.parts):
            continue
        try:
            if now - db.stat().st_mtime <= fresh_window:
                fresh_dbs.append(db)
        except OSError:
            continue
    if not fresh_dbs:
        return {"enabled": True, "warnings": []}

    # Check for uncommitted schema-related changes via git
    if not (root / ".git").exists():
        return {"enabled": True, "warnings": []}
    try:
        proc = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return {"enabled": True, "warnings": []}
    if proc.returncode != 0:
        return {"enabled": True, "warnings": []}
    schema_dirty = False
    for line in proc.stdout.splitlines():
        rel = line[3:].strip()
        if not rel:
            continue
        low = rel.lower()
        if low.endswith("schema.sql") or "/migrations/" in low or low.startswith("migrations/"):
            schema_dirty = True
            break
    if not schema_dirty:
        return {"enabled": True, "warnings": []}

    for db in fresh_dbs:
        warnings.append({"db": str(db.relative_to(root)), "reason": "fresh_with_dirty_schema"})
    return {"enabled": True, "warnings": warnings}


def _detect_staged_secrets(root: Path) -> dict:
    """Wrapper over the secrets_scan module — keeps health.py the single dispatch surface."""
    from . import secrets_scan

    if not (root / ".git").exists():
        return {"backend": None, "findings": [], "checked": False}
    result = secrets_scan.scan(str(root))
    result["checked"] = True
    return result


def _detect_secrets_hygiene(root: Path) -> dict:
    """Check for .env in .gitignore, no committed secrets."""
    gitignore_path = root / ".gitignore"
    env_ignored = False
    if gitignore_path.is_file():
        content = gitignore_path.read_text()
        env_ignored = ".env" in content
    env_committed = (root / ".env").is_file() and not env_ignored
    return {"env_in_gitignore": env_ignored, "env_committed": env_committed}


# Common cache/build dirs that bloat first commits when not ignored.
# Detection only fires for entries whose corresponding directory/file
# actually exists in the project — no point flagging absent caches.
COMMON_CACHE_ENTRIES: tuple[tuple[str, str], ...] = (
    (".lintgate/", ".lintgate"),
    (".prism/", ".prism"),
    (".ruff_cache/", ".ruff_cache"),
    (".mypy_cache/", ".mypy_cache"),
    (".pytest_cache/", ".pytest_cache"),
    (".pyre/", ".pyre"),
    (".tox/", ".tox"),
    ("__pycache__/", "__pycache__"),
    ("node_modules/", "node_modules"),
    (".DS_Store", ".DS_Store"),
)


def _gitignore_lines(content: str) -> set[str]:
    """Return non-comment, non-empty lines normalized for membership checks."""
    out: set[str] = set()
    for line in content.splitlines():
        s = line.strip()
        if s and not s.startswith("#"):
            out.add(s)
    return out


def _detect_missing_cache_ignores(root: Path) -> dict:
    """Find cache dirs/files present on disk but not listed in .gitignore.

    Returns:
        {"present": [...], "missing": [...]}
        present: cache entries that exist AND are properly ignored
        missing: cache entries that exist AND are NOT ignored (the actionable set)
    """
    gitignore_path = root / ".gitignore"
    ignored: set[str] = set()
    if gitignore_path.is_file():
        ignored = _gitignore_lines(gitignore_path.read_text())

    present: list[str] = []
    missing: list[str] = []
    for pattern, on_disk_name in COMMON_CACHE_ENTRIES:
        candidate = root / on_disk_name
        # Recursive glob is too expensive; check top-level + first-level dirs.
        exists_top = candidate.exists()
        exists_nested = False
        if not exists_top:
            try:
                for sub in root.iterdir():
                    skip = sub.name in {".git", ".venv", "venv", "node_modules"}
                    if sub.is_dir() and not skip and (sub / on_disk_name).exists():
                        exists_nested = True
                        break
            except (OSError, PermissionError):
                pass
        if not (exists_top or exists_nested):
            continue
        # Match against pattern OR bare name (gitignore tolerates both forms)
        bare = pattern.rstrip("/")
        if pattern in ignored or bare in ignored:
            present.append(pattern)
        else:
            missing.append(pattern)
    return {"present": present, "missing": missing}


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


# Weight profiles for setup-maturity scoring.
#
# Each profile defines per-channel point allocations. Choice of profile
# reflects what kind of project this is — a personal CLI doesn't need CI
# the same way a production service does, and the score should reflect that
# rather than uniformly penalize every project against a one-size template.
#
# Add a profile here when a new use-case shows up; don't bend an existing
# profile to fit, since callers may rely on its shape.
WEIGHT_PROFILES: dict[str, dict[str, int]] = {
    "solo_dev": {
        "venv": 15,
        "lockfile": 10,
        "git_init": 15,
        "git_ignore": 5,
        "git_clean": 10,
        "ci": 5,
        "secrets_ignore": 15,
        "secrets_not_committed": 10,
        "toolchain": 15,
    },
    "shared_repo": {
        "venv": 10,
        "lockfile": 15,
        "git_init": 10,
        "git_ignore": 5,
        "git_clean": 5,
        "ci": 15,
        "secrets_ignore": 10,
        "secrets_not_committed": 10,
        "toolchain": 20,
    },
    "production": {
        "venv": 5,
        "lockfile": 15,
        "git_init": 5,
        "git_ignore": 5,
        "git_clean": 5,
        "ci": 25,
        "secrets_ignore": 15,
        "secrets_not_committed": 15,
        "toolchain": 10,
    },
}

DEFAULT_WEIGHT_PROFILE = "solo_dev"


def _resolve_profile(profile: str | None) -> dict[str, int]:
    """Pick a weight profile from explicit arg, env var, or default."""
    name = profile or os.environ.get("PRISM_WEIGHT_PROFILE") or DEFAULT_WEIGHT_PROFILE
    return WEIGHT_PROFILES.get(name, WEIGHT_PROFILES[DEFAULT_WEIGHT_PROFILE])


def _compute_score(checks: dict, profile: str | None = None) -> int:
    """Compute setup maturity as 0-100 score under a chosen weight profile."""
    weights = _resolve_profile(profile)
    points = 0
    total = sum(weights.values())

    if checks["venv"]["found"]:
        points += weights["venv"]

    if checks["lockfile"]["found"]:
        # Stale lockfile: half credit
        points += weights["lockfile"] // 2 if checks["lockfile"]["stale"] else weights["lockfile"]

    if checks["git"]["initialized"]:
        points += weights["git_init"]
    if checks["git"]["gitignore"]:
        points += weights["git_ignore"]
    if checks["git"]["clean"]:
        points += weights["git_clean"]

    if checks["ci"]["found"]:
        points += weights["ci"]

    if checks["secrets"]["env_in_gitignore"]:
        points += weights["secrets_ignore"]
    if not checks["secrets"]["env_committed"]:
        points += weights["secrets_not_committed"]

    configured = sum(1 for v in checks["toolchain"].values() if v)
    if configured >= 2:
        points += weights["toolchain"]
    elif configured == 1:
        points += weights["toolchain"] // 2

    return round(points / total * 100) if total else 0


def assess(project_path: str, profile: str | None = None) -> dict:
    """Run all health checks and compute maturity score under a weight profile."""
    root = Path(project_path)
    checks = {
        "venv": _detect_venv(root),
        "lockfile": _detect_lockfile(root),
        "git": _detect_git(root),
        "ci": _detect_ci(root),
        "secrets": _detect_secrets_hygiene(root),
        "toolchain": _detect_toolchain(root),
        "cache_ignores": _detect_missing_cache_ignores(root),
        "large_files": _detect_large_staged_files(root),
        "remote": _detect_remote_state(root),
        "secrets_scan": _detect_staged_secrets(root),
        "sqlite": _detect_sqlite_hygiene(root),
    }
    score = _compute_score(checks, profile=profile)
    resolved_profile = (
        profile or os.environ.get("PRISM_WEIGHT_PROFILE") or DEFAULT_WEIGHT_PROFILE
    )
    return {"score": score, "profile": resolved_profile, **checks}


def check(project_path: str, profile: str | None = None) -> str:
    """MCP tool entry point. Returns compact summary + persists for LintGate.

    Args:
        project_path: Project root.
        profile: Weight profile (solo_dev, shared_repo, production). Defaults
            to env PRISM_WEIGHT_PROFILE or solo_dev.
    """
    from . import cross_project
    from . import project_type as _project_type

    root = Path(project_path)
    if not root.is_dir():
        return f"Not a directory: {project_path}"

    checks = assess(project_path, profile=profile)
    score = checks["score"]
    phash = _project_hash(project_path)
    # Update cross-project state so prism_cross_project can find patterns.
    try:
        ptype = _project_type.detect(project_path).get("type", "unknown")
        cross_project.update(project_path, checks, ptype)
    except OSError:
        pass

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
    lines.append(f"**Setup maturity: {score}/100** _(profile: {checks['profile']})_")
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
