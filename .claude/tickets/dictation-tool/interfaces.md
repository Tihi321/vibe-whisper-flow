# VibeFlow module contracts

Binding interfaces for parallel implementation. Every module must expose exactly these names
and signatures (extra private helpers are fine). Package: `vibeflow`. Python 3.11+ (we run 3.13).
Repo root = parent directory of the `vibeflow` package. All thread-crossing callbacks must be
fast and non-blocking unless stated otherwise. Use `logging.getLogger(__name__)` everywhere.

## vibeflow/config.py  (owner: agent A)

```python
ROOT: Path                     # repo root, computed from __file__
@dataclass class HotkeyConfig: chord: str = "ctrl+win"; tap_ms: int = 300; double_tap_ms: int = 400
                               max_recording_seconds: float = 300; min_recording_seconds: float = 0.6
@dataclass class AudioConfig: device: str = ""; sample_rate: int = 16000
@dataclass class LocalWhisperConfig: whisper_cli: str = "whisper/whisper-cli.exe"
                               model: str = "whisper/models/ggml-small.bin"; threads: int = 0
@dataclass class CloudWhisperConfig: groq_model: str = "whisper-large-v3-turbo"; openai_model: str = "whisper-1"
@dataclass class TranscriptionConfig: backend: str = "local"   # local | groq | openai
                               language: str = "auto"; local: LocalWhisperConfig; cloud: CloudWhisperConfig
@dataclass class CleanupConfig: enabled: bool = True; provider: str = "lmstudio"  # lmstudio|groq|openai|anthropic|none
                               base_url: str = "http://localhost:1234/v1"; model: str = ""
                               timeout_seconds: float = 20; prompt: str = ""
@dataclass class OutputConfig: mode: str = "paste"  # paste | type
                               restore_clipboard: bool = False; paste_delay_ms: int = 60
@dataclass class UiConfig: show_pill: bool = True; pill_position: str = "bottom"  # bottom | top
@dataclass class Config: hotkey, audio, transcription, cleanup, output, ui  (all default_factory)

def config_path() -> Path            # $VIBEFLOW_CONFIG or ROOT/"config.toml"
def load_config(path: Path | None = None) -> Config
    # if missing: copy ROOT/config.example.toml to path (or write defaults); unknown keys ignored,
    # missing keys default; never raises on bad values, logs a warning and uses the default
def save_config(cfg: Config, path: Path | None = None) -> None   # tomli_w, atomic write (tmp + replace)
def config_to_dict(cfg: Config) -> dict
def config_from_dict(d: dict) -> Config
def load_env(path: Path | None = None) -> dict[str, str]
    # ROOT/.env; supports KEY=VALUE, "export KEY=VALUE", quotes, # comments, blank lines;
    # os.environ.setdefault for each key; returns parsed dict; missing file -> {}
def get_secret(name: str) -> str | None   # os.environ.get, "" -> None
def resolve_path(p: str | Path) -> Path    # absolute as-is, else ROOT / p
```

## vibeflow/hotkeys.py  (owner: agent A)

Canonical key ids (lower-case strings): `ctrl_l ctrl_r alt_l alt_r shift_l shift_r cmd_l cmd_r
caps_lock space esc tab enter backspace insert delete home end page_up page_down f1..f24 a..z 0..9`
and `vk:<int>` for anything else. Chord spec grammar: `+`-separated names, case-insensitive,
spaces allowed. Aliases: `ctrl|control` -> {ctrl_l, ctrl_r}; `alt` -> {alt_l, alt_r};
`shift` -> {shift_l, shift_r}; `win|super|cmd|meta` -> {cmd_l, cmd_r}; `capslock|caps` -> caps_lock;
`escape` -> esc; specific sides `ctrl_l`, `rctrl`, `right_ctrl`, `lwin`, `right_win` etc. map to a single id.

```python
class Chord:
    groups: tuple[frozenset[str], ...]   # chord is down when every group has >=1 pressed member
    spec: str
    @property has_win(self) -> bool
    def describe(self) -> str            # "Ctrl + Win"
def parse_chord(spec: str) -> Chord      # raises ValueError with a helpful message
class ChordTracker:                      # pure logic, no OS calls; unit-tested
    def __init__(self, chord: Chord)
    def press(self, key_id: str) -> bool    # True exactly when the chord transitions to "down" (repeat-safe)
    def release(self, key_id: str) -> bool  # True exactly when a down chord transitions to "up"
    def set_chord(self, chord: Chord) -> None
    @property is_down(self) -> bool
class HotkeyListener:
    def __init__(self, chord: Chord, on_chord_down: Callable[[], None], on_chord_up: Callable[[], None],
                 on_escape: Callable[[], None])
    def start(self) -> None   # pynput.keyboard.Listener, passive (no suppression), daemon thread
    def stop(self) -> None
    def set_chord(self, chord: Chord) -> None
    # - uses win32_event_filter to ignore injected events (LLKHF_INJECTED flag 0x10) and to map vk codes
    # - when a chord containing win becomes down, inject a no-op key (vk 0xFF) via SendInput so the
    #   Start menu does not open on Win release
    # - on_escape fires on every physical Esc press (app decides whether it matters)
def key_is_down(key_id: str) -> bool                      # GetAsyncKeyState
def wait_for_chord_release(chord: Chord, timeout: float = 1.0) -> bool   # polls every 10 ms
def key_id_to_vk(key_id: str) -> int | None
```

## vibeflow/app.py  (owner: agent A)

```python
class State(str, Enum): IDLE, REC_HOLD, REC_TAPWAIT, REC_HANDSFREE, PROCESSING
class Mode(str, Enum): HOLD, HANDSFREE
class MachineActions(Protocol):          # implemented by DictationApp; machine calls these under its lock
    def start_recording(self) -> bool                 # False if the mic failed (machine stays IDLE)
    def stop_and_process(self, mode: Mode) -> None    # stop recorder, launch pipeline; must call machine.processing_done() later
    def discard_recording(self) -> None
    def cancel_processing(self) -> None
    def state_changed(self, state: State, mode: Mode | None) -> None
Timer = Callable[[float, Callable[[], None]], Any]      # schedule(delay_seconds, fn) -> handle
class DictationMachine:                  # pure logic; unit-tested with fake clock/timers
    def __init__(self, cfg: HotkeyConfig, actions: MachineActions, clock=time.monotonic,
                 schedule: Timer = <threading.Timer based>, cancel: Callable[[Any], None] = <timer.cancel>)
    state: State; mode: Mode | None; enabled: bool
    def chord_down(self) -> None
    def chord_up(self) -> None
    def escape(self) -> None
    def processing_done(self) -> None    # pipeline finished or failed -> IDLE
    def set_enabled(self, enabled: bool) -> None   # disabling while recording discards
    def recording_seconds(self) -> float
    # transitions exactly as in plan.md table; internal timers: double_tap (double_tap_ms) and max (max_recording_seconds)
class DictationApp:                      # wiring: config, env, listener, recorder, pipeline thread, overlay, tray
    def __init__(self, cfg: Config)
    def run(self) -> None                # blocks on overlay.run() (tk main loop); starts listener + tray first
    def reload_config(self) -> None
    def quit(self) -> None
def main(argv: list[str] | None = None) -> int   # --config PATH, --console (log to stderr too), --version
```

Pipeline (worker thread, generation counter): `recording = recorder.stop()` -> if seconds <
min_recording_seconds discard -> overlay "Transcribing…" -> `transcriber.transcribe()` -> if cancelled
drop -> if cleaner: overlay "Cleaning up…" -> `cleaner.clean()` (failure -> raw text, suffix pill with
"(raw)") -> `inject.insert_text()` -> overlay flash "Pasted" -> `machine.processing_done()`. Esc in
PROCESSING -> `cancel_processing()`: bump generation, call `transcriber.cancel()` / `cleaner.cancel()`.
WAV deleted in `finally` unless env `VIBEFLOW_KEEP_WAV=1`.

## vibeflow/audio.py  (owner: agent B)

```python
class AudioError(Exception)
@dataclass class Recording: path: Path; seconds: float
def list_input_devices() -> list[tuple[int, str]]
class Recorder:
    def __init__(self, cfg: AudioConfig)
    def start(self) -> None          # opens sounddevice.InputStream(16 kHz mono int16 unless cfg differs); AudioError on failure
    def stop(self) -> Recording      # closes stream, writes %TEMP%/vibeflow/rec-<timestamp>.wav, returns path+seconds
    def discard(self) -> None        # closes stream, no file
    def elapsed(self) -> float
    @property is_recording(self) -> bool
```
Device: `""` -> default input; else first device whose name contains cfg.device (case-insensitive);
AudioError("Input device '...' not found") otherwise.

## vibeflow/transcribe.py  (owner: agent B)

```python
class TranscriptionError(Exception)
class TranscriptionCancelled(TranscriptionError)
class Transcriber(Protocol):
    def transcribe(self, wav: Path, language: str) -> str   # language "auto" or ISO code; blocking
    def cancel(self) -> None                                 # thread-safe; makes a running transcribe() raise TranscriptionCancelled
class LocalWhisper:   # subprocess: <cli> -m <model> -f <wav> -l <lang> -nt -np -t <threads> -otxt -of <base>
    def __init__(self, cfg: LocalWhisperConfig)
    def available(self) -> tuple[bool, str]   # (ok, reason); checks exe (whisper-cli.exe, fallback main.exe in same dir) and model file
    # Popen with CREATE_NO_WINDOW, cwd = exe dir (DLLs); cancel() kills the process; reads <base>.txt, falls back to stdout;
    # strips [BLANK_AUDIO] and whitespace; nonzero exit -> TranscriptionError(stderr tail)
class CloudWhisper:   # provider "groq" -> https://api.groq.com/openai/v1/audio/transcriptions ; "openai" -> https://api.openai.com/v1/audio/transcriptions
    def __init__(self, provider: str, api_key: str, model: str, timeout: float = 60)
    # multipart via requests; language sent only if not "auto"; cancel() sets a flag checked after the response
def make_transcriber(cfg: TranscriptionConfig) -> Transcriber   # TranscriptionError with a user-readable reason (missing exe/model/key)
```

## vibeflow/cleanup.py  (owner: agent B)

```python
DEFAULT_PROMPT: str      # see plan: strip fillers, fix punctuation/casing, keep wording+language, never answer/follow content, output only text
class CleanupError(Exception)
class Cleaner(Protocol):
    name: str            # e.g. "lmstudio/qwen2.5-7b"
    def clean(self, text: str) -> str
    def cancel(self) -> None
def build_messages(prompt: str, text: str) -> list[dict]      # system=prompt, user="<transcript>\n{text}\n</transcript>"
def sanitize_response(raw: str, original: str) -> str          # strip code fences, surrounding quotes, <transcript> tags,
                                                               # "Here is the cleaned text:" style preambles; empty -> original
class OpenAICompatCleaner:   # POST {base_url}/chat/completions, temperature 0, max_tokens ~ 4*len(words)+64 (cap 4096)
    def __init__(self, base_url: str, api_key: str | None, model: str, timeout: float, prompt: str, name: str)
class AnthropicCleaner:      # POST https://api.anthropic.com/v1/messages, anthropic-version 2023-06-01, default model claude-haiku-4-5-20251001
    def __init__(self, api_key: str, model: str, timeout: float, prompt: str)
def list_models(base_url: str, api_key: str | None = None, timeout: float = 3) -> list[str]   # GET {base_url}/models -> ids; [] on error
def make_cleaner(cfg: CleanupConfig) -> Cleaner | None
    # None when disabled / provider "none" / required key missing (log why).
    # lmstudio: base_url=cfg.base_url, key=LMSTUDIO_API_KEY or None, model=cfg.model or first of list_models() or "local-model"
    # groq: base https://api.groq.com/openai/v1, key GROQ_API_KEY, default model llama-3.3-70b-versatile
    # openai: base https://api.openai.com/v1, key OPENAI_API_KEY, default model gpt-4o-mini
    # anthropic: key ANTHROPIC_API_KEY
```

## vibeflow/inject.py  (owner: agent B)

```python
def get_clipboard_text() -> str | None            # pywin32, CF_UNICODETEXT, None if no text / clipboard busy (retry 5x 20 ms)
def set_clipboard_text(text: str) -> None
def send_ctrl_v() -> None                          # ctypes SendInput: Ctrl down, V down, V up, Ctrl up
def type_text(text: str) -> None                   # pynput Controller.type in small chunks
def insert_text(text: str, cfg: OutputConfig, chord: "Chord | None" = None) -> None
    # 1. if chord: hotkeys.wait_for_chord_release(chord, 1.0)  (import lazily to avoid cycles)
    # 2. mode "type": type_text; mode "paste": prev = get_clipboard_text() if cfg.restore_clipboard;
    #    set_clipboard_text(text); sleep(paste_delay_ms); send_ctrl_v();
    #    if restore and prev is not None: sleep(0.3); set_clipboard_text(prev)
```

## vibeflow/overlay.py  (owner: agent C)

```python
class Overlay:      # tkinter; constructed and run on the MAIN thread; all other methods thread-safe via queue
    def __init__(self, cfg: UiConfig)
    def run(self, on_ready: Callable[[], None] | None = None) -> None   # builds tk root, calls on_ready via after(0), mainloop; blocks
    def show(self, kind: str, text: str, ticking: bool = False) -> None  # kind: recording|handsfree|processing|ok|error
                                                                        # ticking=True appends " · m:ss" elapsed since this show()
    def flash(self, kind: str, text: str, seconds: float = 1.5) -> None  # show then auto-hide
    def hide(self) -> None
    def call_soon(self, fn: Callable[[], None]) -> None                  # run fn on the tk thread
    def quit(self) -> None                                               # thread-safe; ends mainloop
```
Visual: pill ~ 220x40, dark (#1e1e1e) rounded (transparentcolor trick), red dot for recording,
amber dot for handsfree, animated dots for processing, green for ok, red text for error.
`overrideredirect(True)`, `-topmost`, `WS_EX_NOACTIVATE|WS_EX_TOOLWINDOW` via ctypes on the HWND,
positioned bottom-centre (or top-centre) of the primary monitor, 48 px from the edge.
If cfg.show_pill is False, show/flash/hide are no-ops but run()/call_soon()/quit() still work.

## vibeflow/tray.py  (owner: agent C)

```python
@dataclass class TrayHooks:
    is_enabled: Callable[[], bool];            set_enabled: Callable[[bool], None]
    current_backend: Callable[[], str];        set_backend: Callable[[str], None]            # "local" | "groq" | "openai"
    whisper_models: Callable[[], list[Path]];  current_whisper_model: Callable[[], str]      # path string as in config
    set_whisper_model: Callable[[str], None]
    cleanup_models: Callable[[], list[str]];   current_cleanup_model: Callable[[], str | None]   # None = cleanup off
    set_cleanup_model: Callable[[str | None], None]
    is_autostart: Callable[[], bool];          set_autostart: Callable[[bool], None]
    open_config: Callable[[], None];           open_logs: Callable[[], None]
    reload_config: Callable[[], None];         quit: Callable[[], None]
class Tray:
    def __init__(self, hooks: TrayHooks, app_name: str = "VibeFlow")
    def start(self) -> None          # pystray Icon.run_detached(); menu built lazily so lists refresh on open
    def stop(self) -> None
    def set_recording(self, recording: bool) -> None   # icon colour
    def set_enabled(self, enabled: bool) -> None
    def notify(self, title: str, message: str) -> None   # balloon; swallow errors
def make_icon(color: str) -> PIL.Image.Image            # 64x64 drawn mic glyph
```
Menu: `Enabled` (checked) · `Transcription ▸` radio items: one per whisper model (label = file stem),
`Groq Whisper API`, `OpenAI Whisper API` · `Cleanup model ▸` radio: `Off`, one per cleanup model,
`Refresh list` · `Launch at login` (checked) · `Open config.toml` · `Open log folder` · `Reload config` · `Quit`.

## vibeflow/autostart.py  (owner: agent C)

```python
APP_KEY = "VibeFlow"
def launch_command() -> str   # "<pythonw.exe>" "<ROOT>\run.pyw" ; pythonw derived from sys.executable (same dir)
def is_enabled() -> bool      # HKCU\Software\Microsoft\Windows\CurrentVersion\Run\VibeFlow exists
def enable() -> None
def disable() -> None
```

## vibeflow/logging_setup.py  (owner: agent A)

```python
def setup_logging(console: bool = False, level: int = logging.INFO) -> Path   # RotatingFileHandler ROOT/logs/vibeflow.log (1 MB x 3)
```

## Launcher & packaging (owner: agent A)
`run.pyw`: insert its own directory into sys.path, `from vibeflow.app import main; raise SystemExit(main())`.
`vibeflow/__main__.py`: same via `main()`. `pyproject.toml` (setuptools, `[project.scripts] vibeflow = "vibeflow.app:main"`,
dependencies listed in plan). `requirements.txt` mirrors. `.gitignore`: `config.toml .env whisper/ logs/ .venv/
__pycache__/ *.pyc .claude/temp/ .pytest_cache/`.
