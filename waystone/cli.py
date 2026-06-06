"""CLI for Waystone."""

import asyncio
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import click

from .config import get_db_path, get_project_dir, load_config
from .monitoring import init_sentry
from .extractor import (
    ExtractionBuffer,
    extract,
    extract_config_items,
    extract_targeted,
    extract_turn,
    reconcile_group,
    score_extraction_quality,
    split_into_chunks,
    split_transcript_into_turns,
    synthesize_extraction,
    verify_extraction,
)
from .retriever import bfs_collect, cluster_by_tags, extract_keywords, find_conflicts, retrieve_with_stats, score_by_relevance
from .store import GraphStore


def _parse_strategy_overrides(enable, disable, confidence, token_budget):
    """Build a strategy override dict from CLI flags."""
    overrides = {}
    for name in (enable or []):
        overrides[name] = True
    for name in (disable or []):
        overrides[name] = False
    if confidence is not None:
        overrides["confidence_threshold"] = confidence
    if token_budget is not None:
        overrides["token_budget"] = token_budget
    return overrides


def _resolve_strategies(config, overrides):
    """Merge config strategies with CLI overrides."""
    strats = dict(config.get("strategies", {}))
    strats.update(overrides)
    return strats


def _load_cfg(config_path):
    """Load config, handling errors."""
    try:
        return load_config(config_path)
    except FileNotFoundError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)


@click.group()
@click.option("--config", "config_path", default=None, help="Path to config.yaml")
@click.pass_context
def cli(ctx, config_path):
    """Waystone — DAG-based context intelligence for LLM workflows."""
    init_sentry()
    ctx.ensure_object(dict)
    ctx.obj["config_path"] = config_path


@cli.command()
@click.argument("project")
@click.pass_context
def init(ctx, project):
    """Initialize a new project with an empty graph."""
    config = _load_cfg(ctx.obj["config_path"])
    project_dir = get_project_dir(config, project)

    if project_dir.exists():
        click.echo(f"Project '{project}' already exists at {project_dir}")
        return

    try:
        project_dir.mkdir(parents=True)
        (project_dir / "transcripts").mkdir()
        (project_dir / "exports").mkdir()

        db_path = get_db_path(config, project)
        store = GraphStore(db_path)
        store.close()
    except (PermissionError, OSError) as e:
        click.echo(f"Error: Cannot initialize project '{project}': {e}", err=True)
        sys.exit(1)

    click.echo(f"Initialized project '{project}' at {project_dir}")



_MAX_FILE_BYTES = 50 * 1024 * 1024  # 50 MB hard limit


@cli.command("extract")
@click.argument("project")
@click.argument("transcript_file", type=click.Path())
@click.option("--verify", is_flag=True, help="Run a second verification pass to catch missed facts")
@click.option("--lessons", is_flag=True, help="Run a targeted pass hunting for lesson_learned nodes (failed approaches, rejected alternatives)")
@click.option("--decisions", is_flag=True, help="Run a targeted pass hunting for decision nodes and their rationale")
@click.option("--questions", is_flag=True, help="Run a targeted pass hunting for open questions and unresolved items")
@click.option("--constraints", is_flag=True, help="Run a targeted pass hunting for hard constraints and requirements")
@click.option("--numerics", is_flag=True, help="Run a targeted pass hunting for numeric values, measurements, and quantified facts")
@click.option("--preferences", is_flag=True, help="Run a targeted pass hunting for personal preferences, habits, tastes, and dislikes")
@click.option("--synthesize", is_flag=True, help="Run a synthesis pass after extraction: create cross-cutting summary nodes across all graph nodes")
@click.option("--timeout", type=float, default=None, help="LLM timeout in seconds (overrides config)")
@click.option("--chunk-size", type=int, default=None, metavar="CHARS",
              help="Max chars per LLM call (default: 20000 for Gemini; auto-applied when file > 20000 chars)")
@click.pass_context
def extract_cmd(ctx, project, transcript_file, verify, lessons, decisions, questions, constraints, numerics, preferences, synthesize, timeout, chunk_size):
    """Extract facts from a transcript and merge into the project graph."""
    config = _load_cfg(ctx.obj["config_path"])

    if timeout is not None:
        config = dict(config)
        config["llm"] = dict(config.get("llm", {}))
        config["llm"]["timeout"] = timeout

    db_path = get_db_path(config, project)

    if not db_path.parent.exists():
        click.echo(f"Error: Project '{project}' not found. Run 'waystone init {project}' first.", err=True)
        sys.exit(1)

    transcript_path = Path(transcript_file)
    if not transcript_path.exists():
        click.echo(f"Error: Transcript file '{transcript_file}' not found.", err=True)
        sys.exit(1)
    file_size = transcript_path.stat().st_size
    if file_size > _MAX_FILE_BYTES:
        click.echo(
            f"Error: {transcript_path.name} is {file_size // 1024 // 1024}MB — "
            f"exceeds the 50MB limit. Split the file first.", err=True
        )
        sys.exit(1)

    try:
        transcript_text = transcript_path.read_text()
    except UnicodeDecodeError as e:
        click.echo(
            f"Error: Could not read {transcript_path} as UTF-8: {e}\n"
            f"Try saving the file as UTF-8 and try again.",
            err=True,
        )
        sys.exit(1)

    # Auto-chunk: use explicit --chunk-size if given, otherwise chunk anything
    # over 20000 chars at 20000 (safe default for Gemini 2.5 Flash and similar models).
    _AUTO_CHUNK = 20_000
    effective_chunk_size = chunk_size or (_AUTO_CHUNK if len(transcript_text) > _AUTO_CHUNK else None)
    if effective_chunk_size:
        chunks = split_into_chunks(transcript_text, effective_chunk_size)
    else:
        chunks = [transcript_text]

    n_chunks = len(chunks)
    chunk_info = f" ({n_chunks} chunks × ≤{effective_chunk_size:,} chars, parallel)" if n_chunks > 1 else ""
    click.echo(f"Extracting from {transcript_path.name}...{chunk_info}")

    all_nodes: list[dict] = []
    all_edges: list[dict] = []
    failed_chunks = 0

    _targeted_categories = [
        c for c, flag in [("lessons", lessons), ("decisions", decisions),
                          ("questions", questions), ("constraints", constraints),
                          ("numerics", numerics), ("preferences", preferences)]
        if flag
    ]

    if n_chunks == 1:
        # Single chunk: inline progress
        click.echo(f"  sending {len(chunks[0]):,} chars...", nl=False)
        t0 = time.monotonic()
        try:
            result = asyncio.run(extract(chunks[0], config))
        except Exception as e:
            elapsed = time.monotonic() - t0
            click.echo("")
            click.echo(f"  FAILED after {elapsed:.0f}s ({type(e).__name__}): {e}", err=True)
            sys.exit(1)
        elapsed = time.monotonic() - t0
        nodes, edges = result["nodes"], result["edges"]
        if verify:
            click.echo(f" {len(nodes)} nodes [{elapsed:.0f}s]. Verifying...")
            try:
                vr = asyncio.run(verify_extraction(chunks[0], nodes, config))
                click.echo(f"  +{len(vr['nodes'])} nodes, +{len(vr['edges'])} edges")
                nodes = nodes + vr["nodes"]
                edges = edges + vr["edges"]
            except Exception as e:
                click.echo(f"  Verification failed (continuing with pass 1): {e}", err=True)
        else:
            click.echo(f" {len(nodes)} nodes, {len(edges)} edges [{elapsed:.0f}s]")
        if _targeted_categories:
            n_passes = len(_targeted_categories)
            label = " concurrently" if n_passes > 1 else ""
            click.echo(f"  Running {n_passes} targeted pass(es){label}...")
            async def _run_targeted_cli(cats, snap_nodes):
                return await asyncio.gather(
                    *[extract_targeted(chunks[0], cat, snap_nodes, config) for cat in cats],
                    return_exceptions=True,
                )
            targeted_results = asyncio.run(_run_targeted_cli(_targeted_categories, nodes))
            for cat, tr in zip(_targeted_categories, targeted_results):
                if isinstance(tr, Exception):
                    click.echo(f"  --{cat} pass failed (continuing): {tr}", err=True)
                else:
                    click.echo(f"  +{len(tr['nodes'])} nodes, +{len(tr['edges'])} edges (--{cat})")
                    nodes = nodes + tr["nodes"]
                    edges = edges + tr["edges"]
        all_nodes.extend(nodes)
        all_edges.extend(edges)
    else:
        # Multiple chunks: run all in parallel, then report results in order
        click.echo(f"  Submitting {n_chunks} chunks in parallel...")

        async def _extract_chunk(chunk_text: str, do_verify: bool) -> tuple[list, list]:
            result = await extract(chunk_text, config)
            nodes, edges = result["nodes"], result["edges"]
            if do_verify:
                try:
                    vr = await verify_extraction(chunk_text, nodes, config)
                    nodes = nodes + vr["nodes"]
                    edges = edges + vr["edges"]
                except Exception:
                    pass  # verification failure is non-fatal
            if _targeted_categories:
                targeted_results = await asyncio.gather(
                    *[extract_targeted(chunk_text, cat, nodes, config) for cat in _targeted_categories],
                    return_exceptions=True,
                )
                for tr in targeted_results:
                    if not isinstance(tr, Exception):
                        nodes = nodes + tr["nodes"]
                        edges = edges + tr["edges"]
            return nodes, edges

        t0 = time.monotonic()
        async def _run_all_chunks():
            return await asyncio.gather(
                *[_extract_chunk(c, verify) for c in chunks], return_exceptions=True
            )
        raw_results = asyncio.run(_run_all_chunks())
        elapsed = time.monotonic() - t0

        for i, r in enumerate(raw_results, 1):
            if isinstance(r, BaseException):
                click.echo(f"  Chunk {i}/{n_chunks}: FAILED ({type(r).__name__}): {r}", err=True)
                failed_chunks += 1
            else:
                nodes, edges = r
                click.echo(f"  Chunk {i}/{n_chunks}: {len(nodes)} nodes, {len(edges)} edges")
                all_nodes.extend(nodes)
                all_edges.extend(edges)

        click.echo(f"  All {n_chunks} chunks done in {elapsed:.0f}s")

        if failed_chunks == n_chunks:
            click.echo(f"Error: all {n_chunks} chunks failed — nothing extracted.", err=True)
            sys.exit(1)
        if failed_chunks:
            click.echo(f"Warning: {failed_chunks}/{n_chunks} chunks failed; continuing with partial extraction.", err=True)

    nodes = all_nodes
    edges = all_edges

    # Set source transcript on all nodes
    for node in nodes:
        node["source_transcript"] = transcript_path.name
        node.setdefault("created_at", datetime.now(timezone.utc).isoformat())

    store = GraphStore(db_path)
    store.merge_extraction(nodes, edges)
    store.embed_missing_nodes()
    store.close()

    if synthesize:
        # Load ALL graph nodes (not just newly extracted) so synthesis spans full graph.
        # Filter to types most likely to contain parallel metrics — avoids prompt bloat.
        _SYNTHESIS_TYPES = {"decision", "implementation", "transition"}
        store = GraphStore(db_path)
        all_graph_nodes = store.get_all_nodes()
        store.close()
        candidate_nodes = [n for n in all_graph_nodes if n.get("type") in _SYNTHESIS_TYPES]
        click.echo(f"  Running synthesis pass over {len(candidate_nodes)} candidate nodes ({len(all_graph_nodes)} total)...")
        _SYNTHESIS_SOURCE = "__synthesis__"
        try:
            sr = asyncio.run(synthesize_extraction(candidate_nodes, config))
            if sr["nodes"]:
                click.echo(f"  +{len(sr['nodes'])} summary nodes, +{len(sr['edges'])} edges (--synthesize)")
                for sn in sr["nodes"]:
                    sn["source_transcript"] = _SYNTHESIS_SOURCE
                    sn.setdefault("created_at", datetime.now(timezone.utc).isoformat())
                store = GraphStore(db_path)
                store.merge_extraction(sr["nodes"], sr["edges"])
                store.close()
            else:
                click.echo("  No synthesis clusters found (need ≥3 parallel nodes on same metric).")
        except Exception as e:
            click.echo(f"  Synthesis pass failed (continuing): {e}", err=True)

    # Copy transcript to project's transcripts dir
    dest = get_project_dir(config, project) / "transcripts" / transcript_path.name
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not dest.exists():
        dest.write_text(transcript_text)

    quality = score_extraction_quality(nodes, edges, transcript_text)
    click.echo(
        f"Extracted {len(nodes)} nodes, {len(edges)} edges from {transcript_path.name}  "
        f"[density={quality['nodes_per_1k_chars']}/1kc  "
        f"avg_tags={quality['avg_tags_per_node']}  "
        f"edge/node={quality['edge_to_node_ratio']}]"
    )
    if quality["low_tag_nodes"]:
        click.echo(
            f"  Warning: {quality['low_tag_nodes']} node(s) have < 4 tags — "
            "may be hard to retrieve. Run 'waystone show {project}' to inspect."
        )


@cli.command("extract-config")
@click.argument("project")
@click.argument("config_file", type=click.Path(exists=True, dir_okay=False, readable=True))
@click.option("--dry-run", is_flag=True, help="Print nodes without storing them")
@click.option("--source", default=None, metavar="LABEL",
              help="Source label stored on each node (defaults to filename)")
@click.option("--dedup-threshold", type=float, default=0.75, show_default=True,
              metavar="FLOAT",
              help="Cosine similarity threshold above which a new pinned node is considered "
                   "redundant with an existing pinned node and skipped (0 = disable).")
@click.pass_context
def extract_config_cmd(ctx, project, config_file, dry_run, source, dedup_threshold):
    """Extract and classify items from a config file into the project graph.

    Reads CONFIG_FILE (e.g. CLAUDE.md, MEMORY.md, SOUL.md), classifies each
    item as pinned (always-inject) or conditional (relevance-gated), and inserts
    the resulting nodes into PROJECT's graph.

    Pinned nodes are flagged so they always appear in context output regardless
    of query. Use 'waystone pinned PROJECT' to review what's been pinned.
    """
    config = _load_cfg(ctx.obj["config_path"])
    db_path = get_db_path(config, project)

    if not db_path.parent.exists():
        click.echo(f"Error: Project '{project}' not found.", err=True)
        sys.exit(1)

    config_path = Path(config_file)
    config_text = config_path.read_text(encoding="utf-8")
    source_label = source or config_path.name

    click.echo(f"Extracting config items from '{config_path.name}' into '{project}'...")

    try:
        result = asyncio.run(extract_config_items(config_text, config))
    except Exception as e:
        click.echo(f"Error: Extraction failed: {e}", err=True)
        sys.exit(1)

    nodes = result.get("nodes", [])
    if not nodes:
        click.echo("No nodes extracted.")
        return

    pinned_count = sum(1 for n in nodes if n.get("pinned"))
    conditional_count = len(nodes) - pinned_count

    store = GraphStore(str(db_path))

    # Build dedup index: embed existing pinned nodes for similarity comparison.
    # Only used when dedup_threshold > 0 and embedder is available.
    dedup_index: list[tuple[str, str, bytes]] = []
    embedder_ok = False
    if dedup_threshold > 0:
        from waystone import embedder as _embedder
        if _embedder.is_available() and store._vec_available:
            embedder_ok = True
            dedup_index = store.get_pinned_embeddings()

    def _find_duplicate(fact: str) -> tuple[str, str, float] | None:
        """Return (matched_id, matched_fact, sim) if fact is near-duplicate of a pinned node."""
        if not embedder_ok or not dedup_index:
            return None
        from waystone import embedder as _embedder
        blob = _embedder.embed_text(fact)
        best_id, best_fact, best_sim = "", "", 0.0
        for pid, pfact, pemb in dedup_index:
            sim = _embedder.cosine_similarity(blob, pemb)
            if sim > best_sim:
                best_sim, best_id, best_fact = sim, pid, pfact
        if best_sim >= dedup_threshold:
            return best_id, best_fact, best_sim
        return None

    if dry_run:
        click.echo(f"\nDry run — {len(nodes)} nodes ({pinned_count} pinned, {conditional_count} conditional):\n")
        for node in nodes:
            is_pinned = node.get("pinned", False)
            pin_marker = "[PINNED]" if is_pinned else "[cond]  "
            tags = ", ".join(node.get("tags", [])[:4])
            click.echo(f"  {pin_marker} [{node.get('type', '?')}] {node['fact'][:90]}")
            if tags:
                click.echo(f"           tags: {tags}")
            if is_pinned and dedup_threshold > 0:
                dup = _find_duplicate(node["fact"])
                if dup:
                    dup_id, dup_fact, dup_sim = dup
                    click.echo(
                        f"           DEDUP [sim={dup_sim:.3f} vs '{dup_id}']: "
                        f"{dup_fact[:70]}"
                    )
        store.close()
        return

    inserted = 0
    pinned_inserted = 0
    dedup_skipped = 0
    now = datetime.now(timezone.utc).isoformat()

    for node in nodes:
        node.setdefault("created_at", now)
        node["source_transcript"] = source_label
        is_pinned = node.pop("pinned", False)
        # Dedup check: skip pinning (but still insert) if near-duplicate pinned node exists.
        skip_pin = False
        if is_pinned:
            dup = _find_duplicate(node["fact"])
            if dup:
                dup_id, dup_fact, dup_sim = dup
                click.echo(
                    f"  [dedup] Skipped pin (sim={dup_sim:.3f} vs '{dup_id}'): "
                    f"{node['fact'][:70]}"
                )
                skip_pin = True
                dedup_skipped += 1
        canonical_id = store.add_node(node)
        inserted += 1
        if is_pinned and not skip_pin:
            store.pin_node(canonical_id)
            pinned_inserted += 1

    store.close()

    dedup_note = f", {dedup_skipped} dedup-skipped" if dedup_skipped else ""
    click.echo(
        f"Inserted {inserted} nodes from '{config_path.name}': "
        f"{pinned_inserted} pinned, {inserted - pinned_inserted - dedup_skipped} conditional"
        f"{dedup_note}."
    )


@cli.command("survey")
@click.argument("project")
@click.option("--dry-run", is_flag=True, help="Print survey nodes without storing them")
@click.option("--max-nodes", type=int, default=1000, show_default=True,
              help="Max nodes to send to LLM (sorted by confidence desc); use 0 for no limit")
@click.option("--tags", multiple=True, metavar="TAG",
              help="Only include nodes tagged with at least one of these tags (e.g. --tags benchmark --tags model)")
@click.pass_context
def survey_cmd(ctx, project, dry_run, max_nodes, tags):
    """Create cross-cutting summary nodes from all nodes in the project graph.

    Scans the full graph for clusters of parallel facts (3+ nodes sharing the
    same metric/attribute across different subjects) and creates one comprehensive
    summary node per cluster — tagged with all subject names so survey queries
    like "rank all models" can find everything in one hop.
    """
    config = _load_cfg(ctx.obj["config_path"])
    db_path = get_db_path(config, project)

    if not db_path.parent.exists():
        click.echo(f"Error: Project '{project}' not found.", err=True)
        sys.exit(1)

    store = GraphStore(db_path)
    all_graph_nodes = store.get_all_nodes()
    store.close()

    # Filter to types most likely to contain parallel cross-subject metrics.
    _SYNTHESIS_TYPES = {"decision", "implementation", "transition"}
    candidate_nodes = [n for n in all_graph_nodes if n.get("type") in _SYNTHESIS_TYPES]

    # Optional tag filter
    if tags:
        tag_set = set(t.lower() for t in tags)
        candidate_nodes = [
            n for n in candidate_nodes
            if tag_set & {t.lower() for t in n.get("tags", [])}
        ]

    # Cap node count (sorted by confidence desc) to keep prompt manageable
    candidate_nodes.sort(key=lambda n: n.get("confidence", 0.0), reverse=True)
    if max_nodes and len(candidate_nodes) > max_nodes:
        click.echo(f"  Capping to {max_nodes} highest-confidence nodes (of {len(candidate_nodes)} candidates).")
        candidate_nodes = candidate_nodes[:max_nodes]

    click.echo(
        f"Running survey over {len(candidate_nodes)} candidate nodes "
        f"({len(all_graph_nodes)} total) in '{project}'..."
    )
    try:
        sr = asyncio.run(synthesize_extraction(candidate_nodes, config))
    except Exception as e:
        click.echo(f"Error: Survey failed: {e}", err=True)
        sys.exit(1)

    if not sr["nodes"]:
        click.echo("No survey clusters found (need ≥3 parallel nodes on same metric).")
        return

    click.echo(f"Found {len(sr['nodes'])} summary node(s), {len(sr['edges'])} edge(s):")
    for sn in sr["nodes"]:
        click.echo(f"  [{sn['type']}] {sn['fact'][:100]}")

    if dry_run:
        click.echo("(dry-run: not stored)")
        return

    _SYNTHESIS_SOURCE = "__survey__"
    for sn in sr["nodes"]:
        sn["source_transcript"] = _SYNTHESIS_SOURCE
        sn.setdefault("created_at", datetime.now(timezone.utc).isoformat())

    store = GraphStore(db_path)
    store.merge_extraction(sr["nodes"], sr["edges"])
    store.close()
    click.echo(f"Stored {len(sr['nodes'])} summary node(s).")


@cli.command("synthesize")
@click.argument("project")
@click.option("--dry-run", is_flag=True, help="Print survey nodes without storing them")
@click.option("--max-nodes", type=int, default=1000, show_default=True,
              help="Max nodes to send to LLM (sorted by confidence desc); use 0 for no limit")
@click.option("--tags", multiple=True, metavar="TAG",
              help="Only include nodes tagged with at least one of these tags (e.g. --tags benchmark --tags model)")
@click.pass_context
def synthesize_cmd(ctx, project, dry_run, max_nodes, tags):
    """Deprecated: Use 'waystone survey' instead.

    This command is an alias for backward compatibility. It forwards all arguments
    to the survey command.
    """
    click.echo("Warning: `waystone synthesize` is deprecated. Use `waystone survey` instead.", err=True)
    # Forward to survey_cmd with the same context and arguments
    ctx.invoke(survey_cmd, project=project, dry_run=dry_run, max_nodes=max_nodes, tags=tags)


@cli.command("reflect")
@click.argument("project")
@click.argument("transcript", type=click.Path(exists=True))
@click.option("--since-turn", type=int, default=None, help="Start from conversation turn N (0-indexed)")
@click.option("--domain", default="software_dev", help="Domain label for extracted process nodes")
@click.option("--chunk-size", type=int, default=200, show_default=True,
              help="Max utterances per LLM call; sessions longer than this are processed in windows")
@click.pass_context
def reflect_cmd(ctx, project, transcript, since_turn, domain, chunk_size):
    """Extract process patterns and protocols from a transcript window.

    Discovers procedural patterns and workflows that emerged through iteration,
    as opposed to explicit decisions or constraints.

    TRANSCRIPT can be a .jsonl file (Claude Code session) or plain text
    (Human:/Assistant: format). Long sessions are automatically chunked into
    windows of --chunk-size turns so LLM output stays within token limits.
    """
    from .transcript import from_claude_jsonl, from_plain_text, slice_since, to_prompt_text
    from .extractor import reflect_extraction
    from .llm import get_provider
    from .prompts import build_reflect_prompt

    config = _load_cfg(ctx.obj["config_path"])
    db_path = get_db_path(config, project)

    if not db_path.parent.exists():
        click.echo(f"Error: Project '{project}' not found. Run 'waystone init {project}' first.", err=True)
        sys.exit(1)

    transcript_path = Path(transcript)

    # Detect format and load
    if transcript_path.suffix.lower() == ".jsonl":
        utterances = from_claude_jsonl(transcript_path)
    else:
        text = transcript_path.read_text()
        utterances = from_plain_text(text)

    if not utterances:
        click.echo("Error: No utterances found in transcript.", err=True)
        sys.exit(1)

    # Slice if requested
    if since_turn is not None:
        if since_turn < 0 or since_turn >= len(utterances):
            click.echo(
                f"Error: --since-turn {since_turn} out of range (0-{len(utterances) - 1})",
                err=True,
            )
            sys.exit(1)
        utterances = slice_since(utterances, since_turn)

    total = len(utterances)

    # Token threshold for input prompts. Bisect chunks that exceed this so the
    # model's output stays well within max_tokens. Default: half of max_tokens
    # (conservative proxy — larger input tends to produce more output).
    llm_cfg = config.get("llm", {})
    max_output_tokens = llm_cfg.get("max_tokens", 65536)
    token_threshold = max_output_tokens // 2

    provider = get_provider(config)
    use_token_count = provider is not None and provider.supports_count_tokens

    click.echo(
        f"Reflecting on {total} turn(s) from {transcript_path.name} "
        f"(domain={domain}, max_chunk={chunk_size} turns, "
        f"token_threshold={token_threshold:,}"
        + (", count_tokens=on" if use_token_count else ", count_tokens=off (static chunking)")
        + ")..."
    )

    store = GraphStore(db_path)
    _episodic_domains = {"episodic_personal", "episodic_personal_no_dates"}
    _reflect_type = "episode" if domain in _episodic_domains else "process"
    existing_process_nodes = [n for n in store.get_all_nodes() if n.get("type") == _reflect_type]
    store.close()

    session_nodes: list[dict] = []  # nodes found this session (for dedup across chunks)
    total_nodes = 0
    total_edges = 0
    chunk_idx = 0
    remaining = list(utterances)

    # Max nodes passed to the prompt as dedup context. Passing all session_nodes
    # grows the prompt unboundedly: 1000 nodes × ~130 chars/node ≈ 32K tokens of
    # overhead, which forces bisection down to 1-turn chunks and causes
    # over-extraction. Cap at a small recent window; the DB fact_hash dedup handles
    # true duplicates regardless.
    _DEDUP_CONTEXT_LIMIT = 50

    while remaining:
        chunk_idx += 1
        # Use only the most recent N session nodes as dedup context so the prompt
        # stays bounded even after many chunks.
        recent_session = session_nodes[-_DEDUP_CONTEXT_LIMIT:] if len(session_nodes) > _DEDUP_CONTEXT_LIMIT else session_nodes
        dedup_nodes = existing_process_nodes[:max(0, _DEDUP_CONTEXT_LIMIT - len(recent_session))] + recent_session

        # Start with up to chunk_size utterances, then bisect by token count
        candidate = remaining[:chunk_size]

        if use_token_count and len(candidate) > 1:
            # Bisect down until the prompt fits within token_threshold
            while len(candidate) > 1:
                probe_prompt = build_reflect_prompt(to_prompt_text(candidate), dedup_nodes, domain)
                n_tokens = provider.count_tokens(probe_prompt)
                if n_tokens <= token_threshold:
                    break
                new_size = len(candidate) // 2
                click.echo(
                    f"  [token check] {n_tokens:,} tokens > {token_threshold:,}; "
                    f"bisecting {len(candidate)} → {new_size} turns"
                )
                candidate = candidate[:new_size]

        start_turn = total - len(remaining)
        end_turn = start_turn + len(candidate) - 1
        click.echo(f"  Chunk {chunk_idx}: turns {start_turn}–{end_turn} ({len(candidate)} turns)...")

        # Inner retry loop: if output overflows (MAX_TOKENS), bisect and retry
        result = None
        while True:
            try:
                result = asyncio.run(
                    reflect_extraction(
                        to_prompt_text(candidate),
                        project,
                        existing_process_nodes=dedup_nodes,
                        domain=domain,
                        config=config,
                    )
                )
                break  # success
            except ValueError as e:
                if "truncated" in str(e) and len(candidate) > 1:
                    # Output overflow — the chunk is too dense regardless of input size.
                    # Bisect and retry; the skipped second half will be picked up in the
                    # next outer iteration since remaining advances by len(candidate).
                    new_size = max(1, len(candidate) // 2)
                    click.echo(
                        f"  [output overflow] MAX_TOKENS on {len(candidate)} turns; "
                        f"retrying with {new_size} turns (turns {start_turn}–{start_turn + new_size - 1})"
                    )
                    candidate = candidate[:new_size]
                    end_turn = start_turn + len(candidate) - 1
                else:
                    click.echo(f"Error: Reflection failed on chunk {chunk_idx}: {e}", err=True)
                    if total_nodes:
                        click.echo(f"  Saved {total_nodes} node(s) from completed chunks.", err=True)
                    sys.exit(1)
            except Exception as e:
                click.echo(f"Error: Reflection failed on chunk {chunk_idx}: {e}", err=True)
                if total_nodes:
                    click.echo(f"  Saved {total_nodes} node(s) from completed chunks.", err=True)
                sys.exit(1)

        chunk_nodes = result.get("nodes", [])
        chunk_edges = result.get("edges", [])
        for node in chunk_nodes:
            node["source_transcript"] = transcript_path.name
            node.setdefault("created_at", datetime.now(timezone.utc).isoformat())

        if chunk_nodes:
            # Merge each chunk immediately so progress is never lost
            store = GraphStore(db_path)
            store.merge_extraction(chunk_nodes, chunk_edges)
            store.close()
            session_nodes.extend(chunk_nodes)
            total_nodes += len(chunk_nodes)
            total_edges += len(chunk_edges)

        click.echo(f"    → {len(chunk_nodes)} process node(s) found (total: {total_nodes})")
        remaining = remaining[len(candidate):]

    if not total_nodes:
        click.echo("No process patterns found in this conversation window.")
        return

    # Embed all newly added nodes in one pass
    store = GraphStore(db_path)
    store.embed_missing_nodes()
    store.close()

    click.echo(f"Extracted {total_nodes} process node(s), {total_edges} edge(s) into '{project}'.")


@cli.command()
@click.argument("project")
@click.argument("task")
@click.option("--hops", default=None, type=int, help="Graph traversal depth")
@click.option("--top-k", default=None, type=int, help="Max nodes to return")
@click.option("--enable", "-e", multiple=True, help="Enable a strategy (e.g. -e superseded_pruning -e recency_decay)")
@click.option("--disable", "-d", multiple=True, help="Disable a strategy (e.g. -d relevance_scoring)")
@click.option("--confidence", type=float, default=None, help="Min confidence threshold (e.g. 0.6)")
@click.option("--token-budget", type=int, default=None, help="Max tokens in output (e.g. 500)")
@click.option("--source", "source_prefix", multiple=True,
              help="Restrict results to nodes from this source path prefix (repeatable). "
                   "E.g. --source health/ --source work/ to scope to two sub-folders.")
@click.option("--stats", "show_stats", is_flag=True, help="Show retrieval stats (for benchmarking)")
@click.option("--at-time", "at_time", default=None,
              help="Bi-temporal point-in-time query: only return nodes whose valid window "
                   "covers this ISO 8601 timestamp (e.g. 2025-06-01T00:00:00). "
                   "Answers 'what was true in the world at this moment?'")
@click.pass_context
def query(ctx, project, task, hops, top_k, enable, disable, confidence, token_budget, source_prefix, show_stats, at_time):
    """Retrieve relevant context for a task description."""
    config = _load_cfg(ctx.obj["config_path"])
    db_path = get_db_path(config, project)

    if not db_path.parent.exists():
        click.echo(f"Error: Project '{project}' not found.", err=True)
        sys.exit(1)

    store = GraphStore(db_path)
    persisted_turns = store.load_buffer()
    store.close()

    if persisted_turns:
        inc_cfg = config.get("incremental", {})
        buffer = ExtractionBuffer(
            min_turns=inc_cfg.get("min_turns"),
            min_words=inc_cfg.get("min_words"),
            max_turns=inc_cfg.get("max_turns"),
            short_turn_words=inc_cfg.get("short_turn_words"),
        )
        buffer._turns = persisted_turns
        flushed_text = buffer.flush_if_nonempty()

        if flushed_text:
            click.echo(f"Auto-flushing buffer ({len(persisted_turns)} turns)...")

            store = GraphStore(db_path)
            inc_cfg = config.get("incremental", {})
            ctx_k = inc_cfg.get("context_k", 30)
            ctx_hops = inc_cfg.get("context_hops", 2)

            keywords = extract_keywords(flushed_text)
            context_nodes = []
            if keywords:
                entry_nodes = store.get_nodes_by_tags(keywords)
                if entry_nodes:
                    entry_nodes = score_by_relevance(entry_nodes, keywords)
                    context_nodes = bfs_collect(store, entry_nodes, ctx_hops)
                    context_nodes.sort(key=lambda n: n.get("_relevance", 0), reverse=True)
                    context_nodes = context_nodes[:ctx_k]
            store.close()

            try:
                result = asyncio.run(extract_turn(flushed_text, context_nodes, config))
            except Exception as e:
                click.echo(f"Extraction failed: {e}", err=True)
                sys.exit(1)

            nodes = result["nodes"]
            edges = result["edges"]

            for node in nodes:
                node.setdefault("source_transcript", "turn")
                node.setdefault("created_at", datetime.now(timezone.utc).isoformat())

            store = GraphStore(db_path)
            store.merge_extraction(nodes, edges)
            store.clear_buffer()
            store.close()

    defaults = config.get("defaults", {})
    hops = hops or defaults.get("hops", 3)
    top_k = top_k or defaults.get("top_k", 10)

    overrides = _parse_strategy_overrides(enable, disable, confidence, token_budget)
    strategies = _resolve_strategies(config, overrides)
    if source_prefix:
        strategies["source_prefix"] = list(source_prefix)

    store = GraphStore(db_path)
    stats = store.get_stats()
    if stats["node_count"] == 0:
        click.echo(
            f"Graph is empty. Extract a transcript first:\n"
            f"  waystone extract {project} <transcript_file>\n"
            f"Or replay an existing transcript turn-by-turn:\n"
            f"  waystone extract-replay {project} <transcript_file>",
            err=True,
        )
        store.close()
        sys.exit(1)

    if at_time:
        # Bi-temporal mode: pass valid_at to the retrieval strategy pipeline so
        # post-BFS filtering drops nodes outside their valid window.
        strategies["temporal_valid_at"] = at_time
        click.echo(f"[temporal] Restricting to nodes valid at {at_time}")

    result = retrieve_with_stats(store, task, hops=hops, top_k=top_k, strategies=strategies)
    store.close()

    click.echo(result.markdown)

    if show_stats:
        click.echo("\n--- Retrieval Stats ---")
        click.echo(f"Nodes before strategies: {result.nodes_before_strategies}")
        click.echo(f"Nodes after strategies:  {result.nodes_after_strategies}")
        click.echo(f"Strategies applied:      {', '.join(result.strategies_applied) or 'none'}")
        click.echo(f"Estimated tokens:        {result.tokens_estimated}")


@cli.command("conflicts")
@click.argument("project")
@click.option("--min-overlap", default=2, show_default=True, type=int,
              help="Min shared tags to flag a conflict candidate")
@click.option("--tags", "-t", multiple=True,
              help="Restrict conflict scan to nodes sharing these tags (repeatable). "
                   "Default: scan all decision/transition nodes against each other.")
@click.option("-o", "--output", default=None, help="Write report to this file instead of stdout")
@click.pass_context
def conflicts_cmd(ctx, project, min_overlap, tags, output):
    """Audit the graph for potentially contradictory decisions.

    Scans all decision and transition nodes. For each node, finds others with
    overlapping tags and reports them as conflict candidates. Useful for
    reviewing accumulated decisions before a big architectural change.

    Examples:

        waystone conflicts myproject
        waystone conflicts myproject --min-overlap 3
        waystone conflicts myproject --tags redis --tags auth-service
        waystone conflicts myproject -o conflicts.md
    """
    config = _load_cfg(ctx.obj["config_path"])
    db_path = get_db_path(config, project)

    if not db_path.parent.exists():
        click.echo(f"Error: Project '{project}' not found.", err=True)
        sys.exit(1)

    store = GraphStore(db_path, vec_enabled=False)
    all_nodes = store.get_all_nodes()
    decision_nodes = [
        n for n in all_nodes
        if n["type"] in ("decision", "transition") and n.get("is_active", True)
    ]

    if not decision_nodes:
        click.echo("No decision or transition nodes found in this project.")
        store.close()
        return

    # Build edge index: supersedes pairs (already resolved) and conflicts_with pairs (known).
    all_edges = store.get_all_edges()
    supersedes_pairs: set[frozenset] = set()
    known_conflict_pairs: set[frozenset] = set()
    for edge in all_edges:
        pair = frozenset({edge["from_id"], edge["to_id"]})
        if edge["relation"] == "supersedes":
            supersedes_pairs.add(pair)
        elif edge["relation"] == "conflicts_with":
            known_conflict_pairs.add(pair)

    # If --tags provided, use them as the candidate set; otherwise scan every
    # decision node against the full tag universe for that node.
    report_lines = [f"# Conflict Audit: {project}", ""]
    total_pairs: list[tuple[dict, list[dict]]] = []

    for node in decision_nodes:
        node_tags = node.get("tags") or []
        if not node_tags:
            continue
        # Use provided --tags filter if given, else use this node's own tags
        scan_tags = list(tags) if tags else node_tags
        candidates = find_conflicts(
            store,
            scan_tags,
            exclude_ids={node["id"]},
            min_tag_overlap=min_overlap,
        )
        if candidates:
            total_pairs.append((node, candidates))

    # Deduplicate: (A conflicts with B) and (B conflicts with A) are the same pair.
    # Skip pairs that are already resolved via supersedes.
    seen_pairs: set[frozenset] = set()
    unique_pairs: list[tuple[dict, dict]] = []
    for node, candidates in total_pairs:
        for cand in candidates:
            pair_key = frozenset({node["id"], cand["id"]})
            if pair_key not in seen_pairs and pair_key not in supersedes_pairs:
                seen_pairs.add(pair_key)
                unique_pairs.append((node, cand))

    if not unique_pairs:
        click.echo(f"No conflict candidates found (min_overlap={min_overlap}).")
        store.close()
        return

    # Group by shared tags
    from .retriever import _polarity
    report_lines.append(f"Found **{len(unique_pairs)}** potential conflict pair(s) (min_overlap={min_overlap}).")
    report_lines.append("")

    for i, (a, b) in enumerate(unique_pairs, 1):
        a_tags = {t.lower() for t in (a.get("tags") or [])}
        b_tags = {t.lower() for t in (b.get("tags") or [])}
        shared = sorted(a_tags & b_tags)
        a_pol = _polarity(a["fact"])
        b_pol = _polarity(b["fact"])
        pair_key = frozenset({a["id"], b["id"]})
        if pair_key in known_conflict_pairs:
            confidence = "KNOWN"
        elif a_pol != b_pol and a_pol != "unknown" and b_pol != "unknown":
            confidence = "HIGH"
        else:
            confidence = "POSSIBLE"
        a_date = (a.get("created_at") or "")[:10]
        b_date = (b.get("created_at") or "")[:10]

        report_lines.append(f"## Pair {i} [{confidence} CONFLICT]")
        report_lines.append(f"**Shared tags:** {', '.join(shared)}")
        report_lines.append("")
        report_lines.append(f"**A** [{a['type']}] ({a_date}, confidence {a.get('confidence', 0.5):.2f})")
        report_lines.append(f"> {a['fact']}")
        report_lines.append("")
        report_lines.append(f"**B** [{b['type']}] ({b_date}, confidence {b.get('confidence', 0.5):.2f})")
        report_lines.append(f"> {b['fact']}")
        report_lines.append("")

    store.close()

    report = "\n".join(report_lines)
    if output:
        from pathlib import Path
        Path(output).write_text(report)
        click.echo(f"Conflict report written to {output} ({len(unique_pairs)} pair(s)).")
    else:
        click.echo(report)


@cli.command("impact")
@click.argument("project")
@click.argument("node_id", required=False, default=None)
@click.option("--query", "query_text", default=None, metavar="TEXT",
              help="Find seed nodes by tag-matching this text instead of a node ID")
@click.option("--hops", default=3, type=int, show_default=True,
              help="Traversal depth")
@click.option("--reverse", is_flag=True, default=False,
              help="Traverse incoming edges (what does this node depend on?) instead of outgoing")
@click.option("--types", "node_types", default="decision,constraint,implementation",
              show_default=True, help="Comma-separated node types to include in results")
@click.option("--format", "fmt", type=click.Choice(["markdown", "json"]),
              default="markdown", show_default=True)
@click.option("-o", "--output", default=None, help="Write report to this file path")
@click.pass_context
def impact_cmd(ctx, project, node_id, query_text, hops, reverse, node_types, fmt, output):
    """Trace the blast radius of a decision — show what it touches in the graph.

    \b
    Examples:
      waystone impact myproject n_abc12345
      waystone impact myproject --query "auth token format" --hops 4
      waystone impact myproject n_abc12345 --reverse
    """
    if not node_id and not query_text:
        raise click.UsageError("Provide either NODE_ID or --query TEXT.")
    if node_id and query_text:
        raise click.UsageError("Provide either NODE_ID or --query TEXT, not both.")

    config = _load_cfg(ctx.obj["config_path"])
    db_path = get_db_path(config, project)

    if not db_path.parent.exists():
        click.echo(f"Error: Project '{project}' not found.", err=True)
        sys.exit(1)

    store = GraphStore(db_path)
    types_set = frozenset(t.strip() for t in node_types.split(",") if t.strip())

    from .retriever import compute_impact, extract_keywords, format_impact_report, score_by_relevance

    if node_id:
        seed_nodes = store.get_nodes_by_ids([node_id])
        if not seed_nodes:
            click.echo(f"Error: Node '{node_id}' not found in project '{project}'.", err=True)
            store.close()
            sys.exit(1)
    else:
        keywords = extract_keywords(query_text)
        if not keywords:
            click.echo("Error: Could not extract keywords from query text.", err=True)
            store.close()
            sys.exit(1)
        candidates = store.get_nodes_by_tags(keywords)
        if not candidates:
            click.echo("No nodes matched the query — impact map is empty.")
            store.close()
            return
        seed_nodes = score_by_relevance(candidates, keywords)[:5]

    seed_ids = [n["id"] for n in seed_nodes]
    impact = compute_impact(store, seed_ids, hops=hops, reverse=reverse, node_types=types_set)
    store.close()

    if fmt == "json":
        import json
        result = {
            "seed_nodes": seed_nodes,
            "impact": {
                str(depth): [
                    {"node": node, "path": path}
                    for node, path in hop_nodes
                ]
                for depth, hop_nodes in impact.items()
            },
        }
        report = json.dumps(result, indent=2)
    else:
        report = format_impact_report(seed_nodes, impact, reverse=reverse)

    if output:
        from pathlib import Path
        Path(output).write_text(report)
        total = sum(len(v) for v in impact.values())
        click.echo(f"Impact report written to {output} ({total} node(s) found).")
    else:
        click.echo(report)


def _detect_marker_project(cwd: Path | None = None) -> str | None:
    """Resolve a project name from the nearest .waystone marker (cwd upward)."""
    start = (cwd or Path.cwd()).resolve()
    home = Path.home()
    for directory in [start, *start.parents]:
        marker = directory / ".waystone"
        if marker.exists():
            try:
                name = marker.read_text().strip()
                if name:
                    return name
            except Exception:
                pass
        if directory == home:
            break
    return None


@cli.command("remember")
@click.argument("text")
@click.option("--project", default=None, help="Project name (auto-detected from .waystone if omitted)")
@click.option("--type", "node_type", default="decision", help="Node type (default: decision)")
@click.option("--pin", is_flag=True, help="Pin the fact so it's always injected")
@click.option("--tags", default="", help="Comma-separated extra tags")
@click.pass_context
def remember(ctx, text, project, node_type, pin, tags):
    """Add a fact to the graph immediately — no LLM, no buffering.

    The instant, deterministic capture behind the /btw slash command: stores
    your text verbatim as one high-confidence node, keyword-tagged and
    retrievable in every future session. Use --pin for "never forget this" facts.
    """
    from uuid import uuid4

    config = _load_cfg(ctx.obj["config_path"])
    project = project or _detect_marker_project()
    if not project:
        click.echo(
            "Error: no project given and no .waystone marker found. Pass --project.",
            err=True,
        )
        sys.exit(1)

    db_path = get_db_path(config, project)
    # vec disabled → instant (no embedding-model load). Tags make it immediately
    # retrievable; embeddings backfill on the next extraction or `waystone reembed`.
    store = GraphStore(db_path, vec_enabled=False)
    base_tags = list(extract_keywords(text))
    if tags:
        base_tags += [t.strip() for t in tags.split(",") if t.strip()]
    now = datetime.now(timezone.utc).isoformat()
    node = {
        "id": f"n_{uuid4().hex[:8]}",
        "fact": text,
        "type": node_type,
        "confidence": 1.0,
        "tags": sorted(set(base_tags)),
        "source_transcript": "manual",
        "created_at": now,
        "occurred_at": now,
    }
    node_id = store.add_node(node)
    if pin:
        store.pin_node(node_id)
    store.close()
    click.echo(
        f"✓ Remembered [{node_type}{', pinned' if pin else ''}] in '{project}': {text[:80]}"
    )


@cli.command("reembed")
@click.argument("project")
@click.pass_context
def reembed(ctx, project):
    """Rebuild all node embeddings using the configured embedding backend.

    Run this after changing `embeddings.backend` (or model/dim) in your config:
    the vector table is recreated at the new dimension and every node is
    re-embedded. Requires sqlite-vec; the `api` backend also needs an API key.
    """
    from waystone import embedder

    config = _load_cfg(ctx.obj["config_path"])
    db_path = get_db_path(config, project)
    if not db_path.parent.exists():
        click.echo(f"Error: Project '{project}' not found.", err=True)
        sys.exit(1)

    embedder.configure(config)
    if not embedder.is_available():
        click.echo(
            "Error: embedding backend unavailable. For 'local', install "
            "waystone[semantic]; for 'api', set your API key.",
            err=True,
        )
        sys.exit(1)

    store = GraphStore(db_path)
    if not store._vec_available:
        click.echo("Error: sqlite-vec is not loaded — cannot build embeddings.", err=True)
        store.close()
        sys.exit(1)

    click.echo(
        f"Rebuilding embeddings for '{project}' "
        f"(backend={embedder.get_backend()}, dim={embedder.get_embedding_dim()})…"
    )
    count = store.rebuild_embeddings()
    store.close()
    click.echo(f"✓ Re-embedded {count} node(s).")


@cli.command()
@click.argument("project")
@click.option("--failures", is_flag=True, help="Show recent extraction failures instead of nodes")
@click.pass_context
def show(ctx, project, failures):
    """Display graph statistics and recent nodes, or extraction failures."""
    config = _load_cfg(ctx.obj["config_path"])
    db_path = get_db_path(config, project)

    if not db_path.parent.exists():
        click.echo(f"Error: Project '{project}' not found.", err=True)
        sys.exit(1)

    store = GraphStore(db_path)

    if failures:
        # Show extraction failures
        failure_list = store.get_extraction_failures(limit=50)
        if not failure_list:
            click.echo("No extraction failures recorded.")
        else:
            click.echo("Extraction Failures (last 50)")
            click.echo("─" * 100)
            for failure in failure_list:
                created = failure["created_at"][:16] if failure["created_at"] else "unknown"
                source = failure["source_transcript"] or "unknown"
                error_type = failure["error_type"] or "unknown"
                model = failure["model"] or "unknown"
                msg = failure["error_message"] or ""
                msg_short = msg[:50] + "..." if len(msg) > 50 else msg
                click.echo(f"{created}  {error_type:12}  {source:25}  {model:20}  {msg_short}")
    else:
        # Show graph statistics
        stats = store.get_stats()

        click.echo(f"Project: {project}")
        click.echo(f"Nodes: {stats['node_count']}")
        click.echo(f"Edges: {stats['edge_count']}")

        if stats["type_counts"]:
            click.echo("\nBy type:")
            for t, count in sorted(stats["type_counts"].items()):
                click.echo(f"  {t}: {count}")

        recent = store.get_recent_nodes(10)
        if recent:
            click.echo("\nRecent nodes:")
            for node in recent:
                tags = ", ".join(node["tags"][:3]) if node["tags"] else ""
                click.echo(f"  [{node['type']}] {node['fact'][:80]}  ({tags})")

    store.close()


@cli.command()
@click.argument("project")
@click.option("--top-k", default=None, type=int, help="Assumed nodes retrieved per query (default: from config)")
@click.option("--queries", default=100, type=int, help="Query count for cumulative savings projection (default 100)")
@click.pass_context
def savings(ctx, project, top_k, queries):
    """Estimate token and cost savings from using Waystone vs full-context replay."""
    config = _load_cfg(ctx.obj["config_path"])
    db_path = get_db_path(config, project)

    if not db_path.parent.exists():
        click.echo(f"Error: Project '{project}' not found.", err=True)
        sys.exit(1)

    store = GraphStore(db_path)
    stats = store.get_stats()
    node_count = stats["node_count"]

    if node_count == 0:
        click.echo(f"Project '{project}' has no nodes yet. Run 'waystone extract' first.")
        store.close()
        return

    # Average fact length from actual node data
    row = store.conn.execute(
        "SELECT AVG(LENGTH(fact)) FROM nodes WHERE is_active = 1 OR is_active IS NULL"
    ).fetchone()
    avg_fact_chars = row[0] or 200.0

    # Tags add ~half their JSON byte size in real content
    tag_row = store.conn.execute(
        "SELECT AVG(LENGTH(tags)) FROM nodes WHERE is_active = 1 OR is_active IS NULL"
    ).fetchone()
    avg_tags_chars = (tag_row[0] or 60.0) * 0.5
    avg_node_chars = avg_fact_chars + avg_tags_chars
    avg_tokens_per_node = avg_node_chars / 4.0  # ~4 chars/token

    full_tokens = int(node_count * avg_tokens_per_node)

    # Retrieval: top_k entry nodes expanded by BFS hops
    default_top_k = config.get("defaults", {}).get("top_k", 10)
    effective_top_k = top_k or default_top_k
    hops = config.get("defaults", {}).get("hops", 3)
    bfs_multiplier = min(2.5, 1.0 + hops * 0.5)  # hops=3 → ~2.5×, hops=1 → ~1.5×
    retrieved_nodes = min(int(effective_top_k * bfs_multiplier), node_count)
    retrieval_tokens = int(retrieved_nodes * avg_tokens_per_node)

    savings_pct = (1.0 - retrieval_tokens / full_tokens) * 100 if full_tokens > 0 else 0.0

    store.close()

    # Price table: (label, $/1M input tokens)  — approximate list prices
    PRICE_POINTS = [
        ("Gemini 2.5 Flash-Lite", 0.10),
        ("Gemini 2.5 Flash",      0.15),
        ("Claude Haiku 4.5",      0.80),
        ("Claude Sonnet 4.6",     3.00),
    ]

    click.echo(f"\nWaystone Memory Savings — {project}")
    click.echo("─" * 52)
    click.echo(f"Graph:             {node_count:,} nodes  ·  {stats['edge_count']:,} edges")
    click.echo(f"Avg node size:     ~{int(avg_tokens_per_node)} tokens  ({int(avg_node_chars)} chars)")
    click.echo()
    click.echo("Context per query:")
    click.echo(f"  Full graph replay:   {full_tokens:>9,} tokens")
    click.echo(f"  Waystone retrieval:    {retrieval_tokens:>9,} tokens  (~{retrieved_nodes} nodes, top_k={effective_top_k}, hops={hops})")
    click.echo(f"  Reduction:           {savings_pct:.0f}% fewer tokens per query")
    click.echo()
    click.echo(f"Estimated cost savings  (over {queries:,} queries, input tokens only):")
    header = f"  {'Model':<26}  {'Full-ctx/q':>12}  {'Waystone/q':>10}  {'Per-query':>10}  {'×{:,} queries'.format(queries):>13}"
    click.echo(header)
    click.echo(f"  {'─'*26}  {'─'*12}  {'─'*10}  {'─'*10}  {'─'*13}")
    for label, price_per_m in PRICE_POINTS:
        full_cost = full_tokens * price_per_m / 1_000_000
        engram_cost = retrieval_tokens * price_per_m / 1_000_000
        saved_per_q = full_cost - engram_cost
        total_saved = saved_per_q * queries
        click.echo(
            f"  {label:<26}  ${full_cost:>11.4f}  ${engram_cost:>9.4f}  ${saved_per_q:>9.4f}  ${total_saved:>12.2f}"
        )
    click.echo()
    click.echo("Note: based on graph topology and default retrieval settings.")
    click.echo("Actual savings depend on query patterns and BFS traversal depth.")


@cli.command()
@click.argument("project")
@click.option("--limit", default=50, type=int, help="Max sources to show (default 50)")
@click.pass_context
def sources(ctx, project, limit):
    """List all source paths ingested into a project, with node counts."""
    config = _load_cfg(ctx.obj["config_path"])
    db_path = get_db_path(config, project)

    if not db_path.parent.exists():
        click.echo(f"Error: Project '{project}' not found.", err=True)
        sys.exit(1)

    store = GraphStore(db_path)
    srcs = store.get_sources()[:limit]
    store.close()

    if not srcs:
        click.echo("No sources found.")
        return

    for s in srcs:
        click.echo(f"{s['count']:6d}  {s['source']}")


@cli.command()
@click.argument("project")
@click.argument("node_id")
@click.pass_context
def history(ctx, project, node_id):
    """Show belief-revision history for a node (oldest ancestor → current).

    Walks the supersedes[] chain to reconstruct how a fact evolved over time.
    Each entry shows the node's fact, type, confidence, and valid time window.
    """
    config = _load_cfg(ctx.obj["config_path"])
    db_path = get_db_path(config, project)
    store = GraphStore(db_path)
    chain = store.get_revision_history(node_id)
    store.close()

    if not chain:
        click.echo(f"Node {node_id} not found.", err=True)
        sys.exit(1)

    click.echo(f"Revision history for {node_id} ({len(chain)} version(s)):")
    click.echo("─" * 80)
    for i, n in enumerate(chain):
        status = "ACTIVE" if n.get("is_active", 1) else "superseded"
        valid_from = n.get("occurred_at") or n.get("created_at", "")[:10]
        valid_to = n.get("valid_to", "")[:10] if n.get("valid_to") else "present"
        click.echo(
            f"  [{i+1}] {n['id']}  [{status}]  {valid_from} → {valid_to}\n"
            f"      {n['fact']}\n"
            f"      type={n['type']}  confidence={n.get('confidence',0):.2f}"
        )
        if n.get("supersedes"):
            click.echo(f"      supersedes: {n['supersedes']}")
        click.echo()


@cli.command()
@click.argument("project")
@click.argument("node_id")
@click.pass_context
def pin(ctx, project, node_id):
    """Pin a node so it always injects into context regardless of query relevance."""
    config = _load_cfg(ctx.obj["config_path"])
    db_path = get_db_path(config, project)
    store = GraphStore(db_path)
    ok = store.pin_node(node_id)
    store.close()
    if ok:
        click.echo(f"Pinned node {node_id}")
    else:
        click.echo(f"Node {node_id} not found.", err=True)
        sys.exit(1)


@cli.command()
@click.argument("project")
@click.argument("node_id", required=False)
@click.option("--source", default=None, metavar="LABEL",
              help="Unpin all nodes with this source_transcript label (e.g. SOUL.md)")
@click.pass_context
def unpin(ctx, project, node_id, source):
    """Remove the pinned flag from a node, or from all nodes matching a source label.

    Either NODE_ID or --source is required.

    Examples:

      waystone unpin myproject some_node_id

      waystone unpin myproject --source SOUL.md
    """
    if not node_id and not source:
        click.echo("Error: provide NODE_ID or --source LABEL.", err=True)
        sys.exit(1)
    config = _load_cfg(ctx.obj["config_path"])
    db_path = get_db_path(config, project)
    store = GraphStore(db_path)
    if source:
        count = store.unpin_by_source(source)
        store.close()
        click.echo(f"Unpinned {count} node(s) with source '{source}'.")
    else:
        ok = store.unpin_node(node_id)
        store.close()
        if ok:
            click.echo(f"Unpinned node {node_id}")
        else:
            click.echo(f"Node {node_id} not found.", err=True)
            sys.exit(1)


@cli.command("pinned")
@click.argument("project")
@click.pass_context
def pinned_cmd(ctx, project):
    """List all pinned nodes for a project."""
    config = _load_cfg(ctx.obj["config_path"])
    db_path = get_db_path(config, project)
    store = GraphStore(db_path)
    nodes = store.get_pinned_nodes()
    store.close()
    if not nodes:
        click.echo("No pinned nodes.")
        return
    click.echo(f"Pinned nodes ({len(nodes)}):")
    for node in nodes:
        tags = ", ".join(node["tags"][:3]) if node["tags"] else ""
        click.echo(f"  {node['id']}  [{node['type']}]  {node['fact'][:80]}  ({tags})")


@cli.command()
@click.argument("project")
@click.option("--output", "-o", default=None, help="Output file path")
@click.option("--format", "fmt", type=click.Choice(["dump", "briefing", "handoff"]),
              default="dump", help="Output format")
@click.option("--include-superseded", is_flag=True, default=True,
              help="Include superseded nodes in briefing (default: true)")
@click.option("--enable", "-e", multiple=True, help="Enable a strategy")
@click.option("--disable", "-d", multiple=True, help="Disable a strategy")
@click.option("--confidence", type=float, default=None, help="Min confidence threshold")
@click.option("--token-budget", type=int, default=None, help="Max tokens in output")
@click.pass_context
def export(ctx, project, output, fmt, include_superseded, enable, disable, confidence, token_budget):
    """Export the full graph as markdown, with optional strategy filtering."""
    config = _load_cfg(ctx.obj["config_path"])
    db_path = get_db_path(config, project)

    if not db_path.parent.exists():
        click.echo(f"Error: Project '{project}' not found.", err=True)
        sys.exit(1)

    store = GraphStore(db_path)
    all_nodes = store.get_all_nodes()

    if not all_nodes:
        click.echo("Graph is empty — nothing to export.")
        store.close()
        return

    # If using briefing or handoff format, fetch edges and assemble directly
    if fmt in ("briefing", "handoff"):
        from .retriever import assemble_briefing
        all_edges = store.get_all_edges()
        markdown = assemble_briefing(all_nodes, all_edges, project, fmt=fmt, include_superseded=include_superseded)
        store.close()
    else:
        # Original dump format with strategy filtering
        overrides = _parse_strategy_overrides(enable, disable, confidence, token_budget)
        strategies = _resolve_strategies(config, overrides)

        # Apply post-retrieval strategies to all nodes
        from .retriever import (
            apply_recency_decay,
            apply_token_budget,
            assemble_markdown,
            filter_by_confidence,
            prune_superseded,
        )

        applied = []
        if strategies.get("superseded_pruning"):
            all_nodes = prune_superseded(all_nodes, store)
            applied.append("superseded_pruning")
        if strategies.get("confidence_threshold", 0) > 0:
            all_nodes = filter_by_confidence(all_nodes, strategies["confidence_threshold"])
            applied.append(f"confidence_threshold({strategies['confidence_threshold']})")
        if strategies.get("recency_decay"):
            all_nodes = apply_recency_decay(all_nodes, strategies.get("recency_half_life_days", 30))
            applied.append("recency_decay")
            all_nodes.sort(key=lambda n: n.get("_score", n.get("confidence", 0)), reverse=True)
        if strategies.get("token_budget", 0) > 0:
            all_nodes = apply_token_budget(all_nodes, strategies["token_budget"])
            applied.append(f"token_budget({strategies['token_budget']})")

        markdown = assemble_markdown(all_nodes, f"Full export of {project}", applied)
        store.close()

    if output:
        out_path = Path(output)
    else:
        out_path = get_project_dir(config, project) / "exports" / "current.md"

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(markdown)
    click.echo(f"Exported {len(all_nodes)} nodes to {out_path}")



@cli.command("reconcile")
@click.argument("project")
@click.option("--dry-run", is_flag=True, help="Show supersedes pairs without writing to graph")
@click.option("--max-cluster-size", default=20, type=int, show_default=True,
              help="Max nodes per LLM call")
@click.option("--semantic-dedup/--no-semantic-dedup", default=True, show_default=True,
              help="After LLM reconcile, merge paraphrase duplicates via embedding cosine similarity")
@click.option("--dedup-threshold", default=0.93, type=float, show_default=True,
              help="Cosine similarity threshold for semantic dedup (0.93 recommended)")
@click.pass_context
def reconcile_cmd(ctx, project, dry_run, max_cluster_size, semantic_dedup, dedup_threshold):
    """Aggressively find and record supersedes relationships across all graph nodes.

    Clusters nodes by tag overlap, sends each cluster to the LLM asking
    "which of these are superseded by others?", and writes the resulting
    supersedes edges back into the graph.

    Optionally follows the LLM pass with a semantic dedup step that finds
    paraphrase duplicates via embedding cosine similarity and merges them.
    Use --no-semantic-dedup to skip or --dry-run to preview without writing.

    Use this after bulk imports or long sessions to clean up stale nodes.
    """
    config = _load_cfg(ctx.obj["config_path"])
    db_path = get_db_path(config, project)

    if not db_path.parent.exists():
        click.echo(f"Error: Project '{project}' not found.", err=True)
        sys.exit(1)

    store = GraphStore(db_path)
    all_nodes = store.get_all_nodes()

    if not all_nodes:
        click.echo("Graph is empty — nothing to reconcile.")
        store.close()
        return

    # Skip nodes that are already superseded (no point reconciling them)
    existing_superseded = set()
    for node in all_nodes:
        for sid in node.get("supersedes", []):
            existing_superseded.add(sid)
    for edge in store.get_all_edges():
        if edge["relation"] == "supersedes":
            existing_superseded.add(edge["to_id"])

    active_nodes = [n for n in all_nodes if n["id"] not in existing_superseded]

    clusters = cluster_by_tags(active_nodes, max_cluster_size)
    click.echo(
        f"Reconciling {len(active_nodes)} active nodes across {len(clusters)} clusters "
        f"({'dry run' if dry_run else 'will write'})..."
    )

    total_pairs = 0
    total_written = 0

    async def run_all():
        nonlocal total_pairs, total_written
        for i, cluster in enumerate(clusters, 1):
            pairs = await reconcile_group(cluster, config)
            if not pairs:
                click.echo(f"  [{i}/{len(clusters)}] {len(cluster)} nodes — no supersedes found")
                continue

            total_pairs += len(pairs)
            click.echo(f"  [{i}/{len(clusters)}] {len(cluster)} nodes — {len(pairs)} supersedes pair(s):")
            for pair in pairs:
                sup_id = pair["superseding_id"]
                old_id = pair["superseded_id"]
                # Find facts for display
                sup_node = next((n for n in cluster if n["id"] == sup_id), None)
                old_node = next((n for n in cluster if n["id"] == old_id), None)
                sup_fact = sup_node["fact"][:70] if sup_node else sup_id
                old_fact = old_node["fact"][:70] if old_node else old_id
                click.echo(f"    SUPERSEDES: \"{sup_fact}\"")
                click.echo(f"      → replaces: \"{old_fact}\"")

                if not dry_run:
                    store.add_edge(sup_id, old_id, "supersedes")
                    # Also update supersedes list on the superseding node
                    sup_node_stored = store.get_node(sup_id)
                    if sup_node_stored:
                        supersedes_list = sup_node_stored.get("supersedes", [])
                        if old_id not in supersedes_list:
                            supersedes_list.append(old_id)
                            import json as _json
                            store.conn.execute(
                                "UPDATE nodes SET supersedes = ? WHERE id = ?",
                                (_json.dumps(supersedes_list), sup_id),
                            )
                            store.conn.commit()
                    total_written += 1

    asyncio.run(run_all())

    # --- Semantic dedup pass (embedding-based paraphrase merging) ---
    if semantic_dedup:
        from waystone import embedder as _embedder
        if not store._vec_available or not _embedder.is_available():
            click.echo("\nSemantic dedup skipped — sqlite-vec or sentence-transformers unavailable.")
        else:
            click.echo(f"\nSemantic dedup pass (threshold={dedup_threshold})...")
            embedded = store.embed_missing_nodes()
            if embedded:
                click.echo(f"  Embedded {embedded} unindexed node(s).")
            pairs_dedup = store.find_semantic_duplicates(threshold=dedup_threshold, top_k=5)
            if not pairs_dedup:
                click.echo("  No paraphrase duplicates found.")
            else:
                click.echo(f"  Found {len(pairs_dedup)} paraphrase duplicate pair(s):")
                for keep_id, drop_id, sim in pairs_dedup[:20]:
                    keep_row = store.conn.execute(
                        "SELECT fact FROM nodes WHERE id = ?", (keep_id,)
                    ).fetchone()
                    drop_row = store.conn.execute(
                        "SELECT fact FROM nodes WHERE id = ?", (drop_id,)
                    ).fetchone()
                    if keep_row and drop_row:
                        keep_fact = (keep_row[0][:60] + "...") if len(keep_row[0]) > 63 else keep_row[0]
                        drop_fact = (drop_row[0][:60] + "...") if len(drop_row[0]) > 63 else drop_row[0]
                        click.echo(f"    sim={sim:.3f}  KEEP [{keep_id}] {keep_fact}")
                        click.echo(f"           DROP [{drop_id}] {drop_fact}")
                if len(pairs_dedup) > 20:
                    click.echo(f"  ... and {len(pairs_dedup) - 20} more.")
                if dry_run:
                    click.echo(f"  Dry run: would merge {len(pairs_dedup)} pair(s).")
                else:
                    merged = sum(
                        1 for keep_id, drop_id, _ in pairs_dedup
                        if store.merge_node_into(keep_id, drop_id)
                    )
                    click.echo(f"  Merged {merged} paraphrase duplicate(s).")

    store.close()

    if dry_run:
        click.echo(f"\nDry run: found {total_pairs} supersedes pair(s) across {len(clusters)} clusters.")
        click.echo("Run without --dry-run to write them to the graph.")
    else:
        click.echo(f"\nWrote {total_written} supersedes edge(s) to '{project}' graph.")
        if total_written:
            click.echo("Run 'waystone show' to inspect, or 'waystone query' to see the pruned results.")


@cli.command("prune")
@click.argument("project")
@click.option(
    "--older-than",
    "older_than_days",
    default=None,
    type=int,
    help="Remove nodes older than N days",
)
@click.option(
    "--confidence-below",
    "confidence_below",
    default=None,
    type=float,
    help="Remove nodes with confidence below this threshold",
)
@click.option(
    "--source",
    "source_pattern",
    default=None,
    help="Remove nodes whose source_transcript contains this substring (e.g. 'live')",
)
@click.option(
    "--execute",
    is_flag=True,
    help="Actually delete matched nodes (default: preview only)",
)
@click.pass_context
def prune_cmd(ctx, project, older_than_days, confidence_below, source_pattern, execute):
    """Preview or remove graph nodes matching the given criteria.

    Runs in preview mode by default — prints what would be removed.
    Use --execute to actually delete. All criteria are ANDed.

    Examples:

    \b
      waystone prune myproject --source live
      waystone prune myproject --older-than 90 --confidence-below 0.5
      waystone prune myproject --source live --execute
    """
    if older_than_days is None and confidence_below is None and source_pattern is None:
        click.echo("Error: at least one filter (--older-than, --confidence-below, --source) is required.", err=True)
        ctx.exit(1)

    cfg = _load_cfg(ctx.obj["config_path"])
    db_path = get_db_path(cfg, project)
    store = GraphStore(db_path)

    ids = store.prune_nodes(
        older_than_days=older_than_days,
        confidence_below=confidence_below,
        source_pattern=source_pattern,
        dry_run=not execute,
    )

    if not ids:
        store.close()
        click.echo("No nodes matched the given criteria.")
        return

    if not execute:
        click.echo(f"Would remove {len(ids)} node(s):")
        for nid in ids:
            row = store.conn.execute(
                "SELECT fact, confidence, source_transcript FROM nodes WHERE id = ?", (nid,)
            ).fetchone()
            if row:
                fact_preview = (row["fact"][:77] + "...") if len(row["fact"]) > 80 else row["fact"]
                click.echo(f"  [{nid}] (conf={row['confidence']:.2f}, src={row['source_transcript']}) {fact_preview}")
        click.echo("\nRun with --execute to delete.")
    else:
        click.echo(f"Deleted {len(ids)} node(s) from '{project}'.")

    store.close()


@cli.command("vacuum")
@click.argument("project")
@click.option(
    "--min-age-days",
    default=90,
    show_default=True,
    type=int,
    help="Only prune nodes older than N days",
)
@click.option(
    "--max-confidence",
    default=0.75,
    show_default=True,
    type=float,
    help="Only prune nodes with confidence below this threshold",
)
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Preview what would be removed without deleting",
)
@click.pass_context
def vacuum_cmd(ctx, project, min_age_days, max_confidence, dry_run):
    """Remove stale nodes that have never been retrieved.

    Deletes nodes that satisfy ALL of:
      - Never returned by any query (hit_count = 0)
      - Older than --min-age-days
      - Confidence below --max-confidence
      - Not pinned

    Use --dry-run to preview without deleting.
    """
    cfg = _load_cfg(ctx.obj["config_path"])
    db_path = get_db_path(cfg, project)
    store = GraphStore(db_path)
    ids = store.vacuum_unused(min_age_days=min_age_days, max_confidence=max_confidence, dry_run=dry_run)
    store.close()

    if not ids:
        click.echo("No stale nodes found.")
        return

    if dry_run:
        click.echo(f"Would remove {len(ids)} stale node(s). Run without --dry-run to delete.")
    else:
        click.echo(f"Removed {len(ids)} stale node(s) from '{project}'.")


@cli.command("dedup")
@click.argument("project")
@click.option(
    "--threshold",
    default=0.92,
    show_default=True,
    type=float,
    help="Cosine similarity threshold above which two nodes are considered duplicates",
)
@click.option(
    "--top-k",
    default=5,
    show_default=True,
    type=int,
    help="Neighbors to check per node when scanning for duplicates",
)
@click.option(
    "--limit",
    default=0,
    show_default=True,
    type=int,
    help="Max nodes to scan per run (0 = all). Use to batch large graphs.",
)
@click.option(
    "--execute",
    is_flag=True,
    default=False,
    help="Actually merge duplicate nodes (default: preview only)",
)
@click.pass_context
def dedup_cmd(ctx, project, threshold, top_k, limit, execute):
    """Find and merge semantically duplicate nodes.

    Two nodes are considered duplicates when their embedding cosine similarity
    exceeds --threshold. The lower-confidence node is merged into the higher-
    confidence one: tags are unioned, confidence takes the max, and edges are
    rewritten to point at the surviving node.

    Runs in preview mode by default. Use --execute to actually merge.
    Requires sqlite-vec and sentence-transformers to be installed.

    Examples:

    \b
      waystone dedup myproject
      waystone dedup myproject --threshold 0.90 --execute
    """
    cfg = _load_cfg(ctx.obj["config_path"])
    db_path = get_db_path(cfg, project)
    store = GraphStore(db_path)

    if not store._vec_available:
        click.echo("sqlite-vec not available — semantic dedup requires sqlite-vec.", err=True)
        store.close()
        ctx.exit(1)

    click.echo("Embedding any unindexed nodes...")
    embedded = store.embed_missing_nodes()
    if embedded:
        click.echo(f"  Embedded {embedded} new node(s).")

    limit_str = f", limit={limit}" if limit else ""
    click.echo(f"Scanning for duplicates (threshold={threshold}, top_k={top_k}{limit_str})...")
    pairs = store.find_semantic_duplicates(threshold=threshold, top_k=top_k, limit=limit)

    if not pairs:
        store.close()
        click.echo("No semantic duplicates found.")
        return

    click.echo(f"Found {len(pairs)} duplicate pair(s):")
    for keep_id, drop_id, sim in pairs[:20]:
        keep_row = store.conn.execute("SELECT fact FROM nodes WHERE id = ?", (keep_id,)).fetchone()
        drop_row = store.conn.execute("SELECT fact FROM nodes WHERE id = ?", (drop_id,)).fetchone()
        if keep_row and drop_row:
            keep_fact = (keep_row[0][:60] + "...") if len(keep_row[0]) > 63 else keep_row[0]
            drop_fact = (drop_row[0][:60] + "...") if len(drop_row[0]) > 63 else drop_row[0]
            click.echo(f"  sim={sim:.3f}  KEEP [{keep_id}] {keep_fact}")
            click.echo(f"         DROP [{drop_id}] {drop_fact}")
    if len(pairs) > 20:
        click.echo(f"  ... and {len(pairs) - 20} more.")

    if not execute:
        click.echo(f"\nRun with --execute to merge {len(pairs)} pair(s).")
        store.close()
        return

    merged = sum(1 for keep_id, drop_id, _ in pairs if store.merge_node_into(keep_id, drop_id))
    store.close()
    click.echo(f"Merged {merged} duplicate node(s) from '{project}'.")


@cli.command("hook-init")
@click.argument("project")
@click.option(
    "--dir",
    "target_dir",
    default=".",
    help="Directory to mark (default: current directory)",
)
@click.pass_context
def hook_init_cmd(ctx, project, target_dir):
    """Mark a directory so the Claude Code hook auto-detects the project.

    Creates a .waystone file containing the project name. The hook
    reads this when determining which graph to query for context injection.

    Example:
        waystone hook-init myproject          # marks current directory
        waystone hook-init myproject --dir ~/code/myapp
    """
    marker = Path(target_dir).resolve() / ".waystone"
    if marker.exists():
        existing = marker.read_text().strip()
        click.echo(f"Already marked as project '{existing}'. Overwrite? [y/N] ", nl=False)
        if input().strip().lower() != "y":
            click.echo("Aborted.")
            return

    marker.write_text(project + "\n")
    click.echo(f"Marked {marker.parent} as project '{project}'")
    click.echo(f"The Claude Code hook will now query the '{project}' graph for context.")


@cli.command("extract-turn")
@click.argument("project")
@click.argument("turn_file")
@click.option("--context-k", default=None, type=int, help="Max context nodes to include (default: from config)")
@click.option("--context-hops", default=None, type=int, help="BFS hops for context retrieval (default: from config)")
@click.pass_context
def extract_turn_cmd(ctx, project, turn_file, context_k, context_hops):
    """Extract facts from a single conversation turn using existing graph as context.

    TURN_FILE: path to a file containing the turn text, or '-' for stdin.
    """
    config = _load_cfg(ctx.obj["config_path"])
    db_path = get_db_path(config, project)

    if not db_path.parent.exists():
        click.echo(f"Error: Project '{project}' not found. Run 'waystone init {project}' first.", err=True)
        sys.exit(1)

    inc_cfg = config.get("incremental", {})
    ctx_k = context_k if context_k is not None else inc_cfg.get("context_k", 30)
    ctx_hops = context_hops if context_hops is not None else inc_cfg.get("context_hops", 2)

    if turn_file == "-":
        turn_text = sys.stdin.read()
    else:
        p = Path(turn_file)
        if not p.exists():
            click.echo(f"Error: {turn_file} not found", err=True)
            sys.exit(1)
        turn_text = p.read_text()

    store = GraphStore(db_path)
    persisted_turns = store.load_buffer()
    store.close()

    inc_cfg = config.get("incremental", {})
    buffer = ExtractionBuffer(
        min_turns=inc_cfg.get("min_turns"),
        min_words=inc_cfg.get("min_words"),
        max_turns=inc_cfg.get("max_turns"),
        short_turn_words=inc_cfg.get("short_turn_words"),
    )
    for t in persisted_turns:
        buffer._turns.append(t)

    should_flush = buffer.add(turn_text)

    if should_flush:
        flushed_text = buffer.flush()
        click.echo(f"Buffer flushed ({len(persisted_turns) + 1} turns)...")

        store = GraphStore(db_path)
        keywords = extract_keywords(flushed_text)
        context_nodes = []
        if keywords:
            entry_nodes = store.get_nodes_by_tags(keywords)
            if entry_nodes:
                entry_nodes = score_by_relevance(entry_nodes, keywords)
                context_nodes = bfs_collect(store, entry_nodes, ctx_hops)
                context_nodes.sort(key=lambda n: n.get("_relevance", 0), reverse=True)
                context_nodes = context_nodes[:ctx_k]
        store.close()

        try:
            result = asyncio.run(extract_turn(flushed_text, context_nodes, config))
        except Exception as e:
            click.echo(f"Extraction failed: {e}", err=True)
            sys.exit(1)

        nodes = result["nodes"]
        edges = result["edges"]
        existing_ids = {n["id"] for n in context_nodes}

        for node in nodes:
            node.setdefault("source_transcript", "turn")
            node.setdefault("created_at", datetime.now(timezone.utc).isoformat())

        store = GraphStore(db_path)
        store.merge_extraction(nodes, edges)
        store.clear_buffer()
        store.close()

        cross_turn = sum(
            1 for e in edges if e["from_id"] in existing_ids or e["to_id"] in existing_ids
        )
        click.echo(
            f"Extracted {len(nodes)} nodes, {len(edges)} edges "
            f"({cross_turn} cross-turn references)"
        )
    else:
        store = GraphStore(db_path)
        store.save_buffer(buffer._turns)
        store.close()

        click.echo(
            f"Buffered {buffer.buffered_turns} turns "
            f"({buffer.buffered_words} words total) — waiting for more"
        )


@cli.command("extract-replay")
@click.argument("project")
@click.argument("transcript_file", type=click.Path(exists=True))
@click.option("--turn-size", default=2, type=int, help="Messages per turn (default: 2)")
@click.option("--context-k", default=None, type=int, help="Max context nodes per turn")
@click.option("--context-hops", default=None, type=int, help="BFS hops for context")
@click.pass_context
def extract_replay_cmd(ctx, project, transcript_file, turn_size, context_k, context_hops):
    """Replay a transcript turn-by-turn through incremental extraction.

    Splits the transcript into turns of TURN_SIZE messages, then processes
    each turn sequentially so each one benefits from the accumulated graph.
    """
    config = _load_cfg(ctx.obj["config_path"])
    db_path = get_db_path(config, project)

    if not db_path.parent.exists():
        click.echo(f"Error: Project '{project}' not found. Run 'waystone init {project}' first.", err=True)
        sys.exit(1)

    inc_cfg = config.get("incremental", {})
    ctx_k = context_k if context_k is not None else inc_cfg.get("context_k", 30)
    ctx_hops = context_hops if context_hops is not None else inc_cfg.get("context_hops", 2)

    transcript_path = Path(transcript_file)
    transcript_text = transcript_path.read_text()

    turns = split_transcript_into_turns(transcript_text, turn_size)
    click.echo(f"Split transcript into {len(turns)} turns (turn_size={turn_size})")

    inc_cfg = config.get("incremental", {})
    buffer = ExtractionBuffer(
        min_turns=inc_cfg.get("min_turns"),
        min_words=inc_cfg.get("min_words"),
        max_turns=inc_cfg.get("max_turns"),
        short_turn_words=inc_cfg.get("short_turn_words"),
    )
    total_nodes = 0
    total_edges = 0
    total_cross_turn = 0
    total_ctx_nodes = 0
    llm_calls = 0

    for i, turn_text in enumerate(turns, 1):
        should_flush = buffer.add(turn_text)

        if not should_flush:
            click.echo(f"  Turn {i}/{len(turns)} buffered ({buffer.buffered_turns} turns, {buffer.buffered_words} words)")
            continue

        flushed_text = buffer.flush()

        store = GraphStore(db_path)
        keywords = extract_keywords(flushed_text)
        context_nodes = []
        if keywords:
            entry_nodes = store.get_nodes_by_tags(keywords)
            if entry_nodes:
                entry_nodes = score_by_relevance(entry_nodes, keywords)
                context_nodes = bfs_collect(store, entry_nodes, ctx_hops)
                context_nodes.sort(key=lambda n: n.get("_relevance", 0), reverse=True)
                context_nodes = context_nodes[:ctx_k]
        store.close()

        existing_ids = {n["id"] for n in context_nodes}
        total_ctx_nodes += len(context_nodes)

        click.echo(f"  Turns {i-buffer.MAX_TURNS+1}-{i} (flushed, {len(context_nodes)} ctx)...", nl=False)
        t0 = time.time()

        try:
            result = asyncio.run(extract_turn(flushed_text, context_nodes, config))
        except Exception as e:
            click.echo(f" FAILED: {e}")
            continue

        elapsed = time.time() - t0
        nodes = result["nodes"]
        edges = result["edges"]
        llm_calls += 1

        for node in nodes:
            node["source_transcript"] = transcript_path.name
            node.setdefault("created_at", datetime.now(timezone.utc).isoformat())

        cross_turn = sum(
            1 for e in edges if e["from_id"] in existing_ids or e["to_id"] in existing_ids
        )

        store = GraphStore(db_path)
        store.merge_extraction(nodes, edges)
        store.close()

        total_nodes += len(nodes)
        total_edges += len(edges)
        total_cross_turn += cross_turn

        click.echo(f" {len(nodes)} nodes, {len(edges)} edges, {cross_turn} cross-turn [{elapsed:.1f}s]")

    remaining = buffer.flush_if_nonempty()
    if remaining:
        store = GraphStore(db_path)
        keywords = extract_keywords(remaining)
        context_nodes = []
        if keywords:
            entry_nodes = store.get_nodes_by_tags(keywords)
            if entry_nodes:
                entry_nodes = score_by_relevance(entry_nodes, keywords)
                context_nodes = bfs_collect(store, entry_nodes, ctx_hops)
                context_nodes.sort(key=lambda n: n.get("_relevance", 0), reverse=True)
                context_nodes = context_nodes[:ctx_k]
        store.close()

        existing_ids = {n["id"] for n in context_nodes}
        total_ctx_nodes += len(context_nodes)

        click.echo(f"  Final turns (flushed remainder, {len(context_nodes)} ctx)...", nl=False)
        t0 = time.time()

        try:
            result = asyncio.run(extract_turn(remaining, context_nodes, config))
        except Exception as e:
            click.echo(f" FAILED: {e}")
        else:
            elapsed = time.time() - t0
            nodes = result["nodes"]
            edges = result["edges"]
            llm_calls += 1

            for node in nodes:
                node["source_transcript"] = transcript_path.name
                node.setdefault("created_at", datetime.now(timezone.utc).isoformat())

            cross_turn = sum(
                1 for e in edges if e["from_id"] in existing_ids or e["to_id"] in existing_ids
            )

            store = GraphStore(db_path)
            store.merge_extraction(nodes, edges)
            store.close()

            total_nodes += len(nodes)
            total_edges += len(edges)
            total_cross_turn += cross_turn

            click.echo(f" {len(nodes)} nodes, {len(edges)} edges, {cross_turn} cross-turn [{elapsed:.1f}s]")

    dest = get_project_dir(config, project) / "transcripts" / transcript_path.name
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not dest.exists():
        dest.write_text(transcript_text)

    avg_ctx = total_ctx_nodes / llm_calls if llm_calls else 0
    click.echo(f"\nTotal: {total_nodes} nodes, {total_edges} edges from {llm_calls} LLM calls on {len(turns)} turns")
    click.echo(f"Cross-turn edges: {total_cross_turn}")
    click.echo(f"Avg context nodes/call: {avg_ctx:.1f}")


@cli.command("last-context")
@click.option("--raw", is_flag=True, help="Print raw markdown without paging")
@click.pass_context
def last_context_cmd(ctx, raw):
    """Show the context most recently injected by the Claude Code hook.

    The hook writes last_context.md per project. Auto-detects the current
    project from a .waystone marker file.
    Also prints the retrieval metrics from the last hook invocation.
    """
    import json as _json
    import time as _time

    state_dir = Path.home() / ".waystone"
    state_path = state_dir / "state.json"

    # Detect project from CWD to find per-project last_context.md
    def _detect_project_from_cwd() -> str | None:
        cwd_path = Path.cwd().resolve()
        home = Path.home()
        for directory in [cwd_path, *cwd_path.parents]:
            marker = directory / ".waystone"
            if marker.exists():
                try:
                    name = marker.read_text().strip()
                    if name:
                        return name
                except Exception:
                    pass
            if directory == home:
                break
        return None

    cfg = _load_cfg(ctx.obj.get("config") if ctx.obj else None)
    project_name = _detect_project_from_cwd()
    context_path = None
    if project_name:
        candidate = get_db_path(cfg, project_name).parent / "last_context.md"
        if candidate.exists():
            context_path = candidate
    # Fall back to legacy global path
    if context_path is None:
        legacy = state_dir / "last_context.md"
        if legacy.exists():
            context_path = legacy

    if context_path is None:
        click.echo(
            "No hook context found yet.\n"
            "Install the hook with:  python hooks/install.py\n"
            "Then trigger a Claude Code session.",
            err=True,
        )
        sys.exit(1)

    # Show metrics from last retrieval
    if state_path.exists():
        try:
            state = _json.loads(state_path.read_text())
            age_s = _time.time() - state.get("timestamp", 0)
            age_str = f"{int(age_s)}s ago" if age_s < 3600 else f"{int(age_s/3600)}h ago"
            project = state.get("project", "?")
            status = state.get("status", "?")
            if status == "ok":
                click.echo(
                    f"Last retrieval ({age_str}):  project={project}  "
                    f"{state.get('nodes_retrieved')}/{state.get('nodes_total')} nodes  "
                    f"~{state.get('tokens_injected')} tok injected  "
                    f"+{state.get('tokens_filtered')} tok filtered  "
                    f"[{state.get('elapsed_ms')}ms]"
                )
            else:
                click.echo(f"Last hook status ({age_str}): {status} (project={project})")
        except Exception:
            pass

    click.echo()

    content = context_path.read_text()
    if raw or not sys.stdout.isatty():
        click.echo(content)
    else:
        click.echo_via_pager(content)


@cli.command("serve")
@click.option("--host", default="0.0.0.0", show_default=True, help="Bind address")
@click.option("--port", default=8000, show_default=True, type=int, help="Listen port")
@click.option("--reload", is_flag=True, help="Auto-reload on code changes (dev mode)")
@click.pass_context
def serve_cmd(ctx, host, port, reload):
    """Start the Waystone HTTP API server.

    \b
    Clients configure api_url in config.yaml to route requests here:
        api_url: http://localhost:8000
        api_key: my-secret   # optional; set WAYSTONE_API_KEY on server to require it

    Requires the 'api' extra:  pip install 'waystone[api]'
    """
    try:
        import uvicorn
    except ImportError:
        click.echo(
            "Error: uvicorn not installed.\n"
            "Install with:  pip install 'waystone[api]'",
            err=True,
        )
        sys.exit(1)

    click.echo(f"Waystone API → http://{host}:{port}  (docs: /docs)")
    uvicorn.run(
        "waystone.api_server:app",
        host=host,
        port=port,
        reload=reload,
    )


@cli.command("mcp-serve")
@click.option(
    "--transport",
    default="stdio",
    type=click.Choice(["stdio", "sse"]),
    show_default=True,
    help="Transport protocol: 'stdio' for Claude Code, 'sse' for HTTP clients",
)
def mcp_serve_cmd(transport):
    """Start the Waystone MCP server.

    \b
    For Claude Code, add to ~/.claude/claude_desktop_config.json:
      {
        "mcpServers": {
          "waystone": {
            "command": "waystone",
            "args": ["mcp-serve"]
          }
        }
      }

    The server exposes four tools:
      waystone_query        — retrieve context for a task
      waystone_extract      — extract facts from text into graph
      waystone_stats        — show graph node/edge counts
      waystone_list_projects — list all available projects
    """
    from .mcp_server import run_server
    run_server(transport=transport)


def _jsonl_to_markdown(jsonl_path: Path) -> str:
    """Convert a Claude Code .jsonl session file to markdown transcript format."""
    import json as _json

    lines = []
    for raw in jsonl_path.read_text(encoding="utf-8", errors="replace").splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            msg = _json.loads(raw)
        except Exception:
            continue

        role = msg.get("role", "")
        content = msg.get("content", "")

        # content can be a list of content blocks (Claude API format)
        if isinstance(content, list):
            text_parts = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    text_parts.append(block["text"])
                elif isinstance(block, str):
                    text_parts.append(block)
            content = "\n".join(text_parts)

        if not content or not isinstance(content, str):
            continue

        if role == "user":
            lines.append(f"**Human:** {content}\n")
        elif role == "assistant":
            lines.append(f"**Assistant:** {content}\n")

    return "\n".join(lines)


def _find_claude_sessions(project_hint: str | None) -> list[dict]:
    """Find recent Claude Code .jsonl session files under ~/.claude/projects/.

    Returns a list of dicts with keys: path, chars, mtime, project_dir.
    """
    sessions_root = Path.home() / ".claude" / "projects"
    if not sessions_root.exists():
        return []

    results = []
    for project_dir in sorted(sessions_root.iterdir()):
        if not project_dir.is_dir():
            continue
        for jsonl in sorted(project_dir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True):
            try:
                stat = jsonl.stat()
                results.append({
                    "path": jsonl,
                    "chars": stat.st_size,
                    "mtime": stat.st_mtime,
                    "project_dir": project_dir.name,
                })
            except OSError:
                continue

    # Sort newest first
    results.sort(key=lambda r: r["mtime"], reverse=True)
    return results


@cli.command("onboard")
@click.argument("project", required=False)
@click.option("--limit", default=10, show_default=True, type=int,
              help="Max sessions to show in menu")
@click.option("--verify", is_flag=True, help="Run verification pass on each import")
@click.option("--chunk-size", default=30000, show_default=True, type=int, metavar="CHARS",
              help="Auto-split sessions larger than this many characters")
@click.option("--timeout", type=float, default=None,
              help="LLM timeout in seconds per chunk (overrides config)")
@click.pass_context
def onboard_cmd(ctx, project, limit, verify, chunk_size, timeout):
    """One-click import of recent Claude Code sessions into a project graph.

    Discovers .jsonl session files from ~/.claude/projects/, lets you select
    which to import, extracts facts from each, then shows a sample query.

    \b
    Example:
        waystone onboard myproject
        waystone onboard myproject --verify --limit 5
    """
    config = _load_cfg(ctx.obj["config_path"])

    if timeout is not None:
        config = dict(config)
        config["llm"] = dict(config.get("llm", {}))
        config["llm"]["timeout"] = timeout

    # Auto-detect project from .waystone marker if not given
    if not project:
        marker = Path.cwd() / ".waystone"
        if marker.exists():
            project = marker.read_text().strip()
        if not project:
            click.echo("Error: specify a project name or run 'waystone hook-init <project>' first.", err=True)
            sys.exit(1)

    # Ensure project exists
    db_path = get_db_path(config, project)
    if not db_path.parent.exists():
        click.echo(f"Project '{project}' not found — initializing it now...")
        project_dir = get_project_dir(config, project)
        project_dir.mkdir(parents=True, exist_ok=True)
        (project_dir / "transcripts").mkdir(exist_ok=True)
        (project_dir / "exports").mkdir(exist_ok=True)
        store = GraphStore(db_path)
        store.close()
        click.echo(f"Initialized project '{project}'.")

    # Discover sessions
    sessions = _find_claude_sessions(project)
    if not sessions:
        click.echo(
            "No Claude Code session files found in ~/.claude/projects/.\n"
            "Sessions appear after you've used Claude Code at least once.",
            err=True,
        )
        sys.exit(1)

    sessions = sessions[:limit]

    # Present menu
    from datetime import datetime as _dt
    click.echo(f"\nFound {len(sessions)} recent Claude Code session(s):\n")
    for i, s in enumerate(sessions, 1):
        ts = _dt.fromtimestamp(s["mtime"]).strftime("%Y-%m-%d %H:%M")
        size_k = s["chars"] // 1024
        click.echo(f"  [{i:2d}] {ts}  {size_k:>5}KB  {s['project_dir']}/{s['path'].name}")

    click.echo()
    selection = click.prompt(
        "Import which sessions? (e.g. 1,3-5 or 'all' or Enter to import all)",
        default="all",
    )

    # Parse selection
    indices: list[int] = []
    if selection.strip().lower() in ("all", ""):
        indices = list(range(len(sessions)))
    else:
        for part in selection.split(","):
            part = part.strip()
            if "-" in part:
                lo, hi = part.split("-", 1)
                indices.extend(range(int(lo) - 1, int(hi)))
            elif part.isdigit():
                indices.append(int(part) - 1)
        indices = sorted(set(i for i in indices if 0 <= i < len(sessions)))

    if not indices:
        click.echo("No sessions selected. Exiting.")
        return

    selected = [sessions[i] for i in indices]
    click.echo(f"\nImporting {len(selected)} session(s) into project '{project}'...\n")

    total_nodes = 0
    total_edges = 0

    for s in selected:
        path: Path = s["path"]
        click.echo(f"  {path.name} ({s['chars'] // 1024}KB)... ", nl=False)

        try:
            text = _jsonl_to_markdown(path)
        except Exception as e:
            click.echo(f"SKIP (read error: {e})")
            continue

        if not text.strip():
            click.echo("SKIP (empty after conversion)")
            continue

        chunks = split_into_chunks(text, chunk_size) if len(text) > chunk_size else [text]
        chunk_label = f" ({len(chunks)} chunks)" if len(chunks) > 1 else ""
        click.echo(f"converting{chunk_label}... ", nl=False)

        session_nodes: list[dict] = []
        session_edges: list[dict] = []
        source_name = f"claude_session:{path.stem}"

        for chunk_text in chunks:
            try:
                result = asyncio.run(extract(chunk_text, config))
            except Exception as e:
                msg = str(e) or repr(e)
                click.echo(f"CHUNK FAILED ({type(e).__name__}): {msg}", err=True)
                continue

            nodes = result["nodes"]
            edges = result["edges"]

            if verify:
                try:
                    v = asyncio.run(verify_extraction(chunk_text, nodes, config))
                    nodes = nodes + v["nodes"]
                    edges = edges + v["edges"]
                except Exception:
                    pass

            session_nodes.extend(nodes)
            session_edges.extend(edges)

        now = datetime.now(timezone.utc).isoformat()
        for node in session_nodes:
            node["source_transcript"] = source_name
            node.setdefault("created_at", now)

        store = GraphStore(db_path)
        store.merge_extraction(session_nodes, session_edges)
        store.close()

        total_nodes += len(session_nodes)
        total_edges += len(session_edges)
        click.echo(f"{len(session_nodes)} nodes, {len(session_edges)} edges")

    click.echo(f"\nDone. Imported {total_nodes} nodes and {total_edges} edges into '{project}'.\n")

    # Show value immediately — sample query
    store = GraphStore(db_path)
    stats = store.get_stats()
    store.close()

    if stats["node_count"] == 0:
        click.echo("Graph is empty after import — check for extraction errors above.")
        return

    click.echo(f"Graph now has {stats['node_count']} nodes, {stats['edge_count']} edges.")
    click.echo("\nSample query: \"what are the key decisions and constraints?\"\n")
    click.echo("─" * 60)

    from .retriever import retrieve_with_stats as _retrieve
    store = GraphStore(db_path)
    strategies = dict(config.get("strategies", {}))
    result = _retrieve(store, "key decisions and constraints", hops=3, top_k=15, strategies=strategies)
    store.close()
    click.echo(result.markdown)
    click.echo("─" * 60)
    click.echo(f"\nRun 'waystone query {project} \"<your task>\"' to retrieve context anytime.")


@cli.command("import-claude-sessions")
@click.argument("project")
@click.argument("session_files", nargs=-1, type=click.Path(exists=True))
@click.option("--verify", is_flag=True, help="Run verification pass")
@click.option("--chunk-size", default=30000, show_default=True, type=int, metavar="CHARS")
@click.option("--timeout", type=float, default=None)
@click.option("--list-only", is_flag=True, help="List discovered sessions without importing")
@click.pass_context
def import_sessions_cmd(ctx, project, session_files, verify, chunk_size, timeout, list_only):
    """Import one or more Claude Code .jsonl session files into the project graph.

    If no files are given, discovers all sessions under ~/.claude/projects/.

    \b
    Examples:
        waystone import-claude-sessions myproject ~/.claude/projects/abc123/session.jsonl
        waystone import-claude-sessions myproject --list-only
    """
    config = _load_cfg(ctx.obj["config_path"])

    if timeout is not None:
        config = dict(config)
        config["llm"] = dict(config.get("llm", {}))
        config["llm"]["timeout"] = timeout

    db_path = get_db_path(config, project)
    if not db_path.parent.exists():
        click.echo(f"Error: Project '{project}' not found. Run 'waystone init {project}' first.", err=True)
        sys.exit(1)

    if session_files:
        paths = [Path(f) for f in session_files]
    else:
        discovered = _find_claude_sessions(project)
        paths = [s["path"] for s in discovered]

    if not paths:
        click.echo("No session files found.")
        return

    if list_only:
        click.echo(f"Found {len(paths)} session file(s):")
        for p in paths:
            try:
                size_k = p.stat().st_size // 1024
                click.echo(f"  {p}  ({size_k}KB)")
            except OSError:
                click.echo(f"  {p}")
        return

    total_nodes = 0
    total_edges = 0

    for path in paths:
        click.echo(f"  {path.name}... ", nl=False)
        try:
            text = _jsonl_to_markdown(path)
        except Exception as e:
            click.echo(f"SKIP ({e})")
            continue

        if not text.strip():
            click.echo("SKIP (empty)")
            continue

        chunks = split_into_chunks(text, chunk_size) if len(text) > chunk_size else [text]
        session_nodes: list[dict] = []
        session_edges: list[dict] = []
        source_name = f"claude_session:{path.stem}"

        for chunk_text in chunks:
            try:
                result = asyncio.run(extract(chunk_text, config))
            except Exception as e:
                msg = str(e) or repr(e)
                click.echo(f"FAILED ({type(e).__name__}): {msg}", err=True)
                continue
            nodes = result["nodes"]
            edges = result["edges"]
            if verify:
                try:
                    v = asyncio.run(verify_extraction(chunk_text, nodes, config))
                    nodes = nodes + v["nodes"]
                    edges = edges + v["edges"]
                except Exception:
                    pass
            session_nodes.extend(nodes)
            session_edges.extend(edges)

        now = datetime.now(timezone.utc).isoformat()
        for node in session_nodes:
            node["source_transcript"] = source_name
            node.setdefault("created_at", now)

        store = GraphStore(db_path)
        store.merge_extraction(session_nodes, session_edges)
        store.close()

        total_nodes += len(session_nodes)
        total_edges += len(session_edges)
        click.echo(f"{len(session_nodes)} nodes, {len(session_edges)} edges")

    click.echo(f"\nImported {total_nodes} nodes and {total_edges} edges into '{project}'.")


@cli.command("doctor")
@click.option("--fix", "do_fix", is_flag=True, default=False,
              help="Attempt to automatically fix detected issues.")
@click.pass_context
def doctor_cmd(ctx, do_fix):
    """Run a preflight check: config, LLM reachability, project marker, MCP.

    Prints a checklist of what's working and what needs attention.
    Pass --fix to automatically resolve common issues (register MCP, install hooks,
    seed the graph, save a corrected API key).

    \b
        waystone doctor          # check only
        waystone doctor --fix    # check + fix
    """
    import os as _os

    config_path = ctx.obj["config_path"]
    ok = True
    # State gathered during checks — used by --fix
    marker_project: str | None = None
    has_nodes = False
    has_submit = False
    mcp_registered = False
    llm_auth_failed = False
    llm_cfg: dict = {}

    def _check(label: str, passed: bool, detail: str = ""):
        nonlocal ok
        icon = "✓" if passed else "✗"
        msg = f"  {icon}  {label}"
        if detail:
            msg += f"  ({detail})"
        click.echo(msg)
        if not passed:
            ok = False

    click.echo("Waystone — Doctor\n")

    # --- Config file ---
    try:
        config = _load_cfg(config_path)
        cfg_path = config_path or "~/.waystone/config.yaml or ./config.yaml"
        _check("Config file loaded", True, cfg_path)
    except SystemExit:
        _check("Config file loaded", False, "run 'waystone --help' for config path options")
        click.echo("\nCannot continue without a valid config.")
        sys.exit(1)

    # --- API key ---
    llm_cfg = config.get("llm", {})
    has_key = bool(
        llm_cfg.get("api_key")
        or (llm_cfg.get("api_key_env") and _os.environ.get(llm_cfg["api_key_env"]))
        or _os.environ.get("CTX_API_KEY")
        or _os.environ.get("OPENAI_API_KEY")
    )
    _check(
        "API key configured",
        has_key,
        "set CTX_API_KEY or add api_key to config.yaml" if not has_key else "",
    )

    # --- LLM endpoint reachability ---
    base_url = llm_cfg.get("base_url", "http://localhost:1234/v1")
    llm_auth_failed = False
    from .setup import test_llm_connection as _test_llm
    _llm_ok, _llm_msg = _test_llm(
        base_url,
        api_key=llm_cfg.get("api_key"),
        api_key_env=llm_cfg.get("api_key_env"),
    )
    if not _llm_ok and any(word in _llm_msg for word in ("invalid", "rejected", "auth")):
        llm_auth_failed = True
    _check("LLM endpoint reachable", _llm_ok, _llm_msg if not _llm_ok else "")

    # --- MCP package ---
    try:
        import mcp  # noqa: F401
        _check("mcp package installed", True)
    except ImportError:
        _check("mcp package installed", False, "pip install 'mcp>=1.0'")

    # --- Project marker in CWD tree ---
    marker_found = False
    marker_project = None
    path = Path.cwd().resolve()
    for candidate in [path, *path.parents]:
        marker = candidate / ".waystone"
        if marker.is_file():
            marker_found = True
            marker_project = marker.read_text().strip()
            break
    _check(
        ".waystone marker found",
        marker_found,
        f"project='{marker_project}'" if marker_found else "run 'waystone hook-init <project>' to create one",
    )

    # --- Project graph exists ---
    if marker_project:
        db_path = get_db_path(config, marker_project)
        db_exists = db_path.exists()
        _check(
            f"Graph DB exists for '{marker_project}'",
            db_exists,
            str(db_path) if db_exists else f"run 'waystone init {marker_project}' or 'waystone onboard {marker_project}'",
        )
        if db_exists:
            store = GraphStore(db_path)
            stats = store.get_stats()
            store.close()
            has_nodes = stats["node_count"] > 0
            if has_nodes:
                _check(f"Graph populated ({stats['node_count']} nodes)", True)
            else:
                # Empty graph is normal on first install — informational, not a failure
                click.echo(
                    f"  –  Graph is empty  "
                    f"(run 'waystone onboard {marker_project}' to import past sessions)"
                )

    # --- Claude Code integration ---
    settings_path = Path.home() / ".claude" / "settings.json"
    mcp_config_path = Path.home() / ".claude" / "claude_desktop_config.json"

    # Read the integration mode saved by `waystone configure`
    _int_mode = config.get("integration_mode", None)   # mcp | hooks | both | skip | None
    wants_mcp   = _int_mode in (None, "mcp", "both")
    wants_hooks = _int_mode in (None, "hooks", "both")

    # Detect MCP registration (check both claude_desktop_config.json and settings.json)
    mcp_registered = False
    import json as _json
    for _mcp_path in [mcp_config_path, settings_path]:
        if _mcp_path.exists():
            try:
                _cfg = _json.loads(_mcp_path.read_text())
                if "waystone" in _cfg.get("mcpServers", {}):
                    mcp_registered = True
                    break
            except Exception:
                pass

    if _int_mode == "hooks":
        click.echo("  –  MCP server (not selected — hooks-only mode)")
    elif _int_mode == "skip":
        click.echo("  –  MCP server (skipped during configure)")
    else:
        _check(
            "MCP server registered",
            mcp_registered,
            "" if mcp_registered else "run 'waystone configure' and choose MCP, or: claude mcp add waystone waystone mcp-serve",
        )

    # Detect hooks
    if settings_path.exists():
        try:
            settings = _json.loads(settings_path.read_text())
            hooks = settings.get("hooks", {})
            has_submit = any(
                "waystone" in str(h)
                for h in hooks.get("UserPromptSubmit", [])
            )
            has_stop = any(
                "waystone" in str(h)
                for h in hooks.get("Stop", [])
            )
        except Exception:
            has_submit = has_stop = False
            _check("Claude Code settings readable", False, str(settings_path))
    else:
        has_submit = has_stop = False
        _check("Claude Code settings found", False,
               f"{settings_path} missing — Claude Code not installed or not yet run")

    if _int_mode == "mcp":
        # Hooks are optional — show as info
        click.echo(f"  {'✓' if has_submit else '–'}  UserPromptSubmit hook"
                   f"{'  (active)' if has_submit else '  (optional — MCP-only mode)'}")
        click.echo(f"  {'✓' if has_stop else '–'}  Stop hook (transcript recording)"
                   f"{'  (active)' if has_stop else '  (optional — MCP-only mode)'}")
    elif _int_mode == "skip":
        click.echo("  –  Hooks (skipped during configure)")
    else:
        _check("UserPromptSubmit hook installed", has_submit,
               "" if has_submit else "run 'waystone configure' and choose Hooks")
        _check("Stop hook installed", has_stop,
               "" if has_stop else "run 'waystone configure' and choose Hooks")

    # --- Additional tools (Antigravity, Codex) ---
    _extra_tools = config.get("integration_tools", []) or []

    if "antigravity" in _extra_tools:
        from .setup import ANTIGRAVITY_SETTINGS_PATH
        _ag_has_hooks = False
        if ANTIGRAVITY_SETTINGS_PATH.exists():
            try:
                _ag_cfg = _json.loads(ANTIGRAVITY_SETTINGS_PATH.read_text())
                _ag_hooks = _ag_cfg.get("hooks", {})
                _ag_has_hooks = any(
                    "waystone" in str(h)
                    for h in _ag_hooks.get("UserPromptSubmit", [])
                )
            except Exception:
                pass
        _check("Antigravity UserPromptSubmit hook", _ag_has_hooks,
               "" if _ag_has_hooks else "run 'waystone configure' to reinstall Antigravity hooks")

    if "codex" in _extra_tools:
        from .setup import CODEX_HOOKS_PATH
        _cx_has_hooks = CODEX_HOOKS_PATH.exists() and "waystone" in CODEX_HOOKS_PATH.read_text()
        _check("Codex CLI hook", _cx_has_hooks,
               "" if _cx_has_hooks else "run 'waystone configure' to reinstall Codex hooks")

    if "openhands" in _extra_tools:
        from .setup import OPENHANDS_HOOKS_PATH
        _oh_has_hooks = OPENHANDS_HOOKS_PATH.exists() and "waystone" in OPENHANDS_HOOKS_PATH.read_text()
        _check("OpenHands hook", _oh_has_hooks,
               "" if _oh_has_hooks else "run 'waystone configure' to reinstall OpenHands hooks")

    if "opencode" in _extra_tools:
        from .setup import OPENCODE_PLUGIN_PATH
        _check("OpenCode plugin installed", OPENCODE_PLUGIN_PATH.exists(),
               "" if OPENCODE_PLUGIN_PATH.exists() else "run 'waystone configure' to reinstall OpenCode plugin")

    click.echo()
    if ok:
        click.echo("All checks passed. Waystone is ready.")
        return

    # ------------------------------------------------------------------ --fix
    if not do_fix:
        click.echo("Some checks failed. Run 'waystone doctor --fix' to auto-fix what's possible.")
        sys.exit(1)

    # Attempt auto-fixes for common issues
    click.echo("Attempting auto-fix...\n")
    fixed_any = False

    from .setup import (
        install_claude_md,
        install_hooks,
        register_mcp_server,
        test_llm_connection as _test_llm,
        write_llm_config,
    )

    # Fix: API key 403
    if llm_auth_failed:
        api_key_env = llm_cfg.get("api_key_env", "OPENAI_API_KEY")
        click.echo(f"  API key fix — your {api_key_env} is missing or invalid.")
        click.echo("  (Your key will not be shown as you type — this is normal)")
        new_key = click.prompt(f"  Enter your {api_key_env}", hide_input=True, default="", prompt_suffix=" (leave blank to skip): ").strip()
        if new_key:
            masked = new_key[:4] + "..." + new_key[-4:] if len(new_key) > 8 else "****"
            click.echo(f"  ✓  Key received ({masked})")
            # Test the new key before saving
            click.echo("  Testing...", nl=False)
            test_ok, test_msg = _test_llm(llm_cfg.get("base_url", ""), api_key=new_key)
            if test_ok:
                click.echo(f"\r  ✓  {test_msg}          ")
            else:
                click.echo(f"\r  ✗  {test_msg}")
                if not click.confirm("  Key may be invalid. Save it anyway?", default=False):
                    new_key = ""
        if new_key:
            write_llm_config(
                base_url=llm_cfg.get("base_url", ""),
                model=llm_cfg.get("model", ""),
                api_key_env=api_key_env,
                api_key=new_key,
            )
            click.echo(f"  ✓  API key saved to ~/.waystone/config.yaml")
            fixed_any = True
        else:
            click.echo(f"  –  Skipped. Set {api_key_env} in your shell or add api_key to ~/.waystone/config.yaml")

    # Fix: MCP not registered
    if not mcp_registered:
        click.echo("  MCP fix — registering waystone MCP server...")
        mcp_ok, mcp_msg = register_mcp_server()
        prefix = "  ✓ " if mcp_ok else "  ✗ "
        for line in mcp_msg.splitlines():
            click.echo(f"{prefix}{line}")
            prefix = "    "
        if mcp_ok:
            fixed_any = True

    # Fix: hooks not installed (only when MCP is also absent or user wants both)
    if not mcp_registered and not has_submit:
        click.echo("  Hooks fix — installing Claude Code hooks...")
        try:
            added, skipped = install_hooks()
            for label in added:
                click.echo(f"  ✓  {label} added")
            if install_claude_md():
                click.echo("  ✓  Waystone section appended to ~/.claude/CLAUDE.md")
            fixed_any = True
        except Exception as e:
            click.echo(f"  ✗  Could not install hooks: {e}")

    # Fix: 0 nodes — offer to onboard
    if marker_project and not has_nodes:
        click.echo(f"  Graph fix — '{marker_project}' graph is empty.")
        if click.confirm(f"  Run 'waystone onboard {marker_project}' now?", default=True):
            import subprocess as _sp
            result = _sp.run([sys.executable, "-m", "waystone.cli", "onboard", marker_project])
            fixed_any = result.returncode == 0

    click.echo()
    if fixed_any:
        click.echo("Fixes applied. Run 'waystone doctor' to verify.")
    else:
        click.echo("Nothing auto-fixed. Check the items above manually.")
    sys.exit(0 if fixed_any else 1)


@cli.command("configure")
@click.option("--non-interactive", is_flag=True, hidden=True,
              help="Skip prompts (used in tests)")
def configure_cmd(non_interactive):
    """Interactive setup wizard: configure LLM, Claude Code integration, and a project.

    Run this once after 'pip install waystone':

    \b
        waystone configure

    The wizard walks you through:
      1. LLM provider — picks base URL, model, and API key
      2. Claude Code integration — MCP server (recommended) or hooks
      3. Project marker — optionally marks the current directory for tracking
    """
    import os as _os

    from .setup import (
        PROVIDERS,
        WAYSTONE_CONFIG_PATH,
        install_antigravity_hooks,
        install_claude_md,
        install_codex_hooks,
        install_hooks,
        install_opencode_plugin,
        install_openhands_hooks,
        install_slash_commands,
        register_antigravity_mcp,
        register_codex_mcp,
        register_mcp_server,
        save_integration_mode,
        write_llm_config,
    )

    # ------------------------------------------------------------------ header
    click.echo()
    click.echo("Waystone Setup Wizard")
    click.echo("=" * 50)
    click.echo("This wizard configures Waystone in about 2 minutes.")
    click.echo("Press Ctrl-C at any time to quit without saving.\n")
    click.echo("  Important: Waystone needs an external LLM API to extract facts from")
    click.echo("  your transcripts. You'll need an API key from a supported provider")
    click.echo("  (Gemini, OpenAI, Anthropic, or a local model like LM Studio).")
    click.echo("  Retrieval — the per-prompt context injection — is fully local (SQLite,")
    click.echo("  no API calls). Only extraction uses the LLM.\n")

    # --------------------------------------------------------- Step 1: LLM
    click.echo("Step 1 of 3 — LLM for Extraction")
    click.echo("-" * 35)

    provider_keys = list(PROVIDERS.keys())
    for i, key in enumerate(provider_keys, 1):
        click.echo(f"  [{i}] {PROVIDERS[key]['label']}")

    default_choice = "1"
    if non_interactive:
        choice_str = default_choice
    else:
        choice_str = click.prompt("\nProvider", default=default_choice)

    try:
        provider_key = provider_keys[int(choice_str) - 1]
    except (ValueError, IndexError):
        click.echo("Invalid choice — defaulting to Gemini.", err=True)
        provider_key = "gemini"

    prov = PROVIDERS[provider_key]

    # Model
    if prov["models"]:
        click.echo(f"\n  Available models for {provider_key}:")
        for j, m in enumerate(prov["models"], 1):
            suffix = " (recommended)" if j == 1 else ""
            click.echo(f"    [{j}] {m}{suffix}")
        if non_interactive:
            model = prov["default_model"]
        else:
            model = click.prompt("  Model", default=prov["default_model"])
            # accept numeric shortcut
            try:
                idx = int(model) - 1
                if 0 <= idx < len(prov["models"]):
                    model = prov["models"][idx]
            except ValueError:
                pass
    else:
        if non_interactive:
            model = prov["default_model"] or "my-local-model"
        else:
            model = click.prompt("  Model name", default=prov["default_model"] or "")

    # API key
    api_key: str | None = None
    api_key_env: str | None = prov["api_key_env"]
    if provider_key == "local":
        click.echo("  (No API key needed for local inference.)")
    elif provider_key == "custom":
        api_key_env = click.prompt("  Env var name for API key", default="OPENAI_API_KEY") if not non_interactive else "OPENAI_API_KEY"

    if api_key_env and provider_key not in ("local",):
        existing_key = _os.environ.get(api_key_env, "")
        if existing_key:
            click.echo(f"  {api_key_env} already set in environment — using it.")
            api_key = None  # will be read from env at runtime
        elif non_interactive:
            click.echo(f"  (Skipping key prompt in non-interactive mode.)")
        else:
            if prov.get("key_url"):
                click.echo(f"  Get a key: {prov['key_url']}")
            click.echo("  (Your key will not be shown as you type — this is normal)")
            raw = click.prompt(
                f"  {api_key_env}",
                default="",
                hide_input=True,
                prompt_suffix=": ",
            )
            api_key = raw.strip() or None
            if api_key:
                masked = api_key[:4] + "..." + api_key[-4:] if len(api_key) > 8 else "****"
                click.echo(f"  ✓  Key received ({masked})")

    # base_url for custom
    base_url = prov["base_url"]
    if provider_key == "custom" and not non_interactive:
        base_url = click.prompt("  Base URL", default="https://api.openai.com/v1")

    # --- Optional API key test ---
    if not non_interactive and provider_key != "local" and base_url:
        from .setup import test_llm_connection as _test_llm
        click.echo("  Testing connection...", nl=False)
        ok, msg = _test_llm(base_url, api_key=api_key, api_key_env=api_key_env)
        if ok:
            click.echo(f"\r  ✓  {msg}          ")
        else:
            click.echo(f"\r  ✗  {msg}")
            click.echo("  You can continue and fix the key later, or press Ctrl-C to exit.")
            if not click.confirm("  Continue anyway?", default=True):
                raise SystemExit(1)

    # Write config
    cfg_path = write_llm_config(
        base_url=base_url,
        model=model,
        api_key_env=api_key_env,
        api_key=api_key,
    )
    click.echo(f"\n  ✓  Config written to {cfg_path}")

    # ----------------------------------------- Step 2: Integration targets
    click.echo()
    click.echo("Step 2 of 3 — Integration Targets")
    click.echo("-" * 35)

    _TOOL_MENU = [
        # (key, label, note)
        ("claude_code",  "Claude Code",         "hooks / MCP / both — you'll choose next"),
        ("antigravity",  "Google Antigravity",  "hooks + MCP"),
        ("codex",        "OpenAI Codex CLI",    "hooks + MCP"),
        ("openhands",    "OpenHands",           "hooks"),
        ("opencode",     "OpenCode",            "JS plugin (chat.message hook)"),
    ]

    click.echo("  Select tools — enter numbers space-separated (e.g. '1 3').\n")
    for i, (key, label, note) in enumerate(_TOOL_MENU, 1):
        click.echo(f"  [{i}] {label}  —  {note}")
    click.echo()

    if non_interactive:
        raw_selection = "1"
    else:
        raw_selection = click.prompt(
            "  Tools",
            default="1",
            prompt_suffix=" (Enter for Claude Code): ",
        )

    import re as _re
    selected_keys: list[str] = []
    for token in _re.split(r"[,\s]+", raw_selection.strip()):
        try:
            idx = int(token) - 1
            if 0 <= idx < len(_TOOL_MENU):
                key = _TOOL_MENU[idx][0]
                if key not in selected_keys:
                    selected_keys.append(key)
        except ValueError:
            pass

    if not selected_keys:
        click.echo("  –  No tools selected. Skipped.")

    # ----- Claude Code sub-menu -----
    integration_choice = "4"
    if "claude_code" in selected_keys:
        click.echo()
        click.echo("  → Claude Code — choose integration method:")
        click.echo("    [1] Hooks (recommended) — auto-inject context before every prompt")
        click.echo("    [2] MCP server — Claude calls Waystone as a tool on-demand")
        click.echo("    [3] Both hooks + MCP")
        click.echo()
        if non_interactive:
            cc_method = "1"
        else:
            cc_method = click.prompt("    Method", default="1")
        integration_choice = {"1": "2", "2": "1", "3": "3"}.get(cc_method, "2")

        if integration_choice in ("1", "3"):  # MCP
            ok, msg = register_mcp_server()
            prefix = "  ✓ " if ok else "  ✗ "
            for line in msg.splitlines():
                click.echo(f"{prefix}{line}")
                prefix = "    "

        if integration_choice in ("2", "3"):  # hooks
            hooks_dir = Path(__file__).resolve().parent.parent / "hooks"
            if not hooks_dir.exists():
                hooks_dir = Path(__file__).resolve().parent / "hooks"
            if hooks_dir.exists():
                added, skipped = install_hooks(hooks_dir)
                for label in added:
                    click.echo(f"  ✓  {label} added to ~/.claude/settings.json")
                for label in skipped:
                    click.echo(f"  –  {label} already installed")
                if install_claude_md():
                    click.echo("  ✓  Waystone section appended to ~/.claude/CLAUDE.md")
                else:
                    click.echo("  –  CLAUDE.md already has Waystone section")

        # Slash commands (e.g. /btw) — useful with any Claude Code method
        installed_cmds = install_slash_commands()
        for name in installed_cmds:
            click.echo(f"  ✓  /{name} command installed to ~/.claude/commands/")

    # ----- Additional tools -----
    extra_tools: list[str] = []

    if "antigravity" in selected_keys:
        extra_tools.append("antigravity")
        ag_added, _ = install_antigravity_hooks()
        _ag_ok, _ag_msg = register_antigravity_mcp()
        for label in ag_added:
            click.echo(f"  ✓  {label} added")
        click.echo(f"  {'✓' if _ag_ok else '✗'}  {_ag_msg}")

    if "codex" in selected_keys:
        extra_tools.append("codex")
        cx_added, _ = install_codex_hooks()
        _cx_ok, _cx_msg = register_codex_mcp()
        for label in cx_added:
            click.echo(f"  ✓  {label} added")
        click.echo(f"  {'✓' if _cx_ok else '✗'}  {_cx_msg}")

    if "openhands" in selected_keys:
        extra_tools.append("openhands")
        oh_added, _ = install_openhands_hooks()
        for label in oh_added:
            click.echo(f"  ✓  {label} added")

    if "opencode" in selected_keys:
        extra_tools.append("opencode")
        oc_ok, oc_msg = install_opencode_plugin()
        click.echo(f"  {'✓' if oc_ok else '✗'}  {oc_msg}")

    # Save the chosen mode so waystone doctor can contextualize its output
    from .setup import save_integration_mode as _save_mode
    _save_mode(integration_choice, extra_tools=extra_tools or None)

    # ----------------------------------------------- Step 3: Project marker
    click.echo()
    click.echo("Step 3 of 3 — Mark a Project (optional)")
    click.echo("-" * 35)
    click.echo("  Waystone identifies which graph to use via a '.waystone' file")
    click.echo("  in your project root.\n")

    if non_interactive:
        mark = False
    else:
        mark = click.confirm("  Mark the current directory as a Waystone project?", default=True)

    if mark:
        default_name = Path.cwd().name
        project_name = click.prompt("  Project name", default=default_name)
        marker = Path.cwd() / ".waystone"
        if marker.is_dir():
            click.echo(
                f"  ✗  Cannot create marker: {marker} is already a directory (Waystone's data folder).",
                err=True,
            )
            click.echo("  Run 'waystone configure' from your project directory instead, or run:")
            click.echo(f"    echo '{project_name}' > /path/to/your/project/.waystone")
        else:
            try:
                marker.write_text(project_name + "\n", encoding="utf-8")
                click.echo(f"  ✓  Created {marker} → project '{project_name}'")
                click.echo(f"\n  Next: extract your first transcript or run 'waystone onboard {project_name}'")
            except PermissionError:
                click.echo(f"  ✗  Permission denied writing {marker}.", err=True)
                click.echo("  Run 'waystone configure' from your project directory instead, or run:")
                click.echo(f"    echo '{project_name}' > /path/to/your/project/.waystone")
    else:
        click.echo("  Skipped. When ready:")
        click.echo("    echo 'myproject' > /path/to/your/project/.waystone")

    # ------------------------------------------------------------------ done
    click.echo()
    click.echo("=" * 50)
    click.echo("Setup complete. Run 'waystone doctor' to verify everything is working.")
    click.echo()


@cli.command("pause")
def pause_cmd():
    """Pause background extraction (context injection continues from existing graph).

    Creates ~/.waystone/paused. Run 'waystone resume' to re-enable.
    Prompts continue to be buffered while paused so no turns are lost.
    """
    pause_file = Path.home() / ".waystone" / "paused"
    pause_file.parent.mkdir(parents=True, exist_ok=True)
    if pause_file.exists():
        click.echo("Extraction already paused.")
    else:
        pause_file.touch()
        click.echo("Extraction paused. Context injection from existing graph continues.")
        click.echo("Run 'waystone resume' to re-enable.")


@cli.command("resume")
def resume_cmd():
    """Resume background extraction after 'waystone pause'.

    Removes ~/.waystone/paused. Buffered turns will be extracted
    on the next flush trigger.
    """
    pause_file = Path.home() / ".waystone" / "paused"
    if pause_file.exists():
        pause_file.unlink()
        click.echo("Extraction resumed.")
    else:
        click.echo("Extraction was not paused.")


# ---------------------------------------------------------------------------
# Pilot REPL (imported from pilot package)
# ---------------------------------------------------------------------------

try:
    from pilot.cli import main as _orchestrate_cmd
    cli.add_command(_orchestrate_cmd, name="orchestrate")
except ImportError:
    pass  # pilot package not installed — skip gracefully


# ---------------------------------------------------------------------------
# Feedback labeling
# ---------------------------------------------------------------------------

@cli.command("feedback")
@click.argument("project")
@click.option("--node", "node_id", default=None, help="Rate a specific node by ID (non-interactive)")
@click.option("--rating", type=click.Choice(["up", "down", "1", "-1"]), default=None,
              help="Rating for --node: up/1 or down/-1")
@click.option("--comment", default="", help="Optional comment to attach to rating")
@click.option("--export", "export_path", default=None, metavar="FILE.jsonl",
              help="Export rated nodes to JSONL for LoRA fine-tuning")
@click.option("--only-up", is_flag=True, help="Export only thumbs-up nodes")
@click.option("--only-down", is_flag=True, help="Export only thumbs-down nodes")
@click.option("--limit", default=50, show_default=True, type=int,
              help="Max unrated nodes to show in interactive mode")
@click.option("--stats", "show_stats", is_flag=True, help="Show feedback statistics")
@click.option("--auto-label", "auto_label_mode", is_flag=True, help="Use LLM-as-judge to auto-rate unrated nodes")
@click.option("--dry-run", is_flag=True, help="Show what would be rated without writing to DB")
@click.pass_context
def feedback_cmd(ctx, project, node_id, rating, comment, export_path, only_up, only_down, limit, show_stats, auto_label_mode, dry_run):
    """Label extracted facts with thumbs up/down for LoRA fine-tuning.

    Interactive mode (default): steps through unrated nodes one by one.
    Keypresses: [u]p  [d]own  [s]kip  [q]uit

    \b
    Rate a specific node:
        waystone feedback myproject --node n_abc123 --rating up

    Auto-label nodes with LLM-as-judge:
        waystone feedback myproject --auto-label
        waystone feedback myproject --auto-label --dry-run

    Export rated nodes to JSONL:
        waystone feedback myproject --export training.jsonl
        waystone feedback myproject --export good_only.jsonl --only-up

    Show statistics:
        waystone feedback myproject --stats
    """
    from .feedback import export_jsonl, rate_node, review_loop, auto_label

    config = _load_cfg(ctx.obj["config_path"])
    db_path = get_db_path(config, project)

    if not db_path.parent.exists():
        click.echo(f"Error: Project '{project}' not found.", err=True)
        sys.exit(1)

    store = GraphStore(db_path)

    # --auto-label: use LLM to rate unrated nodes
    if auto_label_mode:
        cfg = _load_cfg(ctx.obj["config_path"])
        transcripts_dir = Path(os.environ.get("HOME", "~")).expanduser() / ".waystone" / "transcripts" / project
        result = auto_label(store, project, transcripts_dir, cfg["llm"], limit=limit, dry_run=dry_run)
        store.close()
        if dry_run:
            click.echo(f"[dry-run] Would rate {result['rated']} nodes ({result['thumbs_up']} up, {result['thumbs_down']} down; {result['skipped']} skipped)")
        else:
            click.echo(f"Auto-labeled {result['rated']} nodes: {result['thumbs_up']} up, {result['thumbs_down']} down ({result['skipped']} skipped)")
        return

    # --stats: show feedback summary
    if show_stats:
        fb_stats = store.get_feedback_stats()
        click.echo(f"Project: {project}")
        click.echo(f"  Total nodes:   {fb_stats['total']}")
        click.echo(f"  Rated:         {fb_stats['rated']}  ({fb_stats['thumbs_up']} up / {fb_stats['thumbs_down']} down)")
        click.echo(f"  Unrated:       {fb_stats['unrated']}")
        store.close()
        return

    # --export: dump JSONL
    if export_path:
        export_rating = None
        if only_up:
            export_rating = 1
        elif only_down:
            export_rating = -1
        out = Path(export_path)
        count = export_jsonl(store, out, rating=export_rating)
        store.close()
        if count == 0:
            click.echo("No rated nodes to export.")
        else:
            click.echo(f"Exported {count} record(s) to {out}")
        return

    # --node + --rating: single non-interactive rating
    if node_id is not None:
        if rating is None:
            click.echo("Error: --rating required when using --node", err=True)
            store.close()
            sys.exit(1)
        int_rating = 1 if rating in ("up", "1") else -1
        ok = rate_node(store, node_id, int_rating, comment=comment)
        store.close()
        if ok:
            label = "thumbs up" if int_rating == 1 else "thumbs down"
            click.echo(f"Rated {node_id}: {label}")
        else:
            click.echo(f"Error: node '{node_id}' not found.", err=True)
            sys.exit(1)
        return

    # Default: interactive review loop
    summary = review_loop(store, limit=limit)
    store.close()

    if summary["rated"] > 0:
        click.echo(f"\nTip: export your labels with:")
        click.echo(f"  waystone feedback {project} --export training.jsonl")


# ---------------------------------------------------------------------------
# Domain bootstrapper
# ---------------------------------------------------------------------------

@cli.command("bootstrap-domain")
@click.option("--name", required=True, help="Short snake_case name for the new domain (e.g. medical)")
@click.option("--samples", "sample_files", required=True, multiple=True, type=click.Path(exists=True),
              help="Text files representative of the domain. Pass multiple: --samples a.txt b.txt")
@click.option("--output", default=None, metavar="FILE",
              help="Write Python source for domain_profiles.py to FILE (default: print to stdout)")
@click.option("--yaml", "print_yaml", is_flag=True,
              help="Also print config.yaml snippet for using the new domain")
@click.pass_context
def bootstrap_domain_cmd(ctx, name, sample_files, output, print_yaml):
    """Derive a DomainProfile from sample documents.

    \b
    Runs a two-pass LLM analysis:
      1. Discovery — for each sample, proposes domain-specific node/edge types
      2. Synthesis  — consolidates proposals into a clean, non-redundant schema

    \b
    Example:
        waystone bootstrap-domain --name medical \\
            --samples consult1.txt consult2.txt discharge_summary.txt \\
            --output medical_profile.py
    """
    from .domain_bootstrapper import bootstrap_domain, profile_to_python, profile_to_yaml

    config = _load_cfg(ctx.obj["config_path"])

    samples = []
    for path in sample_files:
        try:
            text = Path(path).read_text(encoding="utf-8", errors="replace")
            samples.append(text)
        except OSError as e:
            click.echo(f"Error reading {path}: {e}", err=True)
            sys.exit(1)

    click.echo(f"Bootstrapping domain '{name}' from {len(samples)} sample(s)...")

    result = asyncio.run(bootstrap_domain(name, samples, config, verbose=True))
    profile = result.profile

    click.echo(f"\nDerived profile '{name}':")
    click.echo(f"  Node types:     {', '.join(profile.node_types)}")
    click.echo(f"  Edge relations: {', '.join(profile.edge_relations)}")
    if profile.node_types_note:
        click.echo(f"  Note: {profile.node_types_note[:120]}...")

    py_code = profile_to_python(profile)

    if output:
        Path(output).write_text(py_code + "\n", encoding="utf-8")
        click.echo(f"\nPython source written to {output}")
        click.echo("Add the variable to BUILTIN_PROFILES in waystone/domain_profiles.py to register it.")
    else:
        click.echo("\n--- Python source (paste into waystone/domain_profiles.py) ---")
        click.echo(py_code)

    if print_yaml:
        click.echo("\n--- config.yaml snippet ---")
        click.echo(profile_to_yaml(profile))


# ---------------------------------------------------------------------------
# Automated maintenance — watch + auto-import
# ---------------------------------------------------------------------------

@cli.command("watch")
@click.argument("project")
@click.argument("paths", nargs=-1, required=True, type=click.Path())
@click.option("--interval", default=60, show_default=True, type=int, help="Poll interval in seconds")
@click.option("--verify", is_flag=True, help="Pass --verify to each extraction call")
@click.option("--extensions", default="md,txt,rst", show_default=True,
              help="Comma-separated file extensions to watch")
@click.pass_context
def watch_cmd(ctx, project, paths, interval, verify, extensions):
    """Watch directories for new or changed docs and auto-extract them.

    Polls PATH(s) every INTERVAL seconds. Any file whose mtime has changed
    since the last extraction is sent through `waystone extract`. State is
    persisted in the project directory so restarts don't re-extract unchanged
    files.

    \b
    Example:
        waystone watch Waystone ~/Apps/ContextBroker/docs --interval 120 --verify
        waystone watch myproject ./docs ./notes --extensions md,txt
    """
    import time as _time

    config = _load_cfg(ctx.obj["config_path"])
    db_path = get_db_path(config, project)

    if not db_path.parent.exists():
        click.echo(f"Error: Project '{project}' not found. Run 'waystone init {project}' first.", err=True)
        sys.exit(1)

    exts = {("." + e.lstrip(".").lower()) for e in extensions.split(",")}
    manifest_path = db_path.parent / "watch_manifest.json"
    manifest: dict = {}
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text())
        except Exception:
            pass

    def _save_manifest():
        try:
            manifest_path.write_text(json.dumps(manifest))
        except Exception:
            pass

    def _scan() -> list[Path]:
        found = []
        for p in paths:
            root = Path(p).expanduser().resolve()
            if root.is_file():
                if root.suffix.lower() in exts:
                    found.append(root)
            elif root.is_dir():
                for f in root.rglob("*"):
                    if f.is_file() and f.suffix.lower() in exts:
                        found.append(f)
        return found

    click.echo(f"Watching {len(paths)} path(s) for project '{project}' (every {interval}s)...")
    click.echo("Press Ctrl+C to stop.\n")

    while True:
        files = _scan()
        for f in files:
            key = str(f)
            try:
                mtime = f.stat().st_mtime
            except Exception:
                continue
            if manifest.get(key) == mtime:
                continue

            click.echo(f"[{datetime.now().strftime('%H:%M:%S')}] Extracting {f.name}...", nl=False)
            cmd = [sys.executable, "-m", "waystone.cli", "--config", str(ctx.obj["config_path"] or ""),
                   "extract", project, str(f)]
            if verify:
                cmd.append("--verify")
            # Remove empty config arg if not set
            if not ctx.obj["config_path"]:
                cmd = [c for c in cmd if c != ""]

            try:
                result = subprocess.run(
                    cmd,
                    capture_output=True, text=True,
                    cwd=str(Path(__file__).resolve().parent.parent),
                )
                if result.returncode == 0:
                    manifest[key] = mtime
                    _save_manifest()
                    # Grab node count from output
                    summary = result.stdout.strip().splitlines()[-1] if result.stdout.strip() else "done"
                    click.echo(f" {summary}")
                else:
                    click.echo(f" FAILED\n{result.stderr.strip()}", err=True)
            except Exception as e:
                click.echo(f" ERROR: {e}", err=True)

        _time.sleep(interval)


# --- World container subcommands ---

@cli.group("world")
@click.pass_context
def world_group(ctx):
    """Manage named context namespaces (worlds)."""
    pass


@world_group.command("create")
@click.argument("project")
@click.option("--name", required=True, help="Name of the world")
@click.option("--description", default=None, help="Optional description")
@click.option("--parent", default=None, help="Optional parent world ID for hierarchy")
@click.pass_context
def world_create(ctx, project, name, description, parent):
    """Create a new world (named context namespace)."""
    config = _load_cfg(ctx.obj["config_path"])
    db_path = get_db_path(config, project)

    if not db_path.exists():
        click.echo(f"Error: Project '{project}' not found. Run 'waystone init {project}' first.", err=True)
        sys.exit(1)

    try:
        store = GraphStore(db_path)
        world_id = store.create_world(
            name=name,
            description=description,
            parent_world_id=parent,
        )
        store.close()
        click.echo(f"Created world '{name}' (ID: {world_id})")
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)


@world_group.command("list")
@click.argument("project")
@click.option("--parent", default=None, help="Filter by parent world ID")
@click.pass_context
def world_list(ctx, project, parent):
    """List all worlds in a project."""
    config = _load_cfg(ctx.obj["config_path"])
    db_path = get_db_path(config, project)

    if not db_path.exists():
        click.echo(f"Error: Project '{project}' not found.", err=True)
        sys.exit(1)

    try:
        store = GraphStore(db_path)
        worlds = store.list_worlds(parent_world_id=parent)
        store.close()

        if not worlds:
            click.echo("No worlds found.")
            return

        for w in worlds:
            click.echo(f"  {w['name']:20} (ID: {w['world_id']}, nodes: {w['node_count']})")
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)


@world_group.command("show")
@click.argument("project")
@click.argument("world_id")
@click.option("--recursive", is_flag=True, help="Include child worlds")
@click.pass_context
def world_show(ctx, project, world_id, recursive):
    """Show details of a specific world."""
    config = _load_cfg(ctx.obj["config_path"])
    db_path = get_db_path(config, project)

    if not db_path.exists():
        click.echo(f"Error: Project '{project}' not found.", err=True)
        sys.exit(1)

    try:
        store = GraphStore(db_path)
        world = store.get_world(world_id)

        if not world:
            click.echo(f"World '{world_id}' not found.", err=True)
            sys.exit(1)

        click.echo(f"World: {world['name']}")
        click.echo(f"ID: {world['world_id']}")
        if world.get('description'):
            click.echo(f"Description: {world['description']}")
        click.echo(f"Node count: {world['node_count']}")
        click.echo(f"Created: {world.get('created_at', 'unknown')}")

        if recursive:
            nodes = store.get_world_nodes(world_id, recursive=True)
            click.echo(f"Total nodes (recursive): {len(nodes)}")

        store.close()
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)


@world_group.command("add-node")
@click.argument("project")
@click.argument("world_id")
@click.argument("node_id")
@click.pass_context
def world_add_node(ctx, project, world_id, node_id):
    """Add a node to a world."""
    config = _load_cfg(ctx.obj["config_path"])
    db_path = get_db_path(config, project)

    if not db_path.exists():
        click.echo(f"Error: Project '{project}' not found.", err=True)
        sys.exit(1)

    try:
        store = GraphStore(db_path)
        store.add_node_to_world(node_id, world_id)
        store.close()
        click.echo(f"Added node {node_id} to world {world_id}")
    except ValueError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)


@cli.command("auto-import")
@click.argument("project")
@click.argument("directory", type=click.Path(exists=True))
@click.option("--verify", is_flag=True, help="Run --verify pass on each file")
@click.option("--extensions", default="md,txt,rst", show_default=True,
              help="Comma-separated file extensions to import")
@click.option("--force", is_flag=True, help="Re-import files even if already in manifest")
@click.option("--dry-run", is_flag=True, help="List files that would be imported without importing")
@click.pass_context
def auto_import_cmd(ctx, project, directory, verify, extensions, force, dry_run):
    """Bulk-import all docs in a directory, skipping already-extracted files.

    Maintains a manifest of (path, mtime) pairs so re-running only imports
    new or modified files. Use --force to re-import everything.

    \b
    Example:
        waystone auto-import Waystone ~/Apps/ContextBroker --verify
        waystone auto-import myproject ./docs --dry-run
        waystone auto-import myproject ./docs --force --verify
    """
    import time as _time

    config = _load_cfg(ctx.obj["config_path"])
    db_path = get_db_path(config, project)

    if not db_path.parent.exists():
        click.echo(f"Error: Project '{project}' not found. Run 'waystone init {project}' first.", err=True)
        sys.exit(1)

    exts = {("." + e.lstrip(".").lower()) for e in extensions.split(",")}
    manifest_path = db_path.parent / "watch_manifest.json"
    manifest: dict = {}
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text())
        except Exception:
            pass

    root = Path(directory).expanduser().resolve()
    all_files = [f for f in root.rglob("*") if f.is_file() and f.suffix.lower() in exts]
    all_files.sort(key=lambda f: f.stat().st_mtime)

    to_import = []
    skipped = 0
    for f in all_files:
        key = str(f)
        try:
            mtime = f.stat().st_mtime
        except Exception:
            continue
        if not force and manifest.get(key) == mtime:
            skipped += 1
        else:
            to_import.append((f, mtime))

    if dry_run:
        click.echo(f"Would import {len(to_import)} file(s), skip {skipped} unchanged:")
        for f, _ in to_import:
            click.echo(f"  {f}")
        return

    if not to_import:
        click.echo(f"Nothing to import — {skipped} file(s) already up to date.")
        return

    click.echo(f"Importing {len(to_import)} file(s) into '{project}' (skipping {skipped} unchanged)...\n")

    total_imported = 0
    t_start = _time.monotonic()

    for i, (f, mtime) in enumerate(to_import, 1):
        click.echo(f"[{i}/{len(to_import)}] {f.name}...", nl=False)
        cmd = [sys.executable, "-m", "waystone.cli", "extract", project, str(f)]
        if verify:
            cmd.append("--verify")
        if ctx.obj["config_path"]:
            cmd = [sys.executable, "-m", "waystone.cli", "--config", ctx.obj["config_path"],
                   "extract", project, str(f)] + (["--verify"] if verify else [])

        try:
            result = subprocess.run(
                cmd,
                capture_output=True, text=True,
                cwd=str(Path(__file__).resolve().parent.parent),
            )
            if result.returncode == 0:
                manifest[str(f)] = mtime
                try:
                    manifest_path.write_text(json.dumps(manifest))
                except Exception:
                    pass
                summary = result.stdout.strip().splitlines()[-1] if result.stdout.strip() else "done"
                click.echo(f" {summary}")
                total_imported += 1
            else:
                click.echo(f" FAILED\n{result.stderr.strip()[:200]}", err=True)
        except Exception as e:
            click.echo(f" ERROR: {e}", err=True)

    elapsed = _time.monotonic() - t_start
    click.echo(f"\nDone: {total_imported}/{len(to_import)} files imported in {elapsed:.0f}s.")
