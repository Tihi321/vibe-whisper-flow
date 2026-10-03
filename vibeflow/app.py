"""VibeFlow application: dictation state machine, pipeline worker and wiring."""
from __future__ import annotations

import argparse
import logging
import os
import sys
import threading
import time
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Protocol

from . import __version__
from .config import (
    ROOT,
    Config,
    HotkeyConfig,
    config_path,
    load_config,
    load_env,
    resolve_path,
    save_config,
)
from .logging_setup import setup_logging

log = logging.getLogger(__name__)


# ---------------------------------------------------------------- state machine

class State(str, Enum):
    IDLE = "IDLE"
    REC_HOLD = "REC_HOLD"
    REC_TAPWAIT = "REC_TAPWAIT"
    REC_HANDSFREE = "REC_HANDSFREE"
    PROCESSING = "PROCESSING"


class Mode(str, Enum):
    HOLD = "HOLD"
    HANDSFREE = "HANDSFREE"


class MachineActions(Protocol):
    def start_recording(self) -> bool: ...
    def stop_and_process(self, mode: Mode) -> None: ...
    def discard_recording(self) -> None: ...
    def cancel_processing(self) -> None: ...
    def state_changed(self, state: State, mode: Mode | None) -> None: ...


Timer = Callable[[float, Callable[[], None]], Any]

_REC_STATES = (State.REC_HOLD, State.REC_TAPWAIT, State.REC_HANDSFREE)


def _default_schedule(delay: float, fn: Callable[[], None]) -> Any:
    t = threading.Timer(delay, fn)
    t.daemon = True
    t.start()
    return t


def _default_cancel(handle: Any) -> None:
    handle.cancel()


class DictationMachine:
    """Pure logic for hold / tap / double-tap / Esc handling. Thread-safe."""

    def __init__(self, cfg: HotkeyConfig, actions: MachineActions,
                 clock: Callable[[], float] = time.monotonic,
                 schedule: Timer = _default_schedule,
                 cancel: Callable[[Any], None] = _default_cancel):
        self.cfg = cfg
        self._actions = actions
        self._clock = clock
        self._schedule = schedule
        self._cancel = cancel
        self._lock = threading.RLock()
        self.state: State = State.IDLE
        self.mode: Mode | None = None
        self.enabled: bool = True
        self._down_at = 0.0
        self._rec_start = 0.0
        self._ignore_up = False
        self._dt_timer: Any = None
        self._max_timer: Any = None
        self._token = 0  # invalidates stale timer callbacks

    # -- helpers (call with lock held)
    def _set(self, state: State, mode: Mode | None) -> None:
        self.state = state
        self.mode = mode
        self._actions.state_changed(state, mode)

    def _cancel_timers(self) -> None:
        self._token += 1
        for h in (self._dt_timer, self._max_timer):
            if h is not None:
                try:
                    self._cancel(h)
                except Exception:
                    log.exception("timer cancel failed")
        self._dt_timer = None
        self._max_timer = None

    def _arm_max(self) -> None:
        token = self._token
        self._max_timer = self._schedule(
            float(self.cfg.max_recording_seconds), lambda: self._on_max(token))

    def _process(self, mode: Mode) -> None:
        self._cancel_timers()
        self._set(State.PROCESSING, mode)
        self._actions.stop_and_process(mode)

    def _discard(self) -> None:
        self._cancel_timers()
        self._actions.discard_recording()
        self._set(State.IDLE, None)

    # -- public API
    def recording_seconds(self) -> float:
        with self._lock:
            if self.state in _REC_STATES:
                return max(0.0, self._clock() - self._rec_start)
            return 0.0

    def chord_down(self) -> None:
        with self._lock:
            if not self.enabled or self.state == State.PROCESSING:
                return
            if self.state == State.IDLE:
                self._ignore_up = False
                if not self._actions.start_recording():
                    return
                now = self._clock()
                self._down_at = now
                self._rec_start = now
                self._cancel_timers()
                self._arm_max()
                self._set(State.REC_HOLD, Mode.HOLD)
            elif self.state == State.REC_TAPWAIT:
                if self._dt_timer is not None:
                    self._cancel(self._dt_timer)
                    self._dt_timer = None
                self._ignore_up = True
                self._set(State.REC_HANDSFREE, Mode.HANDSFREE)
            elif self.state == State.REC_HANDSFREE:
                self._ignore_up = True
                self._process(Mode.HANDSFREE)
            # REC_HOLD: repeat, ignored

    def chord_up(self) -> None:
        with self._lock:
            if self._ignore_up:
                self._ignore_up = False
                return
            if not self.enabled or self.state != State.REC_HOLD:
                return
            held = self._clock() - self._down_at
            if held >= self.cfg.tap_ms / 1000.0:
                self._process(Mode.HOLD)
            else:
                token = self._token
                self._dt_timer = self._schedule(
                    self.cfg.double_tap_ms / 1000.0, lambda: self._on_double_tap_timeout(token))
                self._set(State.REC_TAPWAIT, Mode.HOLD)

    def escape(self) -> None:
        with self._lock:
            if self.state in _REC_STATES:
                self._discard()
            elif self.state == State.PROCESSING:
                self._actions.cancel_processing()
                self._set(State.IDLE, None)

    def processing_done(self) -> None:
        with self._lock:
            if self.state == State.PROCESSING:
                self._set(State.IDLE, None)

    def set_enabled(self, enabled: bool) -> None:
        with self._lock:
            self.enabled = bool(enabled)
            if not self.enabled and self.state in _REC_STATES:
                self._discard()

    # -- timers
    def _on_double_tap_timeout(self, token: int) -> None:
        with self._lock:
            if token != self._token or self.state != State.REC_TAPWAIT:
                return
            self._dt_timer = None
            if self.recording_seconds() >= self.cfg.min_recording_seconds:
                self._process(Mode.HOLD)
            else:
                self._discard()

    def _on_max(self, token: int) -> None:
        with self._lock:
            if token != self._token or self.state not in _REC_STATES:
                return
            self._max_timer = None
            mode = Mode.HANDSFREE if self.state == State.REC_HANDSFREE else Mode.HOLD
            self._process(mode)


# ---------------------------------------------------------------- application

def _short(msg: str, limit: int = 60) -> str:
    msg = " ".join(str(msg).split())
    return msg if len(msg) <= limit else msg[: limit - 1] + "…"


def is_silent(recording: Any, threshold: float) -> bool:
    """True when the recording's peak amplitude (0..1) is below the silence threshold."""
    return float(getattr(recording, "peak", 1.0)) < threshold


class CleanupBreaker:
    """Pauses cleanup for a while after the cleanup backend proved unreachable."""

    def __init__(self, pause_seconds: float = 30.0, clock: Callable[[], float] = time.monotonic):
        self.pause_seconds = pause_seconds
        self._clock = clock
        self.down_until = 0.0

    def allowed(self) -> bool:
        return self._clock() >= self.down_until

    def note_failure(self) -> bool:
        """Open the breaker. Returns True if it was closed before (log once)."""
        was_closed = self.allowed()
        self.down_until = self._clock() + self.pause_seconds
        return was_closed

    def reset(self) -> None:
        self.down_until = 0.0


class DictationApp:
    def __init__(self, cfg: Config):
        from . import audio, autostart, cleanup, inject, transcribe  # noqa: F401
        from .hotkeys import HotkeyListener, parse_chord
        from .overlay import Overlay
        from .tray import Tray, TrayHooks

        self.cfg = cfg
        self._audio = audio
        self._cleanup = cleanup
        self._inject = inject
        self._transcribe = transcribe
        self._autostart = autostart
        self._gen = 0
        self._flash_until = 0.0
        self._quit_lock = threading.Lock()
        self._quit_done = False
        self._startup_errors: list[str] = []
        self._transcriber: Any = None
        self._transcriber_error: str | None = None
        self._cleaner: Any = None
        self._breaker = CleanupBreaker(30.0)
        self._build_lock = threading.Lock()
        self._models_cache: list[str] = []
        self._models_at = 0.0
        self._models_refreshing = False

        try:
            self.chord = parse_chord(cfg.hotkey.chord)
        except ValueError as e:
            log.error("invalid hotkey chord: %s; falling back to ctrl+win", e)
            self._startup_errors.append(_short(str(e)))
            self.chord = parse_chord("ctrl+win")

        self.overlay = Overlay(cfg.ui)
        self.recorder = audio.Recorder(cfg.audio)
        self._build_backends()

        self.machine = DictationMachine(cfg.hotkey, self)
        self.listener = HotkeyListener(
            self.chord, self.machine.chord_down, self.machine.chord_up, self.machine.escape)

        hooks = TrayHooks(
            is_enabled=lambda: self.machine.enabled,
            set_enabled=self._set_enabled,
            current_backend=lambda: self.cfg.transcription.backend,
            set_backend=self._set_backend,
            whisper_models=self._whisper_models,
            current_whisper_model=lambda: self.cfg.transcription.local.model,
            set_whisper_model=self._set_whisper_model,
            cleanup_models=self._cleanup_models,
            current_cleanup_model=self._current_cleanup_model,
            set_cleanup_model=self._set_cleanup_model,
            is_autostart=autostart.is_enabled,
            set_autostart=self._set_autostart,
            open_config=lambda: os.startfile(str(config_path())),  # type: ignore[attr-defined]
            open_logs=self._open_logs,
            reload_config=self.reload_config,
            quit=self.quit,
        )
        self.tray = Tray(hooks)

    # -- backends
    def _build_transcriber(self) -> None:
        try:
            self._transcriber = self._transcribe.make_transcriber(self.cfg.transcription)
            self._transcriber_error = None
        except Exception as e:
            self._transcriber = None
            self._transcriber_error = str(e) or e.__class__.__name__
            log.error("transcriber unavailable: %s", self._transcriber_error)

    def _build_cleaner(self) -> None:
        """Blocking (may hit the network); run via _build_cleaner_async."""
        with self._build_lock:
            try:
                self._cleaner = self._cleanup.make_cleaner(self.cfg.cleanup)
            except Exception:
                log.exception("could not create cleaner; cleanup disabled")
                self._cleaner = None
            if self._cleaner is not None:
                self._breaker.reset()
            log.info("cleaner=%s", getattr(self._cleaner, "name", None))
        self._refresh_models(force=True)

    def _build_cleaner_async(self) -> None:
        self._breaker.reset()  # config changed: allow an immediate retry
        threading.Thread(target=self._build_cleaner, name="vibeflow-cleaner-init",
                         daemon=True).start()

    def _build_backends(self) -> None:
        self._build_transcriber()
        self._build_cleaner_async()
        log.info("backend=%s provider=%s", self.cfg.transcription.backend,
                 self.cfg.cleanup.provider if self.cfg.cleanup.enabled else "none")

    # -- cleanup model list cache (never blocks on network)
    def _refresh_models(self, force: bool = False) -> None:
        if self.cfg.cleanup.provider != "lmstudio":
            return
        if self._models_refreshing:
            return
        if not force and time.monotonic() - self._models_at < 30.0:
            return
        self._models_refreshing = True
        base_url = self.cfg.cleanup.base_url

        def work() -> None:
            try:
                models = self._cleanup.list_models(base_url)
                self._models_cache = list(models)
                self._models_at = time.monotonic()
            except Exception:
                log.exception("model list refresh failed")
            finally:
                self._models_refreshing = False
            try:
                self.tray.update_menu()
            except Exception:
                pass

        threading.Thread(target=work, name="vibeflow-models", daemon=True).start()

    # -- run / quit
    def run(self) -> None:
        log.info("starting; hotkey=%s", self.chord.describe())
        try:
            self.overlay.run(on_ready=self._start)
        finally:
            self.quit()

    def _start(self) -> None:
        try:
            self.listener.start()
        except Exception:
            log.exception("failed to start hotkey listener")
            self._startup_errors.append("Hotkey listener failed")
        try:
            self.tray.start()
        except Exception:
            log.exception("failed to start tray")
        if self._transcriber_error:
            self._startup_errors.append(self._transcriber_error)
        for msg in self._startup_errors:
            self._flash("error", "Error: " + _short(msg, 53))
            try:
                self.tray.notify("VibeFlow", msg)
            except Exception:
                pass
        self._startup_errors.clear()

    def quit(self) -> None:
        with self._quit_lock:
            if self._quit_done:
                return
            self._quit_done = True
        log.info("quitting")
        self._gen += 1
        for step in (self.listener.stop, self.tray.stop, self.recorder.discard, self.overlay.quit):
            try:
                step()
            except Exception:
                log.exception("error during quit")

    def reload_config(self) -> None:
        try:
            new = load_config()
        except Exception:
            log.exception("reload failed")
            return
        from .hotkeys import parse_chord
        self.cfg = new
        self.machine.cfg = new.hotkey
        try:
            self.chord = parse_chord(new.hotkey.chord)
            self.listener.set_chord(self.chord)
        except ValueError as e:
            log.error("invalid hotkey chord on reload: %s", e)
            self._flash("error", "Error: " + _short(str(e), 53))
        if self.machine.state == State.IDLE:
            self.recorder = self._audio.Recorder(new.audio)
        self._build_backends()
        if self._transcriber_error:
            self._flash("error", "Error: " + _short(self._transcriber_error, 53))
            self.tray.notify("VibeFlow", self._transcriber_error)
        else:
            self._flash("ok", "Config reloaded")

    # -- overlay helpers
    def _flash(self, kind: str, text: str, seconds: float = 1.5) -> None:
        self._flash_until = time.monotonic() + seconds
        try:
            self.overlay.flash(kind, text, seconds)
        except Exception:
            log.exception("overlay flash failed")

    # -- MachineActions
    def start_recording(self) -> bool:
        if self._transcriber is None:
            self._flash("error", "Error: " + _short(self._transcriber_error or "no transcriber", 53))
            return False
        try:
            self.recorder.start()
        except Exception as e:
            log.error("could not start recording: %s", e)
            self._flash("error", "Error: " + _short(str(e), 53))
            return False
        return True

    def discard_recording(self) -> None:
        try:
            self.recorder.discard()
        except Exception:
            log.exception("discard failed")

    def stop_and_process(self, mode: Mode) -> None:
        self._gen += 1
        gen = self._gen
        threading.Thread(target=self._pipeline, args=(gen, mode), name="vibeflow-pipeline",
                         daemon=True).start()

    def cancel_processing(self) -> None:
        self._gen += 1
        for obj in (self._transcriber, self._cleaner):
            if obj is not None:
                try:
                    obj.cancel()
                except Exception:
                    log.exception("cancel failed")
        self._flash("error", "Cancelled")

    def state_changed(self, state: State, mode: Mode | None) -> None:
        try:
            if state in (State.REC_HOLD, State.REC_TAPWAIT):
                self.overlay.show("recording", "Hold to talk", ticking=True)
            elif state == State.REC_HANDSFREE:
                self.overlay.show("handsfree", "Hands-free", ticking=True)
            elif state == State.PROCESSING:
                self.overlay.show("processing", "Transcribing…")
            elif time.monotonic() >= self._flash_until:
                self.overlay.hide()
            self.tray.set_recording(state in _REC_STATES)
        except Exception:
            log.exception("state_changed failed")

    # -- pipeline (worker thread)
    def _pipeline(self, gen: int, mode: Mode) -> None:
        keep = os.environ.get("VIBEFLOW_KEEP_WAV") == "1"
        recording = None
        outcome: tuple[str, str] | None = None
        try:
            recording = self.recorder.stop()
            if recording.seconds < self.cfg.hotkey.min_recording_seconds:
                log.info("recording too short (%.2fs); discarded", recording.seconds)
                return
            if is_silent(recording, self.cfg.audio.silence_threshold):
                log.info("recording is silent (peak %.4f < %.4f); skipping",
                         getattr(recording, "peak", 0.0), self.cfg.audio.silence_threshold)
                outcome = ("ok", "No speech detected")
                return
            transcriber = self._transcriber
            if transcriber is None:
                outcome = ("error", "Error: " + _short(self._transcriber_error or "no transcriber", 53))
                return
            text = transcriber.transcribe(recording.path, self.cfg.transcription.language)
            if gen != self._gen:
                return
            text = (text or "").strip()
            if not text:
                outcome = ("ok", "No speech detected")
                return
            raw = False
            c = self.cfg.cleanup
            want_cleanup = c.enabled and c.provider != "none"
            cleaner = self._cleaner
            if want_cleanup and cleaner is None and self._breaker.allowed():
                self._build_cleaner()
                cleaner = self._cleaner
                if cleaner is None and self._breaker.note_failure():
                    log.warning("cleanup unavailable (no LM Studio model loaded); "
                                "retrying in 30 s")
                if gen != self._gen:
                    return
            if want_cleanup and (cleaner is None or not self._breaker.allowed()):
                raw = True
            elif cleaner is not None:
                self.overlay.show("processing", "Cleaning up…")
                try:
                    cleaned = cleaner.clean(text)
                    if gen != self._gen:
                        return
                    text = cleaned or text
                except Exception as e:
                    if gen != self._gen:
                        return
                    raw = True
                    unavailable = getattr(self._cleanup, "CleanupUnavailable", ())
                    if unavailable and isinstance(e, unavailable):
                        if self._breaker.note_failure():
                            log.warning("LM Studio unreachable, pausing cleanup for 30 s (%s)", e)
                    else:
                        log.warning("cleanup failed, using raw transcript: %s", e)
            if gen != self._gen:
                return
            self._inject.insert_text(text, self.cfg.output, self.chord)
            outcome = ("ok", "Pasted (raw)" if raw else "Pasted")
        except Exception as e:
            if gen != self._gen:
                log.info("pipeline cancelled (%s)", e.__class__.__name__)
                return
            if e.__class__.__name__ == "TranscriptionCancelled":
                return
            log.exception("pipeline failed")
            outcome = ("error", "Error: " + _short(str(e) or e.__class__.__name__, 53))
        finally:
            if recording is not None and not keep:
                try:
                    Path(recording.path).unlink(missing_ok=True)
                except OSError:
                    log.warning("could not delete %s", recording.path)
            if gen == self._gen:
                if outcome is not None:
                    self._flash(*outcome)
                self.machine.processing_done()

    # -- tray hooks
    def _set_enabled(self, enabled: bool) -> None:
        self.machine.set_enabled(enabled)
        self.tray.set_enabled(enabled)

    def _save(self) -> None:
        try:
            save_config(self.cfg)
        except Exception:
            log.exception("could not save config")

    def _set_backend(self, backend: str) -> None:
        self.cfg.transcription.backend = backend
        self._save()
        self._build_transcriber()
        self._report_transcriber()

    def _set_whisper_model(self, model: str) -> None:
        self.cfg.transcription.local.model = model
        self.cfg.transcription.backend = "local"
        self._save()
        self._build_transcriber()
        self._report_transcriber()

    def _report_transcriber(self) -> None:
        if self._transcriber_error:
            self._flash("error", "Error: " + _short(self._transcriber_error, 53))
            self.tray.notify("VibeFlow", self._transcriber_error)

    def _set_cleanup_model(self, model: str | None) -> None:
        c = self.cfg.cleanup
        if model is None:
            c.enabled = False
        else:
            c.enabled = True
            c.model = model
            if c.provider == "none":
                c.provider = "lmstudio"
        self._save()
        self._build_cleaner_async()

    def _current_cleanup_model(self) -> str | None:
        c = self.cfg.cleanup
        if not c.enabled or c.provider == "none":
            return None
        if c.model:
            return c.model
        name = getattr(self._cleaner, "name", "") or ""
        return name.split("/", 1)[-1]

    def _cleanup_models(self) -> list[str]:
        if self.cfg.cleanup.provider != "lmstudio":
            return []
        self._refresh_models()
        return list(self._models_cache)

    def _whisper_models(self) -> list[Path]:
        folder = resolve_path(self.cfg.transcription.local.model).parent
        if not folder.is_dir():
            folder = ROOT / "whisper" / "models"
        return sorted(folder.glob("ggml-*.bin"))

    def _set_autostart(self, enabled: bool) -> None:
        try:
            if enabled:
                self._autostart.enable()
            else:
                self._autostart.disable()
        except Exception:
            log.exception("autostart change failed")

    def _open_logs(self) -> None:
        folder = ROOT / "logs"
        folder.mkdir(parents=True, exist_ok=True)
        os.startfile(str(folder))  # type: ignore[attr-defined]


# ---------------------------------------------------------------- entry point

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="vibeflow", description="System-wide AI dictation")
    parser.add_argument("--config", help="path to config.toml")
    parser.add_argument("--console", action="store_true", help="also log to stderr")
    parser.add_argument("--version", action="store_true")
    args = parser.parse_args(argv)
    if args.version:
        print(f"vibeflow {__version__}")
        return 0
    if args.config:
        os.environ["VIBEFLOW_CONFIG"] = str(Path(args.config).resolve())
    try:
        load_env()
        log_path = setup_logging(console=args.console)
        log.info("VibeFlow %s starting; log=%s", __version__, log_path)
        cfg = load_config()
        log.info("effective hotkey=%s backend=%s cleanup=%s", cfg.hotkey.chord,
                 cfg.transcription.backend,
                 cfg.cleanup.provider if cfg.cleanup.enabled else "off")
        DictationApp(cfg).run()
        return 0
    except KeyboardInterrupt:
        return 0
    except Exception:
        log.exception("fatal error")
        return 1


if __name__ == "__main__":
    sys.exit(main())
