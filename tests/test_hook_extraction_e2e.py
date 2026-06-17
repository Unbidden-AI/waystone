"""End-to-end WRITE-path test: drive the real hook all the way until nodes are
extracted into the database, then confirm the extracted nodes are correct.

The companion ``test_hook_injection_e2e.py`` covers the READ path (retrieval +
injection). It deliberately does NOT trigger extraction: extraction is a
*detached background worker*, and it only fires when either

  1. the transcript has ≥ MIN_ASSISTANT_WORDS of new assistant text since the
     watermark (``_spawn_extraction(..., source="assistant")``) — this CAN fire
     on the very first turn, or
  2. the user-prompt buffer flushes after enough turns/words accumulate.

So a single sub-threshold prompt with no transcript (the injection test's
setup) never spawns a worker and never writes nodes.

This module closes that gap: it feeds the hook a real transcript with assistant
text, lets the detached worker run a REAL LLM extraction, polls until the worker
finishes, and asserts the resulting graph contains the correct facts.

Because it makes a real LLM call, it is OPT-IN: it is skipped unless
``LLM_API_KEY`` or ``GEMINI_API_KEY`` is set. It never runs in default CI and
never burns credit by accident. Defaults target Gemini 2.5 Flash (the project's
benchmark model); override the endpoint/model with ``WAYSTONE_E2E_BASE_URL`` /
``WAYSTONE_E2E_MODEL`` for an OpenAI-compatible local server.
"""

import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest

from waystone.store import GraphStore

HOOK_MODULE = "waystone._hooks.submit"

LLM_KEY = os.environ.get("LLM_API_KEY") or os.environ.get("GEMINI_API_KEY")
BASE_URL = os.environ.get("WAYSTONE_E2E_BASE_URL",
                          "https://generativelanguage.googleapis.com/v1beta/openai")
MODEL = os.environ.get("WAYSTONE_E2E_MODEL", "gemini-2.5-flash")

requires_llm = pytest.mark.skipif(
    not LLM_KEY,
    reason="real-LLM extraction e2e; set LLM_API_KEY or GEMINI_API_KEY to run",
)

# A specific, unambiguous decision so extraction reliably produces a node that
# mentions the key entities — that's what lets us assert correctness, not just
# "something got written". ≥ 40 words to clear MIN_ASSISTANT_WORDS.
ASSISTANT_TURN = (
    "We made a key architecture decision today: we are migrating the primary "
    "cache layer from Redis to Memcached. The reason is that Redis kept hitting "
    "memory pressure and evicting keys under peak load, which caused cache misses "
    "and latency spikes. Memcached's slab allocator gives us more predictable "
    "memory behavior. The migration is owned by the platform team and is "
    "scheduled for the third quarter."
)


def _nid() -> str:
    return f"n_{uuid.uuid4().hex[:8]}"


def _write_config(home: Path) -> None:
    cfg_dir = home / ".waystone"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "config.yaml").write_text(
        "llm:\n"
        f"  base_url: {BASE_URL}\n"
        f"  model: {MODEL}\n"
        f"  api_key: {LLM_KEY}\n"
        f"projects_dir: {cfg_dir / 'projects'}\n",
        encoding="utf-8",
    )


def _project_cwd(tmp_path: Path, project: str) -> Path:
    work = tmp_path / "work"
    work.mkdir(exist_ok=True)
    (work / ".waystone").write_text(project, encoding="utf-8")
    return work


def _write_transcript(path: Path, assistant_text: str) -> None:
    """A Claude Code transcript line as the hook's _read_assistant_since expects:
    JSONL with message.role == 'assistant' and a list of text content blocks."""
    line = {
        "message": {
            "role": "assistant",
            "content": [{"type": "text", "text": assistant_text}],
        }
    }
    path.write_text(json.dumps(line) + "\n", encoding="utf-8")


def _run_hook(home: Path, cwd: Path, prompt: str, *, session_id: str,
              transcript_path: str) -> subprocess.CompletedProcess:
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
    return subprocess.run(
        [sys.executable, "-m", HOOK_MODULE],
        input=payload, capture_output=True, text=True,
        cwd=str(cwd), env=env, timeout=60,
    )


def _state(home: Path, session_id: str) -> dict:
    p = home / ".waystone" / "state" / f"{session_id}.json"
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _wait_for_extraction(home: Path, db_path: Path, session_id: str,
                         timeout: float = 150.0) -> list[dict]:
    """Block until the detached worker finishes (state: extracting=False with a
    node count) or the graph is non-empty, then return the active nodes."""
    deadline = time.time() + timeout
    last_err = None
    while time.time() < deadline:
        st = _state(home, session_id)
        last_err = st.get("extract_error")
        done = (st.get("extracting") is False) and ("last_extract_nodes" in st)
        if db_path.exists():
            store = GraphStore(db_path, vec_enabled=False)
            try:
                count = store.get_stats()["node_count"]
                if count > 0 and (done or count > 0):
                    nodes = store.get_all_nodes()
                    # If the worker is still mid-merge, keep waiting a beat.
                    if done or nodes:
                        return nodes
            finally:
                store.close()
        if last_err:
            pytest.fail(f"extraction worker reported an error: {last_err}")
        time.sleep(2)
    pytest.fail(f"extraction did not complete within {timeout}s "
                f"(state={_state(home, session_id)})")


@pytest.fixture
def env_home(tmp_path: Path) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    _write_config(home)
    return home


@pytest.mark.parametrize("module,extra_args", [
    # worker is spawned detached by submit.py / posttool.py; needs project args.
    # With empty stdin it hits the empty-text guard and exits 0 before any DB/LLM.
    ("waystone._hooks.worker", ["--project", "t", "--db-path", "/tmp/guard-unused.db"]),
    # stop re-spawns itself detached; ENGRAM_STOP_BG=1 makes it do work inline.
    ("waystone._hooks.stop", []),
])
def test_spawned_hook_modules_run_without_import_error(module, extra_args):
    """Regression guard (no LLM): the detached-spawn targets must be runnable
    the way the hooks invoke them — as `-m <module>`, with package context.

    These modules do `from .._logging import hook_entry` at import time. They
    were once spawned as bare script paths (`python /path/worker.py`), which
    raises 'attempted relative import with no known parent package' and killed
    the worker silently (stdout/stderr → DEVNULL). Run as a module they import
    cleanly; with empty stdin they exit 0 via their empty-input guard.

    We ALSO assert the bare-script form STILL fails on the relative import — so
    if anyone reverts a spawn to a bare script path, this documents exactly why
    `-m` is mandatory (and the guard stays meaningful).
    """
    env = dict(os.environ)
    env["ENGRAM_STOP_BG"] = "1"  # stop.py: skip the re-spawn, do the work inline

    # The way the hooks now invoke it — must NOT raise ImportError, must exit 0.
    as_module = subprocess.run(
        [sys.executable, "-m", module, *extra_args],
        input="", capture_output=True, text=True, env=env, timeout=60,
    )
    assert "attempted relative import" not in as_module.stderr, as_module.stderr
    assert as_module.returncode == 0, f"{module} -m failed: {as_module.stderr}"

    # The old, broken form — proves the import context is what matters.
    script_path = subprocess.run(
        [sys.executable, "-c",
         f"import importlib.util as u; print(u.find_spec('{module}').origin)"],
        capture_output=True, text=True,
    ).stdout.strip()
    as_script = subprocess.run(
        [sys.executable, script_path, *extra_args],
        input="", capture_output=True, text=True, env=env, timeout=60,
    )
    assert "attempted relative import" in as_script.stderr, (
        f"expected the bare-script form of {module} to fail on the relative "
        f"import; if this no longer fails the regression guard is moot:\n{as_script.stderr}"
    )


@requires_llm
def test_hook_extracts_assistant_turn_into_db(tmp_path, env_home):
    """Full write path: real hook → detached worker → real LLM extraction →
    nodes merged into the local graph → assert the nodes are correct."""
    project = "extract-demo"
    work = _project_cwd(tmp_path, project)
    session_id = "sess-extract"

    transcript = tmp_path / "transcript.jsonl"
    _write_transcript(transcript, ASSISTANT_TURN)

    db_path = env_home / ".waystone" / "projects" / project / "context.db"

    proc = _run_hook(env_home, work, "summarize what we decided",
                     session_id=session_id, transcript_path=str(transcript))
    # The hook itself must succeed and return promptly; the worker is detached.
    assert proc.returncode == 0, proc.stderr

    nodes = _wait_for_extraction(env_home, db_path, session_id)

    # 1. Something was actually extracted.
    assert nodes, "no nodes were extracted into the database"

    # 2. The extracted facts are CORRECT — they capture the Redis→Memcached
    #    migration that the assistant turn described.
    facts = " \n".join(n["fact"].lower() for n in nodes)
    assert "memcached" in facts, f"extracted nodes missed the decision; got:\n{facts}"
    assert "redis" in facts, f"extracted nodes missed the prior state; got:\n{facts}"

    # 3. Nodes are well-formed: valid type from the extractor palette + sane confidence.
    palette = {
        "decision", "transition", "constraint", "implementation",
        "question", "resolved", "lesson_learned", "preference",
    }
    for n in nodes:
        assert n["type"] in palette, f"unexpected node type: {n['type']}"
        assert 0.0 <= float(n.get("confidence", 0.5)) <= 1.0

    # 4. The migration decision should be captured as a decision/transition.
    assert any(n["type"] in {"decision", "transition"} for n in nodes), \
        "the migration was not captured as a decision/transition"

    # 5. State reflects a completed extraction with a positive node count.
    st = _state(env_home, session_id)
    assert st.get("extracting") is False
    assert st.get("last_extract_nodes", 0) > 0
