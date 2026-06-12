"""Tests for the benchmark grading function (score_recall).

Validates exact-match, keyword-overlap, hyphenated-compound, and
singular/plural variant handling.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from benchmarks.run_benchmark import _keyword_variants, score_recall

# ---------------------------------------------------------------------------
# _keyword_variants
# ---------------------------------------------------------------------------

class TestKeywordVariants:
    def test_plain_word_no_hyphen(self):
        v = _keyword_variants("timeout")
        assert "timeout" in v
        assert "timeouts" in v  # plural added

    def test_plural_word_gets_singular(self):
        v = _keyword_variants("minutes")
        assert "minutes" in v
        assert "minute" in v

    def test_singular_word_gets_plural(self):
        v = _keyword_variants("container")
        assert "container" in v
        assert "containers" in v

    def test_hyphenated_compound_splits(self):
        v = _keyword_variants("ip-level")
        assert "ip-level" in v
        assert "level" in v   # "ip" is len 2 so filtered
        assert "ip" not in v  # too short

    def test_hyphenated_with_long_parts(self):
        v = _keyword_variants("hot-path")
        assert "hot-path" in v
        assert "hot" in v
        assert "path" in v

    def test_number_unit_compound(self):
        # "15-minute" → split parts include "minute"; plural applied to whole word
        v = _keyword_variants("15-minute")
        assert "15-minute" in v
        assert "minute" in v    # split part (len>2, not stop)
        # Matching works via the OTHER direction: _keyword_variants("minutes")
        # returns ["minutes", "minute"], and "minute" appears in "15-minute" text.
        # We don't need "minutes" inside variants of "15-minute" itself.
        v2 = _keyword_variants("minutes")
        assert "minute" in v2   # singular of "minutes"
        # "minute" is in the text "15-minute" as a substring → grading passes


# ---------------------------------------------------------------------------
# score_recall — exact match
# ---------------------------------------------------------------------------

class TestScoreRecallExactMatch:
    def test_exact_substring_match(self):
        text = "Rate limiting: 1000 requests per minute per user token"
        gt = ["1000 requests per minute per user token"]
        recall, matched, missed = score_recall(text, gt)
        assert recall == 1.0
        assert matched == gt
        assert missed == []

    def test_exact_match_case_insensitive(self):
        text = "JWT ACCESS TOKENS expire after 15 minutes"
        gt = ["jwt access tokens expire after 15 minutes"]
        recall, matched, missed = score_recall(text, gt)
        assert recall == 1.0

    def test_no_match(self):
        text = "nothing relevant here"
        gt = ["access tokens: 15 minutes"]
        recall, matched, missed = score_recall(text, gt)
        assert recall == 0.0
        assert missed == gt

    def test_empty_ground_truth(self):
        recall, matched, missed = score_recall("some text", [])
        assert recall == 0.0
        assert matched == []
        assert missed == []

    def test_partial_match(self):
        text = "refresh tokens: 24 hours. access tokens: 15 minutes."
        gt = ["refresh tokens: 24 hours", "access tokens: 15 minutes", "opaque format"]
        recall, matched, missed = score_recall(text, gt)
        assert recall == pytest.approx(2 / 3)
        assert "opaque format" in missed


# ---------------------------------------------------------------------------
# score_recall — keyword fallback
# ---------------------------------------------------------------------------

import pytest  # noqa: E402


class TestScoreRecallKeywordFallback:
    def test_keyword_fallback_basic(self):
        # "15 minutes" not a substring, but keywords are present
        text = "JWT access tokens expire after 15 minutes via a short TTL"
        gt = ["access tokens: 15 minutes"]
        # keywords: ["access", "tokens", "minutes"] (15 filtered, len==2)
        recall, matched, missed = score_recall(text, gt)
        assert recall == 1.0

    def test_hyphenated_unit_matches_plural(self):
        # Model says "15-minute" but ground truth says "15 minutes"
        # _keyword_variants("minutes") → ["minutes", "minute"]
        # "minute" is in "15-minute" via substring → should match
        text = "JWT access tokens use a 15-minute TTL"
        gt = ["access tokens: 15 minutes"]
        recall, matched, missed = score_recall(text, gt)
        assert recall == 1.0, f"missed={missed}"

    def test_ip_level_compound_splits(self):
        # Ground truth has "ip-level", extracted text has "IP level" (two words)
        # _keyword_variants("ip-level") → ["ip-level", "level"]
        # "level" appears in "ip level rate limiting"
        text = "All failed login attempts trigger rate limiting at the IP level"
        gt = ["IP-level rate limiting also applied"]
        # keywords after stop word filter: ["ip-level", "rate", "limiting", "applied"]
        # variants: ip-level→level, rate, limiting, applied
        # "level" in text ✓, "rate" ✓, "limiting" ✓, "applied"? No → 3/4=75%
        recall, matched, missed = score_recall(text, gt)
        assert recall == 1.0, f"missed={missed}"

    def test_ip_level_as_two_words(self):
        # "applied" present explicitly
        text = "IP level rate limiting is also applied to all failed attempts"
        gt = ["IP-level rate limiting also applied"]
        recall, matched, missed = score_recall(text, gt)
        assert recall == 1.0

    def test_below_threshold_fails(self):
        # Only 1 of 4 keywords present → 25% < 70%
        text = "the system uses rate limiting"
        gt = ["OPA policies in Git, deployed as sidecar containers"]
        recall, matched, missed = score_recall(text, gt)
        assert recall == 0.0

    def test_stop_words_excluded(self):
        # "also" is a stop word — must not be counted as a keyword
        # "IP-level rate limiting also applied" → keywords: ip-level, rate, limiting, applied
        text = "rate limiting applied"
        gt = ["IP-level rate limiting also applied"]
        # keywords: ["ip-level","rate","limiting","applied"]
        # variants for ip-level → level; "level" not in text
        # "rate" ✓, "limiting" ✓, "applied" ✓ → 3/4=75% → matched
        recall, matched, missed = score_recall(text, gt)
        assert recall == 1.0

    def test_plural_container_matches_singular(self):
        # ground truth "containers", extracted "container" (singular)
        text = "OPA policies stored in git, deployed as sidecar container"
        gt = ["OPA policies in Git, deployed as sidecar containers"]
        # keywords: opa, policies, git, deployed, sidecar, containers
        # variants for containers → container; "container" in text ✓
        recall, matched, missed = score_recall(text, gt)
        assert recall == 1.0

    def test_multiple_elements_mixed(self):
        text = (
            "access tokens expire after 15 minutes. "
            "refresh tokens last 24 hours on a sliding window. "
            "tokens are stored in the database."
        )
        gt = [
            "access tokens: 15 minutes",
            "refresh tokens: 24 hours sliding window",
            "stored in database",
            "tokens are JWT format",  # not present
        ]
        recall, matched, missed = score_recall(text, gt)
        assert recall == pytest.approx(3 / 4)
        assert "tokens are JWT format" in missed

    def test_exactly_at_threshold(self):
        # 3 keywords, need floor(3*0.7)=2.1 → need 3 for >=70%? No, 2/3=66.7% < 70%
        # Actually 2/3 = 0.667, which is < 0.7 → miss
        # 3/3 = 1.0 → match
        text = "sidecar containers deployed"
        gt = ["git sidecar containers"]
        # keywords: ["git", "sidecar", "containers"] (all len>2, not stop)
        # "git" not in text, "sidecar" ✓, "containers" ✓ → 2/3 = 0.667 → miss
        recall, matched, missed = score_recall(text, gt)
        assert recall == 0.0

    def test_at_threshold_passes(self):
        text = "git sidecar containers"
        gt = ["git sidecar containers"]
        recall, matched, missed = score_recall(text, gt)
        assert recall == 1.0
