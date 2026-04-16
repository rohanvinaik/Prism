"""User message classifier — directive / continuation / clarification.

Heuristic classifier (no LLM call). Used by the phase matcher to
detect phase boundaries and by the narrative frame builder to label
user intent more precisely than "User request:".

Labels:
    directive     — new task, imperative verb, phase boundary likely
    continuation  — short affirmative or reference to prior work
    clarification — question about current or prior work

Design: conservative defaults. Unambiguous signals produce high
confidence; ambiguous text falls back to "directive" at low confidence
(treat as new work by default rather than assume continuation).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Imperative verbs — canonical start-of-sentence indicators for directives
_DIRECTIVE_VERBS = frozenset({
    "add", "build", "change", "create", "delete", "fix", "implement",
    "make", "move", "refactor", "remove", "rename", "rewrite", "run",
    "start", "stop", "switch", "update", "write", "check", "test",
    "review", "analyze", "deploy", "install", "set", "configure",
    "optimize", "improve", "port", "extract", "merge", "split",
    "replace", "apply", "generate", "migrate", "upgrade", "downgrade",
    "push", "pull", "commit", "revert", "undo", "redo", "clean",
    "show", "list", "find", "search", "look", "explore", "trace",
    "debug", "profile", "benchmark",
})

# Phrases that signal "let's move on" — typically start new work
_DIRECTIVE_PHRASES = (
    "let's", "lets ", "now ", "next,", "next ", "move on", "moving on",
    "switch to", "start ", "begin ", "time to", "new task", "different task",
    "unrelated", "pivot", "change of plans",
)

# Affirmatives / short continuations
_CONTINUATION_TOKENS = frozenset({
    "yes", "yeah", "yep", "yup", "y", "ok", "okay", "sure", "right",
    "sounds good", "sounds great", "go", "go on", "continue", "proceed",
    "keep going", "do it", "go for it", "fine", "good", "great", "perfect",
    "nice", "awesome", "correct", "exactly", "agreed", "approved",
    "fair enough", "makes sense", "got it", "thanks", "thank you",
    "no", "nope", "not quite", "not exactly", "wait", "hold on", "stop",
})

# Question leads — clarification signals
# Strong question leads — imply question regardless of punctuation
_STRONG_Q_LEADS = frozenset({
    "why", "what", "how", "when", "where", "which", "who", "whose", "whom",
})

# Modal / auxiliary leads — only imply question if ends with ?
_MODAL_Q_LEADS = frozenset({
    "can", "could", "would", "should", "does", "do", "did", "is", "are",
    "was", "were", "will", "has", "have", "had",
})

_CLARIFICATION_PHRASES = (
    "explain", "clarify", "elaborate", "walk me through", "tell me",
    "describe", "what does", "what is", "what's", "how does", "how do",
    "why did", "why does", "why is", "can you explain", "help me understand",
    "i don't understand", "i'm confused", "not sure what",
)

_WORD_RE = re.compile(r"\b[a-z']+\b")


@dataclass
class ClassificationResult:
    label: str
    confidence: float
    signals: list[str]

    def to_dict(self) -> dict:
        return {
            "label": self.label,
            "confidence": round(self.confidence, 3),
            "signals": self.signals,
        }


def _first_word(text: str) -> str:
    m = _WORD_RE.search(text.lower())
    return m.group(0) if m else ""


def _word_count(text: str) -> int:
    return len(_WORD_RE.findall(text))


def classify(text: str) -> ClassificationResult:
    """Classify a user message. Pure function, no side effects."""
    if not text or not text.strip():
        return ClassificationResult("continuation", 0.3, ["empty"])

    raw = text.strip()
    lower = raw.lower()
    word_count = _word_count(lower)
    first = _first_word(lower)
    ends_with_question = raw.rstrip().endswith("?")

    signals: list[str] = []
    directive_score = 0.0
    continuation_score = 0.0
    clarification_score = 0.0

    # ---- Clarification: strongest single signal is a question mark ----
    if ends_with_question:
        clarification_score += 0.6
        signals.append("ends_with_?")
    if first in _STRONG_Q_LEADS and word_count >= 2:
        clarification_score += 0.4
        signals.append(f"q_lead:{first}")
    elif first in _MODAL_Q_LEADS and ends_with_question and word_count >= 2:
        # Modal leads only count when the sentence is explicitly a question.
        # "can you explain X?" → clarification; "can you implement X?" is
        # decided below by the directive verb check.
        if any(v in lower.split()[:6] for v in _DIRECTIVE_VERBS):
            directive_score += 0.5
            signals.append("modal+imperative")
        else:
            clarification_score += 0.4
            signals.append(f"q_lead:{first}")
    if any(p in lower for p in _CLARIFICATION_PHRASES):
        clarification_score += 0.4
        signals.append("clarify_phrase")

    # ---- Directive: imperative verb at start or phrase indicators ----
    if first in _DIRECTIVE_VERBS:
        directive_score += 0.7
        signals.append(f"imperative:{first}")
    if any(lower.startswith(p) for p in _DIRECTIVE_PHRASES):
        directive_score += 0.5
        signals.append("directive_phrase")
    if any(p in lower for p in (" instead", " instead of", "not that")):
        # Corrective directive
        directive_score += 0.3
        signals.append("corrective")

    # ---- Continuation: short affirmative / short text ----
    # Strip trailing punctuation for exact-match check
    stripped = lower.rstrip(".!?,;:")
    if stripped in _CONTINUATION_TOKENS:
        continuation_score += 0.8
        signals.append(f"affirmative:{stripped}")
    elif word_count <= 3 and any(tok in stripped for tok in _CONTINUATION_TOKENS):
        continuation_score += 0.5
        signals.append("short_affirmative")
    if word_count <= 5 and not ends_with_question and directive_score < 0.3:
        # Very short non-question with no imperative — likely continuation
        continuation_score += 0.3
        signals.append("short_non_question")

    # ---- Pick winner ----
    scores = {
        "directive": directive_score,
        "continuation": continuation_score,
        "clarification": clarification_score,
    }
    top_label = max(scores, key=lambda k: scores[k])
    top_score = scores[top_label]

    # Confidence is the margin over second-best, normalized
    sorted_scores = sorted(scores.values(), reverse=True)
    margin = sorted_scores[0] - sorted_scores[1]
    confidence = min(1.0, top_score * 0.5 + margin * 0.5)

    # Fallback: ambiguous → directive at low confidence
    if top_score < 0.2:
        return ClassificationResult("directive", 0.2, signals + ["fallback"])

    return ClassificationResult(top_label, confidence, signals)
