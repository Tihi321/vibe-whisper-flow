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


# ---------------------------------------------------------------- segments (file transcription)

def test_build_command_segments(tmp_path):
    cmd = LocalWhisper(_cfg(tmp_path))._build_command(tmp_path / "a.wav", "en", segments=True)
    assert "-oj" in cmd and "-pp" in cmd
    for flag in ("-nt", "-np", "-otxt"):
        assert flag not in cmd
    assert cmd[cmd.index("-of") + 1] == str(tmp_path / "a")
    assert cmd[cmd.index("-l") + 1] == "en"


def test_segments_from_whisper_json():
    from vibeflow.transcribe import Segment, _segments_from_whisper_json

    data = {"transcription": [
        {"offsets": {"from": 0, "to": 2500}, "text": " Hello there."},
        {"offsets": {"from": 2500, "to": 3000}, "text": " [BLANK_AUDIO]"},
        {"offsets": {"from": 3000, "to": 61_250}, "text": " Bye"},
    ]}
    assert _segments_from_whisper_json(data) == [Segment(0.0, 2.5, "Hello there."),
                                                 Segment(3.0, 61.25, "Bye")]
    assert _segments_from_whisper_json({}) == []


def test_chunk_bounds_short_is_one_chunk():
    import numpy as np

    from vibeflow.transcribe import _chunk_bounds

    assert _chunk_bounds(np.zeros(16000 * 30, dtype=np.int16), 16000) == [(0, 16000 * 30)]


def test_chunk_bounds_cuts_at_quiet_spot():
    import numpy as np

    from vibeflow.transcribe import _chunk_bounds

    rate = 1000
    x = np.full(rate * 100, 1000, dtype=np.int16)
    quiet = 25 * rate  # quiet window 5 s before the 30 s limit
    x[quiet:quiet + rate] = 0
    bounds = _chunk_bounds(x, rate, max_s=30, search_s=10)
    assert bounds[0][0] == 0 and bounds[-1][1] == len(x)
    assert abs(bounds[0][1] - (quiet + rate // 2)) <= rate // 2
    assert all(b - a <= 30 * rate for a, b in bounds)
    assert all(bounds[i][1] == bounds[i + 1][0] for i in range(len(bounds) - 1))


def _write_wav(path, seconds, rate=16000):
    import wave

    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * int(seconds * rate))


class _Resp:
    def __init__(self, status=200, body=None, text=""):
        self.status_code = status
        self._body = body
        self.text = text

    def json(self):
        if self._body is None:
            raise ValueError("no json")
        return self._body


def test_cloud_segments_offsets_applied(tmp_path, monkeypatch):
    import threading

    import requests

    from vibeflow.transcribe import CloudWhisper, Segment

    wav = tmp_path / "a.wav"
    _write_wav(wav, 3)
    monkeypatch.setattr("vibeflow.transcribe._chunk_bounds",
                        lambda s, r, **k: [(0, 16000), (16000, 48000)])
    sent = []

    def post(url, headers=None, files=None, data=None, timeout=None):
        sent.append(data["response_format"])
        return _Resp(200, {"segments": [{"start": 0.5, "end": 1.0, "text": " hi "}]})

    monkeypatch.setattr(requests, "post", post)
    prog = []
    segs = CloudWhisper("groq", "k", "whisper-large-v3").transcribe_segments(
        wav, "auto", 3.0, prog.append, threading.Event())
    assert segs == [Segment(0.5, 1.0, "hi"), Segment(1.5, 2.0, "hi")]
    assert sent == ["verbose_json", "verbose_json"]
    assert prog == [0.5, 1.0]


def test_cloud_segments_json_fallback(tmp_path, monkeypatch):
    import threading

    import requests

    from vibeflow.transcribe import CloudWhisper, Segment

    wav = tmp_path / "a.wav"
    _write_wav(wav, 2)
    sent = []

    def post(url, headers=None, files=None, data=None, timeout=None):
        sent.append(data["response_format"])
        if data["response_format"] == "verbose_json":
            return _Resp(400, text="unsupported")
        return _Resp(200, {"text": " plain text "})

    monkeypatch.setattr(requests, "post", post)
    segs = CloudWhisper("openai", "k", "gpt-4o-transcribe").transcribe_segments(
        wav, "en", 2.0, lambda f: None, threading.Event())
    assert sent == ["verbose_json", "json"]
    assert segs == [Segment(0.0, 2.0, "plain text")]


def test_cloud_segments_cancel(tmp_path, monkeypatch):
    import threading

    import requests

    from vibeflow.transcribe import CloudWhisper, TranscriptionCancelled

    wav = tmp_path / "a.wav"
    _write_wav(wav, 1)
    monkeypatch.setattr(requests, "post", lambda *a, **k: pytest.fail("should not post"))
    ev = threading.Event()
    ev.set()
    with pytest.raises(TranscriptionCancelled):
        CloudWhisper("groq", "k", "m").transcribe_segments(wav, "auto", 1.0, lambda f: None, ev)


class _VadSeg:
    def __init__(self, start, end, text):
        self.start, self.end, self.text = start, end, text


def _parakeet_with_model(tmp_path, monkeypatch, model):
    _install_fake_onnx_asr(monkeypatch, [])
    t = make_transcriber(TranscriptionConfig(backend="parakeet", parakeet=_parakeet_cfg(tmp_path)))
    t._ready.wait()
    t._model = model
    return t


def test_parakeet_segments_progress_and_lock_released(tmp_path, monkeypatch):
    import threading

    from vibeflow.transcribe import Segment

    holder = {}
    lock_free = []

    class Model:
        def recognize(self, wav):
            def gen():
                for seg in (_VadSeg(0, 2, "One."), _VadSeg(2, 5, "[BLANK_AUDIO]"),
                            _VadSeg(5, 10, " Two.")):
                    lock_free.append(not holder["t"]._lock.locked())  # held only inside next()
                    yield seg
            return gen()

    t = _parakeet_with_model(tmp_path, monkeypatch, Model())
    holder["t"] = t
    prog = []
    segs = t.transcribe_segments(tmp_path / "a.wav", "en", 10.0, prog.append, threading.Event())
    assert segs == [Segment(0.0, 2.0, "One."), Segment(5.0, 10.0, "Two.")]
    assert prog == [0.2, 0.5, 1.0]
    assert not t._lock.locked()
    assert lock_free == [False, False, False]  # generator body runs inside the locked next()
    assert t._cancelled is False


def test_parakeet_segments_cancel_between_segments(tmp_path, monkeypatch):
    import threading

    from vibeflow.transcribe import TranscriptionCancelled

    cancel = threading.Event()
    pulled = []

    class Model:
        def recognize(self, wav):
            def gen():
                for i in range(5):
                    pulled.append(i)
                    yield _VadSeg(i, i + 1, f"s{i}")
            return gen()

    t = _parakeet_with_model(tmp_path, monkeypatch, Model())
    with pytest.raises(TranscriptionCancelled):
        t.transcribe_segments(tmp_path / "a.wav", "auto", 5.0,
                              lambda f: cancel.set() if f >= 0.4 else None, cancel)
    assert pulled == [0, 1]
    assert not t._lock.locked() and t._cancelled is False
