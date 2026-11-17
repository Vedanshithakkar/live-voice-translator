"""Command line: `python -m voicebridge [ui|speak|listen|say|devices|voices|languages]`."""
from __future__ import annotations

import argparse
import sys
import time
import wave

from . import __version__, languages


def _utf8_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def _engine_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--stt", default="auto", choices=["auto", "whisper", "google"], help="speech recognition engine")
    p.add_argument("--model", default="small", help="Whisper model: base, small, medium, large-v3")
    p.add_argument("--translator", default="auto", choices=["auto", "google", "mymemory", "azure"])


def _print_event(event: dict) -> None:
    stamp = time.strftime("%H:%M:%S", time.localtime(event["time"]))
    kind = event["kind"]
    if kind == "state":
        return
    if kind == "line":
        print(f"[{stamp}] {event['source']}: {event['original']}")
        if event["original"] != event["text"]:
            print(f"[{stamp}] {event['target']}: {event['text']}   ({event['seconds']}s, {event['engine']})")
    elif kind == "error":
        print(f"[{stamp}] ERROR: {event['text']}", file=sys.stderr)
    else:
        print(f"[{stamp}] {event['text']}")


def cmd_ui(args) -> int:
    from .webui import App, serve

    serve(App(args.stt, args.model, args.translator), port=args.port, open_browser=not args.no_browser)
    return 0


def _run_pipeline(args, mode: str) -> int:
    from . import audio
    from .pipeline import Engines, EventLog, Pipeline, PipelineSettings

    output = args.out
    if mode == "speak" and output == "auto":
        cable = audio.find_virtual_cable()
        if cable:
            output = cable["id"]
        else:
            print("No virtual cable found - playing on your speakers (test mode). "
                  "Install VB-CABLE to send the voice into Teams/Zoom.")
            output = None
    elif output in ("auto", "default"):
        output = None

    log = EventLog()
    log.subscribe(_print_event)
    settings = PipelineSettings(
        mode=mode,
        source=args.source,
        target=args.target,
        voice=args.voice,
        input_device=args.input,
        output_device=output,
        speak=(mode == "speak") or args.speak,
        monitor_device=args.monitor if mode == "speak" else None,
        sensitivity=args.sensitivity,
        rate=args.rate,
    )
    pipe = Pipeline(settings, Engines(args.stt, args.model, args.translator), log)
    try:
        pipe.start()
    except Exception:
        return 1
    print("Running - press Ctrl+C to stop.")
    try:
        while pipe.running:
            time.sleep(0.2)
    except KeyboardInterrupt:
        pass
    pipe.stop()
    return 1 if pipe.state == "error" else 0


def cmd_say(args) -> int:
    from . import audio, translate, tts

    text = " ".join(args.text)
    translated, engine = translate.create(args.translator).translate(text, args.source, args.target)
    print(f"{args.target} ({engine}): {translated}")
    clip = tts.synthesize(translated, languages.resolve_voice(args.target, args.voice), rate=args.rate)
    if args.save:
        pcm = (clip.clip(-1, 1) * 32767).astype("<i2").tobytes()
        with wave.open(args.save, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(tts.SAMPLE_RATE)
            wav.writeframes(pcm)
        print(f"Saved {args.save}")
        return 0
    out = audio.AudioOutput(None if args.out in ("", "default") else args.out, tts.SAMPLE_RATE, on_error=print)
    out.start().play(clip)
    time.sleep(len(clip) / tts.SAMPLE_RATE + 0.5)
    out.stop()
    return 0


def cmd_devices(args) -> int:
    from . import audio

    dm, ds = audio.default_microphone(), audio.default_speaker()
    print("Microphones (use with `speak --input`):")
    for d in audio.list_microphones():
        print(f"  {'*' if dm and d['id'] == dm['id'] else ' '} {d['name']}")
    print("\nSpeakers (use with `--out`, or with `listen --input` to capture what they play):")
    for d in audio.list_speakers():
        tag = "  <- virtual cable" if audio.is_virtual_cable(d["name"]) else ""
        print(f"  {'*' if ds and d['id'] == ds['id'] else ' '} {d['name']}{tag}")
    if not audio.find_virtual_cable():
        print("\nNo virtual cable found. Install VB-CABLE (https://vb-audio.com/Cable/) to feed meeting apps.")
    return 0


def cmd_voices(args) -> int:
    from . import tts

    for v in tts.list_voices(args.lang):
        print(f"{v['ShortName']:<34} {v['Gender']:<7} {v['Locale']}")
    return 0


def cmd_languages(args) -> int:
    for lang in languages.LANGUAGES.values():
        print(f"{lang.code:<4} {lang.name:<20} {lang.female_voice} / {lang.male_voice}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="voicebridge", description="Live speech translation for meeting apps.")
    parser.add_argument("--version", action="version", version=f"VoiceBridge {__version__}")
    sub = parser.add_subparsers(dest="command")

    ui = sub.add_parser("ui", help="open the control panel in your browser (default)")
    ui.add_argument("--port", type=int, default=8765)
    ui.add_argument("--no-browser", action="store_true")
    _engine_args(ui)
    ui.set_defaults(func=cmd_ui)

    for mode, help_text in (("speak", "your mic -> translated voice (into a virtual cable)"),
                            ("listen", "meeting audio -> translated captions / voice for you")):
        p = sub.add_parser(mode, help=help_text)
        p.add_argument("--from", dest="source", default="en" if mode == "speak" else "auto",
                       help="spoken language code, or 'auto' (Whisper only)")
        p.add_argument("--to", dest="target", default="gu" if mode == "speak" else "en")
        p.add_argument("--voice", default="female", help="female, male, or a full voice name")
        p.add_argument("--rate", default="+0%", help="voice speed, e.g. +10%%")
        p.add_argument("--input", default=None,
                       help="microphone (speak) or speaker to capture (listen); name or part of it")
        p.add_argument("--out", default="auto" if mode == "speak" else "default",
                       help="where to play the translated voice (speak default: the virtual cable)")
        p.add_argument("--sensitivity", type=float, default=1.0)
        if mode == "speak":
            p.add_argument("--monitor", default=None, help="also play the translation on this device")
        else:
            p.add_argument("--speak", action="store_true", help="read translations aloud (default: captions only)")
        _engine_args(p)
        p.set_defaults(func=lambda a, m=mode: _run_pipeline(a, m))

    say = sub.add_parser("say", help="translate text and speak it (test without a mic)")
    say.add_argument("text", nargs="+")
    say.add_argument("--from", dest="source", default="en")
    say.add_argument("--to", dest="target", default="gu")
    say.add_argument("--voice", default="female")
    say.add_argument("--rate", default="+0%")
    say.add_argument("--out", default="default", help="output device name")
    say.add_argument("--save", default=None, help="write a .wav file instead of playing")
    say.add_argument("--translator", default="auto", choices=["auto", "google", "mymemory", "azure"])
    say.set_defaults(func=cmd_say)

    sub.add_parser("devices", help="list microphones and speakers").set_defaults(func=cmd_devices)
    voices = sub.add_parser("voices", help="list neural voices")
    voices.add_argument("--lang", default=None, help="filter by language code, e.g. gu")
    voices.set_defaults(func=cmd_voices)
    sub.add_parser("languages", help="list built-in languages").set_defaults(func=cmd_languages)
    return parser


def main(argv: list[str] | None = None) -> int:
    _utf8_console()
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        args = parser.parse_args(["ui", *(argv or sys.argv[1:])])
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
