"""CLI for Context Broker."""

import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path

import click

from .config import get_db_path, get_project_dir, load_config
from .extractor import extract
from .retriever import retrieve, retrieve_with_stats
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
    """Context Broker — DAG-based context intelligence for LLM workflows."""
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


@cli.command("extract")
@click.argument("project")
@click.argument("transcript_file", type=click.Path(exists=True))
@click.pass_context
def extract_cmd(ctx, project, transcript_file):
    """Extract facts from a transcript and merge into the project graph."""
    config = _load_cfg(ctx.obj["config_path"])
    db_path = get_db_path(config, project)

    if not db_path.parent.exists():
        click.echo(f"Error: Project '{project}' not found. Run 'ctx init {project}' first.", err=True)
        sys.exit(1)

    transcript_path = Path(transcript_file)
    transcript_text = transcript_path.read_text()

    click.echo(f"Extracting from {transcript_path.name}...")

    try:
        result = asyncio.run(extract(transcript_text, config))
    except Exception as e:
        click.echo(f"Extraction failed: {e}", err=True)
        sys.exit(1)

    nodes = result["nodes"]
    edges = result["edges"]

    # Set source transcript on all nodes
    for node in nodes:
        node["source_transcript"] = transcript_path.name
        node.setdefault("created_at", datetime.now(timezone.utc).isoformat())

    store = GraphStore(db_path)
    store.merge_extraction(nodes, edges)
    store.close()

    # Copy transcript to project's transcripts dir
    dest = get_project_dir(config, project) / "transcripts" / transcript_path.name
    if not dest.exists():
        dest.write_text(transcript_text)

    click.echo(f"Extracted {len(nodes)} nodes, {len(edges)} edges from {transcript_path.name}")


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

    defaults = config.get("defaults", {})
    hops = hops or defaults.get("hops", 3)
    top_k = top_k or defaults.get("top_k", 10)

    overrides = _parse_strategy_overrides(enable, disable, confidence, token_budget)
    strategies = _resolve_strategies(config, overrides)

    store = GraphStore(db_path)
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
@click.pass_context
def show(ctx, project):
    """Display graph statistics and recent nodes."""
    config = _load_cfg(ctx.obj["config_path"])
    db_path = get_db_path(config, project)

    if not db_path.parent.exists():
        click.echo(f"Error: Project '{project}' not found.", err=True)
        sys.exit(1)

    store = GraphStore(db_path)
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
        click.echo(f"\nRecent nodes:")
        for node in recent:
            tags = ", ".join(node["tags"][:3]) if node["tags"] else ""
            click.echo(f"  [{node['type']}] {node['fact'][:80]}  ({tags})")

    store.close()


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
