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


def _hooks(cfg: Config, tmp_path: Path, applied: list, file_transcriber=None,
           **overrides) -> SettingsHooks:
    noop = lambda *a, **k: None  # noqa: E731
    return SettingsHooks(
        get_config=lambda: copy.deepcopy(cfg),
        apply=lambda c, env: applied.append((c, env)) or None,
        cleanup_models=lambda refresh: ["m1"],
        test_cleanup=lambda c, t: t.upper(),
        is_autostart=lambda: False, set_autostart=noop, set_show_on_start=noop,
        whisper_dir=lambda: tmp_path, models_dir=lambda: tmp_path,
        active_model_path=lambda: tmp_path / "ggml-small.bin",
        open_logs=noop, open_config=noop, audio_devices=lambda: ["Mic A"],
        file_transcriber=file_transcriber or (lambda: None), version="0.0.0",
        **overrides,
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


class _Pump:
    """Thread-safe `post` for tests: queue callbacks from workers, run them on the Tk thread."""

    def __init__(self, root):
        import queue

        self.root, self.q = root, queue.Queue()

    def post(self, fn):
        self.q.put(fn)

    def wait(self, win, timeout=10, until=None):
        import queue
        import time

        deadline = time.time() + timeout
        busy = until or (lambda: win._file_busy)
        while busy() and time.time() < deadline:
            try:
                self.q.get(timeout=0.02)()
            except queue.Empty:
                pass
            self.root.update()
        while not self.q.empty():
            self.q.get()()


def test_file_tab_does_not_dirty_and_runs(tmp_path, monkeypatch):
    from vibeflow.transcribe import Segment

    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("no display")
    root.withdraw()

    class Stub:
        def transcribe_segments(self, wav, language, duration, progress, cancel):
            progress(0.5)
            return [Segment(0.0, 1.5, "Hello."), Segment(62.0, 64.0, "World.")]

    monkeypatch.setattr(settings_ui.filetranscribe, "decode_to_wav",
                        lambda src, dst, progress=None, cancel=None: 70.0)
    cfg = Config()
    cfg.transcription.language = "hr"
    pump = _Pump(root)
    try:
        win = SettingsWindow(root, _hooks(cfg, tmp_path, [], file_transcriber=lambda: Stub()),
                             pump.post)
        win.show("file")
        root.update()
        assert "file" in settings_ui.TABS
        assert win._file_lang.get() == "hr"  # initialised from the saved config
        assert "Engine:" in win._file_engine.cget("text")

        # editing File tab fields leaves the form clean
        win._file_lang.set("de")
        win._file_format.set(settings_ui.filetranscribe.FORMAT_LABELS["srt"])
        assert not win._is_dirty()
        assert not any(k.startswith("file") for k in win._collect())

        # no file chosen: readable status, nothing runs
        win._file_start()
        assert "Choose" in win._file_status_var.get() and not win._file_busy

        src = tmp_path / "talk.mp3"
        src.write_bytes(b"x")
        win._file_src = src
        win._file_path.set(str(src))
        win._file_format.set(settings_ui.filetranscribe.FORMAT_LABELS["plain"])
        win._file_start()
        assert win._file_busy
        pump.wait(win)
        assert not win._file_busy
        assert win._file_text.get("1.0", "end-1c") == "Hello. World."
        assert win._file_status_var.get().startswith("Done: 1:04 of audio")

        # re-render on format change without re-running
        win._file_format.set(settings_ui.filetranscribe.FORMAT_LABELS["timestamps"])
        win._file_format_combo.event_generate("<<ComboboxSelected>>")
        root.update()
        assert win._file_text.get("1.0", "end-1c") == "[00:00:00] Hello.\n[00:01:02] World."

        # reopening Settings (populate) keeps the result and stays clean
        win.show("file")
        root.update()
        assert win._file_text.get("1.0", "end-1c").startswith("[00:00:00]")
        assert not win._is_dirty()

        # saving a different backend refreshes the engine line
        assert "whisper.cpp" in win._file_engine.cget("text")
        win._vars["backend"].set("groq")
        assert win.save()
        assert "Groq API" in win._file_engine.cget("text")
    finally:
        root.destroy()


def test_file_tab_error_and_cancel(tmp_path, monkeypatch):
    from vibeflow.transcribe import TranscriptionCancelled, TranscriptionError

    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("no display")
    root.withdraw()
    src = tmp_path / "a.mp3"
    src.write_bytes(b"x")
    outcome = {"exc": TranscriptionError("model missing")}
    pump = _Pump(root)

    def failing():
        raise outcome["exc"]

    try:
        win = SettingsWindow(root, _hooks(Config(), tmp_path, [], file_transcriber=failing),
                             pump.post)
        win.show("file")
        win._file_src = src
        for exc, text in ((TranscriptionError("model missing"), "Failed: model missing"),
                          (TranscriptionCancelled("cancelled"), "Cancelled.")):
            outcome["exc"] = exc
            win._file_start()
            pump.wait(win)
            assert win._file_status_var.get() == text
            assert not win._file_segments
    finally:
        root.destroy()


def test_file_tab_lrc_batch_writes_next_to_sources(tmp_path, monkeypatch):
    from vibeflow.transcribe import Segment, TranscriptionError

    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("no display")
    root.withdraw()

    class Stub:
        def transcribe_segments(self, wav, language, duration, progress, cancel):
            return [Segment(0.0, 1.5, "Hello."), Segment(10.0, 12.0, "World.")]

    def fake_decode(src, dst, progress=None, cancel=None):
        if src.name == "bad.mp3":
            raise TranscriptionError("broken file")
        return 12.0

    monkeypatch.setattr(settings_ui.filetranscribe, "decode_to_wav", fake_decode)
    songs = []
    for name in ("one.mp3", "bad.mp3", "three.mp3"):
        p = tmp_path / name
        p.write_bytes(b"x")
        songs.append(p)
    pump = _Pump(root)
    try:
        win = SettingsWindow(root, _hooks(Config(), tmp_path, [], file_transcriber=lambda: Stub()),
                             pump.post)
        win.show("file")
        root.update()
        win._file_format.set(settings_ui.filetranscribe.FORMAT_LABELS["lrc"])
        win._file_format_combo.event_generate("<<ComboboxSelected>>")
        root.update()
        assert win._file_beside.get()  # on by default for LRC
        win._file_srcs = songs
        win._file_src = songs[0]
        win._file_start()
        pump.wait(win)
        assert (tmp_path / "one.lrc").read_text(encoding="utf-8").startswith("[ti:one]")
        assert (tmp_path / "three.lrc").is_file()
        assert not (tmp_path / "bad.lrc").exists()  # one failure does not stop the batch
        status = win._file_status_var.get()
        assert status.startswith("Wrote 2 of 3") and "bad.mp3: broken file" in status
    finally:
        root.destroy()


# ---------------------------------------------------------------- Microphone tab (VWT-06)

def test_device_choices_and_labels():
    ch = settings_ui.device_choices(["Mic A", "Mic B"], "Mic A", "")
    assert ch == [("Windows default (Mic A)", ""), ("Mic A", "Mic A"), ("Mic B", "Mic B")]
    assert settings_ui.device_choices(["Mic A"], None, "")[0][0] == "Windows default"
    ch = settings_ui.device_choices(["Mic A"], None, "Ghost")
    assert ch[-1] == ("Ghost (not connected)", "Ghost")
    assert settings_ui.device_label_for("Ghost", ch, ["Mic A"]) == "Ghost (not connected)"
    ch = settings_ui.device_choices(["USB Mic B"], None, "usb")
    assert len(ch) == 2  # legacy substring matches: nothing appended
    assert settings_ui.device_label_for("usb", ch, ["USB Mic B"]) == "USB Mic B"
    assert settings_ui.device_label_for("", ch, ["USB Mic B"]) == "Windows default"


def test_engine_text_all_backends():
    cfg = Config()
    cfg.transcription.local.model = "D:\\m\\ggml-small.bin"
    cfg.transcription.backend = "local"
    assert settings_ui.engine_text(cfg) == "Local (whisper.cpp), ggml-small.bin"
    cfg.transcription.backend = "parakeet"
    assert settings_ui.engine_text(cfg) == "Local (Parakeet v3)"
    cfg.transcription.backend = "groq"
    assert settings_ui.engine_text(cfg).startswith("Groq API, ")
    cfg.transcription.backend = "openai"
    assert settings_ui.engine_text(cfg).startswith("OpenAI API, ")


class FakeMonitor:
    instances: list = []
    peak = 0.3
    fail = None

    def __init__(self, device, sample_rate=16000, block_seconds=0.05):
        self.device = device
        self.sample_rate = sample_rate
        self.device_label = device or "Windows default"
        self.running = False
        self.captured = False
        FakeMonitor.instances.append(self)

    def start(self):
        if FakeMonitor.fail:
            raise settings_ui.audio.AudioError(FakeMonitor.fail)
        self.running = True

    def stop(self):
        self.running = False

    def levels(self):
        return FakeMonitor.peak, FakeMonitor.peak, 0.0

    def begin_capture(self):
        self.captured = True

    def end_capture(self):
        import numpy as np

        return np.full(1600, 3000, dtype=np.int16)


def _mic_window(tmp_path, monkeypatch, pump, cfg=None, **hook_kw):
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("no display")
    root.withdraw()
    FakeMonitor.instances = []
    FakeMonitor.peak = 0.3
    FakeMonitor.fail = None
    monkeypatch.setattr(settings_ui.audio, "InputMonitor", FakeMonitor)
    win = SettingsWindow(root, _hooks(cfg or Config(), tmp_path, [], **hook_kw), pump.post)
    win.show("microphone")
    root.update()
    return root, win


def test_microphone_tab_device_pick_and_save(tmp_path):
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("no display")
    root.withdraw()
    applied: list = []
    try:
        win = SettingsWindow(
            root, _hooks(Config(), tmp_path, applied,
                         default_audio_device=lambda: "Mic A"), lambda fn: fn())
        win.show("microphone")
        root.update()
        assert "microphone" in settings_ui.TABS
        assert tuple(win._device_combo.cget("values")) == ("Windows default (Mic A)", "Mic A")
        assert win._mic_label.get() == "Windows default (Mic A)"
        assert not win._is_dirty()
        win._mic_label.set("Mic A")
        win._device_combo.event_generate("<<ComboboxSelected>>")
        root.update()
        assert win._vars["audio_device"].get() == "Mic A"
        assert win._is_dirty()
        assert win.save()
        assert applied[-1][0].audio.device == "Mic A"
        assert not win._is_dirty()
    finally:
        root.destroy()


def test_sync_audio_device_keeps_clean_form_clean(tmp_path):
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("no display")
    root.withdraw()
    try:
        win = SettingsWindow(root, _hooks(Config(), tmp_path, []), lambda fn: fn())
        win.show("microphone")
        win.sync_audio_device("Mic A")
        assert win._vars["audio_device"].get() == "Mic A"
        assert win._mic_label.get() == "Mic A"
        assert not win._is_dirty()
        win._vars["audio_device"].set("Other")  # user edit: tray must not override it
        win.sync_audio_device("Mic B")
        assert win._vars["audio_device"].get() == "Other"
    finally:
        root.destroy()


def test_mic_record_and_play(tmp_path, monkeypatch):
    pump = _Pump(None)
    played = []
    monkeypatch.setattr(settings_ui.audio, "play",
                        lambda data, rate, cancel=None: played.append((len(data), rate)))
    root, win = _mic_window(tmp_path, monkeypatch, pump)
    pump.root = root
    try:
        win._mic_seconds.set("3")
        win._mic_cancel.wait = lambda timeout=None: False  # do not really wait
        win._mic_record("play")
        assert win._mic_busy
        pump.wait(win, until=lambda: win._mic_busy)
        assert played == [(1600, 16000)]
        assert win._mic_status_var.get().startswith("Done.")
        assert "Peak" in win._mic_status_var.get()
        assert not FakeMonitor.instances[0].running  # was not running before: stopped again
        assert not win._is_dirty()
    finally:
        root.destroy()


def test_mic_record_and_transcribe(tmp_path, monkeypatch):
    pump = _Pump(None)
    seen = {}

    def hook(path, seconds, cancel):
        seen["exists"] = path.exists()
        seen["path"] = path
        seen["seconds"] = seconds
        return "hello there"

    monkeypatch.setattr(settings_ui.tempfile, "gettempdir", lambda: str(tmp_path))
    root, win = _mic_window(tmp_path, monkeypatch, pump, test_transcribe=hook)
    pump.root = root
    try:
        win._mic_cancel.wait = lambda timeout=None: False
        win._mic_record("transcribe")
        pump.wait(win, until=lambda: win._mic_busy)
        assert win._mic_text.get("1.0", "end").strip() == "hello there"
        assert win._mic_status_var.get().startswith("Done.")
        assert seen["exists"] and abs(seen["seconds"] - 0.1) < 1e-9
        assert not seen["path"].exists()  # temp WAV deleted
    finally:
        root.destroy()


def test_mic_transcribe_error_and_missing_hook(tmp_path, monkeypatch):
    from vibeflow.transcribe import TranscriptionError

    pump = _Pump(None)

    def hook(path, seconds, cancel):
        raise TranscriptionError("whisper model not found")

    monkeypatch.setattr(settings_ui.tempfile, "gettempdir", lambda: str(tmp_path))
    root, win = _mic_window(tmp_path, monkeypatch, pump, test_transcribe=hook)
    pump.root = root
    try:
        win._mic_cancel.wait = lambda timeout=None: False
        win._mic_record("transcribe")
        pump.wait(win, until=lambda: win._mic_busy)
        assert "Transcription failed: whisper model not found" in win._mic_status_var.get()
        assert not list((tmp_path / "vibeflow").glob("mictest-*.wav"))
        win.hooks.test_transcribe = None
        win._mic_record("transcribe")
        pump.wait(win, until=lambda: win._mic_busy)
        assert "not available" in win._mic_status_var.get()
    finally:
        root.destroy()


def test_mic_stop_cancels(tmp_path, monkeypatch):
    pump = _Pump(None)
    called = []
    monkeypatch.setattr(settings_ui.audio, "play", lambda *a, **k: called.append(1))
    root, win = _mic_window(tmp_path, monkeypatch, pump)
    pump.root = root
    try:
        win._mic_seconds.set("10")
        win._mic_record("play")
        assert win._mic_busy
        win._mic_cancel.set()  # the Stop button
        pump.wait(win, until=lambda: win._mic_busy)
        assert win._mic_status_var.get() == "Stopped."
        assert called == []
    finally:
        root.destroy()


def test_mic_start_error_shown(tmp_path, monkeypatch):
    pump = _Pump(None)
    root, win = _mic_window(tmp_path, monkeypatch, pump)
    pump.root = root
    try:
        FakeMonitor.fail = "Ghost is not connected"
        win._mic_toggle_meter()
        assert win._mic_status_var.get() == "Ghost is not connected"
        assert win._mic_monitor is None
    finally:
        root.destroy()


def test_mic_silence_timer(tmp_path, monkeypatch):
    pump = _Pump(None)
    root, win = _mic_window(tmp_path, monkeypatch, pump)
    pump.root = root
    clock = [100.0]
    monkeypatch.setattr(settings_ui.time, "monotonic", lambda: clock[0])
    try:
        FakeMonitor.peak = 0.0
        win._mic_toggle_meter()
        win.top.after_cancel(win._mic_poll_id)
        win._mic_poll_id = None
        win._mic_poll()
        assert win._mic_quiet_since == 100.0
        assert "No signal" not in win._mic_level_var.get()
        clock[0] = 101.0
        win._mic_poll()
        assert "No signal" not in win._mic_level_var.get()
        clock[0] = 101.6
        win._mic_poll()
        assert "No signal" in win._mic_level_var.get()
        FakeMonitor.peak = 0.5  # a loud block resets the timer
        win._mic_poll()
        assert win._mic_quiet_since is None
        assert "Good" in win._mic_level_var.get()
        win._mic_stop_meter()
    finally:
        root.destroy()


def test_mic_watchdog_unplugged(tmp_path, monkeypatch):
    pump = _Pump(None)
    root, win = _mic_window(tmp_path, monkeypatch, pump)
    pump.root = root
    try:
        win._mic_toggle_meter()
        win.top.after_cancel(win._mic_poll_id)
        win._mic_poll_id = None
        mon = win._mic_monitor
        mon.levels = lambda: (0.0, 0.0, 2.5)
        win._mic_poll()
        assert "Is it unplugged?" in win._mic_status_var.get()
        assert win._mic_monitor is None and not mon.running
    finally:
        root.destroy()
