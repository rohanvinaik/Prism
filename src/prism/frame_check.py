"""Frame-discipline gate for PreToolUse hook.

When a skill with a `frame` frontmatter block is active, this module decides
whether a proposed tool call is allowed:

1. Skill-tool surface check: if `frame.refuse_outside_surface` is true, the
   proposed tool must match one of the globs in `frame.allowed_tools`.
2. next_actions discipline: if `frame.authority == "next_actions"` AND the
   most recent tool response had a `next_actions` field, the proposed tool
   call must match one of those prescribed items.

Decisions:
- "allow"       - call passes both checks (or no frame active)
- "block"       - hard block; PreToolUse returns exit code 2 with explanation
- "frame_init"  - the call IS a Skill tool invocation; we parse the target
                  SKILL.md's frontmatter and write the frame to phase_state,
                  then allow the call through

Manual override: if a tool call's args include the magic key `_frame_deviation`
(a string explanation), the deviation is logged and the call passes.
"""

from __future__ import annotations

import fnmatch
import json
import os
from pathlib import Path
from typing import Any

from . import phase_matcher

# Skill lookup roots (project-local first, then global)
_GLOBAL_SKILLS_DIR = Path.home() / ".claude" / "skills"

# Magic args key for manual frame override
DEVIATION_KEY = "_frame_deviation"


# ---------------------------------------------------------------------------
# Frontmatter parsing (no external YAML dep — supports simple frame blocks)
# ---------------------------------------------------------------------------


def _parse_frontmatter(text: str) -> dict | None:
    """Extract YAML frontmatter from a SKILL.md file.

    Returns the parsed top-level dict, or None if no frontmatter found.

    We avoid a YAML dep by hand-parsing the supported subset:
    - top-level scalar `key: value`
    - `frame:` nested block with the documented keys
    - lists as `- item` indented under their key
    """
    if not text.startswith("---"):
        return None
    end = text.find("\n---", 3)
    if end < 0:
        return None
    body = text[3:end].strip()
    return _parse_simple_yaml(body)


def _parse_simple_yaml(text: str) -> dict:
    """Hand-rolled subset of YAML parsing sufficient for SKILL.md frontmatter.

    Supports:
    - Top-level `key: value` (string)
    - Nested mapping (one-level deep) via indented `  key: value`
    - Lists via `  - value` indented under their key
    - Booleans `true`/`false`/`yes`/`no`
    - Quoted strings (double quotes)
    """
    result: dict[str, Any] = {}
    lines = text.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.rstrip()
        if not stripped or stripped.lstrip().startswith("#"):
            i += 1
            continue

        # Detect indentation
        indent = len(line) - len(line.lstrip())
        if indent == 0:
            # Top-level key
            if ":" not in stripped:
                i += 1
                continue
            key, _, val = stripped.partition(":")
            key = key.strip()
            val = val.strip()
            if val:
                result[key] = _coerce_scalar(val)
                i += 1
            else:
                # Block follows — gather indented lines
                block, consumed = _parse_block(lines, i + 1)
                result[key] = block
                i += 1 + consumed
        else:
            # Stray indented line at top level — skip
            i += 1

    return result


def _parse_block(lines: list[str], start: int) -> tuple[Any, int]:
    """Parse an indented block starting at lines[start].

    Returns (block_value, lines_consumed). Block is either a list (if first
    non-blank line starts with `-`) or a dict.
    """
    # Find first non-blank line
    j = start
    while j < len(lines) and not lines[j].strip():
        j += 1
    if j >= len(lines):
        return {}, j - start

    first_line = lines[j]
    first_indent = len(first_line) - len(first_line.lstrip())
    if first_indent == 0:
        # No block content
        return {}, 0

    first_stripped = first_line.strip()
    is_list = first_stripped.startswith("- ") or first_stripped == "-"

    if is_list:
        items: list[Any] = []
        k = j
        while k < len(lines):
            line = lines[k]
            stripped = line.rstrip()
            if not stripped or stripped.lstrip().startswith("#"):
                k += 1
                continue
            line_indent = len(line) - len(line.lstrip())
            if line_indent < first_indent:
                break
            if line_indent == first_indent and stripped.lstrip().startswith("-"):
                val = stripped.lstrip()[1:].strip()
                items.append(_coerce_scalar(val))
                k += 1
            else:
                break
        return items, k - start
    else:
        # Dict block — recurse
        sub: dict[str, Any] = {}
        k = j
        while k < len(lines):
            line = lines[k]
            stripped = line.rstrip()
            if not stripped or stripped.lstrip().startswith("#"):
                k += 1
                continue
            line_indent = len(line) - len(line.lstrip())
            if line_indent < first_indent:
                break
            if line_indent > first_indent:
                # Belongs to a previous key's nested block — we already consumed it
                k += 1
                continue
            if ":" not in stripped:
                k += 1
                continue
            sub_key, _, sub_val = stripped.lstrip().partition(":")
            sub_key = sub_key.strip()
            sub_val = sub_val.strip()
            if sub_val:
                sub[sub_key] = _coerce_scalar(sub_val)
                k += 1
            else:
                inner, consumed = _parse_block(lines, k + 1)
                sub[sub_key] = inner
                k += 1 + consumed
        return sub, k - start


def _coerce_scalar(raw: str) -> Any:
    """Coerce a YAML scalar string to bool/int/string."""
    s = raw.strip()
    # Strip matching quotes
    if len(s) >= 2 and ((s[0] == s[-1] == '"') or (s[0] == s[-1] == "'")):
        return s[1:-1]
    low = s.lower()
    if low in ("true", "yes"):
        return True
    if low in ("false", "no"):
        return False
    if low in ("null", "none", "~"):
        return None
    try:
        return int(s)
    except ValueError:
        pass
    try:
        return float(s)
    except ValueError:
        pass
    return s


# ---------------------------------------------------------------------------
# Skill resolution
# ---------------------------------------------------------------------------


def _resolve_skill_path(skill_name: str, cwd: str) -> Path | None:
    """Look up a skill's SKILL.md, project-local first then global."""
    candidates: list[Path] = []
    # Project-local: ./.claude/skills/{name}/SKILL.md (walking up to repo root)
    try:
        current = Path(cwd).resolve()
        for _ in range(20):
            local = current / ".claude" / "skills" / skill_name / "SKILL.md"
            if local.is_file():
                candidates.append(local)
                break
            if current.parent == current:
                break
            current = current.parent
    except (OSError, RuntimeError):
        pass
    # Global: ~/.claude/skills/{name}/SKILL.md
    glob_path = _GLOBAL_SKILLS_DIR / skill_name / "SKILL.md"
    if glob_path.is_file():
        candidates.append(glob_path)

    return candidates[0] if candidates else None


def _load_skill_frame(skill_name: str, cwd: str) -> tuple[str, dict | None]:
    """Load and parse the frame block from a skill's SKILL.md.

    Returns (skill_name, frame_dict_or_None).
    """
    path = _resolve_skill_path(skill_name, cwd)
    if path is None:
        return skill_name, None
    try:
        text = path.read_text()
    except OSError:
        return skill_name, None
    fm = _parse_frontmatter(text)
    if not isinstance(fm, dict):
        return skill_name, None
    frame = fm.get("frame")
    if not isinstance(frame, dict):
        return skill_name, None
    return skill_name, frame


# ---------------------------------------------------------------------------
# Tool-call signature matching
# ---------------------------------------------------------------------------


def _tool_matches_surface(tool_name: str, allowed: list) -> bool:
    """True iff tool_name matches any glob in allowed (fnmatch semantics)."""
    if not allowed:
        return False
    for pat in allowed:
        if not isinstance(pat, str):
            continue
        if fnmatch.fnmatch(tool_name, pat):
            return True
    return False


def _args_compatible(prescribed_args: dict, proposed_args: dict) -> bool:
    """Per SCHEMA: proposed must contain each key in prescribed with the same
    primitive value. Empty/None prescribed args means 'any args accepted'."""
    if not prescribed_args:
        return True
    for k, v in prescribed_args.items():
        if not isinstance(v, (str, int, float, bool, type(None))):
            # Non-primitive constraint: skip strict check (treat as wildcard)
            continue
        if k not in proposed_args:
            return False
        if proposed_args[k] != v:
            return False
    return True


def _matches_next_action(tool_name: str, proposed_args: dict, item: dict) -> bool:
    """True iff (tool_name, proposed_args) matches a next_actions entry."""
    if item.get("tool") != tool_name:
        return False
    return _args_compatible(item.get("args", {}) or {}, proposed_args or {})


# ---------------------------------------------------------------------------
# Main gate
# ---------------------------------------------------------------------------


def decide(
    session_id: str,
    tool_name: str,
    tool_input: dict,
    cwd: str,
) -> dict:
    """Decide whether to allow, block, or initialize-frame for a proposed call.

    Returns a dict suitable for stdout from a PreToolUse hook:
    - {} or {"continue": True}                  -> allow
    - {"decision": "block", "reason": "..."}    -> hard block (preferred PreToolUse shape)

    Side effects:
    - Skill invocations write the parsed frame to phase_state.
    - Manual deviations (proposed_args includes DEVIATION_KEY) are logged and
      allowed through without checks.
    """
    proposed_args = tool_input if isinstance(tool_input, dict) else {}

    # Manual override: log and allow
    if DEVIATION_KEY in proposed_args:
        phase_matcher.record_frame_deviation(
            session_id,
            {
                "type": "manual_override",
                "tool": tool_name,
                "reason": str(proposed_args.get(DEVIATION_KEY, ""))[:200],
            },
        )
        return {}

    # Skill-tool invocation: initialize frame from target skill's SKILL.md
    if tool_name == "Skill":
        skill_name = proposed_args.get("skill") or proposed_args.get("name") or ""
        if isinstance(skill_name, str) and skill_name:
            name, frame = _load_skill_frame(skill_name, cwd)
            if frame is not None:
                phase_matcher.activate_frame(session_id, name, frame)
        return {}

    # Frame consultation
    frame, skill_name = phase_matcher.get_active_frame(session_id)
    if frame is None:
        return {}

    # Surface check
    if frame.get("refuse_outside_surface", True):
        allowed = frame.get("allowed_tools") or []
        if isinstance(allowed, list) and not _tool_matches_surface(tool_name, allowed):
            return {
                "decision": "block",
                "reason": (
                    f"[Prism frame guard] Skill '{skill_name}' is active and its "
                    f"frame.allowed_tools does not include '{tool_name}'. Allowed: "
                    f"{', '.join(str(a) for a in allowed[:10])}. "
                    f"To override for this call only, include "
                    f"{DEVIATION_KEY}=\"<reason>\" in the tool args. "
                    f"To exit the frame, ask the user."
                ),
            }

    # next_actions discipline
    if frame.get("authority") == "next_actions":
        next_actions, run_id = phase_matcher.get_last_next_actions(session_id)
        if next_actions:
            matched = any(
                _matches_next_action(tool_name, proposed_args, item)
                for item in next_actions
            )
            if not matched:
                # Build a compact reason listing the prescribed alternatives
                presc = []
                for item in next_actions[:5]:
                    t = item.get("tool", "?")
                    args = item.get("args") or {}
                    pri = item.get("priority", "?")
                    arg_str = ", ".join(f"{k}={v}" for k, v in list(args.items())[:3])
                    presc.append(f"  P{pri}: {t}({arg_str})")
                presc_text = "\n".join(presc) if presc else "  (none)"
                return {
                    "decision": "block",
                    "reason": (
                        f"[Prism frame guard] Skill '{skill_name}' is active with "
                        f"authority=next_actions, but '{tool_name}' is not in the "
                        f"most recent tool response's next_actions list "
                        f"(run_id={run_id or 'unknown'}). Prescribed next actions:\n"
                        f"{presc_text}\n"
                        f"To override for this call only, include "
                        f"{DEVIATION_KEY}=\"<reason>\" in the tool args. "
                        f"To exit the frame, ask the user."
                    ),
                }

    return {}


# ---------------------------------------------------------------------------
# Hook entry point (called from prism-hook on PreToolUse events)
# ---------------------------------------------------------------------------


def handle_pre_tool_use(data: dict) -> dict:
    """PreToolUse hook handler. Returns block/allow decision."""
    session_id = (
        data.get("session_id")
        or os.environ.get("CLAUDE_SESSION_ID")
        or "unknown"
    )
    tool_name = data.get("tool_name", "")
    tool_input = data.get("tool_input", {}) or {}
    cwd = data.get("cwd", os.getcwd())

    try:
        return decide(session_id, tool_name, tool_input, cwd)
    except Exception:
        # Fail-safe: never block on internal errors
        return {}


if __name__ == "__main__":  # pragma: no cover
    # Standalone debug entry point
    import sys

    raw = sys.stdin.read()
    payload = json.loads(raw) if raw.strip() else {}
    out = handle_pre_tool_use(payload)
    print(json.dumps(out))
