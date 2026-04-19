"""Cross-project pattern learning.

Tracks lightweight per-project hygiene signals across all Prism-monitored
projects so recommendations can be grounded in the user's own pattern
("you have CI on 3/4 of your Python projects; this one is missing it")
rather than generic best-practice scolding.

State lives at ~/.prism/projects.json, schema-versioned to allow safe
migrations. Updated automatically on every prism_health call.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

PROJECTS_STATE_PATH = Path.home() / ".prism" / "projects.json"
SCHEMA_VERSION = 1

# Tracked attributes are intentionally minimal — only the booleans the
# pattern engine compares across projects. Add fields conservatively;
# every new field becomes a comparison axis users may not want.
TRACKED_ATTRS = (
    "type",
    "has_ci",
    "has_tests",
    "has_claude_md",
    "has_lockfile",
    "has_readme",
)


def _empty_state() -> dict[str, Any]:
    return {"schema_version": SCHEMA_VERSION, "projects": {}}


def load_state(path: Path = PROJECTS_STATE_PATH) -> dict[str, Any]:
    """Read state file, returning empty state if missing or unreadable."""
    if not path.is_file():
        return _empty_state()
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return _empty_state()
    if not isinstance(data, dict):
        return _empty_state()
    if data.get("schema_version") != SCHEMA_VERSION:
        # Future migration hook — currently treat as empty
        return _empty_state()
    if not isinstance(data.get("projects"), dict):
        return _empty_state()
    return data


def save_state(state: dict[str, Any], path: Path = PROJECTS_STATE_PATH) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state, indent=2, sort_keys=True))
    except OSError:
        pass


def _project_attrs(project_path: str, checks: dict, project_type: str) -> dict[str, Any]:
    root = Path(project_path)
    return {
        "type": project_type,
        "has_ci": bool(checks.get("ci", {}).get("found")),
        "has_tests": (root / "tests").is_dir() or (root / "test").is_dir(),
        "has_claude_md": (root / "CLAUDE.md").is_file()
        or (root / ".claude" / "CLAUDE.md").is_file(),
        "has_lockfile": bool(checks.get("lockfile", {}).get("found")),
        "has_readme": (root / "README.md").is_file() or (root / "README.rst").is_file(),
        "last_scanned": int(time.time()),
        "name": root.name,
    }


def update(
    project_path: str,
    checks: dict,
    project_type: str,
    path: Path = PROJECTS_STATE_PATH,
) -> dict[str, Any]:
    """Update the projects state file with latest per-project signals.

    Returns the post-update state for callers that want to derive patterns
    in the same call without re-loading.
    """
    state = load_state(path)
    state["projects"][str(Path(project_path).resolve())] = _project_attrs(
        project_path, checks, project_type
    )
    save_state(state, path)
    return state


def patterns(state: dict[str, Any], project_path: str) -> list[dict[str, Any]]:
    """Identify cross-project gaps for the given project.

    A pattern fires when:
      - >=3 other projects share the SAME type as the target,
      - the target lacks an attribute that >=66% of those peers have.

    The 3-project floor prevents noise from a single counterexample.
    The same-type filter prevents library suggestions bleeding into CLIs.
    """
    projects = state.get("projects", {})
    target_key = str(Path(project_path).resolve())
    target = projects.get(target_key)
    if not target:
        return []

    target_type = target.get("type", "unknown")
    peers = [
        attrs
        for key, attrs in projects.items()
        if key != target_key and attrs.get("type") == target_type
    ]
    if len(peers) < 3:
        return []

    findings: list[dict[str, Any]] = []
    for attr in ("has_ci", "has_tests", "has_claude_md", "has_lockfile", "has_readme"):
        if target.get(attr):
            continue
        present_count = sum(1 for p in peers if p.get(attr))
        ratio = present_count / len(peers)
        if ratio >= 2 / 3:
            findings.append(
                {
                    "attribute": attr,
                    "peer_ratio": round(ratio, 2),
                    "peer_count_with": present_count,
                    "peer_total": len(peers),
                    "target_type": target_type,
                }
            )
    return findings


def summarize(project_path: str, state: dict[str, Any] | None = None) -> str:
    """Render patterns for a project as a short markdown report."""
    if state is None:
        state = load_state()
    pats = patterns(state, project_path)
    project_name = Path(project_path).name
    header = f"# Cross-project patterns — {project_name}"
    if not pats:
        return f"{header}\n\nNo gaps detected (or insufficient peer projects)."
    lines = [header, ""]
    attr_labels = {
        "has_ci": "CI configuration",
        "has_tests": "tests/ directory",
        "has_claude_md": "CLAUDE.md",
        "has_lockfile": "lockfile",
        "has_readme": "README",
    }
    for p in pats:
        label = attr_labels.get(p["attribute"], p["attribute"])
        ratio_pct = int(p["peer_ratio"] * 100)
        lines.append(
            f"- **{label}**: {p['peer_count_with']}/{p['peer_total']} of your "
            f"{p['target_type']} projects have this ({ratio_pct}%) — this one doesn't."
        )
    return "\n".join(lines)
