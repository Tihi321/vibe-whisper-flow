from types import SimpleNamespace

from vibeflow import parakeet
from vibeflow.app import DictationApp
from vibeflow.config import Config


def _app(error, tmp_path, exists, backend="local"):
    cfg = Config()
    model = tmp_path / "ggml-m.bin"
    if exists:
        model.write_bytes(b"x")
    cfg.transcription.local.model = str(model)
    cfg.transcription.parakeet.model_dir = str(tmp_path / "pk")
    cfg.transcription.backend = backend
    app = DictationApp.__new__(DictationApp)
    app.cfg = cfg
    app._transcriber_error = error
    return app


def test_banner_none_when_ok(tmp_path):
    assert _app(None, tmp_path, True)._settings_banner() is None


def test_banner_download_when_model_missing(tmp_path):
    assert "Download a model" in _app("boom", tmp_path, False)._settings_banner()


def _install_parakeet(tmp_path):
    d = tmp_path / "pk"
    d.mkdir()
    for f in parakeet.PARAKEET_FILES:
        (d / f.name).write_bytes(b"x")


def test_banner_suggests_parakeet_when_only_parakeet_installed(tmp_path):
    _install_parakeet(tmp_path)
    banner = _app("boom", tmp_path, False)._settings_banner()
    assert "No whisper model" in banner and "Parakeet" in banner


def test_banner_other_whisper_model_installed(tmp_path):
    (tmp_path / "ggml-small.bin").write_bytes(b"x")
    assert "Pick another model" in _app("boom", tmp_path, False)._settings_banner()


def test_banner_parakeet_backend_missing_model(tmp_path):
    assert "Download the Parakeet model" in _app(
        "boom", tmp_path, False, "parakeet")._settings_banner()
    assert "switch the backend" in _app("boom", tmp_path, True, "parakeet")._settings_banner()


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
