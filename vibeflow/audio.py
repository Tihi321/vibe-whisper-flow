"""Microphone capture: sounddevice InputStream -> 16-bit mono WAV in %TEMP%."""
from __future__ import annotations

import logging
import math
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


@dataclass(frozen=True)
class InputDevice:
    index: int
    name: str
    is_default: bool


_MAPPER_NAMES = ("microsoft sound mapper - input", "primary sound capture driver")
_MME_NAME_LEN = 31
# Guards EVERY PortAudio call VibeFlow makes outside stream callbacks: query_devices,
# init/terminate, stream open/start/stop/close. Reentrant: resolve_device -> refresh_devices
# nests inside Recorder.start().
_PA_LOCK = threading.RLock()
_open_streams = 0  # streams opened by VibeFlow and not yet closed


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


def _stream_opened() -> None:
    global _open_streams
    _open_streams += 1


def _stream_closed() -> None:
    global _open_streams
    _open_streams = max(0, _open_streams - 1)


def open_stream_count() -> int:
    with _PA_LOCK:
        return _open_streams


def _query_all(sd) -> list:
    try:
        return list(sd.query_devices())
    except Exception as exc:
        raise AudioError(f"Cannot list audio devices: {exc}") from exc


def _default_input(sd) -> dict | None:
    try:
        dev = sd.query_devices(kind="input")
    except Exception:
        return None
    return dev if isinstance(dev, dict) else None


def _all_inputs(sd) -> list[tuple[int, dict]]:
    return [
        (idx, dev)
        for idx, dev in enumerate(_query_all(sd))
        if dev.get("max_input_channels", 0) > 0
    ]


def input_devices() -> list[InputDevice]:
    """Inputs of PortAudio's default host API, without mapper entries or duplicates."""
    with _PA_LOCK:
        sd = _sd()
        inputs = _all_inputs(sd)
        default = _default_input(sd)
        hostapi = default.get("hostapi") if default else None
        default_idx = default.get("index") if default else None
        if default_idx is None and default is not None:
            try:
                default_idx = int(sd.default.device[0])
            except Exception:
                default_idx = None
        out: list[InputDevice] = []
        seen: set[str] = set()
        for idx, dev in inputs:
            if hostapi is not None and dev.get("hostapi") != hostapi:
                continue
            name = str(dev.get("name", ""))
            key = name.casefold()
            if key in _MAPPER_NAMES or key in seen:
                continue
            seen.add(key)
            out.append(InputDevice(idx, name, idx == default_idx))
        return out


def list_input_devices() -> list[tuple[int, str]]:
    return [(d.index, d.name) for d in input_devices()]


def default_input_name() -> str | None:
    with _PA_LOCK:
        try:
            dev = _default_input(_sd())
        except Exception:
            return None
        name = str(dev.get("name", "")) if dev else ""
        return name or None


def match_device(saved: str, names) -> str | None:
    """Match a saved device name against `names` (D3): exact, MME-truncated prefix, substring."""
    if not saved:
        return None
    needle = saved.casefold()
    names = list(names)
    for n in names:
        if n.casefold() == needle:
            return n
    for n in names:
        if len(n) == _MME_NAME_LEN and needle.startswith(n.casefold()):
            return n
    for n in names:
        if needle in n.casefold():
            return n
    return None


def _mapper_index(sd) -> int | None:
    default = _default_input(sd)
    hostapi = default.get("hostapi") if default else None
    for idx, dev in _all_inputs(sd):
        if str(dev.get("name", "")).casefold() != _MAPPER_NAMES[0]:
            continue
        if hostapi is None or dev.get("hostapi") == hostapi:
            return idx
    return None


def _find_index(sd, name: str) -> int | None:
    devs = input_devices()
    hit = match_device(name, [d.name for d in devs])
    if hit is not None:
        return next(d.index for d in devs if d.name == hit)
    # legacy configs: any host API
    allin = [(i, str(d.get("name", ""))) for i, d in _all_inputs(sd)]
    hit = match_device(name, [n for _, n in allin])
    if hit is not None:
        return next(i for i, n in allin if n == hit)
    return None


def _resolve(name: str, fallback: bool) -> tuple[int | None, bool]:
    """Return (device index or None, fell_back). Caller must hold _PA_LOCK while opening."""
    with _PA_LOCK:
        sd = _sd()
        if not name:
            return _mapper_index(sd), False
        idx = _find_index(sd, name)
        if idx is None:
            refresh_devices()
            idx = _find_index(sd, name)
        if idx is not None:
            return idx, False
        if not fallback:
            raise AudioError(f"{name} is not connected")
        log.warning("Input device '%s' not found, using the Windows default", name)
        return _mapper_index(sd), True


def resolve_device(name: str, *, fallback: bool = False) -> int | None:
    with _PA_LOCK:
        return _resolve(name, fallback)[0]


_resolve_device = resolve_device


def refresh_devices() -> bool:
    """Re-initialise PortAudio so new/removed devices show up. Refused while a stream is open."""
    with _PA_LOCK:
        if _open_streams:
            return False
        try:
            sd = _sd()
            sd._terminate()
            sd._initialize()
        except AudioError:
            raise
        except Exception as exc:
            raise AudioError(f"Could not refresh audio devices: {exc}") from exc
        return True


def write_wav(path: Path, data, rate: int) -> None:
    """Write a mono int16 array as a WAV file."""
    import numpy as np

    arr = np.asarray(data).reshape(-1).astype("int16", copy=False)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(arr.tobytes())


def level_db(peak: float) -> float:
    if peak <= 0:
        return -90.0
    return max(20.0 * math.log10(peak), -90.0)


def meter_percent(peak: float, floor_db: float = -60.0) -> int:
    pct = (level_db(peak) - floor_db) / (0.0 - floor_db) * 100.0
    return int(round(min(100.0, max(0.0, pct))))


def level_verdict(peak: float, threshold: float, silent_for: float = 0.0) -> str:
    if peak < 1e-4 and silent_for >= 1.5:
        return "silent"
    if peak >= 0.99:
        return "clipping"
    if peak < threshold:
        return "quiet"
    return "ok"


class Recorder:
    def __init__(self, cfg: AudioConfig):
        self.cfg = cfg
        self._lock = threading.Lock()
        self._frames: list = []
        self._stream = None
        self._started_at = 0.0
        self.fell_back = False

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
        with self._lock:
            self._frames = []
        with _PA_LOCK:
            device, fell = _resolve(self.cfg.device, fallback=True)
            self.fell_back = bool(self.cfg.device) and fell
            stream = None
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
                if stream is not None:
                    try:
                        stream.close()
                    except Exception:
                        pass
                log.warning("Could not open microphone: %s", exc)
                raise AudioError(
                    f"No microphone found or input device busy ({exc})"
                ) from exc
            _stream_opened()
            self._stream = stream
        self._started_at = time.monotonic()
        log.debug("recording started (device=%s)", device)

    def _close_stream(self) -> None:
        stream, self._stream = self._stream, None
        if stream is None:
            return
        with _PA_LOCK:
            try:
                try:
                    stream.stop()
                except Exception:
                    log.debug("stream.stop failed", exc_info=True)
                try:
                    stream.close()
                except Exception:
                    log.debug("stream.close failed", exc_info=True)
            finally:
                _stream_closed()

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
        path = out_dir / f"rec-{datetime.now().strftime('%Y%m%d-%H%M%S')}.wav"
        try:
            write_wav(path, data, rate)
        except OSError as exc:
            raise AudioError(f"Could not write recording: {exc}") from exc
        seconds = len(data) / float(rate) if rate else 0.0
        peak, rms = _levels(data)
        log.debug("recording saved: %s (%.2fs, peak=%.4f, rms=%.4f)", path, seconds, peak, rms)
        return Recording(path=path, seconds=seconds, peak=peak, rms=rms)


class InputMonitor:
    """One input stream feeding a level meter and (optionally) capturing a clip."""

    def __init__(self, device: str, sample_rate: int = 16000, block_seconds: float = 0.05):
        self.device = device
        self.sample_rate = sample_rate
        self.block_seconds = block_seconds
        self.device_label = device or "Windows default"
        self._stream = None
        self._lock = threading.Lock()
        self._peak = 0.0
        self._rms = 0.0
        self._last = time.monotonic()
        self._capturing = False
        self._frames: list = []

    @property
    def running(self) -> bool:
        return self._stream is not None

    def _callback(self, indata, frames, time_info, status) -> None:
        peak, rms = _levels(indata)
        with self._lock:
            self._peak, self._rms, self._last = peak, rms, time.monotonic()
            if self._capturing:
                self._frames.append(indata.copy())

    def start(self) -> None:
        if self._stream is not None:
            return
        with _PA_LOCK:
            sd = _sd()
            idx, _ = _resolve(self.device, fallback=False)
            if self.device:
                self.device_label = self.device
            else:
                default = default_input_name()
                self.device_label = f"Windows default ({default})" if default else "Windows default"
            stream = None
            try:
                stream = sd.InputStream(
                    samplerate=self.sample_rate,
                    channels=1,
                    dtype="int16",
                    blocksize=int(self.sample_rate * self.block_seconds),
                    device=idx,
                    callback=self._callback,
                )
                with self._lock:
                    self._peak = self._rms = 0.0
                    self._last = time.monotonic()
                stream.start()
            except Exception as exc:
                if stream is not None:
                    try:
                        stream.close()
                    except Exception:
                        pass
                raise AudioError(
                    f"Could not open {self.device_label}: {exc}. "
                    "Is another app using it in exclusive mode?"
                ) from exc
            _stream_opened()
            self._stream = stream

    def stop(self) -> None:
        with _PA_LOCK:
            stream, self._stream = self._stream, None
            if stream is not None:
                try:
                    try:
                        stream.stop()
                    except Exception:
                        log.debug("monitor stop failed", exc_info=True)
                    try:
                        stream.close()
                    except Exception:
                        log.debug("monitor close failed", exc_info=True)
                finally:
                    _stream_closed()
        with self._lock:
            self._capturing = False

    def levels(self) -> tuple[float, float, float]:
        """(peak, rms) of the latest block and seconds since the last callback."""
        with self._lock:
            return self._peak, self._rms, time.monotonic() - self._last

    def begin_capture(self) -> None:
        with self._lock:
            self._frames = []
            self._capturing = True

    def end_capture(self):
        import numpy as np

        with self._lock:
            self._capturing = False
            frames, self._frames = self._frames, []
        if not frames:
            return np.zeros(0, dtype="int16")
        return np.concatenate(frames, axis=0).reshape(-1).astype("int16", copy=False)


def play(data, rate: int, cancel: threading.Event | None = None) -> None:
    """Play a mono int16 clip on the default output. Blocking: call from a worker thread."""
    import numpy as np

    arr = np.asarray(data).reshape(-1).astype("int16", copy=False)
    stream = None
    try:
        with _PA_LOCK:
            sd = _sd()
            stream = sd.OutputStream(samplerate=rate, channels=1, dtype="int16")
            stream.start()
            _stream_opened()
        chunk = max(1, int(rate * 0.1))
        for i in range(0, len(arr), chunk):
            if cancel is not None and cancel.is_set():
                break
            stream.write(arr[i:i + chunk].reshape(-1, 1))
    except AudioError:
        raise
    except Exception as exc:
        raise AudioError(f"Could not play back: {exc}") from exc
    finally:
        if stream is not None:
            with _PA_LOCK:
                try:
                    stream.stop()
                except Exception:
                    pass
                try:
                    stream.close()
                except Exception:
                    pass
                _stream_closed()
