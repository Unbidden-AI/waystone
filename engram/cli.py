"""CLI for Engram."""

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
from .retriever import bfs_collect, cluster_by_tags, extract_keywords, retrieve_with_stats, score_by_relevance
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
    """Engram — DAG-based context intelligence for LLM workflows."""
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

    project_dir.mkdir(parents=True)
    (project_dir / "transcripts").mkdir()
    (project_dir / "exports").mkdir()

    db_path = get_db_path(config, project)
    store = GraphStore(db_path)
    store.close()

    click.echo(f"Initialized project '{project}' at {project_dir}")



_MAX_FILE_BYTES = 50 * 1024 * 1024  # 50 MB hard limit


@cli.command("extract")
@click.argument("project")
@click.argument("transcript_file", type=click.Path(exists=True))
@click.option("--verify", is_flag=True, help="Run a second verification pass to catch missed facts")
@click.option("--lessons", is_flag=True, help="Run a targeted pass hunting for lesson_learned nodes (failed approaches, rejected alternatives)")
@click.option("--decisions", is_flag=True, help="Run a targeted pass hunting for decision nodes and their rationale")
@click.option("--questions", is_flag=True, help="Run a targeted pass hunting for open questions and unresolved items")
@click.option("--constraints", is_flag=True, help="Run a targeted pass hunting for hard constraints and requirements")
@click.option("--numerics", is_flag=True, help="Run a targeted pass hunting for numeric values, measurements, and quantified facts")
@click.option("--synthesize", is_flag=True, help="Run a synthesis pass after extraction: create cross-cutting summary nodes across all graph nodes")
@click.option("--timeout", type=float, default=None, help="LLM timeout in seconds (overrides config)")
@click.option("--chunk-size", type=int, default=None, metavar="CHARS",
              help="Max chars per LLM call (default: 20000 for Gemini; auto-applied when file > 20000 chars)")
@click.pass_context
def extract_cmd(ctx, project, transcript_file, verify, lessons, decisions, questions, constraints, numerics, synthesize, timeout, chunk_size):
    """Extract facts from a transcript and merge into the project graph."""
    config = _load_cfg(ctx.obj["config_path"])

    if timeout is not None:
        config = dict(config)
        config["llm"] = dict(config.get("llm", {}))
        config["llm"]["timeout"] = timeout

    db_path = get_db_path(config, project)

    if not db_path.parent.exists():
        click.echo(f"Error: Project '{project}' not found. Run 'engram init {project}' first.", err=True)
        sys.exit(1)

    transcript_path = Path(transcript_file)
    file_size = transcript_path.stat().st_size
    if file_size > _MAX_FILE_BYTES:
        click.echo(
            f"Error: {transcript_path.name} is {file_size // 1024 // 1024}MB — "
            f"exceeds the 50MB limit. Split the file first.", err=True
        )
        sys.exit(1)

    transcript_text = transcript_path.read_text()

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
                          ("numerics", numerics)]
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
            "may be hard to retrieve. Run 'engram show {project}' to inspect."
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
    of query. Use 'engram pinned PROJECT' to review what's been pinned.
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
        from engram import embedder as _embedder
        if _embedder.is_available() and store._vec_available:
            embedder_ok = True
            dedup_index = store.get_pinned_embeddings()

    def _find_duplicate(fact: str) -> tuple[str, str, float] | None:
        """Return (matched_id, matched_fact, sim) if fact is near-duplicate of a pinned node."""
        if not embedder_ok or not dedup_index:
            return None
        from engram import embedder as _embedder
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


@cli.command("synthesize")
@click.argument("project")
@click.option("--dry-run", is_flag=True, help="Print synthesis nodes without storing them")
@click.option("--max-nodes", type=int, default=1000, show_default=True,
              help="Max nodes to send to LLM (sorted by confidence desc); use 0 for no limit")
@click.option("--tags", multiple=True, metavar="TAG",
              help="Only include nodes tagged with at least one of these tags (e.g. --tags benchmark --tags model)")
@click.pass_context
def synthesize_cmd(ctx, project, dry_run, max_nodes, tags):
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
        f"Running synthesis over {len(candidate_nodes)} candidate nodes "
        f"({len(all_graph_nodes)} total) in '{project}'..."
    )
    try:
        sr = asyncio.run(synthesize_extraction(candidate_nodes, config))
    except Exception as e:
        click.echo(f"Error: Synthesis failed: {e}", err=True)
        sys.exit(1)

    if not sr["nodes"]:
        click.echo("No synthesis clusters found (need ≥3 parallel nodes on same metric).")
        return

    click.echo(f"Found {len(sr['nodes'])} summary node(s), {len(sr['edges'])} edge(s):")
    for sn in sr["nodes"]:
        click.echo(f"  [{sn['type']}] {sn['fact'][:100]}")

    if dry_run:
        click.echo("(dry-run: not stored)")
        return

    _SYNTHESIS_SOURCE = "__synthesis__"
    for sn in sr["nodes"]:
        sn["source_transcript"] = _SYNTHESIS_SOURCE
        sn.setdefault("created_at", datetime.now(timezone.utc).isoformat())

    store = GraphStore(db_path)
    store.merge_extraction(sr["nodes"], sr["edges"])
    store.close()
    click.echo(f"Stored {len(sr['nodes'])} summary node(s).")


@cli.command()
@click.argument("project")
@click.argument("task")
@click.option("--hops", default=None, type=int, help="Graph traversal depth")
@click.option("--top-k", default=None, type=int, help="Max nodes to return")
@click.option("--enable", "-e", multiple=True, help="Enable a strategy (e.g. -e superseded_pruning -e recency_decay)")
@click.option("--disable", "-d", multiple=True, help="Disable a strategy (e.g. -d relevance_scoring)")
@click.option("--confidence", type=float, default=None, help="Min confidence threshold (e.g. 0.6)")
@click.option("--token-budget", type=int, default=None, help="Max tokens in output (e.g. 500)")
@click.option("--stats", "show_stats", is_flag=True, help="Show retrieval stats (for benchmarking)")
@click.pass_context
def query(ctx, project, task, hops, top_k, enable, disable, confidence, token_budget, show_stats):
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

    store = GraphStore(db_path)
    stats = store.get_stats()
    if stats["node_count"] == 0:
        click.echo(
            f"Graph is empty. Extract a transcript first:\n"
            f"  engram extract {project} <transcript_file>\n"
            f"Or replay an existing transcript turn-by-turn:\n"
            f"  engram extract-replay {project} <transcript_file>",
            err=True,
        )
        store.close()
        sys.exit(1)

    result = retrieve_with_stats(store, task, hops=hops, top_k=top_k, strategies=strategies)
    store.close()

    click.echo(result.markdown)

    if show_stats:
        click.echo("\n--- Retrieval Stats ---")
        click.echo(f"Nodes before strategies: {result.nodes_before_strategies}")
        click.echo(f"Nodes after strategies:  {result.nodes_after_strategies}")
        click.echo(f"Strategies applied:      {', '.join(result.strategies_applied) or 'none'}")
        click.echo(f"Estimated tokens:        {result.tokens_estimated}")


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

      engram unpin myproject some_node_id

      engram unpin myproject --source SOUL.md
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
@click.option("--enable", "-e", multiple=True, help="Enable a strategy")
@click.option("--disable", "-d", multiple=True, help="Disable a strategy")
@click.option("--confidence", type=float, default=None, help="Min confidence threshold")
@click.option("--token-budget", type=int, default=None, help="Max tokens in output")
@click.pass_context
def export(ctx, project, output, enable, disable, confidence, token_budget):
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

    if output:
        out_path = Path(output)
    else:
        out_path = get_project_dir(config, project) / "exports" / "current.md"

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(markdown)
    click.echo(f"Exported {len(all_nodes)} nodes to {out_path}")

    store.close()


@cli.command("reconcile")
@click.argument("project")
@click.option("--dry-run", is_flag=True, help="Show supersedes pairs without writing to graph")
@click.option("--max-cluster-size", default=20, type=int, show_default=True,
              help="Max nodes per LLM call")
@click.pass_context
def reconcile_cmd(ctx, project, dry_run, max_cluster_size):
    """Aggressively find and record supersedes relationships across all graph nodes.

    Clusters nodes by tag overlap, sends each cluster to the LLM asking
    "which of these are superseded by others?", and writes the resulting
    supersedes edges back into the graph.

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
    store.close()

    if dry_run:
        click.echo(f"\nDry run: found {total_pairs} supersedes pair(s) across {len(clusters)} clusters.")
        click.echo("Run without --dry-run to write them to the graph.")
    else:
        click.echo(f"\nWrote {total_written} supersedes edge(s) to '{project}' graph.")
        if total_written:
            click.echo("Run 'engram show' to inspect, or 'engram query' to see the pruned results.")


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
      engram prune myproject --source live
      engram prune myproject --older-than 90 --confidence-below 0.5
      engram prune myproject --source live --execute
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

    Creates a .context-broker file containing the project name. The hook
    reads this when determining which graph to query for context injection.

    Example:
        engram hook-init myproject          # marks current directory
        engram hook-init myproject --dir ~/code/myapp
    """
    marker = Path(target_dir).resolve() / ".context-broker"
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
        click.echo(f"Error: Project '{project}' not found. Run 'engram init {project}' first.", err=True)
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
        click.echo(f"Error: Project '{project}' not found. Run 'engram init {project}' first.", err=True)
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
    project from a .context-broker marker file.
    Also prints the retrieval metrics from the last hook invocation.
    """
    import json as _json
    import time as _time

    state_dir = Path.home() / ".context-broker"
    state_path = state_dir / "state.json"

    # Detect project from CWD to find per-project last_context.md
    def _detect_project_from_cwd() -> str | None:
        cwd_path = Path.cwd().resolve()
        home = Path.home()
        for directory in [cwd_path, *cwd_path.parents]:
            marker = directory / ".context-broker"
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
    """Start the Engram HTTP API server.

    \b
    Clients configure api_url in config.yaml to route requests here:
        api_url: http://localhost:8000
        api_key: my-secret   # optional; set CB_API_KEY on server to require it

    Requires the 'api' extra:  pip install 'context-broker[api]'
    """
    try:
        import uvicorn
    except ImportError:
        click.echo(
            "Error: uvicorn not installed.\n"
            "Install with:  pip install 'context-broker[api]'",
            err=True,
        )
        sys.exit(1)

    click.echo(f"Engram API → http://{host}:{port}  (docs: /docs)")
    uvicorn.run(
        "engram.api_server:app",
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
    """Start the Engram MCP server.

    \b
    For Claude Code, add to ~/.claude/claude_desktop_config.json:
      {
        "mcpServers": {
          "context-broker": {
            "command": "engram",
            "args": ["mcp-serve"]
          }
        }
      }

    The server exposes four tools:
      context_broker_query        — retrieve context for a task
      context_broker_extract      — extract facts from text into graph
      context_broker_stats        — show graph node/edge counts
      context_broker_list_projects — list all available projects
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
        engram onboard myproject
        engram onboard myproject --verify --limit 5
    """
    config = _load_cfg(ctx.obj["config_path"])

    if timeout is not None:
        config = dict(config)
        config["llm"] = dict(config.get("llm", {}))
        config["llm"]["timeout"] = timeout

    # Auto-detect project from .context-broker marker if not given
    if not project:
        marker = Path.cwd() / ".context-broker"
        if marker.exists():
            project = marker.read_text().strip()
        if not project:
            click.echo("Error: specify a project name or run 'engram hook-init <project>' first.", err=True)
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
    click.echo(f"\nRun 'engram query {project} \"<your task>\"' to retrieve context anytime.")


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
        engram import-claude-sessions myproject ~/.claude/projects/abc123/session.jsonl
        engram import-claude-sessions myproject --list-only
    """
    config = _load_cfg(ctx.obj["config_path"])

    if timeout is not None:
        config = dict(config)
        config["llm"] = dict(config.get("llm", {}))
        config["llm"]["timeout"] = timeout

    db_path = get_db_path(config, project)
    if not db_path.parent.exists():
        click.echo(f"Error: Project '{project}' not found. Run 'engram init {project}' first.", err=True)
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
@click.pass_context
def doctor_cmd(ctx):
    """Run a preflight check: config, LLM reachability, project marker, MCP.

    Prints a checklist of what's working and what needs attention.
    Use this to diagnose setup issues before running engram extract or engram query.
    """
    import os as _os

    config_path = ctx.obj["config_path"]
    ok = True

    def _check(label: str, passed: bool, detail: str = ""):
        nonlocal ok
        icon = "✓" if passed else "✗"
        msg = f"  {icon}  {label}"
        if detail:
            msg += f"  ({detail})"
        click.echo(msg)
        if not passed:
            ok = False

    click.echo("Engram — Doctor\n")

    # --- Config file ---
    try:
        config = _load_cfg(config_path)
        cfg_path = config_path or "~/.context-broker/config.yaml or ./config.yaml"
        _check("Config file loaded", True, cfg_path)
    except SystemExit:
        _check("Config file loaded", False, "run 'engram --help' for config path options")
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
    import httpx as _httpx
    base_url = llm_cfg.get("base_url", "http://localhost:1234/v1")
    try:
        r = _httpx.get(base_url.rstrip("/").rsplit("/", 1)[0] + "/models", timeout=5)
        reachable = r.status_code < 500
        _check("LLM endpoint reachable", reachable, f"{base_url} → HTTP {r.status_code}")
    except Exception as e:
        _check("LLM endpoint reachable", False, f"{base_url} — {type(e).__name__}")

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
        marker = candidate / ".context-broker"
        if marker.exists():
            marker_found = True
            marker_project = marker.read_text().strip()
            break
    _check(
        ".context-broker marker found",
        marker_found,
        f"project='{marker_project}'" if marker_found else "run 'engram hook-init <project>' to create one",
    )

    # --- Project graph exists ---
    if marker_project:
        db_path = get_db_path(config, marker_project)
        db_exists = db_path.exists()
        _check(
            f"Graph DB exists for '{marker_project}'",
            db_exists,
            str(db_path) if db_exists else f"run 'engram init {marker_project}' or 'engram onboard {marker_project}'",
        )
        if db_exists:
            store = GraphStore(db_path)
            stats = store.get_stats()
            store.close()
            has_nodes = stats["node_count"] > 0
            _check(
                f"Graph populated ({stats['node_count']} nodes)",
                has_nodes,
                "" if has_nodes else f"run 'engram onboard {marker_project}' to import sessions",
            )

    # --- Claude Code hooks ---
    settings_path = Path.home() / ".claude" / "settings.json"
    if settings_path.exists():
        import json as _json
        try:
            settings = _json.loads(settings_path.read_text())
            hooks = settings.get("hooks", {})
            has_submit = any(
                "engram" in str(h)
                for h in hooks.get("UserPromptSubmit", [])
            )
            has_stop = any(
                "engram" in str(h)
                for h in hooks.get("Stop", [])
            )
            _check("UserPromptSubmit hook installed", has_submit,
                   "" if has_submit else "run hooks/install.py or use MCP server instead")
            _check("Stop hook installed", has_stop,
                   "" if has_stop else "run hooks/install.py or use MCP server instead")
        except Exception:
            _check("Claude Code settings readable", False, str(settings_path))
    else:
        _check("Claude Code settings found", False,
               f"{settings_path} missing — Claude Code not installed or not yet run")

    click.echo()
    if ok:
        click.echo("All checks passed. Engram is ready.")
    else:
        click.echo("Some checks failed. See above for remediation steps.")
        sys.exit(1)


@cli.command("pause")
def pause_cmd():
    """Pause background extraction (context injection continues from existing graph).

    Creates ~/.context-broker/paused. Run 'engram resume' to re-enable.
    Prompts continue to be buffered while paused so no turns are lost.
    """
    pause_file = Path.home() / ".context-broker" / "paused"
    pause_file.parent.mkdir(parents=True, exist_ok=True)
    if pause_file.exists():
        click.echo("Extraction already paused.")
    else:
        pause_file.touch()
        click.echo("Extraction paused. Context injection from existing graph continues.")
        click.echo("Run 'engram resume' to re-enable.")


@cli.command("resume")
def resume_cmd():
    """Resume background extraction after 'engram pause'.

    Removes ~/.context-broker/paused. Buffered turns will be extracted
    on the next flush trigger.
    """
    pause_file = Path.home() / ".context-broker" / "paused"
    if pause_file.exists():
        pause_file.unlink()
        click.echo("Extraction resumed.")
    else:
        click.echo("Extraction was not paused.")


# ---------------------------------------------------------------------------
# Orchestrator REPL (imported from orchestrator package)
# ---------------------------------------------------------------------------

try:
    from orchestrator.cli import main as _orchestrate_cmd
    cli.add_command(_orchestrate_cmd, name="orchestrate")
except ImportError:
    pass  # orchestrator package not installed — skip gracefully


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
        engram feedback myproject --node n_abc123 --rating up

    Auto-label nodes with LLM-as-judge:
        engram feedback myproject --auto-label
        engram feedback myproject --auto-label --dry-run

    Export rated nodes to JSONL:
        engram feedback myproject --export training.jsonl
        engram feedback myproject --export good_only.jsonl --only-up

    Show statistics:
        engram feedback myproject --stats
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
        transcripts_dir = Path(os.environ.get("HOME", "~")).expanduser() / ".context-broker" / "transcripts" / project
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
        click.echo(f"  engram feedback {project} --export training.jsonl")


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
        engram bootstrap-domain --name medical \\
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
        click.echo("Add the variable to BUILTIN_PROFILES in engram/domain_profiles.py to register it.")
    else:
        click.echo("\n--- Python source (paste into engram/domain_profiles.py) ---")
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
    since the last extraction is sent through `engram extract`. State is
    persisted in the project directory so restarts don't re-extract unchanged
    files.

    \b
    Example:
        engram watch Engram ~/Apps/ContextBroker/docs --interval 120 --verify
        engram watch myproject ./docs ./notes --extensions md,txt
    """
    import time as _time

    config = _load_cfg(ctx.obj["config_path"])
    db_path = get_db_path(config, project)

    if not db_path.parent.exists():
        click.echo(f"Error: Project '{project}' not found. Run 'engram init {project}' first.", err=True)
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
            cmd = [sys.executable, "-m", "engram.cli", "--config", str(ctx.obj["config_path"] or ""),
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
        engram auto-import Engram ~/Apps/ContextBroker --verify
        engram auto-import myproject ./docs --dry-run
        engram auto-import myproject ./docs --force --verify
    """
    import time as _time

    config = _load_cfg(ctx.obj["config_path"])
    db_path = get_db_path(config, project)

    if not db_path.parent.exists():
        click.echo(f"Error: Project '{project}' not found. Run 'engram init {project}' first.", err=True)
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
        cmd = [sys.executable, "-m", "engram.cli", "extract", project, str(f)]
        if verify:
            cmd.append("--verify")
        if ctx.obj["config_path"]:
            cmd = [sys.executable, "-m", "engram.cli", "--config", ctx.obj["config_path"],
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
