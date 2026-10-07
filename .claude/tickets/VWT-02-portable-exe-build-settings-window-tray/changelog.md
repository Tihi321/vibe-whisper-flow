# Changelog: VWT-02 portable exe + settings window

## 2026-10-07 — Portable PyInstaller build and tkinter settings window (uncommitted)

Outcome: `scripts\build.ps1` produces `dist\VibeFlow\VibeFlow.exe` plus `dist\VibeFlow-0.1.0-win64.zip` (36.8 MB). The zip runs from any folder without Python and keeps config, keys, logs and models next to the exe. The new Settings window covers everything that config.toml, .env and setup-whisper.ps1 did. It opens from the tray (left-click or "Settings…"), X hides it to the tray, and Quit is only in the tray. "Show this window when VibeFlow starts" is saved to config.

Repo: vibe-whisper-flow. Branch: `VWT-02_portable-exe-settings-ui` (cut from `dictation-tool_initial-build` = origin/HEAD; no main/master exists).

### Files

| Path | Change |
|---|---|
| `vibeflow/config.py` | Frozen-aware `ROOT` (exe folder) and `RESOURCES` (`_MEIPASS`), `env_path()`, `portable_path()` (stores paths relative to the app folder), `save_env()` (rewrites `.env` keeping comments; a blank value removes the key; updates `os.environ`), `UiConfig.show_settings_on_start = True`. |
| `vibeflow/whisper_setup.py` (new) | Python port of setup-whisper.ps1: model catalogue and URLs, engine release picking (CPU/CUDA), streaming model download with progress, cancel and a `.part` file, engine install, CLI `python -m vibeflow.whisper_setup`. |
| `vibeflow/single_instance.py` (new) | Named mutex plus "show" event. A second launch signals the running instance to open Settings and exits. |
| `vibeflow/settings_ui.py` (new) | Settings Toplevel on the overlay's Tk root. Tabs: General (hotkey with validation, timings, audio device and threshold, output, pill, launch at login), Transcription (backend, language, model, threads, model manager with download/delete/progress/cancel, engine install CPU/CUDA, cloud models), Cleanup (provider, URL, model list refresh, timeout, prompt with reset, test), API keys (masked, written to .env), About. Also: dirty check on close, banner, sv-ttk theme following Windows light/dark, dark title bar, taskbar button, auto-fit height. |
| `vibeflow/app.py` | DPI awareness, `--hidden`, single-instance handling, `apply_config()` split from `reload_config()`, settings hooks, `open_settings()` (thread-safe; adds the "download a model" banner while the transcriber is unavailable), opens Settings on start (setting) or when no model is installed, `autostart.sync()` on start, whisper model stored as a portable path. |
| `vibeflow/tray.py` | Default "Settings…" item (left-click on the icon). |
| `vibeflow/overlay.py` | `root` accessor; pill pixel sizes scaled by DPI. |
| `vibeflow/autostart.py` | Command now `"<VibeFlow.exe>" --hidden` (frozen) or `pythonw run.pyw --hidden`; `current_command()`, `sync()` rewrites a stale Run value after the folder moves. |
| `vibeflow/transcribe.py` | Missing engine/model hint now points to Settings > Transcription. |
| `packaging/VibeFlow.spec`, `scripts/build.ps1`, `requirements-build.txt` (new) | One-folder windowed build, no UPX, generated icon. The script runs tests (`-SkipTests`), builds, bundles the cached CPU whisper engine (`-Cuda` for the cuBLAS engine, zip gets a `-cuda` suffix) and zips. It never copies config.toml, .env, logs or models. |
| `requirements.txt`, `pyproject.toml`, `.gitignore`, `config.example.toml` | sv-ttk dependency, `build` extra, ignore build/ and dist/, `show_settings_on_start`. |
| `README.md` | Settings window and Portable build sections; updated autostart, permissions, install and layout. |
| `tests/test_settings_ui.py`, `test_whisper_setup.py`, `test_single_instance.py`, `test_app_banner.py` (new); `test_config.py`, `test_autostart.py`, `test_tray.py` | New coverage (see Verification). |

### Deviations from the plan
- "Launch at login" applies immediately from its checkbox rather than on Save. The tray's "Enabled" toggle is not in the Settings window (it is runtime state, not config).
- After you download a model, it is selected automatically only when the active model file is missing.
- sv-ttk needed an explicit `configure_colors` call because `<<ThemeChanged>>` isn't delivered while the root is withdrawn. The pill is unaffected (verified by screenshot).
- `save_env` replaces `"` in values with `'` (the .env parser has no escapes) and drops duplicate lines for updated keys.
- Added after QA of the frozen exe: the banner persists when Settings is reopened while no model is installed; the window auto-fits its height so the engine row is never clipped; the hint text changed in transcribe.py.

### Verification (run, passing)
- `.venv\Scripts\python.exe -m pytest -q tests`: 185 passed (143 before).
- `scripts\build.ps1 -SkipTests`: exit 0; zip 36.8 MB, contains no config.toml, .env, *.bin or logs.
- `python -m vibeflow.whisper_setup --engine` against GitHub: installed release b4938 (whisper-cli + 15 DLLs).
- Frozen exe unzipped into a fresh folder (`.claude/temp/vm`):
  - config.toml and logs\ were created next to the exe.
  - With no model installed, Settings opened on Transcription with the banner.
  - A second launch kept one process (single-instance signal logged), and Settings stayed up with the banner.
  - The dark theme and taskbar button showed.
- `python -m vibeflow.whisper_setup --models tiny` downloaded 77 MB with progress. The bundled whisper-cli plus tiny model transcribed a TTS WAV verbatim.
- The settings window with and without the banner was checked by screenshot, and nothing is clipped.

### Not run (needs a human at the keyboard or other hardware)
- Clicking through the UI in the frozen exe: Download/Delete/Cancel buttons, Install CPU/CUDA, Save/apply, API key save, Test cleanup with live LM Studio, dirty "Save changes?" prompt.
- Real tray left-click, X-to-tray, and Quit from the tray in the exe (the test instance was stopped with Stop-Process).
- Real Ctrl+Win dictation from the exe; mic permission prompt for VibeFlow.exe.
- Writing the Launch-at-login registry value (`--hidden`) and `sync()` against the real registry; no Run value existed on this machine.
- Light theme, 125/150 % DPI, small screens (model list shrink path), `-Cuda` build.
- Running on a second physical machine (SmartScreen / AV behaviour of the unsigned exe).

### Follow-ups / limitations
- The exe is unsigned, so SmartScreen shows a "More info → Run anyway" prompt (documented in README).
- Saving from the UI rewrites config.toml without its comments (same as tray saves before).

Commit status: uncommitted.
