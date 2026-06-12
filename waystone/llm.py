"""LLM provider abstraction for Waystone.

The default path is the existing OpenAI-compatible httpx client — no new
dependencies, works with any model. The GeminiNativeProvider is an optional
add-in that unlocks Google-specific capabilities:

  - count_tokens()     — pre-flight token check; eliminates bisection retries
  - context caching    — CachedContent for repeated system prompts (~4× cheaper)
  - batch submission   — 50% cost reduction for async workloads (judge calls, sweeps)

Enable by installing ``google-genai`` and setting ``llm.use_native_sdk: true``
in config.yaml. Falls back silently to OpenAI-compatible path if not installed.

Usage (from extractor.py or scoring.py):

    from .llm import get_provider

    provider = get_provider(config)
    if provider.supports_count_tokens:
        n = provider.count_tokens(prompt)
    result = await provider.complete_async(prompt, max_tokens=4096)
"""

from __future__ import annotations

import hashlib
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

log = logging.getLogger(__name__)

# Module-level provider cache — avoids recreating the API client on every call
_provider_instances: dict[str, "LLMProvider"] = {}


# ---------------------------------------------------------------------------
# Provider protocol
# ---------------------------------------------------------------------------

class LLMProvider:
    """Base interface. All methods raise NotImplementedError by default."""

    supports_count_tokens: bool = False
    supports_batch: bool = False

    def count_tokens(self, prompt: str) -> int:
        raise NotImplementedError

    async def complete_async(self, prompt: str, *, max_tokens: int, **kwargs) -> str:
        raise NotImplementedError

    def complete_sync(self, prompt: str, *, max_tokens: int, **kwargs) -> str:
        raise NotImplementedError

    def submit_batch(self, requests: list[dict]) -> str:
        """Submit a batch job. Returns a job ID / resource name."""
        raise NotImplementedError

    def poll_batch(self, job_id: str) -> list[str] | None:
        """Check batch status. Returns list of results when done, None if still running."""
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Gemini native provider (optional — requires google-genai)
# ---------------------------------------------------------------------------

class GeminiNativeProvider(LLMProvider):
    """Google AI SDK provider with count_tokens, context caching, and batch support.

    Install: pip install google-genai
    Enable:  llm.use_native_sdk: true in config.yaml

    Context caching:
        Call cache_system_prompt(system_prompt) once per run to create a
        CachedContent object. Subsequent complete_async() calls reuse the
        cache, paying only cache-read rates (~4x cheaper) for the system
        prompt portion.

    Batch API:
        submit_batch(requests) uploads a JSONL file and starts an async batch
        job. poll_batch(job_id) returns results when done. Results are written
        back to engram_judge_cache.json by the caller.
    """

    supports_count_tokens = True
    supports_batch = True

    def __init__(self, model: str, api_key: str):
        try:
            from google import genai
            from google.genai import types as genai_types
        except ImportError:
            raise ImportError(
                "google-genai is required for GeminiNativeProvider. "
                "Install it with: pip install google-genai"
            )
        self._genai = genai
        self._types = genai_types
        self._client = genai.Client(api_key=api_key)
        self._model = model
        self._cached_content = None   # set by cache_system_prompt()

    # ------------------------------------------------------------------
    # Token counting
    # ------------------------------------------------------------------

    def count_tokens(self, prompt: str) -> int:
        """Return exact token count without making a generation call."""
        response = self._client.models.count_tokens(
            model=self._model,
            contents=prompt,
        )
        return response.total_tokens

    # ------------------------------------------------------------------
    # Context caching
    # ------------------------------------------------------------------

    def cache_system_prompt(self, system_prompt: str, ttl_seconds: int = 3600) -> None:
        """Upload the system prompt as a CachedContent object.

        Cached tokens cost ~4x less than regular input tokens. Call this once
        per benchmark run before making extraction calls.
        """
        self._cached_content = self._client.caches.create(
            model=self._model,
            contents=[
                self._types.Content(
                    role="user",
                    parts=[self._types.Part(text=system_prompt)],
                )
            ],
            ttl=f"{ttl_seconds}s",
        )
        log.info("Created CachedContent: %s", self._cached_content.name)

    def clear_cache(self) -> None:
        """Delete the active CachedContent and release the cached token slot."""
        if self._cached_content:
            try:
                self._client.caches.delete(name=self._cached_content.name)
            except Exception:
                pass
            self._cached_content = None

    # ------------------------------------------------------------------
    # Single completion (async)
    # ------------------------------------------------------------------

    async def complete_async(self, prompt: str, *, max_tokens: int, **kwargs) -> str:
        import asyncio
        loop = asyncio.get_event_loop()
        future = loop.run_in_executor(
            None, lambda: self.complete_sync(prompt, max_tokens=max_tokens, **kwargs)
        )
        return await asyncio.wait_for(future, timeout=300)  # 5 min hard cap — prevents indefinite hangs

    @staticmethod
    def _classify_error(e: Exception) -> str:
        """Classify an API exception for retry policy.

        Returns one of:
          'transient'        — 503/500/502; short backoff, retry
          'rate_limit'       — 429 RPM/TPM burst; longer backoff, retry
          'quota_exhausted'  — 429 daily/billing/project hard cap; fail fast
          'fatal'            — non-retryable (auth, bad request, etc.)
        """
        err_lower = str(e).lower()

        # Check google-api-core exception hierarchy first (most reliable)
        try:
            from google.api_core import exceptions as _gexc
            if isinstance(e, _gexc.ResourceExhausted):
                # Distinguish hard quota (per-day / billing) from burst RPM/TPM.
                # Hard-quota messages typically contain "per day", "daily", or
                # "quota exceeded" while RPM/TPM messages say "rate limit exceeded"
                # or "too many requests".
                if any(phrase in err_lower for phrase in (
                    "per day", "daily limit", "monthly", "billing",
                    "quota exceeded", "project quota",
                )):
                    return "quota_exhausted"
                return "rate_limit"
            if isinstance(e, (_gexc.ServiceUnavailable, _gexc.InternalServerError)):
                return "transient"
        except ImportError:
            pass

        # Fall back to string matching for non-google-api-core wrappers
        if any(s in err_lower for s in ("503", "502", "service unavailable",
                                         "500", "internal server")):
            return "transient"
        if any(s in err_lower for s in ("per day", "daily limit", "monthly",
                                         "billing", "quota exceeded",
                                         "project quota")):
            return "quota_exhausted"
        if any(s in err_lower for s in ("429", "rate limit", "resource exhausted",
                                         "too many requests", "overloaded")):
            return "rate_limit"
        return "fatal"

    @staticmethod
    def _extract_retry_after(e: Exception) -> float | None:
        """Try to parse a suggested retry delay from the exception.

        Checks (in order):
          1. trailing_metadata retry-after header (google-api-core)
          2. RetryInfo.retry_delay in error details (google-api-core)
          3. "try again in Xs" / "retry after Xs" substring in error message
        """
        import re as _re
        try:
            if hasattr(e, "trailing_metadata") and e.trailing_metadata:
                for key, value in e.trailing_metadata:
                    if key.lower() == "retry-after":
                        return float(value) + 0.5
            if hasattr(e, "details") and e.details:
                for detail in e.details:
                    if hasattr(detail, "retry_delay"):
                        d = detail.retry_delay
                        seconds = getattr(d, "seconds", 0) + getattr(d, "nanos", 0) / 1e9
                        if seconds > 0:
                            return seconds + 0.5
        except (ImportError, Exception):
            pass
        m = _re.search(r"(?:retry after|try again in)\s+(\d+(?:\.\d+)?)\s*s",
                        str(e).lower())
        if m:
            return float(m.group(1)) + 1.0
        return None

    def complete_sync(self, prompt: str, *, max_tokens: int, system: str | None = None, **kwargs) -> str:
        import time as _time
        config_kwargs = dict(max_output_tokens=max_tokens, temperature=0.1)
        if system:
            config_kwargs["system_instruction"] = system
        config_kwargs.update(kwargs)
        gen_cfg = self._types.GenerateContentConfig(**config_kwargs)

        # Retry policy by error class:
        #   transient  (503/500): short backoff — server hiccup, usually clears fast
        #   rate_limit (429 RPM/TPM): longer backoff — with 300–1000 RPM / 2–4M TPM
        #     on Tier 1, a burst should clear within 15–60s; respect Retry-After
        #     header when present
        #   quota_exhausted (daily/billing cap): fail immediately — retrying in
        #     seconds is useless; the caller must know to pause or check billing
        _TRANSIENT_BACKOFF = (5, 15, 45)       # up to 3 retries
        _RATE_LIMIT_BACKOFF = (15, 30, 60)     # up to 3 retries

        response = None
        attempt = 0
        while True:
            try:
                if self._cached_content:
                    response = self._client.models.generate_content(
                        model=self._model,
                        contents=prompt,
                        config=gen_cfg,
                        cached_content=self._cached_content.name,
                    )
                else:
                    response = self._client.models.generate_content(
                        model=self._model,
                        contents=prompt,
                        config=gen_cfg,
                    )
                break  # success
            except Exception as e:
                kind = self._classify_error(e)

                if kind == "quota_exhausted":
                    raise RuntimeError(
                        f"Gemini quota exhausted (daily/billing/project hard cap). "
                        f"Check https://aistudio.google.com/app/usage or your GCP "
                        f"billing console. Original error: {e}"
                    ) from e

                if kind == "transient":
                    backoff = _TRANSIENT_BACKOFF
                elif kind == "rate_limit":
                    backoff = _RATE_LIMIT_BACKOFF
                else:
                    raise  # fatal / auth / bad request — don't retry

                if attempt >= len(backoff):
                    raise  # max retries exceeded

                # Prefer API-supplied retry delay; fall back to schedule.
                # Cap at 120s — server sometimes suggests hours (e.g. RetryInfo.retry_delay=3600s)
                # which would block extraction indefinitely.
                suggested = self._extract_retry_after(e)
                wait = min(suggested, 120) if suggested is not None else backoff[attempt]
                log.warning(
                    "Gemini %s (attempt %d/%d), retrying in %.0fs: %s",
                    kind, attempt + 1, len(backoff), wait, e,
                )
                _time.sleep(wait)
                attempt += 1

        # Check finish reason
        candidate = response.candidates[0]
        finish = candidate.finish_reason
        if str(finish) in ("FinishReason.MAX_TOKENS", "MAX_TOKENS", "length"):
            raise ValueError(
                f"LLM response was truncated (finish_reason=MAX_TOKENS, "
                f"max_tokens={max_tokens}). Try increasing max_tokens in config.yaml "
                f"or use count_tokens() to pre-check input size."
            )

        return response.text

    # ------------------------------------------------------------------
    # Batch API
    # ------------------------------------------------------------------

    def submit_batch(self, requests: list[dict]) -> str:
        """Submit a batch of generation requests.

        Each request is a dict with keys: ``prompt`` (str), ``cache_key`` (str).
        Returns the batch job resource name.

        Batch results come back within 24 hours at 50% of normal cost.
        """

        # Build JSONL inline requests
        inline_requests = []
        for req in requests:
            inline_requests.append(
                self._types.EmbedContentRequest(  # re-used as generic request wrapper
                    content=self._types.Content(
                        parts=[self._types.Part(text=req["prompt"])]
                    )
                )
            )

        batch_job = self._client.batches.create(
            model=self._model,
            src=inline_requests,
            config=self._types.CreateBatchJobConfig(
                display_name="waystone-judge-batch",
            ),
        )
        log.info("Submitted batch job: %s (%d requests)", batch_job.name, len(requests))
        return batch_job.name

    def poll_batch(self, job_id: str) -> list[str] | None:
        """Check batch job status. Returns list of text responses if done, else None."""
        job = self._client.batches.get(name=job_id)
        state = str(job.state)
        log.debug("Batch job %s state: %s", job_id, state)

        if "SUCCEEDED" not in state and "COMPLETE" not in state:
            return None

        results = []
        for response in job.responses:
            try:
                results.append(response.response.candidates[0].content.parts[0].text)
            except Exception:
                results.append("")
        return results

    def cancel_batch(self, job_id: str) -> None:
        self._client.batches.cancel(name=job_id)

    def delete_batch(self, job_id: str) -> None:
        self._client.batches.delete(name=job_id)


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def get_provider(config: dict) -> LLMProvider | None:
    """Return a GeminiNativeProvider if native SDK is enabled and available.

    Returns None if:
      - llm.use_native_sdk is not True in config
      - google-genai is not installed
      - model is not a gemini-* model
      - API key is not resolvable

    Callers should fall back to the existing OpenAI-compatible path when
    this returns None.
    """
    llm_cfg = config.get("llm", {})
    if not llm_cfg.get("use_native_sdk", False):
        return None

    model = llm_cfg.get("model", "")
    if not model.startswith("gemini"):
        log.debug("use_native_sdk=true but model %r is not a gemini model — skipping", model)
        return None

    # Resolve API key (same env var logic as extractor.py)
    # Single source of truth for key resolution (env var → inline → generic).
    from .config import resolve_llm_api_key
    api_key, _key_source = resolve_llm_api_key(llm_cfg)

    if not api_key:
        log.warning("use_native_sdk=true but no API key found — falling back to OpenAI-compatible path")
        return None

    # Hash the API key to avoid leaking it in cache keys or logs
    key_hash = hashlib.sha256(api_key.encode()).hexdigest()[:16]
    cache_key = f"{model}:{key_hash}"
    if cache_key in _provider_instances:
        return _provider_instances[cache_key]

    try:
        provider = GeminiNativeProvider(model=model, api_key=api_key)
        _provider_instances[cache_key] = provider
        return provider
    except ImportError as e:
        log.warning("use_native_sdk=true but google-genai not installed (%s) — falling back", e)
        return None
