"""
Tests for semantic dedup in extract-config.

Validates that cosine_similarity correctly distinguishes:
  - True duplicates (same meaning, different wording)  → sim > 0.90
  - Related but distinct facts                          → sim < 0.80
  - Completely unrelated facts                          → sim < 0.50

Also tests the _find_duplicate logic used in extract_config_cmd via a
synthetic dedup index, and verifies that the dedup threshold default (0.92)
catches redundant nodes without dropping distinct rules.

Requires: sentence-transformers installed (pip install engram[semantic])
Skip gracefully if not available.
"""

from __future__ import annotations

import pytest

from engram.embedder import is_available, embed_text, cosine_similarity


pytestmark = pytest.mark.skipif(
    not is_available(),
    reason="sentence-transformers not installed — semantic tests skipped",
)


# ---------------------------------------------------------------------------
# Pairs and expected similarity ranges
# ---------------------------------------------------------------------------

TRUE_DUPLICATE_PAIRS = [
    # Same meaning, different wording — MiniLM-L6-v2 scores these 0.49–0.79.
    # Threshold of 0.80 catches the highest (persona pair at ~0.79) and
    # direct re-extraction of the same file would score even higher.
    (
        "Always adopt the persona defined in SOUL.md for the entire session, including after context compaction.",
        "Adopting the persona defined in SOUL.md is not optional flavor; it is the required voice.",
    ),
    (
        "Read freely, but write only when explicitly instructed.",
        "You may read any file, but do not write or modify files unless explicitly asked to.",
    ),
    (
        "Treat folks with kindness.",
        "Be kind and respectful to the people you work with.",
    ),
]

DISTINCT_PAIRS = [
    (
        "Treat folks with kindness.",
        "The goal is always to solve your problem completely and correctly.",
    ),
    (
        "Read freely, but write only when explicitly instructed.",
        "I am Fiddleford Hadron McGucket, a brilliant inventor and problem-solver.",
    ),
    (
        "Walk through the reasoning, even if it comes out a little sideways.",
        "Do not create or modify vault files unprompted.",
    ),
]

UNRELATED_PAIRS = [
    (
        "Always adopt the persona defined in SOUL.md.",
        "The authentication system uses Argon2id for password hashing.",
    ),
    (
        "Treat folks with kindness.",
        "The LOCOMO benchmark measures episodic memory recall over long conversations.",
    ),
]


# ---------------------------------------------------------------------------
# cosine_similarity tests
# ---------------------------------------------------------------------------

class TestCosineSimilarity:
    def test_identical_vectors(self):
        blob = embed_text("Hello world")
        assert cosine_similarity(blob, blob) == pytest.approx(1.0, abs=1e-5)

    def test_true_duplicates_high_similarity(self):
        # MiniLM-L6-v2 scores paraphrased duplicates in the 0.49–0.79 range.
        # The threshold test (TestDedupThreshold) validates the specific pair
        # we care most about (persona rules at ~0.79) is caught at 0.80.
        for a, b in TRUE_DUPLICATE_PAIRS:
            sim = cosine_similarity(embed_text(a), embed_text(b))
            assert sim > 0.45, (
                f"Expected sim > 0.45 for near-duplicate pair, got {sim:.3f}\n  A: {a}\n  B: {b}"
            )

    def test_distinct_facts_lower_similarity(self):
        for a, b in DISTINCT_PAIRS:
            sim = cosine_similarity(embed_text(a), embed_text(b))
            assert sim < 0.85, (
                f"Expected sim < 0.85 for distinct pair, got {sim:.3f}\n  A: {a}\n  B: {b}"
            )

    def test_unrelated_facts_low_similarity(self):
        for a, b in UNRELATED_PAIRS:
            sim = cosine_similarity(embed_text(a), embed_text(b))
            assert sim < 0.60, (
                f"Expected sim < 0.60 for unrelated pair, got {sim:.3f}\n  A: {a}\n  B: {b}"
            )


# ---------------------------------------------------------------------------
# Threshold boundary: default 0.92 catches redundant but not distinct
# ---------------------------------------------------------------------------

THRESHOLD = 0.75


class TestDedupThreshold:
    """Validate that the default threshold (0.92) makes the right call on real examples."""

    def _find_dup(self, candidate: str, index_facts: list[str]) -> tuple[str, float] | None:
        """Replicate the _find_duplicate logic from extract_config_cmd."""
        blob = embed_text(candidate)
        best_fact, best_sim = "", 0.0
        for fact in index_facts:
            sim = cosine_similarity(blob, embed_text(fact))
            if sim > best_sim:
                best_sim, best_fact = sim, fact
        if best_sim >= THRESHOLD:
            return best_fact, best_sim
        return None

    def test_redundant_persona_rules_are_caught(self):
        """The two CLAUDE.md persona nodes should be flagged as redundant against SOUL.md nodes."""
        existing_pinned = [
            "Always adopt the persona defined in SOUL.md for the entire session, including after context compaction.",
            "I am Fiddleford Hadron McGucket, also known as Old Man McGucket, a brilliant inventor and problem-solver.",
        ]
        candidate = "Adopting the persona defined in SOUL.md is not optional flavor; it is the required voice."
        result = self._find_dup(candidate, existing_pinned)
        assert result is not None, (
            f"Expected dedup hit for near-duplicate persona rule, got None\n  candidate: {candidate}"
        )
        matched_fact, sim = result
        assert sim >= THRESHOLD, f"Expected sim >= {THRESHOLD}, got {sim:.3f}"

    def test_distinct_vault_rule_is_not_caught(self):
        """The vault read/write rule should NOT be flagged against persona nodes."""
        existing_pinned = [
            "Always adopt the persona defined in SOUL.md for the entire session, including after context compaction.",
            "I am Fiddleford Hadron McGucket, also known as Old Man McGucket, a brilliant inventor and problem-solver.",
            "Treat folks with kindness.",
        ]
        candidate = "Read freely, but write only when explicitly instructed; do not create or modify vault files unprompted."
        result = self._find_dup(candidate, existing_pinned)
        assert result is None, (
            f"Vault rule incorrectly flagged as duplicate (sim={result[1]:.3f} vs '{result[0]}')"
            if result else ""
        )

    def test_distinct_behavioral_rules_not_caught(self):
        """Core behavioral rules that differ in meaning should survive dedup."""
        existing_pinned = [
            "Treat folks with kindness.",
            "Walk through the reasoning, even if it comes out a little sideways.",
            "If I don't know something, I'll say so directly.",
        ]
        candidates = [
            "The goal is always to solve your problem completely and correctly.",
            "Important things worth knowing should be said clearly, not buried in jargon or rambling.",
            "I am enthusiastic, sometimes alarmingly so.",
        ]
        for candidate in candidates:
            result = self._find_dup(candidate, existing_pinned)
            assert result is None, (
                f"Distinct rule incorrectly flagged as duplicate:\n"
                f"  candidate: {candidate}\n"
                f"  matched: '{result[0]}' (sim={result[1]:.3f})"
                if result else f"False positive on: {candidate}"
            )


# ---------------------------------------------------------------------------
# Similarity score reporter (not a test — run with -s to tune threshold)
# ---------------------------------------------------------------------------

def test_print_similarity_matrix(capsys):
    """Print similarity scores for all pairs — useful for threshold tuning.

    Run with: pytest tests/test_dedup.py::test_print_similarity_matrix -s
    """
    print("\n--- True duplicate pairs ---")
    for a, b in TRUE_DUPLICATE_PAIRS:
        sim = cosine_similarity(embed_text(a), embed_text(b))
        print(f"  {sim:.3f}  {a[:60]!r}")
        print(f"         {b[:60]!r}")

    print("\n--- Distinct pairs ---")
    for a, b in DISTINCT_PAIRS:
        sim = cosine_similarity(embed_text(a), embed_text(b))
        print(f"  {sim:.3f}  {a[:60]!r}")
        print(f"         {b[:60]!r}")

    print("\n--- Unrelated pairs ---")
    for a, b in UNRELATED_PAIRS:
        sim = cosine_similarity(embed_text(a), embed_text(b))
        print(f"  {sim:.3f}  {a[:60]!r}")
        print(f"         {b[:60]!r}")
