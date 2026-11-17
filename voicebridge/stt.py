"""Speech-to-text engines.

* ``whisper`` - faster-whisper, runs locally (private, works offline once the
  model is downloaded, can auto-detect the spoken language).
* ``google``  - Google's free web speech service through the SpeechRecognition
  package. Needs internet and a known source language, but no model download
  and no native libraries, so it also works where local Whisper cannot load.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass

import numpy as np

from . import languages

SAMPLE_RATE = 16000  # every engine receives 16 kHz mono float32


@dataclass
class Transcript:
    text: str
    language: str | None  # detected or assumed language code


class WhisperSTT:
    name = "whisper"

    def __init__(self, model: str = "small", device: str = "cpu", compute_type: str = "int8") -> None:
        from faster_whisper import WhisperModel  # heavy import; may fail on locked-down PCs

        self.model_name = model
        self._model = WhisperModel(model, device=device, compute_type=compute_type)
        self._lock = threading.Lock()  # one transcription at a time per model

    def transcribe(self, audio: np.ndarray, language: str | None) -> Transcript:
        with self._lock:
            segments, info = self._model.transcribe(
                audio,
                language=language,
                beam_size=1,
                vad_filter=True,
                condition_on_previous_text=False,
                without_timestamps=True,
            )
            parts = [s.text.strip() for s in segments if s.no_speech_prob < 0.6]
        text = " ".join(p for p in parts if p).strip()
        return Transcript(text, language or info.language)


class GoogleSTT:
    name = "google"

    def __init__(self) -> None:
        import speech_recognition as sr

        self._sr = sr
        self._recognizer = sr.Recognizer()

    def transcribe(self, audio: np.ndarray, language: str | None) -> Transcript:
        if not language:
            raise ValueError(
                "The Google speech engine needs the spoken language; auto-detect "
                "only works with the Whisper engine."
            )
        lang = languages.get(language)
        locale = lang.locale if lang else language
        pcm = (np.clip(audio, -1.0, 1.0) * 32767).astype("<i2").tobytes()
        data = self._sr.AudioData(pcm, SAMPLE_RATE, 2)
        try:
            text = self._recognizer.recognize_google(data, language=locale)
        except self._sr.UnknownValueError:  # nothing intelligible
            text = ""
        except self._sr.RequestError as exc:
            raise RuntimeError(f"Google speech service error: {exc}") from exc
        return Transcript(str(text).strip(), language)


def create(engine: str = "auto", whisper_model: str = "small") -> tuple[object, str]:
    """Create an engine. Returns (engine, note) where note explains fallbacks."""
    engine = engine.lower()
    if engine == "google":
        return GoogleSTT(), "Speech recognition: Google (online)."
    if engine == "whisper":
        return WhisperSTT(whisper_model), f"Speech recognition: Whisper '{whisper_model}' (local)."
    if engine != "auto":
        raise ValueError(f"Unknown speech engine '{engine}' (use auto, whisper or google).")
    try:
        return WhisperSTT(whisper_model), f"Speech recognition: Whisper '{whisper_model}' (local)."
    except Exception as exc:  # ImportError, blocked DLL, failed download...
        reason = str(exc).splitlines()[0] if str(exc) else type(exc).__name__
        return GoogleSTT(), f"Whisper unavailable ({reason}); using Google speech (online)."
