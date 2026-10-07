"""Speech-to-text backends: local whisper.cpp CLI and Groq/OpenAI Whisper APIs."""
from __future__ import annotations

import logging
import os
import re
import subprocess
import threading
from pathlib import Path
from typing import Protocol

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

    def _build_command(self, wav: Path, language: str) -> list[str]:
        wav = Path(wav).resolve()  # cwd is the exe dir, so paths must be absolute
        base = wav.with_suffix("")
        threads = self.cfg.threads or os.cpu_count() or 4
        cmd = [
            str(self._exe()),
            "-m", str(self._model()),
            "-f", str(wav),
            "-nt", "-np",
            "-t", str(threads),
            "-otxt",
            "-of", str(base),
        ]
        cmd += ["-l", language or "auto"]
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


def make_transcriber(cfg: TranscriptionConfig) -> Transcriber:
    backend = (cfg.backend or "").lower()
    if backend == "local":
        t = LocalWhisper(cfg.local)
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
