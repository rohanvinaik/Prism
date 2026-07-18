# Prism

**Every Claude Code session writes down exactly what it did. Prism is the part that reads it back.**

[![CI](https://github.com/rohanvinaik/Prism/actions/workflows/ci.yml/badge.svg)](https://github.com/rohanvinaik/Prism/actions/workflows/ci.yml)
[![Quality Gate](https://sonarcloud.io/api/project_badges/measure?project=rohanvinaik_Prism&metric=alert_status)](https://sonarcloud.io/summary/new_code?id=rohanvinaik_Prism)
[![Coverage](https://sonarcloud.io/api/project_badges/measure?project=rohanvinaik_Prism&metric=coverage)](https://sonarcloud.io/summary/new_code?id=rohanvinaik_Prism)
[![Tests](https://raw.githubusercontent.com/rohanvinaik/Prism/badges/.github/badges/test-count.svg)](https://github.com/rohanvinaik/Prism/actions/workflows/spec-badges.yml)
[![Mean σ](https://raw.githubusercontent.com/rohanvinaik/Prism/badges/.github/badges/sigma.svg)](https://github.com/rohanvinaik/Prism/actions/workflows/spec-badges.yml)
<br>
[![Mutation Kill Rate](https://raw.githubusercontent.com/rohanvinaik/Prism/badges/.github/badges/mutation-kill-rate.svg)](https://github.com/rohanvinaik/Prism/actions/workflows/spec-badges.yml)
[![MC/DC](https://raw.githubusercontent.com/rohanvinaik/Prism/badges/.github/badges/mcdc.svg)](https://github.com/rohanvinaik/Prism/actions/workflows/spec-badges.yml)
[![Mutation Sampling](https://raw.githubusercontent.com/rohanvinaik/Prism/badges/.github/badges/mutation-sampling.svg)](https://github.com/rohanvinaik/Prism/actions/workflows/spec-badges.yml)

`13 MCP tools · ~300 tokens per call · Zero inference · Read-only`

Every token you spend, every tool you call, every subagent you spawn, every place a session compacts — Claude Code already writes all of it to `~/.claude/`. The record is complete, and nobody reads it. Prism reads it: token economics, behavioral signals, session forensics, and project-setup health, computed straight from the logs on disk. No inference calls, no agents spawned, and — because the hooks that collect the data are silent writers — zero tokens added to the session it is watching.

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

That is the whole install. Start a session and Prism begins collecting; after a few, every tool returns real analytics.

## What you get, as the record deepens

- **First session** — `prism_health("/path/to/project")` scores the setup (venv, lockfile, git, CI, secrets, toolchain) from 0–100; `prism_recommend` proposes fixes; `prism_fix` applies them deterministically.
- **After a few sessions** — `prism_snapshot("week")` shows token burn, cache efficiency, tool distribution, read/edit ratios; `prism_economics` breaks down API consumption and subagent cost; `prism_behavior` names the workflow mode you were in (Explore, Surgical, Shell-heavy, Delegating, Balanced).
- **Over time** — `prism_trends` catches efficiency drift, error-rate changes, and tool-distribution shifts from pre-aggregated daily summaries; `prism_trajectory` tracks quality and decision trends; `prism_forensics` reconstructs any session in full.
- **Before merging** — `prism_pr_ready("/path/to/project")` is a composite go/no-go: git clean, health score, lockfile freshness, session error rate.

## How it works

Every tool writes its full result to disk and returns ~300 tokens plus a snapshot ID; you drill into the rest on demand with `prism_details(id, section)`. The hooks are silent writers, so watching a session costs that session nothing.

```
~/.claude/prism/
├── snapshots/{id}.json           # Analysis results (drill-down via prism_details)
├── sessions/{session_id}.jsonl   # Real-time tool events from hooks
├── daily/{YYYYMMDD}.jsonl        # Session summaries (one line per session)
└── health/{project_hash}.json    # Project setup maturity state
```

**Core data sources** — these work for anyone with Claude Code installed:

| Source | What Prism reads |
|--------|-----------------|
| Claude Code sessions (`~/.claude/projects/`) | Token usage, tool calls, subagents, prompts |
| stats-cache (`~/.claude/stats-cache.json`) | Daily activity rollups |
| Prism hook events (`~/.claude/prism/`) | Real-time tool errors, output sizes, compaction boundaries |

**Optional integrations** — auto-detected if present; absent ones simply drop their section, nothing breaks:

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
| `prism_cross_project` | Pattern-based gaps relative to your other projects |
| `prism_pr_ready` | PR readiness gate (go/no-go) |
| `prism_compaction_analysis` | A/B validation of narrative-frame injection at compaction boundaries |
| `prism_details` | Drill into any snapshot by section |

Every tool takes an optional `project` parameter (substring match) to scope its results.

## Design principles

- **Read-only.** Prism never touches external data — every write lands in `~/.claude/prism/`.
- **No inference.** It runs on JSON parsing and file stats; the recommend → fix → gate loop is pure computation.
- **Compact-first.** Full results on disk, ~300-token summaries to the model, drill-down on demand.
- **Graceful degradation.** A missing data source returns an empty result — no crash, no error, just one fewer section in the output.

## License

MIT

## Author

Built by Rohan Vinaik with Claude Opus 4.6.
