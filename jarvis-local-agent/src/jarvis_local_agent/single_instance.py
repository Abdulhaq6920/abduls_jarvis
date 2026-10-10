"""Allow only one JARVIS runtime per Windows session.

Two runtimes would open the microphone twice, hear each other, and answer every request
twice. A named kernel mutex is released automatically by Windows if the process dies, so a
crash can never leave a stale lock behind (unlike a lock file).
"""
from __future__ import annotations

import sys

_handle = None  # keep a reference for the life of the process
_NAME = "Local\\JARVIS-voice-runtime"
_ERROR_ALREADY_EXISTS = 183


def acquire() -> bool:
    """True if we are the only instance (always True off Windows)."""
    global _handle
    if sys.platform != "win32":
        return True
    import ctypes

    kernel32 = ctypes.windll.kernel32
    kernel32.SetLastError(0)
    _handle = kernel32.CreateMutexW(None, False, _NAME)
    return kernel32.GetLastError() != _ERROR_ALREADY_EXISTS
