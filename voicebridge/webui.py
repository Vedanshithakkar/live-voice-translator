"""Local control panel: a tiny web server on 127.0.0.1 plus one HTML page.

Only reachable from this computer. POST requests must be JSON from the same
origin, so other websites open in your browser cannot start the microphone.
"""
from __future__ import annotations

import json
import threading
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import __version__, audio, languages, tts
from .pipeline import Engines, EventLog, Pipeline, PipelineSettings

STATIC = Path(__file__).with_name("static")


def _opt(value) -> str | None:
    return value if value not in (None, "", "default") else None


class App:
    def __init__(self, stt_engine: str = "auto", whisper_model: str = "small", translator: str = "auto") -> None:
        self.log = EventLog()
        self.engines = Engines(stt_engine, whisper_model, translator)
        self.pipelines: dict[str, Pipeline] = {}
        self._lock = threading.Lock()

    # -- queries ----------------------------------------------------------------
    def config(self) -> dict:
        return {
            "version": __version__,
            "languages": [{"code": l.code, "name": l.name} for l in languages.LANGUAGES.values()],
            "microphones": audio.list_microphones(),
            "speakers": audio.list_speakers(),
            "default_microphone": audio.default_microphone(),
            "default_speaker": audio.default_speaker(),
            "virtual_cable": audio.find_virtual_cable(),
            "engines": {
                "stt": self.engines.stt_engine,
                "model": self.engines.whisper_model,
                "translator": self.engines.translator_name,
            },
        }

    def state(self, since: int) -> dict:
        with self._lock:
            pipes = dict(self.pipelines)
        status = {}
        for mode, pipe in pipes.items():
            if not pipe.running and pipe.state in ("error",):
                pipe.stop()  # release devices after a failure
            status[mode] = {"state": pipe.state, "running": pipe.running, "level": round(pipe.level, 5)}
        return {"pipelines": status, "events": self.log.since(since)}

    # -- actions ----------------------------------------------------------------
    def start(self, body: dict) -> dict:
        mode = body.get("mode")
        if mode not in ("speak", "listen"):
            raise ValueError("mode must be 'speak' or 'listen'")
        self.engines.configure(body.get("stt"), body.get("model"), body.get("translator"))
        settings = PipelineSettings(
            mode=mode,
            source=body.get("source") or "en",
            target=body.get("target") or "gu",
            voice=body.get("voice") or "female",
            input_device=_opt(body.get("input_device")),
            output_device=_opt(body.get("output_device")),
            speak=bool(body.get("speak", True)),
            monitor_device=_opt(body.get("monitor_device")) if body.get("monitor") else None,
            sensitivity=float(body.get("sensitivity") or 1.0),
        )
        self.stop(mode)
        pipe = Pipeline(settings, self.engines, self.log)
        with self._lock:
            self.pipelines[mode] = pipe
        pipe.start()
        return {"ok": True}

    def stop(self, mode: str) -> dict:
        with self._lock:
            pipe = self.pipelines.get(mode)
        if pipe:
            pipe.stop()
        return {"ok": True}

    def stop_all(self) -> None:
        for mode in list(self.pipelines):
            self.stop(mode)

    def say(self, body: dict) -> dict:
        """Translate typed text and speak it - a quick test without a microphone."""
        text = (body.get("text") or "").strip()
        if not text:
            raise ValueError("Type something to say.")
        source, target = body.get("source") or "en", body.get("target") or "gu"
        if source == languages.AUTO:
            raise ValueError("Pick the language you typed in.")
        voice = languages.resolve_voice(target, body.get("voice") or "female")
        device = _opt(body.get("output_device"))
        self.engines.configure(translator=body.get("translator"))

        def work() -> None:
            try:
                translated, engine = self.engines.translator.translate(text, source, target)
                self.log.add("test", "line", translated, original=text, source=source, target=target,
                             engine=engine, seconds=0)
                clip = tts.synthesize(translated, voice)
                out = audio.AudioOutput(device, tts.SAMPLE_RATE, on_error=lambda m: self.log.add("test", "error", m))
                out.start().play(clip)
                threading.Timer(len(clip) / tts.SAMPLE_RATE + 1.5, out.stop).start()
            except Exception as exc:
                self.log.add("test", "error", f"Test failed: {exc}")

        threading.Thread(target=work, name="say", daemon=True).start()
        return {"ok": True}


def make_handler(app: App, port: int):
    allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
    allowed_origins = {f"http://{h}" for h in allowed_hosts}

    class Handler(BaseHTTPRequestHandler):
        server_version = f"VoiceBridge/{__version__}"

        def log_message(self, fmt, *args):  # keep the console for captions
            pass

        def _send(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, payload, status: int = 200) -> None:
            self._send(status, json.dumps(payload).encode("utf-8"), "application/json; charset=utf-8")

        def _host_ok(self) -> bool:
            return self.headers.get("Host", "") in allowed_hosts

        def do_GET(self) -> None:
            if not self._host_ok():
                return self._json({"error": "forbidden"}, HTTPStatus.FORBIDDEN)
            url = urlparse(self.path)
            try:
                if url.path in ("/", "/index.html"):
                    body = (STATIC / "index.html").read_bytes()
                    return self._send(200, body, "text/html; charset=utf-8")
                if url.path == "/api/config":
                    return self._json(app.config())
                if url.path == "/api/state":
                    since = int(parse_qs(url.query).get("since", ["0"])[0])
                    return self._json(app.state(since))
            except Exception as exc:
                return self._json({"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)
            self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)

        def do_POST(self) -> None:
            origin = self.headers.get("Origin")
            if not self._host_ok() or (origin and origin not in allowed_origins):
                return self._json({"error": "forbidden"}, HTTPStatus.FORBIDDEN)
            if not self.headers.get("Content-Type", "").startswith("application/json"):
                return self._json({"error": "expected JSON"}, HTTPStatus.UNSUPPORTED_MEDIA_TYPE)
            try:
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length) or b"{}")
                path = urlparse(self.path).path
                if path == "/api/start":
                    return self._json(app.start(body))
                if path == "/api/stop":
                    return self._json(app.stop(body.get("mode", "")))
                if path == "/api/say":
                    return self._json(app.say(body))
            except Exception as exc:
                return self._json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)

    return Handler


def serve(app: App, port: int = 8765, open_browser: bool = True) -> None:
    audio.init()  # load the audio backend on the main thread, which lives as long as the app
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(app, port))
    server.daemon_threads = True
    url = f"http://127.0.0.1:{port}/"
    print(f"VoiceBridge control panel: {url}  (Ctrl+C to quit)")
    if open_browser:
        threading.Timer(0.5, webbrowser.open, args=(url,)).start()
    try:
        server.serve_forever(poll_interval=0.3)
    except KeyboardInterrupt:
        print("\nStopping...")
    finally:
        app.stop_all()
        server.server_close()
