import numpy as np

from voicebridge.pipeline import EventLog, Pipeline, PipelineSettings
from voicebridge.stt import Transcript


class FakeSTT:
    name = "whisper"

    def __init__(self, text, language="en"):
        self.text, self.language = text, language

    def transcribe(self, audio, language):
        return Transcript(self.text, language or self.language)


class FakeTranslator:
    def translate(self, text, source, target):
        return (f"[{target}] {text}", "fake") if source != target else (text, "none")


class FakeEngines:
    def __init__(self, stt):
        self.translator = FakeTranslator()
        self._stt = stt


def make(mode="speak", source="en", target="gu", stt=None, speak=True):
    log = EventLog()
    pipe = Pipeline(PipelineSettings(mode=mode, source=source, target=target, speak=speak),
                    FakeEngines(stt or FakeSTT("hello")), log)
    pipe._stt = pipe.engines._stt
    return pipe, log


def test_utterance_is_translated_logged_and_queued_for_voice():
    pipe, log = make()
    assert pipe.process_utterance(np.zeros(16000, dtype=np.float32)) == "[gu] hello"
    line = [e for e in log.since(0) if e["kind"] == "line"][0]
    assert line["original"] == "hello" and line["text"] == "[gu] hello" and line["engine"] == "fake"
    assert pipe._speech.get_nowait() == "[gu] hello"
    assert pipe.voice == "gu-IN-DhwaniNeural"


def test_empty_recognition_is_ignored():
    pipe, log = make(stt=FakeSTT("  "))
    assert pipe.process_utterance(np.zeros(1600, dtype=np.float32)) is None
    assert log.since(0) == []


def test_listen_mode_does_not_speak_speech_already_in_my_language():
    pipe, _ = make(mode="listen", source="auto", target="en", stt=FakeSTT("good morning", "en"))
    assert pipe.process_utterance(np.zeros(1600, dtype=np.float32)) == "good morning"
    assert pipe._speech.empty()


def test_captions_only_has_no_voice():
    pipe, _ = make(mode="listen", source="gu", target="en", speak=False)
    pipe.process_utterance(np.zeros(1600, dtype=np.float32))
    assert pipe.voice is None


def test_event_log_since_and_subscribe():
    log, seen = EventLog(maxlen=3), []
    log.subscribe(seen.append)
    for i in range(5):
        log.add("speak", "status", str(i))
    assert [e["text"] for e in log.since(0)] == ["2", "3", "4"]
    assert [e["text"] for e in log.since(4)] == ["4"]
    assert len(seen) == 5
