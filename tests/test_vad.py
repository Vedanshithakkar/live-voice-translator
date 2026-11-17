import numpy as np

from voicebridge.vad import UtteranceSegmenter

SR = 16000


def tone(seconds, amp=0.2, freq=220.0):
    t = np.arange(int(SR * seconds)) / SR
    return (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def noise(seconds, amp=0.001, seed=0):
    return (np.random.default_rng(seed).standard_normal(int(SR * seconds)) * amp).astype(np.float32)


def run(seg, signal, block=480):
    out = []
    for i in range(0, len(signal), block):
        out.extend(seg.feed(signal[i:i + block]))
    return out


def test_single_utterance_is_detected_with_preroll_and_short_tail():
    seg = UtteranceSegmenter()
    audio = np.concatenate([noise(1.0), tone(1.5), noise(1.5, seed=1)])
    utts = run(seg, audio)
    assert len(utts) == 1
    seconds = len(utts[0]) / SR
    assert 1.5 <= seconds <= 2.2  # speech + preroll + ~200 ms tail


def test_two_sentences_with_a_gap_become_two_utterances():
    seg = UtteranceSegmenter()
    audio = np.concatenate([noise(0.6), tone(1.0), noise(1.2, seed=1), tone(0.8), noise(1.2, seed=2)])
    assert len(run(seg, audio)) == 2


def test_silence_and_clicks_produce_nothing():
    seg = UtteranceSegmenter()
    click = tone(0.06)  # shorter than start_ms + min_speech_ms
    audio = np.concatenate([noise(1.0), click, noise(1.0, seed=1), click, noise(1.0, seed=2)])
    assert run(seg, audio) == []


def test_long_monologue_is_split():
    seg = UtteranceSegmenter(soft_max_s=4.0, hard_max_s=6.0)
    # 12 s of speech with a short 300 ms breath every 2 s
    parts = [noise(0.5)]
    for i in range(6):
        parts += [tone(1.7), noise(0.3, seed=i + 1)]
    utts = run(seg, np.concatenate(parts + [noise(1.0, seed=99)]))
    assert len(utts) >= 2
    assert all(len(u) / SR <= 6.1 for u in utts)


def test_noise_floor_adapts_to_steady_background():
    seg = UtteranceSegmenter()
    hum = tone(1.0, amp=0.02, freq=50.0)  # steady hum present from the start
    run(seg, np.tile(hum, 3))
    assert seg.noise > 0.01
    speech = np.concatenate([np.tile(hum, 1), tone(1.0, amp=0.3) + tone(1.0, amp=0.02, freq=50.0), np.tile(hum, 2)])
    assert len(run(seg, speech)) == 1


def test_flush_returns_unfinished_utterance():
    seg = UtteranceSegmenter()
    run(seg, np.concatenate([noise(0.6), tone(1.0)]))
    assert seg.active
    tail = seg.flush()
    assert tail is not None and len(tail) / SR >= 0.9
    assert not seg.active
