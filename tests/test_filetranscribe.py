import threading
import wave

import pytest

from vibeflow import filetranscribe
from vibeflow.filetranscribe import decode_to_wav, render, transcribe_file
from vibeflow.transcribe import Segment, TranscriptionCancelled, TranscriptionError

SEGS = [Segment(0.0, 1.2345, "Hello world."), Segment(3725.0, 3726.9996, "After an hour.")]


def test_render_plain():
    assert render(SEGS, "plain") == "Hello world. After an hour."


def test_render_timestamps_hour_rollover():
    assert render(SEGS, "timestamps") == "[00:00:00] Hello world.\n[01:02:05] After an hour."


def test_render_srt():
    out = render(SEGS, "srt")
    assert out == ("1\n00:00:00,000 --> 00:00:01,235\nHello world.\n\n"
                   "2\n01:02:05,000 --> 01:02:07,000\nAfter an hour.\n")


def test_render_vtt():
    out = render(SEGS, "vtt")
    assert out.startswith("WEBVTT\n\n")
    assert "00:00:00.000 --> 00:00:01.235\nHello world." in out
    assert "01:02:05.000 --> 01:02:07.000" in out and "," not in out.split("\n\n", 1)[1].split("\n")[0]


@pytest.mark.parametrize("fmt", filetranscribe.FORMATS)
def test_render_empty(fmt):
    out = render([], fmt)
    assert out == ("WEBVTT\n\n" if fmt == "vtt" else "")


def test_render_unknown_format():
    with pytest.raises(ValueError):
        render(SEGS, "doc")


def test_format_tables_consistent():
    assert set(filetranscribe.FORMAT_LABELS) == set(filetranscribe.FORMATS) == set(filetranscribe.FORMAT_EXT)


def _stereo_wav(path, seconds=2.0, rate=44100):
    np = pytest.importorskip("numpy")
    t = np.arange(int(seconds * rate)) / rate
    tone = (np.sin(2 * np.pi * 440 * t) * 12000).astype(np.int16)
    data = np.column_stack([tone, tone // 2]).tobytes()
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(data)


def test_decode_to_wav_resamples(tmp_path):
    pytest.importorskip("av")
    src, dst = tmp_path / "in.wav", tmp_path / "out.wav"
    _stereo_wav(src, 2.0)
    prog = []
    dur = decode_to_wav(src, dst, prog.append, threading.Event())
    assert dur == pytest.approx(2.0, abs=0.05)
    with wave.open(str(dst), "rb") as w:
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate()) == (1, 2, 16000)
        assert w.getnframes() / 16000 == pytest.approx(dur)
    assert prog and prog[-1] == 1.0


def test_decode_non_media_is_readable(tmp_path):
    pytest.importorskip("av")
    src = tmp_path / "notes.txt"
    src.write_text("this is not audio")
    with pytest.raises(TranscriptionError, match="notes.txt"):
        decode_to_wav(src, tmp_path / "out.wav")
    assert not (tmp_path / "out.wav").exists()


def test_decode_no_audio_track(tmp_path):
    av = pytest.importorskip("av")
    src = tmp_path / "silent.mp4"
    try:
        with av.open(str(src), "w") as c:
            s = c.add_stream("mpeg4", rate=10)
            s.width, s.height, s.pix_fmt = 64, 64, "yuv420p"
            frame = av.VideoFrame(64, 64, "yuv420p")
            for pkt in s.encode(frame):
                c.mux(pkt)
            for pkt in s.encode():
                c.mux(pkt)
    except Exception as exc:  # encoder missing in this build
        pytest.skip(f"cannot create a video-only file: {exc}")
    with pytest.raises(TranscriptionError, match="No audio track in silent.mp4"):
        decode_to_wav(src, tmp_path / "out.wav")


def test_decode_cancel(tmp_path):
    pytest.importorskip("av")
    src, dst = tmp_path / "in.wav", tmp_path / "out.wav"
    _stereo_wav(src, 2.0)
    ev = threading.Event()
    ev.set()
    with pytest.raises(TranscriptionCancelled):
        decode_to_wav(src, dst, None, ev)
    assert not dst.exists()


def test_transcribe_file_stages_and_cleanup(tmp_path):
    pytest.importorskip("av")
    src = tmp_path / "in.wav"
    _stereo_wav(src, 1.0)
    seen = {}

    class Fake:
        def transcribe_segments(self, wav, language, duration, progress, cancel):
            seen.update(wav=wav, language=language, duration=duration, existed=wav.exists())
            progress(0.5)
            return [Segment(0, 1, "ok")]

    stages = []
    out = transcribe_file(src, Fake(), "hr", lambda stage, f: stages.append(stage), threading.Event())
    assert out == [Segment(0, 1, "ok")]
    assert seen["existed"] and seen["language"] == "hr" and seen["duration"] == pytest.approx(1.0, abs=0.05)
    assert not seen["wav"].exists()
    assert stages[0] == "Decoding" and stages[-1] == "Transcribing"
