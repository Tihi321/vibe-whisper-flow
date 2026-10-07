import threading

import pytest

from vibeflow import whisper_setup as ws


def _rel(tag, names, draft=False, prerelease=False):
    return {"tag_name": tag, "draft": draft, "prerelease": prerelease,
            "assets": [{"name": n, "browser_download_url": f"https://x/{n}", "size": 1} for n in names]}


def test_model_helpers():
    assert ws.model_filename("small") == "ggml-small.bin"
    assert ws.model_url("tiny.en") == \
        "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-tiny.en.bin"
    for fn in (ws.model_filename, ws.model_url):
        with pytest.raises(ValueError):
            fn("nope")
    assert ws.MODELS["large-v3-turbo"] == 1_620_000_000


def test_pick_cpu_skips_prerelease_and_draft():
    rels = [
        _rel("v9", ["whisper-bin-x64.zip"], prerelease=True),
        _rel("v8", ["whisper-bin-x64.zip"], draft=True),
        _rel("v7", ["source.zip"]),
        _rel("v6", ["whisper-bin-Win32.zip", "whisper-bin-x64.zip"]),
    ]
    tag, asset = ws.pick_engine_asset(rels, cuda=False)
    assert tag == "v6" and asset["name"] == "whisper-bin-x64.zip"


def test_pick_cuda_highest_version():
    rels = [_rel("v1", ["whisper-cublas-11.8.0-bin-x64.zip", "whisper-cublas-12.4.0-bin-x64.zip",
                        "whisper-bin-x64.zip"])]
    tag, asset = ws.pick_engine_asset(rels, cuda=True)
    assert asset["name"] == "whisper-cublas-12.4.0-bin-x64.zip"


def test_pick_none_raises():
    with pytest.raises(ws.SetupError):
        ws.pick_engine_asset([_rel("v1", ["whisper-bin-x64.zip"])], cuda=True)
    with pytest.raises(ws.SetupError):
        ws.pick_engine_asset([], cuda=False)


class FakeResp:
    def __init__(self, chunks, length=True):
        self._chunks = chunks
        self.headers = {"Content-Length": str(sum(len(c) for c in chunks))} if length else {}
        self.closed = False

    def raise_for_status(self):
        pass

    def iter_content(self, chunk_size=None):
        yield from self._chunks

    def close(self):
        self.closed = True


def test_download_model_success(monkeypatch, tmp_path):
    chunks = [b"a" * 600_000, b"b" * 600_000, b"c" * 10]
    resp = FakeResp(chunks)
    seen = {}

    def fake_get(url, **kw):
        seen["url"] = url
        return resp

    monkeypatch.setattr(ws.requests, "get", fake_get)
    calls = []
    out = ws.download_model("tiny", tmp_path / "models", progress=lambda d, t: calls.append((d, t)))
    total = sum(len(c) for c in chunks)
    assert out == tmp_path / "models" / "ggml-tiny.bin"
    assert out.stat().st_size == total
    assert not (tmp_path / "models" / "ggml-tiny.bin.part").exists()
    assert calls[-1] == (total, total)
    assert [c[0] for c in calls] == sorted(c[0] for c in calls)
    assert seen["url"] == ws.model_url("tiny")
    assert resp.closed

    # already present (> 1 MB) -> no network
    monkeypatch.setattr(ws.requests, "get", lambda *a, **k: pytest.fail("should not download"))
    assert ws.download_model("tiny", tmp_path / "models") == out


def test_download_model_cancel_removes_part(monkeypatch, tmp_path):
    cancel = threading.Event()

    def gen_chunks():
        yield b"x" * 1000
        cancel.set()
        yield b"y" * 1000

    class R(FakeResp):
        def iter_content(self, chunk_size=None):
            return gen_chunks()

    monkeypatch.setattr(ws.requests, "get", lambda *a, **k: R([b"x"]))
    with pytest.raises(ws.DownloadCancelled):
        ws.download_model("tiny", tmp_path, cancel=cancel)
    assert list(tmp_path.iterdir()) == []


def test_download_model_error_removes_part(monkeypatch, tmp_path):
    class R(FakeResp):
        def iter_content(self, chunk_size=None):
            yield b"x" * 10
            raise ws.requests.ConnectionError("boom")

    monkeypatch.setattr(ws.requests, "get", lambda *a, **k: R([b"x"]))
    with pytest.raises(ws.SetupError):
        ws.download_model("tiny", tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_download_model_unknown(tmp_path):
    with pytest.raises(ValueError):
        ws.download_model("bogus", tmp_path)


def test_engine_installed(tmp_path):
    assert not ws.engine_installed(tmp_path)
    (tmp_path / "main.exe").write_bytes(b"x")
    assert ws.engine_installed(tmp_path)
