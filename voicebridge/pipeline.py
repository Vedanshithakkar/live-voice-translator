"""The live translation pipeline.

    capture -> utterances -> speech-to-text -> translate -> text-to-speech -> output(s)

Each arrow is a queue between threads, so the next sentence is being
recognised while the previous one is still being spoken.

Two modes use the same pipeline:
* speak  - your microphone -> translated voice into a virtual cable, which the
           meeting app uses as its microphone (others hear the translation).
* listen - what your speakers play (the meeting) -> translated captions, and
           optionally a translated voice in your own headphones.
"""
from __future__ import annotations

import queue
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Callable

import numpy as np

from . import audio, languages, stt, translate, tts
from .vad import UtteranceSegmenter

AUTO = languages.AUTO


class EventLog:
    """Thread-safe list of recent events (captions, status, errors)."""

    def __init__(self, maxlen: int = 500) -> None:
        self._events: deque[dict] = deque(maxlen=maxlen)
        self._next_id = 1
        self._lock = threading.Lock()
        self._listeners: list[Callable[[dict], None]] = []

    def add(self, mode: str, kind: str, text: str, **extra) -> dict:
        with self._lock:
            event = {"id": self._next_id, "time": time.time(), "mode": mode, "kind": kind, "text": text, **extra}
            self._next_id += 1
            self._events.append(event)
            listeners = list(self._listeners)
        for listener in listeners:
            listener(event)
        return event

    def since(self, last_id: int) -> list[dict]:
        with self._lock:
            return [e for e in self._events if e["id"] > last_id]

    def subscribe(self, listener: Callable[[dict], None]) -> None:
        with self._lock:
            self._listeners.append(listener)


class Engines:
    """Speech-to-text and translation engines shared by all pipelines."""

    def __init__(self, stt_engine: str = "auto", whisper_model: str = "small", translator: str = "auto") -> None:
        self.stt_engine = stt_engine
        self.whisper_model = whisper_model
        self.translator_name = translator
        self.translator = translate.create(translator)
        self._stt = None
        self.stt_note = ""
        self._lock = threading.Lock()

    def configure(self, stt_engine: str | None = None, whisper_model: str | None = None,
                  translator: str | None = None) -> None:
        with self._lock:
            if stt_engine and stt_engine != self.stt_engine:
                self.stt_engine, self._stt = stt_engine, None
            if whisper_model and whisper_model != self.whisper_model:
                self.whisper_model, self._stt = whisper_model, None
            if translator and translator != self.translator_name:
                self.translator = translate.create(translator)
                self.translator_name = translator

    def get_stt(self, on_status: Callable[[str], None] = lambda s: None):
        with self._lock:
            if self._stt is None:
                if self.stt_engine in ("auto", "whisper"):
                    on_status(f"Loading Whisper '{self.whisper_model}' (first run downloads the model)...")
                self._stt, self.stt_note = stt.create(self.stt_engine, self.whisper_model)
            return self._stt


@dataclass
class PipelineSettings:
    mode: str = "speak"              # "speak" or "listen"
    source: str = "en"               # spoken language code, or "auto" (Whisper only)
    target: str = "gu"               # language to translate into
    voice: str = "female"            # "female", "male" or a full Edge voice name
    input_device: str | None = None  # speak: microphone; listen: speaker to capture. None = default
    output_device: str | None = None  # where the translated voice plays. None = default speaker
    speak: bool = True               # False = captions only (useful in listen mode)
    monitor_device: str | None = None  # speak mode: also play the translation here
    sensitivity: float = 1.0         # higher = picks up quieter speech
    rate: str = "+0%"                # voice speed, e.g. "+10%"


class Pipeline:
    def __init__(self, settings: PipelineSettings, engines: Engines, log: EventLog) -> None:
        if settings.mode not in ("speak", "listen"):
            raise ValueError("mode must be 'speak' or 'listen'")
        if settings.target == AUTO:
            raise ValueError("Choose the language to translate into.")
        self.s = settings
        self.engines = engines
        self.log = log
        self.voice = languages.resolve_voice(settings.target, settings.voice) if settings.speak else None
        self.state = "idle"
        self.level = 0.0
        self.source: audio.AudioSource | None = None
        self.outputs: list[audio.AudioOutput] = []
        self._echo_ids: set[str] = set()
        self._stt = None
        self._stop = threading.Event()
        self._closed = False
        self._utterances: queue.Queue = queue.Queue()
        self._speech: queue.Queue = queue.Queue()
        self._threads: list[threading.Thread] = []

    # -- lifecycle ------------------------------------------------------------
    @property
    def running(self) -> bool:
        return any(t.is_alive() for t in self._threads) and not self._stop.is_set()

    def start(self) -> "Pipeline":
        s = self.s
        self._set_state("starting")
        try:
            self.source = audio.AudioSource(s.input_device, loopback=(s.mode == "listen"))
            if s.speak:
                devices = [s.output_device] + ([s.monitor_device] if s.monitor_device else [])
                for dev in devices:
                    out = audio.AudioOutput(dev, tts.SAMPLE_RATE, on_error=self._error)
                    if all(o.device_id != out.device_id for o in self.outputs):
                        self.outputs.append(out.start())
        except Exception as exc:
            for out in self.outputs:
                out.stop()
            self._closed = True
            self._fail(f"Could not open audio device: {exc}")
            raise
        # Never translate our own voice: mute capture while we play on a device it can hear.
        if s.mode == "listen":
            self._echo_ids = {self.source.device_id}
        else:  # a mic may hear our voice on real speakers, but not inside a virtual cable
            self._echo_ids = {o.device_id for o in self.outputs if not audio.is_virtual_cable(o.name)}

        self._status(self._describe())
        for target, name in ((self._capture_loop, "capture"), (self._recognize_loop, "recognize"),
                             (self._voice_loop, "voice")):
            thread = threading.Thread(target=target, name=f"{s.mode}-{name}", daemon=True)
            self._threads.append(thread)
            thread.start()
        return self

    def stop(self, timeout: float = 5.0) -> None:
        if self._closed:
            return
        self._closed = True
        self._stop.set()
        self._utterances.put(None)
        self._speech.put(None)
        for thread in self._threads:
            if thread is not threading.current_thread():
                thread.join(timeout)
        for out in self.outputs:
            out.stop()
        if self.state != "error":
            self._set_state("stopped")
        self.level = 0.0

    # -- the three worker threads ---------------------------------------------
    def _capture_loop(self) -> None:
        seg = UtteranceSegmenter(sensitivity=self.s.sensitivity)
        try:
            for block in self.source.blocks(self._stop):
                if self._echo_ids and any(audio.device_busy(d) for d in self._echo_ids):
                    seg.reset()  # we are talking; ignore what the device hears
                    self.level = 0.0
                    continue
                for utterance in seg.feed(block):
                    self._utterances.put(utterance)
                self.level = seg.level
        except Exception as exc:
            if not self._stop.is_set():
                self._fail(f"Audio capture stopped: {exc}")
        finally:
            self._utterances.put(None)

    def _recognize_loop(self) -> None:
        try:
            try:
                self._stt = self.engines.get_stt(self._status)
            except Exception as exc:
                self._fail(f"Could not start speech recognition: {exc}")
                return
            if self.engines.stt_note:
                self._status(self.engines.stt_note)
            if self.s.source == AUTO and getattr(self._stt, "name", "") != "whisper":
                self._fail("Auto-detect needs the Whisper engine. Pick the spoken language instead.")
                return
            self._set_state("listening")
            while True:
                item = self._utterances.get()
                if item is None:
                    break
                if not self._stop.is_set():
                    self.process_utterance(item)
        finally:
            self._speech.put(None)

    def _voice_loop(self) -> None:
        while True:
            text = self._speech.get()
            if text is None:
                break
            if self._stop.is_set() or not self.outputs:
                continue
            try:
                clip = tts.synthesize(text, self.voice, rate=self.s.rate)
            except Exception as exc:
                self._error(f"Voice synthesis failed: {exc}")
                continue
            for out in self.outputs:
                out.play(clip)

    # -- one utterance ---------------------------------------------------------
    def process_utterance(self, samples: np.ndarray) -> str | None:
        """Recognise and translate one utterance; queue it for speaking."""
        started = time.monotonic()
        source = None if self.s.source == AUTO else self.s.source
        try:
            heard = self._stt.transcribe(samples, source)
        except Exception as exc:
            self._error(f"Speech recognition failed: {exc}")
            return None
        text = heard.text.strip()
        if not text:
            return None
        spoken_lang = heard.language or source or "en"
        try:
            translated, engine = self.engines.translator.translate(text, spoken_lang, self.s.target)
        except Exception as exc:
            self._error(f"Translation failed: {exc}")
            self.log.add(self.s.mode, "line", text, original=text, source=spoken_lang,
                         target=spoken_lang, engine="untranslated", seconds=0)
            return None
        self.log.add(self.s.mode, "line", translated, original=text, source=spoken_lang, target=self.s.target,
                     engine=engine, seconds=round(time.monotonic() - started, 1))
        already_in_target = self.s.mode == "listen" and spoken_lang == self.s.target
        if self.voice and not already_in_target:
            if self._speech.qsize() >= 3:
                self._status("Falling behind - the translated voice is queued. Pause briefly between sentences.")
            self._speech.put(translated)
        return translated

    # -- helpers ----------------------------------------------------------------
    def _describe(self) -> str:
        s = self.s
        src = languages.name(s.source)
        dst = languages.name(s.target)
        where = ", ".join(o.name for o in self.outputs) or "captions only"
        what = "Microphone" if s.mode == "speak" else "Meeting audio"
        return f"{what} '{self.source.name}' ({src}) -> {dst} -> {where}"

    def _set_state(self, state: str) -> None:
        self.state = state
        self.log.add(self.s.mode, "state", state)

    def _status(self, text: str) -> None:
        self.log.add(self.s.mode, "status", text)

    def _error(self, text: str) -> None:
        self.log.add(self.s.mode, "error", text)

    def _fail(self, text: str) -> None:
        self._error(text)
        self._set_state("error")
        self._stop.set()
        self._utterances.put(None)
