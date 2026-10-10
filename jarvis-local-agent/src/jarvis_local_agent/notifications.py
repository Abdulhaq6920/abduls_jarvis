"""Desktop notifications.

JARVIS can notify the user even when the dashboard is closed (task complete, approval
needed, failure). Uses win10toast when available; otherwise prints a clearly-formatted
notification block so the behavior is observable in a terminal/CI.
"""
from __future__ import annotations

import sys


class Notifier:
    def __init__(self) -> None:
        self._toaster = None
        try:  # pragma: no cover - optional dependency
            from win10toast_click import ToastNotifier  # type: ignore
            self._toaster = ToastNotifier()
        except Exception:
            self._toaster = None

    def notify(self, title: str, message: str, action: str = "") -> None:
        if self._toaster:  # pragma: no cover - needs Windows
            try:
                self._toaster.show_toast(title, message, duration=6, threaded=True)
                return
            except Exception:
                pass
        # ASCII-only frame: Windows consoles default to cp1252, which cannot encode
        # box-drawing characters and raises UnicodeEncodeError when piped.
        rule = "-" * 44
        block = (f"\n+{rule}+"
                 f"\n| {title}"
                 f"\n| {message}"
                 + (f"\n| [{action}]" if action else "")
                 + f"\n+{rule}+")
        try:
            sys.stdout.write(block + "\n")
            sys.stdout.flush()
        except UnicodeEncodeError:
            sys.stdout.write(block.encode("ascii", "replace").decode("ascii") + "\n")
            sys.stdout.flush()
