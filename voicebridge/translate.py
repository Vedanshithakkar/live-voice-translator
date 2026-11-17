"""Text translation engines with automatic fallback.

* ``google``   - free Google Translate (deep-translator). No key. Google may
  rate-limit some shared/office networks.
* ``mymemory`` - free MyMemory API. No key; ~5,000 chars/day anonymous, more if
  you set the MYMEMORY_EMAIL environment variable.
* ``azure``    - Microsoft Translator with your own key (free tier: 2M chars a
  month). Set AZURE_TRANSLATOR_KEY and AZURE_TRANSLATOR_REGION.

``auto`` tries Azure (if a key is set), then Google, then MyMemory, and
temporarily skips an engine that just failed.
"""
from __future__ import annotations

import os
import re
import threading
import time

import requests

from . import languages


class TranslationError(RuntimeError):
    pass


def _lang(code: str, attr: str) -> str:
    lang = languages.get(code)
    return getattr(lang, attr) if lang else code


def split_text(text: str, max_bytes: int) -> list[str]:
    """Split text into pieces of at most max_bytes UTF-8, preferring sentence ends."""
    if len(text.encode("utf-8")) <= max_bytes:
        return [text]
    pieces: list[str] = []
    current = ""
    for part in re.split(r"(?<=[.!?।॥。？！])\s+|\s+", text):
        if not part:
            continue
        candidate = f"{current} {part}".strip()
        if len(candidate.encode("utf-8")) <= max_bytes:
            current = candidate
            continue
        if current:
            pieces.append(current)
        while len(part.encode("utf-8")) > max_bytes:  # one enormous "word"
            cut = max_bytes
            while len(part[:cut].encode("utf-8")) > max_bytes:
                cut -= 1
            pieces.append(part[:cut])
            part = part[cut:]
        current = part
    if current:
        pieces.append(current)
    return pieces


class GoogleTranslate:
    name = "google"

    def translate(self, text: str, source: str, target: str) -> str:
        from deep_translator import GoogleTranslator

        try:
            out = GoogleTranslator(source=_lang(source, "google"), target=_lang(target, "google")).translate(text)
        except Exception as exc:
            raise TranslationError(f"Google Translate: {type(exc).__name__}: {exc}") from exc
        if not out:
            raise TranslationError("Google Translate returned nothing")
        return out


class MyMemoryTranslate:
    name = "mymemory"
    URL = "https://api.mymemory.translated.net/get"

    def __init__(self, email: str | None = None) -> None:
        self.email = email or os.environ.get("MYMEMORY_EMAIL") or None

    def translate(self, text: str, source: str, target: str) -> str:
        pair = f"{_lang(source, 'google')}|{_lang(target, 'google')}"
        out = []
        for piece in split_text(text, 450):  # API limit is 500 bytes per request
            params = {"q": piece, "langpair": pair}
            if self.email:
                params["de"] = self.email
            try:
                data = requests.get(self.URL, params=params, timeout=15).json()
            except Exception as exc:
                raise TranslationError(f"MyMemory: {exc}") from exc
            translated = (data.get("responseData") or {}).get("translatedText") or ""
            if str(data.get("responseStatus")) != "200" or translated.upper().startswith("MYMEMORY WARNING"):
                raise TranslationError(f"MyMemory: {data.get('responseDetails') or translated or 'error'}")
            out.append(translated)
        return " ".join(out)


class AzureTranslate:
    name = "azure"
    URL = "https://api.cognitive.microsofttranslator.com/translate"

    def __init__(self, key: str, region: str | None = None) -> None:
        self.key = key
        self.region = region

    @classmethod
    def from_env(cls) -> "AzureTranslate | None":
        key = os.environ.get("AZURE_TRANSLATOR_KEY")
        return cls(key, os.environ.get("AZURE_TRANSLATOR_REGION")) if key else None

    def translate(self, text: str, source: str, target: str) -> str:
        params = {"api-version": "3.0", "to": _lang(target, "azure")}
        if source:
            params["from"] = _lang(source, "azure")
        headers = {"Ocp-Apim-Subscription-Key": self.key, "Content-Type": "application/json"}
        if self.region:
            headers["Ocp-Apim-Subscription-Region"] = self.region
        try:
            resp = requests.post(self.URL, params=params, headers=headers, json=[{"Text": text}], timeout=15)
            resp.raise_for_status()
            return resp.json()[0]["translations"][0]["text"]
        except Exception as exc:
            raise TranslationError(f"Azure Translator: {exc}") from exc


class FallbackTranslator:
    """Try engines in order; skip an engine for a while after it fails."""

    def __init__(self, engines: list, cooldown_s: float = 300.0) -> None:
        if not engines:
            raise ValueError("No translation engines configured")
        self.engines = engines
        self.cooldown_s = cooldown_s
        self._failed_at: dict[str, float] = {}
        self._lock = threading.Lock()

    @property
    def name(self) -> str:
        return "+".join(e.name for e in self.engines)

    def translate(self, text: str, source: str, target: str) -> tuple[str, str]:
        """Return (translated_text, engine_name)."""
        if not text.strip() or source == target:
            return text, "none"
        now = time.monotonic()
        with self._lock:
            fresh = [e for e in self.engines if now - self._failed_at.get(e.name, -1e9) > self.cooldown_s]
        order = fresh or self.engines  # everything failed recently: try all again
        errors = []
        for engine in order:
            try:
                result = engine.translate(text, source, target)
                with self._lock:
                    self._failed_at.pop(engine.name, None)
                return result, engine.name
            except TranslationError as exc:
                errors.append(str(exc))
                with self._lock:
                    self._failed_at[engine.name] = time.monotonic()
        raise TranslationError("; ".join(errors))


def create(engine: str = "auto") -> FallbackTranslator:
    engine = engine.lower()
    azure = AzureTranslate.from_env()
    if engine == "google":
        return FallbackTranslator([GoogleTranslate()])
    if engine == "mymemory":
        return FallbackTranslator([MyMemoryTranslate()])
    if engine == "azure":
        if azure is None:
            raise ValueError("Set AZURE_TRANSLATOR_KEY (and AZURE_TRANSLATOR_REGION) to use Azure.")
        return FallbackTranslator([azure])
    if engine != "auto":
        raise ValueError(f"Unknown translator '{engine}' (use auto, google, mymemory or azure).")
    chain = ([azure] if azure else []) + [GoogleTranslate(), MyMemoryTranslate()]
    return FallbackTranslator(chain)
