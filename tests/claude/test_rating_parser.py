"""Tests for the LLM judge rating parser — RB-32a.

The _extract_rating function is used by Phase L quality evaluation tests.
It must correctly extract the final verdict from judge responses, even when
rating keywords appear in the reasoning section.

The bug: the parser checked for POOR before GOOD in the fallback path,
so "this is not POOR... Rating: GOOD" returned POOR. The fix uses
last-occurrence logic — whichever keyword appears latest wins.
"""

import re
import sys
from pathlib import Path

import pytest

# Import the function under test from the live test module
sys.path.insert(0, str(Path(__file__).resolve().parent / "live"))
from test_phase_l_quality_eval import _extract_rating


class TestExtractRatingExplicitPatterns:
    """Explicit rating patterns (Rating: X, **X**, Verdict: X) take priority."""

    def test_rating_colon_good(self):
        assert _extract_rating("Some analysis. Rating: GOOD") == "GOOD"

    def test_rating_colon_poor(self):
        assert _extract_rating("Terrible output. Rating: POOR") == "POOR"

    def test_rating_colon_acceptable(self):
        assert _extract_rating("Decent. Rating: ACCEPTABLE") == "ACCEPTABLE"

    def test_bold_markdown_good(self):
        assert _extract_rating("Overall: **GOOD**") == "GOOD"

    def test_bold_markdown_poor(self):
        assert _extract_rating("Overall: **POOR**") == "POOR"

    def test_verdict_pattern(self):
        assert _extract_rating("Verdict: ACCEPTABLE") == "ACCEPTABLE"

    def test_overall_pattern(self):
        assert _extract_rating("Overall: GOOD") == "GOOD"

    def test_explicit_overrides_earlier_mention(self):
        """Explicit pattern wins even if a different keyword appeared earlier."""
        assert _extract_rating(
            "The output could be seen as POOR by some standards, "
            "but it meets the criteria. Rating: GOOD"
        ) == "GOOD"


class TestExtractRatingLastOccurrence:
    """Fallback: when no explicit pattern, last occurrence wins."""

    def test_poor_in_reasoning_good_at_end(self):
        """Judge mentions POOR in reasoning but concludes GOOD."""
        assert _extract_rating(
            "Some might say this is POOR but I disagree. "
            "The quality is actually quite GOOD"
        ) == "GOOD"

    def test_good_in_reasoning_poor_at_end(self):
        """Judge initially says GOOD but concludes POOR."""
        assert _extract_rating(
            "This looks GOOD initially but upon review it is POOR"
        ) == "POOR"

    def test_acceptable_after_poor_mention(self):
        assert _extract_rating(
            "Not POOR but not great either. ACCEPTABLE"
        ) == "ACCEPTABLE"

    def test_multiple_mentions_last_wins(self):
        """Multiple keywords — whichever is last in the text wins."""
        assert _extract_rating(
            "POOR start, then GOOD middle, then ACCEPTABLE end"
        ) == "ACCEPTABLE"

    def test_only_good(self):
        assert _extract_rating("This is GOOD") == "GOOD"

    def test_only_poor(self):
        assert _extract_rating("This is POOR") == "POOR"


class TestExtractRatingEdgeCases:
    """Edge cases and robustness."""

    def test_case_insensitive_input(self):
        """Keywords found regardless of surrounding case."""
        assert _extract_rating("the output is good overall GOOD") == "GOOD"

    def test_no_rating_keyword_defaults_to_acceptable(self):
        """Defaults to ACCEPTABLE when no keyword present (safe fallback)."""
        result = _extract_rating("This response has no rating keywords at all.")
        assert result == "ACCEPTABLE"

    def test_poor_in_negation_good_explicit(self):
        """'not POOR' in reasoning, explicit GOOD verdict."""
        assert _extract_rating(
            "This is definitely not POOR quality. The summaries capture "
            "key themes and compress effectively. Rating: GOOD"
        ) == "GOOD"

    def test_whitespace_around_rating(self):
        assert _extract_rating("Rating:   GOOD  ") == "GOOD"
