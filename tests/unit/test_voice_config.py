"""Configurable Vosk model language/path, wake grammar derivation, and SAPI voice hint.

These exercise pure config + string logic only; the mic device is never started and the
vosk import stays lazy, so no model download or audio hardware is needed here.
"""
import json

from jarvis_local_agent.config import RuntimeConfig


# ------------------------------------------------------------------- config env pickup
def test_config_defaults_when_unset(monkeypatch):
    for var in ("JARVIS_VOSK_MODEL_LANG", "JARVIS_VOSK_MODEL_PATH", "JARVIS_TTS_VOICE_HINT"):
        monkeypatch.delenv(var, raising=False)
    cfg = RuntimeConfig(_env_file=None)
    assert cfg.vosk_model_lang == "en-us"
    assert cfg.vosk_model_path == ""
    assert cfg.tts_voice_hint == ""


def test_config_reads_vosk_env(monkeypatch):
    monkeypatch.setenv("JARVIS_VOSK_MODEL_LANG", "fr")
    monkeypatch.setenv("JARVIS_VOSK_MODEL_PATH", r"C:\models\vosk-fr")
    monkeypatch.setenv("JARVIS_TTS_VOICE_HINT", "hortense")
    cfg = RuntimeConfig(_env_file=None)
    assert cfg.vosk_model_lang == "fr"
    assert cfg.vosk_model_path.endswith("vosk-fr")
    assert cfg.tts_voice_hint == "hortense"


def test_config_reads_no_speech_timeout(monkeypatch):
    monkeypatch.delenv("JARVIS_NO_SPEECH_TIMEOUT_S", raising=False)
    assert RuntimeConfig(_env_file=None).no_speech_timeout_s == 3.0
    monkeypatch.setenv("JARVIS_NO_SPEECH_TIMEOUT_S", "2")
    assert RuntimeConfig(_env_file=None).no_speech_timeout_s == 2.0


def test_stt_accepts_no_speech_window():
    """The STT provider carries a no-speech window so a false/empty wake idles quickly
    (returning empty) instead of blocking the full max_seconds. Constructing it must not
    open a mic or load a model (both are lazy)."""
    from jarvis_local_agent.voice_local import VoskSTTProvider

    stt = VoskSTTProvider(object(), max_seconds=12.0, no_speech_seconds=0.5)
    assert stt.no_speech_seconds == 0.5
    assert stt.no_speech_seconds < stt.max_seconds


# ------------------------------------------------------------------- wake grammar
def test_wake_grammar_derives_from_custom_phrase():
    # Constructing the engine does not open the mic or import vosk; _grammar() is pure.
    from jarvis_local_agent.voice_local import MicStream, VoskWakeWordEngine

    engine = VoskWakeWordEngine(wake_phrase="ok jarvis", mic=MicStream())
    tokens = json.loads(engine._grammar())
    assert "ok jarvis" in tokens
    assert "ok" in tokens and "jarvis" in tokens
    assert "[unk]" in tokens
    assert "hey jarvis" not in tokens


def test_wake_grammar_default_phrase_keeps_unk():
    from jarvis_local_agent.voice_local import MicStream, VoskWakeWordEngine

    engine = VoskWakeWordEngine(mic=MicStream())
    tokens = json.loads(engine._grammar())
    assert "hey jarvis" in tokens and "[unk]" in tokens


# ------------------------------------------------------------------- SAPI voice hint
class _FakeVoice:
    def __init__(self, name, vid="", languages=None):
        self.name = name
        self.id = vid or name
        self.languages = languages or []


def test_voice_hint_prefers_matching_voice():
    from jarvis_local_agent.voice_local import SapiTTSProvider

    voices = [_FakeVoice("Microsoft David"), _FakeVoice("Microsoft Hortense Desktop"),
              _FakeVoice("Microsoft Zira")]
    assert SapiTTSProvider._select_voice(voices, "david", "hortense", None) == "Microsoft Hortense Desktop"


def test_voice_hint_falls_back_to_english_when_no_match():
    from jarvis_local_agent.voice_local import SapiTTSProvider

    voices = [_FakeVoice("Microsoft David"), _FakeVoice("Microsoft Zira")]
    assert SapiTTSProvider._select_voice(voices, "david", "nonexistent", None) == "Microsoft David"
    assert SapiTTSProvider._select_voice(voices, "zira", "", None) == "Microsoft Zira"
