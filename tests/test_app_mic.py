import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from vibeflow import audio
from vibeflow.app import DictationApp
from vibeflow.config import Config
from vibeflow.transcribe import Segment, TranscriptionError


class FakeRecorder:
    instances: list = []

    def __init__(self, cfg, fell_back=False):
        self.cfg = cfg
        self.fell_back = fell_back
        self.starts = 0
        FakeRecorder.instances.append(self)

    def start(self):
        self.starts += 1


def _app(fell_back=False):
    app = DictationApp.__new__(DictationApp)
    app.cfg = Config()
    app._transcriber = object()
    app._missing_mic_warned = set()
    app.flashes = []
    app.notes = []
    app._flash = lambda kind, text, seconds=1.5: app.flashes.append((kind, text))
    app.tray = SimpleNamespace(notify=lambda t, m: app.notes.append(m),
                               update_menu=lambda: app.flashes.append(("menu", "")))
    FakeRecorder.instances = []
    app._audio = SimpleNamespace(Recorder=lambda cfg: FakeRecorder(cfg, fell_back),
                                 AudioError=audio.AudioError)
    app.recorder = FakeRecorder(app.cfg.audio, fell_back)
    return app


def test_set_input_device(monkeypatch):
    app = _app()
    saved = []
    app._save = lambda: saved.append(app.cfg.audio.device)
    app._missing_mic_warned.add("USB Mic")
    synced = []
    app.settings = SimpleNamespace(sync_audio_device=synced.append)
    app.overlay = SimpleNamespace(call_soon=lambda fn: fn())
    app._set_input_device("USB Mic")
    assert app.cfg.audio.device == "USB Mic"
    assert saved == ["USB Mic"]
    assert "USB Mic" not in app._missing_mic_warned
    assert app.flashes[0][0] == "ok" and "USB Mic" in app.flashes[0][1]
    assert synced == ["USB Mic"]
    app._set_input_device("")
    assert "Windows default" in app.flashes[-1][1]


def test_start_recording_keeps_recorder_after_in_place_switch():
    app = _app()
    rec = app.recorder
    app.cfg.audio.device = "Other"  # tray switch mutates the shared object
    assert app.start_recording() is True
    assert app.recorder is rec and rec.starts == 1


def test_start_recording_rebuilds_recorder_after_config_swap():
    app = _app()
    old = app.recorder
    app.cfg = Config()  # Settings Save during a recording swaps in a new Config
    assert app.start_recording() is True
    assert app.recorder is not old
    assert app.recorder.cfg is app.cfg.audio and app.recorder.starts == 1


def test_fallback_notice_once_per_missing_name():
    app = _app(fell_back=True)
    app.cfg.audio.device = "Ghost Mic"
    assert app.start_recording() is True
    assert app.start_recording() is True
    assert len(app.notes) == 1 and "Ghost Mic" in app.notes[0]
    assert [f for f in app.flashes if f[0] == "error"] and len(
        [f for f in app.flashes if f[0] == "error"]) == 1
    app.cfg.audio.device = "Other Ghost"
    app.start_recording()
    assert len(app.notes) == 2


def test_no_fallback_notice_when_mic_found():
    app = _app(fell_back=False)
    app.cfg.audio.device = "Real Mic"
    app.start_recording()
    assert app.notes == []


def test_test_transcribe_joins_segments_and_passes_args(tmp_path):
    app = _app()
    app.cfg.transcription.language = "hr"
    calls = []

    class T:
        def transcribe_segments(self, wav, language, duration, progress, cancel):
            calls.append((wav, language, duration, cancel))
            progress(0.5)
            return [Segment(0.0, 1.0, " Hello "), Segment(1.0, 2.0, "world.")]

    app._file_transcriber = lambda: T()
    cancel = threading.Event()
    wav = tmp_path / "a.wav"
    assert app._test_transcribe(wav, 2.5, cancel) == "Hello  world."
    assert calls == [(wav, "hr", 2.5, cancel)]


def test_test_transcribe_propagates_error():
    app = _app()

    def boom():
        raise TranscriptionError("not set up")

    app._file_transcriber = boom
    with pytest.raises(TranscriptionError):
        app._test_transcribe(Path("x.wav"), 1.0, threading.Event())


def test_refresh_audio_devices(monkeypatch):
    app = _app()
    monkeypatch.setattr(audio, "refresh_devices", lambda: True)
    app._audio = audio
    assert app._refresh_audio_devices() is True

    def boom():
        raise audio.AudioError("x")

    monkeypatch.setattr(audio, "refresh_devices", boom)
    app.flashes.clear()
    assert app._refresh_audio_devices() is False
    assert ("menu", "") in app.flashes  # update_menu still called


def test_default_audio_device(monkeypatch):
    app = _app()
    app._audio = SimpleNamespace(default_input_name=lambda: "Mic X")
    assert app._default_audio_device() == "Mic X"

    def boom():
        raise RuntimeError("x")

    app._audio = SimpleNamespace(default_input_name=boom)
    assert app._default_audio_device() is None
