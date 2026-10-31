"""System tray icon and menu (pystray)."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import pystray
from PIL import Image, ImageDraw

log = logging.getLogger(__name__)

COLOR_ENABLED = "#e8e8e8"
COLOR_DISABLED = "#7a7a7a"
COLOR_RECORDING = "#e5484d"
TITLE = "VibeFlow – dictation"


@dataclass
class TrayHooks:
    is_enabled: Callable[[], bool]
    set_enabled: Callable[[bool], None]
    current_backend: Callable[[], str]
    set_backend: Callable[[str], None]
    whisper_models: Callable[[], list[Path]]
    current_whisper_model: Callable[[], str]
    set_whisper_model: Callable[[str], None]
    cleanup_models: Callable[[], list[str]]
    current_cleanup_model: Callable[[], str | None]
    set_cleanup_model: Callable[[str | None], None]
    is_autostart: Callable[[], bool]
    set_autostart: Callable[[bool], None]
    open_settings: Callable[[], None]
    open_config: Callable[[], None]
    open_logs: Callable[[], None]
    reload_config: Callable[[], None]
    quit: Callable[[], None]
    input_devices: Callable[[], list[str]] = lambda: []
    default_input_device: Callable[[], str | None] = lambda: None
    current_input_device: Callable[[], str] = lambda: ""
    set_input_device: Callable[[str], None] = lambda _n: None
    refresh_input_devices: Callable[[], bool] = lambda: False
    open_mic_test: Callable[[], None] = lambda: None


def make_icon(color: str) -> Image.Image:
    """64x64 RGBA microphone glyph in `color` on a transparent background."""
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((22, 6, 42, 38), radius=10, fill=color)       # capsule
    d.arc((14, 20, 50, 50), start=0, end=180, fill=color, width=4)     # cradle
    d.rectangle((30, 50, 33, 56), fill=color)                          # stem
    d.rounded_rectangle((20, 56, 44, 59), radius=1, fill=color)        # base
    return img


class Tray:
    def __init__(self, hooks: TrayHooks, app_name: str = "VibeFlow"):
        self.hooks = hooks
        self.app_name = app_name
        self._enabled = True
        self._recording = False
        self._icon = pystray.Icon(app_name, make_icon(COLOR_ENABLED), TITLE, pystray.Menu(self._menu_items))

    # -- menu ---------------------------------------------------------
    @staticmethod
    def _checked(fn: Callable[[], bool]) -> Callable:
        def check(item) -> bool:
            try:
                return bool(fn())
            except Exception:
                log.exception("Tray check failed")
                return False
        return check

    def _action(self, fn: Callable, *args) -> Callable:
        def act(icon, item):
            try:
                fn(*args)
            except Exception:
                log.exception("Tray action failed")
            try:
                self._icon.update_menu()
            except Exception:
                pass
        return act

    def _menu_items(self):
        h = self.hooks
        M = pystray.MenuItem
        act = self._action
        chk = self._checked

        try:
            models = [Path(p) for p in h.whisper_models()]
        except Exception:
            log.exception("whisper_models failed")
            models = []

        def model_selected(p: Path) -> bool:
            return h.current_backend() == "local" and Path(h.current_whisper_model()).name == p.name

        trans_items = [
            M(p.stem, act(self._pick_whisper, p), checked=chk(lambda p=p: model_selected(p)), radio=True)
            for p in models
        ]
        trans_items.append(M("Parakeet v3 (local)", act(h.set_backend, "parakeet"),
                             checked=chk(lambda: h.current_backend() == "parakeet"), radio=True))
        trans_items.append(M("Groq Whisper API", act(h.set_backend, "groq"),
                             checked=chk(lambda: h.current_backend() == "groq"), radio=True))
        trans_items.append(M("OpenAI Whisper API", act(h.set_backend, "openai"),
                             checked=chk(lambda: h.current_backend() == "openai"), radio=True))

        try:
            cmodels = list(h.cleanup_models())
        except Exception:
            log.exception("cleanup_models failed")
            cmodels = []
        clean_items = [M("Off", act(h.set_cleanup_model, None),
                         checked=chk(lambda: h.current_cleanup_model() is None), radio=True)]
        for name in cmodels:
            clean_items.append(M(name, act(h.set_cleanup_model, name),
                                 checked=chk(lambda n=name: h.current_cleanup_model() == n), radio=True))
        clean_items.append(pystray.Menu.SEPARATOR)
        clean_items.append(M("Refresh list", act(lambda: None)))  # act() calls update_menu()

        return [
            M("Settings…", act(h.open_settings), default=True),
            M("Enabled", act(lambda: h.set_enabled(not h.is_enabled())), checked=chk(h.is_enabled)),
            pystray.Menu.SEPARATOR,
            M("Transcription", pystray.Menu(*trans_items)),
            M("Microphone", pystray.Menu(*self._mic_items())),
            M("Cleanup model", pystray.Menu(*clean_items)),
            pystray.Menu.SEPARATOR,
            M("Launch at login", act(lambda: h.set_autostart(not h.is_autostart())),
              checked=chk(h.is_autostart)),
            M("Open config.toml", act(h.open_config)),
            M("Open log folder", act(h.open_logs)),
            M("Reload config", act(h.reload_config)),
            pystray.Menu.SEPARATOR,
            M("Quit", act(h.quit)),
        ]

    def _mic_items(self) -> list:
        from .audio import match_device

        h = self.hooks
        M = pystray.MenuItem
        act = self._action
        chk = self._checked

        try:
            names = list(h.input_devices())
        except Exception:
            log.exception("input_devices failed")
            names = []
        try:
            default = h.default_input_device()
        except Exception:
            default = None
        try:
            current = h.current_input_device() or ""
        except Exception:
            current = ""

        items = [M(f"Windows default ({default})" if default else "Windows default",
                   act(h.set_input_device, ""),
                   checked=chk(lambda: (h.current_input_device() or "") == ""), radio=True)]
        for name in names:
            items.append(M(name, act(h.set_input_device, name),
                           checked=chk(lambda n=name: match_device(h.current_input_device() or "", names) == n),
                           radio=True))
        if current and match_device(current, names) is None:
            items.append(M(f"{current} (not connected)", act(lambda: None),
                           checked=lambda item: True, radio=True, enabled=False))
        if not names:
            items.append(M("No microphones found", act(lambda: None), enabled=False))
        items.append(pystray.Menu.SEPARATOR)
        items.append(M("Refresh list", act(self._refresh_mics)))
        items.append(M("Test microphone…", act(h.open_mic_test)))
        return items

    def _refresh_mics(self) -> None:
        if not self.hooks.refresh_input_devices():
            self.notify("VibeFlow", "Microphone list not rescanned: stop the microphone test "
                                    "and any dictation, then try again.")

    def _pick_whisper(self, path: Path) -> None:
        self.hooks.set_whisper_model(str(path))  # app side also switches backend to local

    # -- lifecycle ----------------------------------------------------
    def start(self) -> None:
        self._icon.run_detached()

    def update_menu(self) -> None:
        try:
            self._icon.update_menu()
        except Exception:
            log.debug("Tray update_menu failed", exc_info=True)

    def stop(self) -> None:
        try:
            self._icon.stop()
        except Exception:
            log.exception("Tray stop failed")

    def _refresh_icon(self) -> None:
        if self._recording:
            color = COLOR_RECORDING
        else:
            color = COLOR_ENABLED if self._enabled else COLOR_DISABLED
        try:
            self._icon.icon = make_icon(color)
        except Exception:
            log.exception("Tray icon update failed")

    def set_recording(self, recording: bool) -> None:
        self._recording = recording
        self._refresh_icon()

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = enabled
        self._refresh_icon()

    def notify(self, title: str, message: str) -> None:
        try:
            self._icon.notify(message, title)
        except Exception:
            log.debug("Tray notify failed", exc_info=True)
