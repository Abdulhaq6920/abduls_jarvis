"""Audio I/O contracts shared by STT and TTS layers."""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class AudioInput(BaseModel):
    """Raw captured audio handed to an STTProvider.

    We keep bytes out of the JSON wire format where possible; the local agent passes
    AudioInput in-process. `sample_rate` and `encoding` let a provider decode/resample.
    """

    sample_rate: int = 16000
    channels: int = 1
    encoding: str = "pcm_s16le"
    # In-process only; not serialized across the network.
    pcm: Optional[bytes] = Field(default=None, repr=False, exclude=True)
    # Optional path to a wav file as an alternative to in-memory pcm.
    wav_path: Optional[str] = None
    duration_ms: int = 0


class Transcript(BaseModel):
    """Result of speech-to-text."""

    text: str
    confidence: float = Field(1.0, ge=0.0, le=1.0)
    is_final: bool = True
    language: str = "en-US"


class AudioResult(BaseModel):
    """Result of text-to-speech.

    Either `wav_path` (file the local agent can play) or in-process `pcm`. For dev TTS
    we may also carry `spoken_text` so a headless/CI run can assert on content without
    audio hardware.
    """

    voice_id: str
    sample_rate: int = 22050
    wav_path: Optional[str] = None
    pcm: Optional[bytes] = Field(default=None, repr=False, exclude=True)
    spoken_text: str = ""
    duration_ms: int = 0
