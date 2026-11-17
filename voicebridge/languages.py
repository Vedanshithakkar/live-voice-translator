"""Languages VoiceBridge knows about, with the neural voice used for each.

Any language supported by the speech, translation and voice engines can be
added here. ``code`` is the ISO-639-1 code used by Whisper and most
translators; the other fields cover engines that spell things differently.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Language:
    code: str            # ISO-639-1 (Whisper, MyMemory)
    name: str
    locale: str          # BCP-47 locale for Google speech recognition
    female_voice: str    # Microsoft Edge neural voices (edge-tts)
    male_voice: str
    google_code: str = ""  # Google Translate code, when it differs from `code`
    azure_code: str = ""   # Azure Translator code, when it differs from `code`

    @property
    def google(self) -> str:
        return self.google_code or self.code

    @property
    def azure(self) -> str:
        return self.azure_code or self.code


_ALL = [
    Language("en", "English", "en-IN", "en-IN-NeerjaNeural", "en-IN-PrabhatNeural"),
    Language("gu", "Gujarati", "gu-IN", "gu-IN-DhwaniNeural", "gu-IN-NiranjanNeural"),
    Language("hi", "Hindi", "hi-IN", "hi-IN-SwaraNeural", "hi-IN-MadhurNeural"),
    Language("mr", "Marathi", "mr-IN", "mr-IN-AarohiNeural", "mr-IN-ManoharNeural"),
    Language("bn", "Bengali", "bn-IN", "bn-IN-TanishaaNeural", "bn-IN-BashkarNeural"),
    Language("ta", "Tamil", "ta-IN", "ta-IN-PallaviNeural", "ta-IN-ValluvarNeural"),
    Language("te", "Telugu", "te-IN", "te-IN-ShrutiNeural", "te-IN-MohanNeural"),
    Language("kn", "Kannada", "kn-IN", "kn-IN-SapnaNeural", "kn-IN-GaganNeural"),
    Language("ml", "Malayalam", "ml-IN", "ml-IN-SobhanaNeural", "ml-IN-MidhunNeural"),
    Language("ur", "Urdu", "ur-IN", "ur-IN-GulNeural", "ur-IN-SalmanNeural"),
    Language("es", "Spanish", "es-ES", "es-ES-ElviraNeural", "es-ES-AlvaroNeural"),
    Language("fr", "French", "fr-FR", "fr-FR-DeniseNeural", "fr-FR-HenriNeural"),
    Language("de", "German", "de-DE", "de-DE-TanjaNeural", "de-DE-ChristophNeural"),
    Language("it", "Italian", "it-IT", "it-IT-ElsaNeural", "it-IT-DiegoNeural"),
    Language("pt", "Portuguese", "pt-BR", "pt-BR-FranciscaNeural", "pt-BR-AntonioNeural"),
    Language("ru", "Russian", "ru-RU", "ru-RU-SvetlanaNeural", "ru-RU-DmitryNeural"),
    Language("ar", "Arabic", "ar-SA", "ar-SA-ZariyahNeural", "ar-SA-HamedNeural"),
    Language("zh", "Chinese (Mandarin)", "zh-CN", "zh-CN-XiaoxiaoNeural", "zh-CN-YunxiNeural",
             google_code="zh-CN", azure_code="zh-Hans"),
    Language("ja", "Japanese", "ja-JP", "ja-JP-NanamiNeural", "ja-JP-KeitaNeural"),
    Language("ko", "Korean", "ko-KR", "ko-KR-SunHiNeural", "ko-KR-InJoonNeural"),
]

LANGUAGES: dict[str, Language] = {lang.code: lang for lang in _ALL}

AUTO = "auto"


def get(code: str) -> Language | None:
    return LANGUAGES.get(code)


def name(code: str) -> str:
    if code == AUTO:
        return "Auto-detect"
    lang = LANGUAGES.get(code)
    return lang.name if lang else code


def resolve_voice(target: str, voice: str) -> str:
    """Turn 'female' / 'male' / a full voice name into an Edge voice name."""
    if voice not in ("female", "male", ""):
        return voice  # already a full voice name such as "gu-IN-DhwaniNeural"
    lang = LANGUAGES.get(target)
    if lang is None:
        raise ValueError(
            f"No default voice for language '{target}'. Pass a full voice name "
            "(run `python -m voicebridge voices` to list them)."
        )
    return lang.male_voice if voice == "male" else lang.female_voice
