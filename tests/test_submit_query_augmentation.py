"""Test that submit hook's query augmentation with recent turns works end-to-end.

Verifies that when query_context_turns is enabled, the hook builds a retrieval
query from recent turns + session narrative + prompt, and this improves retrieval
on short/contentless prompts.
"""

import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

from waystone.store import GraphStore

HOOK_MODULE = "waystone._hooks.submit"


def _nid() -> str:
    return f"n_{uuid.uuid4().hex[:8]}"


def _write_config(home: Path, *, query_context_turns: int = 0) -> None:
    """Write test config with query_context_turns setting."""
    cfg_dir = home / ".waystone"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    config_text = (
        "llm:\n"
        "  base_url: http://127.0.0.1:1/v1\n"
        "  model: test-model\n"
        "  api_key: test-key\n"
        f"projects_dir: {cfg_dir / 'projects'}\n"
        "incremental:\n"
        f"  query_context_turns: {query_context_turns}\n"
    )
    (cfg_dir / "config.yaml").write_text(config_text, encoding="utf-8")


def _seed_graph(home: Path, project: str, nodes: list[dict]) -> Path:
    """Seed test graph."""
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
    """Create a clean working directory marked with .waystone file."""
    work = tmp_path / "work"
    work.mkdir(exist_ok=True)
    (work / ".waystone").write_text(project, encoding="utf-8")
    return work


def _write_transcript(transcript_path: Path, turns: list[dict]) -> None:
    """Write a minimal JSONL transcript with turns."""
    with open(transcript_path, "w", encoding="utf-8") as f:
        for turn in turns:
            f.write(json.dumps(turn) + "\n")


def _run_hook(
    home: Path,
    cwd: Path,
    prompt: str,
    *,
    transcript_path: str = "",
    session_id: str = "sess-test",
) -> tuple[subprocess.CompletedProcess, dict | None]:
    """Run the real hook as a subprocess."""
    payload = json.dumps({
        "prompt": prompt,
        "cwd": str(cwd),
        "transcript_path": transcript_path,
        "session_id": session_id,
    })
    env = dict(os.environ)
    env["HOME"] = str(home)
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
    return home


def test_submit_hook_query_context_turns_disabled_is_noop(tmp_path, env_home):
    """When query_context_turns=0 (default), no augmentation occurs (direct keyword match only)."""
    _write_config(env_home, query_context_turns=0)

    project = "demo"
    _seed_graph(
        env_home, project, [
            {
                "id": _nid(),
                "fact": "JWT tokens are stateless for scalability.",
                "type": "decision",
                "confidence": 0.95,
                "tags": ["jwt", "auth"],
            },
            {
                "id": _nid(),
                "fact": "We use Redis for caching.",
                "type": "decision",
                "confidence": 0.95,
                "tags": ["redis", "cache"],
            },
        ]
    )

    work = _project_cwd(tmp_path, project)
    # Use a prompt with a direct keyword that will match a node
    proc, parsed = _run_hook(
        env_home, work,
        "Tell me about Redis",  # Keyword match — no augmentation needed
    )

    assert proc.returncode == 0, proc.stderr
    ctx = _injected_context(parsed)
    # With query_context_turns=0, only direct keyword matching works.
    # "Redis" is in the prompt, so the Redis node will be retrieved.
    assert "Redis" in ctx or "redis" in ctx.lower()


def test_submit_hook_query_context_turns_enabled(tmp_path, env_home):
    """When query_context_turns > 0, short prompts are augmented with recent turns."""
    _write_config(env_home, query_context_turns=2)

    project = "demo"
    _seed_graph(
        env_home, project, [
            {
                "id": _nid(),
                "fact": "JWT tokens are stateless for scalability.",
                "type": "decision",
                "confidence": 0.95,
                "tags": ["jwt", "auth", "token"],
            },
            {
                "id": _nid(),
                "fact": "We use RS256 signing with a 2048-bit RSA key.",
                "type": "implementation",
                "confidence": 0.9,
                "tags": ["jwt", "auth", "signature"],
            },
        ]
    )

    work = _project_cwd(tmp_path, project)

    # Write a transcript with recent turns discussing JWT
    transcript_path = work / "transcript.jsonl"
    _write_transcript(
        transcript_path, [
            {
                "message": {
                    "role": "user",
                    "content": [{"type": "text", "text": "What auth mechanism should we use?"}],
                }
            },
            {
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "JWT is stateless and scalable."}],
                }
            },
        ]
    )

    proc, parsed = _run_hook(
        env_home, work,
        "ok, let's go",  # Short, contentless prompt
        transcript_path=str(transcript_path),
    )

    assert proc.returncode == 0, proc.stderr
    ctx = _injected_context(parsed)
    # With augmentation enabled and JWT-related prior turns, the short prompt
    # should now surface JWT facts via the augmented query.
    # The augmented query includes the prior turn "What auth mechanism should we use?"
    # which contains keywords like "auth" and "mechanism" that will help surface JWT nodes.
    assert "JWT" in ctx or "jwt" in ctx.lower() or "token" in ctx.lower(), (
        f"Expected JWT-related content in augmented context, but got:\n{ctx}"
    )
