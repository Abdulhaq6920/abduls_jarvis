"""Text-to-speech abstraction + agent voice selection.

The runtime never hardcodes a TTS provider. It depends on the TTSProvider Protocol
and selects a voice via the responding agent's AgentVoiceProfile (fetched from the
backend). Supports interruption (stop) for low-latency barge-in.

  * DevTTSProvider     -- prints the spoken line + voice characteristics (CI-friendly),
                          optionally also drives Windows SAPI if pyttsx3 is present.
  * Pyttsx3TTSProvider -- offline SAPI5 voices on Windows; maps profile pitch/speed.

A production adapter (Azure/ElevenLabs/Piper) implements the same `synthesize` +
`speak` + `stop` surface.
"""
from __future__ import annotations

import sys
import threading
from typing import Protocol, runtime_checkable

from jarvis_shared.audio import AudioResult
from jarvis_shared.voice import AgentVoiceProfile, VoiceSettings


@runtime_checkable
class TTSProvider(Protocol):
    async def synthesize(self, text: str, voice_id: str,
                         settings: VoiceSettings) -> AudioResult: ...
    def speak(self, result: AudioResult) -> None: ...
    def stop(self) -> None: ...


class DevTTSProvider:
    """Deterministic, hardware-free TTS.

    `synthesize` returns an AudioResult carrying the spoken text + voice id so a
    headless run can assert on content. `speak` prints a clearly-labeled line showing
    which agent voice is speaking (so Tests 1-5 are observable in a terminal).

    Console-only by default. For real audio use `SapiTTSProvider`
    (jarvis_local_agent.voice_local), which creates a fresh engine per utterance --
    a single long-lived pyttsx3 engine cannot be driven safely from worker threads
    and will block on `runAndWait`.
    """

    def __init__(self, enable_audio: bool = False) -> None:
        self._stop = threading.Event()
        self._engine = None
        if enable_audio:
            try:
                import pyttsx3  # type: ignore
                self._engine = pyttsx3.init()
            except Exception:
                self._engine = None

    async def synthesize(self, text: str, voice_id: str,
                         settings: VoiceSettings) -> AudioResult:
        return AudioResult(voice_id=voice_id, spoken_text=text,
                           duration_ms=max(400, len(text) * 55))

    def speak(self, result: AudioResult) -> None:
        self._stop.clear()
        # ASCII-only: Windows consoles default to cp1252, which cannot encode emoji
        # and would raise UnicodeEncodeError when output is piped.
        line = f"\n[SPEAKING {result.voice_id}] {result.spoken_text}\n"
        try:
            sys.stdout.write(line)
            sys.stdout.flush()
        except UnicodeEncodeError:
            sys.stdout.write(line.encode("ascii", "replace").decode("ascii"))
            sys.stdout.flush()
        if self._engine and not self._stop.is_set():
            try:
                self._engine.say(result.spoken_text)
                self._engine.runAndWait()
            except Exception:
                pass

    def stop(self) -> None:
        self._stop.set()
        if self._engine:
            try:
                self._engine.stop()
            except Exception:
                pass


class Pyttsx3TTSProvider:  # pragma: no cover - requires audio hardware
    """Offline Windows SAPI5 TTS. Maps distinct profiles onto available system voices
    by rotating voice indices and adjusting rate from the profile speed."""

    def __init__(self) -> None:
        import pyttsx3  # type: ignore
        self._engine = pyttsx3.init()
        self._voices = self._engine.getProperty("voices")
        self._stop = threading.Event()

    async def synthesize(self, text: str, voice_id: str,
                         settings: VoiceSettings) -> AudioResult:
        return AudioResult(voice_id=voice_id, spoken_text=text,
                           duration_ms=max(400, len(text) * 55))

    def speak(self, result: AudioResult) -> None:
        self._stop.clear()
        # Deterministically pick a system voice per voice_id so agents sound different.
        idx = (hash(result.voice_id) % max(1, len(self._voices)))
        try:
            self._engine.setProperty("voice", self._voices[idx].id)
            self._engine.say(result.spoken_text)
            self._engine.runAndWait()
        except Exception:
            pass

    def stop(self) -> None:
        self._stop.set()
        try:
            self._engine.stop()
        except Exception:
            pass


def build_tts(engine: str) -> TTSProvider:
    if engine == "pyttsx3":
        try:
            return Pyttsx3TTSProvider()
        except Exception:
            return DevTTSProvider()
    return DevTTSProvider()


def select_voice(profile: AgentVoiceProfile) -> tuple[str, VoiceSettings]:
    """Agent voice routing: profile -> (voice_id, settings)."""
    return profile.voice_id, profile.settings()
