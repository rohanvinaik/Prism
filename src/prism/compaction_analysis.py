"""Compaction boundary analyzer for narrative-frame validation.

Given a session's event stream, extract per-compaction metrics that
answer: does injecting a narrative frame change post-compact behavior?

Metrics per boundary:
    - frame_injected, frame_length, pattern_before, mode_before
    - pre_error_rate  (errors in N tool events before compact)
    - post_error_rate (errors in N tool events after compact)
    - error_rate_delta (post - pre; positive = got worse)
    - files_pre, files_post (file paths read/edited in each window)
    - re_read_count / re_read_rate (overlap — thrash indicator)

Aggregate across sessions splits by frame_injected to compare means.

Event stream contract (see hooks.py):
    tool_use: {event, tool, error, output_bytes, file_path?}
    pre_compact: {event, tools_so_far, frame, frame_length,
                  frame_injected, frame_disabled, pattern_before,
                  mode_before}
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from . import engine

DEFAULT_WINDOW = 20


@dataclass
class BoundaryMetrics:
    session_id: str
    boundary_idx: int
    tools_so_far: int
    frame_injected: bool
    frame_disabled: bool
    frame_length: int
    pattern_before: str
    mode_before: str
    pre_tools: int
    post_tools: int
    pre_errors: int
    post_errors: int
    files_pre: list[str] = field(default_factory=list)
    files_post: list[str] = field(default_factory=list)
    re_read_count: int = 0

    @property
    def pre_error_rate(self) -> float:
        return self.pre_errors / self.pre_tools if self.pre_tools else 0.0

    @property
    def post_error_rate(self) -> float:
        return self.post_errors / self.post_tools if self.post_tools else 0.0

    @property
    def error_rate_delta(self) -> float:
        return self.post_error_rate - self.pre_error_rate

    @property
    def re_read_rate(self) -> float:
        return self.re_read_count / len(self.files_post) if self.files_post else 0.0

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "boundary_idx": self.boundary_idx,
            "tools_so_far": self.tools_so_far,
            "frame_injected": self.frame_injected,
            "frame_disabled": self.frame_disabled,
            "frame_length": self.frame_length,
            "pattern_before": self.pattern_before,
            "mode_before": self.mode_before,
            "pre_tools": self.pre_tools,
            "post_tools": self.post_tools,
            "pre_errors": self.pre_errors,
            "post_errors": self.post_errors,
            "pre_error_rate": round(self.pre_error_rate, 4),
            "post_error_rate": round(self.post_error_rate, 4),
            "error_rate_delta": round(self.error_rate_delta, 4),
            "files_pre": self.files_pre,
            "files_post": self.files_post,
            "re_read_count": self.re_read_count,
            "re_read_rate": round(self.re_read_rate, 4),
        }


def _find_compaction_indices(events: list[dict]) -> list[int]:
    return [i for i, e in enumerate(events) if e.get("event") == "pre_compact"]


def _window_tool_events(
    events: list[dict], start: int, end: int
) -> list[dict]:
    """Slice events[start:end] and keep only tool_use entries."""
    return [e for e in events[start:end] if e.get("event") == "tool_use"]


def _file_paths(tool_events: Iterable[dict]) -> list[str]:
    """Extract file_path values from tool_use events (dedup, preserve order)."""
    seen: dict[str, None] = {}
    for e in tool_events:
        fp = e.get("file_path")
        if fp and fp not in seen:
            seen[fp] = None
    return list(seen)


def analyze_boundary(
    session_id: str,
    events: list[dict],
    boundary_idx: int,
    window: int = DEFAULT_WINDOW,
) -> BoundaryMetrics:
    """Compute metrics for a single pre_compact event at events[boundary_idx].

    Pre-window is bounded by the prior pre_compact (tools before that
    boundary are already summarized into context and aren't relevant to
    *this* compaction's pre-state). Post-window is bounded by the next
    pre_compact similarly.
    """
    compact_event = events[boundary_idx]

    prior_compact = -1
    for i in range(boundary_idx - 1, -1, -1):
        if events[i].get("event") == "pre_compact":
            prior_compact = i
            break
    pre_start = prior_compact + 1

    next_compact = len(events)
    for i in range(boundary_idx + 1, len(events)):
        if events[i].get("event") == "pre_compact":
            next_compact = i
            break

    pre = _window_tool_events(events, pre_start, boundary_idx)[-window:]
    post = _window_tool_events(events, boundary_idx + 1, next_compact)[:window]

    files_pre = _file_paths(pre)
    files_post = _file_paths(post)
    re_read = set(files_pre) & set(files_post)

    return BoundaryMetrics(
        session_id=session_id,
        boundary_idx=boundary_idx,
        tools_so_far=compact_event.get("tools_so_far", 0),
        frame_injected=bool(compact_event.get("frame_injected", False)),
        frame_disabled=bool(compact_event.get("frame_disabled", False)),
        frame_length=compact_event.get("frame_length", 0),
        pattern_before=compact_event.get("pattern_before", ""),
        mode_before=compact_event.get("mode_before", ""),
        pre_tools=len(pre),
        post_tools=len(post),
        pre_errors=sum(1 for e in pre if e.get("error")),
        post_errors=sum(1 for e in post if e.get("error")),
        files_pre=files_pre,
        files_post=files_post,
        re_read_count=len(re_read),
    )


def analyze_session(session_id: str, window: int = DEFAULT_WINDOW) -> list[BoundaryMetrics]:
    """Return one BoundaryMetrics per pre_compact event in the session."""
    events = engine.read_events(session_id)
    if not events:
        return []
    return [
        analyze_boundary(session_id, events, idx, window=window)
        for idx in _find_compaction_indices(events)
    ]


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def aggregate(
    boundaries: list[BoundaryMetrics],
    min_post_tools: int = 5,
) -> dict:
    """Group boundaries by frame_injected and compute group means.

    Drops boundaries with fewer than min_post_tools post-compact tool calls
    (too short to yield a meaningful error-rate measurement).
    """
    eligible = [b for b in boundaries if b.post_tools >= min_post_tools]
    with_frame = [b for b in eligible if b.frame_injected]
    without_frame = [b for b in eligible if not b.frame_injected]

    def group_stats(group: list[BoundaryMetrics]) -> dict:
        return {
            "n": len(group),
            "mean_post_error_rate": round(_mean([b.post_error_rate for b in group]), 4),
            "mean_pre_error_rate": round(_mean([b.pre_error_rate for b in group]), 4),
            "mean_error_rate_delta": round(_mean([b.error_rate_delta for b in group]), 4),
            "mean_re_read_rate": round(_mean([b.re_read_rate for b in group]), 4),
            "mean_post_tools": round(_mean([b.post_tools for b in group]), 1),
        }

    return {
        "total_boundaries": len(boundaries),
        "eligible_boundaries": len(eligible),
        "min_post_tools": min_post_tools,
        "with_frame": group_stats(with_frame),
        "without_frame": group_stats(without_frame),
    }


def aggregate_recent(days: int = 7, window: int = DEFAULT_WINDOW) -> dict:
    """Aggregate across all sessions with activity in the last `days`.

    Used by the MCP tool. Reads every session file in SESSIONS_DIR;
    filtering by age is cheap since we short-circuit on sessions that
    have no pre_compact events.
    """
    all_boundaries: list[BoundaryMetrics] = []
    if not engine.SESSIONS_DIR.is_dir():
        return aggregate(all_boundaries)

    cutoff_ts = None
    if days > 0:
        import time
        cutoff_ts = time.time() - days * 86400

    for path in engine.SESSIONS_DIR.glob("*.jsonl"):
        if cutoff_ts is not None and path.stat().st_mtime < cutoff_ts:
            continue
        sid = path.stem
        all_boundaries.extend(analyze_session(sid, window=window))

    result = aggregate(all_boundaries)
    result["sessions_scanned"] = sum(
        1 for _ in engine.SESSIONS_DIR.glob("*.jsonl")
    )
    result["boundaries"] = [b.to_dict() for b in all_boundaries]
    return result


def analyze(days: int = 30, window: int = DEFAULT_WINDOW) -> str:
    """MCP tool entrypoint — compact summary + drill-down snapshot."""
    agg = aggregate_recent(days=days, window=window)
    total = agg.get("total_boundaries", 0)
    eligible = agg.get("eligible_boundaries", 0)
    w = agg.get("with_frame", {})
    wo = agg.get("without_frame", {})

    lines = [
        f"# Compaction Analysis ({days}d, window={window})",
        "",
        f"- Total compactions recorded: **{total}**",
        f"- Eligible (>={agg.get('min_post_tools', 5)} post-tools): **{eligible}**",
        "",
        "## With narrative frame",
        f"- n={w.get('n', 0)} | "
        f"post_error_rate={w.get('mean_post_error_rate', 0):.2%} | "
        f"re_read_rate={w.get('mean_re_read_rate', 0):.2%} | "
        f"Δerror={w.get('mean_error_rate_delta', 0):+.2%}",
        "",
        "## Without narrative frame (baseline)",
        f"- n={wo.get('n', 0)} | "
        f"post_error_rate={wo.get('mean_post_error_rate', 0):.2%} | "
        f"re_read_rate={wo.get('mean_re_read_rate', 0):.2%} | "
        f"Δerror={wo.get('mean_error_rate_delta', 0):+.2%}",
        "",
    ]

    # Interpretation
    if w.get("n", 0) >= 3 and wo.get("n", 0) >= 3:
        err_improvement = (
            wo.get("mean_post_error_rate", 0) - w.get("mean_post_error_rate", 0)
        )
        reread_improvement = (
            wo.get("mean_re_read_rate", 0) - w.get("mean_re_read_rate", 0)
        )
        lines.append("## Signal")
        if err_improvement > 0.02:
            lines.append(
                f"- Frame **reduces** post-compact error rate by {err_improvement:.2%}"
            )
        elif err_improvement < -0.02:
            lines.append(
                f"- Frame **increases** post-compact error rate by {-err_improvement:.2%}"
            )
        else:
            lines.append("- No detectable effect on error rate")
        if reread_improvement > 0.05:
            lines.append(
                f"- Frame **reduces** file re-reads by {reread_improvement:.2%}"
            )
        elif reread_improvement < -0.05:
            lines.append(
                f"- Frame **increases** file re-reads by {-reread_improvement:.2%}"
            )
    else:
        lines.append("## Signal")
        lines.append(
            "- Not enough data yet (need >=3 compactions in each group). "
            "Run some sessions with PRISM_DISABLE_FRAME=1 to build the baseline."
        )

    summary = "\n".join(lines)

    aid = engine.save_snapshot("compaction_analysis", summary, agg)
    return (
        summary
        + f"\n\n_Details: prism_details(\"{aid}\", section=\"boundaries\")_"
    )
