"""Microphone capture: sounddevice InputStream -> 16-bit mono WAV in %TEMP%."""
from __future__ import annotations

import logging
import tempfile
import threading
import time
import wave
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .config import AudioConfig

log = logging.getLogger(__name__)


class AudioError(Exception):
    pass


@dataclass
class Recording:
    path: Path
    seconds: float
    peak: float = 0.0
    rms: float = 0.0


def _levels(data) -> tuple[float, float]:
    """Return (peak, rms) of an int16 array, each normalised to 0..1."""
    import numpy as np

    if data is None or len(data) == 0:
        return 0.0, 0.0
    x = np.asarray(data, dtype=np.float64)
    peak = float(np.max(np.abs(x))) / 32768.0
    rms = float(np.sqrt(np.mean(x * x))) / 32768.0
    return min(peak, 1.0), min(rms, 1.0)


def _sd():
    try:
        import sounddevice as sd
    except Exception as exc:  # ImportError or PortAudio load failure
        raise AudioError(f"Audio backend unavailable: {exc}") from exc
    return sd


def list_input_devices() -> list[tuple[int, str]]:
    sd = _sd()
    try:
        devices = sd.query_devices()
    except Exception as exc:
        raise AudioError(f"Cannot list audio devices: {exc}") from exc
    out: list[tuple[int, str]] = []
    for idx, dev in enumerate(devices):
        if dev.get("max_input_channels", 0) > 0:
            out.append((idx, str(dev.get("name", ""))))
    return out


def _resolve_device(name: str) -> int | None:
    if not name:
        return None
    needle = name.lower()
    for idx, dev_name in list_input_devices():
        if needle in dev_name.lower():
            return idx
    raise AudioError(f"Input device '{name}' not found")


class Recorder:
    def __init__(self, cfg: AudioConfig):
        self.cfg = cfg
        self._lock = threading.Lock()
        self._frames: list = []
        self._stream = None
        self._started_at = 0.0

    @property
    def is_recording(self) -> bool:
        return self._stream is not None

    def _callback(self, indata, frames, time_info, status) -> None:
        if status:
            log.debug("audio status: %s", status)
        with self._lock:
            self._frames.append(indata.copy())

    def start(self) -> None:
        if self._stream is not None:
            return
        sd = _sd()
        device = _resolve_device(self.cfg.device)
        with self._lock:
            self._frames = []
        try:
            stream = sd.InputStream(
                samplerate=self.cfg.sample_rate,
                channels=1,
                dtype="int16",
                blocksize=0,
                device=device,
                callback=self._callback,
            )
            stream.start()
        except Exception as exc:
            log.warning("Could not open microphone: %s", exc)
            raise AudioError(
                f"No microphone found or input device busy ({exc})"
            ) from exc
        self._stream = stream
        self._started_at = time.monotonic()
        log.debug("recording started (device=%s)", device)

    def _close_stream(self) -> None:
        stream, self._stream = self._stream, None
        if stream is None:
            return
        try:
            stream.stop()
        except Exception:
            log.debug("stream.stop failed", exc_info=True)
        try:
            stream.close()
        except Exception:
            log.debug("stream.close failed", exc_info=True)

    def elapsed(self) -> float:
        if self._stream is None:
            return 0.0
        return time.monotonic() - self._started_at

    def discard(self) -> None:
        self._close_stream()
        with self._lock:
            self._frames = []

    def stop(self) -> Recording:
        if self._stream is None:
            log.debug("stop() while not recording")
            return Recording(path=Path(tempfile.gettempdir()) / "vibeflow" / "none.wav", seconds=0.0)
        self._close_stream()
        import numpy as np

        with self._lock:
            frames, self._frames = self._frames, []
        if frames:
            data = np.concatenate(frames, axis=0).reshape(-1).astype("int16", copy=False)
        else:
            data = np.zeros(0, dtype="int16")
        rate = self.cfg.sample_rate
        out_dir = Path(tempfile.gettempdir()) / "vibeflow"
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
            path = out_dir / f"rec-{datetime.now().strftime('%Y%m%d-%H%M%S')}.wav"
            with wave.open(str(path), "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(rate)
                wf.writeframes(data.tobytes())
        except OSError as exc:
            raise AudioError(f"Could not write recording: {exc}") from exc
        seconds = len(data) / float(rate) if rate else 0.0
        peak, rms = _levels(data)
        log.debug("recording saved: %s (%.2fs, peak=%.4f, rms=%.4f)", path, seconds, peak, rms)
        return Recording(path=path, seconds=seconds, peak=peak, rms=rms)
