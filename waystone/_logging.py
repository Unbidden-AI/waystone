"""Central logging sinks for the package.

Every module logs via ``logging.getLogger(__name__)`` but configures no handlers
itself — these helpers attach the sink, scoped to the ``waystone`` logger so we
never capture third-party noise or fight uvicorn's root config:

- **Hooks / MCP** run detached inside the user's editor, where **stdout is the
  hook protocol** — so their logs must go to a ROTATING FILE under
  ``~/.waystone/logs/``, never to stdout. Setup is fail-safe: a logging error must
  never crash a hook (which has to stay fail-open).
- **Server / CLI** log to **stderr** (12-factor — Docker and operators capture it).

Verbosity comes from ``WAYSTONE_LOG_LEVEL`` (e.g. DEBUG/INFO/WARNING); defaults to
INFO for the file sink and WARNING for the stream sink.
"""

from __future__ import annotations

import logging
import logging.handlers
import os
from pathlib import Path

_FMT = "%(asctime)s %(levelname)s %(name)s %(message)s"
_LOGGER_NAME = "waystone"


def _env_level(default: int) -> int:
    name = os.environ.get("WAYSTONE_LOG_LEVEL", "").strip().upper()
    return getattr(logging, name, default) if name else default


def _has_tag(logger: logging.Logger, tag: str) -> bool:
    return any(getattr(h, "_waystone_tag", None) == tag for h in logger.handlers)


def setup_file_logging(name: str = "hooks", level: int | None = None) -> None:
    """Route the ``waystone`` logger to ``~/.waystone/logs/<name>.log`` (rotating,
    5MB x 3). Idempotent and NEVER raises — a logging failure must not break a
    detached hook whose contract is to fail open."""
    try:
        logger = logging.getLogger(_LOGGER_NAME)
        tag = f"file:{name}"
        if _has_tag(logger, tag):
            return
        log_dir = Path.home() / ".waystone" / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(
            log_dir / f"{name}.log", maxBytes=5 * 1024 * 1024, backupCount=3,
            encoding="utf-8",
        )
        handler.setFormatter(logging.Formatter(_FMT))
        handler._waystone_tag = tag  # type: ignore[attr-defined]
        logger.addHandler(handler)
        logger.setLevel(_env_level(level if level is not None else logging.INFO))
        logger.propagate = False  # don't double-emit to the root logger
    except Exception:
        pass  # logging must never break the caller


def hook_entry(fn):
    """Decorator for a hook's ``main()``: route logs to ``hooks.log`` and make ANY
    unhandled error fail OPEN (log it, then ``exit(0)``) so a hook can never crash
    or block the user's editor session. Explicit ``sys.exit(...)`` inside the hook
    is preserved (SystemExit re-raises)."""
    import functools
    import sys

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        setup_file_logging("hooks")
        try:
            return fn(*args, **kwargs)
        except SystemExit:
            raise
        except BaseException:
            try:
                # Log on the `waystone` logger itself so the record always reaches
                # the file sink regardless of the decorated function's module.
                logging.getLogger(_LOGGER_NAME).warning(
                    "hook %s failed — failing open",
                    getattr(fn, "__module__", "?"), exc_info=True)
            except Exception:
                pass
            sys.exit(0)  # fail-open: never break the editor session

    return wrapper


def _logs_dir() -> Path:
    return Path.home() / ".waystone" / "logs"


def open_worker_log(name: str = "worker"):
    """Open ``~/.waystone/logs/<name>.log`` (append, binary) for use as a detached
    subprocess's **stderr**.

    A detached worker that crashes at IMPORT time (before its ``@hook_entry``
    logger ever engages) writes its traceback to stderr — which used to go to
    DEVNULL and vanish. Capturing it here is what turns a silent background
    failure into a visible, diagnosable one (surfaced by ``waystone doctor``).

    Best-effort size cap (rotate at ~5MB). NEVER raises — falls back to
    ``subprocess.DEVNULL`` so a logging problem can't stop the spawn. The child
    process owns the returned fd; the parent should close its copy after Popen.
    """
    import subprocess
    try:
        log_dir = _logs_dir()
        log_dir.mkdir(parents=True, exist_ok=True)
        path = log_dir / f"{name}.log"
        try:
            if path.exists() and path.stat().st_size > 5 * 1024 * 1024:
                path.replace(path.with_name(f"{name}.log.1"))
        except Exception:
            pass
        return open(path, "ab")  # noqa: SIM115 — child process owns this fd
    except Exception:
        return subprocess.DEVNULL


def read_worker_log_errors(names=("worker", "stop", "summarize"),
                           max_age_days: float = 7.0,
                           tail_lines: int = 40) -> str:
    """Return a short summary of RECENT background-worker crashes, or "" if clean.

    These logs receive only a detached worker's stderr. Benign library warnings
    can appear there on success (e.g. sentence-transformers chatter), so we key
    on the precise crash signal — a Python ``Traceback`` — rather than broad
    "error"/"exception" substrings that would false-positive. We gate on file
    mtime (``max_age_days``) so a long-since-fixed crash doesn't keep ``doctor``
    red forever. Used by ``waystone doctor`` to make the silent-failure class loud.
    """
    import time
    markers = ("Traceback (most recent call last)", "attempted relative import")
    found: list[str] = []
    try:
        log_dir = _logs_dir()
        for name in names:
            path = log_dir / f"{name}.log"
            if not path.exists() or path.stat().st_size == 0:
                continue
            if (time.time() - path.stat().st_mtime) > max_age_days * 86400:
                continue  # stale crash — assume resolved
            try:
                lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            except Exception:
                continue
            tail = [ln for ln in lines[-tail_lines:] if ln.strip()]
            if any(any(m in ln for m in markers) for ln in tail):
                # The last line of a Python traceback is the exception type +
                # message — the most informative one-liner to show the operator.
                found.append(f"{name}.log: {tail[-1].strip()[:160]}")
    except Exception:
        return ""
    return "\n      ".join(found)


def setup_stream_logging(level: int | None = None) -> None:
    """Route the ``waystone`` logger to stderr (idempotent). For the CLI + API
    server, where the platform/operator captures stderr."""
    try:
        logger = logging.getLogger(_LOGGER_NAME)
        if _has_tag(logger, "stream"):
            return
        handler = logging.StreamHandler()  # stderr
        handler.setFormatter(logging.Formatter(_FMT))
        handler._waystone_tag = "stream"  # type: ignore[attr-defined]
        logger.addHandler(handler)
        logger.setLevel(_env_level(level if level is not None else logging.WARNING))
        logger.propagate = False
    except Exception:
        pass
