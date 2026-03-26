"""CLI entry point for the Context Broker Orchestrator (interactive REPL)."""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

import click

from engram.config import get_db_path, load_config
from engram.store import GraphStore

from .conversation import Conversation

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_HELP_TEXT = """\
Commands:
  /help       Show this message
  /reset      Clear history (keep graph)
  /stats      Show context manager stats
  /quit       Exit
"""

_BANNER = "Context Broker Orchestrator — type /help for commands, Ctrl-C to exit."


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.WARNING
    logging.basicConfig(
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
        level=level,
    )


def _load_cfg(config_path: str | None) -> dict:
    try:
        return load_config(config_path)
    except FileNotFoundError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)


# ---------------------------------------------------------------------------
# REPL
# ---------------------------------------------------------------------------


async def _repl(conversation: Conversation, stream: bool) -> None:
    """Run the interactive prompt loop."""
    click.echo(_BANNER)
    click.echo()

    while True:
        try:
            user_input = await asyncio.to_thread(click.prompt, "You", prompt_suffix="> ")
        except (EOFError, KeyboardInterrupt):
            click.echo("\nBye.")
            break

        line = user_input.strip()
        if not line:
            continue

        # Built-in slash commands
        if line.startswith("/"):
            cmd = line.split()[0].lower()
            if cmd in ("/quit", "/exit", "/q"):
                click.echo("Bye.")
                break
            elif cmd == "/reset":
                conversation.reset()
                click.echo("[session reset]")
                continue
            elif cmd == "/stats":
                stats = conversation.stats()
                for k, v in stats.items():
                    if isinstance(v, float):
                        click.echo(f"  {k}: {v:.1f}")
                    else:
                        click.echo(f"  {k}: {v}")
                continue
            elif cmd == "/help":
                click.echo(_HELP_TEXT)
                continue
            else:
                click.echo(f"Unknown command: {cmd}. Type /help for available commands.")
                continue

        # Normal turn
        click.echo()
        try:
            if stream:
                click.echo("Assistant> ", nl=False)
                async for chunk in conversation.chat_stream(line):
                    click.echo(chunk, nl=False)
                click.echo()
            else:
                reply = await conversation.chat(line)
                click.echo(f"Assistant> {reply}")
        except Exception as e:
            log.debug("Turn error", exc_info=True)
            click.echo(f"[Error: {e}]", err=True)
        click.echo()


# ---------------------------------------------------------------------------
# Click command
# ---------------------------------------------------------------------------


@click.command(name="orchestrate")
@click.argument("project")
@click.option("--config", "config_path", default=None, help="Path to config.yaml")
@click.option("--stream/--no-stream", default=True, show_default=True, help="Stream reply tokens")
@click.option("-v", "--verbose", is_flag=True, default=False, help="Enable debug logging")
def main(project: str, config_path: str | None, stream: bool, verbose: bool) -> None:
    """Start an interactive orchestrator session for PROJECT.

    PROJECT is the name of a Context Broker project (same namespace used by
    ``engram init`` / ``engram extract``).  The orchestrator loads the project's
    graph store and starts a REPL that keeps a sliding history window,
    retrieves relevant graph context on every turn, and compacts old messages
    back into the graph automatically.

    \b
    Example:
        engram orchestrate my_project
        engram orchestrate my_project --config ./config.yaml -v
    """
    _setup_logging(verbose)

    cfg = _load_cfg(config_path)
    db_path = get_db_path(cfg, project)

    if not db_path.exists():
        click.echo(
            f"Project {project!r} not found at {db_path}. "
            "Run `engram init <project>` first.",
            err=True,
        )
        sys.exit(1)

    store = GraphStore(db_path)
    try:
        conversation = Conversation(cfg=cfg, store=store, project_name=project)
        asyncio.run(_repl(conversation, stream=stream))
    finally:
        store.close()
