"""Waystone Claude Code hook entrypoints (submit / stop / posttool / worker)."""

import sys


def detached_popen_kwargs() -> dict:
    """subprocess.Popen kwargs that detach a spawned child so it outlives the hook.

    ``start_new_session`` is POSIX-only and raises ValueError on Windows. There
    the equivalent is creationflags DETACHED_PROCESS (0x8) | CREATE_NEW_PROCESS_GROUP
    (0x200). Centralised so every hook spawn detaches the same way on both OSes.
    """
    if sys.platform == "win32":
        return {"creationflags": 0x00000008 | 0x00000200}
    return {"start_new_session": True}
