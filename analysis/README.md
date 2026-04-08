# Pattern Discovery

Offline analysis of Claude Code session data to discover recurring work-phase patterns. Produces a pattern catalog consumed by the runtime phase matcher.

## Usage

```bash
cd /Users/rohanvinaik/tools/Prism
.venv/bin/python analysis/pattern_discovery.py --include-subagents
```

Options:
- `--since N` — last N days only
- `--project FILTER` — filter to project name substring
- `--include-subagents` — include subagent session files (recommended)
- `--output PATH` — output path (default: `analysis/patterns.json`)

## What it does

1. Parses all Claude Code JSONL session files in `~/.claude/projects/`
2. Groups messages into exchanges (user prompt + assistant response chain)
3. Extracts subagent data (task prompt, tool sequence, assistant text)
4. Splits sessions into work phases at natural boundaries
5. Abstracts tool sequences to a 5-symbol alphabet (R/W/X/A/M) with run-length encoding
6. Finds recurring patterns, correlates with user text and intent keywords
7. Writes `patterns.json` for the runtime phase matcher

## Output

`patterns.json` contains tool n-grams, workflow mode stats, verb-intent correlations, and phase archetypes. The runtime matcher at `src/prism/phase_matcher.py` loads this file to recognize work phases in real time.

See `docs/narrative-compaction.md` for the full theory and implementation details.
