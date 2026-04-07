"""Auto-Fixer — deterministic remediation from Prism recommendations.

Executes the `action` field from prism_recommend. All fixes are
deterministic (no LLM inference). Safety: dry_run=True by default.

Supported fix categories:
  - setup: venv creation, lockfile sync, .gitignore, git init
  - security: remove committed .env
  - hooks: patch settings.json with missing hooks
  - toolchain: install linter config
"""

import json
import subprocess
from pathlib import Path
from typing import Callable, Optional

from . import engine, recommend

SETTINGS_PATH = Path.home() / ".claude" / "settings.json"

# Registry of fixable recommendation titles → fix functions.
# Each fix function returns (success: bool, message: str).
FIX_REGISTRY: dict[str, Callable[[str], tuple[bool, str]]] = {}


def _register(title: str):
    """Decorator to register a fix function for a recommendation title."""
    def decorator(fn):
        FIX_REGISTRY[title] = fn
        return fn
    return decorator


def _run_cmd(args: list[str], cwd: str, timeout: int = 30) -> tuple[bool, str]:
    """Run a command as arg list, return (success, output)."""
    try:
        result = subprocess.run(
            args, cwd=cwd,
            capture_output=True, text=True, timeout=timeout,
        )
        output = (result.stdout + result.stderr).strip()
        return result.returncode == 0, output or "(no output)"
    except subprocess.TimeoutExpired:
        return False, f"Command timed out after {timeout}s"
    except FileNotFoundError:
        return False, f"Command not found: {args[0]}"


# ---------------------------------------------------------------------------
# Fix implementations
# ---------------------------------------------------------------------------

@_register("Create virtual environment")
def _fix_venv(project_path: str) -> tuple[bool, str]:
    root = Path(project_path)
    if (root / ".venv").exists():
        return True, "Venv already exists"
    ok, msg = _run_cmd(["uv", "venv", ".venv"], project_path)
    if not ok:
        ok, msg = _run_cmd(["python3", "-m", "venv", ".venv"], project_path)
    return ok, msg


@_register("Add lockfile")
def _fix_lockfile(project_path: str) -> tuple[bool, str]:
    root = Path(project_path)
    if (root / "pyproject.toml").is_file():
        return _run_cmd(["uv", "lock"], project_path)
    if (root / "package.json").is_file():
        return _run_cmd(["npm", "install", "--package-lock-only"], project_path)
    return False, "No manifest file found (pyproject.toml or package.json)"


@_register("Refresh stale lockfile")
def _fix_stale_lockfile(project_path: str) -> tuple[bool, str]:
    return _run_cmd(["uv", "lock"], project_path)


@_register("Initialize git")
def _fix_git_init(project_path: str) -> tuple[bool, str]:
    ok, msg = _run_cmd(["git", "init"], project_path)
    if not ok:
        return ok, msg
    _run_cmd(["git", "add", "-A"], project_path)
    return _run_cmd(["git", "commit", "-m", "initial commit"], project_path)


@_register("Add .gitignore")
def _fix_gitignore(project_path: str) -> tuple[bool, str]:
    root = Path(project_path)
    gitignore = root / ".gitignore"
    if gitignore.exists():
        return True, ".gitignore already exists"

    # Detect project type for template
    if (root / "pyproject.toml").is_file() or (root / "setup.py").is_file():
        template = "__pycache__/\n*.pyc\n*.egg-info/\n.venv/\nvenv/\ndist/\nbuild/\n.env\n.lintgate/\n"
    elif (root / "package.json").is_file():
        template = "node_modules/\ndist/\n.env\n*.log\n"
    elif (root / "Cargo.toml").is_file():
        template = "target/\n.env\n"
    else:
        template = ".env\n*.log\n"

    gitignore.write_text(template)
    return True, f"Created .gitignore ({len(template.splitlines())} entries)"


@_register("Remove .env from git tracking")
def _fix_env_committed(project_path: str) -> tuple[bool, str]:
    root = Path(project_path)
    gitignore = root / ".gitignore"

    # Ensure .env is in .gitignore
    if gitignore.is_file():
        content = gitignore.read_text()
        if ".env" not in content:
            with open(gitignore, "a") as f:
                f.write("\n.env\n")
    else:
        gitignore.write_text(".env\n")

    ok, msg = _run_cmd(["git", "rm", "--cached", ".env"], project_path)
    return True, msg  # OK even if .env wasn't tracked


@_register("Configure a linter")
def _fix_linter(project_path: str) -> tuple[bool, str]:
    root = Path(project_path)
    ruff_toml = root / "ruff.toml"
    if ruff_toml.exists():
        return True, "ruff.toml already exists"

    ruff_toml.write_text('[lint]\nselect = ["E", "F", "W", "I"]\n\n[format]\nquote-style = "double"\n')
    return True, "Created ruff.toml with standard rules"


@_register("Add Prism telemetry hooks")
def _fix_prism_hooks(project_path: str) -> tuple[bool, str]:
    if not SETTINGS_PATH.is_file():
        return False, "~/.claude/settings.json not found"

    try:
        settings = json.loads(SETTINGS_PATH.read_text())
    except (json.JSONDecodeError, OSError) as e:
        return False, f"Failed to read settings: {e}"

    hooks = settings.setdefault("hooks", {})
    prism_hook_cmd = "/Users/rohanvinaik/tools/Prism/.venv/bin/prism-hook"
    prism_entry = {"hooks": [{"type": "command", "command": prism_hook_cmd}]}

    changed = False
    for event in ("PostToolUse", "SessionStart", "PreCompact", "Stop"):
        event_hooks = hooks.setdefault(event, [])
        already = any(
            prism_hook_cmd in h.get("command", "")
            for group in event_hooks
            for h in group.get("hooks", [])
        )
        if not already:
            event_hooks.append(prism_entry)
            changed = True

    if not changed:
        return True, "Prism hooks already configured"

    SETTINGS_PATH.write_text(json.dumps(settings, indent=2))
    return True, "Added Prism hooks to settings.json"


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run(project_path: str, dry_run: bool = True, titles: Optional[list[str]] = None) -> str:
    """Run auto-fixes from recommendations.

    Args:
        project_path: Project root to fix.
        dry_run: If True, show what would be fixed without executing.
        titles: Specific recommendation titles to fix. None = fix all fixable.
    """
    # Generate fresh recommendations (writes snapshot to disk)
    recommend.run(project_path, "week")

    # Load the snapshot to get structured recommendations
    snapshots = engine.list_snapshots(limit=5)
    rec_snapshot = None
    for s in snapshots:
        if s.get("tool") == "recommend":
            rec_snapshot = engine.load_snapshot(s["analysis_id"])
            break

    if not rec_snapshot:
        return "No recommendations found. Run prism_recommend first."

    recs = rec_snapshot.get("recommendations", [])
    if not recs:
        return "No recommendations to fix."

    # Filter to fixable items
    fixable = []
    for r in recs:
        title = r.get("title", "")
        if title in FIX_REGISTRY:
            if titles is None or title in titles:
                fixable.append(r)

    if not fixable:
        unfixable = [r["title"] for r in recs if r["title"] not in FIX_REGISTRY]
        return (
            f"No auto-fixable recommendations found.\n"
            f"Manual action needed for: {', '.join(unfixable)}"
        )

    lines = [f"# Prism Auto-Fix ({'DRY RUN' if dry_run else 'APPLYING'})", ""]

    results = []
    for r in fixable:
        title = r["title"]
        priority = r.get("priority", "medium")
        fix_fn = FIX_REGISTRY[title]

        if dry_run:
            lines.append(f"- **[{priority.upper()}]** {title}: would execute fix")
            results.append({"title": title, "status": "dry_run"})
        else:
            success, msg = fix_fn(project_path)
            status = "fixed" if success else "failed"
            icon = "ok" if success else "FAILED"
            lines.append(f"- **[{priority.upper()}]** {title}: {icon} — {msg}")
            results.append({"title": title, "status": status, "message": msg})

    summary = "\n".join(lines)
    full_data = {"project_path": project_path, "dry_run": dry_run, "results": results}
    aid = engine.save_snapshot("fix", summary, full_data)
    lines.append("")
    lines.append(f"_snapshot: {aid}_")

    return "\n".join(lines)
