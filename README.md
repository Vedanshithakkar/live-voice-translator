# VoiceBridge: live voice translator for Teams, Zoom & Meet

Talk in **English**, and the people in your meeting hear you in **Gujarati**, or any other
pair of languages (X → Y). It works in **every** call app (Microsoft Teams, Zoom, Google
Meet, Skype, Discord, WhatsApp desktop…) because VoiceBridge shows up as a normal
microphone.

It also works the other way round: **Listen mode** translates what the meeting says into
your language as live captions, and can also read them aloud.

```
 SPEAK MODE  (others hear you translated)
 your mic ─► speech-to-text ─► translate ─► neural voice ─► "CABLE Input" ══► Teams/Zoom mic = "CABLE Output"
   "Hello everyone"            en → gu        gu-IN-Dhwani                     they hear: "દરેકને નમસ્તે"

 LISTEN MODE  (you hear them translated)
 meeting audio on your speakers ─► speech-to-text ─► translate ─► captions (+ optional voice in your headphones)
```

## Quick start (Windows)

1. **Install Python 3.10+** from <https://www.python.org/downloads/> and tick
   *"Add python.exe to PATH"*.
2. **Install the free virtual audio cable** [VB-CABLE](https://vb-audio.com/Cable/):
   unzip, right-click `VBCABLE_Setup_x64.exe` → *Run as administrator* → *Install Driver*, then restart.
   After installing, check that Windows' default **speaker** is still your real
   speakers/headset (Settings → System → Sound). The installer sometimes switches it to "CABLE Input".
3. **Double-click `start.bat`.** The first run installs everything, then the control panel
   opens in your browser at <http://127.0.0.1:8765>.
4. In the **Speak** card choose *I speak: English → They hear: Gujarati*, set
   *Send translated voice to* = **CABLE Input**, and press **Start**.
5. In your meeting app, set the **microphone** to **CABLE Output (VB-Audio Virtual Cable)**:
   * **Teams:** Settings → Devices → Microphone
   * **Zoom:** Settings → Audio → Microphone. Set *Background noise suppression* to *Low*.
   * **Google Meet:** ⋮ → Settings → Audio → Microphone

Now speak in short sentences. About 2–5 seconds after each sentence, the meeting hears it in Gujarati.

> Want to try it before installing the cable? Leave *Send translated voice to* on your
> speakers, or use the **Quick test** box to type a sentence and hear it translated.

## Listen mode

In the **Listen** card choose *They speak* (or *Auto-detect* with Whisper) and *I want*, set
*Capture audio playing on* to the speakers/headset your meeting plays through, and press
**Start**. Translations appear in the transcript as captions. Tick *Read translations aloud*
to also hear them. While a translation is playing on the captured device, VoiceBridge pauses
capture so it never translates itself, so use captions if people talk quickly.

Both modes can run at the same time for a fully two-way conversation.

## Command line

```bat
start.bat                                   :: control panel (default)
start.bat devices                           :: list microphones / speakers
start.bat speak --from en --to gu           :: mic -> Gujarati voice into the virtual cable
start.bat speak --from en --to hi --voice male --monitor "Headphones"
start.bat listen --from gu --to en          :: meeting audio -> English captions
start.bat listen --from auto --to gu --speak
start.bat say --from en --to ta "Can you see my screen?"
start.bat say --to gu --save hello.wav "Hello"
start.bat voices --lang gu                  :: every neural voice for a language
```

Without `start.bat`: `python -m voicebridge …` inside the virtual environment.

## Languages

Built in, each with a female and a male neural voice: English, **Gujarati**, Hindi,
Marathi, Bengali, Tamil, Telugu, Kannada, Malayalam, Urdu, Spanish, French, German,
Italian, Portuguese, Russian, Arabic, Chinese, Japanese, Korean. Any pair works in either
direction. To add a language, add a line to `voicebridge/languages.py` (see `start.bat voices`).

## Engines

| Step | Engine | Notes |
|---|---|---|
| Speech → text | **Whisper** (faster-whisper), *default* | Runs on your PC. Model downloads once (`small` ≈ 470 MB). Can auto-detect the language. Use `medium` for better Indian-language accuracy. |
| | **Google** (online), *automatic fallback* | No download. Used when Whisper can't load (some company laptops block its DLL). |
| Translate | **Google** (free) → **MyMemory** (free), *auto* | Falls back automatically. Google sometimes rate-limits shared office networks. MyMemory allows about 5,000 characters a day, or 50,000 if you set `MYMEMORY_EMAIL`. |
| | **Azure Translator** (your key) | Most reliable. Free tier is 2M characters a month. Set `AZURE_TRANSLATOR_KEY` and `AZURE_TRANSLATOR_REGION`. |
| Text → voice | **Edge neural voices** (edge-tts) | Natural voices, online. |

Change engines in the control panel (*Engines* card) or with `--stt`, `--model`, `--translator`.

## Good to know

* **Delay:** expect 2–5 s per sentence, like a human interpreter. Short sentences with a small pause work best.
* **Privacy:** with the online engines, your speech or text goes to Google / MyMemory /
  Microsoft to be processed. Whisper keeps the audio on your PC, but translation and voice
  still use the internet. Check your company's policy before using it on confidential work calls,
  and tell the other people that they are hearing a machine translation.
* **The control panel only listens on 127.0.0.1**, so other computers and websites can't reach it.
* Works on Windows (tested). macOS/Linux should run *speak* mode with a virtual device such
  as BlackHole / PulseAudio null-sink. Listen-mode loopback is Windows/Linux only.

## Troubleshooting

| Problem | Fix |
|---|---|
| "Whisper unavailable … Application Control policy has blocked this file" | Your PC blocks the Whisper library. VoiceBridge uses Google speech automatically. |
| Translations come from `mymemory` or fail with *TooManyRequests* | Google is rate-limiting your network. MyMemory takes over; for heavy use add an Azure key. |
| The meeting hears nothing | Meeting mic must be **CABLE Output**; VoiceBridge output must be **CABLE Input**; press Start; watch the level meter move when you talk. |
| It cuts my sentences / misses quiet speech | Move the *Mic sensitivity* slider (higher = picks up quieter speech). |
| My own laptop speakers became silent after installing VB-CABLE | Set the default speaker back to your real device in Windows Sound settings. |

## Development

```bat
.venv\Scripts\python -m pip install pytest
.venv\Scripts\python -m pytest
```

Code map: `vad.py` (cuts audio into sentences) → `stt.py` → `translate.py` → `tts.py`,
connected by `pipeline.py`; `audio.py` handles devices; `webui.py` + `static/index.html`
are the control panel; `__main__.py` is the CLI.

Ideas for next steps: capture just the meeting app's audio (Windows process loopback) so
read-aloud never needs to pause, streaming recognition for lower delay, and a system-tray icon.

## License

MIT
