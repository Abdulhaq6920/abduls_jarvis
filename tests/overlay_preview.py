"""Visual preview of the centre-screen wave overlay.

Cycles through every state so you can see the HUD without talking. Auto-closes.

    python tests/overlay_preview.py
"""
from __future__ import annotations

import math
import threading
import time

from jarvis_local_agent.overlay import WaveOverlay

SCRIPT = [
    ("listening", "",            "Speak now",                      3.0),
    ("thinking",  "",            '"ask shuri to research langgraph"', 2.5),
    ("speaking",  "JARVIS",      "SHURI will handle that.",        2.5),
    ("speaking",  "SHURI",       "I've completed the research…",   3.5),
    ("speaking",  "TONY",        "Checking your project…",         2.5),
    ("speaking",  "NATASHA",     "This needs your approval.",      2.5),
    ("stopped",   "",            "",                               1.5),
]


def drive(ov: WaveOverlay) -> None:
    time.sleep(0.6)
    for state, agent, caption, hold in SCRIPT:
        print(f"  -> {state:10} {agent}")
        ov.show(state, agent=agent, caption=caption)
        end = time.time() + hold
        while time.time() < end:
            # fake a voice envelope so the waves move
            t = time.time() * 4
            amp = 0.35 + 0.4 * abs(math.sin(t)) * (0.6 + 0.4 * math.sin(t * 0.7))
            ov.feed_amplitude(amp if state in ("listening", "speaking") else 0.05)
            time.sleep(0.03)
    ov.hide()
    time.sleep(1.0)
    print("  -> done")
    ov.quit()


if __name__ == "__main__":
    print("Overlay preview — watch the centre of your screen:")
    overlay = WaveOverlay()
    threading.Thread(target=drive, args=(overlay,), daemon=True).start()
    overlay.run()
