"""Recommender — automation suggestions from Prism signals.

Rules-based pattern matching against collected data. No LLM inference.
Reads: health checks, tool usage, error patterns, hook config, LintGate state.
Recommends: hooks, setup fixes, workflow improvements, tool configurations.

Each recommendation includes a confidence score (0-100):
  95+ = deterministic fact (file exists/doesn't exist)
  70-94 = strong behavioral signal (many data points)
  40-69 = moderate signal (few sessions, heuristic)
  <40 = weak signal (insufficient data, speculative)
"""

import json
from collections import Counter
from pathlib import Path

from . import engine, sources

SETTINGS_PATH = Path.home() / ".claude" / "settings.json"


def _rec(category: str, priority: str, title: str, reason: str, action: str, confidence: int) -> dict:
    """Build a recommendation dict with all required fields."""
    return {
        "category": category,
        "priority": priority,
        "title": title,
        "reason": reason,
        "action": action,
        "confidence": max(0, min(100, confidence)),
    }


def _load_hook_config() -> dict:
    if not SETTINGS_PATH.is_file():
        return {}
    try:
        data = json.loads(SETTINGS_PATH.read_text())
        return data.get("hooks", {})
    except (json.JSONDecodeError, OSError):
        return {}


def _has_hook(hooks: dict, event: str, command_substr: str) -> bool:
    for matcher_group in hooks.get(event, []):
        for hook in matcher_group.get("hooks", []):
            if command_substr in hook.get("command", ""):
                return True
    return False


def _setup_recommendations(project_path: str) -> list[dict]:
    from . import health
    if not project_path or not Path(project_path).is_dir():
        return []

    checks = health.assess(project_path)
    recs = []
    has_manifest = Path(project_path, "pyproject.toml").is_file() or Path(project_path, "package.json").is_file()

    if not checks.get("venv", {}).get("found"):
        # Higher confidence if a manifest exists (we know they need a venv)
        conf = 95 if has_manifest else 70
        recs.append(_rec("setup", "high", "Create virtual environment",
                         "No venv detected. Dependency isolation prevents global package conflicts.",
                         "uv venv .venv", conf))

    lock = checks.get("lockfile", {})
    if not lock.get("found") and has_manifest:
        recs.append(_rec("setup", "high", "Add lockfile",
                         "No lockfile found. Builds are non-reproducible.",
                         "uv lock", 95))
    elif lock.get("stale"):
        recs.append(_rec("setup", "medium", "Refresh stale lockfile",
                         "Lockfile is older than manifest. Dependencies may have drifted.",
                         "uv lock", 90))

    git = checks.get("git", {})
    if not git.get("initialized"):
        recs.append(_rec("setup", "high", "Initialize git",
                         "No git repo. Version control is table stakes.",
                         "git init", 95))
    elif not git.get("gitignore"):
        recs.append(_rec("setup", "medium", "Add .gitignore",
                         "No .gitignore. Risk of committing venvs, caches, secrets.",
                         "generate .gitignore", 90))

    secrets = checks.get("secrets", {})
    if secrets.get("env_committed"):
        recs.append(_rec("security", "critical", "Remove .env from git tracking",
                         ".env file is committed. Secrets may be in git history.",
                         "git rm --cached .env", 98))

    toolchain = checks.get("toolchain", {})
    if not any(toolchain.values()) and has_manifest:
        recs.append(_rec("setup", "medium", "Configure a linter",
                         "No linter/formatter configured. Code quality is unguarded.",
                         "add ruff.toml", 75))

    return recs


def _hook_recommendations(hooks: dict, sessions: list[sources.SessionData]) -> list[dict]:
    recs = []
    tool_counts: Counter[str] = Counter()
    for s in sessions:
        for tc in s.tool_calls:
            tool_counts[tc.name] += 1

    # More data points = higher confidence
    data_confidence = min(90, 40 + len(sessions) * 5)

    edits = sum(tool_counts.get(t, 0) for t in ("Edit", "Write"))
    if edits > 10 and not _has_hook(hooks, "PostToolUse", "lintgate"):
        recs.append(_rec("hooks", "high", "Add PostToolUse lint hook",
                         f"{edits} edit operations with no automatic linting.",
                         "Add LintGate PostToolUse hook", data_confidence))

    bash_count = tool_counts.get("Bash", 0)
    if bash_count > 20 and not _has_hook(hooks, "PreToolUse", "lintgate"):
        recs.append(_rec("hooks", "medium", "Add PreToolUse mutation guard",
                         f"{bash_count} Bash calls with no system mutation guard.",
                         "Add LintGate PreToolUse hook", data_confidence))

    if bash_count > 10 and not _has_hook(hooks, "PreToolUse", "rtk"):
        recs.append(_rec("hooks", "medium", "Add RTK token-saving hook",
                         f"{bash_count} Bash calls with no RTK command rewriting.",
                         "Install rtk and add PreToolUse hook", data_confidence))

    if not _has_hook(hooks, "PostToolUse", "prism"):
        recs.append(_rec("hooks", "low", "Add Prism telemetry hooks",
                         "No real-time Prism monitoring.",
                         "Add prism-hook to PostToolUse and Stop", 60))

    return recs


def _efficiency_recommendations() -> list[dict]:
    recs = []
    bridge = engine.read_bridge()
    if not bridge:
        return recs

    error_rate = bridge.get("error_rate", 0)
    tool_calls = bridge.get("tool_calls", 0)
    # More tool calls = more data = higher confidence in error rate
    err_confidence = min(85, 30 + tool_calls * 2)

    if error_rate > 0.15:
        recs.append(_rec("workflow", "high", "Investigate high error rate",
                         f"Last session had {error_rate:.0%} tool error rate.",
                         "Run prism_forensics to identify patterns", err_confidence))

    compactions = bridge.get("compactions", 0)
    if compactions >= 3:
        recs.append(_rec("workflow", "medium", "Reduce context pressure",
                         f"{compactions} context compactions in last session.",
                         "Break work into smaller sessions or delegate with Agent", 70))

    return recs


def _subagent_recommendations(sessions: list[sources.SessionData]) -> list[dict]:
    recs = []
    total_subagent_tokens = sum(s.subagent_usage.total for s in sessions)
    total_tokens = sum(s.usage.total for s in sessions)
    data_confidence = min(80, 30 + len(sessions) * 5)

    if total_tokens > 0 and total_subagent_tokens > total_tokens * 2:
        recs.append(_rec("workflow", "medium", "Subagent token cost is high",
                         f"Subagents consumed {total_subagent_tokens:,} vs {total_tokens:,} main tokens.",
                         "Review spawns with prism_behavior", data_confidence))

    return recs


def run(project_path: str = "", period: str = "week", min_confidence: int = 0) -> str:
    since = sources.period_to_since(period)
    sessions = list(sources.iter_sessions(since=since))
    hooks = _load_hook_config()

    all_recs: list[dict] = []
    all_recs.extend(_setup_recommendations(project_path))
    all_recs.extend(_hook_recommendations(hooks, sessions))
    all_recs.extend(_efficiency_recommendations())
    all_recs.extend(_subagent_recommendations(sessions))

    # Filter by confidence threshold
    all_recs = [r for r in all_recs if r["confidence"] >= min_confidence]

    # Sort by priority, then confidence descending
    priority_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    all_recs.sort(key=lambda r: (priority_order.get(r["priority"], 4), -r["confidence"]))

    if not all_recs:
        return "# Prism Recommendations\n\nNo recommendations — setup looks good."

    lines = [f"# Prism Recommendations ({len(all_recs)} items)", ""]
    for r in all_recs:
        lines.append(f"- **[{r['priority'].upper()}]** {r['title']} ({r['confidence']}%): {r['reason']}")

    summary = "\n".join(lines)
    full_data = {"project_path": project_path, "period": period, "recommendations": all_recs}
    aid = engine.save_snapshot("recommend", summary, full_data)
    lines.append("")
    lines.append(f"_Details: prism_details(\"{aid}\", section=\"recommendations\")_")

    return "\n".join(lines)
