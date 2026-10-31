# VibeFlow

Wispr-Flow-style dictation for Windows. Hold a hotkey, speak, release: the transcript is cleaned up by an LLM and pasted into whatever app has focus. Local-first (whisper.cpp + LM Studio), no accounts, no telemetry.

## Features

- Push-to-talk (hold) and hands-free (double-tap) dictation, system-wide
- Local transcription with whisper.cpp (small / medium models), or Groq / OpenAI Whisper API
- Optional LLM cleanup of fillers, punctuation and casing: LM Studio (default), Groq, OpenAI, Anthropic
- Transcribe existing audio/video files (mp3, m4a, mp4, mkv, ...) to plain text, timestamped text, SRT, VTT or timed LRC lyrics
- Tiny floating status pill that never steals focus
- Tray icon: enable/disable, pick whisper model, pick cleanup model, launch at login
- Settings window (tray left-click) for everything: hotkey, models, API keys, cleanup, output
- Portable Windows build (`VibeFlow.exe`): no Python needed on the target machine
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

Instead of `setup-whisper.ps1` you can also download the engine and models from the **Transcription** tab of the Settings window (or `python -m vibeflow.whisper_setup --engine --models small`).

Valid model names: `tiny`, `base`, `small`, `medium`, `large-v3`, `large-v3-turbo` (plus `.en` variants of the first four).

## Run

```powershell
pythonw run.pyw            # background, no console window
python run.pyw --console   # prints the log to the terminal
```

A microphone icon appears in the tray. Left-click it to open the Settings window, right-click for quick menus; **Quit** (tray only) exits.

`--hidden` starts without opening the Settings window (used by launch at login).

## Settings window

Open it with a left-click on the tray icon or **Settings...** in the tray menu. Tabs: **General**, **Microphone**, **Transcription**, **File**, **Cleanup**, **API keys**, **About**.

- Closing the window with **X** only hides it to the tray; **Quit** is available from the tray menu only.
- The **Show this window when VibeFlow starts** checkbox is saved immediately. Uncheck it to start silently in the tray.
- Launching VibeFlow again while it is already running brings the window up instead of starting a second copy.
- Launch at login starts VibeFlow hidden (`--hidden`).
- The Transcription tab downloads whisper models and the whisper engine (CPU or CUDA). API keys are written to `.env`.

## Choose and test the microphone

Pick the microphone on **Settings > Microphone** or from the tray (**Microphone** submenu; the choice is saved immediately). The list shows one entry per microphone. **Windows default** follows the recording device set in Windows Sound settings.

- **Refresh** (also in the tray submenu) rescans for microphones plugged in after VibeFlow started. It is refused while the microphone test or a dictation is running; stop them and try again.
- **Start meter** shows a live level bar in dB with a verdict: "No signal", "Too quiet: dictation would ignore this" (below the silence threshold), "Good" or "Clipping". The meter uses the device selected in the box, even before you save.
- **Record and play back** records a 3, 5 or 10 second clip and plays it on the default output.
- **Record and transcribe** records a clip and shows the raw text from the saved transcription backend (no LLM cleanup).
- **Stop** ends a recording, playback or transcription. The tests also work while you dictate.
- If the saved microphone is not connected, dictation uses the Windows default and shows a one-time notice; the saved name is kept, so the microphone is used again when plugged back in. The test itself says "<name> is not connected".
- If the meter shows "No signal", check Windows Settings > Privacy & security > Microphone.

## Transcribe a file

Open **Settings > File**, click **Browse...** and pick an audio or video file (mp3, wav, m4a, aac, flac, ogg, opus, wma, mp4, mkv, mov, avi, webm). Choose a format and click **Transcribe**:

- **Plain text**, **Text with timestamps** (`[hh:mm:ss]` per line), **SRT subtitles** or **VTT subtitles**. Switching the format re-renders the finished result without transcribing again.
- The engine is the backend saved on the **Transcription** tab (whisper.cpp, Parakeet, Groq or OpenAI). The language box is for this job only and is not saved.
- Audio is decoded locally with PyAV (FFmpeg is bundled), so no extra install is needed. LLM cleanup is not applied to file transcripts.
- Use **Copy** or **Save as...** for the result; nothing is saved automatically. **Cancel** stops a running job.
- Groq and OpenAI accept at most 25 MB per upload, so long files are split into chunks of up to 10 minutes and the timestamps are joined back together. Some models (for example `gpt-4o-transcribe`) return no timestamps; those files get one timestamp per chunk.

## Song lyrics for Spodrive

Turn songs into timed `.lrc` lyrics that the Spodrive web player can import:

1. Open **Settings > File** and set **Format** to **Lyrics (LRC)**. For Croatian songs pick the `large-v3-turbo-hr` model on the **Transcription** tab; for songs in mixed languages use Parakeet v3.
2. Click **Browse...** and select one or more songs, then click **Transcribe**. **Save next to each source file** is on by default for LRC, so every song gets `<song name>.lrc` in its own folder. Files run one after another ("File 2 of 5"), **Cancel** stops the batch, and a file that fails is reported while the rest continue.
3. In Spodrive open **Settings > Import lyrics files (.lrc)** (or a track's menu > **Add lyrics file...**) and pick the `.lrc` files.

- Each file starts with `[ti:]`, `[ar:]` and `[al:]` tags read from the song's metadata (the title falls back to the file name). Spodrive matches lyrics to tracks by those tags first, then by the file name.
- Gaps longer than 4 seconds get an empty timed line, which Spodrive shows as a music note.
- Music under the vocals lowers accuracy, so treat the result as a draft: open the `.lrc` in any text editor, fix it, then import.

## Portable build (exe)

Build (needs the venv from the Install section):

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build.ps1            # add -SkipTests to skip pytest, -Cuda for the CUDA engine
```

This produces `dist\VibeFlow\VibeFlow.exe` and `dist\VibeFlow-<version>-win64.zip` (PyInstaller one-folder build with the CPU whisper engine bundled; models are not).

On another machine: unzip anywhere and run `VibeFlow.exe`. The first run opens the Settings window; download a model from the **Transcription** tab (for example `small`) and start dictating.

- The exe is unsigned, so Windows SmartScreen may warn: click **More info**, then **Run anyway**.
- All data lives next to `VibeFlow.exe`: `config.toml`, `.env`, `logs\` and `whisper\models\`. Copying the folder copies settings and API keys too.

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
- **Local (Parakeet v3):** NVIDIA Parakeet-TDT-0.6B-v3 through [onnx-asr](https://github.com/istupakov/onnx-asr) (CPU, about 670 MB, 25 European languages including Croatian, language is detected automatically). Download it in **Settings > Transcription > Model manager** (`parakeet-tdt-0.6b-v3`), then pick **Local (Parakeet v3)**. Long recordings are split with a Silero VAD. The `language` setting is ignored.
- **Groq / OpenAI API:** put keys in `.env` (`GROQ_API_KEY`, `OPENAI_API_KEY`), then pick the API in the tray or set `[transcription].backend = "groq"` / `"openai"`. Audio is uploaded in that case.
- **Language:** `[transcription].language = "auto"` or an ISO code such as `"en"`, `"hr"`. A fixed language is faster and more reliable than auto-detect.

### Croatian

Whisper `small` is weak on Croatian. Better options, in Settings > Transcription:

- **`large-v3-turbo-hr (Croatian only)`**: a Croatian fine-tune of Whisper large-v3-turbo ([GoranS/whisper-large-v3-turbo-hr-parla](https://huggingface.co/GoranS/whisper-large-v3-turbo-hr-parla), ggml build by JoaoZaokk, Apache-2.0; about 874 MB, `-q5` is 574 MB). Best Croatian accuracy of the options here (FLEURS WER about 8.7). It only understands Croatian: English and other languages come out mangled. With Language set to `auto`, VibeFlow forces `-l hr` for this model. The download is pinned to a specific commit and verified with SHA-256.
- **Parakeet v3** (see above): multilingual, fast on CPU, automatic language detection (FLEURS hr WER about 12.5), a good pick if you switch between Croatian and English.
- `large-v3` / `large-v3-turbo`: multilingual Whisper (hr WER about 11 to 13), slower.

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
"C:\...\vibe-whisper-flow\.venv\Scripts\pythonw.exe" "C:\...\vibe-whisper-flow\run.pyw" --hidden
```

For the portable build it is `"C:\...\VibeFlow\VibeFlow.exe" --hidden`. If you move the folder, the entry is rewritten on the next start.

## Permissions on Windows

Windows has no equivalent of macOS "Accessibility" or "Input Monitoring" grants. Low-level keyboard hooks (pynput), `SendInput` and clipboard access need no permission for a normal user process.

What you do need:

- **Microphone:** Settings > Privacy & security > Microphone: turn on **Microphone access** and **Let desktop apps access your microphone**. `VibeFlow.exe` (or `python.exe` when running from source) appears under desktop apps after first use.

Caveats:

- **Elevated apps (UIPI):** User Interface Privilege Isolation stops the hotkey and the paste from working in apps running as Administrator (elevated terminals, installers) unless VibeFlow is also launched elevated.
- **Antivirus / EDR:** some products flag keyboard hooks. Allow-list `VibeFlow.exe` (or the venv's `python.exe` / `pythonw.exe`).
- **Controlled Folder Access** can block writing to `logs/`; allow the venv Python or move the project.
- **Corporate policy** may block the Run key, which disables "Launch at login".
- **Esc** is observed passively, so it still reaches the app you are typing in.
- **Clipboard History (Win+V)** keeps copies of transcripts unless you disable it (Settings > System > Clipboard).

## Troubleshooting

| Problem | Fix |
|---|---|
| No tray icon | Run `python run.pyw --console` and read `logs\vibeflow.log`; check the hidden-icons overflow |
| `whisper-cli.exe not found` | Install the engine from the Settings window (Transcription tab), re-run `scripts\setup-whisper.ps1`, or check `[transcription.local].whisper_cli` |
| "No microphone" / wrong mic | Pick it on Settings > Microphone (or tray > Microphone), then use **Start meter** and **Record and play back** to check it. Plugged it in after starting? Click **Refresh**. Power users can still set `[audio].device` to part of a device name |
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
scripts/setup-whisper.ps1  downloads whisper.cpp + models (dev)
scripts/build.ps1        builds the portable exe + zip
packaging/VibeFlow.spec  PyInstaller spec
requirements-build.txt   build dependencies (PyInstaller)
vibeflow/
  app.py                 state machine, pipeline, wiring
  hotkeys.py             global hotkey listener, chord parsing
  audio.py               microphone recording, device list, level meter, playback
  transcribe.py          whisper.cpp / Groq / OpenAI
  filetranscribe.py      audio/video file decoding (PyAV) and text/SRT/VTT/LRC output
  cleanup.py             LLM cleanup (LM Studio, Groq, OpenAI, Anthropic)
  inject.py              clipboard + paste / type
  overlay.py             status pill (tkinter)
  tray.py                tray icon and menu
  autostart.py           Run-key launch at login
  config.py              config + .env handling, frozen-aware paths
  settings_ui.py         Settings window (tkinter + sv-ttk)
  whisper_setup.py       whisper engine + model downloader (also a CLI)
  single_instance.py     one running copy; second launch shows the window
tests/
```

## License

MIT (placeholder).
