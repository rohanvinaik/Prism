"""Recommender — automation suggestions from Prism signals.

Rules-based pattern matching against collected data. No LLM inference.
Reads: health checks, tool usage, error patterns, hook config, LintGate state.
Recommends: hooks, setup fixes, workflow improvements, tool configurations.
"""

import json
from collections import Counter
from pathlib import Path
from . import engine, sources

SETTINGS_PATH = Path.home() / ".claude" / "settings.json"


def _load_hook_config() -> dict:
    """Read current hook configuration from settings.json."""
    if not SETTINGS_PATH.is_file():
        return {}
    try:
        data = json.loads(SETTINGS_PATH.read_text())
        return data.get("hooks", {})
    except (json.JSONDecodeError, OSError):
        return {}


def _has_hook(hooks: dict, event: str, command_substr: str) -> bool:
    """Check if a hook matching command_substr exists for the event type."""
    for matcher_group in hooks.get(event, []):
        for hook in matcher_group.get("hooks", []):
            if command_substr in hook.get("command", ""):
                return True
    return False


def _setup_recommendations(project_path: str) -> list[dict]:
    """Recommendations from health lens data."""
    from . import health
    if not project_path or not Path(project_path).is_dir():
        return []

    checks = health.assess(project_path)
    recs = []

    if not checks.get("venv", {}).get("found"):
        recs.append({
            "category": "setup",
            "priority": "high",
            "title": "Create virtual environment",
            "reason": "No venv detected. Dependency isolation prevents global package conflicts.",
            "action": "uv venv .venv && source .venv/bin/activate",
        })

    lock = checks.get("lockfile", {})
    if not lock.get("found"):
        recs.append({
            "category": "setup",
            "priority": "high",
            "title": "Add lockfile",
            "reason": "No lockfile found. Builds are non-reproducible.",
            "action": "uv lock (or pip freeze > requirements.txt)",
        })
    elif lock.get("stale"):
        recs.append({
            "category": "setup",
            "priority": "medium",
            "title": "Refresh stale lockfile",
            "reason": "Lockfile is older than manifest. Dependencies may have drifted.",
            "action": "uv lock --upgrade",
        })

    git = checks.get("git", {})
    if not git.get("initialized"):
        recs.append({
            "category": "setup",
            "priority": "high",
            "title": "Initialize git",
            "reason": "No git repo. Version control is table stakes.",
            "action": "git init && git add -A && git commit -m 'initial'",
        })
    elif not git.get("gitignore"):
        recs.append({
            "category": "setup",
            "priority": "medium",
            "title": "Add .gitignore",
            "reason": "No .gitignore. Risk of committing venvs, caches, secrets.",
            "action": "curl -sL gitignore.io/api/python > .gitignore",
        })

    secrets = checks.get("secrets", {})
    if secrets.get("env_committed"):
        recs.append({
            "category": "security",
            "priority": "critical",
            "title": "Remove .env from git tracking",
            "reason": ".env file is committed. Secrets may be in git history.",
            "action": "echo '.env' >> .gitignore && git rm --cached .env",
        })

    toolchain = checks.get("toolchain", {})
    if not any(toolchain.values()):
        recs.append({
            "category": "setup",
            "priority": "medium",
            "title": "Configure a linter",
            "reason": "No linter/formatter configured. Code quality is unguarded.",
            "action": "uv add --dev ruff && ruff check .",
        })

    return recs


def _hook_recommendations(hooks: dict, sessions: list[sources.SessionData]) -> list[dict]:
    """Recommendations based on tool usage patterns + hook gaps."""
    recs = []
    tool_counts: Counter[str] = Counter()
    for s in sessions:
        for tc in s.tool_calls:
            tool_counts[tc.name] += 1

    # High Edit usage but no PostToolUse lint hook
    edits = sum(tool_counts.get(t, 0) for t in ("Edit", "Write"))
    if edits > 10 and not _has_hook(hooks, "PostToolUse", "lintgate"):
        recs.append({
            "category": "hooks",
            "priority": "high",
            "title": "Add PostToolUse lint hook",
            "reason": f"{edits} edit operations with no automatic linting. Errors accumulate silently.",
            "action": "Add LintGate PostToolUse hook to settings.json",
        })

    # High Bash usage but no PreToolUse guard
    bash_count = tool_counts.get("Bash", 0)
    if bash_count > 20 and not _has_hook(hooks, "PreToolUse", "lintgate"):
        recs.append({
            "category": "hooks",
            "priority": "medium",
            "title": "Add PreToolUse mutation guard",
            "reason": f"{bash_count} Bash calls with no system mutation guard.",
            "action": "Add LintGate PreToolUse hook to settings.json",
        })

    # No RTK rewriting
    if bash_count > 10 and not _has_hook(hooks, "PreToolUse", "rtk"):
        recs.append({
            "category": "hooks",
            "priority": "medium",
            "title": "Add RTK token-saving hook",
            "reason": f"{bash_count} Bash calls with no RTK command rewriting. Potential token savings.",
            "action": "Install rtk and add PreToolUse hook",
        })

    # No Prism hooks
    if not _has_hook(hooks, "PostToolUse", "prism"):
        recs.append({
            "category": "hooks",
            "priority": "low",
            "title": "Add Prism telemetry hooks",
            "reason": "No real-time Prism monitoring. Session efficiency and trends unavailable.",
            "action": "Add prism-hook to PostToolUse and Stop in settings.json",
        })

    return recs


def _efficiency_recommendations() -> list[dict]:
    """Recommendations from bridge/efficiency data."""
    recs = []
    bridge = engine.read_bridge()
    if not bridge:
        return recs

    error_rate = bridge.get("error_rate", 0)
    if error_rate > 0.15:
        recs.append({
            "category": "workflow",
            "priority": "high",
            "title": "Investigate high error rate",
            "reason": f"Last session had {error_rate:.0%} tool error rate. Consider reading files before editing.",
            "action": "Run prism_forensics to identify error patterns",
        })

    compactions = bridge.get("compactions", 0)
    if compactions >= 3:
        recs.append({
            "category": "workflow",
            "priority": "medium",
            "title": "Reduce context pressure",
            "reason": f"{compactions} context compactions in last session. Consider breaking work into smaller sessions.",
            "action": "Use Agent tool to delegate independent subtasks",
        })

    return recs


def _subagent_recommendations(sessions: list[sources.SessionData]) -> list[dict]:
    """Recommendations from subagent usage patterns."""
    recs = []
    total_subagent_tokens = sum(s.subagent_usage.total for s in sessions)
    total_tokens = sum(s.usage.total for s in sessions)

    if total_tokens > 0 and total_subagent_tokens > total_tokens * 2:
        recs.append({
            "category": "workflow",
            "priority": "medium",
            "title": "Subagent token cost is high",
            "reason": f"Subagents consumed {total_subagent_tokens:,} tokens vs {total_tokens:,} main. Consider more targeted agent prompts.",
            "action": "Review subagent spawns with prism_behavior",
        })

    return recs


def run(project_path: str = "", period: str = "week") -> str:
    since = sources.period_to_since(period)
    sessions = list(sources.iter_sessions(since=since))
    hooks = _load_hook_config()

    all_recs: list[dict] = []
    all_recs.extend(_setup_recommendations(project_path))
    all_recs.extend(_hook_recommendations(hooks, sessions))
    all_recs.extend(_efficiency_recommendations())
    all_recs.extend(_subagent_recommendations(sessions))

    # Sort by priority
    priority_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    all_recs.sort(key=lambda r: priority_order.get(r.get("priority", "low"), 4))

    if not all_recs:
        return "# Prism Recommendations\n\nNo recommendations — setup looks good."

    # Compact summary
    lines = [f"# Prism Recommendations ({len(all_recs)} items)", ""]
    for r in all_recs:
        lines.append(f"- **[{r['priority'].upper()}]** {r['title']}: {r['reason']}")

    summary = "\n".join(lines)

    full_data = {
        "project_path": project_path,
        "period": period,
        "recommendations": all_recs,
    }
    aid = engine.save_snapshot("recommend", summary, full_data)
    lines.append("")
    lines.append(f"_Details: prism_details(\"{aid}\", section=\"recommendations\")_")

    return "\n".join(lines)
