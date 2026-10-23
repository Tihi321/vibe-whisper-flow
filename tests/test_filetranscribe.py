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


def test_lrc_in_format_tables():
    assert filetranscribe.FORMAT_LABELS["lrc"] == "Lyrics (LRC)"
    assert filetranscribe.FORMAT_EXT["lrc"] == ".lrc"


def test_render_lrc_headers_and_lines():
    meta = {"title": "Pjesma", "artist": "Bend", "album": "Album", "length": 185.0}
    segs = [Segment(0.0, 2.0, "Prvi redak"), Segment(3.5, 6.0, "Drugi  redak")]
    out = render(segs, "lrc", meta).split("\n")
    assert out[:5] == ["[ti:Pjesma]", "[ar:Bend]", "[al:Album]", "[length:03:05]", "[re:VibeFlow]"]
    assert out[5:7] == ["[00:00.00] Prvi redak", "[00:03.50] Drugi redak"]
    # the known length (185 s) is far past the last segment, so it gets an end marker too
    assert out[7] == "[00:06.00]" and len(out) == 8


def test_render_lrc_without_meta_omits_unknown_tags():
    out = render([Segment(1.0, 2.0, "Hi")], "lrc").split("\n")
    assert out == ["[length:00:02]", "[re:VibeFlow]", "[00:01.00] Hi"]
    out = render([Segment(1.0, 2.0, "Hi")], "lrc", {"title": "T", "artist": "", "album": ""}).split("\n")
    assert out[0] == "[ti:T]" and not any(line.startswith(("[ar:", "[al:")) for line in out)


def test_render_lrc_centisecond_rounding_and_carry():
    out = render([Segment(59.996, 61.234, "a"), Segment(61.235, 62.0, "b")], "lrc").split("\n")
    assert "[01:00.00] a" in out  # 59.996 rounds up into the next minute
    assert "[01:01.24] b" in out  # 61.235 -> 61.24 (no float drift)
    assert render([Segment(3599.999, 3601.0, "x")], "lrc").split("\n")[-1] == "[60:00.00] x"


def test_render_lrc_gap_markers():
    segs = [Segment(0.0, 2.0, "a"), Segment(6.5, 8.0, "b"), Segment(12.0, 14.0, "c")]
    out = render(segs, "lrc").split("\n")[2:]
    # 4.5 s gap gets a marker (a -> b), exactly 4 s does not (b -> c); the last end needs a known length
    assert out == ["[00:00.00] a", "[00:02.00]", "[00:06.50] b", "[00:12.00] c"]
    out = render(segs, "lrc", {"length": 30.0}).split("\n")
    assert out[-1] == "[00:14.00]"


def test_render_lrc_trailing_marker_when_length_past_last_end():
    segs = [Segment(0.0, 2.0, "a")]
    assert render(segs, "lrc", {"length": 6.5}).split("\n")[-2:] == ["[00:00.00] a", "[00:02.00]"]
    assert render(segs, "lrc", {"length": 6.0}).split("\n")[-1] == "[00:00.00] a"  # exactly 4 s: none


def test_render_lrc_empty():
    assert render([], "lrc", {"title": "T"}) == ""


def test_output_path_next_to_source(tmp_path):
    src = tmp_path / "songs" / "Moja pjesma.v2.mp3"
    assert filetranscribe.output_path(src, "lrc") == tmp_path / "songs" / "Moja pjesma.v2.lrc"
    assert filetranscribe.output_path(src, "srt").name == "Moja pjesma.v2.srt"


def test_read_tags_non_media_falls_back_to_stem(tmp_path):
    bad = tmp_path / "Some Song.mp3"
    bad.write_bytes(b"not audio at all")
    assert filetranscribe.read_tags(bad) == {"title": "Some Song", "artist": "", "album": ""}
    assert filetranscribe.read_tags(tmp_path / "missing.mp3")["title"] == "missing"


def test_read_tags_without_tags_and_with_tags(tmp_path):
    plain = tmp_path / "untagged.wav"
    _stereo_wav(plain)
    assert filetranscribe.read_tags(plain) == {"title": "untagged", "artist": "", "album": "",
                                              "length": pytest.approx(2.0, abs=0.05)}

    av = pytest.importorskip("av")
    tagged = tmp_path / "tagged.mka"
    with av.open(str(tagged), "w") as out:
        out.metadata["TITLE"] = "Naslov"
        out.metadata["Artist"] = " Izvodjac "
        stream = out.add_stream("pcm_s16le", rate=8000)
        stream.layout = "mono"
        frame = av.AudioFrame(format="s16", layout="mono", samples=800)
        frame.sample_rate = 8000
        for plane in frame.planes:
            plane.update(bytes(plane.buffer_size))
        for packet in stream.encode(frame):
            out.mux(packet)
        for packet in stream.encode(None):
            out.mux(packet)
    assert filetranscribe.read_tags(tagged) == {"title": "Naslov", "artist": "Izvodjac", "album": "",
                                              "length": pytest.approx(0.1, abs=0.05)}
