import os
import tomllib

from vibeflow import config
from vibeflow.config import (Config, config_from_dict, config_to_dict, get_secret, load_config,
                             load_env, resolve_path, save_config)


def test_defaults():
    c = Config()
    assert c.hotkey.chord == "ctrl+win"
    assert c.hotkey.tap_ms == 300 and c.hotkey.double_tap_ms == 400
    assert c.hotkey.max_recording_seconds == 300 and c.hotkey.min_recording_seconds == 0.6
    assert c.audio.sample_rate == 16000
    assert c.audio.silence_threshold == 0.01
    assert c.cleanup.base_url == "http://127.0.0.1:1234/v1"
    assert c.transcription.backend == "local"
    assert c.transcription.local.model == "whisper/models/ggml-small.bin"
    assert c.cleanup.provider == "lmstudio" and c.cleanup.timeout_seconds == 20
    assert c.output.mode == "paste" and c.output.restore_clipboard is False
    assert c.ui.pill_position == "bottom"


def test_round_trip():
    c = Config()
    c.hotkey.chord = "ctrl_r"
    c.cleanup.model = "qwen"
    c.transcription.cloud.groq_model = "x"
    assert config_from_dict(config_to_dict(c)) == c


def test_unknown_keys_ignored_and_bad_values_default():
    c = config_from_dict({
        "bogus": 1,
        "hotkey": {"chord": "f9", "tap_ms": "abc", "nope": 3, "double_tap_ms": 500},
        "output": "not a table",
        "cleanup": {"enabled": "yes"},
    })
    assert c.hotkey.chord == "f9"
    assert c.hotkey.tap_ms == 300
    assert c.hotkey.double_tap_ms == 500
    assert c.output.mode == "paste"
    assert c.cleanup.enabled is True


def test_int_for_float_field():
    c = config_from_dict({"hotkey": {"min_recording_seconds": 1}})
    assert c.hotkey.min_recording_seconds == 1.0


def test_save_and_load(tmp_path):
    p = tmp_path / "c.toml"
    c = Config()
    c.transcription.local.model = "D:/m/ggml-medium.bin"
    save_config(c, p)
    assert load_config(p) == c
    assert not list(tmp_path.glob("*.tmp"))


def test_load_creates_from_example(tmp_path):
    p = tmp_path / "sub" / "config.toml"
    c = load_config(p)
    assert p.exists()
    assert c == Config()
    assert (config.ROOT / "config.example.toml").read_bytes() == p.read_bytes()


def test_example_parses_to_defaults():
    with open(config.ROOT / "config.example.toml", "rb") as fh:
        assert config_from_dict(tomllib.load(fh)) == Config()


def test_bad_toml_gives_defaults(tmp_path):
    p = tmp_path / "c.toml"
    p.write_text("this is [not toml")
    assert load_config(p) == Config()


def test_config_path_env(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBEFLOW_CONFIG", str(tmp_path / "x.toml"))
    assert config.config_path() == tmp_path / "x.toml"
    monkeypatch.delenv("VIBEFLOW_CONFIG")
    assert config.config_path() == config.ROOT / "config.toml"


def test_env_parsing(tmp_path, monkeypatch):
    for k in ("A_KEY", "B_KEY", "C_KEY", "D_KEY", "E_KEY", "F_KEY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("E_KEY", "preset")
    p = tmp_path / ".env"
    p.write_text(
        "# comment\n\nA_KEY=abc\nexport B_KEY=\"quoted value\"\nC_KEY='single # not comment'\n"
        "D_KEY=val # trailing\nE_KEY=other\nF_KEY=\n  junk line\n", encoding="utf-8")
    d = load_env(p)
    assert d == {"A_KEY": "abc", "B_KEY": "quoted value", "C_KEY": "single # not comment",
                 "D_KEY": "val", "E_KEY": "other", "F_KEY": ""}
    assert os.environ["A_KEY"] == "abc"
    assert os.environ["E_KEY"] == "preset"  # setdefault
    assert get_secret("F_KEY") is None
    assert get_secret("A_KEY") == "abc"
    assert get_secret("NOPE_NOT_SET") is None
    for k in ("A_KEY", "B_KEY", "C_KEY", "D_KEY", "F_KEY"):
        os.environ.pop(k, None)


def test_env_missing(tmp_path):
    assert load_env(tmp_path / "none") == {}


def test_resolve_path(tmp_path):
    assert resolve_path(tmp_path) == tmp_path
    assert resolve_path("whisper/x") == config.ROOT / "whisper" / "x"
