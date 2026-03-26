"""
Scoring for the LOCOMO benchmark.

Two scoring modes:
  1. Keyword recall  — fast, deterministic, no LLM calls.
                       Matches the existing run_benchmark.py score_recall() pattern.
  2. LLM judge       — calls Claude to assess whether retrieved context supports
                       the ground-truth answer. More accurate, used for final paper numbers.

Both return a float in [0, 1].
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Sequence


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class ScoringResult:
    question: str
    ground_truth_answer: str
    retrieved_context: str
    keyword_score: float        # 0.0–1.0
    llm_score: float | None     # 0.0–1.0, None if not evaluated
    matched_keywords: list[str]
    missed_keywords: list[str]
    tokens_used: int            # estimated tokens in retrieved_context


# ---------------------------------------------------------------------------
# Keyword recall scorer (fast path)
# ---------------------------------------------------------------------------

def score_keyword_recall(
    retrieved_context: str,
    ground_truth_answer: str,
    min_overlap: float = 0.7,
) -> tuple[float, list[str], list[str]]:
    """
    Score how well retrieved_context supports ground_truth_answer.

    Strategy (mirrors existing run_benchmark.py score_recall):
      1. Exact substring match → 1.0
      2. Word-level overlap ≥ min_overlap → partial credit
      3. Otherwise → 0.0

    Returns:
        (score, matched_keywords, missed_keywords)
    """
    context_lower = retrieved_context.lower()
    answer_lower = str(ground_truth_answer).lower()

    # Exact match
    if answer_lower in context_lower:
        answer_words = _tokenize(answer_lower)
        return 1.0, answer_words, []

    # Word-level overlap
    answer_words = _tokenize(answer_lower)
    if not answer_words:
        return 0.0, [], []

    matched = [w for w in answer_words if w in context_lower]
    missed = [w for w in answer_words if w not in context_lower]
    overlap = len(matched) / len(answer_words)

    score = overlap if overlap >= min_overlap else 0.0
    return score, matched, missed


def _tokenize(text: str) -> list[str]:
    """Extract meaningful words (3+ chars, alpha) from text."""
    words = re.findall(r"\b[a-z]{3,}\b", text.lower())
    # Remove very common stop words that don't discriminate
    stopwords = {
        "the", "and", "was", "for", "that", "this", "with", "from",
        "are", "were", "have", "had", "has", "been", "they", "their",
        "when", "what", "who", "how", "did", "not", "but", "also",
    }
    return [w for w in words if w not in stopwords]


# ---------------------------------------------------------------------------
# LLM judge scorer (accurate path)
# ---------------------------------------------------------------------------

def score_llm_judge(
    question: str,
    ground_truth_answer: str,
    retrieved_context: str,
    model: str = "claude-haiku-4-5-20251001",
) -> float:
    """
    Use an LLM to assess whether retrieved_context supports ground_truth_answer.

    Returns a score: 1.0 (supported), 0.5 (partially supported), 0.0 (not supported).
    Uses Haiku by default — cheap enough to run on all 1,986 QA pairs.
    """
    import anthropic

    client = anthropic.Anthropic()

    prompt = _judge_prompt(question, ground_truth_answer, retrieved_context)

    response = client.messages.create(
        model=model,
        max_tokens=64,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = response.content[0].text.strip().upper()

    if raw.startswith("YES"):
        return 1.0
    if raw.startswith("PARTIAL"):
        return 0.5
    return 0.0


def _judge_prompt(question: str, answer: str, context: str) -> str:
    return f"""You are evaluating a memory retrieval system.

QUESTION: {question}

GROUND TRUTH ANSWER: {answer}

RETRIEVED CONTEXT:
{context}

Does the retrieved context contain enough information to correctly answer the question with the ground truth answer?

Reply with exactly one of:
- YES (context clearly supports the ground truth answer)
- PARTIAL (context has some relevant info but is incomplete or imprecise)
- NO (context does not support the ground truth answer)

Reply with only YES, PARTIAL, or NO."""


# ---------------------------------------------------------------------------
# Batch scoring utility
# ---------------------------------------------------------------------------

def score_batch(
    qa_pairs: Sequence[tuple[str, str]],   # [(question, ground_truth_answer), ...]
    retrieved_contexts: Sequence[str],
    token_counts: Sequence[int] | None = None,
    use_llm_judge: bool = False,
    llm_model: str = "claude-haiku-4-5-20251001",
    verbose: bool = False,
) -> list[ScoringResult]:
    """
    Score a batch of (question, answer) pairs against their retrieved contexts.

    Args:
        qa_pairs: List of (question, ground_truth_answer).
        retrieved_contexts: Corresponding retrieved context strings.
        token_counts: Estimated token counts; estimated from word count if None.
        use_llm_judge: Whether to also run LLM judge scoring.
        llm_model: Model for LLM judge.
        verbose: Print progress.

    Returns:
        List of ScoringResult.
    """
    assert len(qa_pairs) == len(retrieved_contexts), "Mismatched qa_pairs/contexts"

    results = []
    for i, ((question, gt_answer), context) in enumerate(
        zip(qa_pairs, retrieved_contexts)
    ):
        kw_score, matched, missed = score_keyword_recall(context, gt_answer)

        tokens = (
            token_counts[i]
            if token_counts is not None
            else _estimate_tokens(context)
        )

        llm_score = None
        if use_llm_judge:
            try:
                llm_score = score_llm_judge(question, gt_answer, context, llm_model)
            except Exception as e:
                if verbose:
                    print(f"  [warn] LLM judge failed for Q{i}: {e}")

        results.append(ScoringResult(
            question=question,
            ground_truth_answer=gt_answer,
            retrieved_context=context,
            keyword_score=kw_score,
            llm_score=llm_score,
            matched_keywords=matched,
            missed_keywords=missed,
            tokens_used=tokens,
        ))

        if verbose and (i + 1) % 50 == 0:
            avg_kw = sum(r.keyword_score for r in results) / len(results)
            print(f"  [score] {i+1}/{len(qa_pairs)} done, avg keyword={avg_kw:.3f}")

    return results


def _estimate_tokens(text: str) -> int:
    """Rough token estimate: ~0.75 tokens per word."""
    return int(len(text.split()) * 0.75)


# ---------------------------------------------------------------------------
# Aggregate metrics
# ---------------------------------------------------------------------------

def aggregate(results: list[ScoringResult]) -> dict:
    """Compute aggregate metrics from a list of ScoringResults."""
    if not results:
        return {}

    kw_scores = [r.keyword_score for r in results]
    kw_acc = sum(1 for s in kw_scores if s > 0) / len(kw_scores)
    kw_exact = sum(1 for s in kw_scores if s == 1.0) / len(kw_scores)

    metrics = {
        "n": len(results),
        "keyword_accuracy": round(kw_acc, 4),      # any credit
        "keyword_exact": round(kw_exact, 4),        # exact match only
        "avg_tokens": round(sum(r.tokens_used for r in results) / len(results), 1),
    }

    llm_scores = [r.llm_score for r in results if r.llm_score is not None]
    if llm_scores:
        metrics["llm_accuracy"] = round(
            sum(1 for s in llm_scores if s >= 1.0) / len(llm_scores), 4
        )
        metrics["llm_partial_credit"] = round(
            sum(s for s in llm_scores) / len(llm_scores), 4
        )
        metrics["llm_n"] = len(llm_scores)

    return metrics
