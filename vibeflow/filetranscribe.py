"""Transcribe audio/video files: PyAV decoding to 16 kHz mono WAV, backend orchestration, text/SRT/VTT/LRC renderers."""
from __future__ import annotations

import logging
import os
import tempfile
import threading
import time
import wave
from pathlib import Path
from typing import Callable

from .transcribe import Segment, Transcriber, TranscriptionCancelled, TranscriptionError

log = logging.getLogger(__name__)

FORMATS = ("plain", "timestamps", "srt", "vtt", "lrc")
FORMAT_LABELS = {
    "plain": "Plain text",
    "timestamps": "Text with timestamps",
    "srt": "SRT subtitles",
    "vtt": "VTT subtitles",
    "lrc": "Lyrics (LRC)",
}
FORMAT_EXT = {"plain": ".txt", "timestamps": ".txt", "srt": ".srt", "vtt": ".vtt", "lrc": ".lrc"}
MEDIA_PATTERNS = ("*.mp3", "*.wav", "*.m4a", "*.aac", "*.flac", "*.ogg", "*.opus", "*.wma",
                  "*.mp4", "*.mkv", "*.mov", "*.avi", "*.webm")

_RATE = 16000
_LRC_GAP = 4.0  # seconds of silence after which an LRC line is closed with an empty timed line

# (stage name, fraction 0..1 or None when unknown)
StageFn = Callable[[str, "float | None"], None]


def _av_import_error() -> str | None:
    try:
        import av  # noqa: F401
    except Exception as exc:  # ImportError, or a broken FFmpeg DLL
        return f"{type(exc).__name__}: {exc}"
    return None


def decode_to_wav(src: Path, dst: Path, progress: Callable[[float], None] | None = None,
                  cancel: threading.Event | None = None) -> float:
    """Decode the first audio track of `src` to 16 kHz mono s16 WAV at `dst`; returns seconds."""
    src, dst = Path(src), Path(dst)
    err = _av_import_error()
    if err:
        raise TranscriptionError(
            f"Audio decoding (PyAV) is not available ({err}); install it with pip install av")
    import av

    try:
        container = av.open(str(src))
    except Exception as exc:  # noqa: BLE001  (av.error.*, OSError)
        raise TranscriptionError(f"Could not open {src.name} as audio/video: {exc}") from exc
    frames_written = 0
    try:
        stream = next((s for s in container.streams if s.type == "audio"), None)
        if stream is None:
            raise TranscriptionError(f"No audio track in {src.name}")
        total = (container.duration / av.time_base) if container.duration else 0.0
        resampler = av.AudioResampler(format="s16", layout="mono", rate=_RATE)
        dst.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(dst), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(_RATE)

            def write(frames) -> None:
                nonlocal frames_written
                for f in frames or []:
                    data = bytes(f.planes[0])[: f.samples * 2]
                    wf.writeframes(data)
                    frames_written += f.samples

            try:
                for frame in container.decode(stream):
                    if cancel is not None and cancel.is_set():
                        raise TranscriptionCancelled("cancelled")
                    write(resampler.resample(frame))
                    if progress and total > 0 and frame.time is not None:
                        progress(min(max(frame.time / total, 0.0), 1.0))
                write(resampler.resample(None))  # flush
            except (TranscriptionError, OSError):
                raise
            except Exception as exc:  # noqa: BLE001
                raise TranscriptionError(f"Could not decode {src.name}: {exc}") from exc
    except BaseException:
        try:
            dst.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    finally:
        container.close()
    if frames_written == 0:
        try:
            dst.unlink(missing_ok=True)
        except OSError:
            pass
        raise TranscriptionError(f"No audio could be decoded from {src.name}")
    if progress:
        progress(1.0)
    return frames_written / _RATE


def transcribe_file(src: Path, transcriber: Transcriber, language: str, progress: StageFn,
                    cancel: threading.Event) -> list[Segment]:
    """Decode `src` to a temp WAV, then run the backend's transcribe_segments over it."""
    tmp_dir = Path(tempfile.gettempdir()) / "vibeflow"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    wav = tmp_dir / f"file-{int(time.time() * 1000)}-{os.getpid()}.wav"
    try:
        progress("Decoding", 0.0)
        duration = decode_to_wav(src, wav, lambda f: progress("Decoding", f), cancel)
        if cancel.is_set():
            raise TranscriptionCancelled("cancelled")
        progress("Transcribing", 0.0)
        return transcriber.transcribe_segments(
            wav, language, duration, lambda f: progress("Transcribing", f), cancel)
    finally:
        try:
            wav.unlink(missing_ok=True)
        except OSError:
            log.debug("could not remove %s", wav, exc_info=True)


def _split(seconds: float) -> tuple[int, int, int, int]:
    ms = max(0, int(round(seconds * 1000 + 1e-6)))
    h, rem = divmod(ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return h, m, s, ms


def _stamp(seconds: float, sep: str) -> str:
    h, m, s, ms = _split(seconds)
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}"


def _lrc_stamp(seconds: float) -> str:
    cs = max(0, int(round(seconds * 100 + 1e-6)))  # round once so 59.996 carries into the minute
    m, rem = divmod(cs, 6000)
    s, cs = divmod(rem, 100)
    return f"[{m:02d}:{s:02d}.{cs:02d}]"


def _render_lrc(segments: list[Segment], meta: dict | None) -> str:
    if not segments:
        return ""
    meta = meta or {}
    lines = []
    for tag, key in (("ti", "title"), ("ar", "artist"), ("al", "album")):
        value = " ".join(str(meta.get(key) or "").split())
        if value:
            lines.append(f"[{tag}:{value}]")
    length = meta.get("length") or segments[-1].end
    total = int(round(length))
    lines.append(f"[length:{total // 60:02d}:{total % 60:02d}]")
    lines.append("[re:VibeFlow]")
    for i, s in enumerate(segments):
        lines.append(f"{_lrc_stamp(s.start)} {' '.join(s.text.split())}".rstrip())
        # Spodrive shows a music note for empty timed lines, so mark long instrumental gaps
        nxt = segments[i + 1].start if i + 1 < len(segments) else meta.get("length")
        if nxt is not None and nxt - s.end > _LRC_GAP:
            lines.append(_lrc_stamp(s.end))
    return "\n".join(lines)


def output_path(src: Path, fmt: str) -> Path:
    """`<source stem><ext>` next to the source file (used for batch / save-next-to-source)."""
    src = Path(src)
    return src.with_name(src.stem + FORMAT_EXT[fmt])


def read_tags(src: Path) -> dict:
    """Title/artist/album (+ `length` in seconds when known) from the container; title falls back to the stem. Never raises."""
    src = Path(src)
    tags = {"title": src.stem, "artist": "", "album": ""}
    if _av_import_error():
        return tags
    try:
        import av

        with av.open(str(src)) as container:
            found = {str(k).lower(): str(v).strip() for k, v in dict(container.metadata).items()}
            if container.duration:
                tags["length"] = container.duration / av.time_base
    except Exception:  # noqa: BLE001  (not media, unreadable, broken FFmpeg)
        log.debug("could not read tags from %s", src, exc_info=True)
        return tags
    for key in tags:
        if found.get(key):
            tags[key] = found[key]
    return tags


def render(segments: list[Segment], fmt: str, meta: dict | None = None) -> str:
    if fmt == "lrc":
        return _render_lrc(segments, meta)
    if fmt == "plain":
        return " ".join(s.text for s in segments)
    if fmt == "timestamps":
        lines = []
        for s in segments:
            h, m, sec, _ = _split(s.start)
            lines.append(f"[{h:02d}:{m:02d}:{sec:02d}] {s.text}")
        return "\n".join(lines)
    if fmt == "srt":
        blocks = [f"{i}\n{_stamp(s.start, ',')} --> {_stamp(s.end, ',')}\n{s.text}\n"
                  for i, s in enumerate(segments, 1)]
        return "\n".join(blocks)
    if fmt == "vtt":
        blocks = [f"{_stamp(s.start, '.')} --> {_stamp(s.end, '.')}\n{s.text}\n" for s in segments]
        return "WEBVTT\n\n" + "\n".join(blocks)
    raise ValueError(f"Unknown format '{fmt}'")
