# Prism

Holographic usage analytics for LLM coding agents. Monitors, scores, recommends, and fixes — all from on-disk data, zero LLM inference.

## When to use Prism

1. **Orient** — Start a session with `prism_snapshot(period, project)` to see where you are: tokens, tools, efficiency, cognitive state.
2. **Monitor** — Hooks capture every tool call silently. `prism_trends(days)` shows cross-session drift (efficiency, error rate, tool distribution). Near-instant — reads pre-aggregated hook data.
3. **Diagnose** — `prism_forensics(session_id)` reconstructs any session: token breakdown, tool sequence, subagent costs, real-time error detection from hooks.
4. **Assess** — `prism_health(project_path)` scores project setup maturity (0-100). `prism_pr_ready(project_path)` gates PRs with a composite go/no-go.
5. **Fix** — `prism_recommend(project_path)` identifies issues with confidence scores. `prism_fix(project_path)` auto-remediates deterministically.
6. **Drill down** — Every tool returns a snapshot_id. `prism_details(id, section, path)` navigates into full results without re-running analysis.

## Tool reference

| Tool | Cost | Purpose |
|------|------|---------|
| `prism_snapshot` | ~1s | Multi-lens composite (tokens, tools, efficiency, cognitive, quality) |
| `prism_economics` | ~1s | Token burn, cache efficiency, RTK savings, subagent costs |
| `prism_behavior` | ~1s | Tool choreography, sequences, workflow mode detection |
| `prism_trajectory` | <1s | Quality/decision/cognitive trends over time |
| `prism_forensics` | ~1s | Session deep-dive with hook-enriched real-time data |
| `prism_trends` | <0.1s | Cross-session intelligence from pre-aggregated hook data |
| `prism_health` | <0.5s | Project setup maturity scoring (0-100) |
| `prism_recommend` | ~1s | Confidence-scored automation recommendations |
| `prism_fix` | varies | Deterministic auto-remediation (dry-run by default) |
| `prism_pr_ready` | <0.5s | PR readiness gate (git, health, LintGate, efficiency) |
| `prism_details` | <0.1s | Drill into any snapshot by ID + section/path |

## Key concept

Prism treats **local compute as free and token budget as precious**. Every tool writes full results to `~/.claude/prism/snapshots/` and returns only a compact summary (~300 tokens) plus a snapshot_id. Claude drills down via `prism_details` only when needed. Hooks capture telemetry silently — no token cost, no systemMessage unless anomalies are detected.

All analytics tools accept a `project` parameter for per-project scoping. Leave empty for cross-project aggregate view.

## Data sources

Prism reads (never writes to) 8 external sources: Claude Code JSONL sessions, RTK SQLite, stats-cache, usage-data facets, LintGate metrics, LintGate sessions, Continuity decisions, Mneme cognitive state.

## LintGate integration

Prism and LintGate communicate via on-disk state:
- **Prism writes** `~/.claude/prism/bridge.json` (session efficiency) and `~/.claude/prism/health/` (setup maturity)
- **LintGate reads** these in its controlplane via `_inject_prism_data()`
- Neither calls the other's MCP tools — pure disk-mediated bridge
