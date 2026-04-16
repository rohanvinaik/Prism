"""Proactive compaction trigger — recommends /compact at phase boundaries.

Rather than reactively compacting at 100% context, evaluate whether
*now* is a good moment: enough work to summarize, at a natural phase
boundary, not mid-burst, and not too soon after the last compaction.

Four guard conditions (anti-thrash):
    1. MIN_TOOLS_SINCE_LAST_COMPACT — enough work accumulated
    2. NOT_MID_BURST                — last N tools aren't all edits
    3. AT_PHASE_BOUNDARY            — directive just arrived, or no
                                      directive for a long stretch
    4. COOLDOWN                     — no compaction in recent window

All four must pass. The hook emits a systemMessage suggestion when
they do; the user decides whether to act on it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import engine, phase_matcher

# Tunable thresholds
MIN_TOOLS_SINCE_LAST_COMPACT = 20
MIN_TOOLS_SINCE_LAST_DIRECTIVE_WHEN_IDLE = 30
COOLDOWN_TOOLS = 10
BURST_WINDOW = 3


@dataclass
class CompactionRecommendation:
    recommend: bool
    reason: str
    confidence: float
    conditions_passed: list[str] = field(default_factory=list)
    conditions_failed: list[str] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "recommend": self.recommend,
            "reason": self.reason,
            "confidence": round(self.confidence, 3),
            "conditions_passed": self.conditions_passed,
            "conditions_failed": self.conditions_failed,
            "metrics": self.metrics,
        }


def _tool_use_events(events: list[dict]) -> list[dict]:
    return [e for e in events if e.get("event") == "tool_use"]


def _last_compact_idx(events: list[dict]) -> int:
    """Index of the most recent pre_compact event, or -1."""
    for i in range(len(events) - 1, -1, -1):
        if events[i].get("event") == "pre_compact":
            return i
    return -1


def _tools_since(events: list[dict], start_idx: int) -> int:
    """Count tool_use events after start_idx (exclusive)."""
    if start_idx < 0:
        return sum(1 for e in events if e.get("event") == "tool_use")
    return sum(1 for e in events[start_idx + 1 :] if e.get("event") == "tool_use")


def _last_n_tool_kinds(events: list[dict], n: int) -> list[str]:
    """Return the raw tool names of the last n tool_use events."""
    tool_events = _tool_use_events(events)
    return [e.get("tool", "") for e in tool_events[-n:]]


def should_compact(
    session_id: str,
    directive_just_arrived: bool = False,
) -> CompactionRecommendation:
    """Evaluate compaction suggestion for this session.

    `directive_just_arrived` is True when called from the
    UserPromptSubmit hook on a message classified as directive — that
    IS the phase boundary signal, so we skip the idle-stretch path.
    """
    events = engine.read_events(session_id)
    if not events:
        return CompactionRecommendation(
            recommend=False,
            reason="no events yet",
            confidence=0.0,
            metrics={"tool_count": 0},
        )

    last_compact = _last_compact_idx(events)
    tools_since_compact = _tools_since(events, last_compact)

    summary = phase_matcher.get_phase_summary(session_id)
    tools_since_directive = summary.get("tools_since_directive", 0)
    last_kinds = _last_n_tool_kinds(events, BURST_WINDOW)
    in_edit_burst = len(last_kinds) == BURST_WINDOW and all(
        k in ("Edit", "Write", "NotebookEdit") for k in last_kinds
    )

    passed: list[str] = []
    failed: list[str] = []

    # Condition 1: enough work since last compact
    if tools_since_compact >= MIN_TOOLS_SINCE_LAST_COMPACT:
        passed.append(f"tools_since_compact>={MIN_TOOLS_SINCE_LAST_COMPACT}")
    else:
        failed.append(f"tools_since_compact={tools_since_compact}<{MIN_TOOLS_SINCE_LAST_COMPACT}")

    # Condition 2: not in edit burst
    if not in_edit_burst:
        passed.append("not_in_edit_burst")
    else:
        failed.append("in_edit_burst")

    # Condition 3: at phase boundary
    if directive_just_arrived:
        passed.append("directive_boundary")
    elif tools_since_directive >= MIN_TOOLS_SINCE_LAST_DIRECTIVE_WHEN_IDLE:
        passed.append(f"idle_stretch>={MIN_TOOLS_SINCE_LAST_DIRECTIVE_WHEN_IDLE}")
    else:
        failed.append("no_phase_boundary")

    # Condition 4: cooldown — no compaction too recent. If last_compact is -1
    # (no prior compact), this trivially passes.
    if last_compact < 0 or tools_since_compact >= COOLDOWN_TOOLS:
        passed.append("cooldown_ok")
    else:
        failed.append(f"cooldown_active({tools_since_compact}<{COOLDOWN_TOOLS})")

    recommend = not failed
    metrics = {
        "tools_since_compact": tools_since_compact,
        "tools_since_directive": tools_since_directive,
        "last_compact_idx": last_compact,
        "in_edit_burst": in_edit_burst,
        "current_pattern": summary.get("current_pattern", ""),
    }

    if recommend:
        reason = f"phase boundary detected, {tools_since_compact} tools since last compact"
    else:
        reason = "; ".join(failed)

    # Confidence: fraction of conditions passed
    confidence = len(passed) / (len(passed) + len(failed)) if (passed or failed) else 0.0

    return CompactionRecommendation(
        recommend=recommend,
        reason=reason,
        confidence=confidence,
        conditions_passed=passed,
        conditions_failed=failed,
        metrics=metrics,
    )


def format_nudge(rec: CompactionRecommendation) -> str:
    """Build the systemMessage text for a recommended compaction."""
    m = rec.metrics
    pattern = m.get("current_pattern") or "n/a"
    return (
        f"[Prism] {m.get('tools_since_compact', 0)} tools since last compact, "
        f"phase boundary (pattern={pattern}). Consider /compact to preserve "
        f"the narrative frame."
    )
