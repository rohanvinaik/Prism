---
paths:
  - "**/*.py"
---

# Theory Rules

This file stores extracted theory signals for `prism`.

## Facet Summaries
- Core Theory: No strong signal extracted for this facet yet.
- Problem-Solving: No strong signal extracted for this facet yet.
- Alignment: No strong signal extracted for this facet yet.
- Architecture: No strong signal extracted for this facet yet.
- Anti-Patterns: No strong signal extracted for this facet yet.
- Key Abstractions: No strong signal extracted for this facet yet.

## High-Signal Anti-Patterns
- Do not try a 4th approach without first enumerating all known constraints and verifying which ones the new approach actually addresses.
- Do not discover constraints one-at-a-time through failure — enumerate the full constraint space upfront by reading before acting.
- Do not re-attempt an approach that already failed unless the conditions that caused the failure have changed.
- Do not use O(n²) algorithms when O(n) alternatives exist — quadratic membership checks on lists, re.compile inside loops, and sorted()[0] instead of min() are structural mistakes, not style issues.
- Do not treat N instances of the same root cause as N separate problems — cluster issues by shared fix before diving into individual repairs.

## Enforceable Rules
- No enforceable rules extracted yet.

## Extraction Quality
- Validity status: weak
- Docs scanned: 2
- Total claims: 0
- Missing required facets: core_theory, problem_solving, alignment
- Warning: Missing required facets: core_theory, problem_solving, alignment
- Warning: Low claim density (0.0 claims/doc). Extraction may be too sparse for robust theory alignment.
- Warning: No enforceable rules found (existing or proposed).
