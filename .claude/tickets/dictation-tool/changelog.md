# Changelog: dictation-tool

## 2026-10-03 — VibeFlow initial build (uncommitted)

Outcome: complete Windows system-wide dictation tray app (hold / double-tap hands-free hotkey,
whisper.cpp or Groq/OpenAI transcription, LM Studio / cloud LLM cleanup, paste into the focused
app, floating pill, tray, autostart). Verified end to end on this machine with a real mic,
the local whisper.cpp binary and a live LM Studio.

Branch: `dictation-tool_initial-build` (cut from the unborn `master`; remote `origin` had no
branches). Nothing committed.

### Files (all new)

| Path | What it does |
|---|---|
| `vibeflow/app.py` | `DictationMachine` state machine (IDLE / REC_HOLD / REC_TAPWAIT / REC_HANDSFREE / PROCESSING), `CleanupBreaker`, `is_silent`, `DictationApp` wiring (pipeline thread with generation counter, lazy cleaner build, cached LM Studio model list refreshed in a background thread), `main()` with `--config/--console/--version`. |
| `vibeflow/hotkeys.py` | Chord parsing (`ctrl+win`, `ctrl_r`, `caps_lock`, …), `ChordTracker`, passive pynput `HotkeyListener` (ignores injected events, sends a no-op VK 0xFF when a Win chord activates so the Start menu stays closed), `wait_for_chord_release`. |
| `vibeflow/audio.py` | `Recorder` on sounddevice, 16 kHz mono int16 WAV in `%TEMP%\vibeflow`, returns `Recording(path, seconds, peak, rms)`. |
| `vibeflow/transcribe.py` | `LocalWhisper` (killable `whisper-cli.exe` subprocess, absolute paths, `[BLANK_AUDIO]` stripping), `CloudWhisper` (Groq / OpenAI multipart), `make_transcriber`. |
| `vibeflow/cleanup.py` | Dictation prompt, `OpenAICompatCleaner` (LM Studio / Groq / OpenAI; `extra_body` with `reasoning_effort: none` for LM Studio and a 400-retry without it), `AnthropicCleaner`, `CleanupUnavailable`, `list_models_detailed` via LM Studio `/api/v0/models` (loaded first, embeddings excluded, `/v1/models` fallback), `make_cleaner` (never JIT-loads an unchosen model). |
| `vibeflow/inject.py` | Clipboard set/get/restore (pywin32), Ctrl+V via SendInput, typing fallback, `insert_text` waits until all chord keys are physically up. |
| `vibeflow/overlay.py` | tkinter pill on the main thread, queue-driven, `WS_EX_NOACTIVATE|WS_EX_TOOLWINDOW`, ticking timer, processing dots, flash with token. |
| `vibeflow/tray.py` | pystray icon + dynamic menu (Enabled, Transcription radio incl. each `ggml-*.bin`, Cleanup model radio from LM Studio, Launch at login, Open config/logs, Reload, Quit), `update_menu()`. |
| `vibeflow/autostart.py` | HKCU Run key `VibeFlow` → `"pythonw.exe" "<root>\run.pyw"`. |
| `vibeflow/config.py`, `logging_setup.py`, `__init__.py`, `__main__.py` | TOML config dataclasses (tomllib/tomli_w), `.env` parser, rotating log in `logs/`. |
| `run.pyw`, `pyproject.toml`, `requirements.txt`, `.gitignore`, `config.example.toml`, `.env.example` | Launcher and packaging. |
| `scripts/setup-whisper.ps1` | Downloads the newest stable `ggml-org/whisper.cpp` Windows release (`whisper-bin-x64.zip`, or cuBLAS with `-Cuda`) and the ggml models (`-Models small,medium`). |
| `README.md` | Install, run, dictation modes, hotkey syntax, backends, LM Studio, clipboard, autostart, Windows permissions and caveats, troubleshooting, privacy. |
| `tests/*` | 143 pytest tests: config, chord parsing/tracker, every state-machine transition, breaker, silence gate, cleanup prompt/sanitiser/providers/model listing, transcribe command building, inject modes, tray icon, autostart command. |

### Deviations from the plan

- Default hotkey is `ctrl+win` (user choice) instead of the plan's original Right Ctrl idea; the Win-key Start-menu guard was added for it.
- Cleanup defaults to LM Studio on `http://127.0.0.1:1234/v1` (not `localhost`: a refused connection to `localhost` takes 4 s on Windows vs 2 s for 127.0.0.1).
- Added beyond the plan after live testing: silence gate (`[audio] silence_threshold`, whisper hallucinated "you" on 2.5 s of silence), 30 s cleanup circuit breaker, cached/background LM Studio model list (menu evaluation used to block for seconds), `reasoning_effort: "none"` (Qwen 3.x thinking models returned empty content under the token budget), loaded-model-first selection and no automatic just-in-time load (the fallback tried to load a 71 GB model).
- The setup script queries the release list instead of `releases/latest` (that tag has no binaries) and takes the newest non-prerelease with a matching asset.
- Tray's whisper-model pick no longer calls `set_backend` separately (the app switches the backend itself).

### Verification

Run and passing:
- `./.venv/Scripts/python.exe -m pytest -q tests` → 143 passed.
- `scripts/setup-whisper.ps1 -Models small` → installed `whisper-cli.exe` + 15 DLLs from release b4938, skipped the existing model.
- Local transcription of a Windows-TTS WAV through `LocalWhisper` → verbatim transcript in 2.3 s.
- Real LM Studio cleanup (nail-qwen3.6-35b-a3b-mtp): filler removal, punctuation, injection sample cleaned not obeyed, Croatian preserved, 0.3–0.9 s per request.
- Live driver (`.claude/temp/live_driver.py`) running the real `DictationApp` with overlay, tray, pynput listener, real mic and whisper, paste mocked: silence gated ("No speech detected"), hold → cleaned paste in 2.6 s, double-tap → hands-free → tap → cleaned paste, Esc during transcription killed whisper (`TranscriptionCancelled`), Esc during recording discarded. PASS.
- Overlay focus check (agent C): foreground window unchanged while the pill is shown; `WS_EX_NOACTIVATE` set.

Not run (needs a human at the keyboard):
- Physical Ctrl+Win hold / double-tap / Esc through the real keyboard hook, and the real Ctrl+V paste into Notepad.
- 5-minute auto-stop with the real timer (covered by unit test with a fake clock only).
- `restore_clipboard = true` with a real clipboard; `mode = "type"`.
- Tray "Launch at login" against the real registry (only `launch_command` is unit-tested).
- Groq / OpenAI / Anthropic paths (no keys available); covered by mocked tests only.

### Environment side effects

- Created `.venv` and installed dependencies; downloaded `whisper/whisper-cli.exe` + DLLs and `whisper/models/ggml-small.bin` (gitignored). `ggml-medium.bin` was not downloaded (run the setup script to fetch it).
- LM Studio: started its local server for testing and stopped it again; the model that was loaded at session start (`nail-qwen3.6-35b-a3b-mtp`) was reloaded after LM Studio had unloaded it mid-session.
- `config.toml` was created from the example (gitignored).

### Follow-ups / known limitations

- Voice commands like "new paragraph" / "period" are passed through as words (not requested).
- Passive hook: the chord keys and Esc also reach the focused app; elevated apps are not reachable without running VibeFlow elevated (documented in README).
- Clipboard restore only handles text clipboards.
- Consider a PyInstaller build for a single-exe distribution later.
