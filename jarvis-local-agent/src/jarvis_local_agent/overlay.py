"""Floating "speaking light" overlay -- the on-screen face of JARVIS.

A frameless, always-on-top, transparent HUD that pops into the CENTER of the screen
when the wake word fires and fades away when the turn ends. It renders a glowing core
with concentric pulse rings and a symmetric waveform, tinted per state:

    LISTENING  cyan     waves driven by live mic amplitude
    THINKING   amber    slow orbiting pulse, waves settle
    SPEAKING   agent    waves driven by speech rhythm; shows the agent's name
    STOPPED    red      brief flash on barge-in

Design constraints that matter:
  * Click-through + no-activate (WS_EX_TRANSPARENT | WS_EX_NOACTIVATE) so it never
    steals focus or clicks while you keep working in VS Code, Chrome, etc.
  * Tk must own the main thread, so all state changes arrive through a thread-safe
    queue drained inside the animation tick.
  * Pure stdlib (tkinter) -- no GUI dependency to install.
"""
from __future__ import annotations

import math
import queue
import random
import sys
import time
import tkinter as tk
from dataclasses import dataclass
from typing import Optional

# Key colour used for per-pixel transparency on Windows. Must be a colour we never
# actually draw with.
CHROMA = "#010203"

W, H = 620, 300           # overlay size
BARS = 44                 # waveform bar count
FPS_MS = 16               # ~60fps

STATE_COLORS = {
    "listening": ("#36c6ff", "#0a4a63"),
    "thinking":  ("#ffb020", "#5a3c05"),
    "speaking":  ("#39d98a", "#0b4a2c"),
    "stopped":   ("#ff5c6c", "#5a1118"),
    "idle":      ("#6f8aa6", "#1b2733"),
}

# Per-agent accent so you can see *who* is talking at a glance.
AGENT_COLORS = {
    "JARVIS":      ("#36c6ff", "#0a4a63"),
    "FRIDAY":      ("#7ee0c0", "#0b4639"),
    "VISION":      ("#c9a6ff", "#2e1b52"),
    "NATASHA":     ("#ff8fa3", "#5a1b28"),
    "WONG":        ("#ffcf6b", "#5a4210"),
    "TONY":        ("#ff9d4d", "#5a2f0c"),
    "SHURI":       ("#9af0ff", "#0b4552"),
    "QUICKSILVER": ("#d7e3ff", "#2b3550"),
    "NICK_FURY":   ("#ff6b6b", "#521414"),
    "DR_STRANGE":  ("#8ad6a8", "#13492f"),
}


@dataclass
class _Cmd:
    kind: str                 # "state" | "hide" | "quit" | "amp"
    state: str = "idle"
    agent: str = ""
    caption: str = ""
    amp: float = 0.0


class WaveOverlay:
    """Thread-safe handle + Tk renderer for the centre-screen speaking light."""

    def __init__(self) -> None:
        self._q: queue.Queue[_Cmd] = queue.Queue()
        self._root: Optional[tk.Tk] = None
        self._canvas: Optional[tk.Canvas] = None

        # animation state
        self._state = "idle"
        self._agent = ""
        self._caption = ""
        self._visible = False
        self._alpha = 0.0           # 0..1 fade
        self._target_alpha = 0.0
        self._t = 0.0
        self._levels = [0.08] * BARS
        self._live_amp = 0.0
        self._amp_decay = 0.0
        # Set once teardown starts so no queued animation tick touches dead widgets.
        self._dead = False
        self._after_id: Optional[str] = None

    # ---------------------------------------------------------------- public API
    # These are safe to call from any thread.
    def show(self, state: str, agent: str = "", caption: str = "") -> None:
        self._q.put(_Cmd("state", state=state, agent=agent, caption=caption))

    def hide(self) -> None:
        self._q.put(_Cmd("hide"))

    def feed_amplitude(self, amp: float) -> None:
        """Drive the waveform from real mic/speech level (0..1)."""
        self._q.put(_Cmd("amp", amp=amp))

    def quit(self) -> None:
        self._q.put(_Cmd("quit"))

    # ------------------------------------------------------------------ Tk setup
    def run(self) -> None:
        """Blocking. MUST be called on the main thread."""
        root = tk.Tk()
        self._root = root
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        root.configure(bg=CHROMA)
        try:
            root.attributes("-transparentcolor", CHROMA)
        except tk.TclError:
            pass  # non-Windows: falls back to a solid panel

        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        x, y = (sw - W) // 2, int(sh * 0.40) - H // 2      # centred, slightly high
        root.geometry(f"{W}x{H}+{x}+{y}")

        self._canvas = tk.Canvas(root, width=W, height=H, bg=CHROMA,
                                 highlightthickness=0, bd=0)
        self._canvas.pack()

        root.update_idletasks()
        self._make_click_through()
        root.attributes("-alpha", 0.0)

        self._tick()
        root.mainloop()

    def _make_click_through(self) -> None:
        """Let clicks pass straight through and never take focus."""
        if sys.platform != "win32":
            return
        try:
            import ctypes
            hwnd = ctypes.windll.user32.GetParent(self._root.winfo_id())
            if not hwnd:
                hwnd = self._root.winfo_id()
            GWL_EXSTYLE = -20
            WS_EX_TRANSPARENT = 0x00000020
            WS_EX_LAYERED = 0x00080000
            WS_EX_NOACTIVATE = 0x08000000
            cur = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            ctypes.windll.user32.SetWindowLongW(
                hwnd, GWL_EXSTYLE,
                cur | WS_EX_TRANSPARENT | WS_EX_LAYERED | WS_EX_NOACTIVATE,
            )
        except Exception:
            pass

    # ------------------------------------------------------------------ main loop
    def _tick(self) -> None:
        if self._dead:
            return
        self._drain()
        if self._dead or not self._root:
            return
        self._animate()
        self._draw()
        if not self._dead and self._root:
            self._after_id = self._root.after(FPS_MS, self._tick)

    def _teardown(self) -> None:
        self._dead = True
        root, self._root = self._root, None
        self._canvas = None
        if root is not None:
            try:
                if self._after_id:
                    root.after_cancel(self._after_id)
            except Exception:
                pass
            try:
                root.quit()
                root.destroy()
            except Exception:
                pass

    def _drain(self) -> None:
        while True:
            try:
                cmd = self._q.get_nowait()
            except queue.Empty:
                return
            if cmd.kind == "quit":
                self._teardown()
                return
            if cmd.kind == "amp":
                self._live_amp = max(0.0, min(1.0, cmd.amp))
                self._amp_decay = time.time()
            elif cmd.kind == "hide":
                self._target_alpha = 0.0
            elif cmd.kind == "state":
                self._state = cmd.state
                if cmd.agent:
                    self._agent = cmd.agent
                self._caption = cmd.caption
                self._target_alpha = 1.0

    def _animate(self) -> None:
        self._t += FPS_MS / 1000.0

        # fade toward target
        d = self._target_alpha - self._alpha
        self._alpha += max(-0.08, min(0.08, d))
        self._alpha = max(0.0, min(1.0, self._alpha))
        self._visible = self._alpha > 0.01
        if self._root and not self._dead:
            try:
                self._root.attributes("-alpha", round(self._alpha, 3))
            except tk.TclError:
                pass

        # stale amplitude decays so bars relax when audio stops
        if time.time() - self._amp_decay > 0.25:
            self._live_amp *= 0.90

        # drive the waveform per state
        for i in range(BARS):
            centre_bias = 1.0 - abs(i - (BARS - 1) / 2) / ((BARS - 1) / 2)
            centre_bias = 0.25 + 0.75 * (centre_bias ** 1.4)

            if self._state == "listening":
                base = 0.10 + self._live_amp * 1.25
                wob = 0.5 + 0.5 * math.sin(self._t * 7 + i * 0.5)
                target = base * centre_bias * (0.55 + 0.75 * wob)
            elif self._state == "thinking":
                wob = 0.5 + 0.5 * math.sin(self._t * 2.2 + i * 0.28)
                target = (0.08 + 0.16 * wob) * centre_bias
            elif self._state == "speaking":
                amp = max(self._live_amp, 0.45)
                wob = (0.5 + 0.5 * math.sin(self._t * 11 + i * 0.7)) * \
                      (0.6 + 0.4 * math.sin(self._t * 3.1 + i * 0.13))
                target = (0.14 + amp * 0.95 * wob) * centre_bias
                target += random.uniform(-0.03, 0.03)
            elif self._state == "stopped":
                target = 0.06 * centre_bias
            else:
                target = 0.05 * centre_bias

            # critically-damped-ish smoothing
            self._levels[i] += (max(0.02, target) - self._levels[i]) * 0.35

    # ---------------------------------------------------------------------- draw
    def _draw(self) -> None:
        c = self._canvas
        if self._dead or not c:
            return
        try:
            c.delete("all")
        except tk.TclError:
            return      # canvas went away mid-teardown
        if not self._visible:
            return

        fg, dim = self._palette()
        cx, cy = W / 2, H / 2 - 10

        # --- concentric pulse rings -------------------------------------------
        rings = 3
        for r in range(rings):
            phase = (self._t * 0.55 + r / rings) % 1.0
            rad = 40 + phase * 150
            if self._state == "thinking":
                rad = 46 + (0.5 + 0.5 * math.sin(self._t * 2 + r)) * 36
            width = max(1, int(3 * (1 - phase)))
            col = dim if phase > 0.45 else fg
            c.create_oval(cx - rad, cy - rad * 0.62, cx + rad, cy + rad * 0.62,
                          outline=col, width=width)

        # --- waveform ----------------------------------------------------------
        span = W - 110
        bw = span / BARS
        maxh = 86
        for i, lv in enumerate(self._levels):
            h = max(3.0, min(1.35, lv) * maxh)
            x = 55 + i * bw + bw * 0.22
            x2 = x + bw * 0.56
            # brighter toward the centre of the wave
            near = 1.0 - abs(i - (BARS - 1) / 2) / ((BARS - 1) / 2)
            col = fg if near > 0.33 else dim
            c.create_rectangle(x, cy - h, x2, cy + h, fill=col, outline="")

        # --- glowing core ------------------------------------------------------
        pulse = 0.5 + 0.5 * math.sin(self._t * (5.5 if self._state == "speaking" else 2.4))
        core = 15 + pulse * 7
        c.create_oval(cx - core * 2.1, cy - core * 2.1, cx + core * 2.1, cy + core * 2.1,
                      fill="", outline=dim, width=2)
        c.create_oval(cx - core, cy - core, cx + core, cy + core, fill=fg, outline="")
        c.create_oval(cx - core * 0.45, cy - core * 0.45, cx + core * 0.45,
                      cy + core * 0.45, fill="#ffffff", outline="")

        # --- labels ------------------------------------------------------------
        label, sub = self._labels()
        c.create_text(cx, cy + 108, text=label, fill=fg,
                      font=("Segoe UI Semibold", 17), anchor="c")
        if sub:
            c.create_text(cx, cy + 134, text=sub, fill="#9fb3c8",
                          font=("Segoe UI", 10), anchor="c")

    def _palette(self) -> tuple[str, str]:
        if self._state == "speaking" and self._agent in AGENT_COLORS:
            return AGENT_COLORS[self._agent]
        return STATE_COLORS.get(self._state, STATE_COLORS["idle"])

    def _labels(self) -> tuple[str, str]:
        if self._state == "listening":
            return "L I S T E N I N G", self._caption or "Speak now"
        if self._state == "thinking":
            return "T H I N K I N G", self._caption or "Routing your request"
        if self._state == "speaking":
            name = self._agent.replace("_", " ") or "JARVIS"
            return f"{name}", self._caption or "Speaking…"
        if self._state == "stopped":
            return "S T O P P E D", ""
        return "J A R V I S", 'Say "Hey JARVIS"'


def run_overlay_in_main_thread(overlay: WaveOverlay) -> None:
    overlay.run()
