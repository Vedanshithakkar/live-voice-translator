"""Text-to-speech with Microsoft Edge neural voices (edge-tts).

The service returns MP3; PyAV (FFmpeg) decodes it to float32 samples.
"""
from __future__ import annotations

import asyncio
import io

import numpy as np

SAMPLE_RATE = 24000


async def _fetch_mp3(text: str, voice: str, rate: str) -> bytes:
    import edge_tts

    audio = bytearray()
    async for chunk in edge_tts.Communicate(text, voice, rate=rate).stream():
        if chunk["type"] == "audio":
            audio.extend(chunk["data"])
    return bytes(audio)


def decode_mp3(data: bytes, sample_rate: int = SAMPLE_RATE) -> np.ndarray:
    """Decode compressed audio to mono float32 at sample_rate."""
    import av

    out: list[np.ndarray] = []
    with av.open(io.BytesIO(data)) as container:
        resampler = av.AudioResampler(format="flt", layout="mono", rate=sample_rate)
        for frame in container.decode(audio=0):
            for res in resampler.resample(frame):
                out.append(res.to_ndarray().reshape(-1))
        for res in resampler.resample(None):  # flush
            out.append(res.to_ndarray().reshape(-1))
    if not out:
        return np.zeros(0, dtype=np.float32)
    return np.concatenate(out).astype(np.float32, copy=False)


def synthesize(text: str, voice: str, rate: str = "+0%", sample_rate: int = SAMPLE_RATE) -> np.ndarray:
    """Speak `text` with `voice`; returns mono float32 samples."""
    mp3 = asyncio.run(_fetch_mp3(text, voice, rate))
    if not mp3:
        raise RuntimeError(f"No audio returned for voice '{voice}'")
    return decode_mp3(mp3, sample_rate)


def list_voices(language: str | None = None) -> list[dict]:
    """Return available Edge voices, optionally filtered by language code (e.g. 'gu')."""
    import edge_tts

    voices = asyncio.run(edge_tts.list_voices())
    if language:
        voices = [v for v in voices if v["Locale"].split("-")[0].lower() == language.lower()]
    return sorted(voices, key=lambda v: v["ShortName"])
