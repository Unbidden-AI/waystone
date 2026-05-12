"""Graceful-degradation error hierarchy for the Engram OpenClaw skill.

All errors degrade gracefully — the skill catches these and logs/returns
user-friendly messages rather than crashing OpenClaw.
"""

from __future__ import annotations


class EngramOpenClawError(Exception):
    """Base class for all Engram OpenClaw errors."""


class DBInitError(EngramOpenClawError):
    """Raised when the GraphStore cannot be opened or initialized.

    Common causes: DB file missing, permissions error, SQLite corruption.
    The skill degrades to MEMORY.md-only mode when this is raised.
    """


class LLMExtractionError(EngramOpenClawError):
    """Raised when the LLM extraction call fails.

    Common causes: LLM endpoint unreachable, non-JSON response, token budget
    exceeded. The skill buffers the text and retries on next session end.
    """


class MemoryMDCorruptError(EngramOpenClawError):
    """Raised when MEMORY.md cannot be read, parsed, or written.

    Common causes: encoding errors, filesystem full, concurrent write conflict.
    The skill skips the MEMORY.md sync step rather than crashing.
    """


class ConfigError(EngramOpenClawError):
    """Raised when required configuration is missing or invalid.

    Common cause: ENGRAM_PROJECT not set, config.yaml not found.
    """
