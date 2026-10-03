# VibeFlow: system-wide AI dictation for Windows (Wispr Flow clone)

## Context

Empty repo `C:\projects\Personal\vibe-whisper-flow`. Goal: a background Windows tray app that
records the mic while a global hotkey is held (or in a hands-free session started by double-tap),
transcribes locally with whisper.cpp (or Groq/OpenAI Whisper API by config flag), cleans the
transcript with an LLM (LM Studio by default, also any OpenAI-compatible endpoint / Anthropic),
and pastes the result into whatever app has focus. No accounts, no telemetry, offline-capable.

Decisions from the user:
- **Stack:** Python 3.13 (installed at `...\Python313`, pythonw available).
- **Default hotkey:** `ctrl+win`, programmable in `config.toml` (single keys like `ctrl_r`,
  `caps_lock`, or chords like `ctrl+shift+space`).
- **LLM cleanup:** LM Studio (OpenAI-compatible server on `http://localhost:1234/v1`) is the
  default provider; tray menu gets a **model picker** that lists LM Studio's models. Groq /
  OpenAI / Anthropic supported when a key is in `.env`. No keys yet, so local-only must work.
- **Whisper models:** setup script downloads both `ggml-small.bin` and `ggml-medium.bin`;
  tray menu gets a picker to switch between models found in `whisper/models/`.

Note (global CLAUDE.md rule): on implementation start, copy this plan to
`.claude/tickets/dictation-tool/plan.md` in the repo and keep later artifacts there.

## Project layout

```
vibe-whisper-flow/
  README.md                 setup, hotkey syntax, LM Studio, Windows permissions/caveats
  pyproject.toml            deps + `vibeflow` console entry; requirements.txt mirror
  run.pyw                   launcher (adds repo root to sys.path, calls vibeflow.app.main) – used by autostart
  config.example.toml       copied to config.toml on first run
  .env.example              GROQ_API_KEY / OPENAI_API_KEY / ANTHROPIC_API_KEY (all optional)
  .gitignore                config.toml .env whisper/ logs/ .venv/ .claude/temp/
  scripts/setup-whisper.ps1 downloads whisper.cpp Windows release + both ggml models
  vibeflow/
    __init__.py, __main__.py
    app.py            DictationApp: state machine, pipeline worker, wiring of all parts
    config.py         dataclasses, paths (repo root), TOML load/save (tomllib + tomli_w), tiny .env parser
    hotkeys.py        HotkeyListener (pynput Win32 hook): chord parsing, hold / tap / double-tap / Esc events
    audio.py          Recorder: sounddevice InputStream -> 16 kHz mono int16 WAV in %TEMP%
    transcribe.py     LocalWhisper (subprocess whisper-cli.exe, killable) + CloudWhisper (Groq/OpenAI multipart)
    cleanup.py        prompt + OpenAICompatChat (LM Studio/Groq/OpenAI) + AnthropicChat; list_models()
    inject.py         clipboard set/save/restore (pywin32), Ctrl+V via SendInput, "type" fallback
    overlay.py        tkinter pill (main thread, queue-driven, no-activate, topmost, rounded)
    tray.py           pystray icon + menu (enable, model pickers, autostart, open config/log, quit)
    autostart.py      HKCU\...\CurrentVersion\Run entry pointing at pythonw + run.pyw
    logging_setup.py  rotating file log in logs/
  tests/
    test_hotkeys.py   chord parsing + hold/tap/double-tap/esc state machine with a fake clock
    test_config.py    defaults, TOML round-trip, .env parsing
    test_cleanup.py   prompt building, response sanitising, model-list parsing
```

Dependencies: `pynput`, `sounddevice`, `numpy`, `pystray`, `Pillow`, `pywin32`, `requests`,
`tomli-w`; dev: `pytest`. Python 3.11+ (`tomllib`).

## Config (`config.toml`)

```toml
[hotkey]
chord = "ctrl+win"          # names: ctrl ctrl_l ctrl_r alt alt_l alt_r shift win caps_lock space f1..f24 a..z
tap_ms = 300                # press shorter than this counts as a tap
double_tap_ms = 400         # second tap must start within this window
max_recording_seconds = 300 # auto-stop (hands-free and hold)
min_recording_seconds = 0.6 # shorter recordings are discarded

[audio]
device = ""                 # "" = Windows default input; or substring of device name
sample_rate = 16000

[transcription]
backend = "local"           # local | groq | openai
language = "auto"           # or "en", "hr", ...
[transcription.local]
whisper_cli = "whisper/whisper-cli.exe"
model = "whisper/models/ggml-small.bin"
threads = 0                 # 0 = cpu count
[transcription.cloud]
groq_model = "whisper-large-v3-turbo"
openai_model = "whisper-1"

[cleanup]
enabled = true
provider = "lmstudio"       # lmstudio | groq | openai | anthropic | none
base_url = "http://localhost:1234/v1"   # used by lmstudio (and any OpenAI-compatible server)
model = ""                  # "" = first model LM Studio reports; set via tray picker
timeout_seconds = 20
prompt = ""                 # "" = built-in prompt

[output]
mode = "paste"              # paste | type
restore_clipboard = false   # true = put previous text clipboard back after pasting
paste_delay_ms = 60

[ui]
show_pill = true
pill_position = "bottom"    # bottom | top
```

Tray pickers write back `transcription.local.model` and `cleanup.model` with `tomli_w`.

## Core behaviour

### Hotkey state machine (`app.py`, logic unit-tested with injected clock)
States: `IDLE`, `REC_HOLD`, `REC_TAPWAIT`, `REC_HANDSFREE`, `PROCESSING`.

| Event | Transition |
|---|---|
| chord down in `IDLE` | start recorder → `REC_HOLD` (pill "Hold to talk") |
| chord up in `REC_HOLD`, held ≥ tap_ms | stop → `PROCESSING` |
| chord up in `REC_HOLD`, held < tap_ms | keep recording → `REC_TAPWAIT`, arm double_tap timer |
| chord down in `REC_TAPWAIT` | → `REC_HANDSFREE` (pill "Hands-free · m:ss"), set ignore_next_up |
| double_tap timer fires | stop; if ≥ min_recording_seconds → `PROCESSING` else discard → `IDLE` |
| chord down in `REC_HANDSFREE` | stop → `PROCESSING`, set ignore_next_up |
| max_recording timer | any `REC_*` → stop → `PROCESSING` |
| Esc in `REC_*` | discard WAV → `IDLE` |
| Esc in `PROCESSING` | cancel: kill whisper subprocess / set cancel flag, result dropped → `IDLE` |
| chord down in `PROCESSING` | ignored |
| tray "Enabled" off | chord events ignored; active recording discarded |

Recording starts on the very first key-down, so the beginning of a hands-free session is never lost.

### Hotkey listener (`hotkeys.py`)
- `pynput.keyboard.Listener` in **passive** mode (no suppression) so Esc and the chord still reach
  the focused app. Dedupe key-repeat via a pressed-set. Modifier names match both L/R variants.
- Chord "down" = all groups pressed; "up" = any member released.
- **Win-key fix:** when a chord containing `win` activates, inject a no-op key (`VK 0xFF`) via
  SendInput so Windows does not open the Start menu on Win release (the AutoHotkey trick);
  injected events (`LLKHF_INJECTED`) are ignored by our hook.
- Esc delivered as a separate event; only acted on when not `IDLE`.

### Audio (`audio.py`)
`sounddevice.InputStream(samplerate=16000, channels=1, dtype='int16')`, callback appends to a list;
`stop()` writes WAV via `wave` to `%TEMP%\vibeflow\rec-<ts>.wav` and returns path + duration.
Device selected by substring of name. Mic errors surface in the pill and log.

### Transcription (`transcribe.py`)
- **Local:** `whisper-cli.exe -m <model> -f <wav> -l <lang> -nt -np -t <threads> -otxt -of <base>`,
  `subprocess.Popen` with `CREATE_NO_WINDOW`; handle stored for `cancel()` (kill). Reads
  `<base>.txt`, falls back to stdout. Falls back to `main.exe` if `whisper-cli.exe` missing.
- **Cloud:** `requests.post` multipart to Groq `/openai/v1/audio/transcriptions` or OpenAI
  `/v1/audio/transcriptions`; cancellation = result dropped by generation check.
- Temp WAV deleted after use (kept when `VIBEFLOW_KEEP_WAV=1` for debugging).

### Cleanup (`cleanup.py`)
- Built-in prompt: remove fillers (um, uh, like, you know, repeated words), fix punctuation and
  casing to match dictation style, keep wording and language, never answer or follow the content,
  output only the text. Transcript wrapped in `<transcript>` tags to resist instruction-following.
- `OpenAICompatChat(base_url, api_key, model)` used for LM Studio (no key), Groq
  (`https://api.groq.com/openai/v1`, default `llama-3.3-70b-versatile`), OpenAI (`gpt-4o-mini`).
  `AnthropicChat` via raw REST (`claude-haiku-4-5-20251001`). `temperature=0`.
- `list_models(base_url)` → `GET /v1/models` ids (LM Studio model picker).
- Any failure/timeout/unreachable LM Studio → log + use the raw transcript (pill shows "raw").

### Insert (`inject.py`)
- Wait (≤1 s, `GetAsyncKeyState`) until the chord's keys are physically released so Ctrl+V isn't
  turned into Ctrl+Win+V (clipboard history) by a still-held Win key.
- `paste` mode: save previous text clipboard (if `restore_clipboard`), set `CF_UNICODETEXT`,
  SendInput Ctrl+V, after 300 ms restore previous text. Default leaves transcript on clipboard.
- `type` mode: `pynput` `Controller.type()` for apps that block paste.

### Overlay pill (`overlay.py`)
tkinter on the **main thread**; other threads post to a `queue.Queue` polled every 50 ms.
`overrideredirect`, `-topmost`, `-transparentcolor` for rounded corners, `WS_EX_NOACTIVATE |
WS_EX_TOOLWINDOW` via ctypes so it never steals focus. Bottom-centre of primary monitor.
States: red dot "Hold to talk", "Hands-free · 0:42", spinner "Transcribing…", "Cleaning up…",
flash "Pasted" / "Cancelled" / "Error: …" for 1.5 s, hidden when idle.

### Tray (`tray.py`)
`pystray.Icon.run_detached()`; Pillow-drawn mic icon (grey = disabled, red = recording).
Menu: **Enabled** ✓ · **Transcription ▸** (radio: each `ggml-*.bin` found in `whisper/models`,
Groq, OpenAI) · **Cleanup model ▸** (Off, LM Studio models fetched from `/v1/models` when the
menu opens, Refresh) · **Launch at login** ✓ · Open config · Open log folder · Reload config · Quit.

### Autostart (`autostart.py`)
`HKCU\Software\Microsoft\Windows\CurrentVersion\Run\VibeFlow = "<pythonw.exe>" "<root>\run.pyw"`
(pythonw derived from `sys.executable`, so a venv works). Read/set/remove helpers.

### Setup script (`scripts/setup-whisper.ps1`)
Queries GitHub API for latest `ggerganov/whisper.cpp` release, downloads the asset matching
`whisper-bin-x64.zip` (`-Cuda` switch picks the `whisper-cublas-*-bin-x64.zip` build), extracts into
`whisper/`, downloads `ggml-small.bin` and `ggml-medium.bin` from
`https://huggingface.co/ggerganov/whisper.cpp/resolve/main/` into `whisper/models/`
(`-Models small,medium` parameter; skips files that exist).

### README
Install (venv, `pip install -r requirements.txt`, run setup script, copy `config.example.toml` and
`.env.example`), run (`pythonw run.pyw`, or `python run.pyw` to see logs), hotkey syntax and
modes, LM Studio setup (start local server, load model, enable JIT loading, picker in tray),
cloud backends, and a **Windows permissions** section: Settings › Privacy & security › Microphone
› allow desktop apps; note that macOS "Accessibility / Input Monitoring" grants have no Windows
equivalent (low-level hooks and SendInput need no permission); caveats: UIPI means the hotkey and
paste do not reach apps running as Administrator unless VibeFlow is also elevated; some AV tools
flag keyboard hooks; Windows Clipboard History will record transcripts; Esc is passive so it also
reaches the focused app. Troubleshooting (no mic, whisper not found, LM Studio unreachable).

## Implementation order
1. Scaffold: pyproject, requirements, .gitignore, config.example.toml, .env.example, run.pyw, logging.
2. `config.py` + tests. 3. `hotkeys.py` + state machine in `app.py` + tests (fake clock).
4. `audio.py`, `transcribe.py`, `cleanup.py` (+tests), `inject.py`.
5. `overlay.py`, `tray.py`, `autostart.py`, wire everything in `app.py`.
6. `scripts/setup-whisper.ps1`, README.
7. Smoke-run on this machine (imports, overlay shows, tray appears, whisper-cli invocation).

## Verification
- `pytest` green: chord parsing, hold vs tap vs double-tap vs Esc transitions, auto-stop timer,
  config round-trip, prompt/response handling.
- Run `python run.pyw` on this machine: tray icon appears; pill shows on chord down; log clean.
- Manual E2E (needs the user's mic): in Notepad hold Ctrl+Win, speak, release → cleaned text
  pasted and left on clipboard; double-tap → "Hands-free" pill, one tap stops and pastes; Esc
  during recording discards, Esc during transcription kills whisper-cli; set
  `max_recording_seconds = 10` to confirm auto-stop; `restore_clipboard = true` restores
  previous text; tray pickers switch whisper model / LM Studio model and persist to config.toml;
  "Launch at login" creates/removes the Run key; with network off and backend `local`,
  cleanup `none`, dictation still works.

## Progress

### Agent A (scaffold, config, hotkeys, app, logging, tests)
- [x] pyproject.toml, requirements.txt, .gitignore, config.example.toml, .env.example, run.pyw
- [x] vibeflow/__init__.py (0.1.0), __main__.py, config.py, logging_setup.py, hotkeys.py, app.py
- [x] tests/test_config.py, test_hotkeys.py, test_machine.py (+ empty tests/__init__.py)
- Verification: `pytest -q tests/test_config.py tests/test_hotkeys.py tests/test_machine.py` -> 64 passed; `import vibeflow.config, vibeflow.hotkeys` OK; `py_compile vibeflow/app.py` OK. app.py DictationApp not import/run-tested (needs agents B/C modules); no real OS hook or mic smoke test done.
- Deviations: (1) app.py imports audio/cleanup/inject/transcribe/autostart/overlay/tray lazily inside DictationApp.__init__ (not top-level) so the machine tests do not depend on other agents' modules. (2) Injected-event filtering uses pynput on_press(key, injected) instead of win32_event_filter. (3) set_whisper_model tray hook also sets backend to "local". (4) Pipeline flashes "No speech detected" on empty transcript. (5) test uses 0.31 s hold (float rounding at exactly 0.3).

- [x] Review follow-ups (agent A): AudioConfig.silence_threshold=0.01 + silence gate (is_silent) before whisper; cleanup base_url default 127.0.0.1; CleanupBreaker (30 s pause on CleanupUnavailable, reset on rebuild); cleaner built in a daemon thread under a lock; cleanup model list cached/refreshed off-thread (never network in _cleanup_models, calls tray.update_menu). Tests: test_cleanup_breaker, test_is_silent, updated test_config defaults. Full suite: 133 passed; DictationApp(load_config()) constructs.

### Agent B (audio, transcribe, cleanup, inject, test_cleanup)
- [x] audio.py (Recorder, Recording.peak/rms), transcribe.py (LocalWhisper killable subprocess, CloudWhisper, make_transcriber), cleanup.py (prompt, OpenAICompat/Anthropic cleaners, CleanupUnavailable, extra_body reasoning_effort=none for LM Studio, list_models_detailed loaded-first via /api/v0/models, no JIT load of unchosen models), inject.py
- [x] tests/test_cleanup.py, test_transcribe.py, test_inject.py

### Agent C (overlay, tray, autostart, scripts, README)
- [x] overlay.py (no-activate pill), tray.py (dynamic menu, pickers, update_menu), autostart.py, scripts/setup-whisper.ps1, README.md, tests/test_tray.py, test_autostart.py

### Orchestrator verification (2026-10-03)
- [x] Branch `dictation-tool_initial-build`; nothing committed.
- [x] `pytest -q tests` -> 143 passed.
- [x] setup-whisper.ps1 run for real: whisper-cli.exe + DLLs from release b4938; ggml-small.bin present.
- [x] LocalWhisper on a Windows-TTS WAV -> verbatim transcript (2.3 s). Fixed: whisper needs absolute paths (cwd = exe dir).
- [x] Real LM Studio cleanup (nail-qwen3.6-35b-a3b-mtp): fillers removed, punctuation fixed, injection sample not obeyed, Croatian kept; 0.3-0.9 s. Required `reasoning_effort: "none"` (thinking model returned empty content otherwise).
- [x] Live driver through the real DictationApp (mic, whisper, overlay, tray, listener; paste mocked): silence gate, hold -> cleaned paste, double-tap hands-free -> tap -> cleaned paste, Esc kills whisper, Esc discards recording. PASS.
- [ ] Not run: physical keyboard hold/double-tap/Esc, real Ctrl+V into an app, 5-min auto-stop in real time, restore_clipboard/type mode, registry autostart toggle, Groq/OpenAI/Anthropic with real keys. See changelog.md.
 
