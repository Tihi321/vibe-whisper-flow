"""Settings window (tkinter + sv-ttk). Lives on the overlay's Tk root / Tk thread."""
from __future__ import annotations

import copy
import ctypes
import logging
import os
import sys
import threading
import tkinter as tk
from dataclasses import dataclass
from pathlib import Path
from tkinter import messagebox, ttk
from typing import Any, Callable

from . import whisper_setup
from .cleanup import DEFAULT_PROMPT
from .config import ROOT, Config, portable_path
from .hotkeys import parse_chord

try:  # optional: UI still works (with the stock ttk theme) without it
    import sv_ttk
except ImportError:  # pragma: no cover
    sv_ttk = None

log = logging.getLogger(__name__)

TABS = ("general", "transcription", "cleanup", "keys", "about")
TAB_TITLES = {"general": "General", "transcription": "Transcription", "cleanup": "Cleanup",
              "keys": "API keys", "about": "About"}
BACKENDS = ("local", "groq", "openai")
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
    elif not model:
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
    version: str = ""


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
        self._build_transcription(frames["transcription"])
        self._build_cleanup(frames["cleanup"])
        self._build_keys(frames["keys"])
        self._build_about(frames["about"])
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

        au = ttk.LabelFrame(f, text="Audio", padding=10)
        au.pack(fill="x", pady=(10, 0))
        self._device_combo = self._combo(au, 0, "Input device", "audio_device", [], width=44)
        ttk.Label(au, text="Leave blank for the Windows default microphone.",
                  foreground="gray").grid(row=1, column=1, sticky="w")
        self._entry(au, 2, "Silence threshold", "silence_threshold", 8,
                    hint="0..1, quieter is ignored")

        out = ttk.LabelFrame(f, text="Output and pill", padding=10)
        out.pack(fill="x", pady=(10, 0))
        self._combo(out, 0, "Output mode", "output_mode", OUTPUT_MODES, 10, readonly=True)
        self._entry(out, 1, "Paste delay (ms)", "paste_delay_ms", 8)
        self._check(out, 2, "Restore clipboard after pasting", "restore_clipboard")
        self._check(out, 3, "Show status pill while dictating", "show_pill")
        self._combo(out, 4, "Pill position", "pill_position", PILL_POSITIONS, 10, readonly=True)

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
                                         ("groq", "Groq API"), ("openai", "OpenAI API"))):
            ttk.Radiobutton(be, text=text, value=val, variable=be_var).grid(
                row=0, column=i, sticky="w", padx=(0, 16))
        opts = ttk.Frame(f)
        opts.pack(fill="x", pady=(8, 0))
        self._combo(opts, 0, "Language", "language", LANGUAGES, 10)
        self._model_combo = self._combo(opts, 1, "Local model", "local_model", [], 30,
                                        readonly=True)
        self._entry(opts, 2, "Threads", "threads", 6, hint="0 = automatic")
        self._entry(opts, 3, "Groq model", "groq_model", 28)
        self._entry(opts, 4, "OpenAI model", "openai_model", 28)

        mm = ttk.LabelFrame(f, text="Model manager", padding=8)
        mm.pack(fill="both", expand=True, pady=(8, 0))
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
            self._tree.insert("", "end", iid=name, values=(name, _fmt_size(size), ""))
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
        en.pack(fill="x", pady=(8, 0))
        self._engine_label = ttk.Label(en, text="")
        self._engine_label.pack(side="left")
        self._cuda_btn = ttk.Button(en, text="Install CUDA (NVIDIA)",
                                    command=lambda: self._install_engine(True))
        self._cuda_btn.pack(side="right")
        self._cpu_btn = ttk.Button(en, text="Install CPU",
                                   command=lambda: self._install_engine(False))
        self._cpu_btn.pack(side="right", padx=6)

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
        self._refresh_engine()

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
        fn = whisper_setup.model_filename(name)
        if (self.hooks.models_dir() / fn).exists():
            self._set_status(f"{name} is already installed.")
            return
        cb = self._progress_cb(f"Downloading {name}")
        cancel = self._cancel

        def work() -> Path:
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
        """If the active model file is missing, switch to the freshly downloaded one and save."""
        try:
            if self.hooks.active_model_path().exists():
                return
            base = self.hooks.get_config()
            rel = portable_path(self.hooks.models_dir() / whisper_setup.model_filename(name))
            base.transcription.local.model = rel
            err = self.hooks.apply(base, {})
        except Exception:
            log.exception("auto-select of downloaded model failed")
            return
        fn = whisper_setup.model_filename(name)
        self._vars["local_model"].set(fn)
        self._snapshot["local_model"] = fn
        self._refresh_models()
        self._set_banner(None)
        self._set_status(f"{name} selected and applied." if not err else f"Applied: {err}")

    def _delete_selected(self) -> None:
        name = self._selected_model()
        if self._busy or not name:
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
        self._check_chord()
        self._set_status("")

    def _load_devices(self) -> None:
        try:
            names = ["", *self.hooks.audio_devices()]
        except Exception:
            names = [""]
        self._device_combo.configure(values=names)

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
        if err:
            self._set_status(f"Saved, but: {err}")
            messagebox.showwarning("VibeFlow", err, parent=self.top)
        else:
            self._set_status("Saved.")
        return True
