"""Settings window (tkinter + sv-ttk). Lives on the overlay's Tk root / Tk thread."""
from __future__ import annotations

import copy
import ctypes
import logging
import os
import sys
import tempfile
import threading
import time
import tkinter as tk
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any, Callable

from . import audio, filetranscribe, parakeet, whisper_setup
from .cleanup import DEFAULT_PROMPT
from .config import ROOT, Config, portable_path, resolve_path
from .hotkeys import parse_chord
from .transcribe import Segment, TranscriptionCancelled, TranscriptionError

try:  # optional: UI still works (with the stock ttk theme) without it
    import sv_ttk
except ImportError:  # pragma: no cover
    sv_ttk = None

log = logging.getLogger(__name__)

TABS = ("general", "microphone", "transcription", "file", "cleanup", "keys", "about")
TAB_TITLES = {"general": "General", "microphone": "Microphone", "transcription": "Transcription", "file": "File",
              "cleanup": "Cleanup", "keys": "API keys", "about": "About"}
MIC_VERDICTS = {
    "silent": "No signal (check Windows Settings > Privacy & security > Microphone)",
    "quiet": "Too quiet: dictation would ignore this",
    "ok": "Good",
    "clipping": "Clipping",
}
DEFAULT_DEVICE_LABEL = "Windows default"
BACKENDS = ("local", "parakeet", "groq", "openai")
PROVIDERS = ("lmstudio", "groq", "openai", "anthropic", "none")
LANGUAGES = ("auto", "en", "hr", "de", "fr", "es", "it", "pt", "nl", "pl", "ru", "uk", "ja", "zh")
OUTPUT_MODES = ("paste", "type")
PILL_POSITIONS = ("top", "bottom")
ENV_KEYS = ("GROQ_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "LMSTUDIO_API_KEY")
SAMPLE_TEXT = "um so i was like thinking we should uh meet on tuesday no wait wednesday"
HOTKEY_HELP = ("Join keys with +, e.g. ctrl+win. Names: ctrl, alt, shift, win, caps_lock, "
               "space, f1..f24, a..z, 0..9. Sides: ctrl_r, lwin, rshift.")


# ---------------------------------------------------------------- pure form mapping

def _num(v: float) -> str:
    return f"{v:g}"


def form_from_config(cfg: Config) -> dict[str, Any]:
    """Flat dict of everything the form edits (numbers as strings, as typed in entries)."""
    t, c = cfg.transcription, cfg.cleanup
    return {
        "hotkey_chord": cfg.hotkey.chord,
        "tap_ms": str(cfg.hotkey.tap_ms),
        "double_tap_ms": str(cfg.hotkey.double_tap_ms),
        "min_recording_seconds": _num(cfg.hotkey.min_recording_seconds),
        "max_recording_seconds": _num(cfg.hotkey.max_recording_seconds),
        "show_pill": bool(cfg.ui.show_pill),
        "pill_position": cfg.ui.pill_position,
        "output_mode": cfg.output.mode,
        "restore_clipboard": bool(cfg.output.restore_clipboard),
        "paste_delay_ms": str(cfg.output.paste_delay_ms),
        "audio_device": cfg.audio.device,
        "silence_threshold": _num(cfg.audio.silence_threshold),
        "backend": t.backend,
        "language": t.language,
        "local_model": _basename(t.local.model),
        "threads": str(t.local.threads),
        "groq_model": t.cloud.groq_model,
        "openai_model": t.cloud.openai_model,
        "cleanup_enabled": bool(c.enabled),
        "cleanup_provider": c.provider,
        "cleanup_base_url": c.base_url,
        "cleanup_model": c.model,
        "cleanup_timeout": _num(c.timeout_seconds),
        "cleanup_prompt": c.prompt or DEFAULT_PROMPT,
    }


def _basename(path: str) -> str:
    return path.replace("\\", "/").rsplit("/", 1)[-1]


def _int(form: dict, key: str, label: str, minimum: int = 0) -> int:
    raw = str(form.get(key, "")).strip()
    try:
        value = int(raw)
    except ValueError:
        raise ValueError(f"{label} must be a whole number (got {raw!r})") from None
    if value < minimum:
        raise ValueError(f"{label} must be at least {minimum}")
    return value


def _float(form: dict, key: str, label: str, minimum: float = 0.0,
           maximum: float | None = None) -> float:
    raw = str(form.get(key, "")).strip()
    try:
        value = float(raw)
    except ValueError:
        raise ValueError(f"{label} must be a number (got {raw!r})") from None
    if value != value or value < minimum:
        raise ValueError(f"{label} must be at least {_num(minimum)}")
    if maximum is not None and value > maximum:
        raise ValueError(f"{label} must be at most {_num(maximum)}")
    return value


def _choice(form: dict, key: str, label: str, options: tuple[str, ...]) -> str:
    value = str(form.get(key, "")).strip()
    if value not in options:
        raise ValueError(f"{label} must be one of: {', '.join(options)}")
    return value


def config_from_form(base: Config, form: dict[str, Any]) -> Config:
    """Apply `form` onto a deep copy of `base`; ValueError (readable text) on invalid input."""
    cfg = copy.deepcopy(base)
    chord = str(form.get("hotkey_chord", "")).strip()
    parse_chord(chord)  # raises ValueError with a readable message
    cfg.hotkey.chord = chord
    cfg.hotkey.tap_ms = _int(form, "tap_ms", "Tap time (ms)", 1)
    cfg.hotkey.double_tap_ms = _int(form, "double_tap_ms", "Double-tap time (ms)", 1)
    lo = _float(form, "min_recording_seconds", "Minimum recording (s)", 0.0)
    hi = _float(form, "max_recording_seconds", "Maximum recording (s)", 0.0)
    if hi <= 0 or lo >= hi:
        raise ValueError("Minimum recording must be smaller than maximum recording")
    cfg.hotkey.min_recording_seconds = lo
    cfg.hotkey.max_recording_seconds = hi
    cfg.ui.show_pill = bool(form.get("show_pill"))
    cfg.ui.pill_position = _choice(form, "pill_position", "Pill position", PILL_POSITIONS)
    cfg.output.mode = _choice(form, "output_mode", "Output mode", OUTPUT_MODES)
    cfg.output.restore_clipboard = bool(form.get("restore_clipboard"))
    cfg.output.paste_delay_ms = _int(form, "paste_delay_ms", "Paste delay (ms)", 0)
    cfg.audio.device = str(form.get("audio_device", "")).strip()
    cfg.audio.silence_threshold = _float(form, "silence_threshold", "Silence threshold", 0.0, 1.0)

    t = cfg.transcription
    t.backend = _choice(form, "backend", "Backend", BACKENDS)
    t.language = str(form.get("language", "")).strip() or "auto"
    model = str(form.get("local_model", "")).strip()
    if model and model != _basename(base.transcription.local.model):
        old = base.transcription.local.model
        cut = max(old.rfind("/"), old.rfind("\\"))
        t.local.model = (old[: cut + 1] + model) if cut >= 0 else model
    elif not model and t.backend == "local":
        raise ValueError("Pick a local whisper model (or download one first)")
    t.local.threads = _int(form, "threads", "Threads", 0)
    t.cloud.groq_model = str(form.get("groq_model", "")).strip() or t.cloud.groq_model
    t.cloud.openai_model = str(form.get("openai_model", "")).strip() or t.cloud.openai_model

    c = cfg.cleanup
    c.enabled = bool(form.get("cleanup_enabled"))
    c.provider = _choice(form, "cleanup_provider", "Cleanup provider", PROVIDERS)
    c.base_url = str(form.get("cleanup_base_url", "")).strip()
    c.model = str(form.get("cleanup_model", "")).strip()
    c.timeout_seconds = _float(form, "cleanup_timeout", "Cleanup timeout (s)", 1.0)
    prompt = str(form.get("cleanup_prompt", "")).strip()
    c.prompt = "" if prompt in ("", DEFAULT_PROMPT.strip()) else prompt
    return cfg


# ---------------------------------------------------------------- microphone helpers

def device_choices(names, default_name: str | None, saved: str) -> list[tuple[str, str]]:
    """(label, value) pairs for the device combo; value "" is the Windows default."""
    names = list(names)
    first = f"{DEFAULT_DEVICE_LABEL} ({default_name})" if default_name else DEFAULT_DEVICE_LABEL
    out = [(first, "")] + [(n, n) for n in names]
    if saved and audio.match_device(saved, names) is None:
        out.append((f"{saved} (not connected)", saved))
    return out


def device_label_for(saved: str, choices: list[tuple[str, str]], names) -> str:
    """Label shown for the saved value; a legacy substring shows the device it matches."""
    for label, value in choices:
        if value == saved:
            return label
    hit = audio.match_device(saved, list(names))
    return hit if hit is not None else choices[0][0]


def engine_text(cfg: Config) -> str:
    t = cfg.transcription
    if t.backend == "local":
        return f"Local (whisper.cpp), {_basename(t.local.model)}"
    if t.backend == "parakeet":
        return "Local (Parakeet v3)"
    if t.backend == "groq":
        return f"Groq API, {t.cloud.groq_model}"
    if t.backend == "openai":
        return f"OpenAI API, {t.cloud.openai_model}"
    return t.backend


# ---------------------------------------------------------------- hooks

@dataclass
class SettingsHooks:
    get_config: Callable[[], Config]
    apply: Callable[[Config, dict[str, str]], str | None]
    cleanup_models: Callable[[bool], list[str]]
    test_cleanup: Callable[[Config, str], str]
    is_autostart: Callable[[], bool]
    set_autostart: Callable[[bool], None]
    set_show_on_start: Callable[[bool], None]
    whisper_dir: Callable[[], Path]
    models_dir: Callable[[], Path]
    active_model_path: Callable[[], Path]
    open_logs: Callable[[], None]
    open_config: Callable[[], None]
    audio_devices: Callable[[], list[str]]
    file_transcriber: Callable[[], Any]
    version: str = ""
    default_audio_device: Callable[[], str | None] = lambda: None
    refresh_audio_devices: Callable[[], bool] = lambda: False
    test_transcribe: Callable[[Path, float, threading.Event], str] | None = None


# ---------------------------------------------------------------- win32 / theme helpers

def _windows_dark() -> bool:
    if sys.platform != "win32":
        return False
    try:
        import winreg
        with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as k:
            return winreg.QueryValueEx(k, "AppsUseLightTheme")[0] == 0
    except Exception:
        return False


def _fmt_clock(seconds: float) -> str:
    s = int(round(seconds))
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


def _fmt_elapsed(seconds: float) -> str:
    s = int(round(seconds))
    return f"{s // 60}m {s % 60:02d}s" if s >= 60 else f"{s}s"


def _fmt_size(n: int) -> str:
    if n >= 1_000_000_000:
        return f"{n / 1e9:.1f} GB"
    return f"{n / 1e6:.0f} MB"


GWL_EXSTYLE = -20
WS_EX_APPWINDOW = 0x00040000
WS_EX_TOOLWINDOW = 0x00000080
SWP_FLAGS = 0x1 | 0x2 | 0x4 | 0x10 | 0x20  # nosize nomove nozorder noactivate framechanged


class SettingsWindow:
    def __init__(self, root: tk.Misc, hooks: SettingsHooks,
                 post: Callable[[Callable[[], None]], None]):
        self.root = root
        self.hooks = hooks
        self._post = post
        self.top: tk.Toplevel | None = None
        self._vars: dict[str, tk.Variable] = {}
        self._env_vars: dict[str, tk.StringVar] = {}
        self._snapshot: dict[str, Any] = {}
        self._env_snapshot: dict[str, str] = {}
        self._prompt: tk.Text | None = None
        self._icon: Any = None
        self._dark = False
        self._busy = False
        self._cancel = threading.Event()
        self._closed = False
        # File tab state: deliberately outside self._vars so it never feeds _collect/_is_dirty/Save.
        self._file_busy = False
        self._file_cancel = threading.Event()
        self._file_segments: list[Segment] = []
        self._file_src: Path | None = None
        self._file_srcs: list[Path] = []
        self._file_meta: dict | None = None  # tags + length of the single-file result, for LRC
        self._file_lang_init = False
        # Microphone tab state: outside self._vars (the form never sees it).
        self._mic_monitor: audio.InputMonitor | None = None
        self._mic_busy = False
        self._mic_cancel = threading.Event()
        self._mic_poll_id: str | None = None
        self._mic_quiet_since: float | None = None
        self._mic_phase = ""
        self._mic_rec_until = 0.0

    # -- public -----------------------------------------------------------
    def is_visible(self) -> bool:
        top = self.top
        try:
            return bool(top is not None and top.winfo_exists() and top.state() == "normal")
        except tk.TclError:
            return False

    def show(self, tab: str | None = None, banner: str | None = None) -> None:
        if self.top is None:
            self._build()
        top = self.top
        assert top is not None
        self._populate()
        self._set_banner(banner)
        self._fit()
        if tab in TABS:
            self._nb.select(TABS.index(tab))
        top.deiconify()
        if top.state() == "iconic":
            top.state("normal")
        top.lift()
        try:
            top.attributes("-topmost", True)
            top.after(300, lambda: self._untopmost())
            top.focus_force()
        except tk.TclError:
            pass

    def _fit(self) -> None:
        """Grow the window to its required size (banner included); shrink the model list
        rather than clip when the screen is too small."""
        top = self.top
        if top is None:
            return
        try:
            top.update_idletasks()
            cap = max(400, top.winfo_screenheight() - 90)
            rows = 6
            self._tree.configure(height=rows)
            while top.winfo_reqheight() > cap and rows > 3:
                rows -= 1
                self._tree.configure(height=rows)
                top.update_idletasks()
            req_h = min(top.winfo_reqheight(), cap)
            req_w = top.winfo_reqwidth()
            top.minsize(req_w, req_h)
            top.geometry(f"{max(top.winfo_width(), req_w)}x{max(top.winfo_height(), req_h)}")
        except tk.TclError:
            pass

    def hide(self) -> None:
        if self.top is not None:
            self._mic_stop_meter()
            try:
                self.top.withdraw()
            except tk.TclError:
                pass

    def close_requested(self) -> None:
        """X button / Close: prompt when dirty, then hide to tray."""
        if self.top is None:
            return
        if self._is_dirty():
            answer = messagebox.askyesnocancel(
                "VibeFlow", "Save changes?", parent=self.top)
            if answer is None:
                return
            if answer and not self.save():
                return
        self.hide()

    # -- building -----------------------------------------------------------
    def _untopmost(self) -> None:
        try:
            if self.top is not None:
                self.top.attributes("-topmost", False)
        except tk.TclError:
            pass

    def _build(self) -> None:
        self._dark = _windows_dark()
        if sv_ttk is not None:
            try:
                sv_ttk.set_theme("dark" if self._dark else "light")
                # <<ThemeChanged>> is not delivered while the root is withdrawn, so the theme's
                # palette (frame/label backgrounds) would stay unset; apply it explicitly.
                self.root.tk.eval("configure_colors")
            except Exception:
                log.exception("could not apply sv-ttk theme")
        top = tk.Toplevel(self.root)
        self.top = top
        top.withdraw()
        top.title("VibeFlow Settings")
        scale = 1.0
        try:
            scale = max(1.0, float(top.winfo_fpixels("1i")) / 96.0)
        except tk.TclError:
            pass
        top.geometry(f"{int(700 * scale)}x{min(int(780 * scale), top.winfo_screenheight() - 90)}")
        top.protocol("WM_DELETE_WINDOW", self.close_requested)
        self._set_icon()

        outer = ttk.Frame(top, padding=10)
        outer.pack(fill="both", expand=True)

        self._banner = tk.Label(outer, text="", bg="#fff3bf", fg="#5c4400", anchor="w",
                                justify="left", padx=10, pady=6, wraplength=int(620 * scale))

        bottom = ttk.Frame(outer)
        bottom.pack(side="bottom", fill="x", pady=(10, 0))
        self._show_start = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            bottom, text="Show this window when VibeFlow starts", variable=self._show_start,
            command=self._on_show_start).pack(side="left")
        ttk.Button(bottom, text="Close", command=self.close_requested).pack(side="right")
        ttk.Button(bottom, text="Save", style="Accent.TButton" if sv_ttk else "TButton",
                   command=self.save).pack(side="right", padx=(0, 8))
        self._status = ttk.Label(outer, text="")
        self._status.pack(side="bottom", fill="x", pady=(6, 0))

        self._nb = ttk.Notebook(outer)
        self._nb.pack(side="top", fill="both", expand=True)
        frames = {}
        for name in TABS:
            f = ttk.Frame(self._nb, padding=12)
            self._nb.add(f, text=TAB_TITLES[name])
            frames[name] = f
        self._build_general(frames["general"])
        self._build_microphone(frames["microphone"])
        self._build_transcription(frames["transcription"])
        self._build_file(frames["file"])
        self._build_cleanup(frames["cleanup"])
        self._build_keys(frames["keys"])
        self._build_about(frames["about"])
        self._nb.bind("<<NotebookTabChanged>>", self._on_tab_changed)
        top.update_idletasks()
        self._win32_polish()

    def _set_icon(self) -> None:
        try:
            from PIL import ImageTk
            from .tray import make_icon
            img = make_icon("#e8e8e8" if self._dark else "#2b2b2b").resize((32, 32))
            self._icon = ImageTk.PhotoImage(img, master=self.top)
            self.top.iconphoto(False, self._icon)  # type: ignore[union-attr]
        except Exception:
            log.debug("window icon failed", exc_info=True)

    def _win32_polish(self) -> None:
        if sys.platform != "win32" or self.top is None:
            return
        try:
            user32 = ctypes.windll.user32
            user32.GetWindowLongW.restype = ctypes.c_long
            hwnd = user32.GetParent(self.top.winfo_id()) or self.top.winfo_id()
            style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            style = (style | WS_EX_APPWINDOW) & ~WS_EX_TOOLWINDOW
            user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style)
            user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, SWP_FLAGS)
            if self._dark:
                value = ctypes.c_int(1)
                ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    hwnd, 20, ctypes.byref(value), ctypes.sizeof(value))
        except Exception:
            log.debug("win32 window polish failed", exc_info=True)

    # -- widget helpers ---------------------------------------------------------
    def _var(self, key: str, value: Any) -> tk.Variable:
        var: tk.Variable = tk.BooleanVar(value=value) if isinstance(value, bool) \
            else tk.StringVar(value=value)
        self._vars[key] = var
        return var

    def _entry(self, parent, row: int, label: str, key: str, width: int = 12,
               col: int = 0, hint: str | None = None) -> ttk.Entry:
        ttk.Label(parent, text=label).grid(row=row, column=col, sticky="w", pady=3, padx=(0, 10))
        e = ttk.Entry(parent, textvariable=self._var(key, ""), width=width)
        e.grid(row=row, column=col + 1, sticky="w", pady=3)
        if hint:
            ttk.Label(parent, text=hint, foreground="gray").grid(
                row=row, column=col + 2, sticky="w", padx=8)
        return e

    def _combo(self, parent, row: int, label: str, key: str, values, width: int = 24,
               readonly: bool = False, col: int = 0) -> ttk.Combobox:
        ttk.Label(parent, text=label).grid(row=row, column=col, sticky="w", pady=3, padx=(0, 10))
        cb = ttk.Combobox(parent, textvariable=self._var(key, ""), values=list(values),
                          width=width, state="readonly" if readonly else "normal")
        cb.grid(row=row, column=col + 1, sticky="w", pady=3)
        return cb

    def _check(self, parent, row: int, label: str, key: str, span: int = 3) -> ttk.Checkbutton:
        cb = ttk.Checkbutton(parent, text=label, variable=self._var(key, False))
        cb.grid(row=row, column=0, columnspan=span, sticky="w", pady=3)
        return cb

    # -- General -----------------------------------------------------------------
    def _build_general(self, f: ttk.Frame) -> None:
        hk = ttk.LabelFrame(f, text="Hotkey", padding=10)
        hk.pack(fill="x")
        self._chord_entry = self._entry(hk, 0, "Hotkey chord", "hotkey_chord", width=24)
        self._chord_msg = ttk.Label(hk, text="", foreground="#d33")
        self._chord_msg.grid(row=0, column=2, sticky="w", padx=8)
        ttk.Label(hk, text=HOTKEY_HELP, foreground="gray", wraplength=600,
                  justify="left").grid(row=1, column=0, columnspan=3, sticky="w", pady=(0, 6))
        self._entry(hk, 2, "Tap time (ms)", "tap_ms", 8, hint="shorter than this = tap")
        self._entry(hk, 3, "Double-tap time (ms)", "double_tap_ms", 8,
                    hint="second tap within this = hands-free")
        self._entry(hk, 4, "Min recording (s)", "min_recording_seconds", 8)
        self._entry(hk, 5, "Max recording (s)", "max_recording_seconds", 8)
        self._vars["hotkey_chord"].trace_add("write", lambda *_: self._check_chord())
        self._autostart = tk.BooleanVar(value=False)
        ttk.Checkbutton(hk, text="Launch at login", variable=self._autostart,
                        command=self._on_autostart).grid(
            row=6, column=0, columnspan=3, sticky="w", pady=(8, 0))

        out = ttk.LabelFrame(f, text="Output and pill", padding=10)
        out.pack(fill="x", pady=(10, 0))
        self._combo(out, 0, "Output mode", "output_mode", OUTPUT_MODES, 10, readonly=True)
        self._entry(out, 1, "Paste delay (ms)", "paste_delay_ms", 8)
        self._check(out, 2, "Restore clipboard after pasting", "restore_clipboard")
        self._check(out, 3, "Show status pill while dictating", "show_pill")
        self._combo(out, 4, "Pill position", "pill_position", PILL_POSITIONS, 10, readonly=True)

    def _on_tab_changed(self, _event=None) -> None:
        try:
            current = self._nb.index(self._nb.select())
        except tk.TclError:
            return
        if current != TABS.index("microphone"):
            self._mic_stop_meter()

    def _check_chord(self) -> None:
        try:
            parse_chord(str(self._vars["hotkey_chord"].get()))
            self._chord_msg.configure(text="")
        except ValueError as e:
            self._chord_msg.configure(text=str(e).split(";")[0][:60])

    # -- Transcription -------------------------------------------------------------
    def _build_transcription(self, f: ttk.Frame) -> None:
        be = ttk.LabelFrame(f, text="Backend", padding=10)
        be.pack(fill="x")
        be_var = self._var("backend", "local")
        for i, (val, text) in enumerate((("local", "Local (whisper.cpp)"),
                                         ("parakeet", "Local (Parakeet v3)"),
                                         ("groq", "Groq API"), ("openai", "OpenAI API"))):
            ttk.Radiobutton(be, text=text, value=val, variable=be_var).grid(
                row=0, column=i, sticky="w", padx=(0, 16))
        self._parakeet_hint = ttk.Label(
            be, text="Parakeet detects the language automatically (25 European languages, "
            "incl. Croatian).", foreground="gray")
        self._parakeet_hint.grid(row=1, column=0, columnspan=4, sticky="w", pady=(4, 0))
        opts = ttk.Frame(f)
        self._combo(opts, 0, "Language", "language", LANGUAGES, 10)
        self._model_combo = self._combo(opts, 1, "Local model", "local_model", [], 30,
                                        readonly=True)
        self._entry(opts, 2, "Threads", "threads", 6, hint="0 = automatic")
        self._entry(opts, 3, "Groq model", "groq_model", 28)
        self._entry(opts, 4, "OpenAI model", "openai_model", 28)
        # option rows (label + field + hint) shown per backend
        self._backend_rows = {
            row: (opts.grid_slaves(row=row), backends) for row, backends in (
                (0, ("local", "groq", "openai")), (1, ("local",)), (2, ("local",)),
                (3, ("groq",)), (4, ("openai",)))
        }

        mm = ttk.LabelFrame(f, text="Model manager", padding=8)
        cols = ("name", "size", "status")
        self._tree = ttk.Treeview(mm, columns=cols, show="headings", height=6, selectmode="browse")
        for c, w, t in (("name", 150, "Model"), ("size", 80, "Size"), ("status", 160, "Status")):
            self._tree.heading(c, text=t)
            self._tree.column(c, width=w, anchor="w")
        sb = ttk.Scrollbar(mm, orient="vertical", command=self._tree.yview)
        self._tree.configure(yscrollcommand=sb.set)
        self._tree.grid(row=0, column=0, sticky="nsew")
        sb.grid(row=0, column=1, sticky="ns")
        mm.columnconfigure(0, weight=1)
        mm.rowconfigure(0, weight=1)
        for name, size in whisper_setup.MODELS.items():
            label = f"{name} (Croatian only)" if name in whisper_setup.CROATIAN_ONLY else name
            self._tree.insert("", "end", iid=name, values=(label, _fmt_size(size), ""))
        self._tree.insert("", "end", iid=parakeet.MODEL_NAME, values=(
            parakeet.MODEL_NAME, _fmt_size(parakeet.TOTAL_BYTES), ""))
        btns = ttk.Frame(mm)
        btns.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        self._dl_btn = ttk.Button(btns, text="Download", command=self._download_selected)
        self._dl_btn.pack(side="left")
        self._del_btn = ttk.Button(btns, text="Delete", command=self._delete_selected)
        self._del_btn.pack(side="left", padx=6)
        self._progress = ttk.Progressbar(btns, mode="determinate", length=160, maximum=100)
        self._progress.pack(side="left", padx=(12, 6))
        self._cancel_btn = ttk.Button(btns, text="Cancel", command=self._cancel.set,
                                      state="disabled")
        self._cancel_btn.pack(side="left")
        self._progress_label = ttk.Label(mm, text="")
        self._progress_label.grid(row=2, column=0, columnspan=2, sticky="w", pady=(4, 0))

        en = ttk.LabelFrame(f, text="Whisper engine", padding=8)
        self._engine_label = ttk.Label(en, text="")
        self._engine_label.pack(side="left")
        self._cuda_btn = ttk.Button(en, text="Install CUDA (NVIDIA)",
                                    command=lambda: self._install_engine(True))
        self._cuda_btn.pack(side="right")
        self._cpu_btn = ttk.Button(en, text="Install CPU",
                                   command=lambda: self._install_engine(False))
        self._cpu_btn.pack(side="right", padx=6)

        # sections in pack order with their pack options and the backends that show them
        self._backend_sections = (
            (opts, {"fill": "x", "pady": (8, 0)}, ("local", "groq", "openai")),
            (mm, {"fill": "both", "expand": True, "pady": (8, 0)}, ("local", "parakeet")),
            (en, {"fill": "x", "pady": (8, 0)}, ("local",)),
        )
        be_var.trace_add("write", lambda *_: self._show_backend_options())
        self._show_backend_options()

    def _show_backend_options(self) -> None:
        """Show only the options, model rows and sections that belong to the selected backend."""
        backend = str(self._vars["backend"].get())
        for widgets, backends in self._backend_rows.values():
            for w in widgets:
                if backend in backends:
                    w.grid()
                else:
                    w.grid_remove()
        if backend == "parakeet":
            self._parakeet_hint.grid()
        else:
            self._parakeet_hint.grid_remove()
        for frame, _, _ in self._backend_sections:
            frame.pack_forget()
        for frame, pack_opts, backends in self._backend_sections:
            if backend in backends:
                frame.pack(**pack_opts)
        wanted = [parakeet.MODEL_NAME] if backend == "parakeet" else list(whisper_setup.MODELS)
        self._tree.selection_set(())
        for iid in (*whisper_setup.MODELS, parakeet.MODEL_NAME):
            self._tree.detach(iid)
        for i, iid in enumerate(wanted):
            self._tree.move(iid, "", i)

    def _refresh_models(self) -> None:
        mdir = self.hooks.models_dir()
        try:
            active = self.hooks.active_model_path().name
        except Exception:
            active = ""
        installed = set()
        try:
            installed = {p.name for p in mdir.glob("ggml-*.bin")}
        except OSError:
            pass
        for name in whisper_setup.MODELS:
            fn = whisper_setup.model_filename(name)
            if fn in installed:
                status = "Installed (active)" if fn == active else "Installed"
            else:
                status = "Not installed"
            self._tree.set(name, "status", status)
        files = sorted(installed)
        current = str(self._vars["local_model"].get())
        if current and current not in files:
            files.append(current)
        self._model_combo.configure(values=files)
        pk = parakeet.installed(self._parakeet_dir())
        self._tree.set(parakeet.MODEL_NAME, "status", "Installed" if pk else "Not installed")
        self._refresh_engine()

    def _parakeet_dir(self) -> Path:
        return resolve_path(self.hooks.get_config().transcription.parakeet.model_dir)

    def _refresh_engine(self) -> None:
        try:
            ok = whisper_setup.engine_installed(self.hooks.whisper_dir())
        except Exception:
            ok = False
        self._engine_label.configure(
            text="whisper-cli: installed" if ok else "whisper-cli: not installed")

    def _set_busy(self, busy: bool, text: str = "") -> None:
        self._busy = busy
        state = "disabled" if busy else "normal"
        for b in (self._dl_btn, self._del_btn, self._cpu_btn, self._cuda_btn):
            b.configure(state=state)
        self._cancel_btn.configure(state="normal" if busy else "disabled")
        self._progress_label.configure(text=text)
        if not busy:
            self._progress.configure(value=0)
        self._cancel.clear()

    def _selected_model(self) -> str | None:
        sel = self._tree.selection()
        return sel[0] if sel else None

    def _progress_cb(self, label: str) -> Callable[[int, int | None], None]:
        def cb(done: int, total: int | None) -> None:
            def ui() -> None:
                try:
                    if total:
                        self._progress.configure(mode="determinate", value=100.0 * done / total)
                        self._progress_label.configure(
                            text=f"{label}: {done / 1e6:.0f} / {total / 1e6:.0f} MB")
                    else:
                        self._progress_label.configure(text=f"{label}: {done / 1e6:.0f} MB")
                except tk.TclError:
                    pass
            self._post(ui)
        return cb

    def _run_worker(self, label: str, work: Callable[[], Any],
                    done: Callable[[Any, Exception | None], None]) -> None:
        self._set_busy(True, f"{label}...")

        def run() -> None:
            result: Any = None
            error: Exception | None = None
            try:
                result = work()
            except Exception as e:  # noqa: BLE001
                error = e

            def finish() -> None:
                try:
                    self._set_busy(False)
                    done(result, error)
                except tk.TclError:
                    pass
                except Exception:
                    log.exception("settings worker completion failed")
            self._post(finish)

        threading.Thread(target=run, name="vibeflow-settings-worker", daemon=True).start()

    def _download_selected(self) -> None:
        name = self._selected_model()
        if self._busy or not name:
            if not name:
                self._set_status("Select a model in the list first.")
            return
        is_pk = name == parakeet.MODEL_NAME
        if is_pk:
            have = parakeet.installed(self._parakeet_dir())
        else:
            have = (self.hooks.models_dir() / whisper_setup.model_filename(name)).exists()
        if have:
            self._set_status(f"{name} is already installed.")
            return
        cb = self._progress_cb(f"Downloading {name}")
        cancel = self._cancel

        def work() -> Path:
            if is_pk:
                return parakeet.download(self._parakeet_dir(), cb, cancel)
            return whisper_setup.download_model(name, self.hooks.models_dir(), cb, cancel)

        def done(result: Any, err: Exception | None) -> None:
            self._refresh_models()
            if isinstance(err, whisper_setup.DownloadCancelled):
                self._set_status("Download cancelled.")
            elif err is not None:
                messagebox.showerror("Download failed", str(err), parent=self.top)
            else:
                self._set_status(f"Downloaded {name}.")
                self._auto_select(name)
        self._run_worker(f"Downloading {name}", work, done)

    def _auto_select(self, name: str) -> None:
        """If the saved backend has no usable model, switch to the freshly downloaded one and
        save. The other local backend counts as unusable when its model is not installed."""
        is_pk = name == parakeet.MODEL_NAME
        try:
            base = self.hooks.get_config()
            t = base.transcription
            whisper_ok = self.hooks.active_model_path().exists()
            parakeet_ok = parakeet.installed(resolve_path(t.parakeet.model_dir))
            if is_pk:
                # backend already parakeet: re-apply so the transcriber picks up the model
                if not (t.backend == "parakeet" or (t.backend == "local" and not whisper_ok)):
                    return
                t.backend = "parakeet"
            else:
                if whisper_ok and not (t.backend == "parakeet" and not parakeet_ok):
                    return
                if not whisper_ok:
                    fn = whisper_setup.model_filename(name)
                    t.local.model = portable_path(self.hooks.models_dir() / fn)
                if t.backend == "parakeet" and not parakeet_ok:
                    t.backend = "local"
            err = self.hooks.apply(base, {})
        except Exception:
            log.exception("auto-select of downloaded model failed")
            return
        if not is_pk:
            fn = _basename(t.local.model)
            self._vars["local_model"].set(fn)
            self._snapshot["local_model"] = fn
        self._vars["backend"].set(t.backend)
        self._snapshot["backend"] = t.backend
        self._refresh_models()
        self._set_banner(err)
        self._set_status(f"{name} selected and applied." if not err else f"Applied: {err}")

    def _delete_selected(self) -> None:
        name = self._selected_model()
        if self._busy or not name:
            return
        if name == parakeet.MODEL_NAME:
            self._delete_parakeet()
            return
        fn = whisper_setup.model_filename(name)
        path = self.hooks.models_dir() / fn
        if not path.exists():
            self._set_status(f"{name} is not installed.")
            return
        if self.hooks.active_model_path().name == fn:
            messagebox.showinfo("VibeFlow", "This is the active model. Select another model "
                                "first.", parent=self.top)
            return
        if not messagebox.askyesno("Delete model", f"Delete {fn}?", parent=self.top):
            return
        try:
            path.unlink()
        except OSError as e:
            messagebox.showerror("Delete failed", str(e), parent=self.top)
        self._refresh_models()

    def _delete_parakeet(self) -> None:
        path = self._parakeet_dir()
        if not path.exists():
            self._set_status(f"{parakeet.MODEL_NAME} is not installed.")
            return
        if (self.hooks.get_config().transcription.backend == "parakeet"
                or str(self._vars["backend"].get()) == "parakeet"):
            messagebox.showinfo("VibeFlow", "Parakeet is the active backend. Switch to another "
                                "backend first.", parent=self.top)
            return
        if not messagebox.askyesno("Delete model", f"Delete {parakeet.MODEL_NAME}?",
                                   parent=self.top):
            return
        try:
            parakeet.delete(path)
        except OSError as e:
            messagebox.showerror("Delete failed", str(e), parent=self.top)
        self._refresh_models()

    def _install_engine(self, cuda: bool) -> None:
        if self._busy:
            return
        label = "Installing CUDA engine" if cuda else "Installing CPU engine"
        cb = self._progress_cb(label)
        cancel = self._cancel

        def work() -> str:
            return whisper_setup.install_engine(self.hooks.whisper_dir(), cuda, cb, cancel)

        def done(result: Any, err: Exception | None) -> None:
            self._refresh_engine()
            if isinstance(err, whisper_setup.DownloadCancelled):
                self._set_status("Engine install cancelled.")
            elif err is not None:
                messagebox.showerror("Engine install failed", str(err), parent=self.top)
            else:
                self._set_status(f"Whisper engine installed ({result}).")
                try:
                    self.hooks.apply(self.hooks.get_config(), {})  # rebuild transcriber
                except Exception:
                    log.exception("apply after engine install failed")
                self._refresh_models()
        self._run_worker(label, work, done)

    # -- File ---------------------------------------------------------------------
    def _build_file(self, f: ttk.Frame) -> None:
        self._file_path = tk.StringVar(value="")
        self._file_format = tk.StringVar(value=filetranscribe.FORMAT_LABELS["plain"])
        self._file_lang = tk.StringVar(value="auto")
        self._file_status_var = tk.StringVar(value="")
        self._file_beside = tk.BooleanVar(value=False)

        top = ttk.Frame(f)
        top.pack(fill="x")
        top.columnconfigure(1, weight=1)
        ttk.Label(top, text="File").grid(row=0, column=0, sticky="w", pady=3, padx=(0, 10))
        ttk.Entry(top, textvariable=self._file_path, state="readonly").grid(
            row=0, column=1, sticky="ew", pady=3)
        self._file_browse_btn = ttk.Button(top, text="Browse...", command=self._file_browse)
        self._file_browse_btn.grid(row=0, column=2, padx=(8, 0))
        ttk.Label(top, text="Format").grid(row=1, column=0, sticky="w", pady=3, padx=(0, 10))
        self._file_format_combo = ttk.Combobox(
            top, textvariable=self._file_format, state="readonly", width=24,
            values=[filetranscribe.FORMAT_LABELS[k] for k in filetranscribe.FORMATS])
        self._file_format_combo.grid(row=1, column=1, sticky="w", pady=3)
        self._file_format_combo.bind("<<ComboboxSelected>>", self._file_format_changed)
        ttk.Label(top, text="Language").grid(row=2, column=0, sticky="w", pady=3, padx=(0, 10))
        self._file_lang_combo = ttk.Combobox(top, textvariable=self._file_lang, values=list(LANGUAGES),
                                             state="normal", width=10)
        self._file_lang_combo.grid(row=2, column=1, sticky="w", pady=3)
        ttk.Checkbutton(top, text="Save next to each source file", variable=self._file_beside).grid(
            row=3, column=1, sticky="w", pady=3)
        self._file_engine = ttk.Label(f, text="", foreground="gray", wraplength=620, justify="left")
        self._file_engine.pack(anchor="w", pady=(6, 0))
        self._file_hint = ttk.Label(
            f, foreground="gray", wraplength=620, justify="left",
            text="Croatian songs: large-v3-turbo-hr. Mixed languages: Parakeet v3. "
                 "Check the draft before importing into Spodrive.")

        run = ttk.Frame(f)
        run.pack(fill="x", pady=(8, 0))
        self._file_run_btn = ttk.Button(
            run, text="Transcribe", style="Accent.TButton" if sv_ttk else "TButton",
            command=self._file_start)
        self._file_run_btn.pack(side="left")
        self._file_cancel_btn = ttk.Button(run, text="Cancel", command=self._file_cancel.set,
                                           state="disabled")
        self._file_cancel_btn.pack(side="left", padx=6)
        self._file_bar = ttk.Progressbar(run, mode="determinate", length=200, maximum=100)
        self._file_bar.pack(side="left", padx=(8, 0))
        ttk.Label(f, textvariable=self._file_status_var, wraplength=620, justify="left").pack(
            anchor="w", pady=(4, 0))

        rf = ttk.LabelFrame(f, text="Result", padding=8)
        rf.pack(fill="both", expand=True, pady=(8, 0))
        self._file_text = tk.Text(rf, height=10, wrap="word", font=("Segoe UI", 9))
        sb = ttk.Scrollbar(rf, orient="vertical", command=self._file_text.yview)
        self._file_text.configure(yscrollcommand=sb.set)
        self._file_text.grid(row=0, column=0, sticky="nsew")
        sb.grid(row=0, column=1, sticky="ns")
        rf.columnconfigure(0, weight=1)
        rf.rowconfigure(0, weight=1)
        btns = ttk.Frame(rf)
        btns.grid(row=1, column=0, columnspan=2, sticky="e", pady=(6, 0))
        self._file_copy_btn = ttk.Button(btns, text="Copy", command=self._file_copy, state="disabled")
        self._file_copy_btn.pack(side="left")
        self._file_save_btn = ttk.Button(btns, text="Save as...", command=self._file_save,
                                         state="disabled")
        self._file_save_btn.pack(side="left", padx=(6, 0))

    def _file_fmt(self) -> str:
        label = self._file_format.get()
        for key, text in filetranscribe.FORMAT_LABELS.items():
            if text == label:
                return key
        return "plain"

    def _file_set_status(self, text: str) -> None:
        try:
            self._file_status_var.set(text)
        except tk.TclError:
            pass

    def _file_browse(self) -> None:
        if self._file_busy:
            return
        start = self._file_src.parent if self._file_src else None
        paths = filedialog.askopenfilenames(
            parent=self.top, title="Choose audio or video files",
            initialdir=str(start) if start else None,
            filetypes=[("Audio and video", filetranscribe.MEDIA_PATTERNS), ("All files", "*.*")])
        if paths:
            self._file_srcs = [Path(p) for p in paths]
            self._file_src = self._file_srcs[0]
            self._file_path.set(str(self._file_src) if len(paths) == 1
                                else f"{len(paths)} files: {self._file_src.name}, ...")

    def _file_format_changed(self, _event=None) -> None:
        # LRC is meant for batches of songs: show the model hint and default to saving beside the sources
        lrc = self._file_fmt() == "lrc"
        if lrc:
            self._file_hint.pack(anchor="w", pady=(2, 0), after=self._file_engine)
        else:
            self._file_hint.pack_forget()
        self._file_beside.set(lrc)
        self._file_render()

    def _file_sources(self) -> list[Path]:
        if len(self._file_srcs) > 1:
            return list(self._file_srcs)
        return [self._file_src] if self._file_src else []

    def _file_render(self) -> None:
        text = (filetranscribe.render(self._file_segments, self._file_fmt(), self._file_meta)
                if self._file_segments else "")
        self._file_text.delete("1.0", "end")
        self._file_text.insert("1.0", text)
        state = "normal" if self._file_segments else "disabled"
        self._file_copy_btn.configure(state=state)
        self._file_save_btn.configure(state=state)

    def _file_copy(self) -> None:
        text = self._file_text.get("1.0", "end-1c")
        if not text:
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.root.update()  # keep the clipboard contents after the window closes
        self._file_set_status("Copied to the clipboard.")

    def _file_save(self) -> None:
        if not self._file_segments:
            return
        fmt = self._file_fmt()
        ext = filetranscribe.FORMAT_EXT[fmt]
        stem = self._file_src.stem if self._file_src else "transcript"
        path = filedialog.asksaveasfilename(
            parent=self.top, title="Save transcript", defaultextension=ext,
            initialdir=str(self._file_src.parent) if self._file_src else None,
            initialfile=f"{stem}{ext}",
            filetypes=[(filetranscribe.FORMAT_LABELS[fmt], f"*{ext}"), ("All files", "*.*")])
        if not path:
            return
        try:
            Path(path).write_text(filetranscribe.render(self._file_segments, fmt, self._file_meta),
                                  encoding="utf-8", newline="\n")
        except OSError as e:
            messagebox.showerror("Save failed", str(e), parent=self.top)
            return
        self._file_set_status(f"Saved {path}")

    def _file_set_busy(self, busy: bool) -> None:
        self._file_busy = busy
        self._file_run_btn.configure(state="disabled" if busy else "normal")
        self._file_browse_btn.configure(state="disabled" if busy else "normal")
        self._file_cancel_btn.configure(state="normal" if busy else "disabled")
        if not busy:
            self._file_bar.configure(value=0)

    def _file_start(self) -> None:
        if self._file_busy:
            return
        srcs = self._file_sources()
        if not srcs or not all(p.is_file() for p in srcs):
            self._file_set_status("Choose an audio or video file first.")
            return
        fmt = self._file_fmt()
        batch = len(srcs) > 1 or self._file_beside.get()  # batch: write <stem><ext> beside each source
        language = self._file_lang.get().strip() or "auto"
        self._file_cancel.clear()
        cancel = self._file_cancel
        self._file_set_busy(True)
        self._file_set_status("Starting...")
        started = time.monotonic()
        last = {"stage": "", "pct": -1, "k": 0}
        current = {"k": 1}

        def progress(stage: str, frac: float | None) -> None:
            pct = -1 if frac is None else int(max(0.0, min(frac, 1.0)) * 100)
            k = current["k"]
            if stage == last["stage"] and pct == last["pct"] and k == last["k"]:
                return
            last["stage"], last["pct"], last["k"] = stage, pct, k
            prefix = f"File {k} of {len(srcs)}: {srcs[k - 1].name} \u2014 " if batch else ""

            def ui() -> None:
                self._file_bar.configure(value=max(pct, 0))
                self._file_set_status(f"{prefix}{stage}... {pct}%" if pct >= 0 else f"{prefix}{stage}...")
            self._post(lambda: self._safe_ui(ui))

        def run() -> None:
            segments: list[Segment] = []
            meta: dict | None = None
            error: Exception | None = None
            written = 0
            failed: list[str] = []
            try:
                transcriber = self.hooks.file_transcriber()
                for k, src in enumerate(srcs, 1):
                    current["k"] = k
                    try:
                        segments = filetranscribe.transcribe_file(src, transcriber, language,
                                                                  progress, cancel)
                        meta = filetranscribe.read_tags(src)
                        if not meta.get("length") and segments:
                            meta["length"] = segments[-1].end  # tags gave no duration
                        if not batch:
                            break
                        if not segments:
                            failed.append(f"{src.name}: no speech found")
                            continue
                        filetranscribe.output_path(src, fmt).write_text(
                            filetranscribe.render(segments, fmt, meta), encoding="utf-8", newline="\n")
                        written += 1
                    except TranscriptionCancelled:
                        raise
                    except Exception as e:  # noqa: BLE001
                        if not batch:
                            raise
                        if not isinstance(e, (TranscriptionError, OSError)):
                            log.exception("file transcription failed")
                        failed.append(f"{src.name}: {e}")  # report it and carry on with the next file
            except Exception as e:  # noqa: BLE001
                if not isinstance(e, TranscriptionError):  # TranscriptionCancelled is a subclass
                    log.exception("file transcription failed")
                error = e
            elapsed = time.monotonic() - started

            def finish() -> None:
                self._file_set_busy(False)
                if batch:
                    msg = f"Wrote {written} of {len(srcs)} file(s) next to the sources"
                    if failed:
                        msg += f"; failed: {'; '.join(failed)}"
                    if isinstance(error, TranscriptionCancelled):
                        msg = f"Cancelled. {msg}"
                    elif error is not None:
                        msg = f"Failed: {error}. {msg}"
                    self._file_set_status(msg)
                elif isinstance(error, TranscriptionCancelled):
                    self._file_set_status("Cancelled.")
                elif error is not None:
                    self._file_set_status(f"Failed: {error}")
                else:
                    self._file_segments = segments
                    self._file_meta = meta
                    self._file_render()
                    if not segments:
                        self._file_set_status("Done, but no speech was found.")
                    else:
                        audio = _fmt_clock(segments[-1].end)
                        self._file_set_status(f"Done: {audio} of audio in {_fmt_elapsed(elapsed)}")
            self._post(lambda: self._safe_ui(finish))

        threading.Thread(target=run, name="vibeflow-file-transcribe", daemon=True).start()

    def _file_refresh_engine(self, cfg: Config) -> None:
        text = engine_text(cfg)
        self._file_engine.configure(
            text=f"Engine: {text}. Change it on the Transcription tab (save first).")

    # -- Microphone -------------------------------------------------------------
    def _build_microphone(self, f: ttk.Frame) -> None:
        self._mic_label = tk.StringVar(value="")
        self._mic_status_var = tk.StringVar(value="")
        self._mic_level_var = tk.StringVar(value="")
        self._mic_seconds = tk.StringVar(value="5")
        self._mic_choices: list[tuple[str, str]] = []
        self._mic_names: list[str] = []

        dev = ttk.LabelFrame(f, text="Device", padding=10)
        dev.pack(fill="x")
        self._var("audio_device", "")  # hidden: the config value; the combo shows labels
        ttk.Label(dev, text="Input device").grid(row=0, column=0, sticky="w", pady=3, padx=(0, 10))
        self._device_combo = ttk.Combobox(dev, textvariable=self._mic_label, state="readonly",
                                          width=44, values=[])
        self._device_combo.grid(row=0, column=1, sticky="w", pady=3)
        self._device_combo.bind("<<ComboboxSelected>>", self._mic_device_selected)
        self._mic_refresh_btn = ttk.Button(dev, text="Refresh", command=self._mic_refresh)
        self._mic_refresh_btn.grid(row=0, column=2, padx=(8, 0))
        ttk.Label(dev, text="Windows default follows the recording device set in Windows "
                  "Sound settings.", foreground="gray", wraplength=600,
                  justify="left").grid(row=1, column=1, columnspan=2, sticky="w")
        self._entry(dev, 2, "Silence threshold", "silence_threshold", 8,
                    hint="0..1, quieter is ignored")

        tf = ttk.LabelFrame(f, text="Test", padding=10)
        tf.pack(fill="x", pady=(10, 0))
        meter = ttk.Frame(tf)
        meter.pack(fill="x")
        self._mic_bar = ttk.Progressbar(meter, mode="determinate", length=260, maximum=100)
        self._mic_bar.pack(side="left")
        ttk.Label(meter, textvariable=self._mic_level_var, wraplength=330,
                  justify="left").pack(side="left", padx=(10, 0))
        row = ttk.Frame(tf)
        row.pack(fill="x", pady=(8, 0))
        self._mic_meter_btn = ttk.Button(row, text="Start meter", command=self._mic_toggle_meter)
        self._mic_meter_btn.pack(side="left")
        ttk.Label(row, text="Clip (s)").pack(side="left", padx=(16, 4))
        self._mic_seconds_combo = ttk.Combobox(row, textvariable=self._mic_seconds, width=4,
                                               values=["3", "5", "10"], state="readonly")
        self._mic_seconds_combo.pack(side="left")
        row2 = ttk.Frame(tf)
        row2.pack(fill="x", pady=(8, 0))
        self._mic_play_btn = ttk.Button(row2, text="Record and play back",
                                        command=lambda: self._mic_record("play"))
        self._mic_play_btn.pack(side="left")
        self._mic_trans_btn = ttk.Button(row2, text="Record and transcribe",
                                         command=lambda: self._mic_record("transcribe"))
        self._mic_trans_btn.pack(side="left", padx=6)
        self._mic_stop_btn = ttk.Button(row2, text="Stop", command=self._mic_cancel.set,
                                        state="disabled")
        self._mic_stop_btn.pack(side="left")
        ttk.Label(tf, textvariable=self._mic_status_var, wraplength=620,
                  justify="left").pack(anchor="w", pady=(8, 0))
        self._mic_engine = ttk.Label(tf, text="", foreground="gray", wraplength=620,
                                     justify="left")
        self._mic_engine.pack(anchor="w", pady=(4, 0))
        rf = ttk.Frame(tf)
        rf.pack(fill="x", pady=(6, 0))
        self._mic_text = tk.Text(rf, height=3, wrap="word", font=("Segoe UI", 9), state="disabled")
        self._mic_text.pack(side="left", fill="x", expand=True)
        ttk.Button(rf, text="Copy", command=self._mic_copy).pack(side="left", padx=(6, 0),
                                                                 anchor="n")

    def _mic_refresh_engine(self, cfg: Config) -> None:
        self._mic_engine.configure(text=f"Engine: {engine_text(cfg)} (saved settings)")

    def _mic_set_status(self, text: str) -> None:
        self._mic_status_var.set(text)

    def _mic_set_text(self, text: str) -> None:
        self._mic_text.configure(state="normal")
        self._mic_text.delete("1.0", "end")
        self._mic_text.insert("1.0", text)
        self._mic_text.configure(state="disabled")

    def _mic_copy(self) -> None:
        text = self._mic_text.get("1.0", "end").strip()
        if text and self.top is not None:
            self.top.clipboard_clear()
            self.top.clipboard_append(text)
            self._mic_set_status("Copied.")

    def _mic_value(self) -> str:
        return str(self._vars["audio_device"].get()).strip()

    def _mic_rate(self) -> int:
        try:
            return int(self.hooks.get_config().audio.sample_rate) or 16000
        except Exception:
            return 16000

    def _mic_threshold(self) -> float:
        try:
            return _float({"v": self._vars["silence_threshold"].get()}, "v", "Silence threshold",
                          0.0, 1.0)
        except ValueError:
            return float(self.hooks.get_config().audio.silence_threshold)

    def _mic_device_selected(self, _event=None) -> None:
        label = self._mic_label.get()
        for text, value in self._mic_choices:
            if text == label:
                self._vars["audio_device"].set(value)
                break
        m = self._mic_monitor
        if m is not None and m.running and not self._mic_busy:
            self._mic_stop_meter()
            self._mic_start_meter()

    def _mic_refresh(self) -> None:
        if self._mic_busy:
            return
        self._mic_stop_meter()
        self._mic_set_status("Refreshing devices...")

        def run() -> None:
            try:
                ok = bool(self.hooks.refresh_audio_devices())
            except Exception:  # noqa: BLE001
                log.exception("device refresh failed")
                ok = False

            def done() -> None:
                self._load_devices()
                self._mic_set_status("Device list refreshed" if ok else
                                     "Close the test and stop dictating to rescan devices")
            self._post(lambda: self._safe_ui(done))
        threading.Thread(target=run, name="vibeflow-mic-refresh", daemon=True).start()

    def _mic_start_monitor(self) -> bool:
        """Start the shared input monitor (and the poll loop). Shows errors in the status line."""
        m = self._mic_monitor
        if m is not None and m.running:
            return True
        self._mic_quiet_since = None
        monitor = audio.InputMonitor(self._mic_value(), self._mic_rate())
        try:
            monitor.start()
        except audio.AudioError as e:
            self._mic_monitor = None
            self._mic_set_status(str(e))
            return False
        self._mic_monitor = monitor
        self._mic_schedule_poll()
        return True

    def _mic_start_meter(self) -> None:
        if self._mic_start_monitor():
            self._mic_meter_btn.configure(text="Stop meter")
            self._mic_set_status("")

    def _mic_toggle_meter(self) -> None:
        m = self._mic_monitor
        if m is not None and m.running:
            if self._mic_busy:
                return
            self._mic_stop_meter()
            self._mic_set_status("")
        else:
            self._mic_start_meter()

    def _mic_stop_meter(self) -> None:
        """Tk thread: stop the monitor and reset the meter."""
        m, self._mic_monitor = self._mic_monitor, None
        if m is not None:
            m.stop()
        self._mic_stop_ui()

    def _mic_stop_ui(self) -> None:
        if self._mic_poll_id is not None and self.top is not None:
            try:
                self.top.after_cancel(self._mic_poll_id)
            except tk.TclError:
                pass
        self._mic_poll_id = None
        self._mic_quiet_since = None
        try:
            self._mic_bar.configure(value=0)
            self._mic_level_var.set("")
            self._mic_meter_btn.configure(text="Start meter")
        except (tk.TclError, AttributeError):
            pass

    def stop_mic_test(self) -> None:
        """Thread-safe (called from quit() on another thread): cancel and close streams. No Tk."""
        self._mic_cancel.set()
        m = self._mic_monitor
        if m is not None:
            m.stop()

    def _mic_schedule_poll(self) -> None:
        if self.top is not None and self._mic_poll_id is None:
            self._mic_poll_id = self.top.after(50, self._mic_poll)

    def _mic_poll(self) -> None:
        self._mic_poll_id = None
        m = self._mic_monitor
        if m is None or not m.running or self.top is None:
            return
        try:
            peak, _rms, age = m.levels()
            now = time.monotonic()
            if age > 2.0:
                label = m.device_label
                self._mic_stop_meter()
                self._mic_set_status(f"No audio from {label}. Is it unplugged?")
                return
            if peak < 1e-4:
                if self._mic_quiet_since is None:
                    self._mic_quiet_since = now
            else:
                self._mic_quiet_since = None
            silent_for = 0.0 if self._mic_quiet_since is None else now - self._mic_quiet_since
            verdict = audio.level_verdict(peak, self._mic_threshold(), silent_for)
            self._mic_bar.configure(value=audio.meter_percent(peak))
            self._mic_level_var.set(f"{audio.level_db(peak):.0f} dB  {MIC_VERDICTS[verdict]}")
            if self._mic_phase == "recording":
                left = max(0.0, self._mic_rec_until - now)
                self._mic_set_status(f"Recording {left:.0f} s... speak now")
        except tk.TclError:
            return
        self._mic_schedule_poll()

    def _mic_set_busy_ui(self, busy: bool) -> None:
        self._mic_play_btn.configure(state="disabled" if busy else "normal")
        self._mic_trans_btn.configure(state="disabled" if busy else "normal")
        self._mic_refresh_btn.configure(state="disabled" if busy else "normal")
        self._mic_stop_btn.configure(state="normal" if busy else "disabled")

    def _mic_record(self, mode: str) -> None:
        if self._mic_busy:
            return
        try:
            seconds = float(self._mic_seconds.get())
        except ValueError:
            seconds = 5.0
        was_running = self._mic_monitor is not None and self._mic_monitor.running
        self._mic_cancel.clear()
        if not self._mic_start_monitor():
            return
        if not was_running:
            self._mic_meter_btn.configure(text="Stop meter")
        monitor = self._mic_monitor
        assert monitor is not None
        rate = self._mic_rate()
        threshold = self._mic_threshold()
        hook = self.hooks.test_transcribe
        self._mic_busy = True
        self._mic_phase = "recording"
        self._mic_rec_until = time.monotonic() + seconds
        self._mic_set_busy_ui(True)
        self._mic_set_status(f"Recording {seconds:.0f} s... speak now")
        if mode == "transcribe":
            self._mic_set_text("")
        monitor.begin_capture()

        def ui(fn: Callable[[], None]) -> None:
            self._post(lambda: self._safe_ui(fn))

        def recorded() -> None:
            self._mic_phase = ""

        def run() -> None:
            final = "Stopped."
            result: str | None = None
            try:
                self._mic_cancel.wait(seconds)
                data = monitor.end_capture()
                ui(recorded)
                if self._mic_cancel.is_set():
                    return
                if len(data) == 0:
                    final = "No audio captured."
                    return
                peak = audio._levels(data)[0]
                note = f"Peak {audio.level_db(peak):.0f} dB."
                if peak < threshold:
                    note += (" Dictation would ignore this recording "
                             f"(silence threshold {threshold:g}).")
                if mode == "play":
                    ui(lambda: self._mic_set_status("Playing back..."))
                    try:
                        audio.play(data, rate, self._mic_cancel)
                    except audio.AudioError as e:
                        final = f"{e} {note}"
                        return
                    final = "Stopped." if self._mic_cancel.is_set() else f"Done. {note}"
                    return
                if hook is None:
                    final = "Transcription test is not available."
                    return
                ui(lambda: self._mic_set_status("Transcribing..."))
                path = Path(tempfile.gettempdir()) / "vibeflow" / (
                    f"mictest-{datetime.now().strftime('%Y%m%d-%H%M%S')}.wav")
                try:
                    audio.write_wav(path, data, rate)
                    text = hook(path, len(data) / float(rate), self._mic_cancel)
                except TranscriptionCancelled:
                    return
                except TranscriptionError as e:
                    final = f"Transcription failed: {e}"
                    return
                except Exception as e:  # noqa: BLE001
                    log.exception("mic test transcription failed")
                    final = f"Transcription failed: {e}"
                    return
                finally:
                    try:
                        path.unlink()
                    except OSError:
                        pass
                if self._mic_cancel.is_set():
                    return
                result = text or ""
                final = f"Done. {note}" if text else f"No speech recognised. {note}"
            except Exception as e:  # noqa: BLE001
                log.exception("mic test failed")
                final = f"Microphone test failed: {e}"
            finally:
                def finish() -> None:
                    self._mic_busy = False
                    self._mic_phase = ""
                    self._mic_set_busy_ui(False)
                    if result is not None:
                        self._mic_set_text(result)
                    self._mic_set_status(final)
                    if not was_running:
                        self._mic_stop_meter()
                ui(finish)

        threading.Thread(target=run, name="vibeflow-mictest", daemon=True).start()

    def sync_audio_device(self, value: str) -> None:
        """Tk thread: a tray switch changed the saved mic. Follow it unless the form has an
        unsaved choice of its own."""
        var = self._vars.get("audio_device")
        if var is None:
            return
        if str(var.get()) == str(self._snapshot.get("audio_device", "")):
            var.set(value)
            self._snapshot["audio_device"] = value
            self._load_devices()

    # -- Cleanup ---------------------------------------------------------------
    def _build_cleanup(self, f: ttk.Frame) -> None:
        top = ttk.Frame(f)
        top.pack(fill="x")
        self._check(top, 0, "Clean up transcripts with an LLM", "cleanup_enabled")
        self._combo(top, 1, "Provider", "cleanup_provider", PROVIDERS, 14, readonly=True)
        self._entry(top, 2, "Base URL", "cleanup_base_url", 44, hint="LM Studio only")
        self._cleanup_combo = self._combo(top, 3, "Model", "cleanup_model", [], 34)
        self._refresh_btn = ttk.Button(top, text="Refresh", command=self._refresh_cleanup_models)
        self._refresh_btn.grid(row=3, column=2, sticky="w", padx=8)
        ttk.Label(top, text="Blank = first loaded LM Studio model / provider default.",
                  foreground="gray").grid(row=4, column=1, columnspan=2, sticky="w")
        self._entry(top, 5, "Timeout (s)", "cleanup_timeout", 8)

        pf = ttk.LabelFrame(f, text="Prompt", padding=8)
        pf.pack(fill="both", expand=True, pady=(10, 0))
        self._prompt = tk.Text(pf, height=7, wrap="word", font=("Segoe UI", 9), undo=True)
        self._prompt.pack(fill="both", expand=True)
        ttk.Button(pf, text="Reset to default",
                   command=self._reset_prompt).pack(anchor="e", pady=(6, 0))

        tf = ttk.LabelFrame(f, text="Test cleanup", padding=8)
        tf.pack(fill="x", pady=(10, 0))
        self._sample = tk.StringVar(value=SAMPLE_TEXT)
        ttk.Entry(tf, textvariable=self._sample).pack(fill="x")
        row = ttk.Frame(tf)
        row.pack(fill="x", pady=(6, 0))
        self._test_btn = ttk.Button(row, text="Test cleanup", command=self._test_cleanup)
        self._test_btn.pack(side="left")
        self._test_result = ttk.Label(row, text="", wraplength=480, justify="left")
        self._test_result.pack(side="left", padx=10)

    def _reset_prompt(self) -> None:
        if self._prompt is not None:
            self._prompt.delete("1.0", "end")
            self._prompt.insert("1.0", DEFAULT_PROMPT)

    def _refresh_cleanup_models(self) -> None:
        self._refresh_btn.configure(state="disabled")

        def work() -> list[str]:
            return self.hooks.cleanup_models(True)

        def finish(models: Any, err: Exception | None) -> None:
            self._refresh_btn.configure(state="normal")
            if err is None:
                self._cleanup_combo.configure(values=list(models))
                self._set_status(f"{len(models)} model(s) found." if models
                                 else "No models found (is the server running?).")
            else:
                self._set_status(f"Could not list models: {err}")

        def run() -> None:
            res: Any = None
            err: Exception | None = None
            try:
                res = work()
            except Exception as e:  # noqa: BLE001
                err = e
            self._post(lambda: self._safe_ui(lambda: finish(res, err)))
        threading.Thread(target=run, name="vibeflow-settings-models", daemon=True).start()

    def _safe_ui(self, fn: Callable[[], None]) -> None:
        try:
            fn()
        except tk.TclError:
            pass

    def _test_cleanup(self) -> None:
        try:
            cfg = config_from_form(self.hooks.get_config(), self._collect())
        except ValueError as e:
            self._test_result.configure(text=str(e), foreground="#d33")
            return
        text = self._sample.get().strip() or SAMPLE_TEXT
        self._test_btn.configure(state="disabled")
        self._test_result.configure(text="Testing...", foreground="")

        def run() -> None:
            out, err = "", None
            try:
                out = self.hooks.test_cleanup(cfg, text)
            except Exception as e:  # noqa: BLE001
                err = e

            def finish() -> None:
                self._test_btn.configure(state="normal")
                if err is not None:
                    self._test_result.configure(text=f"Failed: {err}", foreground="#d33")
                else:
                    self._test_result.configure(text=out, foreground="")
            self._post(lambda: self._safe_ui(finish))
        threading.Thread(target=run, name="vibeflow-settings-test", daemon=True).start()

    # -- API keys / About --------------------------------------------------------------
    def _build_keys(self, f: ttk.Frame) -> None:
        ttk.Label(f, text="Keys are stored in the .env file next to the app.",
                  foreground="gray").grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 8))
        self._entries: list[ttk.Entry] = []
        for i, key in enumerate(ENV_KEYS, start=1):
            ttk.Label(f, text=key).grid(row=i, column=0, sticky="w", pady=4, padx=(0, 10))
            var = tk.StringVar()
            self._env_vars[key] = var
            e = ttk.Entry(f, textvariable=var, show="•", width=48)
            e.grid(row=i, column=1, sticky="w", pady=4)
            self._entries.append(e)
        self._show_keys = tk.BooleanVar(value=False)
        ttk.Checkbutton(f, text="Show keys", variable=self._show_keys,
                        command=self._toggle_keys).grid(
            row=len(ENV_KEYS) + 1, column=1, sticky="w", pady=8)

    def _toggle_keys(self) -> None:
        for e in self._entries:
            e.configure(show="" if self._show_keys.get() else "•")

    def _build_about(self, f: ttk.Frame) -> None:
        ttk.Label(f, text="VibeFlow", font=("Segoe UI", 16, "bold")).pack(anchor="w")
        ttk.Label(f, text=f"Version {self.hooks.version}").pack(anchor="w", pady=(2, 8))
        ttk.Label(f, text=f"App folder: {ROOT}", wraplength=620, justify="left").pack(anchor="w")
        row = ttk.Frame(f)
        row.pack(anchor="w", pady=14)
        ttk.Button(row, text="Open config.toml", command=self._safe_call(
            lambda: self.hooks.open_config())).pack(side="left")
        ttk.Button(row, text="Open log folder", command=self._safe_call(
            lambda: self.hooks.open_logs())).pack(side="left", padx=8)

    def _safe_call(self, fn: Callable[[], None]) -> Callable[[], None]:
        def run() -> None:
            try:
                fn()
            except Exception as e:  # noqa: BLE001
                log.exception("settings action failed")
                messagebox.showerror("VibeFlow", str(e), parent=self.top)
        return run

    # -- state ------------------------------------------------------------------------
    def _populate(self) -> None:
        cfg = self.hooks.get_config()
        form = form_from_config(cfg)
        for key, value in form.items():
            if key in self._vars:
                self._vars[key].set(value)
        if self._prompt is not None:
            self._prompt.delete("1.0", "end")
            self._prompt.insert("1.0", form["cleanup_prompt"])
        self._snapshot = self._collect()
        self._env_snapshot = {k: os.environ.get(k, "") for k in ENV_KEYS}
        for k, var in self._env_vars.items():
            var.set(self._env_snapshot[k])
        try:
            self._show_start.set(bool(cfg.ui.show_settings_on_start))
        except AttributeError:
            pass
        try:
            self._autostart.set(bool(self.hooks.is_autostart()))
        except Exception:
            self._autostart.set(False)
        self._load_devices()
        try:
            self._cleanup_combo.configure(values=list(self.hooks.cleanup_models(False)))
        except Exception:
            pass
        self._refresh_models()
        self._file_refresh_engine(cfg)
        self._mic_refresh_engine(cfg)
        if not self._file_lang_init:  # initial language only; the File tab never persists it
            self._file_lang.set(cfg.transcription.language or "auto")
            self._file_lang_init = True
        self._check_chord()
        self._set_status("")

    def _load_devices(self) -> None:
        try:
            names = list(self.hooks.audio_devices())
        except Exception:
            names = []
        try:
            default = self.hooks.default_audio_device()
        except Exception:
            default = None
        saved = self._mic_value()
        self._mic_names = names
        self._mic_choices = device_choices(names, default, saved)
        self._device_combo.configure(values=[label for label, _ in self._mic_choices])
        self._mic_label.set(device_label_for(saved, self._mic_choices, names))

    def _collect(self) -> dict[str, Any]:
        form = {k: v.get() for k, v in self._vars.items()}
        if self._prompt is not None:
            form["cleanup_prompt"] = self._prompt.get("1.0", "end").strip()
        return form

    def _env_changes(self) -> dict[str, str]:
        return {k: v.get().strip() for k, v in self._env_vars.items()
                if v.get().strip() != self._env_snapshot.get(k, "")}

    def _is_dirty(self) -> bool:
        cur = self._collect()
        snap = self._snapshot
        norm = lambda d: {k: (v.strip() if isinstance(v, str) else v) for k, v in d.items()}  # noqa: E731
        return norm(cur) != norm(snap) or bool(self._env_changes())

    def _set_status(self, text: str) -> None:
        try:
            self._status.configure(text=text)
        except tk.TclError:
            pass

    def _set_banner(self, text: str | None) -> None:
        if text:
            self._banner.configure(text=text)
            self._banner.pack(side="top", fill="x", pady=(0, 8), before=self._nb)
        else:
            self._banner.pack_forget()

    def _on_show_start(self) -> None:
        try:
            self.hooks.set_show_on_start(bool(self._show_start.get()))
        except Exception:
            log.exception("could not save show-on-start")

    def _on_autostart(self) -> None:
        try:
            self.hooks.set_autostart(bool(self._autostart.get()))
        except Exception:
            log.exception("could not change autostart")
        try:
            self._autostart.set(bool(self.hooks.is_autostart()))
        except Exception:
            pass

    def save(self) -> bool:
        try:
            cfg = config_from_form(self.hooks.get_config(), self._collect())
        except ValueError as e:
            messagebox.showerror("Invalid setting", str(e), parent=self.top)
            return False
        env = self._env_changes()
        try:
            err = self.hooks.apply(cfg, env)
        except Exception as e:  # noqa: BLE001
            log.exception("apply failed")
            messagebox.showerror("Could not save", str(e), parent=self.top)
            return False
        self._snapshot = self._collect()
        self._env_snapshot.update({k: v for k, v in
                                   ((k, var.get().strip()) for k, var in self._env_vars.items())})
        self._refresh_models()
        self._file_refresh_engine(cfg)
        self._mic_refresh_engine(cfg)
        if err:
            self._set_status(f"Saved, but: {err}")
            messagebox.showwarning("VibeFlow", err, parent=self.top)
        else:
            self._set_status("Saved.")
        return True
