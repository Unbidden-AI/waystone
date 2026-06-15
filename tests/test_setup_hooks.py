"""Regression: `waystone configure` must wire Claude Code hooks for PIP installs.

The hooks/ directory is a repo artifact that does NOT ship in the wheel, so a
pip-installed user has no such directory. `install_hooks()` is designed for exactly
that case — it prefers the installed console-script entry points and only needs a
hook_dir as a repo-clone fallback. A bug gated the call on the directory existing,
so pip users silently got zero hooks. These tests pin the entry-point path.
"""

from __future__ import annotations

import json

import pytest

from waystone.setup import install_hooks


@pytest.fixture
def isolated_settings(tmp_path, monkeypatch):
    settings = tmp_path / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True)
    monkeypatch.setattr("waystone.setup.SETTINGS_PATH", settings)
    # Pretend the pip console scripts are installed (they are, in a real install).
    monkeypatch.setattr("shutil.which", lambda cmd: f"/usr/local/bin/{cmd}")
    return settings


def test_install_hooks_works_without_a_hook_dir(isolated_settings):
    """The pip-install path: no hook_dir, but entry points are present."""
    added, _ = install_hooks(None)  # must NOT raise
    data = json.loads(isolated_settings.read_text())
    events = data.get("hooks", {})
    # The integration hooks a new user needs are all wired…
    for ev in ("UserPromptSubmit", "Stop", "PostToolUse", "SessionStart"):
        assert ev in events, f"{ev} hook missing"
    # …and they point at the console-script entry points, not raw script paths.
    submit_cmd = events["UserPromptSubmit"][0]["hooks"][0]["command"]
    assert submit_cmd == "waystone-hook-submit"
    assert any("UserPromptSubmit" in a for a in added)


def test_install_hooks_raises_only_when_truly_uninstalled(tmp_path, monkeypatch):
    """With neither entry points nor a hook_dir, it fails loudly (not silently)."""
    settings = tmp_path / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True)
    monkeypatch.setattr("waystone.setup.SETTINGS_PATH", settings)
    monkeypatch.setattr("shutil.which", lambda cmd: None)
    with pytest.raises(RuntimeError):
        install_hooks(None)
