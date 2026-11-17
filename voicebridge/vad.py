"""Cut a continuous audio stream into utterances (one sentence-ish chunk each).

A small energy-based voice activity detector with an adaptive noise floor.
It needs no extra libraries and works the same for a microphone and for
meeting audio captured from the speakers.
"""
from __future__ import annotations

from collections import deque

import numpy as np


class UtteranceSegmenter:
    """Feed it audio blocks; it returns finished utterances.

    An utterance starts after ``start_ms`` of sound above the threshold and
    ends after ``end_silence_ms`` of quiet. Long monologues are split at the
    first short pause after ``soft_max_s`` and always at ``hard_max_s``, so a
    speaker who never stops still gets translated in pieces.
    """

    def __init__(
        self,
        sample_rate: int = 16000,
        frame_ms: int = 30,
        sensitivity: float = 1.0,
        start_ms: int = 90,
        end_silence_ms: int = 700,
        pause_ms: int = 240,
        soft_max_s: float = 8.0,
        hard_max_s: float = 15.0,
        preroll_ms: int = 300,
        tail_ms: int = 200,
        min_speech_ms: int = 300,
        min_rms: float = 0.005,
        noise_ratio: float = 3.0,
    ) -> None:
        def frames(ms: float) -> int:
            return max(1, round(ms / frame_ms))

        self.sample_rate = sample_rate
        self.frame_len = int(sample_rate * frame_ms / 1000)
        self.sensitivity = sensitivity
        self.min_rms = min_rms
        self.noise_ratio = noise_ratio
        self.start_frames = frames(start_ms)
        self.end_frames = frames(end_silence_ms)
        self.pause_frames = frames(pause_ms)
        self.soft_max_frames = frames(soft_max_s * 1000)
        self.hard_max_frames = frames(hard_max_s * 1000)
        self.tail_frames = frames(tail_ms)
        self.min_speech_frames = frames(min_speech_ms)
        self._calibration_frames = frames(450)

        self.noise = min_rms / 2
        self.level = 0.0  # RMS of the latest frame, for level meters
        self._seen = 0
        self._pending = np.zeros(0, dtype=np.float32)
        self._preroll: deque[np.ndarray] = deque(maxlen=frames(preroll_ms))
        self._reset_utterance()

    # -- public API ---------------------------------------------------------
    @property
    def threshold(self) -> float:
        return max(self.min_rms, self.noise * self.noise_ratio) / max(self.sensitivity, 0.05)

    @property
    def active(self) -> bool:
        return self._active

    def feed(self, samples: np.ndarray) -> list[np.ndarray]:
        """Add mono float32 samples; return any utterances that finished."""
        samples = np.asarray(samples, dtype=np.float32).reshape(-1)
        if self._pending.size:
            samples = np.concatenate([self._pending, samples])
        done: list[np.ndarray] = []
        n_full = samples.size // self.frame_len
        for i in range(n_full):
            frame = samples[i * self.frame_len:(i + 1) * self.frame_len]
            utterance = self._process(frame)
            if utterance is not None:
                done.append(utterance)
        self._pending = samples[n_full * self.frame_len:].copy()
        return done

    def flush(self) -> np.ndarray | None:
        """Finish the current utterance (if any), e.g. when stopping."""
        return self._finish() if self._active else None

    def reset(self) -> None:
        """Drop any partial utterance but keep the learned noise floor."""
        self._pending = np.zeros(0, dtype=np.float32)
        self._preroll.clear()
        self._reset_utterance()

    # -- internals ----------------------------------------------------------
    def _reset_utterance(self) -> None:
        self._active = False
        self._frames: list[np.ndarray] = []
        self._loud_run = 0
        self._quiet_run = 0
        self._voiced = 0

    def _track_noise(self, rms: float) -> None:
        self._seen += 1
        if self._seen <= self._calibration_frames:
            # Assume the first ~0.5 s is background: average it.
            self.noise += (rms - self.noise) / self._seen
        elif rms < self.noise:
            self.noise = 0.9 * self.noise + 0.1 * rms        # fall fast
        else:
            self.noise = 0.9995 * self.noise + 0.0005 * rms  # rise slowly (~60 s)

    def _process(self, frame: np.ndarray) -> np.ndarray | None:
        rms = float(np.sqrt(np.mean(frame * frame)))
        self.level = rms
        self._track_noise(rms)
        start_thr = self.threshold
        keep_thr = start_thr * 0.6  # hysteresis: soft syllables keep speech going

        if not self._active:
            self._preroll.append(frame)
            self._loud_run = self._loud_run + 1 if rms > start_thr else 0
            if self._loud_run >= self.start_frames:
                self._active = True
                self._frames = list(self._preroll)
                self._preroll.clear()
                self._voiced = self._loud_run
                self._quiet_run = 0
            return None

        self._frames.append(frame)
        if rms > keep_thr:
            self._voiced += 1
            self._quiet_run = 0
        else:
            self._quiet_run += 1

        n = len(self._frames)
        if (
            self._quiet_run >= self.end_frames
            or (n >= self.soft_max_frames and self._quiet_run >= self.pause_frames)
            or n >= self.hard_max_frames
        ):
            return self._finish()
        return None

    def _finish(self) -> np.ndarray | None:
        frames, voiced, quiet = self._frames, self._voiced, self._quiet_run
        self._reset_utterance()
        drop = max(0, quiet - self.tail_frames)
        if drop:
            frames = frames[:-drop]
        if voiced < self.min_speech_frames or not frames:
            return None
        return np.concatenate(frames)
