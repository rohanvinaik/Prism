# AGENTS.md

## Scope
- Applies to the entire `prism` repository.
- Follow user instructions first, then this file, then local nested guidance.

## Execution Contract
- Read relevant files before editing.
- Prefer minimal diffs over broad rewrites.
- Avoid behavior changes unless requested or required to fix defects.
- Surface assumptions and risks when information is incomplete.

## Required Validation
- Run the smallest check set that proves the change is correct.
- `ruff check .`
- `pytest -q`
- `mypy .`

## Theory and Context
- Read `CLAUDE.md` and `.claude/rules/theory.md` before deep refactors.
- Keep implementation aligned with: Deliver changes that are correct, maintainable, and aligned to project constraints.
- If work conflicts with explicit rules, stop and request clarification.

## Handoff Expectations
- Summarize what changed and why.
- Report what was tested and what remains unverified.
