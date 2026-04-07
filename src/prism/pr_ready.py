"""PR Readiness Gate — composite go/no-go from on-disk state.

Zero agent spawns, zero LLM inference. Reads existing state from:
  - LintGate controlplane (blockers, coherence)
  - Prism health (setup maturity)
  - Prism bridge (session efficiency, error rate)
  - Git status (clean working tree, branch)
  - Lockfile freshness

Returns pass/fail with specific blockers.
"""

import subprocess
from pathlib import Path

from . import engine, health


def _git_status(project_path: str) -> dict:
    """Check git state: branch, clean, uncommitted count."""
    try:
        branch = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=project_path,
            capture_output=True,
            text=True,
            timeout=5,
        )
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=project_path,
            capture_output=True,
            text=True,
            timeout=5,
        )
        uncommitted = len([line for line in status.stdout.splitlines() if line.strip()])
        return {
            "branch": branch.stdout.strip() if branch.returncode == 0 else "unknown",
            "clean": uncommitted == 0,
            "uncommitted_files": uncommitted,
        }
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return {"branch": "unknown", "clean": False, "uncommitted_files": -1}


def _lintgate_status(project_path: str) -> dict:
    """Read latest LintGate controlplane state from disk."""
    lg_dir = Path.home() / ".claude" / "lintgate" / "analysis" / "controlplane_run"
    if not lg_dir.is_dir():
        return {"available": False}

    # Find most recent run file
    runs = sorted(lg_dir.glob("*.json"), key=lambda f: f.stat().st_mtime, reverse=True)
    if not runs:
        return {"available": False}

    import json

    try:
        data = json.loads(runs[0].read_text())
        counts = data.get("counts", {})
        return {
            "available": True,
            "blocking": counts.get("blocking", 0),
            "warnings": counts.get("warning", 0),
            "coherence": data.get("coherence", "unknown"),
        }
    except (json.JSONDecodeError, OSError):
        return {"available": False}


def assess(project_path: str) -> str:
    """Assess PR readiness. Returns pass/fail with blockers."""
    if not project_path or not Path(project_path).is_dir():
        return "Error: project_path required."

    blockers: list[str] = []
    warnings: list[str] = []

    # Git status
    git = _git_status(project_path)
    if not git["clean"]:
        blockers.append(f"Uncommitted changes: {git['uncommitted_files']} files")
    if git["branch"] in ("main", "master"):
        warnings.append("On main/master branch — consider a feature branch")

    # Health score
    checks = health.assess(project_path)
    score = checks.get("score", 0)
    if score < 40:
        blockers.append(f"Setup maturity {score}/100 — critical gaps")
    elif score < 70:
        warnings.append(f"Setup maturity {score}/100 — some gaps")

    lock = checks.get("lockfile", {})
    if lock.get("stale"):
        blockers.append("Lockfile is stale (older than manifest)")
    elif not lock.get("found"):
        warnings.append("No lockfile — builds not reproducible")

    secrets = checks.get("secrets", {})
    if secrets.get("env_committed"):
        blockers.append("CRITICAL: .env file is tracked in git")

    # LintGate
    lg = _lintgate_status(project_path)
    if lg.get("available"):
        if lg["blocking"] > 0:
            blockers.append(f"LintGate: {lg['blocking']} blocking issues")
        if lg.get("coherence") in ("degraded", "systemic"):
            warnings.append(f"LintGate coherence: {lg['coherence']}")

    # Session efficiency
    bridge = engine.read_bridge()
    if bridge:
        error_rate = bridge.get("error_rate", 0)
        if error_rate > 0.2:
            warnings.append(f"Session error rate {error_rate:.0%} — review before merging")
        eff = bridge.get("efficiency_score")
        if eff is not None and eff < 50:
            warnings.append(f"Session efficiency {eff}/100 — consider cleanup pass")

    # Verdict
    passed = len(blockers) == 0

    lines = [f"# PR Readiness — {Path(project_path).name}", ""]
    lines.append(f"**{'PASS' if passed else 'BLOCKED'}** (branch: {git['branch']})")
    lines.append("")

    if blockers:
        lines.append("## Blockers")
        for b in blockers:
            lines.append(f"- {b}")
        lines.append("")

    if warnings:
        lines.append("## Warnings")
        for w in warnings:
            lines.append(f"- {w}")
        lines.append("")

    if not blockers and not warnings:
        lines.append("All checks passed. Ready to push.")

    summary = "\n".join(lines)
    full_data = {
        "project_path": project_path,
        "passed": passed,
        "blockers": blockers,
        "warnings": warnings,
        "git": git,
        "health_score": score,
        "lintgate": lg,
        "bridge": bridge or {},
    }
    aid = engine.save_snapshot("pr_ready", summary, full_data)
    lines.append(f"_snapshot: {aid}_")
    return "\n".join(lines)
