# VWT-02: Portable exe build + settings window (tray app)

Ticket folder: `.claude/tickets/VWT-02-portable-exe-build-settings-window-tray/`. Branch: `VWT-02_portable-exe-settings-ui` (cut from `dictation-tool_initial-build`; the repo has no `main`/`master`).

## Context

VibeFlow currently runs only from a Python venv in the repo (`pythonw run.pyw`), is configured by hand-editing `config.toml` / `.env`, and needs `scripts/setup-whisper.ps1` to fetch whisper.cpp and models. The user wants to:

1. Build an exe they can copy to another Windows machine and run (no Python there).
2. Have a real UI for everything the scripts/config do today: pick whisper model, download models / whisper engine, choose cleanup provider + model, enter API keys, hotkey, audio, output, launch at login.
3. UI opens from the tray icon; optionally on start ("Show on startup" checkbox persisted, so it can stay hidden from the 2nd start on); window **X hides to tray**; **Quit only from the tray**.

User decisions (asked up front):
- **Packaging:** PyInstaller one-folder build, zipped (portable). CPU whisper-cli + DLLs bundled (~15 MB).
- **Data location:** next to the exe (`config.toml`, `.env`, `logs\`, `whisper\models\`), fully portable.
- **Models:** downloaded from the UI (model manager replaces `setup-whisper.ps1` for end users), not bundled.
- **UI toolkit:** tkinter + `sv-ttk` theme (light/dark following Windows), on the Tk main loop the pill already uses.

## Design

### 1. Frozen-aware paths — `vibeflow/config.py`
- `ROOT` = `Path(sys.executable).parent` when `getattr(sys, "frozen", False)`, else repo root (current behaviour). Put the logic in a small `_app_root()` so it is testable.
- `RESOURCES` = `Path(sys._MEIPASS)` when frozen, else `ROOT`; `load_config` copies `RESOURCES / "config.example.toml"` on first run.
- New `portable_path(p) -> str`: return the path relative to `ROOT` (forward slashes) when it is inside `ROOT`, else absolute. Used whenever the UI/tray stores a whisper model path, so a moved folder keeps working (today `_set_whisper_model` stores the absolute glob path — fix that).
- New `save_env(updates: dict[str, str], path=None)`: rewrite `.env` preserving comments/unknown lines, replace or append keys, blank value = remove key; also set/pop `os.environ` (current `load_env` uses `setdefault`, so a changed key must be written explicitly). Atomic write like `save_config`.
- `UiConfig.show_settings_on_start: bool = True` (+ `config.example.toml` line).

### 2. Whisper engine + model downloader — new `vibeflow/whisper_setup.py`
Python port of `scripts/setup-whisper.ps1` (keep the ps1 for dev use):
- `MODELS: dict[str, int]` (name → approx bytes) for tiny…large-v3-turbo incl. `.en`; `model_url(name)` (HF `ggerganov/whisper.cpp/resolve/main/ggml-<name>.bin`).
- `pick_engine_asset(releases, cuda) -> (tag, asset)`: same rules as the ps1 (skip draft/prerelease, regex `^whisper-bin-x64\.zip$` or `^whisper-cublas-.*-bin-x64\.zip$`, highest version) — pure function, unit tested.
- `install_engine(dest, cuda, progress, cancel)`: download zip to temp, extract, copy `whisper-cli.exe` (or `main.exe`) + DLLs into `dest`.
- `download_model(name, models_dir, progress, cancel)`: `requests` stream to `ggml-<name>.bin.part`, progress via Content-Length, `cancel: threading.Event` → delete `.part` and raise; rename on success.
- `python -m vibeflow.whisper_setup [--engine] [--cuda] [--models small,medium] [--dest DIR]` CLI (used by the build script).

### 3. Settings window — new `vibeflow/settings_ui.py`
- `SettingsWindow(root, hooks: SettingsHooks, post)` builds a `tk.Toplevel` of the overlay's existing hidden Tk root (`Overlay.run` owns the main loop; add `Overlay.root` accessor). Created lazily on first `show()`; `post` = `overlay.call_soon` so worker threads update widgets on the Tk thread.
- `SettingsHooks` dataclass (mirrors `TrayHooks` pattern in `vibeflow/tray.py`): `get_config` (deep copy), `apply(cfg, env_updates)`, `cleanup_models(refresh)`, `test_cleanup(text) -> str`, `is_autostart/set_autostart`, `whisper_status`, `open_logs`, `open_config`.
- `ttk.Notebook` tabs:
  - **General:** Enabled, hotkey chord (entry validated with `hotkeys.parse_chord`, help text with key names), tap / double-tap ms, min / max recording seconds, Launch at login, pill on/off + top/bottom, output mode paste/type, restore clipboard, paste delay.
  - **Transcription:** backend (Local / Groq API / OpenAI API), language combobox (auto, en, hr, de, …, free text), installed-model dropdown, threads; **model manager** table (name, size, Installed/Download/Delete, progress bar, cancel; active model cannot be deleted; after first download auto-select it); **whisper engine** status + "Install CPU" / "Install CUDA (NVIDIA)" buttons; cloud model names.
  - **Cleanup:** enabled, provider (lmstudio/groq/openai/anthropic/none), base URL, model combobox (LM Studio list with Refresh, free text for cloud), timeout, prompt text box with "Reset to default" (`cleanup.DEFAULT_PROMPT`), "Test cleanup" (background thread, shows result).
  - **API keys:** GROQ / OPENAI / ANTHROPIC / LMSTUDIO entries, masked with show toggle, saved to `.env` via `save_env`.
  - **About:** version, app folder, open config.toml / logs folder.
- Bottom bar: "Show this window when VibeFlow starts" checkbox (saved immediately), **Save** (validate → `hooks.apply`), **Close**.
- Pure helpers `form_from_config(cfg) -> dict` / `config_from_form(cfg, form) -> Config` (with validation errors) so the mapping is unit tested without Tk.
- Window behaviour: `protocol("WM_DELETE_WINDOW", hide)` → `withdraw()` (asks "Save changes?" only if the form is dirty); minimize button behaves normally. `show(tab=None)` re-populates from current config, `deiconify/lift/focus_force`. Set `WS_EX_APPWINDOW` on the toplevel frame so it gets a taskbar button (root has `WS_EX_TOOLWINDOW`). Window icon from `tray.make_icon` via `ImageTk`. `sv_ttk.set_theme` from the `AppsUseLightTheme` registry value + dark title bar via `DwmSetWindowAttribute(20)`; verify the pill still renders identically.
- DPI: call `SetProcessDpiAwareness(1)` in `main()` and scale overlay pixel constants (`PILL_H`, `PILL_MIN_W`, margins, dot) by `winfo_fpixels("1i")/96` so the pill keeps its size at 125/150 %.

### 4. App wiring — `vibeflow/app.py`, `vibeflow/tray.py`
- Split `reload_config` into `load_config()` + new `apply_config(new: Config)`; settings `apply` = `save_config` + `save_env` + `apply_config` (+ rebuild transcriber/cleaner already handled there).
- `_set_whisper_model` stores `portable_path(model)`.
- Tray: new `open_settings` hook → top item **"Settings…"** with `default=True` (left-click / double-click on the icon opens it); keep existing quick menus and **Quit** (the only exit). `TrayHooks` gets the new field (update `tests/test_tray.py`).
- Startup (`_start`): show settings if `cfg.ui.show_settings_on_start` and not `--hidden`, **or** if the transcriber is unavailable (new machine without a model) → open on the Transcription tab with a "Download a model to start dictating" banner.
- `main()`: new `--hidden` flag.
- Single instance (pywin32 `win32event`): named mutex `Local\VibeFlow`; a second launch sets named event `Local\VibeFlow.Show` and exits; the first instance has a watcher thread that opens the settings window. Avoids double hooks/double paste when the user double-clicks the exe while the tray app runs.

### 5. Autostart — `vibeflow/autostart.py`
- Frozen: `"<VibeFlow.exe>" --hidden`; source: `"pythonw.exe" "<root>\run.pyw" --hidden`. Login start never pops the window.
- On startup, if autostart is enabled but the stored command differs (folder moved), rewrite it.

### 6. Build — new `packaging/VibeFlow.spec`, `scripts/build.ps1`, `requirements-build.txt`
- Deps: add `sv-ttk` to `requirements.txt` + `pyproject.toml`; `pyinstaller` in `requirements-build.txt` (and `[project.optional-dependencies] build`).
- Spec: entry `run.pyw`, `console=False`, `name="VibeFlow"`, `upx=False` (fewer AV false positives), icon `build/vibeflow.ico`, datas `config.example.toml` + `collect_data_files("sv_ttk")`, hiddenimports `pynput.keyboard._win32`, `pynput.mouse._win32`, `pystray._win32`.
- `scripts/build.ps1` (uses `.venv\Scripts\python.exe`): install build deps → run pytest (`-SkipTests` to skip) → generate `build/vibeflow.ico` from `tray.make_icon` → `pyinstaller packaging/VibeFlow.spec --noconfirm` → ensure cached CPU engine in `build/whisper-engine` via `python -m vibeflow.whisper_setup --engine --dest build/whisper-engine` → copy it to `dist/VibeFlow/whisper/` (+ empty `whisper/models/`) → copy `.env.example`, `README.md` → `Compress-Archive` to `dist/VibeFlow-<version>-win64.zip`. **Never** copies `config.toml`, `.env`, `logs`, or models from the repo.
- `.gitignore`: `build/`, `dist/`.

### 7. Docs
README: "Portable build" (build command, unzip on target, first-run flow), "Settings window" (tray left-click, X hides, Quit in tray, show-on-start), notes that keys live in `.env` next to the exe, SmartScreen "More info → Run anyway" for an unsigned exe, mic permission now lists `VibeFlow.exe`, AV allow-list `VibeFlow.exe`. Update project layout.

## Files

New: `vibeflow/settings_ui.py`, `vibeflow/whisper_setup.py`, `vibeflow/single_instance.py`, `packaging/VibeFlow.spec`, `scripts/build.ps1`, `requirements-build.txt`, tests `tests/test_settings_ui.py`, `tests/test_whisper_setup.py`, `tests/test_single_instance.py`.
Modified: `vibeflow/config.py`, `vibeflow/app.py`, `vibeflow/tray.py`, `vibeflow/overlay.py` (root accessor, DPI scaling), `vibeflow/autostart.py`, `config.example.toml`, `requirements.txt`, `pyproject.toml`, `.gitignore`, `README.md`, `tests/test_config.py`, `tests/test_autostart.py`, `tests/test_tray.py`.

## Verification

1. `.venv\Scripts\python.exe -m pytest -q tests` — existing 143 + new tests: frozen `_app_root`/`RESOURCES`, `portable_path`, `save_env` round-trip (comments kept, blank removes, `os.environ` updated), `pick_engine_asset` with fake release JSON (prerelease skipped, CUDA highest version), `download_model` with mocked stream (progress, cancel removes `.part`), `form_from_config`/`config_from_form` incl. invalid chord, frozen autostart command with `--hidden`, single-instance mutex/event with a unique name, Tk smoke test building the window and hide-on-close.
2. `powershell -ExecutionPolicy Bypass -File scripts\build.ps1` → `dist\VibeFlow\VibeFlow.exe` + zip; check zip contains no `config.toml`, `.env`, models.
3. "Other machine" simulation: unzip into a fresh temp folder (outside repo, with the dev app quit) and run `VibeFlow.exe`:
   - settings window opens on Transcription (no model); `config.toml` + `logs\` created next to exe;
   - download `tiny` with progress → auto-selected; Ctrl+Win dictation into Notepad works;
   - LM Studio: start server (`lms server start`), Refresh lists models, Test cleanup works; restore LM Studio state afterwards (server off, `nail-qwen3.6-35b-a3b-mtp` loaded);
   - enter a dummy API key → `.env` written next to exe;
   - X hides to tray; tray left-click reopens; uncheck "Show on startup", Quit from tray, relaunch → stays in tray; launch exe again while running → window comes up, no second instance (one entry in Task Manager);
   - Launch at login → Run key = `"<temp>\VibeFlow\VibeFlow.exe" --hidden`; then disable it;
   - pill looks right at current DPI, light/dark theme matches Windows.
4. Record results in `.claude/tickets/VWT-02-exe-and-settings-ui/changelog.md`.

## Status (2026-10-07)

- [x] §1 frozen-aware paths, `portable_path`, `save_env`, `show_settings_on_start`
- [x] §2 `whisper_setup.py` (engine + model download, CLI)
- [x] §3 settings window (tabs, model manager, engine install, keys, dirty check, theme, DPI)
- [x] §4 app wiring, tray "Settings…", single instance, `--hidden`
- [x] §5 autostart `--hidden` + `sync()`
- [x] §6 PyInstaller spec + `scripts/build.ps1` + zip
- [x] §7 README
- Verification: pytest 185 passed; build OK; frozen exe smoke-tested from a fresh folder. Manual UI click-through, real dictation from the exe, registry autostart and other DPI/theme setups not yet run. See `changelog.md`.
