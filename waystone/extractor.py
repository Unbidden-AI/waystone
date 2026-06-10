"""LLM-based extraction service for Waystone."""

import json
import logging
import os
import re
import uuid

from .prompts import (
    build_extraction_json_schema,
    TARGETED_PASS_PROMPTS,
    build_extraction_prompt,
    build_implicit_prefs_prompt,
    build_incremental_prompt,
    build_reconcile_prompt,
    build_synthesis_prompt,
    build_targeted_prompt,
    build_verification_prompt,
)

log = logging.getLogger(__name__)

# Pattern for existing node IDs assigned by the store (n_ + 8 hex chars)
_EXISTING_ID_PATTERN = re.compile(r"^n_[0-9a-f]{8}$")


def _classify_llm_error(exc: Exception, status_code: int | None = None) -> str:
    """Classify an LLM error into actionable categories.

    Returns one of: "auth", "rate_limit", "quota", "timeout", "api"
    """
    exc_str = str(exc).lower()

    # Auth errors
    if status_code in (401, 403) or any(term in exc_str for term in ("invalid api key", "authentication", "unauthorized")):
        return "auth"

    # Rate limit errors
    if status_code == 429 or "rate limit" in exc_str or "quota exceeded" in exc_str:
        return "rate_limit"

    # Quota/billing errors
    if any(term in exc_str for term in ("quota", "billing", "insufficient quota")):
        return "quota"

    # Timeout errors (asyncio.TimeoutError is a subclass of TimeoutError in Python 3.11+)
    if isinstance(exc, TimeoutError) or "timeout" in exc_str:
        return "timeout"

    # Everything else
    return "api"


class ExtractionBuffer:
    """Buffers conversation turns and decides when to flush to the LLM."""

    MIN_TURNS = 3
    MIN_WORDS = 200
    MAX_TURNS = 10
    SHORT_TURN_WORDS = 20

    def __init__(self, min_turns: int | None = None, min_words: int | None = None,
                 max_turns: int | None = None, short_turn_words: int | None = None):
        self._turns: list[str] = []
        self._min_turns = min_turns if min_turns is not None else self.MIN_TURNS
        self._min_words = min_words if min_words is not None else self.MIN_WORDS
        self._max_turns = max_turns if max_turns is not None else self.MAX_TURNS
        self._short_turn_words = short_turn_words if short_turn_words is not None else self.SHORT_TURN_WORDS

    def add(self, turn_text: str) -> bool:
        """Add a turn. Returns True if the buffer should be flushed now."""
        self._turns.append(turn_text)
        return self.should_flush()

    def should_flush(self) -> bool:
        if not self._turns:
            return False
        if len(self._turns) >= self._max_turns:
            return True
        if all(len(t.split()) < self._short_turn_words for t in self._turns):
            return False
        total_words = sum(len(t.split()) for t in self._turns)
        return len(self._turns) >= self._min_turns and total_words >= self._min_words

    def flush(self) -> str:
        """Return all buffered turns joined and clear the buffer."""
        text = "\n\n".join(self._turns)
        self._turns = []
        return text

    def flush_if_nonempty(self) -> str | None:
        """Force a flush if anything is buffered, otherwise return None."""
        if self._turns:
            return self.flush()
        return None

    @property
    def buffered_turns(self) -> int:
        return len(self._turns)

    @property
    def buffered_words(self) -> int:
        return sum(len(t.split()) for t in self._turns)


async def _call_llm(prompt: str, config: dict, domain_profile=None) -> str:
    """Make an LLM API call and return the raw response content."""
    import asyncio  # lazy — costs ~20ms cold; only needed during extraction, not retrieval
    import httpx  # lazy — costs 97ms cold; only needed during extraction, not retrieval
    llm_cfg = config["llm"]

    thinking_enabled = llm_cfg.get("enable_thinking", True)
    system_suffix = "" if thinking_enabled else " /no_think"
    system_msg = f"You are a context extraction engine. Return only valid JSON.{system_suffix}"

    # Native Gemini SDK path — unlocks count_tokens, context caching, etc.
    # Falls back to OpenAI-compatible httpx path when use_native_sdk is false
    # or google-genai is not installed.
    from .llm import get_provider
    _native = get_provider(config)
    if _native is not None:
        max_tok = llm_cfg.get("max_tokens", 4096)
        temp = llm_cfg.get("temperature", 0.1)
        _MAX_RETRIES = 6
        # Pass thinking_config when reasoning_effort="none" so thinking tokens
        # don't consume the max_tokens budget on Gemini 2.5 Flash Lite.
        _native_extra: dict = {}
        if llm_cfg.get("reasoning_effort") == "none":
            try:
                from google.genai import types as _gt
                _native_extra["thinking_config"] = _gt.ThinkingConfig(thinking_budget=0)
            except (ImportError, AttributeError):
                pass
        for attempt in range(_MAX_RETRIES):
            try:
                return await _native.complete_async(
                    prompt,
                    max_tokens=max_tok,
                    system=system_msg,
                    temperature=temp,
                    **_native_extra,
                )
            except Exception as exc:
                exc_str = str(exc)
                # Propagate output-truncation errors immediately (bisection handles them)
                if "finish_reason" in exc_str or "MAX_TOKENS" in exc_str:
                    raise
                if attempt < _MAX_RETRIES - 1 and any(
                    code in exc_str for code in ("429", "503", "502", "500", "504")
                ):
                    wait = min(30 * (attempt + 1), 120) if "429" in exc_str else 2 ** attempt
                    log.warning(
                        "LLM error (attempt %d/%d), retrying in %ds: %s",
                        attempt + 1, _MAX_RETRIES, wait, exc_str[:120],
                    )
                    await asyncio.sleep(wait)
                    continue
                raise

    # Single source of truth for key resolution (env var → inline → generic).
    from .config import resolve_llm_api_key
    headers = {}
    api_key, _key_source = resolve_llm_api_key(llm_cfg)
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    elif llm_cfg.get("api_key_env"):
        # The user asked for a key (named an env var) but none could be resolved.
        raise ValueError(
            f"No API key found: env var '{llm_cfg['api_key_env']}' is not set and no "
            f"inline 'api_key' is configured. Set {llm_cfg['api_key_env']}, or add "
            f"'api_key:' to your llm config."
        )
    # else: no key configured at all (e.g. a local model that needs none) — send
    # no Authorization header.

    timeout = llm_cfg.get("timeout", 600.0)

    request_body = {
        "model": llm_cfg["model"],
        "messages": [
            {"role": "system", "content": system_msg},
            {"role": "user", "content": prompt},
        ],
        "temperature": llm_cfg.get("temperature", 0.1),
        "max_tokens": llm_cfg.get("max_tokens", 4096),
    }

    # Pass enable_thinking to LM Studio / vLLM if supported
    if llm_cfg.get("enable_thinking") is not None:
        request_body["chat_template_kwargs"] = {"enable_thinking": thinking_enabled}

    # reasoning_effort controls thinking depth on supported models (e.g. Gemini 3, o-series).
    # Use get() with None check so inherited values from a base config don't bleed into
    # models that don't support this parameter (e.g. gpt-4o-mini → 400 Bad Request).
    if llm_cfg.get("reasoning_effort") is not None:
        request_body["reasoning_effort"] = llm_cfg["reasoning_effort"]

    # Structured outputs: enforce the extraction JSON schema at the API level.
    # Supported by OpenAI (gpt-4o, gpt-4o-mini) and compatible APIs.
    # Prevents hallucinated keys, wrong types, and invalid enum values.
    if llm_cfg.get("structured_outputs"):
        request_body["response_format"] = {
            "type": "json_schema",
            "json_schema": build_extraction_json_schema(domain_profile),
        }

    _RETRYABLE_STATUS = {429, 500, 502, 503, 504}
    _MAX_RETRIES = 6

    use_streaming = llm_cfg.get("stream", False)
    if use_streaming:
        request_body["stream"] = True

    log.debug("LLM request: model=%s max_tokens=%s stream=%s", llm_cfg["model"], request_body["max_tokens"], use_streaming)

    url = f"{llm_cfg['base_url']}/chat/completions"

    for attempt in range(_MAX_RETRIES):
        try:
            if use_streaming:
                content, finish_reason = await _llm_stream(url, headers, request_body, timeout)
            else:
                async with httpx.AsyncClient(timeout=timeout) as client:
                    response = await client.post(url, headers=headers, json=request_body)
                    if response.status_code in _RETRYABLE_STATUS and attempt < _MAX_RETRIES - 1:
                        if response.status_code == 429:
                            # Rate limited: honor Retry-After header if present, else use
                            # long exponential backoff (30s, 60s, 90s, 120s). The 1s→4s
                            # default is far too short for Gemini free-tier quota windows.
                            retry_after = response.headers.get("Retry-After")
                            if retry_after and retry_after.isdigit():
                                wait = int(retry_after)
                            else:
                                wait = min(30 * (attempt + 1), 120)
                        else:
                            wait = 2 ** attempt
                        log.warning("LLM returned %s (attempt %d/%d), retrying in %ds", response.status_code, attempt + 1, _MAX_RETRIES, wait)
                        await asyncio.sleep(wait)
                        continue
                    response.raise_for_status()
                    try:
                        data = response.json()
                    except json.JSONDecodeError as e:
                        raw = response.text[:200]
                        raise ValueError(f"LLM returned non-JSON response (status {response.status_code}): {raw!r}") from e
                    choice = data["choices"][0]
                    finish_reason = choice.get("finish_reason")
                    content = choice["message"]["content"]
        except (httpx.ReadTimeout, httpx.ConnectTimeout) as exc:
            if attempt < _MAX_RETRIES - 1:
                wait = 2 ** attempt  # 1s, 2s, 4s, 8s
                log.warning("LLM timeout (attempt %d/%d), retrying in %ds: %s", attempt + 1, _MAX_RETRIES, wait, exc)
                await asyncio.sleep(wait)
                continue
            raise
        break

    log.debug("LLM response: finish_reason=%s len=%d", finish_reason, len(content) if content else 0)
    if finish_reason in ("length", "MAX_TOKENS"):
        max_tok = llm_cfg.get("max_tokens", 4096)
        raise ValueError(
            f"LLM response was truncated (finish_reason={finish_reason}, max_tokens={max_tok}). "
            f"The model hit the token limit before completing the JSON. "
            f"Try: (1) increase max_tokens in config.yaml, "
            f"(2) use --chunk-size to split the input, or "
            f"(3) use 'waystone extract-replay' for turn-by-turn extraction."
        )
    return content


async def _llm_stream(url: str, headers: dict, request_body: dict, timeout: float) -> tuple[str, str | None]:
    """Stream a chat completion response, returning (content, finish_reason)."""
    import httpx  # lazy — shares the cold-load cost with _call_llm when streaming
    content_parts: list[str] = []
    finish_reason: str | None = None
    # Use a long read timeout but short connect timeout for streaming
    stream_timeout = httpx.Timeout(connect=30.0, read=timeout, write=30.0, pool=30.0)
    async with httpx.AsyncClient(timeout=stream_timeout) as client:
        async with client.stream("POST", url, headers=headers, json=request_body) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line.startswith("data: "):
                    continue
                data = line[6:]
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                except json.JSONDecodeError:
                    continue
                choices = chunk.get("choices", [])
                if not choices:
                    # Some streaming APIs emit chunks without choices (e.g., Nemotron)
                    continue
                choice = choices[0]
                delta = choice.get("delta", {})
                content_parts.append(delta.get("content") or "")
                if choice.get("finish_reason"):
                    finish_reason = choice["finish_reason"]
    return "".join(content_parts), finish_reason


async def extract(transcript_text: str, config: dict, store=None, source_transcript: str = "") -> dict:
    """Extract structured facts from a transcript using an LLM.

    Calls an OpenAI-compatible chat/completions endpoint and parses
    the JSON response into nodes and edges with proper IDs.

    When *store* is provided, relevant existing nodes are retrieved via tag
    overlap and injected into the prompt so the LLM can detect supersedence
    at extraction time and avoid re-extracting already-captured facts.

    Args:
        transcript_text: The transcript content to extract from
        config: LLM configuration dict
        store: Optional GraphStore instance for failure logging and context retrieval
        source_transcript: Optional transcript name/path for failure logging

    Returns:
        dict with "nodes" (list[dict]) and "edges" (list[dict])
    """
    log.info("Extracting from %d chars of transcript", len(transcript_text))

    existing_nodes: list[dict] | None = None
    if store is not None:
        from .retriever import extract_keywords
        keywords = extract_keywords(transcript_text)
        if keywords:
            candidates = store.get_nodes_by_tags(keywords)
            # Augment with semantic search so nodes with different vocabulary are included.
            from waystone import embedder
            if embedder.is_available() and store._vec_available:
                query_blob = embedder.embed_text(transcript_text[:2000])  # first 2k chars as query
                sem_ids = store.search_by_embedding(query_blob, top_k=30)
                if sem_ids:
                    existing_ids = {n["id"] for n in candidates}
                    sem_nodes = store.get_nodes_by_ids(sem_ids)
                    candidates.extend(n for n in sem_nodes if n["id"] not in existing_ids)
            if candidates:
                # Rank by keyword overlap count — nodes matching more keywords rank higher.
                # This ensures the top-30 cap selects the most relevant candidates rather
                # than arbitrary insertion-order results.
                kw_set = set(keywords)
                candidates.sort(
                    key=lambda n: len(kw_set.intersection(n.get("tags", []))),
                    reverse=True,
                )
                existing_nodes = candidates[:30]
                log.info(
                    "Context-aware extraction: injecting %d existing nodes (%d candidates) into prompt",
                    len(existing_nodes),
                    len(candidates),
                )

    from waystone.config import get_domain_profile
    domain_profile = get_domain_profile(config)
    prompt = build_extraction_prompt(transcript_text, domain_profile=domain_profile, existing_nodes=existing_nodes)
    try:
        content = await _call_llm(prompt, config, domain_profile)
        extraction = parse_llm_response(content)
    except Exception as exc:
        error_msg = str(exc)

        # Classify the error for better user feedback
        if "Could not parse JSON" in error_msg:
            error_type = "json_parse"
        else:
            error_type = _classify_llm_error(exc)

        if store:
            store.log_extraction_failure(
                error_type=error_type,
                error_message=error_msg,
                raw_response=content if "content" in locals() else "",
                source_transcript=source_transcript,
                model=config.get("llm", {}).get("model", ""),
            )

        # Provide actionable error messages based on error type
        if error_type == "auth":
            raise ValueError(
                "LLM authentication failed — check your API key in config.yaml or GEMINI_API_KEY env var"
            ) from exc
        elif error_type == "rate_limit":
            raise ValueError("LLM rate limit hit — wait a moment and try again") from exc
        elif error_type == "quota":
            raise ValueError("LLM quota exceeded — check your API plan or billing") from exc
        elif error_type == "timeout":
            raise ValueError(
                "LLM request timed out — try increasing `llm.timeout` in config.yaml"
            ) from exc
        else:
            raise
    result = assign_ids(extraction)
    nodes = result["nodes"]
    edges = result["edges"]

    # Adaptive stacking: check if node density is below model-specific floor
    # If so, run a second extraction pass and merge results
    DENSITY_FLOORS = {
        "gemini-2.5-flash-lite": 0.004,
        "gemini-2.5-flash": 0.006,
        "claude-3-5-sonnet": 0.005,
        "gpt-4o-mini": 0.005,
    }
    model_name = config.get("llm", {}).get("model", "")
    floor = DENSITY_FLOORS.get(model_name, 0.004)
    density = len(nodes) / max(len(transcript_text), 1)

    if density < floor:
        log.info(
            "[adaptive] density %.6f < floor %.6f — running second extraction pass",
            density, floor
        )
        try:
            extra_content = await _call_llm(
                build_extraction_prompt(transcript_text, domain_profile=domain_profile, existing_nodes=existing_nodes),
                config,
                domain_profile,
            )
            extra_extraction = assign_ids(parse_llm_response(extra_content))
            extra_nodes = extra_extraction["nodes"]
            extra_edges = extra_extraction["edges"]

            # Merge: skip duplicates by checking if a node's fact already exists
            existing_facts = {n["fact"] for n in nodes}
            new_nodes = [n for n in extra_nodes if n["fact"] not in existing_facts]

            nodes.extend(new_nodes)
            edges.extend(extra_edges)
            log.info(
                "[adaptive] merged %d extra nodes, total density now %.6f",
                len(new_nodes), len(nodes) / max(len(transcript_text), 1)
            )
        except Exception as e:
            log.warning("[adaptive] second pass failed (continuing with first pass): %s", e)

    log.info("Extracted %d nodes, %d edges", len(nodes), len(edges))
    return {"nodes": nodes, "edges": edges}


async def verify_extraction(
    transcript_text: str,
    extracted_nodes: list[dict],
    config: dict,
    candidate_sentences: list[str] | None = None,
    store=None,
    source_transcript: str = "",
    max_context_nodes: int = 50,
) -> dict:
    """Run a second verification pass to find facts missed by the first extraction.

    Focuses specifically on: secondary/addendum details, buried numeric values,
    transition statements, and rationale with time estimates.

    Args:
        transcript_text: The transcript content
        extracted_nodes: Already-extracted nodes from the main pass
        config: LLM configuration dict
        candidate_sentences: optional list of specific sentences to check
        store: Optional GraphStore instance for logging failures
        source_transcript: Optional transcript name/path for failure logging
        max_context_nodes: Cap on nodes included in the verify prompt (sorted by
            confidence descending). Prevents prompt truncation on models with
            smaller output limits (e.g. gemini-2.5-flash-lite). Default 50.

    Returns:
        dict with "nodes" (list[dict]) and "edges" (list[dict]) for NEW nodes only.
        Use assign_ids_incremental to resolve IDs before merging.
    """
    # Cap nodes sent to the prompt to avoid output truncation on smaller models.
    # Keep highest-confidence nodes so the LLM knows the primary facts are covered.
    context_nodes = sorted(extracted_nodes, key=lambda n: n.get("confidence", 0.5), reverse=True)
    context_nodes = context_nodes[:max_context_nodes]
    prompt = build_verification_prompt(transcript_text, context_nodes, candidate_sentences)
    try:
        content = await _call_llm(prompt, config)
        extraction = parse_llm_response(content)
    except Exception as exc:
        error_msg = str(exc)
        if store:
            store.log_extraction_failure(
                error_type="json_parse" if "Could not parse JSON" in error_msg else "api",
                error_message=error_msg,
                raw_response=content if "content" in locals() else "",
                source_transcript=source_transcript,
                model=config.get("llm", {}).get("model", ""),
            )
        raise
    existing_ids = {n["id"] for n in extracted_nodes}
    return assign_ids_incremental(extraction, existing_ids)


async def extract_targeted(
    transcript_text: str,
    category: str,
    extracted_nodes: list[dict],
    config: dict,
) -> dict:
    """Run a targeted extraction pass focused on a specific category.

    Categories: 'lessons', 'decisions', 'questions', 'constraints', 'decisions_constraints_tradeoffs', 'numerics'

    Each category uses a focused prompt that hunts for one type of
    information only (or a thematic group like decisions+constraints+tradeoffs).
    This avoids the recall regression caused by adding new categories to the
    main extraction prompt (where the model wastes attention hunting for content
    that may not exist in the transcript).

    Returns:
        dict with "nodes" (list[dict]) and "edges" (list[dict]) for NEW nodes only.
        Use assign_ids_incremental to resolve IDs before merging.
    """
    if category not in TARGETED_PASS_PROMPTS:
        raise ValueError(
            f"Unknown targeted pass category '{category}'. "
            f"Valid: {sorted(TARGETED_PASS_PROMPTS)}"
        )
    prompt = build_targeted_prompt(category, transcript_text, extracted_nodes)
    content = await _call_llm(prompt, config)
    extraction = parse_llm_response(content)
    existing_ids = {n["id"] for n in extracted_nodes}
    return assign_ids_incremental(extraction, existing_ids)


async def synthesize_extraction(existing_nodes: list[dict], config: dict) -> dict:
    """Create cross-cutting summary nodes from already-extracted graph nodes.

    Unlike other passes, this takes existing nodes (not a transcript) as input
    and produces synthetic summary nodes that aggregate parallel facts across
    subjects — e.g. "Benchmark recall across all models: Gemini = 93%, ..."

    Typical use: run after extraction is complete and nodes are merged into the
    store, passing in all graph nodes so synthesis spans the full graph.

    Returns:
        dict with "nodes" (list[dict]) and "edges" (list[dict]) for new nodes.
        Use assign_ids_incremental to resolve IDs before merging.
    """
    prompt = build_synthesis_prompt(existing_nodes)
    content = await _call_llm(prompt, config)
    extraction = parse_llm_response(content)
    existing_ids = {n["id"] for n in existing_nodes}
    return assign_ids_incremental(extraction, existing_ids)


async def extract_implicit_prefs(existing_nodes: list[dict], config: dict) -> dict:
    """Infer implicit cross-domain preferences from already-extracted nodes.

    Unlike extract_targeted (which reads a transcript), this pass reads existing
    preference/implementation nodes and generates NEW preference nodes that capture
    strongly implied preferences in adjacent domains — e.g.:
      - Gardening nodes about growing tomatoes/basil → cooking preference for
        using homegrown produce in recipes
      - Fitness nodes about marathon running → nutrition preferences

    This bridges the semantic framing gap between how preferences are extracted
    (in the original conversation domain) and how they're queried (in the adjacent
    usage domain).

    Typical use: run once per sample after all session-level extraction is complete,
    passing in all preference + implementation nodes.

    Returns:
        dict with "nodes" (list[dict]) and "edges" (list[dict]) for new nodes.
        Use assign_ids_incremental to resolve IDs before merging.
    """
    prompt = build_implicit_prefs_prompt(existing_nodes)
    content = await _call_llm(prompt, config)
    extraction = parse_llm_response(content)
    existing_ids = {n["id"] for n in existing_nodes}
    return assign_ids_incremental(extraction, existing_ids)


async def reflect_extraction(
    transcript_window: str,
    project: str,
    store=None,
    existing_process_nodes: list[dict] | None = None,
    domain: str = "software_dev",
    config: dict | None = None,
) -> dict:
    """Extract process patterns from a transcript window through iterative reflection.

    Discovers procedural patterns and protocols that emerged from team iteration,
    as opposed to explicit decisions or constraints.

    Args:
        transcript_window: multi-turn conversation text
        project: project name (for loading store and config if needed)
        store: optional GraphStore instance (if None, will be loaded from project)
        existing_process_nodes: list of already-extracted process nodes (for dedup)
        domain: domain name (e.g. "software_dev")
        config: optional config dict (if None, will be loaded from default locations)

    Returns:
        dict with "nodes" (list[dict]) and "edges" (list[dict]) for new nodes.
    """
    from .prompts import build_reflect_prompt
    from .config import load_config

    if config is None:
        config = load_config()

    if store is None:
        from .store import GraphStore
        from .config import get_db_path
        db_path = get_db_path(config, project)
        store = GraphStore(db_path)

    _EPISODIC_DOMAINS = {"episodic_personal", "episodic_personal_no_dates"}
    _reflect_type = "episode" if domain in _EPISODIC_DOMAINS else "process"

    if existing_process_nodes is None:
        existing_process_nodes = [
            n for n in store.get_all_nodes() if n.get("type") == _reflect_type
        ]

    prompt = build_reflect_prompt(transcript_window, existing_process_nodes, domain)
    content = await _call_llm(prompt, config)
    extraction = parse_llm_response(content)
    existing_ids = {n["id"] for n in existing_process_nodes}
    result = assign_ids_incremental(extraction, existing_ids)

    # Set domain on each new node; enforce the correct reflect type
    for node in result.get("nodes", []):
        node["domain"] = domain
        if node.get("type") != _reflect_type:
            node["type"] = _reflect_type

    return result


async def extract_turn(
    turn_text: str,
    existing_nodes: list[dict],
    config: dict,
    domain_profile=None,
) -> dict:
    """Extract structured facts from a single conversation turn with existing context.

    Uses incremental extraction so the LLM can reference existing node IDs in
    supersedes/edges and avoids re-extracting already-captured facts.

    Args:
        turn_text: The conversation turn text to extract from.
        existing_nodes: Already-extracted nodes for context (not re-extracted).
        config: Waystone config dict.
        domain_profile: DomainProfile to use. If None, uses config's domain setting
                        or falls back to software_dev.

    Returns:
        dict with "nodes" (list[dict]) and "edges" (list[dict])
    """
    if domain_profile is None:
        from waystone.config import get_domain_profile
        domain_profile = get_domain_profile(config)
    prompt = build_incremental_prompt(turn_text, existing_nodes, domain_profile)
    content = await _call_llm(prompt, config, domain_profile)
    extraction = parse_llm_response(content)
    existing_ids = {n["id"] for n in existing_nodes}
    result = assign_ids_incremental(extraction, existing_ids)
    # Filter nodes to only those with types declared in the active domain profile
    valid_types = set(domain_profile.node_types.keys())
    result["nodes"] = [n for n in result.get("nodes", []) if n.get("type") in valid_types]
    valid_node_ids = {n["id"] for n in result["nodes"]}
    result["edges"] = [
        e for e in result.get("edges", [])
        if e.get("from_id") in valid_node_ids or e.get("to_id") in valid_node_ids
    ]
    return result


async def extract_config_items(config_text: str, config: dict) -> dict:
    """Extract and classify items from a config file (CLAUDE.md, MEMORY.md, SOUL.md, etc.).

    Each item in the file becomes a node. The LLM classifies each as pinned=true
    (always inject) or pinned=false (only inject when relevant).

    Returns:
        dict with "nodes" list — each node has id, fact, type, confidence, pinned, tags.
    """
    from waystone.prompts import build_config_extraction_prompt
    prompt = build_config_extraction_prompt(config_text)
    content_str = await _call_llm(prompt, config)
    # Parse response — config extraction uses a simpler schema (no edges)
    cleaned = re.sub(r"<think>.*?</think>", "", content_str, flags=re.DOTALL).strip()
    cleaned = re.sub(r"^```(?:json)?\s*\n?", "", cleaned)
    cleaned = re.sub(r"\n?```\s*$", "", cleaned)
    try:
        result = json.loads(cleaned)
    except json.JSONDecodeError:
        candidate = _extract_first_json_object(cleaned)
        if not candidate:
            raise ValueError(f"Could not parse JSON from LLM response: {content_str[:300]}")
        result = json.loads(candidate)
    nodes = result.get("nodes", [])
    # Ensure each node has required fields
    for node in nodes:
        node.setdefault("confidence", 1.0)
        node.setdefault("tags", [])
        node.setdefault("pinned", False)
    return {"nodes": nodes}


def parse_llm_response(content: str) -> dict:
    """Parse LLM response text into structured extraction data.

    Handles raw JSON, markdown-fenced JSON, and responses with leading
    thinking/reasoning text (from Qwen3, DeepSeek, etc.).
    """
    # Strip <think>...</think> blocks
    cleaned = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()

    # Strip markdown code fences if present
    cleaned = re.sub(r"^```(?:json)?\s*\n?", "", cleaned)
    cleaned = re.sub(r"\n?```\s*$", "", cleaned)

    # Try to parse the cleaned content directly first
    try:
        result = json.loads(cleaned)
        return _validate_extraction(result)
    except (json.JSONDecodeError, ValueError):
        pass

    # Fallback: find the outermost balanced JSON object in the response.
    # This handles models that emit thinking text before or around the JSON.
    json_candidate = _extract_first_json_object(cleaned)
    if json_candidate:
        try:
            result = json.loads(json_candidate)
            return _validate_extraction(result)
        except (json.JSONDecodeError, ValueError):
            pass

    raise ValueError(
        f"Could not parse JSON from LLM response.\nContent (first 500 chars): {content[:500]}"
    )


def _extract_first_json_object(text: str) -> str | None:
    """Find and return the first balanced {...} block that looks like extraction output.

    Prefers blocks containing '"nodes"' to avoid matching schema examples
    inside thinking/reasoning text. Falls back to the first balanced block.
    """
    # Try targeted search first: find a { that is quickly followed by "nodes"
    for pattern in ('"nodes"', '"nodes" '):
        idx = text.find(pattern)
        while idx != -1:
            # Walk back to find the opening { for this block
            open_pos = text.rfind("{", 0, idx)
            if open_pos != -1:
                candidate = _extract_balanced_object(text, open_pos)
                if candidate:
                    return candidate
            idx = text.find(pattern, idx + 1)

    # Fallback: first balanced { block
    start = text.find("{")
    if start == -1:
        return None
    return _extract_balanced_object(text, start)


def _extract_balanced_object(text: str, start: int) -> str | None:
    """Extract a balanced {...} block starting at position start."""
    depth = 0
    in_string = False
    escape = False
    for i, ch in enumerate(text[start:], start):
        if escape:
            escape = False
            continue
        if ch == "\\" and in_string:
            escape = True
            continue
        if ch == '"' and not escape:
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


VALID_TYPES = {"decision", "constraint", "implementation", "question", "resolved", "preference", "lesson_learned", "transition"}
VALID_RELATIONS = {"depends_on", "flows_to", "relates_to", "supersedes", "conflicts_with"}

# Some models return edges with "source"/"target" or "from_id"/"to_id" instead of "from"/"to"
_EDGE_FROM_KEYS = ("from", "source", "from_id", "from_node")
_EDGE_TO_KEYS = ("to", "target", "to_id", "to_node")


def _edge_from(edge: dict) -> str | None:
    for k in _EDGE_FROM_KEYS:
        if k in edge:
            return edge[k]
    return None


def _edge_to(edge: dict) -> str | None:
    for k in _EDGE_TO_KEYS:
        if k in edge:
            return edge[k]
    return None


def _validate_extraction(result: dict) -> dict:
    if "nodes" not in result:
        result["nodes"] = []
    if "edges" not in result:
        result["edges"] = []
    return result


def validate_nodes(
    nodes: list[dict],
    valid_types: set[str] | None = None,
) -> tuple[list[dict], list[str]]:
    """Validate extracted nodes, returning (valid_nodes, warnings).

    Filters out nodes with missing required fields. Collects warnings for
    nodes with quality issues (too few tags, unknown type, out-of-range confidence)
    without dropping them.

    Args:
        nodes: Raw nodes from LLM extraction.
        valid_types: Set of valid type strings for this domain. If None, falls
                     back to the module-level VALID_TYPES constant (software_dev).
    """
    effective_valid_types = valid_types if valid_types is not None else VALID_TYPES
    valid = []
    warnings = []

    for node in nodes:
        nid = node.get("id", "<no-id>")

        # Hard required fields — drop silently (LLM emitted a stub)
        if not node.get("id") or not node.get("fact") or not node.get("type"):
            continue

        # Soft quality checks — warn but keep
        if node.get("type") not in effective_valid_types:
            warnings.append(f"node {nid}: unknown type '{node['type']}'")

        confidence = node.get("confidence", 0.5)
        if not (0.0 <= confidence <= 1.0):
            warnings.append(f"node {nid}: confidence {confidence} out of [0,1]")

        tag_count = len(node.get("tags", []))
        if tag_count < 4:
            warnings.append(f"node {nid}: only {tag_count} tags (recommend ≥ 4): '{node['fact'][:60]}'")

        valid.append(node)

    return valid, warnings


def assign_ids(extraction: dict) -> dict:
    """Assign proper UUIDs to nodes and remap edge references.

    The LLM returns short IDs like "n1", "n2". This replaces them
    with "n_<uuid-short>" and updates all edge from/to references.
    """
    id_map = {}
    nodes = []

    for node in extraction["nodes"]:
        # Skip malformed nodes missing required fields
        if not node.get("id") or not node.get("fact") or not node.get("type"):
            continue
        old_id = node["id"]
        new_id = f"n_{uuid.uuid4().hex[:8]}"
        id_map[old_id] = new_id

        nodes.append({
            "id": new_id,
            "fact": node["fact"],
            "type": node["type"],
            "confidence": node.get("confidence", 0.5),
            "source_message_index": node.get("source_message"),
            "tags": node.get("tags", []),
            "supersedes": [id_map.get(s, s) for s in node.get("supersedes", [])],
        })

    edges = []
    for edge in extraction.get("edges", []):
        raw_from = _edge_from(edge)
        raw_to = _edge_to(edge)
        if raw_from is None or raw_to is None:
            log.warning("Skipping edge with missing from/to keys: %s", edge)
            continue
        from_id = id_map.get(raw_from, raw_from)
        to_id = id_map.get(raw_to, raw_to)
        edges.append({
            "from_id": from_id,
            "to_id": to_id,
            "relation": edge["relation"],
        })

    return {"nodes": nodes, "edges": edges}


def assign_ids_incremental(extraction: dict, existing_node_ids: set) -> dict:
    """Assign UUIDs to new nodes while preserving existing node ID references.

    - IDs matching n_[0-9a-f]{8} AND present in existing_node_ids → passed through
    - All other IDs → new UUID (same as assign_ids)
    - IDs that look like existing pattern but are NOT in existing_node_ids are
      treated as hallucinated references and get a new UUID (safety guard)

    This allows the LLM to reference existing graph nodes in edges/supersedes
    without those references being remapped.
    """
    id_map: dict[str, str] = {}

    # First pass: assign final IDs for all nodes emitted by the LLM
    # Skip malformed nodes (e.g. re-emitted existing stubs without fact/type)
    valid_nodes = [n for n in extraction["nodes"] if n.get("id") and n.get("fact") and n.get("type")]
    extraction = {**extraction, "nodes": valid_nodes}

    for node in extraction["nodes"]:
        old_id = node["id"]
        if _EXISTING_ID_PATTERN.match(old_id) and old_id in existing_node_ids:
            id_map[old_id] = old_id  # preserve existing node reference
        else:
            id_map[old_id] = f"n_{uuid.uuid4().hex[:8]}"

    def resolve(id_str: str) -> str:
        """Resolve any ID: known mapping, valid existing ID, or new UUID for unknown."""
        if id_str in id_map:
            return id_map[id_str]
        # ID referenced in supersedes/edges but not in nodes list
        if _EXISTING_ID_PATTERN.match(id_str) and id_str in existing_node_ids:
            id_map[id_str] = id_str  # cache for consistency
            return id_str
        # Hallucinated or unknown — assign new UUID and cache
        new_id = f"n_{uuid.uuid4().hex[:8]}"
        id_map[id_str] = new_id
        return new_id

    nodes = []
    for node in extraction["nodes"]:
        nodes.append({
            "id": id_map[node["id"]],
            "fact": node["fact"],
            "type": node["type"],
            "confidence": node.get("confidence", 0.5),
            "source_message_index": node.get("source_message"),
            "tags": node.get("tags", []),
            "supersedes": [resolve(s) for s in node.get("supersedes", [])],
        })

    edges = []
    for edge in extraction.get("edges", []):
        raw_from = _edge_from(edge)
        raw_to = _edge_to(edge)
        if raw_from is None or raw_to is None:
            log.warning("Skipping edge with missing from/to keys: %s", edge)
            continue
        edges.append({
            "from_id": resolve(raw_from),
            "to_id": resolve(raw_to),
            "relation": edge["relation"],
        })

    return {"nodes": nodes, "edges": edges}


async def reconcile_group(nodes: list[dict], config: dict) -> list[dict]:
    """Find supersedes relationships within a group of related nodes.

    Sends all nodes to the LLM with a reconcile prompt and returns a list of
    {"superseding_id": ..., "superseded_id": ...} dicts for confirmed pairs.
    Only returns pairs where both IDs are present in the input node set.
    """
    if len(nodes) < 2:
        return []

    prompt = build_reconcile_prompt(nodes)
    content = await _call_llm(prompt, config)

    try:
        cleaned = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
        cleaned = re.sub(r"^```(?:json)?\s*\n?", "", cleaned)
        cleaned = re.sub(r"\n?```\s*$", "", cleaned)
        result = json.loads(cleaned)
        pairs = result.get("supersedes", [])
    except Exception as _parse_exc:
        log.warning("reconcile_group parse failed: %s", _parse_exc, exc_info=True)
        return []

    # Validate: only return pairs where both IDs exist in the input set
    valid_ids = {n["id"] for n in nodes}
    return [
        p for p in pairs
        if isinstance(p, dict)
        and p.get("superseding_id") in valid_ids
        and p.get("superseded_id") in valid_ids
        and p["superseding_id"] != p["superseded_id"]
    ]


def score_extraction_quality(nodes: list[dict], edges: list[dict], transcript_text: str) -> dict:
    """Compute per-extraction quality metrics.

    Returns a dict with:
        nodes_per_1k_chars: extraction density
        avg_tags_per_node: tagging richness
        edge_to_node_ratio: graph connectivity
        low_tag_nodes: count of nodes with < 4 tags
        warnings: list of quality warning strings
    """
    char_count = max(1, len(transcript_text))
    node_count = len(nodes)
    edge_count = len(edges)

    _, warnings = validate_nodes(nodes)

    avg_tags = (
        sum(len(n.get("tags", [])) for n in nodes) / node_count
        if node_count else 0.0
    )
    low_tag_count = sum(1 for n in nodes if len(n.get("tags", [])) < 4)

    return {
        "nodes_per_1k_chars": round(node_count / (char_count / 1000), 1),
        "avg_tags_per_node": round(avg_tags, 1),
        "edge_to_node_ratio": round(edge_count / max(1, node_count), 2),
        "low_tag_nodes": low_tag_count,
        "warnings": warnings,
    }


async def summarize_session(sample_text: str, config: dict) -> str:
    """Best-effort one-line summary of a session, for menus (e.g. onboard picker).

    A short, cheap completion — NOT extraction. Returns "" on ANY failure (no
    key, unreachable, timeout, bad response) so the caller can fall back to a
    cheaper preview. No retries: menus must stay snappy.
    """
    import httpx

    from .config import resolve_llm_api_key

    llm_cfg = config.get("llm", {}) or {}
    base_url = llm_cfg.get("base_url")
    model = llm_cfg.get("model")
    if not base_url or not model or not (sample_text or "").strip():
        return ""

    key, _ = resolve_llm_api_key(llm_cfg)
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content":
                "You label coding sessions. Reply with ONLY a terse 6-10 word phrase "
                "describing what the session worked on — no quotes, no preamble."},
            {"role": "user", "content": sample_text[:6000]},
        ],
        "temperature": 0.1,
        "max_tokens": 32,
    }
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            r = await client.post(base_url.rstrip("/") + "/chat/completions", headers=headers, json=body)
            r.raise_for_status()
            txt = r.json()["choices"][0]["message"]["content"]
            return " ".join((txt or "").split())[:90]
    except Exception:
        return ""


_SESSION_SUMMARY_SYSTEM = (
    "You maintain a rolling summary of a coding/work session for a memory system. "
    "Given the running summary so far and the NEW turns since it, produce an UPDATED "
    "summary of the WHOLE session so far. Keep it tight (3-5 sentences) and cover: the "
    "GOAL/intent, the ARC of what was done (key decisions/actions in order), the CURRENT "
    "STATE, and the NEXT step / open threads. Write plain prose, no preamble, no markdown "
    "headers. Preserve important specifics (names, versions, decisions) from the prior "
    "summary unless they were superseded."
)


async def generate_session_summary(new_turns_text: str, prior_summary: str, config: dict) -> str:
    """Incrementally update a rolling session summary from the prior summary + new turns.

    Incremental (prior + only the new turns) → cheap and bounded, like a host
    agent's rolling recap. Best-effort: returns "" only after exhausting retries
    (no key, unreachable, repeated timeout/empty response).

    Retries (bounded) cover two empty-window failure modes seen in practice:
    transient API errors (5xx/429/timeout) and a *blank/null* ``content`` field
    (some models return ``content: null`` on length-truncation or a content
    filter — no exception, just an empty string). ``session_summary.retries``
    (default 2 → 3 attempts total) controls the budget.
    """
    import asyncio
    import httpx

    from .config import resolve_llm_api_key

    llm_cfg = config.get("llm", {}) or {}
    base_url = llm_cfg.get("base_url")
    model = llm_cfg.get("model")
    if not base_url or not model or not (new_turns_text or "").strip():
        return ""

    key, _ = resolve_llm_api_key(llm_cfg)
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    user = (
        f"RUNNING SUMMARY SO FAR:\n{prior_summary.strip() or '(none yet — this is the first summary)'}\n\n"
        f"NEW TURNS SINCE THEN:\n{new_turns_text[:12000]}\n\n"
        "Updated whole-session summary:"
    )
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": _SESSION_SUMMARY_SYSTEM},
            {"role": "user", "content": user},
        ],
        "temperature": 0.2,
        "max_tokens": 512,   # headroom: reduces null-content from length truncation
    }
    timeout = float(llm_cfg.get("timeout", 120.0))
    retries = int((config.get("session_summary", {}) or {}).get("retries", 2))
    attempts = max(1, retries + 1)

    for attempt in range(attempts):
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                r = await client.post(
                    base_url.rstrip("/") + "/chat/completions", headers=headers, json=body
                )
                r.raise_for_status()
                msg = (r.json().get("choices") or [{}])[0].get("message") or {}
                cleaned = " ".join((msg.get("content") or "").split()).strip()
                if cleaned:
                    return cleaned
                # Blank/null content (length truncation or content filter) — retry.
        except Exception:
            pass  # Transient API error — retry.
        if attempt < attempts - 1:
            await asyncio.sleep(0.5 * (attempt + 1))  # small linear backoff
    return ""


def split_into_chunks(text: str, max_chars: int) -> list[str]:
    """Split text into chunks at paragraph boundaries, each ≤ max_chars.

    Splits on double-newlines so chunks never cut through a paragraph.
    If a single paragraph exceeds max_chars it is emitted as its own chunk.
    """
    paragraphs = text.split("\n\n")
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for para in paragraphs:
        para_len = len(para) + 2  # +2 for the "\n\n" separator
        if current and current_len + para_len > max_chars:
            chunks.append("\n\n".join(current))
            current = [para]
            current_len = para_len
        else:
            current.append(para)
            current_len += para_len
    if current:
        chunks.append("\n\n".join(current))
    return chunks


async def _extract_adaptive(chunk_text: str, config: dict, extract_kwargs: dict,
                            depth: int = 0, max_depth: int = 3, min_chars: int = 4000) -> dict:
    """Extract one chunk; on a *timeout* or *truncation*, re-split it smaller and retry.

    `_call_llm` already retries transient failures at the *same* size — which
    doesn't help when the chunk is simply too big/slow to finish within the
    timeout (or too big to fit in max_tokens). This wrapper degrades gracefully:
    on a timeout/truncation it halves the chunk and extracts the pieces, down to
    a floor, so a large session yields partial facts instead of nothing.
    """
    import httpx
    try:
        return await extract(chunk_text, config, **extract_kwargs)
    except Exception as exc:  # noqa: BLE001 — we re-raise unless it's re-splittable
        msg = str(exc).lower()
        is_timeout = isinstance(exc, (httpx.ReadTimeout, httpx.ConnectTimeout)) or "timeout" in msg
        is_truncation = "truncated" in msg or "max_tokens" in msg
        if not (is_timeout or is_truncation) or depth >= max_depth or len(chunk_text) <= min_chars:
            raise
        subs = split_into_chunks(chunk_text, max(len(chunk_text) // 2, min_chars))
        if len(subs) < 2:
            raise
        log.warning(
            "Chunk %s; re-splitting %d chars into %d smaller chunks (depth %d/%d)",
            "timed out" if is_timeout else "was truncated", len(chunk_text), len(subs), depth + 1, max_depth,
        )
        nodes: list[dict] = []
        edges: list[dict] = []
        for sub in subs:
            r = await _extract_adaptive(sub, config, extract_kwargs, depth + 1, max_depth, min_chars)
            nodes.extend(r["nodes"])
            edges.extend(r["edges"])
        return {"nodes": nodes, "edges": edges}


async def extract_chunked(
    text: str,
    config: dict,
    chunk_size: int,
    *,
    verify: bool = False,
    store=None,
    source_transcript: str = "",
) -> dict:
    """Extract from a large transcript by splitting into chunks and merging results.

    Each chunk is extracted independently (in parallel). Nodes and edges from
    all chunks are combined; IDs are already unique UUIDs so there are no
    collisions. Edges can only connect nodes within the same chunk (the LLM
    only sees one chunk at a time).

    Args:
        text: The transcript text
        config: LLM configuration dict
        chunk_size: Maximum characters per chunk
        verify: Whether to run verification pass on each chunk
        store: Optional GraphStore instance for logging failures
        source_transcript: Optional transcript name/path for failure logging

    Returns:
        dict with "nodes" (list[dict]) and "edges" (list[dict])
    """
    chunks = split_into_chunks(text, chunk_size)

    # Build kwargs conditionally for backward compatibility
    extract_kwargs = {}
    if store is not None:
        extract_kwargs["store"] = store
    if source_transcript:
        extract_kwargs["source_transcript"] = source_transcript

    if len(chunks) == 1:
        result = await _extract_adaptive(chunks[0], config, extract_kwargs)
        if verify:
            vr = await verify_extraction(chunks[0], result["nodes"], config, **extract_kwargs)
            result = {
                "nodes": result["nodes"] + vr["nodes"],
                "edges": result["edges"] + vr["edges"],
            }
        return result

    log.info("extract_chunked: splitting %d chars into %d chunks of ≤%d", len(text), len(chunks), chunk_size)

    async def _one_chunk(chunk_text: str, chunk_idx: int) -> tuple[list, list]:
        r = await _extract_adaptive(chunk_text, config, extract_kwargs)
        nodes, edges = r["nodes"], r["edges"]
        if verify:
            try:
                vr = await verify_extraction(chunk_text, nodes, config, **extract_kwargs)
                nodes = nodes + vr["nodes"]
                edges = edges + vr["edges"]
            except Exception as exc:
                if store:
                    store.log_extraction_failure(
                        error_type="chunk",
                        error_message=f"Verification failed for chunk {chunk_idx + 1}/{len(chunks)}: {str(exc)}",
                        raw_response="",
                        source_transcript=source_transcript,
                        model=config.get("llm", {}).get("model", ""),
                    )
                log.warning("verify_extraction failed for chunk (continuing): %s", exc)
        return nodes, edges

    import asyncio
    results = await asyncio.gather(*[_one_chunk(c, i) for i, c in enumerate(chunks)], return_exceptions=True)

    all_nodes: list[dict] = []
    all_edges: list[dict] = []
    failed = 0
    for i, r in enumerate(results):
        if isinstance(r, BaseException):
            log.error("Chunk %d/%d failed: %s", i + 1, len(chunks), r)
            if store:
                store.log_extraction_failure(
                    error_type="chunk",
                    error_message=f"Chunk {i + 1}/{len(chunks)} extraction failed: {str(r)}",
                    raw_response="",
                    source_transcript=source_transcript,
                    model=config.get("llm", {}).get("model", ""),
                )
            failed += 1
        else:
            nodes, edges = r
            all_nodes.extend(nodes)
            all_edges.extend(edges)

    if failed == len(chunks):
        raise RuntimeError(f"All {len(chunks)} extraction chunks failed")
    if failed:
        log.warning("%d/%d chunks failed; continuing with partial extraction", failed, len(chunks))

    log.info("extract_chunked: %d total nodes, %d total edges from %d chunks", len(all_nodes), len(all_edges), len(chunks))
    return {"nodes": all_nodes, "edges": all_edges}


def split_transcript_into_turns(text: str, turn_size: int = 2) -> list[str]:
    """Split a transcript into turns (groups of turn_size messages).

    Splits on **Speaker**: patterns used in the benchmark transcripts.
    Each returned turn is a string containing turn_size consecutive messages.
    """
    # Match the start of each speaker turn: **Name**: at the start of a line
    parts = re.split(r"(?=^\*\*[^*\n]+\*\*:)", text, flags=re.MULTILINE)
    parts = [p.strip() for p in parts if p.strip()]

    turns = []
    for i in range(0, len(parts), turn_size):
        chunk = parts[i : i + turn_size]
        turns.append("\n\n".join(chunk))
    return turns
