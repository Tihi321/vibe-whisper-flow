from pathlib import Path

import pytest

from vibeflow.config import LocalWhisperConfig, TranscriptionConfig
from vibeflow.transcribe import (
    LocalWhisper,
    TranscriptionError,
    _clean_output,
    make_transcriber,
)


def _cfg(tmp_path, threads=0):
    return LocalWhisperConfig(
        whisper_cli=str(tmp_path / "whisper-cli.exe"),
        model=str(tmp_path / "model.bin"),
        threads=threads,
    )


def test_available_missing_exe(tmp_path):
    ok, reason = LocalWhisper(_cfg(tmp_path)).available()
    assert not ok
    assert str(tmp_path / "whisper-cli.exe") in reason


def test_available_missing_model(tmp_path):
    (tmp_path / "whisper-cli.exe").write_bytes(b"")
    ok, reason = LocalWhisper(_cfg(tmp_path)).available()
    assert not ok
    assert str(tmp_path / "model.bin") in reason


def test_available_ok_and_main_exe_fallback(tmp_path):
    (tmp_path / "main.exe").write_bytes(b"")
    (tmp_path / "model.bin").write_bytes(b"")
    lw = LocalWhisper(_cfg(tmp_path))
    assert lw.available() == (True, "")
    cmd = lw._build_command(tmp_path / "a.wav", "en")
    assert cmd[0] == str(tmp_path / "main.exe")


def test_build_command(tmp_path):
    wav = tmp_path / "rec-1.wav"
    cmd = LocalWhisper(_cfg(tmp_path, threads=3))._build_command(wav, "hr")
    assert cmd[0] == str(tmp_path / "whisper-cli.exe")
    assert cmd[cmd.index("-m") + 1] == str(tmp_path / "model.bin")
    assert cmd[cmd.index("-f") + 1] == str(wav)
    assert cmd[cmd.index("-of") + 1] == str(tmp_path / "rec-1")
    assert cmd[cmd.index("-t") + 1] == "3"
    assert cmd[cmd.index("-l") + 1] == "hr"
    for flag in ("-nt", "-np", "-otxt"):
        assert flag in cmd


def test_build_command_auto_and_default_threads(tmp_path):
    import os

    cmd = LocalWhisper(_cfg(tmp_path))._build_command(Path(tmp_path / "x.wav"), "auto")
    assert cmd[cmd.index("-l") + 1] == "auto"
    assert cmd[cmd.index("-t") + 1] == str(os.cpu_count())


@pytest.mark.parametrize(
    "raw,expected",
    [
        (" Hello   world.\n", "Hello world."),
        ("[BLANK_AUDIO]", ""),
        ("(blank audio)", ""),
        ("Hello [BLANK_AUDIO] there", "Hello there"),
        ("line one\nline two\n", "line one line two"),
        ("", ""),
    ],
)
def test_clean_output(raw, expected):
    assert _clean_output(raw) == expected


def test_make_transcriber_missing_key(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(TranscriptionError, match="GROQ_API_KEY missing in .env"):
        make_transcriber(TranscriptionConfig(backend="groq"))
    with pytest.raises(TranscriptionError, match="OPENAI_API_KEY missing"):
        make_transcriber(TranscriptionConfig(backend="openai"))


def test_make_transcriber_cloud_ok(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "k")
    t = make_transcriber(TranscriptionConfig(backend="groq"))
    assert t.model == "whisper-large-v3-turbo"


def test_make_transcriber_unknown_backend():
    with pytest.raises(TranscriptionError):
        make_transcriber(TranscriptionConfig(backend="nope"))


def test_make_transcriber_local_unavailable(tmp_path):
    cfg = TranscriptionConfig(backend="local", local=_cfg(tmp_path))
    with pytest.raises(TranscriptionError):
        make_transcriber(cfg)
