"""Runtime phase matcher — recognizes work phases from tool sequences.

Maintains a sliding window of recent tool calls per session. On each
tool event, abstracts the window to the pattern alphabet and matches
against the discovered pattern catalog. Pure CPU, no LLM calls.

Pattern alphabet:
    R = Read/Grep/Glob (read)
    W = Edit/Write (write)
    X = Bash (execute)
    A = Agent (delegate)
    M = MCP tool (mcp__*)
    ? = other

Run-length encoding:
    R   = 1-3 reads
    R+  = 4-8 reads
    R++ = 9+ reads
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from . import engine
from .message_classifier import classify as classify_message

# ---------------------------------------------------------------------------
# Pattern catalog (loaded once from analysis output)
# ---------------------------------------------------------------------------

_CATALOG_PATH = Path(__file__).resolve().parent.parent.parent / "analysis" / "patterns.json"
_catalog: dict | None = None


def _load_catalog() -> dict:
    """Load pattern catalog from disk. Cached after first load."""
    global _catalog
    if _catalog is not None:
        return _catalog
    if _CATALOG_PATH.is_file():
        try:
            raw = json.loads(_CATALOG_PATH.read_text())
            _catalog = raw if isinstance(raw, dict) else {}
        except (json.JSONDecodeError, OSError):
            _catalog = {}
    else:
        _catalog = {}
    return _catalog


def _get_phase_archetypes() -> dict[str, dict]:
    """Get phase archetype patterns from catalog."""
    cat = _load_catalog()
    return cat.get("phase_archetypes", {})


# ---------------------------------------------------------------------------
# Tool abstraction
# ---------------------------------------------------------------------------

_TOOL_TO_SYMBOL = {
    "Read": "R",
    "Grep": "R",
    "Glob": "R",
    "Edit": "W",
    "Write": "W",
    "NotebookEdit": "W",
    "Bash": "X",
    "Agent": "A",
}

# Meta-tools that don't contribute to phase patterns
_SKIP_TOOLS = frozenset(
    {
        "ToolSearch",
        "TaskCreate",
        "TaskUpdate",
        "TaskGet",
        "TaskList",
        "TaskOutput",
        "TaskStop",
    }
)


def _tool_to_symbol(tool_name: str) -> str | None:
    """Map a tool name to its abstract symbol. None = skip."""
    if tool_name in _SKIP_TOOLS:
        return None
    if tool_name in _TOOL_TO_SYMBOL:
        return _TOOL_TO_SYMBOL[tool_name]
    if tool_name.startswith("mcp__"):
        return "M"
    return "?"


def _encode_runs(symbols: list[str]) -> str:
    """Run-length encode a symbol sequence.

    Collapses consecutive identical symbols into:
    X   = 1-3 occurrences
    X+  = 4-8 occurrences
    X++ = 9+ occurrences
    """
    if not symbols:
        return ""
    runs: list[str] = []
    i = 0
    while i < len(symbols):
        ch = symbols[i]
        run_len = 1
        while i + run_len < len(symbols) and symbols[i + run_len] == ch:
            run_len += 1
        if run_len <= 3:
            runs.append(ch)
        elif run_len <= 8:
            runs.append(f"{ch}+")
        else:
            runs.append(f"{ch}++")
        i += run_len
    return "".join(runs)


# ---------------------------------------------------------------------------
# Session phase state (per-session, in-memory + disk-backed)
# ---------------------------------------------------------------------------

# State file location
_STATE_DIR = engine.PRISM_DIR / "phase_state"


@dataclass
class PhaseState:
    """Sliding window of recent tool calls for phase detection."""

    tools: list[str] = field(default_factory=list)  # Raw tool names
    symbols: list[str] = field(default_factory=list)  # Abstract symbols
    tool_inputs: list[str] = field(default_factory=list)  # Summarized inputs
    user_text: str = ""  # Latest user prompt text
    user_text_class: str = ""  # directive | continuation | clarification
    user_text_confidence: float = 0.0  # Classifier confidence
    files_active: list[str] = field(default_factory=list)  # Recent file paths
    current_pattern: str = ""  # Current matched pattern key
    current_match: dict | None = None  # Matched archetype data
    tool_count: int = 0  # Total tools this session
    tools_at_last_directive: int = 0  # Tool count when last directive arrived

    # Window size — keep last 30 tools for matching
    WINDOW_SIZE: int = 30


def _state_path(session_id: str) -> Path:
    return _STATE_DIR / f"{session_id}.json"


def _load_state(session_id: str) -> PhaseState:
    """Load or create session phase state."""
    path = _state_path(session_id)
    if path.is_file():
        try:
            data = json.loads(path.read_text())
            state = PhaseState()
            state.tools = data.get("tools", [])
            state.symbols = data.get("symbols", [])
            state.tool_inputs = data.get("tool_inputs", [])
            state.user_text = data.get("user_text", "")
            state.user_text_class = data.get("user_text_class", "")
            state.user_text_confidence = data.get("user_text_confidence", 0.0)
            state.files_active = data.get("files_active", [])
            state.current_pattern = data.get("current_pattern", "")
            state.tool_count = data.get("tool_count", 0)
            state.tools_at_last_directive = data.get("tools_at_last_directive", 0)
            return state
        except (json.JSONDecodeError, OSError):
            pass
    return PhaseState()


def _save_state(session_id: str, state: PhaseState) -> None:
    """Persist session phase state to disk."""
    _STATE_DIR.mkdir(parents=True, exist_ok=True)
    data = {
        "tools": state.tools[-state.WINDOW_SIZE :],
        "symbols": state.symbols[-state.WINDOW_SIZE :],
        "tool_inputs": state.tool_inputs[-state.WINDOW_SIZE :],
        "user_text": state.user_text,
        "user_text_class": state.user_text_class,
        "user_text_confidence": state.user_text_confidence,
        "files_active": state.files_active[-15:],
        "current_pattern": state.current_pattern,
        "tool_count": state.tool_count,
        "tools_at_last_directive": state.tools_at_last_directive,
    }
    _state_path(session_id).write_text(json.dumps(data, separators=(",", ":"), default=str))


# ---------------------------------------------------------------------------
# Pattern matching
# ---------------------------------------------------------------------------


def _match_pattern(symbols: list[str]) -> tuple[str, dict | None]:
    """Match the current symbol window against known patterns.

    Tries progressively shorter suffixes of the window to find the
    longest matching pattern. Returns (pattern_key, archetype_data)
    or ("", None) if no match.
    """
    archetypes = _get_phase_archetypes()
    if not archetypes:
        return "", None

    # Try suffixes from full window down to last 2 symbols
    for start in range(max(0, len(symbols) - 20), len(symbols) - 1):
        suffix = symbols[start:]
        encoded = _encode_runs(suffix)
        if encoded in archetypes:
            return encoded, archetypes[encoded]

    return "", None


# ---------------------------------------------------------------------------
# Public API (called from hooks)
# ---------------------------------------------------------------------------


def record_tool(session_id: str, tool_name: str, tool_input_summary: str) -> str:
    """Record a tool call and return the current phase pattern key.

    Called from PostToolUse hook. Returns the matched pattern key
    (e.g., "RWX", "X++") or "" if no match.
    """
    symbol = _tool_to_symbol(tool_name)
    if symbol is None:
        return ""  # Meta-tool, skip

    state = _load_state(session_id)
    state.tools.append(tool_name)
    state.symbols.append(symbol)
    state.tool_inputs.append(tool_input_summary)
    state.tool_count += 1

    # Track active files
    if tool_name in ("Read", "Edit", "Write") and "/" in tool_input_summary:
        if tool_input_summary not in state.files_active:
            state.files_active.append(tool_input_summary)

    # Trim window
    if len(state.tools) > state.WINDOW_SIZE:
        state.tools = state.tools[-state.WINDOW_SIZE :]
        state.symbols = state.symbols[-state.WINDOW_SIZE :]
        state.tool_inputs = state.tool_inputs[-state.WINDOW_SIZE :]

    # Match
    pattern, match = _match_pattern(state.symbols)
    state.current_pattern = pattern
    state.current_match = match

    _save_state(session_id, state)
    return pattern


def record_user_text(session_id: str, text: str) -> dict:
    """Record the latest user prompt text + classification.

    Called from UserPromptSubmit hook. Returns the classification dict
    so the hook can log it as a structured event for offline analysis.
    """
    state = _load_state(session_id)
    state.user_text = text[:500]  # Cap stored text

    result = classify_message(text)
    state.user_text_class = result.label
    state.user_text_confidence = result.confidence

    # Directive → mark phase boundary (used by proactive-compaction logic later)
    if result.label == "directive" and result.confidence >= 0.4:
        state.tools_at_last_directive = state.tool_count

    _save_state(session_id, state)
    return result.to_dict()


def build_narrative_frame(session_id: str) -> str:
    """Build a compact focus string for context compaction.

    Called from PreCompact hook. Constructs a narrative frame from:
    - Current phase pattern (what kind of work is happening)
    - User's last prompt (what they asked for)
    - Active files (what's being worked on)

    Returns a focus string suitable for /compact <focus>.
    """
    state = _load_state(session_id)

    parts: list[str] = []

    # Phase description
    if state.current_pattern and state.current_match:
        match = state.current_match
        mode = match.get("dominant_mode", "")
        intents = list(match.get("top_intents", {}).keys())[:2]
        intent_str = "/".join(intents) if intents else mode
        parts.append(f"Work phase: {intent_str} ({state.current_pattern})")
    elif state.symbols:
        # No catalog match — describe from raw symbols
        encoded = _encode_runs(state.symbols[-15:])
        mode = _infer_mode_from_symbols(state.symbols[-15:])
        parts.append(f"Work phase: {mode} ({encoded})")

    # User intent (labeled by classifier if available)
    if state.user_text:
        # Take first sentence or first 120 chars
        text = state.user_text
        for sep in (".", "\n", "!"):
            idx = text.find(sep)
            if 10 < idx < 120:
                text = text[: idx + 1]
                break
        else:
            text = text[:120]
        label_map = {
            "directive": "User directive",
            "continuation": "User followup",
            "clarification": "User question",
        }
        label = label_map.get(state.user_text_class, "User request")
        parts.append(f"{label}: {text.strip()}")

    # Active files
    if state.files_active:
        # Show last 5 files, basenames only
        recent = state.files_active[-5:]
        basenames = [f.rsplit("/", 1)[-1] for f in recent]
        parts.append(f"Files: {', '.join(basenames)}")

    # Tool count for context
    parts.append(f"Tools used: {state.tool_count}")

    # Next-action hint (heuristic, conservative)
    hint = _suggest_next_action(state.symbols[-10:])
    if hint:
        parts.append(f"Next: {hint}")

    return " | ".join(parts) if parts else ""


def _suggest_next_action(symbols: list[str], recent_errors: int = 0) -> str:
    """Heuristic next-action hint based on recent symbol pattern.

    Returns a short phrase suitable for appending to the narrative frame,
    or "" when no confident suggestion is available. Errs toward silence.
    """
    if len(symbols) < 3:
        return ""
    tail = symbols[-6:]
    tail_str = "".join(tail)

    # Recent error → suggest reading output/logs
    if recent_errors >= 2:
        return "diagnose errors before continuing"

    # Read-heavy tail with no execution yet — ready to edit
    if tail.count("R") >= 4 and "W" not in tail and "X" not in tail:
        return "start editing once exploration is complete"

    # Edit tail with no recent execution → run tests
    if tail.count("W") >= 2 and "X" not in tail[-4:]:
        return "run tests to verify the edits"

    # Execution tail → review results
    if tail.count("X") >= 3 and "R" not in tail[-3:]:
        return "review results before next edit"

    # Mixed RWX rhythm already established — keep going
    if "R" in tail_str and "W" in tail_str and "X" in tail_str:
        return ""  # Healthy implement loop — no hint needed

    return ""


def _infer_mode_from_symbols(symbols: list[str]) -> str:
    """Quick mode inference from abstract symbols."""
    if not symbols:
        return "idle"
    counts = Counter(symbols)
    total = len(symbols)
    r_pct = counts.get("R", 0) / total
    w_pct = counts.get("W", 0) / total
    x_pct = counts.get("X", 0) / total
    a_pct = counts.get("A", 0) / total

    if a_pct > 0.2:
        return "delegating"
    if r_pct > 0.5 and w_pct < 0.1:
        return "exploring"
    if w_pct > 0.3:
        return "implementing"
    if x_pct > 0.4:
        return "executing"
    return "mixed"


def get_phase_summary(session_id: str) -> dict:
    """Get current phase state for diagnostics."""
    state = _load_state(session_id)
    return {
        "tool_count": state.tool_count,
        "window_size": len(state.symbols),
        "current_pattern": state.current_pattern,
        "current_mode": _infer_mode_from_symbols(state.symbols[-15:]),
        "active_files": state.files_active[-5:],
        "user_text_preview": state.user_text[:80] if state.user_text else "",
        "user_text_class": state.user_text_class,
        "user_text_confidence": state.user_text_confidence,
        "tools_since_directive": state.tool_count - state.tools_at_last_directive,
        "encoded_window": _encode_runs(state.symbols[-15:]),
    }
