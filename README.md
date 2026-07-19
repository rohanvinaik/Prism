# Prism

**Every Claude Code session writes down exactly what it did. Prism is the part that reads it back — for free.**

[![CI](https://github.com/rohanvinaik/Prism/actions/workflows/ci.yml/badge.svg)](https://github.com/rohanvinaik/Prism/actions/workflows/ci.yml)
[![Quality Gate](https://sonarcloud.io/api/project_badges/measure?project=rohanvinaik_Prism&metric=alert_status)](https://sonarcloud.io/summary/new_code?id=rohanvinaik_Prism)
[![Tests](https://raw.githubusercontent.com/rohanvinaik/Prism/badges/.github/badges/test-count.svg)](https://github.com/rohanvinaik/Prism/actions/workflows/spec-badges.yml)
[![Mutation Kill Rate](https://raw.githubusercontent.com/rohanvinaik/Prism/badges/.github/badges/mutation-kill-rate.svg)](https://github.com/rohanvinaik/Prism/actions/workflows/spec-badges.yml)

`13 MCP tools · ~300 tokens per call · zero inference · zero tokens added to the session it watches · read-only`

Every token you spend, every tool you call, every subagent you spawn, every place a session compacts — Claude Code already writes all of it to `~/.claude/`. The record is complete, and nobody reads it. Prism reads it. Point it at a real session and it hands back what actually happened:

```
$ prism_forensics   (a real 4.5-hour session)

  81 prompts · 658 tool calls · 204.8M tokens
  97.6% of the tokens were billed to "subagents"
      — but 2 of the 9 were the main session's own compacted continuations,
        not spawned agents at all
  the main session compacted twice · ~4M tokens each time just to rebuild context
  compaction fired at 100% context — too late for the narrative to survive intact
```

None of that is inferred. It is parsed straight off the logs on disk. No inference calls, no agents spawned, and — because the hooks that collect the data are silent writers — **zero tokens added to the session it is watching**.

## What you get, as the record deepens

- **First session** — `prism_health("/path")` scores the setup from 0–100 (venv, lockfile, git, CI, secrets, toolchain). `prism_recommend` proposes fixes. `prism_fix` applies them deterministically, dry-run by default.
- **After a few sessions** — `prism_snapshot("week")` shows token burn, cache efficiency, tool distribution, read/edit ratios. `prism_economics` breaks down API and subagent cost. `prism_behavior` names the mode you were in — Explore, Surgical, Shell-heavy, Delegating, Balanced.
- **Over time** — `prism_trends` catches efficiency drift and error-rate changes in under 0.1 s. `prism_trajectory` tracks quality and decision trends. `prism_forensics` reconstructs any session in full.
- **Before merging** — `prism_pr_ready("/path")` is a composite go/no-go: git clean, health score, lockfile freshness, session error rate.

Every tool writes its full result to disk and returns ~300 tokens plus a snapshot ID. You drill into the rest on demand with `prism_details(id, section)`. Watching a session costs that session nothing.

## The finding that changes what you do with it

Read enough sessions and one result stops being an accounting detail and becomes a claim about how the model should be run. **More context is not better.** A coding agent does not degrade over a long session because the model gets dumber. It degrades because its attention dilutes across an ever-growing window of stale reads, superseded diffs, and resolved reasoning. When compaction finally fires at the limit, it summarizes generically and discards structured state at random.

The evidence is in the numbers above and in the corpus behind them. 41% of exchanges are conversational — zero tool calls, high semantic value, and the first thing generic compaction throws away. Tool sequences are regular enough that 79 recurring phase patterns explain most of the work. So the model rarely needs the 200K tokens of what it read; it needs to know *which phase it is in and why*.

That is the Winstonian bet, from Patrick Winston's story model of intelligence: cognition runs on narrative frames, not accumulated information. The model is not processing tokens — it is understanding a story about what it is doing. So the fix is not a bigger window. It is a **lens**: a ~100-token focus string that tells a fresh context *what kind of story to reconstruct*, injected before compaction instead of after. `prism_compaction_analysis` is the A/B harness that validates it. A precise frame in a clean window beats a full window with the narrative buried under stale ballast.

## Setup

```bash
uv tool install prism-mcp        # or: pip install prism-mcp
```

Add the server to your MCP config, and wire four hooks in `~/.claude/settings.json` — `PostToolUse`, `SessionStart`, `PreCompact`, and `Stop`, each running `prism-hook`. That is the whole install. Start a session and Prism begins collecting; after a few, every tool returns real analytics. It reads from the paths Claude Code already writes (`~/.claude/projects/`, `stats-cache.json`, and its own hook events in `~/.claude/prism/`), and it auto-detects optional signals from RTK, LintGate, Continuity, and Mneme — an absent one simply drops its section, nothing breaks.

Prism is also one third of a repo-hygiene trio: it scores per-repo setup maturity, [SetupAtlas](https://github.com/rohanvinaik/SetupAtlas) covers the machine, and [LintGate](https://github.com/rohanvinaik/LintGate) covers the Python. Point the three at a project and it walks to a clean baseline.

## The tools

| Tool | Purpose |
|------|---------|
| `prism_snapshot` | multi-lens composite view |
| `prism_economics` | token burn, cache efficiency, subagent cost |
| `prism_behavior` | tool choreography, workflow-mode detection |
| `prism_trajectory` | quality / decision / cognitive trends |
| `prism_forensics` | session deep-dive with hook enrichment |
| `prism_trends` | cross-session intelligence (<0.1 s) |
| `prism_health` | project setup maturity (0–100) |
| `prism_recommend` · `prism_fix` | confidence-scored recommendations, deterministic remediation |
| `prism_cross_project` | pattern-based gaps against your other projects |
| `prism_pr_ready` | PR readiness gate (go/no-go) |
| `prism_compaction_analysis` | A/B validation of narrative-frame injection at compaction |
| `prism_details` | drill into any snapshot by section |

Every tool takes an optional `project` to scope its results. Read-only by design: Prism never touches external data, every write lands in `~/.claude/prism/`, and a missing data source returns an empty section rather than an error. The full narrative-compaction theory is in [`docs/narrative-compaction.md`](docs/narrative-compaction.md).

---

MIT — Rohan Vinaik. The session already wrote the record; Prism is what reads it.
