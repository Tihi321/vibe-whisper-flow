from types import SimpleNamespace

from vibeflow.app import DictationApp
from vibeflow.config import Config


def _app(error, tmp_path, exists):
    cfg = Config()
    model = tmp_path / "m.bin"
    if exists:
        model.write_bytes(b"x")
    cfg.transcription.local.model = str(model)
    app = DictationApp.__new__(DictationApp)
    app.cfg = cfg
    app._transcriber_error = error
    return app


def test_banner_none_when_ok(tmp_path):
    assert _app(None, tmp_path, True)._settings_banner() is None


def test_banner_download_when_model_missing(tmp_path):
    assert "Download a whisper model" in _app("boom", tmp_path, False)._settings_banner()


def test_banner_error_text_otherwise(tmp_path):
    assert _app("engine broke", tmp_path, True)._settings_banner() == "engine broke"


def test_open_settings_defaults_banner_and_tab(tmp_path):
    app = _app("boom", tmp_path, False)
    calls = []
    app.overlay = SimpleNamespace(call_soon=lambda fn: calls.append(fn))
    app.settings = SimpleNamespace(show=lambda tab, banner: calls.append((tab, banner)))
    app.open_settings()
    calls[0]()
    assert calls[1][0] == "transcription" and "Download" in calls[1][1]
