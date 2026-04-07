# Prism

**Usage analytics for Claude Code.** Token economics, behavioral signals, session forensics — all from data Claude Code already writes to disk. Zero LLM inference.

[![CI](https://github.com/rohanvinaik/Prism/actions/workflows/ci.yml/badge.svg)](https://github.com/rohanvinaik/Prism/actions/workflows/ci.yml)
[![Quality Gate](https://sonarcloud.io/api/project_badges/measure?project=rohanvinaik_Prism&metric=alert_status)](https://sonarcloud.io/summary/new_code?id=rohanvinaik_Prism)
[![Coverage](https://sonarcloud.io/api/project_badges/measure?project=rohanvinaik_Prism&metric=coverage)](https://sonarcloud.io/summary/new_code?id=rohanvinaik_Prism)
[![Tests](https://raw.githubusercontent.com/rohanvinaik/Prism/badges/.github/badges/test-count.svg)](https://github.com/rohanvinaik/Prism/actions/workflows/spec-badges.yml)
[![Mean σ](https://raw.githubusercontent.com/rohanvinaik/Prism/badges/.github/badges/sigma.svg)](https://github.com/rohanvinaik/Prism/actions/workflows/spec-badges.yml)
<br>
[![Mutation Kill Rate](https://raw.githubusercontent.com/rohanvinaik/Prism/badges/.github/badges/mutation-kill-rate.svg)](https://github.com/rohanvinaik/Prism/actions/workflows/spec-badges.yml)
[![MC/DC](https://raw.githubusercontent.com/rohanvinaik/Prism/badges/.github/badges/mcdc.svg)](https://github.com/rohanvinaik/Prism/actions/workflows/spec-badges.yml)
[![Mutation Sampling](https://raw.githubusercontent.com/rohanvinaik/Prism/badges/.github/badges/mutation-sampling.svg)](https://github.com/rohanvinaik/Prism/actions/workflows/spec-badges.yml)

## Setup

```bash
# 1. Install
uv tool install prism-mcp        # or: pip install prism-mcp

# 2. Add MCP server to Claude Code
#    In ~/.mcp.json (or your MCP config):
{
  "mcpServers": {
    "Prism": {
      "command": "prism-mcp",
      "args": []
    }
  }
}

# 3. Wire hooks (add to ~/.claude/settings.json):
{
  "hooks": {
    "PostToolUse": [{ "hooks": [{ "type": "command", "command": "prism-hook" }] }],
    "SessionStart": [{ "hooks": [{ "type": "command", "command": "prism-hook" }] }],
    "PreCompact": [{ "hooks": [{ "type": "command", "command": "prism-hook" }] }],
    "Stop": [{ "hooks": [{ "type": "command", "command": "prism-hook" }] }]
  }
}
```

That's it. Start a Claude Code session and Prism begins collecting data. After a few sessions, every tool returns meaningful analytics.

## What you get

**First session:** `prism_health("/path/to/project")` scores your project setup (venv, lockfile, git, CI, secrets, toolchain) from 0-100. `prism_recommend` suggests fixes. `prism_fix` applies them deterministically.

**After a few sessions:** `prism_snapshot("week")` shows token burn, cache efficiency, tool distribution, read/edit ratios. `prism_economics` breaks down API consumption and subagent costs. `prism_behavior` detects workflow modes (Explore, Surgical, Shell-heavy, Delegating, Balanced).

**Over time:** `prism_trends` detects efficiency drift, error rate changes, and tool distribution shifts from pre-aggregated daily summaries. `prism_trajectory` shows quality and decision trends. `prism_forensics` reconstructs any session in detail.

**Before merging:** `prism_pr_ready("/path/to/project")` is a composite go/no-go gate — git clean, health score, lockfile freshness, session error rate.

## How it works

Every tool writes full results to disk and returns ~300 tokens + a snapshot ID. Drill into the full data on demand with `prism_details(id, section)`. Hooks are silent writers (zero token cost to your session). No agents spawned, no inference calls.

```
~/.claude/prism/
├── snapshots/{id}.json           # Analysis results (drill-down via prism_details)
├── sessions/{session_id}.jsonl   # Real-time tool events from hooks
├── daily/{YYYYMMDD}.jsonl        # Session summaries (one line per session)
└── health/{project_hash}.json    # Project setup maturity state
```

### Core data sources

These work for everyone with Claude Code installed:

| Source | What Prism reads |
|--------|-----------------|
| Claude Code sessions (`~/.claude/projects/`) | Token usage, tool calls, subagents, prompts |
| stats-cache (`~/.claude/stats-cache.json`) | Daily activity rollups |
| Prism hook events (`~/.claude/prism/`) | Real-time tool errors, output sizes, compaction boundaries |

### Optional integrations

Prism auto-detects these if present. If they're not installed, those sections simply don't appear in output — nothing breaks.

| Integration | What it adds |
|-------------|-------------|
| [RTK](https://github.com/reachingforthejack/rtk) | Token savings from command filtering |
| LintGate | Code quality signals, behavioral compass, coherence trajectory |
| Continuity | Architectural decisions, confidence scoring, session outcomes |
| Mneme | Cognitive events, concept anchors, dimension tracking |

## Tools

| Tool | Purpose |
|------|---------|
| `prism_snapshot` | Multi-lens composite view |
| `prism_economics` | Token burn, cache efficiency, subagent costs |
| `prism_behavior` | Tool choreography, workflow mode detection |
| `prism_trajectory` | Quality / decision / cognitive trends |
| `prism_forensics` | Session deep-dive with hook enrichment |
| `prism_trends` | Cross-session intelligence (<0.1s) |
| `prism_health` | Project setup maturity (0-100) |
| `prism_recommend` | Confidence-scored automation recommendations |
| `prism_fix` | Deterministic auto-remediation (dry-run by default) |
| `prism_pr_ready` | PR readiness gate (go/no-go) |
| `prism_details` | Drill into any snapshot by section |

All tools accept an optional `project` parameter (substring match) to scope results.

## Design principles

- **Read-only.** Prism never modifies external data. All writes go to `~/.claude/prism/`.
- **No inference.** Everything runs on JSON parsing and file stats. The recommend-fix-gate loop is pure computation.
- **Compact-first.** Full results on disk, ~300-token summaries to the LLM. Drill-down on demand.
- **Graceful degradation.** Missing data sources return empty results. No crashes, no error messages, just fewer sections in the output.

## License

MIT

## Author

Built by Rohan Vinaik with Claude Opus 4.6.
