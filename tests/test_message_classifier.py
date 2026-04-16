"""Tests for prism.message_classifier."""

import pytest

from prism.message_classifier import classify


# =====================================================================
# Directive
# =====================================================================


class TestDirective:
    @pytest.mark.parametrize("text", [
        "implement the compaction analyzer",
        "Fix the bug in forensics.py",
        "add a test for the new feature",
        "refactor this module",
        "run the tests",
        "check if the build passes",
        "let's move on to the next feature",
        "now build the aggregate view",
        "next, write the MCP tool",
    ])
    def test_imperative_verbs(self, text):
        r = classify(text)
        assert r.label == "directive", f"{text!r} → {r.label} ({r.signals})"
        assert r.confidence > 0.3

    def test_imperative_with_modal(self):
        # "can you implement X" should still be directive, not clarification
        r = classify("can you implement the phase matcher")
        assert r.label == "directive"

    def test_corrective_directive(self):
        r = classify("do it as a pure function instead")
        assert r.label == "directive"

    def test_pivot_phrase(self):
        r = classify("switch to the other approach")
        assert r.label == "directive"


# =====================================================================
# Continuation
# =====================================================================


class TestContinuation:
    @pytest.mark.parametrize("text", [
        "yes",
        "yep",
        "ok",
        "sure",
        "sounds good",
        "go for it",
        "fair enough",
        "perfect",
        "got it",
        "continue",
        "keep going",
    ])
    def test_short_affirmatives(self, text):
        r = classify(text)
        assert r.label == "continuation", f"{text!r} → {r.label} ({r.signals})"

    def test_affirmative_with_punctuation(self):
        assert classify("yes.").label == "continuation"
        assert classify("ok!").label == "continuation"
        assert classify("sure,").label == "continuation"


# =====================================================================
# Clarification
# =====================================================================


class TestClarification:
    @pytest.mark.parametrize("text", [
        "why did you skip the tests?",
        "what does this function do?",
        "how does the phase matcher work?",
        "can you explain the aggregation logic?",
        "help me understand this diff",
        "walk me through the new module",
        "is this the right approach?",
    ])
    def test_questions(self, text):
        r = classify(text)
        assert r.label == "clarification", f"{text!r} → {r.label} ({r.signals})"


# =====================================================================
# Edge cases
# =====================================================================


class TestEdgeCases:
    def test_empty_string(self):
        r = classify("")
        assert r.label == "continuation"
        assert "empty" in r.signals

    def test_whitespace_only(self):
        r = classify("   \n  ")
        assert r.label == "continuation"

    def test_fallback_on_ambiguous(self):
        # Pure noun phrase — no imperative, no question, no affirmative
        r = classify("the widget module performance concerns me")
        # Should fall through to directive fallback at low confidence
        assert r.confidence < 0.6

    def test_result_to_dict(self):
        r = classify("implement X")
        d = r.to_dict()
        assert d["label"] == "directive"
        assert "confidence" in d
        assert isinstance(d["signals"], list)

    def test_confidence_bounded(self):
        r = classify("implement the thing and fix the bug and add a test")
        assert 0.0 <= r.confidence <= 1.0
