"""Real local voice: microphone, offline wake word, offline STT, Windows TTS.

Everything here runs **on-device**. No audio ever leaves the machine -- Vosk does both
wake-word spotting and transcription locally, and pyttsx3 speaks through Windows SAPI.

Architecture note: a single `MicStream` owns the microphone. This enforces the rule
that only JARVIS owns the wake-word/mic lifecycle -- agents never open the device, and
there is exactly one consumer, so there are no mic conflicts or duplicate detections.

    MicStream (one device)
        |-- VoskWakeWordEngine   restricted grammar, low CPU, always listening
        `-- VoskSTTProvider      full grammar, only after wake fires
"""
from __future__ import annotations

import json
import os
import queue
import threading
import time
from typing import Callable, Optional

from jarvis_shared.audio import AudioInput, AudioResult, Transcript
from jarvis_shared.voice import VoiceSettings

SAMPLE_RATE = 16000
BLOCK = 4000  # 0.25s frames


# --------------------------------------------------------------------------------
# Microphone
# --------------------------------------------------------------------------------
class MicStream:
    """Single owner of the input device. Fans raw PCM frames into a queue.

    Started once at runtime boot and kept open; `paused` gates delivery so the user
    can mute without tearing down the device (and so the tray can reflect real state).
    """

    def __init__(self, device: Optional[int] = None) -> None:
        self._device = device
        self._q: queue.Queue[bytes] = queue.Queue(maxsize=50)
        self._stream = None
        self.paused = False
        self._open = False
        # Live input level (0..1), updated non-destructively so the overlay can render
        # real waveform motion without consuming frames the recognizers need.
        self.last_level = 0.0

    def start(self) -> None:
        import sounddevice as sd

        def _cb(indata, frames, time_info, status) -> None:
            if self.paused:
                self.last_level = 0.0
                return
            buf = bytes(indata)
            self._track_level(buf)
            try:
                self._q.put_nowait(buf)
            except queue.Full:
                pass  # drop frames rather than block the audio thread

        self._stream = sd.RawInputStream(
            samplerate=SAMPLE_RATE, blocksize=BLOCK, dtype="int16",
            channels=1, callback=_cb, device=self._device,
        )
        self._stream.start()
        self._open = True

    def _track_level(self, buf: bytes) -> None:
        """Cheap RMS of the frame, normalised and eased for display."""
        import array
        try:
            samples = array.array("h")
            samples.frombytes(buf)
            if not samples:
                return
            step = max(1, len(samples) // 256)         # subsample: keep it cheap
            chunk = samples[::step]
            acc = sum(s * s for s in chunk) / len(chunk)
            rms = (acc ** 0.5) / 32768.0
            level = min(1.0, (rms * 7.0) ** 0.75)      # perceptual curve
            self.last_level = self.last_level * 0.6 + level * 0.4
        except Exception:
            pass

    def read(self, timeout: float = 0.5) -> Optional[bytes]:
        try:
            return self._q.get(timeout=timeout)
        except queue.Empty:
            return None

    def drain(self) -> None:
        """Discard buffered audio (used before capturing a fresh utterance)."""
        while True:
            try:
                self._q.get_nowait()
            except queue.Empty:
                return

    def stop(self) -> None:
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
        self._open = False

    @property
    def is_open(self) -> bool:
        return self._open


# --------------------------------------------------------------------------------
# Shared Vosk model (loaded once; ~40MB)
# --------------------------------------------------------------------------------
_MODEL = None
_MODEL_LOCK = threading.Lock()


def get_model():
    global _MODEL
    with _MODEL_LOCK:
        if _MODEL is None:
            import vosk
            vosk.SetLogLevel(-1)
            _MODEL = vosk.Model(lang="en-us")
        return _MODEL


# --------------------------------------------------------------------------------
# Wake word (local, low CPU)
# --------------------------------------------------------------------------------
class VoskWakeWordEngine:
    """Offline wake-word spotting with a restricted grammar.

    A tiny grammar (`hey jarvis` / `jarvis` / [unk]) keeps CPU low and sharply reduces
    false positives versus full transcription. Sensitivity maps to how permissive we
    are: at low sensitivity we require the full phrase, at high sensitivity the bare
    name is enough. A debounce window suppresses double-fires.
    """

    def __init__(self, wake_phrase: str = "hey jarvis", sensitivity: float = 0.5,
                 mic: Optional[MicStream] = None) -> None:
        self.wake_phrase = wake_phrase.lower().strip()
        self.sensitivity = max(0.0, min(1.0, sensitivity))
        self.mic = mic or MicStream()
        self._paused = False
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._on_wake: Optional[Callable[[], None]] = None
        self._last_fire = 0.0
        # Suspended while we capture an utterance / speak, so JARVIS doesn't wake itself.
        self.suspended = False
        # Energy gate: a real spoken wake phrase always carries audible level. Near
        # silence that the recogniser force-aligns to the grammar is rejected here.
        self.min_level = float(os.environ.get("JARVIS_WAKE_MIN_LEVEL", "0.045"))
        self._level_peak = 0.0

    @property
    def name(self) -> str:
        return "vosk-local"

    @property
    def paused(self) -> bool:
        return self._paused

    def pause(self) -> None:
        self._paused = True
        self.mic.paused = True

    def resume(self) -> None:
        self._paused = False
        self.mic.paused = False

    def stop(self) -> None:
        self._stop.set()
        self.mic.stop()

    def start(self, on_wake: Callable[[], None]) -> None:
        self._on_wake = on_wake
        if not self.mic.is_open:
            self.mic.start()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="wake-vosk")
        self._thread.start()

    def _grammar(self) -> str:
        words = [self.wake_phrase, "jarvis", "hey jarvis", "[unk]"]
        return json.dumps(sorted(set(words)))

    def _loop(self) -> None:
        from vosk import KaldiRecognizer

        rec = KaldiRecognizer(get_model(), SAMPLE_RATE, self._grammar())
        rec.SetWords(False)

        while not self._stop.is_set():
            if self._paused or self.suspended:
                time.sleep(0.05)
                continue
            frame = self.mic.read(timeout=0.3)
            if not frame:
                continue

            # Track the loudest moment in the recent window the phrase spans.
            self._level_peak = max(self._level_peak * 0.90, self.mic.last_level)

            hit_text = ""
            if rec.AcceptWaveform(frame):
                hit_text = json.loads(rec.Result()).get("text", "")
            else:
                hit_text = json.loads(rec.PartialResult()).get("partial", "")

            if self._matches(hit_text):
                now = time.time()
                if now - self._last_fire < 1.5:   # debounce
                    continue
                if self._level_peak < self.min_level:
                    # Grammar matched near-silence -> almost certainly a false
                    # positive. Reset and keep listening.
                    rec = KaldiRecognizer(get_model(), SAMPLE_RATE, self._grammar())
                    continue
                self._last_fire = now
                self._level_peak = 0.0
                rec = KaldiRecognizer(get_model(), SAMPLE_RATE, self._grammar())
                if self._on_wake:
                    self._on_wake()

    def _matches(self, text: str) -> bool:
        """False-positive protection.

        A restricted grammar makes Vosk force-align *any* sound to its nearest token,
        so ambient noise readily produces a bare "jarvis". We therefore require the
        complete two-word phrase by default, and only accept the bare name at high
        sensitivity, where the user has explicitly opted into hair-trigger wake.
        """
        t = (text or "").strip().lower()
        # Strip the unknown-token marker the grammar emits for non-matching audio.
        t = t.replace("[unk]", " ").strip()
        if not t:
            return False

        words = t.split()
        # Full phrase ("hey jarvis") -- the reliable signal.
        if self.wake_phrase in t:
            return True
        # Bare name: opt-in only, and it must be the whole utterance so that stray
        # noise inside longer speech doesn't trigger a wake.
        if self.sensitivity >= 0.8 and words == ["jarvis"]:
            return True
        return False


# --------------------------------------------------------------------------------
# Speech-to-text (local)
# --------------------------------------------------------------------------------
class VoskSTTProvider:
    """Offline transcription of the post-wake utterance.

    `capture_utterance` pulls from the same MicStream and stops on Vosk's end-of-
    utterance signal, a silence window, or a hard max duration -- so a user who trails
    off doesn't hang the turn.
    """

    def __init__(self, mic: MicStream, max_seconds: float = 12.0,
                 silence_seconds: float = 1.4) -> None:
        self.mic = mic
        self.max_seconds = max_seconds
        self.silence_seconds = silence_seconds

    def capture_utterance(self) -> str:
        """Blocking capture of one spoken request. Returns transcribed text."""
        from vosk import KaldiRecognizer

        rec = KaldiRecognizer(get_model(), SAMPLE_RATE)
        rec.SetWords(False)
        self.mic.drain()

        started = time.time()
        last_voice = time.time()
        partial_seen = ""
        final_text = ""

        while time.time() - started < self.max_seconds:
            frame = self.mic.read(timeout=0.4)
            if not frame:
                if partial_seen and time.time() - last_voice > self.silence_seconds:
                    break
                continue

            if rec.AcceptWaveform(frame):
                chunk = json.loads(rec.Result()).get("text", "").strip()
                if chunk:
                    final_text = (final_text + " " + chunk).strip()
                    last_voice = time.time()
                    # Vosk marked end of utterance and we have content -> done.
                    break
            else:
                p = json.loads(rec.PartialResult()).get("partial", "").strip()
                if p and p != partial_seen:
                    partial_seen = p
                    last_voice = time.time()
                elif partial_seen and time.time() - last_voice > self.silence_seconds:
                    break

        if not final_text:
            tail = json.loads(rec.FinalResult()).get("text", "").strip()
            final_text = tail or partial_seen

        return final_text.strip()

    async def transcribe(self, audio: AudioInput) -> Transcript:
        # Honors the STTProvider Protocol. The runtime's capture step already produced
        # text via `capture_utterance`, which arrives here on `wav_path`.
        text = (audio.wav_path or "").strip()
        return Transcript(text=text, confidence=1.0, is_final=True)


# --------------------------------------------------------------------------------
# Text-to-speech (Windows SAPI, distinct voice per agent)
# --------------------------------------------------------------------------------
# Windows ships a limited set of voices, so agents are differentiated by pairing a
# base voice with a distinct rate/volume. These are original synthetic
# characterisations -- not impersonations of any real person or performance.
VOICE_MAP: dict[str, dict] = {
    "jarvis_default":      {"gender": "male",   "rate": 168, "volume": 1.0},
    "friday_default":      {"gender": "female", "rate": 192, "volume": 1.0},
    "vision_default":      {"gender": "male",   "rate": 172, "volume": 0.95},
    "natasha_default":     {"gender": "female", "rate": 180, "volume": 1.0},
    "wong_default":        {"gender": "male",   "rate": 150, "volume": 0.95},
    "tony_default":        {"gender": "male",   "rate": 205, "volume": 1.0},
    "shuri_default":       {"gender": "female", "rate": 200, "volume": 1.0},
    "quicksilver_default": {"gender": "female", "rate": 228, "volume": 1.0},
    "fury_default":        {"gender": "male",   "rate": 152, "volume": 1.0},
    "strange_default":     {"gender": "male",   "rate": 160, "volume": 0.95},
}


class SapiTTSProvider:
    """Offline Windows speech with per-agent voice characteristics.

    A fresh engine is created per utterance: pyttsx3's `runAndWait` loop cannot be
    safely reused across threads, and this also makes `stop()` (barge-in) reliable.
    """

    def __init__(self) -> None:
        import pyttsx3
        probe = pyttsx3.init()
        voices = probe.getProperty("voices")
        self._male = next((v.id for v in voices if "david" in v.name.lower()),
                          voices[0].id if voices else None)
        self._female = next((v.id for v in voices if "zira" in v.name.lower()),
                            voices[-1].id if voices else None)
        try:
            probe.stop()
        except Exception:
            pass
        self._engine = None
        self._lock = threading.Lock()
        self._stopped = threading.Event()

    async def synthesize(self, text: str, voice_id: str,
                         settings: VoiceSettings) -> AudioResult:
        return AudioResult(voice_id=voice_id, spoken_text=text,
                           duration_ms=max(400, len(text) * 55))

    def speak(self, result: AudioResult) -> None:
        import pyttsx3

        prof = VOICE_MAP.get(result.voice_id, VOICE_MAP["jarvis_default"])
        self._stopped.clear()
        print(f"\n[SPEAKING {result.voice_id}] {result.spoken_text}")

        with self._lock:
            try:
                engine = pyttsx3.init()
                self._engine = engine
                vid = self._female if prof["gender"] == "female" else self._male
                if vid:
                    engine.setProperty("voice", vid)
                engine.setProperty("rate", prof["rate"])
                engine.setProperty("volume", prof["volume"])
                engine.say(result.spoken_text)
                engine.runAndWait()
            except Exception as exc:
                print(f"[tts error] {exc}")
            finally:
                try:
                    if self._engine:
                        self._engine.stop()
                except Exception:
                    pass
                self._engine = None

    def stop(self) -> None:
        """Immediate barge-in: cut playback now (local, low latency)."""
        self._stopped.set()
        eng = self._engine
        if eng is not None:
            try:
                eng.stop()
            except Exception:
                pass
