"""NVIDIA Parakeet-TDT-0.6B-v3 backend via onnx-asr (local, CPU, 25 European languages incl. Croatian)."""
from __future__ import annotations

import hashlib
import logging
import os
import shutil
import threading
from pathlib import Path
from typing import Any, NamedTuple

from . import whisper_setup
from .config import ParakeetConfig, resolve_path
from .transcribe import TranscriptionCancelled, TranscriptionError, _clean_output

log = logging.getLogger(__name__)

MODEL_NAME = "parakeet-tdt-0.6b-v3"
_ONNX_ASR_MODEL = "nemo-parakeet-tdt-0.6b-v3"
_QUANTIZATION = "int8"


class _File(NamedTuple):
    name: str
    url: str
    size: int
    sha256: str


_PARAKEET_BASE = ("https://huggingface.co/istupakov/parakeet-tdt-0.6b-v3-onnx/resolve/"
                  "8f23f0c03c8761650bdb5b40aaf3e40d2c15f1ce/")
_VAD_BASE = ("https://huggingface.co/istupakov/silero-vad-onnx/resolve/"
             "b3e3ee3cce4c11ceb63b1a0b229d916069c1ddf6/")

# int8 set that onnx-asr loads for nemo-conformer-tdt, plus the Silero VAD used to split long
# recordings (Parakeet handles about 20-30 s per pass). All files live in one folder.
PARAKEET_FILES: tuple[_File, ...] = (
    _File("encoder-model.int8.onnx", _PARAKEET_BASE + "encoder-model.int8.onnx", 652_183_999,
          "6139d2fa7e1b086097b277c7149725edbab89cc7c7ae64b23c741be4055aff09"),
    _File("decoder_joint-model.int8.onnx", _PARAKEET_BASE + "decoder_joint-model.int8.onnx",
          18_202_004, "eea7483ee3d1a30375daedc8ed83e3960c91b098812127a0d99d1c8977667a70"),
    _File("nemo128.onnx", _PARAKEET_BASE + "nemo128.onnx", 139_764,
          "a9fde1486ebfcc08f328d75ad4610c67835fea58c73ba57e3209a6f6cf019e9f"),
    _File("vocab.txt", _PARAKEET_BASE + "vocab.txt", 93_939,
          "d58544679ea4bc6ac563d1f545eb7d474bd6cfa467f0a6e2c1dc1c7d37e3c35d"),
    _File("config.json", _PARAKEET_BASE + "config.json", 97,
          "666903c76b9798caf2c210afd4f6cd60b08a8dbf9800ec8d7a3bc0d2148ac466"),
    _File("silero_vad.onnx", _VAD_BASE + "silero_vad.onnx", 2_327_524,
          "1a153a22f4509e292a94e67d6f9b85e8deb25b4988682b7e174c65279d8788e3"),
)
TOTAL_BYTES = sum(f.size for f in PARAKEET_FILES)


def installed(dest_dir: Path) -> bool:
    dest_dir = Path(dest_dir)
    return all((dest_dir / f.name).is_file() for f in PARAKEET_FILES)


def download(dest_dir: Path, progress: whisper_setup.ProgressFn | None = None,
             cancel: threading.Event | None = None) -> Path:
    """Fetch every missing file (sha256 verified, .part + os.replace); progress covers all files."""
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    base = 0  # bytes of the files finished so far
    for f in PARAKEET_FILES:
        final = dest_dir / f.name
        if final.is_file():
            base += f.size
            if progress:
                progress(base, TOTAL_BYTES)
            continue
        part = final.with_name(final.name + ".part")
        hasher = hashlib.sha256()

        def cb(done: int, total: int | None, _base: int = base) -> None:
            if progress:
                progress(_base + done, TOTAL_BYTES)

        try:
            whisper_setup._stream_to(f.url, part, cb, cancel, hasher=hasher)
            if hasher.hexdigest() != f.sha256:
                raise whisper_setup.SetupError(
                    f"Checksum mismatch for {f.name}: expected {f.sha256}, got "
                    f"{hasher.hexdigest()}. The download was discarded.")
            os.replace(part, final)
        except BaseException:
            whisper_setup._unlink(part)
            raise
        base += f.size
    return dest_dir


def delete(dest_dir: Path) -> None:
    shutil.rmtree(Path(dest_dir), ignore_errors=False)


def _onnx_asr_import_error() -> str | None:
    try:
        import onnx_asr  # noqa: F401
    except Exception as exc:  # ImportError, or a broken onnxruntime DLL
        return f"{type(exc).__name__}: {exc}"
    return None


class ParakeetTranscriber:
    def __init__(self, cfg: ParakeetConfig):
        self.cfg = cfg
        self._cancelled = False
        self._lock = threading.Lock()
        self._model: Any = None
        self._load_error: str | None = None
        self._ready = threading.Event()
        # Loading takes seconds: start it now so the first hotkey press doesn't pay for it.
        if self.available()[0]:
            threading.Thread(target=self._load, name="vibeflow-parakeet-load", daemon=True).start()
        else:
            self._ready.set()

    def _dir(self) -> Path:
        return resolve_path(self.cfg.model_dir)

    def available(self) -> tuple[bool, str]:
        err = _onnx_asr_import_error()
        if err:
            return False, (f"onnx-asr is not available ({err}); install it with "
                           "pip install onnx-asr[cpu]")
        d = self._dir()
        if not installed(d):
            return False, f"Parakeet model not found: {d} (download it in Settings > Transcription)"
        return True, ""

    def _load(self) -> None:
        try:
            import onnx_asr

            d = self._dir()
            kwargs: dict[str, Any] = {}
            if self.cfg.threads > 0:
                import onnxruntime as rt

                so = rt.SessionOptions()
                so.intra_op_num_threads = self.cfg.threads
                kwargs["sess_options"] = so
            asr = onnx_asr.load_model(_ONNX_ASR_MODEL, path=d, quantization=_QUANTIZATION, **kwargs)
            vad = onnx_asr.load_vad("silero", path=d, **kwargs)
            self._model = asr.with_vad(vad)
            log.info("parakeet model loaded from %s", d)
        except Exception as exc:  # noqa: BLE001
            log.exception("parakeet load failed")
            self._load_error = f"Could not load the Parakeet model: {exc}"
        finally:
            self._ready.set()

    def cancel(self) -> None:
        self._cancelled = True

    def transcribe(self, wav: Path, language: str) -> str:
        # `language` is ignored: Parakeet detects it itself.
        ok, reason = self.available()
        if not ok:
            raise TranscriptionError(reason)
        with self._lock:
            self._cancelled = False
            self._ready.wait()
            if self._model is None:
                raise TranscriptionError(self._load_error or "Parakeet model is not loaded")
            try:
                segments = self._model.recognize(str(Path(wav)))
                text = " ".join(seg.text for seg in segments)
            except Exception as exc:  # noqa: BLE001
                if self._cancelled:
                    raise TranscriptionCancelled("cancelled") from exc
                raise TranscriptionError(f"Parakeet transcription failed: {exc}") from exc
            if self._cancelled:  # inference can't be interrupted; drop the result
                raise TranscriptionCancelled("cancelled")
            return _clean_output(text)
