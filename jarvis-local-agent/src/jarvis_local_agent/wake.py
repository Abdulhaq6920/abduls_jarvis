"""Wake-word engine abstraction.

Only JARVIS owns the wake-word lifecycle. The engine runs *locally* and must be:
low-CPU when idle, protected against false positives, sensitivity-configurable,
pausable/resumable, and privacy-preserving (no audio leaves the machine before the
wake word fires).

Two implementations:
  * DevWakeWordEngine  -- type/enter driven, zero dependencies, for prototyping + CI.
  * PorcupineWakeWordEngine -- production seam using Picovoice Porcupine (local).

The runtime depends only on the WakeWordEngine Protocol, so swapping engines is a
config change (JARVIS_WAKE_ENGINE).
"""
from __future__ import annotations

import threading
import time
from typing import Callable, Optional, Protocol, runtime_checkable


@runtime_checkable
class WakeWordEngine(Protocol):
    def start(self, on_wake: Callable[[], None]) -> None: ...
    def pause(self) -> None: ...
    def resume(self) -> None: ...
    def stop(self) -> None: ...
    @property
    def paused(self) -> bool: ...
    @property
    def name(self) -> str: ...


class _BaseEngine:
    def __init__(self, wake_phrase: str, sensitivity: float) -> None:
        self.wake_phrase = wake_phrase.lower().strip()
        self.sensitivity = max(0.0, min(1.0, sensitivity))
        self._paused = False
        self._stop = threading.Event()
        self._on_wake: Optional[Callable[[], None]] = None

    @property
    def paused(self) -> bool:
        return self._paused

    def pause(self) -> None:
        self._paused = True

    def resume(self) -> None:
        self._paused = False

    def stop(self) -> None:
        self._stop.set()


class DevWakeWordEngine(_BaseEngine):
    """Development wake word: reads lines from a provided callable (default: stdin).

    When a typed line *contains* the wake phrase, it fires. This lets the whole
    pipeline run with no microphone, while matching the production contract exactly.
    False-positive protection here is a simple phrase-presence + debounce check.
    """

    def __init__(self, wake_phrase: str, sensitivity: float,
                 line_source: Optional[Callable[[], str]] = None) -> None:
        super().__init__(wake_phrase, sensitivity)
        self._line_source = line_source or _stdin_lines()
        self._thread: Optional[threading.Thread] = None
        self._last_fire = 0.0

    @property
    def name(self) -> str:
        return "dev"

    def start(self, on_wake: Callable[[], None]) -> None:
        self._on_wake = on_wake
        self._thread = threading.Thread(target=self._loop, daemon=True, name="wake-dev")
        self._thread.start()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                line = self._line_source()
            except (EOFError, OSError):
                time.sleep(0.2)
                continue
            if line is None:
                time.sleep(0.1)
                continue
            if self._paused:
                continue
            if self.wake_phrase in line.strip().lower():
                now = time.time()
                # debounce: suppress repeats within 1s (false-positive protection)
                if now - self._last_fire < 1.0:
                    continue
                self._last_fire = now
                if self._on_wake:
                    self._on_wake()


class PorcupineWakeWordEngine(_BaseEngine):
    """Production wake word via Picovoice Porcupine (fully local inference).

    Requires the `voice` extra (pvporcupine + sounddevice) and a Picovoice access key
    in JARVIS_PICOVOICE_KEY. Kept import-light so the module loads without the extra.
    """

    def __init__(self, wake_phrase: str, sensitivity: float, access_key: str = "") -> None:
        super().__init__(wake_phrase, sensitivity)
        self._access_key = access_key
        self._thread: Optional[threading.Thread] = None

    @property
    def name(self) -> str:
        return "porcupine"

    def start(self, on_wake: Callable[[], None]) -> None:  # pragma: no cover - needs hw
        import pvporcupine  # type: ignore
        import sounddevice as sd  # type: ignore

        self._on_wake = on_wake
        keyword = "jarvis"  # built-in keyword closest to the default phrase
        porcupine = pvporcupine.create(
            access_key=self._access_key, keywords=[keyword],
            sensitivities=[self.sensitivity],
        )

        def _loop() -> None:
            with sd.RawInputStream(samplerate=porcupine.sample_rate, blocksize=porcupine.frame_length,
                                   dtype="int16", channels=1) as stream:
                import struct
                while not self._stop.is_set():
                    if self._paused:
                        time.sleep(0.05)
                        continue
                    pcm = stream.read(porcupine.frame_length)[0]
                    frame = struct.unpack_from("h" * porcupine.frame_length, pcm)
                    if porcupine.process(frame) >= 0 and self._on_wake:
                        self._on_wake()

        self._thread = threading.Thread(target=_loop, daemon=True, name="wake-porcupine")
        self._thread.start()


def _stdin_lines() -> Callable[[], str]:
    import sys

    def _read() -> str:
        return sys.stdin.readline()

    return _read


def build_wake_engine(engine: str, wake_phrase: str, sensitivity: float,
                      line_source: Optional[Callable[[], str]] = None) -> WakeWordEngine:
    import os
    if engine == "porcupine":
        return PorcupineWakeWordEngine(wake_phrase, sensitivity,
                                       access_key=os.environ.get("JARVIS_PICOVOICE_KEY", ""))
    return DevWakeWordEngine(wake_phrase, sensitivity, line_source=line_source)
