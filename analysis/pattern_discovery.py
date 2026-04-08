"""Session pattern discovery for intelligent context management.

Extracts behavioral patterns from the full Claude Code session corpus
by correlating tool sequences with natural language text. Produces a
pattern catalog for encoding into CPU-based runtime matchers.

Usage:
    .venv/bin/python analysis/pattern_discovery.py [options]

Options:
    --since DAYS        Only analyze sessions from the last N days (default: all)
    --project FILTER    Filter to project name substring
    --output PATH       Output path for patterns.json (default: analysis/patterns.json)
    --include-subagents Include subagent session files
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Iterator

# Prism sources — reuse session discovery, not parsing (it discards text)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from prism.sources import _matching_projects  # noqa: E402

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class ToolUse:
    name: str
    summary: str
    timestamp: float


@dataclass
class Exchange:
    user_text: str
    assistant_text: str
    tool_sequence: list[str]
    tool_details: list[ToolUse]
    timestamp: float
    token_usage: dict
    session_id: str
    project: str
    exchange_index: int


@dataclass
class SubagentRecord:
    agent_id: str
    agent_type: str  # "compact" or "spawned"
    task_prompt: str  # Initial Agent prompt (from first tool_result)
    assistant_text: str  # All assistant natural language
    tool_sequence: list[str]
    tool_details: list[ToolUse]
    token_usage: dict
    timestamp_start: float
    timestamp_end: float
    parent_session_id: str
    project: str


@dataclass
class SessionRecord:
    session_id: str
    project: str
    exchanges: list[Exchange]
    subagents: list[SubagentRecord]
    timestamp_start: float
    timestamp_end: float


# ---------------------------------------------------------------------------
# Phase 1: Extraction
# ---------------------------------------------------------------------------


def extract_user_text(content: object) -> str:
    """Extract natural language from user message content.

    Handles string content (direct text) and list content (text + tool_result
    blocks). Only text blocks are user speech; tool_result blocks are skipped.
    """
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
            elif isinstance(block, str):
                parts.append(block)
        return "\n".join(parts).strip()
    return ""


def extract_assistant_text(content: object) -> str:
    """Extract natural language text from assistant message content.

    Only 'text' type blocks. Skips tool_use, thinking, server_tool_use.
    """
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(block.get("text", ""))
    return "\n".join(parts).strip()


def _is_substantive_user(content: object) -> bool:
    """True if content has user-authored text (not pure tool results)."""
    if isinstance(content, str):
        return bool(content.strip())
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                return True
            if isinstance(block, str) and block.strip():
                return True
    return False


def summarize_tool_input(tool_name: str, input_data: object) -> str:
    """One-line summary of a tool invocation's input."""
    if not isinstance(input_data, dict):
        return str(input_data)[:60]

    if tool_name in ("Read", "Edit", "Write", "NotebookEdit"):
        return input_data.get("file_path", input_data.get("path", ""))
    if tool_name == "Bash":
        return input_data.get("command", "")[:80]
    if tool_name in ("Grep", "Glob"):
        pat = input_data.get("pattern", "")
        path = input_data.get("path", ".")
        return f"{pat} in {path}"
    if tool_name == "Agent":
        return input_data.get("prompt", "")[:60]
    if tool_name == "ToolSearch":
        return input_data.get("query", "")[:60]
    if tool_name.startswith("mcp__"):
        # MCP tools: grab first string value as summary
        for v in input_data.values():
            if isinstance(v, str) and v:
                return v[:60]
    return str(input_data)[:60]


def _parse_ts(ts_str: str | None) -> float:
    """Parse ISO 8601 timestamp to Unix epoch float."""
    if not ts_str:
        return 0.0
    try:
        return datetime.fromisoformat(ts_str.replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError):
        return 0.0


def iter_exchanges(path: Path, project: str) -> Iterator[Exchange]:
    """Stream a JSONL session file and group into Exchange records.

    State machine: a substantive user message starts a new exchange.
    Assistant messages accumulate text and tool calls into the current
    exchange. User messages with only tool_result blocks belong to the
    current exchange (they're tool outputs, not new prompts).
    """
    session_id = path.stem

    # Current exchange accumulator
    cur_user_text = ""
    cur_assistant_text_parts: list[str] = []
    cur_tools: list[str] = []
    cur_tool_details: list[ToolUse] = []
    cur_ts = 0.0
    cur_usage: dict = {}
    exchange_idx = 0
    in_exchange = False

    def _flush() -> Exchange | None:
        nonlocal in_exchange, exchange_idx
        if not in_exchange:
            return None
        ex = Exchange(
            user_text=cur_user_text,
            assistant_text="\n".join(cur_assistant_text_parts).strip(),
            tool_sequence=cur_tools[:],
            tool_details=cur_tool_details[:],
            timestamp=cur_ts,
            token_usage=cur_usage.copy(),
            session_id=session_id,
            project=project,
            exchange_index=exchange_idx,
        )
        exchange_idx += 1
        in_exchange = False
        return ex

    try:
        text = path.read_text(errors="replace")
    except OSError:
        return

    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue

        msg_type = obj.get("type")
        ts = _parse_ts(obj.get("timestamp"))

        if msg_type == "user" and not obj.get("isSidechain"):
            content = obj.get("message", {}).get("content", "")
            if _is_substantive_user(content):
                # Flush previous exchange
                prev = _flush()
                if prev is not None:
                    yield prev
                # Start new exchange
                cur_user_text = extract_user_text(content)
                cur_assistant_text_parts = []
                cur_tools = []
                cur_tool_details = []
                cur_ts = ts
                cur_usage = {}
                in_exchange = True

        elif msg_type == "assistant":
            content = obj.get("message", {}).get("content", [])
            # Accumulate text
            asst_text = extract_assistant_text(content)
            if asst_text:
                cur_assistant_text_parts.append(asst_text)
            # Accumulate tool calls
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_use":
                        name = block.get("name", "unknown")
                        cur_tools.append(name)
                        cur_tool_details.append(ToolUse(
                            name=name,
                            summary=summarize_tool_input(name, block.get("input", {})),
                            timestamp=ts,
                        ))
            # Accumulate token usage
            usage = obj.get("message", {}).get("usage", {})
            if usage:
                for key in ("input_tokens", "output_tokens",
                            "cache_creation_input_tokens", "cache_read_input_tokens"):
                    cur_usage[key] = cur_usage.get(key, 0) + usage.get(key, 0)

    # Flush final exchange
    last = _flush()
    if last is not None:
        yield last


def _extract_tool_result_text(content: object) -> str:
    """Extract text from tool_result blocks (used for subagent initial prompts)."""
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict) or block.get("type") != "tool_result":
            continue
        inner = block.get("content", "")
        if isinstance(inner, str):
            parts.append(inner)
        elif isinstance(inner, list):
            for sub in inner:
                if isinstance(sub, dict) and sub.get("type") == "text":
                    parts.append(sub.get("text", ""))
                elif isinstance(sub, str):
                    parts.append(sub)
    return "\n".join(parts).strip()


def parse_subagent(path: Path, parent_session_id: str, project: str) -> SubagentRecord | None:
    """Parse a subagent JSONL file into a SubagentRecord.

    Subagents have no user prompts — their "user" messages are all tool_results.
    We extract:
    - task_prompt: text from the FIRST tool_result (the Agent's initial prompt)
    - assistant_text: all assistant natural language blocks
    - tool_sequence: all tool calls made by the subagent
    """
    agent_id = path.stem
    agent_type = "compact" if "compact" in agent_id else "spawned"

    try:
        text = path.read_text(errors="replace")
    except OSError:
        return None

    task_prompt = ""
    assistant_parts: list[str] = []
    tools: list[str] = []
    tool_details: list[ToolUse] = []
    usage: dict = {}
    ts_start = 0.0
    ts_end = 0.0
    got_task_prompt = False

    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue

        ts = _parse_ts(obj.get("timestamp"))
        if ts > 0:
            if ts_start == 0.0:
                ts_start = ts
            ts_end = ts

        msg_type = obj.get("type")

        if msg_type == "user" and not got_task_prompt:
            content = obj.get("message", {}).get("content", "")
            # First user message: extract task prompt from tool_result or text
            extracted = extract_user_text(content)
            if not extracted:
                extracted = _extract_tool_result_text(content)
            if extracted:
                task_prompt = extracted[:500]
                got_task_prompt = True

        elif msg_type == "assistant":
            content = obj.get("message", {}).get("content", [])
            asst_text = extract_assistant_text(content)
            if asst_text:
                assistant_parts.append(asst_text)
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_use":
                        name = block.get("name", "unknown")
                        tools.append(name)
                        tool_details.append(ToolUse(
                            name=name,
                            summary=summarize_tool_input(name, block.get("input", {})),
                            timestamp=ts,
                        ))
            msg_usage = obj.get("message", {}).get("usage", {})
            if msg_usage:
                for key in ("input_tokens", "output_tokens",
                            "cache_creation_input_tokens", "cache_read_input_tokens"):
                    usage[key] = usage.get(key, 0) + msg_usage.get(key, 0)

    if not tools and not assistant_parts:
        return None

    return SubagentRecord(
        agent_id=agent_id,
        agent_type=agent_type,
        task_prompt=task_prompt,
        assistant_text="\n".join(assistant_parts[:10]).strip()[:2000],  # Cap size
        tool_sequence=tools,
        tool_details=tool_details,
        token_usage=usage,
        timestamp_start=ts_start,
        timestamp_end=ts_end,
        parent_session_id=parent_session_id,
        project=project,
    )


def extract_corpus(
    since: datetime | None = None,
    project_filter: str | None = None,
    include_subagents: bool = False,
) -> list[SessionRecord]:
    """Extract all sessions into SessionRecord objects."""
    records: list[SessionRecord] = []

    for proj_dir, proj in _matching_projects(project_filter):
        for jsonl_file in sorted(proj_dir.glob("*.jsonl")):
            if since:
                try:
                    mtime = datetime.fromtimestamp(jsonl_file.stat().st_mtime, tz=UTC)
                    if mtime < since:
                        continue
                except OSError:
                    continue

            exchanges = list(iter_exchanges(jsonl_file, proj))
            if not exchanges:
                continue

            subagents: list[SubagentRecord] = []
            if include_subagents:
                sub_dir = proj_dir / jsonl_file.stem / "subagents"
                if sub_dir.is_dir():
                    for sub_file in sorted(sub_dir.glob("*.jsonl")):
                        sub = parse_subagent(sub_file, jsonl_file.stem, proj)
                        if sub:
                            subagents.append(sub)

            records.append(SessionRecord(
                session_id=jsonl_file.stem,
                project=proj,
                exchanges=exchanges,
                subagents=subagents,
                timestamp_start=exchanges[0].timestamp,
                timestamp_end=exchanges[-1].timestamp,
            ))

    return records


# ---------------------------------------------------------------------------
# Phase 2: Analysis
# ---------------------------------------------------------------------------


def tool_ngrams(
    exchanges: list[Exchange],
    n_range: range = range(1, 5),
    min_count: int = 3,
    subagent_seqs: list[list[str]] | None = None,
) -> dict[int, list[tuple[tuple[str, ...], int]]]:
    """Compute n-gram frequencies of tool sequences across exchanges + subagents."""
    counters: dict[int, Counter] = {n: Counter() for n in n_range}

    all_seqs = [ex.tool_sequence for ex in exchanges]
    if subagent_seqs:
        all_seqs.extend(subagent_seqs)

    for seq in all_seqs:
        for n in n_range:
            for i in range(len(seq) - n + 1):
                gram = tuple(seq[i : i + n])
                counters[n][gram] += 1

    return {
        n: [(gram, count) for gram, count in counter.most_common() if count >= min_count]
        for n, counter in counters.items()
    }


def classify_mode(tool_sequence: list[str]) -> str:
    """Classify an exchange's workflow mode from its tool sequence.

    Extends behavior.py's _infer_workflow_mode to per-exchange granularity.
    """
    total = len(tool_sequence)
    if total == 0:
        return "conversational"

    counts: Counter = Counter(tool_sequence)
    reads = sum(counts.get(t, 0) for t in ("Read", "Grep", "Glob"))
    edits = sum(counts.get(t, 0) for t in ("Edit", "Write"))
    read_pct = reads / total
    edit_pct = edits / total
    bash_pct = counts.get("Bash", 0) / total
    agent_pct = counts.get("Agent", 0) / total
    grep_pct = counts.get("Grep", 0) / total
    glob_pct = counts.get("Glob", 0) / total

    if agent_pct > 0.2:
        return "delegate"
    if read_pct > 0.5 and edit_pct < 0.1:
        return "explore"
    if edit_pct > 0.3:
        return "implement"
    if bash_pct > 0.3 and grep_pct > 0.1:
        return "debug"
    if (grep_pct + glob_pct) > 0.5:
        return "navigate"
    if bash_pct > 0.4:
        return "shell"
    return "mixed"


# Common imperative verbs in Claude Code user prompts
_VERBS = frozenset({
    "fix", "add", "implement", "update", "refactor", "review", "check",
    "show", "find", "debug", "test", "run", "create", "move", "rename",
    "delete", "remove", "change", "make", "build", "write", "read",
    "look", "explain", "help", "try", "use", "install", "deploy",
    "merge", "push", "pull", "commit", "revert", "clean", "format",
    "lint", "optimize", "improve", "search", "replace", "set", "get",
    "list", "print", "log", "trace", "profile", "analyze", "audit",
    "generate", "convert", "extract", "parse", "validate", "verify",
})

# Words to skip when looking for the leading verb
_SKIP_WORDS = frozenset({
    "please", "can", "could", "would", "should", "let's", "lets",
    "now", "ok", "okay", "yes", "yeah", "sure", "go", "hey",
    "i", "we", "you", "the", "a", "an", "this", "that",
})


def _extract_verb(text: str) -> str | None:
    """Extract the leading imperative verb from user text."""
    words = text.lower().split()
    for word in words[:4]:
        # Strip punctuation
        clean = word.strip(".,!?:;\"'()[]{}—-/")
        if clean in _VERBS:
            return clean
        if clean not in _SKIP_WORDS:
            break
    return None


def verb_correlations(exchanges: list[Exchange]) -> dict[str, Counter]:
    """Correlate user text verbs with exchange workflow modes."""
    result: dict[str, Counter] = defaultdict(Counter)
    for ex in exchanges:
        verb = _extract_verb(ex.user_text)
        if verb:
            mode = classify_mode(ex.tool_sequence)
            result[verb][mode] += 1
    return dict(result)


def detect_phase_transitions(session: SessionRecord) -> list[dict]:
    """Detect mode transitions within a session."""
    if len(session.exchanges) < 2:
        return []

    transitions: list[dict] = []
    prev_mode = classify_mode(session.exchanges[0].tool_sequence)

    for i, ex in enumerate(session.exchanges[1:], 1):
        mode = classify_mode(ex.tool_sequence)
        if mode != prev_mode:
            transitions.append({
                "index": i,
                "from": prev_mode,
                "to": mode,
                "trigger_text": ex.user_text[:120],
            })
        prev_mode = mode

    return transitions


# ---------------------------------------------------------------------------
# Phase 2b: Work-phase extraction and archetype discovery
# ---------------------------------------------------------------------------

# Intent keyword clusters — detect what the user is TRYING to do
_INTENT_KEYWORDS: dict[str, frozenset[str]] = {
    "fix_bug": frozenset({
        "fix", "bug", "broken", "crash", "doesn't work", "not working",
        "regression",
    }),
    "build_feature": frozenset({
        "add", "implement", "create", "new feature", "build", "support",
        "enable", "introduce", "extend",
    }),
    "refactor": frozenset({
        "refactor", "clean up", "reorganize", "rename", "move", "split",
        "extract", "simplify", "restructure", "consolidate",
    }),
    "debug": frozenset({
        "debug", "trace", "why", "investigate", "diagnose", "figure out",
        "what's happening", "root cause",
    }),
    "setup_config": frozenset({
        "install", "setup", "configure", "deploy", "init", "bootstrap",
        "dependency", "config",
    }),
    "explore_review": frozenset({
        "review", "audit", "explain", "understand", "how does",
        "what does", "show me", "walk me through",
    }),
    "test": frozenset({
        "test", "coverage", "mutation", "spec", "pytest", "verify",
    }),
    "git_ops": frozenset({
        "commit", "push", "merge", "branch", "rebase", "pr",
        "pull request",
    }),
}


def _detect_intents(text: str) -> list[str]:
    """Detect intent categories from user text via keyword matching."""
    text_lower = text.lower()
    found: list[str] = []
    for intent, keywords in _INTENT_KEYWORDS.items():
        for kw in keywords:
            if kw in text_lower:
                found.append(intent)
                break
    return found


@dataclass
class WorkPhase:
    """A contiguous run of 1-10 exchanges forming a coherent work unit."""
    exchanges: list[Exchange]
    mode: str  # Dominant mode across exchanges
    tool_signature: str  # Compressed tool pattern, e.g. "Read×3→Edit×2→Bash"
    tool_sequence: list[str]  # Raw tool sequence (all tools concatenated)
    intents: list[str]  # Detected from user text
    user_texts: list[str]  # User prompts in this phase
    assistant_summary: str  # First significant assistant text
    files_touched: list[str]  # Unique files
    exchange_count: int
    tool_count: int
    session_id: str
    project: str


def _compress_tool_sequence(tools: list[str]) -> str:
    """Compress a tool sequence into a readable signature.

    ["Read","Read","Read","Edit","Edit","Bash"] → "Read×3→Edit×2→Bash"
    """
    if not tools:
        return ""
    runs: list[tuple[str, int]] = []
    current = tools[0]
    count = 1
    for t in tools[1:]:
        if t == current:
            count += 1
        else:
            runs.append((current, count))
            current = t
            count = 1
    runs.append((current, count))

    parts: list[str] = []
    for tool, n in runs:
        # Shorten MCP tool names
        short = tool.split("__")[-1] if "__" in tool else tool
        parts.append(f"{short}×{n}" if n > 1 else short)
    return "→".join(parts)


def _abstract_tool_sequence(tools: list[str]) -> list[str]:
    """Abstract tool names to categories for fuzzy pattern matching.

    Read, Grep, Glob → R (read)
    Edit, Write → W (write)
    Bash → X (execute)
    Agent → A (agent)
    mcp__* → M (mcp)
    ToolSearch, Task* → _ (meta, ignored)
    """
    mapping = {
        "Read": "R", "Grep": "R", "Glob": "R",
        "Edit": "W", "Write": "W", "NotebookEdit": "W",
        "Bash": "X",
        "Agent": "A",
    }
    result: list[str] = []
    for t in tools:
        if t in mapping:
            result.append(mapping[t])
        elif t.startswith("mcp__"):
            result.append("M")
        elif t.startswith("Task") or t == "ToolSearch":
            continue  # Skip meta-tools
        else:
            result.append("?")
    return result


def extract_work_phases(session: SessionRecord) -> list[WorkPhase]:
    """Split a session into work phases at natural boundaries.

    Boundaries are:
    - A "conversational" exchange (no tools) after tool-heavy exchanges
    - A mode transition between tool-heavy exchanges
    - A gap of 2+ consecutive conversational exchanges

    Each phase contains 1-10 exchanges of coherent work.
    """
    exchanges = session.exchanges
    if not exchanges:
        return []

    phases: list[WorkPhase] = []
    current_phase: list[Exchange] = []

    def _flush_phase() -> None:
        if not current_phase:
            return
        # Skip phases that are purely conversational (no tools at all)
        all_tools: list[str] = []
        for ex in current_phase:
            all_tools.extend(ex.tool_sequence)
        if not all_tools:
            return

        # Compute phase features
        mode_counts: Counter = Counter()
        for ex in current_phase:
            mode_counts[classify_mode(ex.tool_sequence)] += 1
        # Remove conversational from dominant mode calc (it's filler)
        non_conv = {m: c for m, c in mode_counts.items() if m != "conversational"}
        dominant = max(non_conv, key=lambda m: non_conv[m]) if non_conv else "mixed"

        intents: list[str] = []
        user_texts: list[str] = []
        files: set[str] = set()
        asst_summary = ""

        for ex in current_phase:
            if ex.user_text:
                user_texts.append(ex.user_text)
                intents.extend(_detect_intents(ex.user_text))
            if not asst_summary and ex.assistant_text:
                asst_summary = ex.assistant_text[:200]
            for td in ex.tool_details:
                if td.name in ("Read", "Edit", "Write") and "/" in td.summary:
                    files.add(td.summary)

        phases.append(WorkPhase(
            exchanges=current_phase[:],
            mode=dominant,
            tool_signature=_compress_tool_sequence(all_tools),
            tool_sequence=all_tools,
            intents=list(dict.fromkeys(intents)),  # Deduplicate, preserve order
            user_texts=user_texts,
            assistant_summary=asst_summary,
            files_touched=sorted(files),
            exchange_count=len(current_phase),
            tool_count=len(all_tools),
            session_id=session.session_id[:12],
            project=session.project,
        ))

    conv_streak = 0
    prev_mode = ""

    for ex in exchanges:
        mode = classify_mode(ex.tool_sequence)

        if mode == "conversational":
            conv_streak += 1
            # 2+ consecutive conversational = boundary
            if conv_streak >= 2 and current_phase:
                _flush_phase()
                current_phase = []
                prev_mode = ""
            continue
        else:
            # A single conversational exchange between tool phases is filler,
            # but a mode TRANSITION is a boundary
            if prev_mode and mode != prev_mode and current_phase:
                _flush_phase()
                current_phase = []

            conv_streak = 0
            current_phase.append(ex)
            prev_mode = mode

            # Cap phase length at 10 exchanges
            if len(current_phase) >= 10:
                _flush_phase()
                current_phase = []
                prev_mode = ""

    _flush_phase()
    return phases


def discover_phase_archetypes(
    corpus: list[SessionRecord],
) -> tuple[list[WorkPhase], dict]:
    """Extract all work phases from the corpus and find recurring patterns."""
    all_phases: list[WorkPhase] = []
    for session in corpus:
        all_phases.extend(extract_work_phases(session))

    # --- Abstract pattern frequencies ---
    # Convert tool sequences to abstract form for fuzzy grouping
    abstract_counter: Counter = Counter()
    phase_by_abstract: dict[str, list[WorkPhase]] = defaultdict(list)

    for phase in all_phases:
        abstract = "".join(_abstract_tool_sequence(phase.tool_sequence))
        # Collapse runs for fuzzy matching: "RRRWWX" → "R+W+X"
        collapsed = ""
        for ch in abstract:
            if not collapsed or ch != collapsed[-1]:
                collapsed += ch
            # else: run continues, already represented
        # Add run-length hint: short (1-3), medium (4-8), long (9+)
        runs: list[str] = []
        i = 0
        while i < len(abstract):
            ch = abstract[i]
            run_len = 1
            while i + run_len < len(abstract) and abstract[i + run_len] == ch:
                run_len += 1
            if run_len <= 3:
                runs.append(ch)
            elif run_len <= 8:
                runs.append(f"{ch}+")
            else:
                runs.append(f"{ch}++")
            i += run_len
        pattern_key = "".join(runs)
        abstract_counter[pattern_key] += 1
        phase_by_abstract[pattern_key].append(phase)

    # --- Build archetype stats for patterns appearing 3+ times ---
    archetype_stats: dict[str, dict] = {}
    for pattern, count in abstract_counter.most_common():
        if count < 3:
            continue
        phases_in = phase_by_abstract[pattern]

        intent_agg: Counter = Counter()
        mode_agg: Counter = Counter()
        verb_agg: Counter = Counter()
        for p in phases_in:
            for intent in p.intents:
                intent_agg[intent] += 1
            mode_agg[p.mode] += 1
            for ut in p.user_texts:
                v = _extract_verb(ut)
                if v:
                    verb_agg[v] += 1

        # Example tool signatures (most common concrete patterns)
        sig_counter: Counter = Counter(p.tool_signature for p in phases_in)

        archetype_stats[pattern] = {
            "count": count,
            "pct": round(count / max(len(all_phases), 1), 3),
            "avg_exchanges": round(
                sum(p.exchange_count for p in phases_in) / count, 1
            ),
            "avg_tools": round(
                sum(p.tool_count for p in phases_in) / count, 1
            ),
            "dominant_mode": mode_agg.most_common(1)[0][0] if mode_agg else "?",
            "mode_distribution": dict(mode_agg.most_common()),
            "top_intents": dict(intent_agg.most_common(5)),
            "top_verbs": dict(verb_agg.most_common(5)),
            "example_signatures": [
                sig for sig, _ in sig_counter.most_common(3)
            ],
            "avg_files": round(
                sum(len(p.files_touched) for p in phases_in) / count, 1
            ),
        }

    return all_phases, archetype_stats


# ---------------------------------------------------------------------------
# Phase 3: Output
# ---------------------------------------------------------------------------


def _mode_stats(exchanges: list[Exchange]) -> list[dict]:
    """Per-mode statistics with signature tools and common verbs."""
    by_mode: dict[str, list[Exchange]] = defaultdict(list)
    for ex in exchanges:
        mode = classify_mode(ex.tool_sequence)
        by_mode[mode].append(ex)

    stats: list[dict] = []
    total = len(exchanges)
    for mode, exs in sorted(by_mode.items(), key=lambda x: -len(x[1])):
        tool_counts: Counter = Counter()
        verb_counts: Counter = Counter()
        bigram_counts: Counter = Counter()
        total_tools = 0
        for ex in exs:
            for t in ex.tool_sequence:
                tool_counts[t] += 1
                total_tools += 1
            verb = _extract_verb(ex.user_text)
            if verb:
                verb_counts[verb] += 1
            seq = ex.tool_sequence
            for i in range(len(seq) - 1):
                bigram_counts[(seq[i], seq[i + 1])] += 1

        stats.append({
            "name": mode,
            "count": len(exs),
            "pct": round(len(exs) / max(total, 1), 3),
            "avg_tools_per_exchange": round(total_tools / max(len(exs), 1), 1),
            "signature_tools": [t for t, _ in tool_counts.most_common(5)],
            "common_verbs": [v for v, _ in verb_counts.most_common(5)],
            "common_bigrams": [
                list(bg) for bg, _ in bigram_counts.most_common(3)
            ],
        })
    return stats


def _verb_intent_map(verb_corr: dict[str, Counter]) -> dict[str, dict]:
    """Build verb → primary_mode + confidence mapping."""
    result: dict[str, dict] = {}
    for verb, mode_counts in sorted(verb_corr.items()):
        total = sum(mode_counts.values())
        if total < 2:
            continue
        primary, primary_count = mode_counts.most_common(1)[0]
        result[verb] = {
            "primary_mode": primary,
            "confidence": round(primary_count / total, 2),
            "count": total,
            "mode_distribution": dict(mode_counts.most_common()),
        }
    return result


def build_pattern_catalog(
    corpus: list[SessionRecord],
    exchanges: list[Exchange],
    ngrams: dict[int, list[tuple[tuple[str, ...], int]]],
    modes: list[dict],
    verb_map: dict[str, dict],
    all_transitions: list[dict],
    all_subagents: list[SubagentRecord] | None = None,
    subagent_modes: Counter | None = None,
) -> dict:
    """Build the structured pattern catalog."""
    all_subagents = all_subagents or []
    subagent_modes = subagent_modes or Counter()

    # Date range
    all_ts = [ex.timestamp for ex in exchanges if ex.timestamp > 0]
    date_start = datetime.fromtimestamp(min(all_ts), tz=UTC).strftime("%Y-%m-%d") if all_ts else "?"
    date_end = datetime.fromtimestamp(max(all_ts), tz=UTC).strftime("%Y-%m-%d") if all_ts else "?"

    # Transition aggregation
    trans_counter: Counter = Counter()
    for t in all_transitions:
        trans_counter[(t["from"], t["to"])] += 1

    sessions_with_trans = sum(
        1 for s in corpus if detect_phase_transitions(s)
    )

    total_exchanges = len(exchanges)
    compact_count = sum(1 for s in all_subagents if s.agent_type == "compact")
    spawned_count = len(all_subagents) - compact_count

    catalog: dict = {
        "_meta": {
            "generated": datetime.now(UTC).isoformat(),
            "corpus": {
                "sessions": len(corpus),
                "exchanges": total_exchanges,
                "subagents": len(all_subagents),
                "subagents_compact": compact_count,
                "subagents_spawned": spawned_count,
                "projects": len({s.project for s in corpus}),
                "date_range": [date_start, date_end],
            },
            "version": 1,
        },
        "tool_patterns": {},
        "workflow_modes": modes,
        "verb_intent_map": verb_map,
        "phase_transitions": {
            "common": [
                {"from": f, "to": t, "count": c, "pct": round(c / max(len(all_transitions), 1), 3)}
                for (f, t), c in trans_counter.most_common(15)
            ],
            "avg_transitions_per_session": round(
                len(all_transitions) / max(len(corpus), 1), 1
            ),
            "sessions_with_transitions": sessions_with_trans,
        },
    }

    # Subagent analysis
    if all_subagents:
        sub_tool_totals: Counter = Counter()
        for sub in all_subagents:
            for t in sub.tool_sequence:
                sub_tool_totals[t] += 1
        catalog["subagents"] = {
            "total": len(all_subagents),
            "compact": compact_count,
            "spawned": spawned_count,
            "mode_distribution": dict(subagent_modes.most_common()),
            "avg_tools": round(
                sum(len(s.tool_sequence) for s in all_subagents) / max(len(all_subagents), 1), 1
            ),
            "top_tools": dict(sub_tool_totals.most_common(10)),
        }

    # Tool patterns
    label = {1: "unigrams", 2: "bigrams", 3: "trigrams", 4: "quadgrams"}
    for n, grams in ngrams.items():
        ngram_total = sum(c for _, c in grams) if grams else 1
        if n == 1:
            catalog["tool_patterns"]["unigrams"] = {
                g[0]: c for g, c in grams
            }
        else:
            catalog["tool_patterns"][label.get(n, f"{n}-grams")] = [
                {
                    "seq": list(g),
                    "count": c,
                    "pct": round(c / ngram_total, 3),
                }
                for g, c in grams[:30]
            ]

    return catalog


def print_summary(catalog: dict) -> None:
    """Print human-readable summary to stdout."""
    meta = catalog["_meta"]
    corp = meta["corpus"]
    print(f"\n{'='*60}")
    print(f"  Session Pattern Discovery — {meta['generated'][:10]}")
    print(f"{'='*60}")
    print(f"\nCorpus: {corp['sessions']} sessions, {corp['exchanges']} exchanges, "
          f"{corp['projects']} projects")
    if corp.get("subagents"):
        print(f"Subagents: {corp['subagents']} ({corp['subagents_compact']} compact, "
              f"{corp['subagents_spawned']} spawned)")
    print(f"Date range: {corp['date_range'][0]} → {corp['date_range'][1]}")

    # Tool unigrams
    unigrams = catalog["tool_patterns"].get("unigrams", {})
    if unigrams:
        print(f"\n--- Tool Frequency (top 15) ---")
        for tool, count in sorted(unigrams.items(), key=lambda x: -x[1])[:15]:
            print(f"  {tool:30s} {count:6d}")

    # Top bigrams
    bigrams = catalog["tool_patterns"].get("bigrams", [])
    if bigrams:
        print(f"\n--- Tool Bigrams (top 15) ---")
        for bg in bigrams[:15]:
            seq = " → ".join(bg["seq"])
            print(f"  {seq:40s} {bg['count']:6d}  ({bg['pct']:.1%})")

    # Top trigrams
    trigrams = catalog["tool_patterns"].get("trigrams", [])
    if trigrams:
        print(f"\n--- Tool Trigrams (top 10) ---")
        for tg in trigrams[:10]:
            seq = " → ".join(tg["seq"])
            print(f"  {seq:50s} {tg['count']:6d}  ({tg['pct']:.1%})")

    # Mode distribution
    modes = catalog.get("workflow_modes", [])
    if modes:
        print(f"\n--- Workflow Modes ---")
        for m in modes:
            tools = ", ".join(m["signature_tools"][:3])
            verbs = ", ".join(m["common_verbs"][:3])
            print(f"  {m['name']:15s} {m['count']:5d} ({m['pct']:.0%})"
                  f"  avg_tools={m['avg_tools_per_exchange']:.1f}"
                  f"  sig=[{tools}]  verbs=[{verbs}]")

    # Verb-intent map
    vim = catalog.get("verb_intent_map", {})
    if vim:
        print(f"\n--- Verb → Mode (top 15, ≥2 occurrences) ---")
        sorted_verbs = sorted(vim.items(), key=lambda x: -x[1]["count"])[:15]
        for verb, info in sorted_verbs:
            print(f"  {verb:15s} → {info['primary_mode']:15s}"
                  f"  conf={info['confidence']:.0%}  n={info['count']}")

    # Subagent stats
    subs = catalog.get("subagents", {})
    if subs:
        print(f"\n--- Subagent Behavior ---")
        print(f"  Total: {subs['total']} ({subs['compact']} compact, {subs['spawned']} spawned)")
        print(f"  Avg tools/subagent: {subs['avg_tools']}")
        modes_str = ", ".join(f"{m}({c})" for m, c in sorted(subs["mode_distribution"].items(), key=lambda x: -x[1])[:5])
        print(f"  Modes: {modes_str}")
        top_tools_str = ", ".join(f"{t}({c})" for t, c in list(subs["top_tools"].items())[:5])
        print(f"  Top tools: {top_tools_str}")

    # Phase transitions
    trans = catalog.get("phase_transitions", {})
    common = trans.get("common", [])
    if common:
        print(f"\n--- Phase Transitions (top 10) ---")
        print(f"  Avg transitions/session: {trans.get('avg_transitions_per_session', 0)}")
        print(f"  Sessions with transitions: {trans.get('sessions_with_transitions', 0)}")
        for t in common[:10]:
            print(f"  {t['from']:15s} → {t['to']:15s}  {t['count']:5d}  ({t['pct']:.0%})")

    # Phase archetypes
    archetypes = catalog.get("phase_archetypes", {})
    if archetypes:
        print(f"\n--- Work Phase Archetypes ({len(archetypes)} patterns, ≥3 occurrences) ---")
        for pattern, stats in sorted(archetypes.items(), key=lambda x: -x[1]["count"]):
            intents = ", ".join(stats["top_intents"].keys()) or "—"
            verbs = ", ".join(stats["top_verbs"].keys()) or "—"
            sigs = stats.get("example_signatures", [])
            sig_str = sigs[0][:60] if sigs else "—"
            print(f"\n  [{pattern}]  n={stats['count']} ({stats['pct']:.0%})"
                  f"  mode={stats['dominant_mode']}"
                  f"  avg_ex={stats['avg_exchanges']}"
                  f"  avg_tools={stats['avg_tools']}"
                  f"  files={stats['avg_files']}")
            print(f"    verbs=[{verbs}]  intents=[{intents}]")
            print(f"    example: {sig_str}")

    print(f"\n{'='*60}\n")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="Session pattern discovery")
    parser.add_argument("--since", type=int, default=None,
                        help="Only analyze sessions from the last N days")
    parser.add_argument("--project", type=str, default=None,
                        help="Filter to project name substring")
    parser.add_argument("--output", type=str, default="analysis/patterns.json",
                        help="Output path for patterns.json")
    parser.add_argument("--include-subagents", action="store_true",
                        help="Include subagent session files")
    args = parser.parse_args()

    since = None
    if args.since:
        since = datetime.now(UTC) - timedelta(days=args.since)

    print("Extracting corpus...")
    corpus = extract_corpus(
        since=since,
        project_filter=args.project,
        include_subagents=args.include_subagents,
    )
    print(f"  Found {len(corpus)} sessions")

    # Flatten exchanges and collect subagent data
    all_exchanges: list[Exchange] = []
    all_subagents: list[SubagentRecord] = []
    for session in corpus:
        all_exchanges.extend(session.exchanges)
        all_subagents.extend(session.subagents)
    print(f"  Total exchanges: {len(all_exchanges)}")
    print(f"  Total subagents: {len(all_subagents)}")

    if not all_exchanges and not all_subagents:
        print("No data found. Nothing to analyze.")
        return

    # Merge subagent tool sequences into the n-gram analysis
    # (subagents are separate work units, not exchanges, but their tool
    # patterns are equally valid for discovering behavioral modes)
    subagent_tool_seqs = [sub.tool_sequence for sub in all_subagents]

    # Analysis
    print("Analyzing patterns...")
    ngrams = tool_ngrams(all_exchanges, subagent_seqs=subagent_tool_seqs)
    modes = _mode_stats(all_exchanges)
    verb_corr = verb_correlations(all_exchanges)
    verb_map = _verb_intent_map(verb_corr)

    all_transitions: list[dict] = []
    for session in corpus:
        all_transitions.extend(detect_phase_transitions(session))

    # Subagent mode distribution
    subagent_modes: Counter = Counter()
    for sub in all_subagents:
        subagent_modes[classify_mode(sub.tool_sequence)] += 1

    # Work-phase archetype discovery
    print("Discovering phase archetypes...")
    all_phases, archetype_stats = discover_phase_archetypes(corpus)
    print(f"  Extracted {len(all_phases)} work phases")
    print(f"  Found {len(archetype_stats)} recurring patterns (≥3 occurrences)")

    # Build catalog
    catalog = build_pattern_catalog(
        corpus, all_exchanges, ngrams, modes, verb_map, all_transitions,
        all_subagents, subagent_modes,
    )
    catalog["phase_archetypes"] = archetype_stats

    # Output
    print_summary(catalog)

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(catalog, indent=2, default=str))
    print(f"Pattern catalog written to {output_path}")


if __name__ == "__main__":
    main()
