"""Waystone — DAG-based context intelligence for LLM workflows."""

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version

try:
    # Single source of truth: the installed package version (from pyproject), so the
    # API server's /v1/health and the CLI never disagree about the version.
    __version__ = _pkg_version("waystone")
except PackageNotFoundError:  # running from a source tree without an install
    __version__ = "0.0.0+source"
