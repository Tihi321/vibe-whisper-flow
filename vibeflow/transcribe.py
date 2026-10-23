"""Speech-to-text backends: local whisper.cpp CLI, local Parakeet (onnx-asr) and Groq/OpenAI Whisper APIs."""
from __future__ import annotations

import io
import json
import logging
import os
import re
import subprocess
import threading
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

from .config import (
    CloudWhisperConfig,
    LocalWhisperConfig,
    TranscriptionConfig,
    get_secret,
    resolve_path,
)

log = logging.getLogger(__name__)


class TranscriptionError(Exception):
    pass


class TranscriptionCancelled(TranscriptionError):
    pass


@dataclass
class Segment:
    start: float  # seconds
    end: float
    text: str


ProgressFn = Callable[[float], None]  # fraction 0..1 of the transcription stage


class Transcriber(Protocol):
    def transcribe(self, wav: Path, language: str) -> str: ...

    def cancel(self) -> None: ...


# Bracketed/parenthesised non-speech tokens whisper emits, e.g. [BLANK_AUDIO], (blank audio),
# [MUSIC], (silence), *noise*
_NOISE_RE = re.compile(
    r"[\[\(]\s*(?:blank[_ ]?audio|silence|music|noise|inaudible|no speech)\s*[\]\)]",
    re.IGNORECASE,
)


def _clean_output(text: str) -> str:
    text = _NOISE_RE.sub(" ", text or "")
    return re.sub(r"\s+", " ", text).strip()


class LocalWhisper:
    def __init__(self, cfg: LocalWhisperConfig):
        self.cfg = cfg
        self._lock = threading.Lock()
        self._proc: subprocess.Popen | None = None
        self._cancelled = False

    def _exe(self) -> Path:
        exe = resolve_path(self.cfg.whisper_cli)
        if not exe.exists():
            alt = exe.parent / "main.exe"
            if alt.exists():
                return alt
        return exe

    def _model(self) -> Path:
        return resolve_path(self.cfg.model)

    def available(self) -> tuple[bool, str]:
        exe = self._exe()
        if not exe.exists():
            return False, f"whisper executable not found: {exe} (install it in Settings > Transcription)"
        model = self._model()
        if not model.exists():
            return False, f"whisper model not found: {model} (install it in Settings > Transcription)"
        return True, ""

    def _build_command(self, wav: Path, language: str, segments: bool = False) -> list[str]:
        wav = Path(wav).resolve()  # cwd is the exe dir, so paths must be absolute
        base = wav.with_suffix("")
        threads = self.cfg.threads or os.cpu_count() or 4
        cmd = [
            str(self._exe()),
            "-m", str(self._model()),
            "-f", str(wav),
            "-t", str(threads),
        ]
        if segments:  # JSON with per-segment offsets + progress on stderr
            cmd += ["-oj", "-pp"]
        else:
            cmd += ["-nt", "-np", "-otxt"]
        cmd += ["-of", str(base)]
        language = language or "auto"
        if language == "auto" and "-turbo-hr" in self._model().name:
            language = "hr"  # monolingual fine-tune: auto-detect is unreliable
        cmd += ["-l", language]
        return cmd

    def cancel(self) -> None:
        with self._lock:
            self._cancelled = True
            proc = self._proc
        if proc is not None:
            try:
                proc.kill()
            except Exception:
                log.debug("kill failed", exc_info=True)

    def transcribe(self, wav: Path, language: str) -> str:
        ok, reason = self.available()
        if not ok:
            raise TranscriptionError(reason)
        wav = Path(wav)
        txt = wav.with_suffix(".txt")
        cmd = self._build_command(wav, language)
        log.debug("whisper command: %s", cmd)
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        with self._lock:
            self._cancelled = False
            try:
                proc = subprocess.Popen(
                    cmd,
                    cwd=str(self._exe().parent),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    creationflags=flags,
                )
            except OSError as exc:
                raise TranscriptionError(f"Could not start whisper: {exc}") from exc
            self._proc = proc
        try:
            try:
                out, err = proc.communicate()
            finally:
                with self._lock:
                    self._proc = None
            if self._cancelled:
                raise TranscriptionCancelled("cancelled")
            if proc.returncode != 0:
                tail = (err or out or "").strip()[-500:]
                raise TranscriptionError(f"whisper exited with code {proc.returncode}: {tail}")
            text = None
            if txt.exists():
                try:
                    text = txt.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    text = None
            if text is None:
                text = out or ""
            return _clean_output(text)
        finally:
            try:
                txt.unlink(missing_ok=True)
            except OSError:
                pass

    def transcribe_segments(self, wav: Path, language: str, duration: float,
                            progress: ProgressFn, cancel: threading.Event) -> list[Segment]:
        ok, reason = self.available()
        if not ok:
            raise TranscriptionError(reason)
        wav = Path(wav)
        js = wav.with_suffix(".json")
        cmd = self._build_command(wav, language, segments=True)
        log.debug("whisper command: %s", cmd)
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            proc = subprocess.Popen(
                cmd,
                cwd=str(self._exe().parent),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=flags,
            )
        except OSError as exc:
            raise TranscriptionError(f"Could not start whisper: {exc}") from exc
        tail: list[str] = []

        def read_stderr() -> None:
            for line in proc.stderr:
                m = _PROGRESS_RE.search(line)
                if m:
                    progress(min(int(m.group(1)), 100) / 100)
                else:
                    tail.append(line)
                    del tail[:-20]

        reader = threading.Thread(target=read_stderr, name="vibeflow-whisper-stderr", daemon=True)
        reader.start()
        try:
            while True:
                try:
                    proc.wait(timeout=0.2)
                    break
                except subprocess.TimeoutExpired:
                    pass
                if cancel.is_set():
                    proc.kill()
                    proc.wait()
                    raise TranscriptionCancelled("cancelled")
            reader.join(timeout=2)
            if cancel.is_set():
                raise TranscriptionCancelled("cancelled")
            if proc.returncode != 0:
                msg = "".join(tail).strip()[-500:]
                raise TranscriptionError(f"whisper exited with code {proc.returncode}: {msg}")
            try:
                data = json.loads(js.read_text(encoding="utf-8", errors="replace"))
            except (OSError, ValueError) as exc:
                raise TranscriptionError(f"Could not read whisper output: {exc}") from exc
            return _segments_from_whisper_json(data)
        finally:
            if proc.poll() is None:
                proc.kill()
            try:
                js.unlink(missing_ok=True)
            except OSError:
                pass


_PROGRESS_RE = re.compile(r"progress\s*=\s*(\d+)\s*%")


def _segments_from_whisper_json(data: dict) -> list[Segment]:
    """whisper.cpp -oj output -> segments (offsets in ms); empty/noise-only entries are dropped."""
    out: list[Segment] = []
    for item in data.get("transcription") or []:
        text = _clean_output(item.get("text", ""))
        if not text:
            continue
        off = item.get("offsets") or {}
        out.append(Segment(off.get("from", 0) / 1000.0, off.get("to", 0) / 1000.0, text))
    return out


_CLOUD_URLS = {
    "groq": "https://api.groq.com/openai/v1/audio/transcriptions",
    "openai": "https://api.openai.com/v1/audio/transcriptions",
}


class CloudWhisper:
    def __init__(self, provider: str, api_key: str, model: str, timeout: float = 60):
        if provider not in _CLOUD_URLS:
            raise TranscriptionError(f"Unknown cloud provider '{provider}'")
        self.provider = provider
        self.url = _CLOUD_URLS[provider]
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self._cancelled = False
        self._session = None

    def cancel(self) -> None:
        self._cancelled = True

    def transcribe(self, wav: Path, language: str) -> str:
        import requests

        self._cancelled = False
        wav = Path(wav)
        data = {"model": self.model, "response_format": "json"}
        if language and language != "auto":
            data["language"] = language
        try:
            with open(wav, "rb") as fh:
                resp = requests.post(
                    self.url,
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    files={"file": (wav.name, fh, "audio/wav")},
                    data=data,
                    timeout=self.timeout,
                )
        except (requests.RequestException, OSError) as exc:
            raise TranscriptionError(f"{self.provider} request failed: {exc}") from exc
        if self._cancelled:
            raise TranscriptionCancelled("cancelled")
        if resp.status_code != 200:
            raise TranscriptionError(
                f"{self.provider} returned {resp.status_code}: {(resp.text or '')[:300]}"
            )
        try:
            text = resp.json().get("text", "")
        except ValueError as exc:
            raise TranscriptionError(f"{self.provider} returned invalid JSON") from exc
        return _clean_output(text)

    def _post(self, requests, wav_bytes: bytes, language: str, fmt: str):
        data = {"model": self.model, "response_format": fmt}
        if language and language != "auto":
            data["language"] = language
        try:
            return requests.post(
                self.url,
                headers={"Authorization": f"Bearer {self.api_key}"},
                files={"file": ("chunk.wav", wav_bytes, "audio/wav")},
                data=data,
                timeout=300,
            )
        except (requests.RequestException, OSError) as exc:
            raise TranscriptionError(f"{self.provider} request failed: {exc}") from exc

    def transcribe_segments(self, wav: Path, language: str, duration: float,
                            progress: ProgressFn, cancel: threading.Event) -> list[Segment]:
        import numpy as np
        import requests

        with wave.open(str(wav), "rb") as wf:
            rate = wf.getframerate()
            samples = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16)
        bounds = _chunk_bounds(samples, rate)
        out: list[Segment] = []
        for i, (a, b) in enumerate(bounds):
            if cancel.is_set():
                raise TranscriptionCancelled("cancelled")
            buf = io.BytesIO()
            with wave.open(buf, "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(rate)
                w.writeframes(samples[a:b].tobytes())
            blob = buf.getvalue()
            offset = a / rate
            resp = self._post(requests, blob, language, "verbose_json")
            segs = None
            if resp.status_code == 200:
                try:
                    segs = resp.json().get("segments")
                except ValueError:
                    segs = None
            elif resp.status_code != 400:
                raise TranscriptionError(
                    f"{self.provider} returned {resp.status_code}: {(resp.text or '')[:300]}")
            if segs:
                for s in segs:
                    text = _clean_output(s.get("text", ""))
                    if text:
                        out.append(Segment(offset + float(s.get("start", 0)),
                                           offset + float(s.get("end", 0)), text))
            else:
                log.warning("%s gave no segment timestamps for this model; using chunk-level timing",
                            self.provider)
                resp = self._post(requests, blob, language, "json")
                if resp.status_code != 200:
                    raise TranscriptionError(
                        f"{self.provider} returned {resp.status_code}: {(resp.text or '')[:300]}")
                try:
                    text = _clean_output(resp.json().get("text", ""))
                except ValueError as exc:
                    raise TranscriptionError(f"{self.provider} returned invalid JSON") from exc
                if text:
                    out.append(Segment(offset, b / rate, text))
            progress((i + 1) / len(bounds))
        if cancel.is_set():
            raise TranscriptionCancelled("cancelled")
        return out


def _chunk_bounds(samples, rate: int, max_s: float = 600, search_s: float = 20) -> list[tuple[int, int]]:
    """Split sample indices into chunks <= max_s, cutting at the quietest 0.5 s in the last search_s."""
    import numpy as np

    n = len(samples)
    max_n = int(max_s * rate)
    if n <= max_n:
        return [(0, n)]
    win = int(0.5 * rate)
    out: list[tuple[int, int]] = []
    start = 0
    while n - start > max_n:
        lo = max(start + 1, start + max_n - int(search_s * rate))
        hi = start + max_n
        region = np.abs(samples[lo:hi].astype(np.float32))
        k = len(region) // win
        if k >= 1:
            energy = region[: k * win].reshape(k, win).mean(axis=1)
            cut = lo + int(energy.argmin()) * win + win // 2
        else:
            cut = hi
        out.append((start, cut))
        start = cut
    out.append((start, n))
    return out


def make_transcriber(cfg: TranscriptionConfig) -> Transcriber:
    backend = (cfg.backend or "").lower()
    if backend == "local":
        t = LocalWhisper(cfg.local)
        ok, reason = t.available()
        if not ok:
            raise TranscriptionError(reason)
        return t
    if backend == "parakeet":
        from .parakeet import ParakeetTranscriber  # lazy: onnx-asr is optional at import time

        t = ParakeetTranscriber(cfg.parakeet)
        ok, reason = t.available()
        if not ok:
            raise TranscriptionError(reason)
        return t
    if backend in ("groq", "openai"):
        env = "GROQ_API_KEY" if backend == "groq" else "OPENAI_API_KEY"
        key = get_secret(env)
        if not key:
            raise TranscriptionError(f"{env} missing in .env")
        model = cfg.cloud.groq_model if backend == "groq" else cfg.cloud.openai_model
        return CloudWhisper(backend, key, model)
    raise TranscriptionError(f"Unknown transcription backend '{cfg.backend}'")
