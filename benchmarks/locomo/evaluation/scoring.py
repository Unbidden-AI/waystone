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
import threading
import time
from dataclasses import dataclass
from typing import Sequence


# ---------------------------------------------------------------------------
# Rate limiter — applied only to gpt-* models, only on cache misses
# ---------------------------------------------------------------------------

class _SlidingWindowRateLimiter:
    def __init__(self, max_rpm: int):
        self._max_rpm = max_rpm
        self._lock = threading.Lock()
        self._timestamps: list[float] = []

    def acquire(self) -> None:
        while True:
            with self._lock:
                now = time.monotonic()
                self._timestamps = [t for t in self._timestamps if now - t < 60.0]
                if len(self._timestamps) < self._max_rpm:
                    self._timestamps.append(now)
                    return
            time.sleep(0.1)


# ~25 RPM target — conservative to prevent TPM burst errors when 25 threads fire simultaneously.
# 200K TPM / 3,700 tokens ≈ 54 RPM theoretical; 25 RPM = 46% utilization, leaves headroom
# for concurrent burst and retry overhead without triggering TPM rate limits.
_gpt_rate_limiter = _SlidingWindowRateLimiter(max_rpm=25)


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

    # Word-level overlap — use a word-set from the context to avoid substring
    # false positives (e.g. "and" matching inside "sandbox").
    answer_words = _tokenize(answer_lower)
    if not answer_words:
        return 0.0, [], []

    context_words = set(_tokenize(context_lower))
    matched = [w for w in answer_words if w in context_words]
    missed = [w for w in answer_words if w not in context_words]
    overlap = len(matched) / len(answer_words)

    score = overlap if overlap >= min_overlap else 0.0
    return score, matched, missed


def _tokenize(text: str) -> list[str]:
    """Extract meaningful words (3+ chars, alpha) and 4-digit years from text."""
    alpha_words = re.findall(r"\b[a-z]{3,}\b", text.lower())
    year_tokens = re.findall(r"\b\d{4}\b", text)
    words = alpha_words + year_tokens
    # Remove very common stop words that don't discriminate
    stopwords = {
        "the", "and", "was", "for", "that", "this", "with", "from",
        "are", "were", "have", "had", "has", "been", "they", "their",
        "when", "what", "who", "how", "did", "not", "but", "also",
    }
    return [w for w in words if w not in stopwords]


# ---------------------------------------------------------------------------
# Judge result cache — persist to disk so reruns never re-score identical pairs
# ---------------------------------------------------------------------------

import hashlib
import json as _json
from pathlib import Path as _Path

_JUDGE_CACHE_PATH = _Path.home() / ".cache" / "engram_judge_cache.json"
_judge_cache: dict[str, float] | None = None


def _load_judge_cache() -> dict[str, float]:
    global _judge_cache
    if _judge_cache is None:
        if _JUDGE_CACHE_PATH.exists():
            try:
                _judge_cache = _json.loads(_JUDGE_CACHE_PATH.read_text())
            except Exception:
                _judge_cache = {}
        else:
            _judge_cache = {}
    return _judge_cache


def _save_judge_cache() -> None:
    if _judge_cache is not None:
        _JUDGE_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _JUDGE_CACHE_PATH.write_text(_json.dumps(_judge_cache))


def _judge_cache_key(question: str, answer: str, context: str, model: str) -> str:
    raw = f"{model}\x00{question}\x00{answer}\x00{context}"
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


# ---------------------------------------------------------------------------
# LLM judge scorer (accurate path)
# ---------------------------------------------------------------------------

def score_llm_judge(
    question: str,
    ground_truth_answer: str,
    retrieved_context: str,
    model: str = "gemini-2.5-flash-lite",
    abstention_mode: bool = False,
) -> float:
    """
    Use an LLM to assess whether retrieved_context supports ground_truth_answer.

    Returns a score: 1.0 (supported), 0.5 (partially supported), 0.0 (not supported).

    Results are cached to ~/.cache/engram_judge_cache.json — reruns never re-score
    the same (question, answer, context, model) tuple. Clear the cache file to force
    re-evaluation.

    Model routing:
      - "gemini-*"       → Gemini OpenAI-compatible endpoint
      - "gpt-*"          → OpenAI API (requires OPENAI_API_KEY)
      - "claude-*"       → Anthropic client (use for final paper numbers only)
      - "local:<model>"  → local OpenAI-compatible server (LM Studio / Ollama)
                           URL from LOCAL_LLM_BASE_URL env var, default http://localhost:1234/v1
                           e.g. model="local:qwen2.5-7b-instruct"
    """
    import os
    from pathlib import Path

    # Cache check — skip API call if we've already judged this exact triple
    cache = _load_judge_cache()
    cache_key = _judge_cache_key(question, ground_truth_answer, retrieved_context, model)
    if cache_key in cache:
        return cache[cache_key]

    # Rate-limit only actual API calls (cache hits already returned above)
    if model.startswith("gpt-"):
        _gpt_rate_limiter.acquire()

    env_path = Path(__file__).parent.parent.parent.parent / ".env"
    if env_path.exists():
        from dotenv import load_dotenv
        load_dotenv(env_path, override=False)

    prompt = _judge_prompt(question, ground_truth_answer, retrieved_context, abstention_mode=abstention_mode)

    if model.startswith("gemini"):
        from openai import OpenAI
        client = OpenAI(
            api_key=os.environ["GEMINI_API_KEY"],
            base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        )
        response = client.chat.completions.create(
            model=model,
            max_tokens=16,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.choices[0].message.content.strip().upper()
    elif model.startswith("gpt-"):
        import time as _time
        from openai import OpenAI, RateLimitError as _RateLimitError
        if not os.environ.get("OPENAI_API_KEY"):
            raise RuntimeError("OPENAI_API_KEY not set")
        client = OpenAI()
        for _attempt in range(5):
            try:
                response = client.chat.completions.create(
                    model=model,
                    max_tokens=16,
                    temperature=0.0,
                    messages=[{"role": "user", "content": prompt}],
                )
                break
            except _RateLimitError as e:
                if _attempt == 4:
                    raise
                # Daily quota errors won't clear for hours — don't retry them.
                # TPM/RPM errors are transient and worth retrying.
                err_str = str(e)
                if "requests per day" in err_str or " RPD" in err_str:
                    raise
                # Parse retry-after from error message if available, else exponential backoff
                import re as _re
                m = _re.search(r"try again in ([\d.]+)s", str(e))
                wait = float(m.group(1)) + 0.5 if m else 2 ** (_attempt + 1)
                _time.sleep(wait)
                # Re-acquire a rate limiter slot before retrying so retries don't
                # bypass the RPM cap and cause a retry storm that burns RPD budget.
                _gpt_rate_limiter.acquire()
        raw = response.choices[0].message.content.strip().upper()
    elif model.startswith("local:"):
        # Local OpenAI-compatible server (LM Studio, Ollama, etc.)
        from openai import OpenAI
        local_model = model[len("local:"):]
        base_url = os.environ.get("LOCAL_LLM_BASE_URL", "http://localhost:1234/v1")
        client = OpenAI(api_key="local", base_url=base_url)
        # Disable Qwen3 thinking mode — judge calls need only YES/PARTIAL/NO,
        # not extended reasoning tokens. /no_think via system message works for
        # any Qwen3 model; non-Qwen3 models ignore it harmlessly.
        response = client.chat.completions.create(
            model=local_model,
            max_tokens=16,
            temperature=0.0,
            messages=[
                {"role": "system", "content": "/no_think"},
                {"role": "user", "content": prompt},
            ],
        )
        raw = response.choices[0].message.content.strip().upper()
    else:
        # Anthropic path — claude-* models
        import anthropic
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise RuntimeError("ANTHROPIC_API_KEY not set")
        client = anthropic.Anthropic()
        response = client.messages.create(
            model=model,
            max_tokens=16,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.content[0].text.strip().upper()

    if raw.startswith("YES"):
        score = 1.0
    elif raw.startswith("PARTIAL"):
        score = 0.5
    else:
        score = 0.0

    cache[cache_key] = score
    _save_judge_cache()
    return score


# ---------------------------------------------------------------------------
# Batch API scorer (OpenAI Batch API — separate RPD quota, 50% cheaper)
# ---------------------------------------------------------------------------

def submit_judge_batch(
    requests: list[tuple[str, str, str]],   # [(question, answer, context), ...]
    model: str = "gpt-4o-mini",
    poll_interval: int = 30,
    verbose: bool = False,
    abstention_mode: bool = False,
) -> dict[str, float | None]:
    """
    Submit judge requests via the OpenAI Batch API.

    Hits a separate RPD quota from the sync API, costs 50% less, and supports
    up to 50K requests per batch.  24h SLA is acceptable for offline benchmark
    scoring.

    Returns a dict mapping cache_key -> score (0.0 / 0.5 / 1.0 / None on error)
    for ALL input requests — cache hits are returned immediately without an API
    call; only uncached requests are submitted.
    """
    import io
    import os
    import time as _time
    from pathlib import Path

    env_path = Path(__file__).parent.parent.parent.parent / ".env"
    if env_path.exists():
        from dotenv import load_dotenv
        load_dotenv(env_path, override=False)

    if not os.environ.get("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY not set")

    from openai import OpenAI
    client = OpenAI()

    cache = _load_judge_cache()

    # Partition into cached vs uncached
    results: dict[str, float | None] = {}
    uncached: list[tuple[str, str, str, str]] = []  # (q, a, ctx, key)

    for question, answer, context in requests:
        key = _judge_cache_key(question, answer, context, model)
        if key in cache:
            results[key] = cache[key]
        else:
            results[key] = None
            uncached.append((question, answer, context, key))

    if not uncached:
        if verbose:
            print(f"  [batch] All {len(requests)} requests already cached — no API calls needed")
        return results

    if verbose:
        print(f"  [batch] {len(requests) - len(uncached)} cached, submitting {len(uncached)} to Batch API")

    # Build JSONL request file — deduplicate by key (OpenAI requires unique custom_id)
    import json as _json2
    batch_lines = []
    seen_keys: set[str] = set()
    for question, answer, context, key in uncached:
        if key in seen_keys:
            continue
        seen_keys.add(key)
        prompt = _judge_prompt(question, answer, context, abstention_mode=abstention_mode)
        batch_lines.append(_json2.dumps({
            "custom_id": key,
            "method": "POST",
            "url": "/v1/chat/completions",
            "body": {
                "model": model,
                "max_tokens": 16,
                "temperature": 0.0,
                "messages": [{"role": "user", "content": prompt}],
            },
        }))

    jsonl_bytes = "\n".join(batch_lines).encode()

    # Upload file
    batch_file = client.files.create(
        file=("batch_judge.jsonl", io.BytesIO(jsonl_bytes), "application/jsonl"),
        purpose="batch",
    )
    if verbose:
        print(f"  [batch] Uploaded file_id={batch_file.id}")

    # Submit batch
    batch = client.batches.create(
        input_file_id=batch_file.id,
        endpoint="/v1/chat/completions",
        completion_window="24h",
    )
    if verbose:
        print(f"  [batch] Submitted batch_id={batch.id} — polling every {poll_interval}s")

    # Poll until terminal state
    while True:
        batch = client.batches.retrieve(batch.id)
        status = batch.status
        counts = batch.request_counts
        if verbose:
            print(f"  [batch] status={status} "
                  f"completed={counts.completed}/{counts.total} "
                  f"failed={counts.failed}")
        if status == "completed":
            break
        if status in ("failed", "expired", "cancelled"):
            # Surface whatever error details the API provides before raising so
            # the user can diagnose quota exhaustion vs authentication vs other causes.
            detail_parts = []
            if hasattr(batch, "errors") and batch.errors:
                for err in (batch.errors.data if hasattr(batch.errors, "data") else [batch.errors]):
                    detail_parts.append(str(err))
            if hasattr(batch, "error") and batch.error:
                detail_parts.append(str(batch.error))
            detail = "; ".join(detail_parts) if detail_parts else "(no detail returned by API)"
            if status == "failed":
                detail += (
                    " — common causes: daily token quota (check platform.openai.com/usage), "
                    "invalid request in JSONL, or org billing limit. "
                    "Re-run with sync judging (omit --use-batch) to bypass the Batch API."
                )
            raise RuntimeError(f"Batch {batch.id} ended with status={status}: {detail}")
        _time.sleep(poll_interval)

    # Download and parse results
    output_content = client.files.content(batch.output_file_id)
    for line in output_content.text.splitlines():
        if not line.strip():
            continue
        row = _json2.loads(line)
        key = row["custom_id"]
        if row.get("error"):
            if verbose:
                print(f"  [batch] request {key} error: {row['error']}")
            # Leave results[key] = None
            continue
        raw = row["response"]["body"]["choices"][0]["message"]["content"].strip().upper()
        if raw.startswith("YES"):
            score = 1.0
        elif raw.startswith("PARTIAL"):
            score = 0.5
        else:
            score = 0.0
        results[key] = score
        cache[key] = score

    _save_judge_cache()

    n_scored = sum(1 for v in results.values() if v is not None)
    if verbose:
        print(f"  [batch] Complete — {n_scored}/{len(requests)} scored "
              f"({len(requests) - len(uncached)} from cache, "
              f"{n_scored - (len(requests) - len(uncached))} fresh)")

    return results


def _judge_prompt(question: str, answer: str, context: str, abstention_mode: bool = False) -> str:
    if abstention_mode and not context.strip():
        return f"""You are evaluating a memory retrieval system.

QUESTION: {question}

GROUND TRUTH ANSWER: {answer}

RETRIEVED CONTEXT: (empty — the memory system found no relevant information)

The memory system returned no results for this question.

Reply YES if the correct answer to this question is that the information is unavailable or was never mentioned in the conversation history (i.e. the ground truth itself says there is no answer).
Reply NO if this question should have an answer that a working memory system should have found.

Reply with only YES or NO."""

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

    n = len(qa_pairs)
    kw_scores_list = [score_keyword_recall(ctx, a) for (q, a), ctx in zip(qa_pairs, retrieved_contexts)]
    token_list = [
        (token_counts[i] if token_counts is not None else _estimate_tokens(retrieved_contexts[i]))
        for i in range(n)
    ]

    # Dispatch LLM judge calls concurrently — local models benefit from 4–8 parallel
    # slots (set n_parallel in LM Studio). Sequential calls leave the GPU idle between
    # requests; threading saturates the server's batch queue.
    llm_scores: list[float | None] = [None] * n
    if use_llm_judge:
        import os
        from concurrent.futures import ThreadPoolExecutor, as_completed
        judge_workers = int(os.environ.get("JUDGE_WORKERS", "8"))

        def _judge(idx: int) -> tuple[int, float | None]:
            q, a = qa_pairs[idx]
            try:
                return idx, score_llm_judge(q, a, retrieved_contexts[idx], llm_model)
            except Exception as e:
                if verbose:
                    print(f"  [warn] LLM judge failed for Q{idx}: {e}")
                return idx, None

        with ThreadPoolExecutor(max_workers=judge_workers) as pool:
            futures = {pool.submit(_judge, i): i for i in range(n)}
            done = 0
            for fut in as_completed(futures):
                idx, score = fut.result()
                llm_scores[idx] = score
                done += 1
                if verbose and done % 50 == 0:
                    print(f"  [judge] {done}/{n} done")

    results = []
    for i, ((question, gt_answer), context) in enumerate(zip(qa_pairs, retrieved_contexts)):
        kw_score, matched, missed = kw_scores_list[i]
        results.append(ScoringResult(
            question=question,
            ground_truth_answer=gt_answer,
            retrieved_context=context,
            keyword_score=kw_score,
            llm_score=llm_scores[i],
            matched_keywords=matched,
            missed_keywords=missed,
            tokens_used=token_list[i],
        ))

        if verbose and (i + 1) % 50 == 0:
            avg_kw = sum(r.keyword_score for r in results) / len(results)
            print(f"  [score] {i+1}/{n} done, avg keyword={avg_kw:.3f}")

    return results


def _estimate_tokens(text: str) -> int:
    """Rough token estimate: ~4 chars per token. Matches retriever's estimate."""
    return len(text) // 4


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
