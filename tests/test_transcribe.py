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


def test_hr_model_auto_forces_hr(tmp_path):
    cfg = _cfg(tmp_path)
    cfg.model = str(tmp_path / "ggml-large-v3-turbo-hr.bin")
    lw = LocalWhisper(cfg)
    wav = tmp_path / "x.wav"
    assert lw._build_command(wav, "auto")[-1] == "hr"
    assert lw._build_command(wav, "")[-1] == "hr"
    cmd = lw._build_command(wav, "en")
    assert cmd[cmd.index("-l") + 1] == "en"
    # other models keep auto
    cmd = LocalWhisper(_cfg(tmp_path))._build_command(wav, "auto")
    assert cmd[cmd.index("-l") + 1] == "auto"


# ---------------------------------------------------------------- parakeet

def _parakeet_cfg(tmp_path, files=True):
    from vibeflow import parakeet
    from vibeflow.config import ParakeetConfig

    d = tmp_path / "pk"
    d.mkdir()
    if files:
        for f in parakeet.PARAKEET_FILES:
            (d / f.name).write_bytes(b"x")
    return ParakeetConfig(model_dir=str(d))


class _FakeSeg:
    def __init__(self, text):
        self.text = text


def _install_fake_onnx_asr(monkeypatch, segments, calls=None, on_recognize=None):
    import sys
    import types

    mod = types.ModuleType("onnx_asr")

    class Adapter:
        def with_vad(self, vad):
            self.vad = vad
            return self

        def recognize(self, wav):
            if calls is not None:
                calls.append(("recognize", wav))
            if on_recognize:
                on_recognize()
            return iter([_FakeSeg(t) for t in segments])

    def load_model(name, path=None, quantization=None, **kw):
        if calls is not None:
            calls.append(("load_model", name, str(path), quantization))
        return Adapter()

    def load_vad(name="silero", path=None, **kw):
        if calls is not None:
            calls.append(("load_vad", name, str(path)))
        return object()

    mod.load_model = load_model
    mod.load_vad = load_vad
    monkeypatch.setitem(sys.modules, "onnx_asr", mod)


def test_make_transcriber_parakeet_missing_files(tmp_path, monkeypatch):
    _install_fake_onnx_asr(monkeypatch, [])
    cfg = TranscriptionConfig(backend="parakeet", parakeet=_parakeet_cfg(tmp_path, files=False))
    with pytest.raises(TranscriptionError, match="Parakeet model not found"):
        make_transcriber(cfg)


def test_make_transcriber_parakeet_no_onnx_asr(tmp_path, monkeypatch):
    import sys

    monkeypatch.setitem(sys.modules, "onnx_asr", None)  # makes `import onnx_asr` fail
    cfg = TranscriptionConfig(backend="parakeet", parakeet=_parakeet_cfg(tmp_path))
    with pytest.raises(TranscriptionError, match="onnx-asr is not available"):
        make_transcriber(cfg)


def test_parakeet_transcribe_joins_segments(tmp_path, monkeypatch):
    calls = []
    _install_fake_onnx_asr(monkeypatch, ["Dobar dan,", " svijete. [BLANK_AUDIO]", "Kako ste?"], calls)
    cfg = TranscriptionConfig(backend="parakeet", parakeet=_parakeet_cfg(tmp_path))
    t = make_transcriber(cfg)
    wav = tmp_path / "a.wav"
    assert t.transcribe(wav, "en") == "Dobar dan, svijete. Kako ste?"  # language is ignored
    kinds = [c[0] for c in calls]
    assert kinds[:2] == ["load_model", "load_vad"] and kinds[-1] == "recognize"
    assert calls[0][1:] == ("nemo-parakeet-tdt-0.6b-v3", cfg.parakeet.model_dir, "int8")
    assert calls[-1][1] == str(wav)


def test_parakeet_cancel_drops_result(tmp_path, monkeypatch):
    from vibeflow.transcribe import TranscriptionCancelled

    holder = {}
    _install_fake_onnx_asr(monkeypatch, ["hello"], on_recognize=lambda: holder["cancel"]())
    cfg = TranscriptionConfig(backend="parakeet", parakeet=_parakeet_cfg(tmp_path))
    t = make_transcriber(cfg)
    holder["cancel"] = t.cancel
    with pytest.raises(TranscriptionCancelled):
        t.transcribe(tmp_path / "a.wav", "auto")
    # the next call is not affected by the earlier cancel
    holder["cancel"] = lambda: None
    assert t.transcribe(tmp_path / "a.wav", "auto") == "hello"


def test_parakeet_load_failure_is_readable(tmp_path, monkeypatch):
    import sys
    import types

    mod = types.ModuleType("onnx_asr")

    def boom(*a, **k):
        raise RuntimeError("bad model")

    mod.load_model = boom
    mod.load_vad = boom
    monkeypatch.setitem(sys.modules, "onnx_asr", mod)
    cfg = TranscriptionConfig(backend="parakeet", parakeet=_parakeet_cfg(tmp_path))
    t = make_transcriber(cfg)
    with pytest.raises(TranscriptionError, match="bad model"):
        t.transcribe(tmp_path / "a.wav", "auto")
