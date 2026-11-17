"""Audio devices: microphones, speakers, and loopback capture of what a speaker plays.

Uses the `soundcard` package (WASAPI on Windows, PulseAudio on Linux,
CoreAudio on macOS). Windows converts sample rates for us, so capture always
runs at 16 kHz mono and playback at the TTS rate.
"""
from __future__ import annotations

import queue
import sys
import threading
import time
import warnings
from typing import Callable, Iterator

import numpy as np

warnings.filterwarnings("ignore", message="data discontinuity in recording")

# Names that identify a virtual audio cable's *playback* side. Meeting apps then
# use the matching recording side ("CABLE Output", "VoiceMeeter Output") as mic.
VIRTUAL_CABLE_HINTS = ("cable input", "voicemeeter input", "voicemeeter aux input", "virtual audio cable")


class DeviceNotFound(LookupError):
    pass


_tls = threading.local()


def _sc():
    """Import soundcard, making sure COM is initialised on the calling thread.

    On Windows every thread that touches WASAPI needs COM. soundcard only
    initialises the thread that imports it, which may be short-lived.
    """
    # Import first: soundcard's own import initialises COM and treats
    # "already initialised" as an error.
    import soundcard

    if sys.platform == "win32" and not getattr(_tls, "com_ready", False):
        import ctypes

        ctypes.windll.ole32.CoInitializeEx(None, 0)  # COINIT_MULTITHREADED; no-op if already set
        _tls.com_ready = True
    return soundcard


def init() -> None:
    """Load the audio backend on the current (long-lived) thread."""
    _sc()


def _describe(dev) -> dict:
    return {"id": dev.id, "name": dev.name}


def list_microphones() -> list[dict]:
    return [_describe(d) for d in _sc().all_microphones()]


def list_speakers() -> list[dict]:
    return [_describe(d) for d in _sc().all_speakers()]


def default_speaker() -> dict | None:
    try:
        return _describe(_sc().default_speaker())
    except Exception:
        return None


def default_microphone() -> dict | None:
    try:
        return _describe(_sc().default_microphone())
    except Exception:
        return None


def is_virtual_cable(name: str) -> bool:
    lowered = name.lower()
    return any(hint in lowered for hint in VIRTUAL_CABLE_HINTS)


def find_virtual_cable() -> dict | None:
    """The playback side of an installed virtual cable (e.g. VB-Audio 'CABLE Input')."""
    for spk in list_speakers():
        if is_virtual_cable(spk["name"]):
            return spk
    return None


def _match(devices, query: str | None):
    """Find a device by exact id or by case-insensitive name substring."""
    if query in (None, "", "default"):
        return None
    for dev in devices:
        if dev.id == query:
            return dev
    q = query.lower()
    for dev in devices:
        if q in dev.name.lower():
            return dev
    names = ", ".join(d.name for d in devices) or "none"
    raise DeviceNotFound(f"No audio device matching '{query}'. Available: {names}")


def get_microphone(query: str | None):
    sc = _sc()
    return _match(sc.all_microphones(), query) or sc.default_microphone()


def get_speaker(query: str | None):
    sc = _sc()
    return _match(sc.all_speakers(), query) or sc.default_speaker()


def get_loopback(query: str | None):
    """A 'microphone' that records everything the chosen speaker plays."""
    sc = _sc()
    loopbacks = [m for m in sc.all_microphones(include_loopback=True) if m.isloopback]
    found = _match(loopbacks, query)
    if found is not None:
        return found
    default_id = sc.default_speaker().id
    for mic in loopbacks:
        if mic.id == default_id:
            return mic
    raise DeviceNotFound("Could not open loopback capture for the default speaker")


# -- which devices are currently playing our own speech ----------------------
# Capture is muted while we play on the device it listens to, so VoiceBridge
# never translates its own voice.
_busy_lock = threading.Lock()
_busy_until: dict[str, float] = {}


def device_busy(device_id: str, margin_s: float = 0.4) -> bool:
    with _busy_lock:
        return time.monotonic() < _busy_until.get(device_id, 0.0) + margin_s


class AudioSource:
    """Capture from a microphone or a speaker loopback as 16 kHz mono blocks."""

    def __init__(self, device: str | None, loopback: bool = False, sample_rate: int = 16000, block_ms: int = 30):
        self.device = get_loopback(device) if loopback else get_microphone(device)
        self.device_id = self.device.id
        self.name = self.device.name
        self.loopback = loopback
        self.sample_rate = sample_rate
        self.block = int(sample_rate * block_ms / 1000)

    def blocks(self, stop: threading.Event) -> Iterator[np.ndarray]:
        _sc()  # COM for this thread
        with self.device.recorder(samplerate=self.sample_rate, channels=1, blocksize=self.block) as rec:
            while not stop.is_set():
                data = rec.record(numframes=self.block)
                yield np.asarray(data, dtype=np.float32).reshape(-1)


class AudioOutput:
    """Plays clips on one output device from a background thread, in order."""

    def __init__(self, device: str | None, sample_rate: int = 24000, on_error: Callable[[str], None] | None = None):
        self.speaker = get_speaker(device)
        self.device_id = self.speaker.id
        self.name = self.speaker.name
        self.sample_rate = sample_rate
        self._on_error = on_error or (lambda msg: None)
        self._queue: queue.Queue = queue.Queue()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> "AudioOutput":
        self._thread = threading.Thread(target=self._run, name=f"out:{self.name}", daemon=True)
        self._thread.start()
        return self

    def play(self, clip: np.ndarray) -> None:
        duration = len(clip) / self.sample_rate
        with _busy_lock:
            start = max(time.monotonic(), _busy_until.get(self.device_id, 0.0))
            _busy_until[self.device_id] = start + duration
        self._queue.put(clip)

    def stop(self) -> None:
        self._stop.set()
        self._queue.put(None)
        if self._thread:
            self._thread.join(timeout=2)

    def _run(self) -> None:
        _sc()  # COM for this thread
        while not self._stop.is_set():
            try:
                with self.speaker.player(samplerate=self.sample_rate) as player:
                    while True:
                        clip = self._queue.get()
                        if clip is None or self._stop.is_set():
                            return
                        player.play(clip)
            except Exception as exc:  # device unplugged, exclusive mode, ...
                self._on_error(f"Playback on '{self.name}' failed: {exc}")
                self._stop.wait(1.0)
