import copy
import tkinter as tk
from pathlib import Path

import pytest

from vibeflow import settings_ui
from vibeflow.cleanup import DEFAULT_PROMPT
from vibeflow.config import Config
from vibeflow.settings_ui import (SettingsHooks, SettingsWindow, config_from_form,
                                  form_from_config)


def test_round_trip_default():
    cfg = Config()
    assert config_from_form(cfg, form_from_config(cfg)) == cfg


def test_round_trip_customised():
    cfg = Config()
    cfg.hotkey.chord = "ctrl_r+f9"
    cfg.hotkey.min_recording_seconds = 0.25
    cfg.transcription.local.model = "D:\\models\\ggml-tiny.bin"
    cfg.transcription.language = "hr"
    cfg.cleanup.prompt = "custom prompt"
    cfg.cleanup.timeout_seconds = 7.5
    cfg.audio.device = "USB Mic"
    cfg.ui.show_settings_on_start = False
    assert config_from_form(cfg, form_from_config(cfg)) == cfg


def test_invalid_chord():
    form = form_from_config(Config())
    form["hotkey_chord"] = "ctrl+bogus"
    with pytest.raises(ValueError, match="unknown key"):
        config_from_form(Config(), form)


@pytest.mark.parametrize("key,value", [
    ("tap_ms", "abc"), ("tap_ms", "-5"), ("paste_delay_ms", "1.5"),
    ("silence_threshold", "2"), ("output_mode", "bogus"), ("backend", "bogus"),
    ("cleanup_provider", "bogus"), ("cleanup_timeout", "0"), ("threads", "-1"),
])
def test_invalid_values(key, value):
    form = form_from_config(Config())
    form[key] = value
    with pytest.raises(ValueError):
        config_from_form(Config(), form)


def test_min_not_below_max():
    form = form_from_config(Config())
    form["min_recording_seconds"] = "10"
    form["max_recording_seconds"] = "10"
    with pytest.raises(ValueError, match="smaller"):
        config_from_form(Config(), form)


def test_default_prompt_saved_as_empty():
    form = form_from_config(Config())
    assert form["cleanup_prompt"] == DEFAULT_PROMPT
    assert config_from_form(Config(), form).cleanup.prompt == ""
    form["cleanup_prompt"] = "be terse"
    assert config_from_form(Config(), form).cleanup.prompt == "be terse"


def test_model_switch_keeps_dir_style():
    form = form_from_config(Config())
    form["local_model"] = "ggml-tiny.bin"
    cfg = config_from_form(Config(), form)
    assert cfg.transcription.local.model == "whisper/models/ggml-tiny.bin"


def test_base_not_mutated():
    base = Config()
    before = copy.deepcopy(base)
    form = form_from_config(base)
    form["tap_ms"] = "123"
    config_from_form(base, form)
    assert base == before


def _hooks(cfg: Config, tmp_path: Path, applied: list) -> SettingsHooks:
    noop = lambda *a, **k: None  # noqa: E731
    return SettingsHooks(
        get_config=lambda: copy.deepcopy(cfg),
        apply=lambda c, env: applied.append((c, env)) or None,
        cleanup_models=lambda refresh: ["m1"],
        test_cleanup=lambda c, t: t.upper(),
        is_autostart=lambda: False, set_autostart=noop, set_show_on_start=noop,
        whisper_dir=lambda: tmp_path, models_dir=lambda: tmp_path,
        active_model_path=lambda: tmp_path / "ggml-small.bin",
        open_logs=noop, open_config=noop, audio_devices=lambda: ["Mic A"], version="0.0.0",
    )


def test_window_smoke(tmp_path, monkeypatch):
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("no display")
    root.withdraw()
    monkeypatch.setattr(settings_ui.messagebox, "askyesnocancel",
                        lambda *a, **k: pytest.fail("no prompt expected"))
    applied: list = []
    try:
        win = SettingsWindow(root, _hooks(Config(), tmp_path, applied), lambda fn: fn())
        win.show("transcription", "Download a model")
        root.update()
        assert win.is_visible()
        for tab in settings_ui.TABS:
            win.show(tab)
            root.update()
        assert not win._is_dirty()
        win.close_requested()  # clean form: hides without a prompt
        assert not win.is_visible()

        win.show()
        win._vars["tap_ms"].set("250")
        assert win._is_dirty()
        monkeypatch.setattr(settings_ui.messagebox, "askyesnocancel", lambda *a, **k: True)
        win.close_requested()
        assert not win.is_visible()
        assert applied and applied[-1][0].hotkey.tap_ms == 250
    finally:
        root.destroy()


def test_backend_shows_only_its_options(tmp_path):
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("no display")
    root.withdraw()
    try:
        win = SettingsWindow(root, _hooks(Config(), tmp_path, []), lambda fn: fn())
        win.show("transcription")
        root.update()

        def shown(row: int) -> bool:
            return all(w.winfo_manager() == "grid" for w in win._backend_rows[row][0])

        def sections() -> list[bool]:
            return [bool(frame.winfo_manager()) for frame, _, _ in win._backend_sections]

        assert shown(1) and not shown(3) and sections() == [True, True, True]
        assert win._tree.get_children() == tuple(settings_ui.whisper_setup.MODELS)

        win._vars["backend"].set("parakeet")
        root.update()
        assert sections() == [False, True, False]
        assert win._tree.get_children() == (settings_ui.parakeet.MODEL_NAME,)
        assert win._parakeet_hint.winfo_manager() == "grid"

        win._vars["backend"].set("groq")
        root.update()
        assert shown(0) and shown(3) and not shown(1) and not shown(4)
        assert sections() == [True, False, False]
        assert win._parakeet_hint.winfo_manager() == ""

        win._vars["backend"].set("local")
        root.update()
        assert sections() == [True, True, True] and shown(2)
        assert win._tree.get_children() == tuple(settings_ui.whisper_setup.MODELS)
    finally:
        root.destroy()


def test_parakeet_backend_accepted():
    cfg = Config()
    cfg.transcription.backend = "parakeet"
    form = form_from_config(cfg)
    assert config_from_form(Config(), form).transcription.backend == "parakeet"
    form["local_model"] = ""  # no whisper model needed for parakeet
    assert config_from_form(Config(), form).transcription.backend == "parakeet"
    form["backend"] = "local"
    with pytest.raises(ValueError):
        config_from_form(Config(), form)


def _install_parakeet(d: Path) -> None:
    d.mkdir(parents=True, exist_ok=True)
    for f in settings_ui.parakeet.PARAKEET_FILES:
        (d / f.name).write_bytes(b"x")


@pytest.mark.parametrize("backend, whisper_present, expected", [
    ("local", False, "parakeet"),   # no whisper model: switch to the new parakeet
    ("parakeet", False, "parakeet"),  # already parakeet: re-apply to load the model
    ("local", True, None),          # whisper works: leave it alone
    ("groq", False, None),          # cloud backend: leave it alone
])
def test_parakeet_download_auto_selects(tmp_path, backend, whisper_present, expected):
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("no display")
    root.withdraw()
    cfg = Config()
    cfg.transcription.backend = backend
    cfg.transcription.parakeet.model_dir = str(tmp_path / "pk")
    _install_parakeet(tmp_path / "pk")
    if whisper_present:
        (tmp_path / "ggml-small.bin").write_bytes(b"x")
    applied: list = []
    try:
        win = SettingsWindow(root, _hooks(cfg, tmp_path, applied), lambda fn: fn())
        win.show("transcription", "Download a model")
        win._auto_select(settings_ui.parakeet.MODEL_NAME)
        root.update()
        if expected is None:
            assert applied == []
        else:
            assert applied[-1][0].transcription.backend == expected
            assert win._vars["backend"].get() == expected
            assert win._banner.winfo_manager() == ""
            assert not win._is_dirty()
    finally:
        root.destroy()


def test_whisper_download_leaves_missing_parakeet(tmp_path):
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("no display")
    root.withdraw()
    cfg = Config()
    cfg.transcription.backend = "parakeet"
    cfg.transcription.parakeet.model_dir = str(tmp_path / "pk")  # not installed
    applied: list = []
    try:
        win = SettingsWindow(root, _hooks(cfg, tmp_path, applied), lambda fn: fn())
        win.show("transcription")
        (tmp_path / "ggml-small.bin").write_bytes(b"x")
        win._auto_select("small")
        root.update()
        t = applied[-1][0].transcription
        assert t.backend == "local" and t.local.model.endswith("ggml-small.bin")
        assert win._vars["backend"].get() == "local"
    finally:
        root.destroy()
