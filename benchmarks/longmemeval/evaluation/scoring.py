"""
LongMemEval scoring — re-exports LOCOMO scoring with LME-specific extensions.

The core judge (GPT-4o-mini) and keyword scoring are identical to LOCOMO.
The only addition is absent-information handling: when retrieval returns no
context for an absent-info question, the correct answer is "I don't know",
so an empty-context response should score 1.0, not 0.0.

Usage:
    from benchmarks.longmemeval.evaluation.scoring import (
        score_keyword_recall,
        score_llm_judge,
        submit_judge_batch,
        aggregate,
        ScoringResult,
    )
"""

# Re-export everything from LOCOMO scoring unchanged.
from benchmarks.locomo.evaluation.scoring import (
    score_keyword_recall,
    score_llm_judge,
    submit_judge_batch,
    aggregate,
    ScoringResult,
    _judge_cache_key,
)

__all__ = [
    "score_keyword_recall",
    "score_llm_judge",
    "submit_judge_batch",
    "aggregate",
    "ScoringResult",
    "_judge_cache_key",
]
