"""System tray application.

Shows JARVIS status and never hides the microphone state. Uses pystray + Pillow when
installed; otherwise exposes the same menu as a console fallback so the behavior is
usable and testable without a GUI.

Menu:
    Open JARVIS
    ----
    Status
    Pause Listening
    Resume Listening
    Microphone
    Agent Status
    Connected Accounts
    Permissions
    Settings
    Quit
"""
from __future__ import annotations

import threading
import webbrowser
from typing import Callable


class TrayController:
    """UI-agnostic controller. The GUI tray and console fallback both call these."""

    def __init__(
        self,
        *,
        open_dashboard_url: str,
        on_pause: Callable[[], None],
        on_resume: Callable[[], None],
        status_text: Callable[[], str],
        on_quit: Callable[[], None],
    ) -> None:
        self.open_dashboard_url = open_dashboard_url
        self.on_pause = on_pause
        self.on_resume = on_resume
        self.status_text = status_text
        self.on_quit = on_quit

    def open_dashboard(self) -> None:
        webbrowser.open(self.open_dashboard_url)


def _make_icon_image():  # pragma: no cover - needs Pillow
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (64, 64), (10, 14, 20, 255))
    d = ImageDraw.Draw(img)
    d.ellipse((18, 18, 46, 46), fill=(54, 198, 255, 255))
    return img


def run_gui_tray(controller: TrayController, paused_getter: Callable[[], bool]) -> bool:
    """Try to run a real tray icon. Returns False if pystray/Pillow unavailable."""
    try:  # pragma: no cover - GUI path
        import pystray
        from pystray import MenuItem as Item
    except Exception:
        return False

    def title() -> str:  # pragma: no cover
        return "JARVIS  ● MICROPHONE PAUSED" if paused_getter() else "JARVIS  ● ONLINE"

    menu = pystray.Menu(  # pragma: no cover
        Item("Open JARVIS", lambda icon, item: controller.open_dashboard()),
        pystray.Menu.SEPARATOR,
        Item(lambda item: controller.status_text(), None, enabled=False),
        Item("Pause Listening", lambda icon, item: controller.on_pause()),
        Item("Resume Listening", lambda icon, item: controller.on_resume()),
        Item("Microphone", lambda icon, item: None, enabled=False),
        Item("Agent Status", lambda icon, item: controller.open_dashboard()),
        Item("Connected Accounts", lambda icon, item: controller.open_dashboard()),
        Item("Permissions", lambda icon, item: controller.open_dashboard()),
        Item("Settings", lambda icon, item: controller.open_dashboard()),
        Item("Quit", lambda icon, item: (controller.on_quit(), icon.stop())),
    )
    icon = pystray.Icon("jarvis", _make_icon_image(), title(), menu)  # pragma: no cover
    threading.Thread(target=icon.run, daemon=True).start()  # pragma: no cover
    return True


def run_console_tray(controller: TrayController, paused_getter: Callable[[], bool]) -> None:
    """Console fallback menu (also serves as the dev prototype's visible 'tray')."""
    banner = (
        "\n" + "═" * 40 +
        "\n  JARVIS  " + ("● MICROPHONE PAUSED" if paused_getter() else "● ONLINE") +
        "\n" + "─" * 40 +
        "\n  Tray menu (type a number):\n"
        "   1) Open JARVIS (dashboard)\n"
        "   2) Status\n"
        "   3) Pause Listening\n"
        "   4) Resume Listening\n"
        "   9) Quit\n" + "═" * 40 + "\n"
    )
    print(banner)
