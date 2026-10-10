"""Speech-to-text abstraction.

Prefer local processing for privacy and latency. The runtime depends only on the
STTProvider Protocol (mirrors jarvis_shared's contract) so local (Whisper) or cloud
providers are interchangeable.

  * DevSTTProvider     -- returns text from a supplied source (stdin), zero deps.
  * WhisperSTTProvider -- local faster-whisper transcription (seam).
"""
from __future__ import annotations

from typing import Callable, Optional, Protocol, runtime_checkable

from jarvis_shared.audio import AudioInput, Transcript


@runtime_checkable
class STTProvider(Protocol):
    async def transcribe(self, audio: AudioInput) -> Transcript: ...


class DevSTTProvider:
    """Reads the user's utterance from a text source instead of a microphone.

    In the dev wake flow the same stdin line that triggered the wake word (or the
    next line) is the utterance. The runtime passes the captured text via
    AudioInput.wav_path as a convenience channel to keep the Protocol intact.
    """

    def __init__(self, text_source: Optional[Callable[[], str]] = None) -> None:
        self._text_source = text_source

    async def transcribe(self, audio: AudioInput) -> Transcript:
        # Dev convenience: text was stuffed into wav_path by the capturer.
        text = (audio.wav_path or "").strip()
        if not text and self._text_source:
            text = (self._text_source() or "").strip()
        return Transcript(text=text, confidence=1.0, is_final=True)


class WhisperSTTProvider:  # pragma: no cover - requires model + audio hardware
    """Local STT via faster-whisper. Loads a small model for low latency."""

    def __init__(self, model_size: str = "base") -> None:
        self._model_size = model_size
        self._model = None

    def _ensure(self):
        if self._model is None:
            from faster_whisper import WhisperModel  # type: ignore
            self._model = WhisperModel(self._model_size, device="cpu", compute_type="int8")
        return self._model

    async def transcribe(self, audio: AudioInput) -> Transcript:
        model = self._ensure()
        source = audio.wav_path
        if not source:
            raise ValueError("WhisperSTTProvider requires AudioInput.wav_path")
        segments, info = model.transcribe(source, language="en")
        text = " ".join(s.text for s in segments).strip()
        return Transcript(text=text, confidence=1.0, is_final=True,
                          language=info.language or "en")


def build_stt(engine: str, text_source: Optional[Callable[[], str]] = None) -> STTProvider:
    if engine == "whisper":
        return WhisperSTTProvider()
    return DevSTTProvider(text_source=text_source)
