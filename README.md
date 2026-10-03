# VibeFlow

Wispr-Flow-style dictation for Windows. Hold a hotkey, speak, release: the transcript is cleaned up by an LLM and pasted into whatever app has focus. Local-first (whisper.cpp + LM Studio), no accounts, no telemetry.

## Features

- Push-to-talk (hold) and hands-free (double-tap) dictation, system-wide
- Local transcription with whisper.cpp (small / medium models), or Groq / OpenAI Whisper API
- Optional LLM cleanup of fillers, punctuation and casing: LM Studio (default), Groq, OpenAI, Anthropic
- Tiny floating status pill that never steals focus
- Tray icon: enable/disable, pick whisper model, pick cleanup model, launch at login
- Programmable hotkey in `config.toml`

## Requirements

- Windows 10 / 11
- Python 3.11+
- A microphone
- Optional: [LM Studio](https://lmstudio.ai) for local cleanup
- Optional: NVIDIA GPU for the CUDA whisper build (`-Cuda`)

## Install

```powershell
git clone <repo-url> vibe-whisper-flow
cd vibe-whisper-flow
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt

# whisper.cpp binaries + models into .\whisper  (add -Cuda for the NVIDIA build)
powershell -ExecutionPolicy Bypass -File scripts\setup-whisper.ps1 [-Cuda] [-Models small,medium]

copy config.example.toml config.toml
copy .env.example .env
```

Valid model names: `tiny`, `base`, `small`, `medium`, `large-v3`, `large-v3-turbo` (plus `.en` variants of the first four).

## Run

```powershell
pythonw run.pyw            # background, no console window
python run.pyw --console   # prints the log to the terminal
```

A microphone icon appears in the tray. Right-click it for settings; **Quit** exits.

## How to dictate

| Action | Result |
|---|---|
| Hold **Ctrl+Win**, speak, release | Transcribe, clean up, paste |
| Quick double-tap | Hands-free: keeps recording; tap once more to stop and paste. Auto-stops after 5 minutes |
| **Esc** while recording | Cancel, nothing is pasted |
| **Esc** while transcribing | Kill the running transcription |

Recordings with no detectable speech are dropped with "No speech detected" (see `silence_threshold` under Troubleshooting).

Pill colours: red dot = recording, amber dot = hands-free, blue animated dots = transcribing/cleaning up, green = pasted, red text = error.

## Configuring the hotkey

Edit `config.toml` (tray: **Open config.toml**, then **Reload config**):

```toml
[hotkey]
chord = "ctrl+win"      # other examples: "ctrl_r"  "ctrl+shift+space"  "caps_lock"  "f13"
tap_ms = 300            # shorter press counts as a tap
double_tap_ms = 400     # second tap must come within this window
```

Chords are `+`-separated, case-insensitive. Names:

- Modifiers: `ctrl` `alt` `shift` `win` (either side), or a specific side: `ctrl_l` `ctrl_r` `alt_l` `alt_r` `shift_l` `shift_r` `cmd_l` `cmd_r`
- Keys: `a`..`z`, `0`..`9`, `f1`..`f24`, `space`, `tab`, `enter`, `backspace`, `caps_lock`, `esc`, `insert`, `delete`, `home`, `end`, `page_up`, `page_down`
- Anything else: `vk:<number>` (Windows virtual-key code)

The keyboard hook is passive: the keys also reach the focused app. Prefer modifier-only chords (`ctrl+win`, `ctrl_r`), otherwise a chord such as `ctrl+shift+space` types a space or triggers shortcuts in the app. Chords containing `win` get a no-op key injected so the Start menu does not open when you release Win.

## Transcription backends

- **Local (default):** whisper.cpp. `small` is fast, `medium` is more accurate. Switch in the tray (**Transcription**) or set `[transcription.local].model`.
- **Groq / OpenAI API:** put keys in `.env` (`GROQ_API_KEY`, `OPENAI_API_KEY`), then pick the API in the tray or set `[transcription].backend = "groq"` / `"openai"`. Audio is uploaded in that case.
- **Language:** `[transcription].language = "auto"` or an ISO code such as `"en"`, `"hr"`. A fixed language is faster and more reliable than auto-detect.

## LLM cleanup

Default provider is LM Studio:

1. Install LM Studio and download a model (a 3-8B instruct model works well).
2. Developer tab: **Start server** (port 1234; default `base_url` is `http://127.0.0.1:1234/v1`) and enable **Just-in-time model loading**.
3. In the VibeFlow tray, open **Cleanup model** and choose the model (use **Refresh list** after loading new ones), or set `[cleanup].model`.

Other providers: set `[cleanup].provider` to `groq`, `openai` or `anthropic` (keys in `.env`), or `none` to paste raw transcripts. The prompt can be overridden with `[cleanup].prompt`. If cleanup fails or times out, the raw transcript is pasted and the pill shows "Pasted (raw)". When LM Studio is unreachable, cleanup attempts pause for 30 s before retrying.

## Clipboard behaviour

Text is inserted by setting the clipboard and sending Ctrl+V. By default the transcript stays on the clipboard. Set `[output].restore_clipboard = true` to put your previous text clipboard content back after pasting. If an app blocks paste, set `[output].mode = "type"` to type the text key by key instead. Windows Clipboard History (Win+V) may keep copies of transcripts, see below.

## Launch at login

Tray: **Launch at login**. This writes `HKCU\Software\Microsoft\Windows\CurrentVersion\Run\VibeFlow`:

```
"C:\...\vibe-whisper-flow\.venv\Scripts\pythonw.exe" "C:\...\vibe-whisper-flow\run.pyw"
```

## Permissions on Windows

Windows has no equivalent of macOS "Accessibility" or "Input Monitoring" grants. Low-level keyboard hooks (pynput), `SendInput` and clipboard access need no permission for a normal user process.

What you do need:

- **Microphone:** Settings > Privacy & security > Microphone: turn on **Microphone access** and **Let desktop apps access your microphone**. `python.exe` appears under desktop apps after first use.

Caveats:

- **Elevated apps (UIPI):** User Interface Privilege Isolation stops the hotkey and the paste from working in apps running as Administrator (elevated terminals, installers) unless VibeFlow is also launched elevated.
- **Antivirus / EDR:** some products flag keyboard hooks. Allow-list the venv's `python.exe` / `pythonw.exe`.
- **Controlled Folder Access** can block writing to `logs/`; allow the venv Python or move the project.
- **Corporate policy** may block the Run key, which disables "Launch at login".
- **Esc** is observed passively, so it still reaches the app you are typing in.
- **Clipboard History (Win+V)** keeps copies of transcripts unless you disable it (Settings > System > Clipboard).

## Troubleshooting

| Problem | Fix |
|---|---|
| No tray icon | Run `python run.pyw --console` and read `logs\vibeflow.log`; check the hidden-icons overflow |
| `whisper-cli.exe not found` | Re-run `scripts\setup-whisper.ps1`, or check `[transcription.local].whisper_cli` |
| "No microphone" / wrong mic | Set `[audio].device` to part of the device name. List devices: `python -c "import sounddevice as sd; print(sd.query_devices())"` |
| LM Studio unreachable | Check the server is started and the port matches `[cleanup].base_url`; the pill shows "Pasted (raw)" meanwhile |
| Paste does nothing in some app | It is probably elevated (see Permissions), or use `[output].mode = "type"` |
| "No speech detected" on quiet speech, or noise triggers transcriptions | Tune `[audio] silence_threshold = 0.01`: recordings whose peak level is below it are dropped (this prevents whisper's "you" / "Thank you." hallucinations on silence). Raise it if background noise triggers transcriptions, lower it if quiet speech gets dropped |
| Start menu opens on hotkey | Use a different chord |

## Privacy

With the local backend, audio never leaves your machine. Temporary WAV files in `%TEMP%\vibeflow` are deleted after use; set `VIBEFLOW_KEEP_WAV=1` to keep them for debugging. Cloud backends send audio/text only to the provider you configure. No telemetry.

## Project layout

```
run.pyw                  launcher (used by autostart)
config.example.toml      copied to config.toml on first run
.env.example             optional API keys
scripts/setup-whisper.ps1  downloads whisper.cpp + models
vibeflow/
  app.py                 state machine, pipeline, wiring
  hotkeys.py             global hotkey listener, chord parsing
  audio.py               microphone recording
  transcribe.py          whisper.cpp / Groq / OpenAI
  cleanup.py             LLM cleanup (LM Studio, Groq, OpenAI, Anthropic)
  inject.py              clipboard + paste / type
  overlay.py             status pill (tkinter)
  tray.py                tray icon and menu
  autostart.py           Run-key launch at login
  config.py              config + .env handling
tests/
```

## License

MIT (placeholder).
