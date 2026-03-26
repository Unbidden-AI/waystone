"""
Token counting utilities for LOCOMO benchmark evaluation.

Tracks tokens consumed during:
  - Ingestion (extraction LLM calls)
  - Retrieval (context delivered to evaluator)
  - Evaluation (LLM judge calls)

This is the primary efficiency metric reported alongside accuracy.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class TokenBudget:
    """Accumulated token counts for a single benchmark run."""
    ingestion_input: int = 0    # tokens sent to LLM during extraction
    ingestion_output: int = 0   # tokens received during extraction
    retrieval_context: int = 0  # tokens in retrieved context delivered per query
    judge_input: int = 0        # tokens sent to LLM judge
    judge_output: int = 0       # tokens received from LLM judge
    query_count: int = 0        # number of QA pairs evaluated

    @property
    def avg_retrieval_tokens(self) -> float:
        if self.query_count == 0:
            return 0.0
        return self.retrieval_context / self.query_count

    @property
    def total_tokens(self) -> int:
        return (
            self.ingestion_input
            + self.ingestion_output
            + self.retrieval_context
            + self.judge_input
            + self.judge_output
        )

    def as_dict(self) -> dict:
        return {
            "query_count": self.query_count,
            "avg_retrieval_tokens": round(self.avg_retrieval_tokens, 1),
            "total_ingestion_tokens": self.ingestion_input + self.ingestion_output,
            "total_judge_tokens": self.judge_input + self.judge_output,
            "total_tokens": self.total_tokens,
        }


def estimate_tokens(text: str) -> int:
    """
    Fast token count estimate without calling the tokenizer API.

    Approximation: GPT/Claude tokenizers average ~0.75 tokens/word for English.
    Good enough for tracking context size; not for billing.
    """
    if not text:
        return 0
    words = len(text.split())
    # Account for punctuation and code which tokenizes at ~1:1
    punct_density = len(re.findall(r"[^\w\s]", text)) / max(len(text), 1)
    if punct_density > 0.15:  # code-heavy
        return int(len(text) / 3.5)
    return int(words * 0.75)


def count_from_anthropic_response(response) -> tuple[int, int]:
    """
    Extract (input_tokens, output_tokens) from an Anthropic API response object.
    Returns (0, 0) if usage data is unavailable.
    """
    try:
        usage = response.usage
        return usage.input_tokens, usage.output_tokens
    except AttributeError:
        return 0, 0
