"""End-to-end last-mile test: does the real UserPromptSubmit hook actually
INJECT the right context into a prompt?

This closes the gap left by ``selfcheck --deep`` and the existing hook unit
tests:

  * ``selfcheck --deep`` feeds the hook a *synthetic* stdin payload and only
    checks that it exits 0 (doesn't crash).
  * ``test_session_narrative_injection.py`` exercises the injection *helper*
    (``_latest_session_narrative``) in-process, not the real hook.

Neither proves the thing a buyer actually depends on: that when Claude Code
spawns the hook as a subprocess with a real prompt, the hook reaches into a
real graph and returns the *relevant facts* in ``additionalContext``.

These tests invoke the real hook the way Claude Code does — ``python -m
waystone._hooks.submit`` with the exact JSON-on-stdin contract — against a
seeded graph in a fully isolated ``$HOME``, then assert the seeded facts come
back out in the injected context.

Tier B (``test_real_claude_session_injects_context``) drives the genuine
``claude -p`` binary and is skipped unless ``WAYSTONE_E2E_CLAUDE=1`` and
``claude`` is on PATH — the injection fires at prompt-submit time, *before* the
model call, so it is asserted even when the model reply itself is unavailable
(e.g. no account credit).
"""

import json
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

from waystone.store import GraphStore

HOOK_MODULE = "waystone._hooks.submit"


def _nid() -> str:
    return f"n_{uuid.uuid4().hex[:8]}"


def _write_config(home: Path) -> None:
    """Minimal local config in an isolated HOME. The LLM endpoint is a dead
    address on purpose — retrieval/injection is fully offline, so a reachable
    LLM is never needed; if the hook ever tried to call it the test would hang,
    which is exactly the regression we'd want to catch."""
    cfg_dir = home / ".waystone"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "config.yaml").write_text(
        "llm:\n"
        "  base_url: http://127.0.0.1:1/v1\n"
        "  model: test-model\n"
        "  api_key: test-key\n"
        f"projects_dir: {cfg_dir / 'projects'}\n",
        encoding="utf-8",
    )


def _seed_graph(home: Path, project: str, nodes: list[dict]) -> Path:
    """Seed the project's graph where the hook will actually read it.

    dedup_threshold=1.1 disables semantic collapse so distinct-but-similar
    seed nodes survive locally (an embedder present on a dev box would
    otherwise merge them — see the semantic-dedup seeding note)."""
    db_path = home / ".waystone" / "projects" / project / "context.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    store = GraphStore(db_path, dedup_threshold=1.1, vec_enabled=False)
    try:
        for node in nodes:
            store.add_node(node)
    finally:
        store.close()
    return db_path


def _project_cwd(tmp_path: Path, project: str) -> Path:
    """A clean working dir marked with a .waystone file so the hook detects
    the project — and so the subprocess's relative ``config.yaml`` search
    finds nothing but our isolated config."""
    work = tmp_path / "work"
    work.mkdir(exist_ok=True)
    (work / ".waystone").write_text(project, encoding="utf-8")
    return work


def _run_hook(home: Path, cwd: Path, prompt: str, *, session_id: str = "sess-e2e",
              transcript_path: str = "") -> tuple[subprocess.CompletedProcess, dict | None]:
    payload = json.dumps({
        "prompt": prompt,
        "cwd": str(cwd),
        "transcript_path": transcript_path,
        "session_id": session_id,
    })
    env = dict(os.environ)
    env["HOME"] = str(home)
    # Force the local sqlite path regardless of the dev shell's env.
    for k in ("WAYSTONE_STORE_BACKEND", "WAYSTONE_DATABASE_URL", "DATABASE_URL"):
        env.pop(k, None)
    proc = subprocess.run(
        [sys.executable, "-m", HOOK_MODULE],
        input=payload, capture_output=True, text=True,
        cwd=str(cwd), env=env, timeout=60,
    )
    parsed: dict | None = None
    out = proc.stdout.strip()
    if out:
        # The hook prints exactly one JSON object on injection. Be tolerant of
        # any stray leading lines by scanning from the bottom for valid JSON.
        for line in reversed(out.splitlines()):
            try:
                parsed = json.loads(line)
                break
            except json.JSONDecodeError:
                continue
    return proc, parsed


def _injected_context(parsed: dict | None) -> str:
    assert parsed is not None, "hook produced no injection JSON"
    hso = parsed["hookSpecificOutput"]
    assert hso["hookEventName"] == "UserPromptSubmit"
    return hso["additionalContext"]


@pytest.fixture
def env_home(tmp_path: Path) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    _write_config(home)
    return home


# ── Tier A: deterministic, offline, CI-safe ────────────────────────────────

def test_submit_hook_injects_relevant_seeded_fact(tmp_path, env_home):
    """The real hook retrieves a seeded fact relevant to the prompt and returns
    it in additionalContext — the actual install→work payoff."""
    project = "demo"
    _seed_graph(env_home, project, [
        {
            "id": _nid(),
            "fact": "We switched the cache backend from Redis to Memcached for lower latency.",
            "type": "decision",
            "confidence": 0.95,
            "tags": ["cache", "backend", "redis", "memcached", "switch"],
        },
        # an unrelated node that should NOT dominate the relevant one
        {
            "id": _nid(),
            "fact": "The frontend is built with SvelteKit.",
            "type": "implementation",
            "confidence": 0.9,
            "tags": ["frontend", "sveltekit"],
        },
    ])
    work = _project_cwd(tmp_path, project)

    proc, parsed = _run_hook(env_home, work, "what cache backend did we switch to?")

    assert proc.returncode == 0, proc.stderr
    ctx = _injected_context(parsed)
    assert "Memcached" in ctx, f"relevant fact not injected; context was:\n{ctx}"
    assert f"project '{project}'" in ctx

    # The hook also persists the exact injected context for `waystone last-context`.
    last_ctx = env_home / ".waystone" / "projects" / project / "last_context.md"
    assert last_ctx.exists()
    assert "Memcached" in last_ctx.read_text(encoding="utf-8")


def test_submit_hook_injects_session_narrative(tmp_path, env_home):
    """A session_summary node surfaces via the dedicated narrative path even
    when the prompt keywords don't match it (generic tags won't BFS in)."""
    project = "demo"
    _seed_graph(env_home, project, [{
        "id": _nid(),
        "fact": ("Goal: ship the Team Server. Currently wiring per-seat licensing. "
                 "Next: push the release commits."),
        "type": "session_summary",
        "source_transcript": "live_session:sess-e2e",
        "confidence": 1.0,
        "tags": ["rolling", "session"],
        "created_at": "2026-06-17T01:00:00",
        "occurred_at": "2026-06-17T01:00:00",
    }])
    work = _project_cwd(tmp_path, project)

    proc, parsed = _run_hook(env_home, work,
                             "completely unrelated question about widgets",
                             session_id="sess-e2e")

    assert proc.returncode == 0, proc.stderr
    ctx = _injected_context(parsed)
    assert "Where we are (session narrative)" in ctx
    assert "per-seat licensing" in ctx


def test_submit_hook_empty_graph_does_not_inject(tmp_path, env_home):
    """An empty graph must NOT fabricate context — the hook exits 0 silently."""
    project = "empty"
    _seed_graph(env_home, project, [])  # creates the db with zero nodes
    work = _project_cwd(tmp_path, project)

    proc, parsed = _run_hook(env_home, work, "anything at all")

    assert proc.returncode == 0, proc.stderr
    assert parsed is None, f"expected no injection on empty graph; stdout={proc.stdout!r}"


def test_submit_hook_irrelevant_prompt_is_safe(tmp_path, env_home):
    """A prompt with no graph overlap and no narrative still exits cleanly —
    either no injection, or a valid (non-crashing) payload."""
    project = "demo"
    _seed_graph(env_home, project, [{
        "id": _nid(),
        "fact": "We switched the cache backend from Redis to Memcached.",
        "type": "decision",
        "confidence": 0.95,
        "tags": ["cache", "backend", "redis", "memcached"],
    }])
    work = _project_cwd(tmp_path, project)

    proc, parsed = _run_hook(env_home, work, "xyzzy plugh nothing relates here")

    assert proc.returncode == 0, proc.stderr
    if parsed is not None:
        # If it injected anything, it must be a well-formed UserPromptSubmit block.
        assert parsed["hookSpecificOutput"]["hookEventName"] == "UserPromptSubmit"


# ── Tier B: the genuine volcano — real `claude -p` (opt-in) ─────────────────

@pytest.mark.skipif(
    os.environ.get("WAYSTONE_E2E_CLAUDE") != "1" or shutil.which("claude") is None,
    reason="real Claude Code session test; set WAYSTONE_E2E_CLAUDE=1 with `claude` on PATH",
)
def test_real_claude_session_injects_context(tmp_path, env_home):
    """Wire the hook into an isolated Claude Code config and run a real
    `claude -p` turn. The UserPromptSubmit hook fires *before* the model call,
    so the injected context (last_context.md) is written even if the model
    reply itself is unavailable (e.g. no account credit)."""
    project = "demo"
    _seed_graph(env_home, project, [{
        "id": _nid(),
        "fact": "We switched the cache backend from Redis to Memcached for lower latency.",
        "type": "decision",
        "confidence": 0.95,
        "tags": ["cache", "backend", "redis", "memcached", "switch"],
    }])
    work = _project_cwd(tmp_path, project)

    # Wire the hook exactly the way `waystone configure` would, but into the
    # isolated HOME so the real claude binary reads it.
    claude_dir = env_home / ".claude"
    claude_dir.mkdir(parents=True, exist_ok=True)
    command = f"{sys.executable} -m {HOOK_MODULE}"
    (claude_dir / "settings.json").write_text(json.dumps({
        "hooks": {
            "UserPromptSubmit": [
                {"hooks": [{"type": "command", "command": command}]}
            ]
        }
    }), encoding="utf-8")

    env = dict(os.environ)
    env["HOME"] = str(env_home)
    for k in ("WAYSTONE_STORE_BACKEND", "WAYSTONE_DATABASE_URL", "DATABASE_URL"):
        env.pop(k, None)

    # The model reply may fail (credit/auth) — we only need the hook to fire.
    try:
        subprocess.run(
            ["claude", "-p", "what cache backend did we switch to?"],
            cwd=str(work), env=env, capture_output=True, text=True, timeout=120,
        )
    except subprocess.TimeoutExpired:
        pass

    last_ctx = env_home / ".waystone" / "projects" / project / "last_context.md"
    assert last_ctx.exists(), "UserPromptSubmit hook did not fire in a real claude session"
    assert "Memcached" in last_ctx.read_text(encoding="utf-8")
