"""Download the whisper.cpp engine and ggml models (Python port of scripts/setup-whisper.ps1)."""
from __future__ import annotations

import argparse
import logging
import os
import re
import shutil
import sys
import tempfile
import threading
import zipfile
from pathlib import Path
from typing import Callable

import requests

from vibeflow.config import ROOT

log = logging.getLogger(__name__)

# name -> approximate size in bytes (used for display / progress fallback)
MODELS: dict[str, int] = {
    "tiny": 75_000_000,
    "tiny.en": 75_000_000,
    "base": 142_000_000,
    "base.en": 142_000_000,
    "small": 466_000_000,
    "small.en": 466_000_000,
    "medium": 1_530_000_000,
    "medium.en": 1_530_000_000,
    "large-v3-turbo": 1_620_000_000,
    "large-v3": 3_100_000_000,
}

RELEASES_URL = "https://api.github.com/repos/ggml-org/whisper.cpp/releases?per_page=20"
USER_AGENT = "vibeflow-setup"
CLI_NAMES = ("whisper-cli.exe", "main.exe")
_CPU_PATTERN = re.compile(r"^whisper-bin-x64\.zip$")
_CUDA_PATTERN = re.compile(r"^whisper-cublas-.*-bin-x64\.zip$")
_MIN_MODEL_BYTES = 1024 * 1024
_CHUNK = 256 * 1024

ProgressFn = Callable[[int, "int | None"], None]


class DownloadCancelled(Exception):
    pass


class SetupError(Exception):
    pass


def _check_model(name: str) -> None:
    if name not in MODELS:
        raise ValueError(f"Unknown model '{name}'. Valid: {', '.join(MODELS)}")


def model_filename(name: str) -> str:
    _check_model(name)
    return f"ggml-{name}.bin"


def model_url(name: str) -> str:
    _check_model(name)
    return f"https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-{name}.bin"


def _version_key(name: str) -> tuple[int, ...]:
    m = re.search(r"(\d+(?:\.\d+)+)", name)
    return tuple(int(x) for x in m.group(1).split(".")) if m else (0, 0)


def pick_engine_asset(releases: list[dict], cuda: bool) -> tuple[str, dict]:
    """First non-draft, non-prerelease release carrying the wanted zip; highest version wins."""
    pattern = _CUDA_PATTERN if cuda else _CPU_PATTERN
    for rel in releases:
        if rel.get("draft") or rel.get("prerelease"):
            continue
        candidates = [a for a in rel.get("assets") or [] if pattern.match(a.get("name", ""))]
        if candidates:
            best = max(candidates, key=lambda a: _version_key(a["name"]))
            return rel.get("tag_name", ""), best
    raise SetupError(
        f"No whisper.cpp release with a Windows x64 {'CUDA' if cuda else 'CPU'} build was found. "
        "Download one manually from https://github.com/ggml-org/whisper.cpp/releases and copy "
        "whisper-cli.exe plus all .dll files into the whisper folder."
    )


def engine_installed(dest: Path) -> bool:
    dest = Path(dest)
    return any((dest / n).is_file() for n in CLI_NAMES)


def _stream_to(url: str, target: Path, progress: ProgressFn | None,
               cancel: threading.Event | None, headers: dict | None = None) -> None:
    """Stream url into target (a .part path chosen by the caller). Caller cleans up on error."""
    try:
        resp = requests.get(url, stream=True, timeout=30, headers=headers or {"User-Agent": USER_AGENT})
    except requests.RequestException as e:
        raise SetupError(f"Could not connect to {url}: {e}") from e
    try:
        try:
            resp.raise_for_status()
        except requests.RequestException as e:
            raise SetupError(f"Download failed ({url}): {e}") from e
        length = resp.headers.get("Content-Length")
        total = int(length) if length and str(length).isdigit() else None
        done = 0
        if progress:
            progress(0, total)
        try:
            with open(target, "wb") as fh:
                for chunk in resp.iter_content(chunk_size=_CHUNK):
                    if cancel is not None and cancel.is_set():
                        raise DownloadCancelled()
                    if not chunk:
                        continue
                    fh.write(chunk)
                    done += len(chunk)
                    if progress:
                        progress(done, total)
        except requests.RequestException as e:
            raise SetupError(f"Download interrupted ({url}): {e}") from e
        except OSError as e:
            raise SetupError(f"Could not write {target}: {e}") from e
    finally:
        close = getattr(resp, "close", None)
        if close:
            close()


def _unlink(p: Path) -> None:
    try:
        p.unlink()
    except OSError:
        pass


def download_model(name: str, models_dir: Path, progress: ProgressFn | None = None,
                   cancel: threading.Event | None = None) -> Path:
    url = model_url(name)
    models_dir = Path(models_dir)
    models_dir.mkdir(parents=True, exist_ok=True)
    final = models_dir / model_filename(name)
    if final.is_file() and final.stat().st_size > _MIN_MODEL_BYTES:
        return final
    part = final.with_name(final.name + ".part")
    try:
        _stream_to(url, part, progress, cancel)
        os.replace(part, final)
    except BaseException:
        _unlink(part)
        raise
    return final


def install_engine(dest: Path, cuda: bool = False, progress: ProgressFn | None = None,
                   cancel: threading.Event | None = None) -> str:
    dest = Path(dest)
    try:
        resp = requests.get(RELEASES_URL, timeout=30, headers={
            "User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"})
        resp.raise_for_status()
        releases = resp.json()
    except (requests.RequestException, ValueError) as e:
        raise SetupError(f"Could not query whisper.cpp releases: {e}") from e
    tag, asset = pick_engine_asset(releases, cuda)

    tmp = Path(tempfile.mkdtemp(prefix="vibeflow-whisper-"))
    try:
        zip_path = tmp / asset["name"]
        part = zip_path.with_name(zip_path.name + ".part")
        _stream_to(asset["browser_download_url"], part, progress, cancel)
        os.replace(part, zip_path)
        extract = tmp / "x"
        try:
            with zipfile.ZipFile(zip_path) as zf:
                zf.extractall(extract)
        except zipfile.BadZipFile as e:
            raise SetupError(f"Downloaded engine archive is corrupt: {e}") from e
        cli = None
        for n in CLI_NAMES:
            cli = next(extract.rglob(n), None)
            if cli:
                break
        if cli is None:
            raise SetupError(f"whisper-cli.exe (or main.exe) not found inside {asset['name']}")
        dlls = list(cli.parent.glob("*.dll")) or list(extract.rglob("*.dll"))
        try:
            dest.mkdir(parents=True, exist_ok=True)
            shutil.copy2(cli, dest / cli.name)
            for dll in dlls:
                shutil.copy2(dll, dest / dll.name)
        except OSError as e:
            raise SetupError(f"Could not copy engine files into {dest}: {e}") from e
        return tag
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------- CLI

def _cli_progress(label: str) -> ProgressFn:
    state = {"last": -1}

    def fn(done: int, total: int | None) -> None:
        if total:
            pct = int(done * 100 / total)
            if pct != state["last"] and pct % 10 == 0:
                state["last"] = pct
                print(f"  {label}: {pct}% ({done // 1_000_000} / {total // 1_000_000} MB)", flush=True)
    return fn


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m vibeflow.whisper_setup",
                                 description="Download the whisper.cpp engine and/or ggml models.")
    ap.add_argument("--engine", action="store_true", help="install whisper-cli.exe + DLLs")
    ap.add_argument("--cuda", action="store_true", help="use the CUDA (cuBLAS) engine build")
    ap.add_argument("--models", default="", help="comma separated, e.g. small,medium")
    ap.add_argument("--dest", default=None, help="target folder (default: <app>/whisper)")
    args = ap.parse_args(argv)

    dest = Path(args.dest) if args.dest else ROOT / "whisper"
    models = [m.strip() for m in args.models.split(",") if m.strip()]
    if not args.engine and not models:
        print("nothing to do (use --engine and/or --models)", file=sys.stderr)
        return 1
    try:
        for m in models:
            _check_model(m)
        print(f"Destination: {dest}", flush=True)
        if args.engine:
            print("Installing whisper.cpp engine...", flush=True)
            tag = install_engine(dest, cuda=args.cuda, progress=_cli_progress("engine"))
            print(f"Installed engine {tag}", flush=True)
        for m in models:
            print(f"Model {m}...", flush=True)
            path = download_model(m, dest / "models", progress=_cli_progress(m))
            print(f"  {path}", flush=True)
    except (SetupError, ValueError, DownloadCancelled) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
