"""Configuration: dataclasses, TOML load/save, tiny .env parser."""
from __future__ import annotations

import dataclasses
import logging
import os
import shutil
import tempfile
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import tomli_w

log = logging.getLogger(__name__)

ROOT: Path = Path(__file__).resolve().parent.parent


@dataclass
class HotkeyConfig:
    chord: str = "ctrl+win"
    tap_ms: int = 300
    double_tap_ms: int = 400
    max_recording_seconds: float = 300
    min_recording_seconds: float = 0.6


@dataclass
class AudioConfig:
    device: str = ""
    sample_rate: int = 16000
    silence_threshold: float = 0.01  # peak amplitude (0..1) below which audio counts as silence


@dataclass
class LocalWhisperConfig:
    whisper_cli: str = "whisper/whisper-cli.exe"
    model: str = "whisper/models/ggml-small.bin"
    threads: int = 0


@dataclass
class CloudWhisperConfig:
    groq_model: str = "whisper-large-v3-turbo"
    openai_model: str = "whisper-1"


@dataclass
class TranscriptionConfig:
    backend: str = "local"  # local | groq | openai
    language: str = "auto"
    local: LocalWhisperConfig = field(default_factory=LocalWhisperConfig)
    cloud: CloudWhisperConfig = field(default_factory=CloudWhisperConfig)


@dataclass
class CleanupConfig:
    enabled: bool = True
    provider: str = "lmstudio"  # lmstudio | groq | openai | anthropic | none
    base_url: str = "http://127.0.0.1:1234/v1"
    model: str = ""
    timeout_seconds: float = 20
    prompt: str = ""


@dataclass
class OutputConfig:
    mode: str = "paste"  # paste | type
    restore_clipboard: bool = False
    paste_delay_ms: int = 60


@dataclass
class UiConfig:
    show_pill: bool = True
    pill_position: str = "bottom"  # bottom | top


@dataclass
class Config:
    hotkey: HotkeyConfig = field(default_factory=HotkeyConfig)
    audio: AudioConfig = field(default_factory=AudioConfig)
    transcription: TranscriptionConfig = field(default_factory=TranscriptionConfig)
    cleanup: CleanupConfig = field(default_factory=CleanupConfig)
    output: OutputConfig = field(default_factory=OutputConfig)
    ui: UiConfig = field(default_factory=UiConfig)


def config_path() -> Path:
    env = os.environ.get("VIBEFLOW_CONFIG")
    return Path(env) if env else ROOT / "config.toml"


def resolve_path(p: str | Path) -> Path:
    p = Path(p)
    return p if p.is_absolute() else ROOT / p


# ---------------------------------------------------------------- dict <-> dataclass

def _coerce(value: Any, default: Any) -> Any:
    """Coerce value to the type of default; raise ValueError/TypeError if impossible."""
    if isinstance(default, bool):
        if isinstance(value, bool):
            return value
        raise TypeError("expected a boolean")
    if isinstance(default, int):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TypeError("expected a number")
        if isinstance(value, float) and not value.is_integer():
            raise ValueError("expected an integer")
        return int(value)
    if isinstance(default, float):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TypeError("expected a number")
        return float(value)
    if isinstance(default, str):
        if not isinstance(value, str):
            raise TypeError("expected a string")
        return value
    raise TypeError("unsupported type")


def _build(cls: type, data: Any, where: str) -> Any:
    obj = cls()
    if not isinstance(data, dict):
        if data is not None:
            log.warning("config: [%s] should be a table, using defaults", where)
        return obj
    for f in dataclasses.fields(cls):
        if f.name not in data:
            continue
        current = getattr(obj, f.name)
        if dataclasses.is_dataclass(current):
            setattr(obj, f.name, _build(type(current), data[f.name], f"{where}.{f.name}"))
            continue
        try:
            setattr(obj, f.name, _coerce(data[f.name], current))
        except (TypeError, ValueError) as e:
            log.warning("config: invalid value for %s.%s (%s); using default %r",
                        where, f.name, e, current)
    return obj


def config_from_dict(d: dict) -> Config:
    return _build(Config, d, "config")


def config_to_dict(cfg: Config) -> dict:
    return dataclasses.asdict(cfg)


# ---------------------------------------------------------------- load / save

def load_config(path: Path | None = None) -> Config:
    path = Path(path) if path else config_path()
    if not path.exists():
        example = ROOT / "config.example.toml"
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            if example.exists():
                shutil.copyfile(example, path)
            else:
                save_config(Config(), path)
            log.info("created %s", path)
        except OSError as e:
            log.warning("could not create %s: %s", path, e)
            return Config()
    try:
        with open(path, "rb") as fh:
            data = tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError) as e:
        log.warning("could not read %s (%s); using defaults", path, e)
        return Config()
    return config_from_dict(data)


def save_config(cfg: Config, path: Path | None = None) -> None:
    path = Path(path) if path else config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as fh:
            tomli_w.dump(config_to_dict(cfg), fh)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------- .env

def _parse_env_line(line: str) -> tuple[str, str] | None:
    line = line.strip()
    if not line or line.startswith("#"):
        return None
    if line.startswith("export ") or line.startswith("export\t"):
        line = line[7:].strip()
    if "=" not in line:
        return None
    key, _, value = line.partition("=")
    key = key.strip()
    value = value.strip()
    if not key:
        return None
    if value[:1] in ("'", '"'):
        quote = value[0]
        end = value.find(quote, 1)
        value = value[1:end] if end != -1 else value[1:]
    else:
        # strip inline comment (needs preceding whitespace)
        for i, ch in enumerate(value):
            if ch == "#" and i > 0 and value[i - 1] in " \t":
                value = value[:i].rstrip()
                break
        else:
            if value.startswith("#"):
                value = ""
    return key, value


def load_env(path: Path | None = None) -> dict[str, str]:
    path = Path(path) if path else ROOT / ".env"
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError:
        return {}
    result: dict[str, str] = {}
    for line in text.splitlines():
        parsed = _parse_env_line(line)
        if parsed is None:
            continue
        key, value = parsed
        result[key] = value
        os.environ.setdefault(key, value)
    return result


def get_secret(name: str) -> str | None:
    return os.environ.get(name) or None
