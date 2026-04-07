# Prism

**Holographic usage analytics for LLM coding agents.**

11 MCP tools. 4 hooks. 8 data sources. Zero LLM inference.

---

## The problem

LLM coding agents burn tokens invisibly. Context windows compress without warning. Subagent spawns multiply costs. Cache efficiency swings between 4% and 96% depending on workflow. Error streaks cascade. And the official tools for monitoring all of this? They spawn *more* agents — consuming the very resource you're trying to understand.

Meanwhile, every Claude Code session already writes detailed JSONL logs. RTK tracks command-level savings in SQLite. LintGate records quality metrics and behavioral signals. Continuity logs architectural decisions. Mneme tracks cognitive state. The data exists. Nobody reads it.

## What Prism does

Prism reads all of it — cheaply, silently, from disk — and reconstructs a holographic view of how you work with LLM agents.

**Analytics** — Token economics, tool choreography, workflow mode detection, cache efficiency, subagent cost attribution. Cross-session trend detection from pre-aggregated hook data. All project-scoped.

**Assessment** — Setup maturity scoring (0-100): venv, lockfile, git, CI, secrets, toolchain. PR readiness gate: composite go/no-go from git state, LintGate blockers, health score, session error rate.

**Remediation** — Confidence-scored recommendations (0-100) from behavioral signals and health checks. Deterministic auto-fixer: venv creation, lockfile sync, .gitignore generation, hook patching. Dry-run by default.

**Forensics** — Reconstruct any session: token breakdown, tool sequence, subagent costs, read/edit ratios. Enriched with real-time hook data (error detection, output sizes, compaction boundaries) that post-hoc JSONL parsing can't capture.

## The economics

Every tool writes full results to disk and returns ~300 tokens + a snapshot ID. Claude drills down on demand via `prism_details(id, section)` — 50-70% token savings per analysis cycle vs returning everything upfront.

Hooks are silent writers (zero token cost). The Stop hook pre-computes session summaries. `prism_trends` reads pre-aggregated daily JSONL in <0.1 seconds. The recommend-fix-gate loop runs on JSON parsing and `stat()` calls — no inference, no agent spawns.

Performance: mtime pre-filter on JSONL scanning — 4,945 files / 2.8GB skipped by file modification time. Unscoped "today" query: 1.1 seconds (down from 15).

## Architecture

```
~/.claude/prism/
├── snapshots/{id}.json        # Analysis results (drill-down via prism_details)
├── sessions/{session_id}.jsonl # Real-time tool call events from hooks
├── daily/{YYYYMMDD}.jsonl     # Session summaries (one line per session)
├── health/{project_hash}.json # Setup maturity (LintGate reads this)
└── bridge.json                # Session efficiency (LintGate reads this)
```

**Three layers:**

1. **On-disk engine** — Snapshot persistence, session event streams, daily summaries, JSON path drill-down with 2048-char response cap.

2. **Real-time hooks** — PostToolUse (tool call logging), SessionStart (session init), PreCompact (compaction boundary), Stop (session finalization + efficiency scoring). Silent by default. Anomaly detection emits warnings only on 3+ consecutive errors or >20% session error rate.

3. **LintGate bridge** — Bidirectional, disk-mediated. Prism writes `bridge.json` and `health/` state. LintGate's controlplane reads these via `_inject_prism_data()` for efficiency-aware nudges. No MCP-to-MCP calls.

## Data sources

| Source | Format | What Prism reads |
|--------|--------|-----------------|
| Claude Code sessions | JSONL | Token usage, tool calls, subagents, prompts |
| RTK | SQLite | Command filtering savings, per-project |
| stats-cache | JSON | Daily activity rollups |
| usage-data facets | JSON | Session outcomes, satisfaction |
| LintGate metrics | JSONL | Code quality, feature usage, purity ratios |
| LintGate sessions | JSON | Behavioral compass, coherence trajectory |
| Continuity | SQLite | Architectural decisions, confidence, outcomes |
| Mneme | SQLite | Cognitive events, weather, concept drift |

All reads are read-only. Prism never modifies external data.

## Quick start

```bash
# Install
cd ~/tools/prism
python3 -m venv .venv
.venv/bin/pip install -e .

# Register in canonical MCP config
# Add to ~/.config/mcp/servers.json:
#   "Prism": {
#     "command": "/Users/you/tools/prism/.venv/bin/prism-mcp",
#     "args": []
#   }
# Then: python3 ~/.config/mcp/sync.py

# Wire hooks (add to ~/.claude/settings.json "hooks" section):
#   PostToolUse, SessionStart, PreCompact, Stop
#   command: /Users/you/tools/prism/.venv/bin/prism-hook
```

**First session workflow:**

1. `prism_snapshot("today", "my-project")` — where am I?
2. `prism_health("/path/to/project")` — setup gaps?
3. `prism_recommend("/path/to/project")` — what should I fix?
4. `prism_fix("/path/to/project", dry_run=False)` — fix it
5. `prism_pr_ready("/path/to/project")` — ready to ship?

## Tools

| Tool | Purpose |
|------|---------|
| `prism_snapshot` | Multi-lens composite view |
| `prism_economics` | Token burn, cache, RTK savings, subagent costs |
| `prism_behavior` | Tool choreography, workflow mode detection |
| `prism_trajectory` | Quality / decision / cognitive trends |
| `prism_forensics` | Session deep-dive with hook enrichment |
| `prism_trends` | Cross-session intelligence (<0.1s) |
| `prism_health` | Project setup maturity (0-100) |
| `prism_recommend` | Confidence-scored recommendations |
| `prism_fix` | Deterministic auto-remediation |
| `prism_pr_ready` | PR readiness gate |
| `prism_details` | Drill into any snapshot |

## What this is not

**Not a linter.** LintGate handles code quality. Prism handles usage analytics and setup health. They collaborate via disk, not compete.

**Not an agent spawner.** Every Anthropic plugin we evaluated (pr-review-toolkit, code-review, claude-code-setup) solves problems by spawning 4-6 parallel agents. Prism solves the same problems by reading JSON files that already exist.

**Not a token counter.** RTK counts tokens saved by command filtering. Prism operates at the session/project/workflow level — cache efficiency, subagent cost attribution, behavioral patterns, cross-session trends. Complementary, not competing.

## License

MIT

## Author

Built by Rohan Vinaik with Claude Opus 4.6.
