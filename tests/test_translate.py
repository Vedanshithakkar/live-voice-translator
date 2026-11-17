import pytest

from voicebridge import languages
from voicebridge.translate import FallbackTranslator, TranslationError, split_text


class Fake:
    def __init__(self, name, result=None, fail=False):
        self.name, self.result, self.fail, self.calls = name, result, fail, 0

    def translate(self, text, source, target):
        self.calls += 1
        if self.fail:
            raise TranslationError(f"{self.name} down")
        return self.result


def test_falls_back_and_then_skips_the_failed_engine():
    bad, good = Fake("a", fail=True), Fake("b", result="નમસ્તે")
    chain = FallbackTranslator([bad, good])
    assert chain.translate("hello", "en", "gu") == ("નમસ્તે", "b")
    assert chain.translate("hello", "en", "gu") == ("નમસ્તે", "b")
    assert bad.calls == 1  # cooled down, not retried immediately


def test_all_failing_raises_with_every_reason():
    chain = FallbackTranslator([Fake("a", fail=True), Fake("b", fail=True)])
    with pytest.raises(TranslationError, match="a down; b down"):
        chain.translate("hello", "en", "gu")


def test_same_language_is_passed_through():
    engine = Fake("a", result="x")
    assert FallbackTranslator([engine]).translate("hello", "en", "en") == ("hello", "none")
    assert engine.calls == 0


def test_split_text_respects_byte_limit_and_keeps_words():
    text = "હું પાંચ મિનિટમાં મીટિંગમાં જોડાઈશ. " * 12
    pieces = split_text(text, 120)
    assert all(len(p.encode("utf-8")) <= 120 for p in pieces)
    assert " ".join(pieces).split() == text.split()


def test_every_language_has_voices_and_resolves():
    for code, lang in languages.LANGUAGES.items():
        assert lang.female_voice.startswith(lang.locale.split("-")[0])
        assert languages.resolve_voice(code, "male") == lang.male_voice
    assert languages.resolve_voice("gu", "gu-IN-NiranjanNeural") == "gu-IN-NiranjanNeural"
