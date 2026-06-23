"""Make-silent-failures-loud: detached background workers must capture their
stderr to a log, and `waystone doctor` must surface recent crashes.

This is the structural guard for the bug class behind the 0.4.42–0.4.46 silent
extraction failure: a detached worker that crashed at import time wrote its
traceback to stderr → DEVNULL, so nothing ever screamed. Here we prove:

  1. a crashing detached child's stderr lands in ~/.waystone/logs/<name>.log
     (not /dev/null),
  2. read_worker_log_errors() flags a RECENT crash and ignores a stale one,
  3. `waystone doctor` goes red and prints the failure when a worker log has a
     fresh traceback.
"""

import os
import subprocess
import sys
import time

import pytest
from click.testing import CliRunner

from waystone._logging import open_worker_log, read_worker_log_errors


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    h.mkdir()
    # Path.home() reads $HOME on POSIX but USERPROFILE on Windows — set both so
    # ~/.waystone/logs is isolated on every platform (else Windows writes to the
    # real home and these assertions read the wrong location).
    monkeypatch.setenv("HOME", str(h))
    monkeypatch.setenv("USERPROFILE", str(h))
    return h


def test_open_worker_log_captures_crashing_child_stderr(home):
    """A detached child that crashes at import time — exactly the original bug —
    must have its traceback captured to the worker log, not lost to DEVNULL."""
    err = open_worker_log("worker")
    assert hasattr(err, "close"), "expected a writable log handle, got DEVNULL"
    proc = subprocess.Popen(
        [sys.executable, "-c", "from .._nope import x"],  # ImportError at import
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=err,
    )
    proc.wait(timeout=30)
    err.close()

    log = home / ".waystone" / "logs" / "worker.log"
    assert log.exists(), "worker log was not created"
    content = log.read_text(encoding="utf-8")
    assert "Traceback" in content or "Error" in content, content


def test_read_worker_log_errors_flags_recent_crash(home):
    log_dir = home / ".waystone" / "logs"
    log_dir.mkdir(parents=True)
    (log_dir / "worker.log").write_text(
        "Traceback (most recent call last):\n"
        "  File \"worker.py\", line 26, in <module>\n"
        "ImportError: attempted relative import with no known parent package\n",
        encoding="utf-8",
    )
    summary = read_worker_log_errors()
    assert summary, "a recent worker crash should be reported"
    assert "worker.log" in summary
    assert "relative import" in summary


def test_read_worker_log_errors_clean_when_empty(home):
    log_dir = home / ".waystone" / "logs"
    log_dir.mkdir(parents=True)
    # Success path writes nothing to the stderr log.
    (log_dir / "worker.log").write_text("", encoding="utf-8")
    assert read_worker_log_errors() == ""


def test_read_worker_log_errors_ignores_stale_crash(home):
    """A long-since-fixed crash must not keep doctor red forever."""
    log_dir = home / ".waystone" / "logs"
    log_dir.mkdir(parents=True)
    log = log_dir / "worker.log"
    log.write_text("Traceback (most recent call last):\nImportError: boom\n",
                   encoding="utf-8")
    # Backdate mtime well beyond the freshness window.
    old = time.time() - 30 * 86400
    os.utime(log, (old, old))
    assert read_worker_log_errors(max_age_days=7) == ""


def test_doctor_reports_worker_crash(home, monkeypatch):
    """End-to-end through the real CLI: a fresh worker traceback makes
    `waystone doctor` fail with a visible, named check."""
    log_dir = home / ".waystone" / "logs"
    log_dir.mkdir(parents=True)
    (log_dir / "worker.log").write_text(
        "Traceback (most recent call last):\n"
        "ImportError: attempted relative import with no known parent package\n",
        encoding="utf-8",
    )
    # Minimal config so doctor proceeds past the config gate.
    cfg_dir = home / ".waystone"
    (cfg_dir / "config.yaml").write_text(
        "llm:\n  base_url: http://127.0.0.1:1/v1\n  model: m\n  api_key: k\n",
        encoding="utf-8",
    )
    from waystone.cli import cli
    result = CliRunner().invoke(cli, ["doctor"])
    assert "Background workers (extraction/stop) not crashing" in result.output
    # The check must be marked failed (✗), and the offending log surfaced.
    assert "✗  Background workers" in result.output, result.output
    assert "passive capture may be failing" in result.output
