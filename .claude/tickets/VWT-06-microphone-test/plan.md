# VWT-06: Choose and test the microphone

Ticket folder: `.claude/tickets/VWT-06-microphone-test/`.
Branch: `VWT-06_microphone-test` (from `master`, create it at implementation time). Implement via the global `implement` skill; it writes `changelog.md` in this folder.

Scope (full reading): pick the mic in Settings **and** from the tray, plus all three test modes (live level meter, record + play back, record + transcribe with the saved backend). Not in scope: WASAPI/exclusive-mode capture, automatic hot-plug detection (WM_DEVICECHANGE), output device choice, LLM cleanup of the test transcript.

## Checklist
- [x] 1. `audio.py` device listing, matching, PortAudio refresh, `InputMonitor`, `play`, meter helpers (+ `tests/test_audio.py`). Evidence: 27 tests in test_audio.py pass (fake sounddevice, lock ownership, stream counter, resolve+open in one hold).
- [x] 2. `app.py` wiring: hooks, `_set_input_device`, recorder rebuild, missing-mic fallback notice, `_test_transcribe`, quit (+ `tests/test_app_mic.py`). Evidence: 9 tests pass.
- [x] 3. `tray.py` "Microphone" submenu (+ `tests/test_tray.py`). Evidence: 12 tests in test_tray.py pass.
- [x] 4. `settings_ui.py` new **Microphone** tab: device chooser + meter + record/play + record/transcribe (+ `tests/test_settings_ui.py`). Evidence: tests pass on a real Tk root (fake InputMonitor and play).
- [x] 5. README done; packaging check: no spec change needed (no new modules or binaries); `scripts/build.ps1` needs no change. The exe was NOT built (the plan only asks for manual checks in the exe).
- [ ] 5b. Manual checks 1-10 in source and in the exe: UNTESTED (need a human with a real mic, playback and transcription).
- [x] Tests pass: `.venv\Scripts\python -m pytest -q` -> 291 or 292 passed, 1 or 2 skipped per run. The skips are intermittent "no display" Tk skips (also hit pre-existing tests), not failures.

Deviations: none in behaviour. Notes: internal helper `_resolve()` returns `(index, fell_back)` and `resolve_device` wraps it; `engine_text` returns only the engine description (the File tab keeps its own suffix); `InputMonitor` is created with `(device, sample_rate)` using the configured sample rate.

## Context

VibeFlow records with `sounddevice` (`audio.Recorder`, 16 kHz mono int16, `device=None` or an index from `_resolve_device`). The only device UI today is an editable "Input device" combo on the General tab (`settings_ui.py:466`), filled from `hooks.audio_devices` -> `app._audio_devices` (`app.py:525`) -> `audio.list_input_devices()`. `[audio].device` is `""` (Windows default) or a substring matched by `audio._resolve_device`. There is no way to hear or see what the mic picks up, and a wrong or unplugged mic shows up only as a failed or silent dictation.

### What I found (facts the design relies on)

- **Device list is noisy.** `sd.query_devices()` on this machine lists every input once per host API: MME (`Microsoft Sound Mapper - Input`, `FrontMic (Realtek(R) Audio)`, `Microphone (Realtek(R) Audio)`), DirectSound (`Primary Sound Capture Driver` + the same two), WASAPI (same two), WDM-KS (`... (Realtek HD Audio Front Mic input)`, `Stereo Mix ...`, a raw `@System32\drivers\bthhfenum.sys...` Bluetooth name). The current combo shows all 12.
- **PortAudio's default host API is MME** (`sd.default.device == [1, 4]`, `query_devices(kind="input")["hostapi"] == 0`). `Recorder` passes `device=None`, so dictation records through MME today.
- **WASAPI can't open at 16 kHz**: `InputStream(samplerate=16000, device=<WASAPI>)` fails with `Invalid sample rate [-9997]` (shared mode needs the native rate unless `WasapiSettings(auto_convert=True)`). If a substring happens to match a WASAPI entry first, recording fails. MME devices open fine at 16 kHz. MME names are cut to 31 characters.
- **PortAudio caches the device list at `Pa_Initialize`.** Mics plugged in after start, and changes of the Windows default, are invisible until `sd._terminate(); sd._initialize()` (private but present in sounddevice 0.5.6; measured 25 ms here). Re-initialising while a stream is open breaks that stream, so it must only run when no stream is open.
- **Two input streams on the same MME device at once work** (tested: two `InputStream`s on device 1 started together). A mic test does not lock out dictation, and dictation does not lock out the test, unless a driver/app holds the device in exclusive mode.
- **The MME Sound Mapper (`Microsoft Sound Mapper - Input`, index 0) opens at 16 kHz.** It maps to the current Windows default recording device when opened (WAVE_MAPPER), unlike `device=None`, which is the concrete default index fixed at init.
- **Playback**: `sd.OutputStream(samplerate=16000, channels=1, dtype="int16")` on the default output opens fine. No new dependency.
- **Settings UI** is tkinter + sv-ttk on the overlay's Tk root/thread. Workers are plain daemon threads that post back with `self._post` (= `overlay.call_soon`, a queue polled every 50 ms) wrapped in `self._safe_ui`. The File tab keeps its own `_file_busy` / `_file_cancel`, separate from the download `_busy`. Unsaved tab state lives outside `self._vars` so it never makes the form dirty.
- **Tray** is pystray; `pystray.Menu(self._menu_items)` is a callable, and the win32 backend rebuilds the native menu (labels and checks) only in `update_menu()`. `_action` already calls `update_menu()` after every click; "Refresh list" under Cleanup model is just `act(lambda: None)`. Tray actions run on the pystray thread and mutate `app.cfg` in place, then `_save()`.
- **Config live reload**: Settings Save -> `_settings_apply` -> `save_config` + `apply_config`, which replaces `self.recorder` only if the machine is IDLE. Tray changes (`_set_backend` etc.) edit `self.cfg` and `_save()` directly. `Recorder` reads `cfg.device` at `start()`.
- **Transcription off the hotkey path** already exists: `app._file_transcriber()` returns the shared Parakeet instance or a fresh private transcriber (so a job never touches dictation's `_proc`/`_cancelled`), and every backend has `transcribe_segments(wav, language, duration, progress, cancel)` with a `threading.Event` cancel. `make_transcriber` raises `TranscriptionError` with a readable reason when the backend isn't set up.
- **Tests** never touch real audio: `test_audio.py` injects a `FakeStream` into `Recorder._stream`; UI tests create a real `tk.Tk()` (skip on `TclError`) with `_hooks()` and a `_Pump` that runs posted callbacks; app tests build `DictationApp.__new__(DictationApp)` and set attributes by hand.
- **Packaging**: sounddevice's PortAudio DLL already ships (pyinstaller-hooks-contrib hook, `_sounddevice_data`). Nothing new to bundle.
- Files in this repo use CRLF; keep edited/new files CRLF.

## Decisions

| # | Decision | Reason |
|---|---|---|
| D1 | List only devices of PortAudio's **default host API** (MME), drop the mapper entries (`Microsoft Sound Mapper - Input`, `Primary Sound Capture Driver`), de-duplicate by name. | One entry per physical mic, and every entry is one that opens at 16 kHz. Matches what dictation already records through. |
| D2 | `""` (Windows default) opens the **MME Sound Mapper** when present, else `device=None`. | Follows a Windows default-device change without restarting VibeFlow. Verify manually (step 5); if it does not follow, fall back to `None` plus `refresh_devices()` on Settings open. |
| D3 | Matching a saved name: exact (case-insensitive) -> saved name starts with a 31-char MME name -> substring (as today), default host API first, then all host APIs. | Keeps every existing config working (substrings, old full WASAPI names), but an exact pick from the list always wins over a shorter name that contains it. |
| D4 | A saved mic that is not connected: **dictation falls back to the Windows default** and shows a flash + tray notification once per missing name; the config keeps the name, so the mic is used again when plugged back in. The **test** does not fall back: it says "<name> is not connected". | Today dictation just fails with "Input device not found". The test must not silently test a different mic. **Confirmed by the user (Q1):** rescan once, fall back to the Windows default, one-time notice. |
| D5 | "Refresh" re-initialises PortAudio only when no VibeFlow stream is open (module counter + lock in `audio.py`); otherwise it re-lists the cached devices and **tells the user** (Settings status line; tray notification for tray "Refresh list"). `Recorder.start` tries one refresh before falling back (D4). Every PortAudio call that lists, resolves, opens, closes or re-inits holds `_PA_LOCK`; resolve + open run under one hold. | Re-init with an open stream would break dictation or the meter. The tray lists devices on its own thread on every menu rebuild, so listing must not overlap a `_terminate`/`_initialize`, and an index resolved before a re-init must not be opened after it. |
| D6 | New **Microphone** tab (after General). The Audio frame (Input device, Silence threshold) moves there from General, together with the tests. The device combo becomes **read-only** with display labels; the config value lives in the hidden `audio_device` var. | Picking and testing belong together; General is already long. Read-only stops typos. Power users can still type a substring in `config.toml`. **Confirmed by the user (Q2).** |
| D7 | Tests use the **device selected in the combo, even unsaved**. Record + transcribe uses the **saved** backend and language (shown in an "Engine:" line, as on the File tab). | Try a mic before saving it; the backend choice stays on the Transcription tab. |
| D8 | One `InputMonitor` stream per test session feeds the meter and captures clips. Record buttons start it if needed and leave it as it was afterwards. | One open stream instead of three; the meter keeps moving while recording. |
| D9 | The mic test may run while dictation is idle **or** active; no lock-out either way. | Shared-mode MME allows two streams (tested). An exclusive-mode driver gives a readable "busy" error instead. |
| D10 | Record + transcribe calls `transcriber.transcribe_segments(...)` on `app._file_transcriber()` and joins the segment texts. No LLM cleanup. | Reuses the File-tab path: cancellable, never touches dictation's state, shares the Parakeet model. Raw text is what the mic test is about. **Confirmed by the user (Q3):** raw text only. |
| D11 | Tray: "Microphone" submenu with radio items, "Refresh list" and "Test microphone...". Picking saves `[audio].device` immediately (like the backend items). | Quick switching from the tray, persisted. |
| D12 | Meter scale: dBFS from -60 to 0 dB over a 0..100 progress bar. Verdict text: "No signal" (peak < 1e-4 for 1.5 s), "Too quiet: dictation would ignore this" (peak < silence threshold), "Good", "Clipping" (peak >= 0.99). | Ties the meter to the setting that actually drops dictations (`is_silent`). |

## Design

### 1. `vibeflow/audio.py`

Keep the existing `Recorder`, `_levels`, `AudioError` and `Recording` behaviour; add:

```python
@dataclass(frozen=True)
class InputDevice:
    index: int
    name: str
    is_default: bool

_MAPPER_NAMES = ("microsoft sound mapper - input", "primary sound capture driver")
_PA_LOCK = threading.RLock()   # guards EVERY PortAudio call VibeFlow makes outside stream callbacks:
                               # query_devices, init/terminate, stream open/start/stop/close.
                               # Reentrant: resolve_device -> refresh_devices nests inside start().
_open_streams = 0              # streams opened by VibeFlow and not yet closed

def input_devices() -> list[InputDevice]
    # whole body under _PA_LOCK (the tray calls this on its thread on every menu rebuild, which can
    # coincide with refresh_devices() on a Settings worker).
    # default host API only (sd.query_devices(kind="input")["hostapi"]; if that raises, use all),
    # inputs only, mapper names removed, de-duplicated by name (first index wins),
    # is_default = index == the default input index. Raises AudioError like list_input_devices.
def list_input_devices() -> list[tuple[int, str]]   # kept: [(d.index, d.name) for d in input_devices()]
def default_input_name() -> str | None              # under _PA_LOCK; name of the default input,
                                                    # None on error/no device
def match_device(saved: str, names: Sequence[str]) -> str | None
    # pure (D3): "" -> None; exact casefold; then len(n) == 31 and saved.casefold().startswith(n.casefold());
    # then substring; returns the matching entry of `names` or None.
def resolve_device(name: str, *, fallback: bool = False) -> int | None
    # under _PA_LOCK. The returned index is only valid while the caller keeps holding the lock,
    # so callers that open a stream must hold _PA_LOCK across resolve + open (see Recorder/InputMonitor).
    # "" -> mapper index (D2) or None. Otherwise match_device over input_devices(), then over all
    # host APIs' inputs (old configs). Not found: refresh_devices() once and retry; still not found ->
    # fallback=True: log warning, return the "" result; fallback=False: AudioError(f"{name} is not connected").
_resolve_device = resolve_device                    # keep the old name for callers/tests
def refresh_devices() -> bool
    # with _PA_LOCK: if _open_streams: return False; sd._terminate(); sd._initialize(); return True.
    # Any exception -> AudioError("Could not refresh audio devices: ...").
def open_stream_count() -> int
def write_wav(path: Path, data, rate: int) -> None  # extracted from Recorder.stop (mono int16)
def level_db(peak: float) -> float                  # 20*log10(peak), floor -90.0
def meter_percent(peak: float, floor_db: float = -60.0) -> int   # 0..100 (D12)
def level_verdict(peak: float, threshold: float, silent_for: float = 0.0) -> str
    # pure, stateless: "silent" (peak < 1e-4 and silent_for >= 1.5), "quiet", "ok", "clipping".
    # The caller measures silent_for; the 1.5 s timer lives in the Settings poll loop
    # (self._mic_quiet_since, see Design 4 "Meter polling"), not in audio.py.
```

`Recorder` changes:
- `start()`: take `_PA_LOCK` once and, inside that single hold: `device = resolve_device(self.cfg.device, fallback=True)`; set `self.fell_back = bool(self.cfg.device) and <resolved to the default>`; open + start the stream; increment `_open_streams`. Resolving and opening under one hold means a Refresh can't re-init PortAudio between them and leave a stale index. `_close_stream()` takes `_PA_LOCK` for stop/close and decrements the counter (in a `finally`, once per opened stream).
- `stop()` writes through `write_wav`.

New `InputMonitor` (meter + clip capture on one stream):

```python
class InputMonitor:
    def __init__(self, device: str, sample_rate: int = 16000, block_seconds: float = 0.05)
    device_label: str            # resolved device name, or "Windows default (<name>)"
    running: bool (property)
    def start(self) -> None      # ONE _PA_LOCK hold around: resolve_device(device, fallback=False),
                                 # InputStream(int16, mono, blocksize=int(rate*block_seconds), callback),
                                 # stream.start(), counter++ (same rule as Recorder.start).
                                 # Open errors -> AudioError("Could not open <label>: <exc>. Is another
                                 # app using it in exclusive mode?")
    def stop(self) -> None       # idempotent, any thread; counter--; also ends a capture
    def levels(self) -> tuple[float, float, float]   # (peak, rms) of the latest block + seconds since
                                                     # the last callback (watchdog for unplugged mics)
    def begin_capture(self) -> None
    def end_capture(self) -> "np.ndarray"            # 1-D int16, empty array if nothing captured
```

Callback runs on the PortAudio thread: compute `_levels(indata)`, store `(peak, rms, time.monotonic())` under a `threading.Lock`, append `indata.copy()` when capturing. No Tk calls there.

New `play(data, rate, cancel: threading.Event | None = None) -> None`: default output `sd.OutputStream(samplerate=rate, channels=1, dtype="int16")` under the lock/counter, `stream.write()` in 0.1 s chunks, stop early when `cancel` is set, always close. Errors -> `AudioError("Could not play back: ...")`. Blocking: call it from a worker thread only.

### 2. `vibeflow/app.py`

- `__init__`: `self._missing_mic_warned: set[str] = set()`.
- Settings hooks (`_settings_window`, `app.py:457`): add `default_audio_device=self._default_audio_device`, `refresh_audio_devices=self._refresh_audio_devices`, `test_transcribe=self._test_transcribe`.
- Tray hooks (`app.py:293`): add `input_devices=self._audio_devices`, `default_input_device=self._default_audio_device`, `current_input_device=lambda: self.cfg.audio.device`, `set_input_device=self._set_input_device`, `refresh_input_devices=self._refresh_audio_devices`, `open_mic_test=lambda: self.open_settings(tab="microphone")`.
- `_default_audio_device() -> str | None`: `audio.default_input_name()`, `None` on any exception.
- `_refresh_audio_devices() -> bool`: `audio.refresh_devices()` (catch `AudioError` -> log, `False`), then `self.tray.update_menu()`. Safe from any thread.
- `_set_input_device(name: str)` (tray thread): `self.cfg.audio.device = name`; `_save()`; `self._missing_mic_warned.discard(name)`; `_flash("ok", "Microphone: " + _short(name or "Windows default", 48))`; if `self.settings` exists, `overlay.call_soon(lambda: self.settings.sync_audio_device(name))`.
- Tray switches need no recorder rebuild: `Recorder.cfg` **is** `self.cfg.audio` (same object), `_set_input_device` mutates it in place, and `Recorder.start()` reads `cfg.device` on every start. So the next hotkey press uses the new mic even if the switch happened mid-recording.
- `start_recording()`: before `recorder.start()`, `if self.recorder.cfg is not self.cfg.audio: self.recorder = self._audio.Recorder(self.cfg.audio)` (identity check, not `!=`). This covers the other path: Settings Save / Reload config during a recording, where `apply_config` swaps in a new `Config` but skips rebuilding the recorder because the machine isn't IDLE. Safe here because `start_recording` is only called from IDLE. After a successful start, if `getattr(self.recorder, "fell_back", False)` and the name is not in `_missing_mic_warned`: add it, `_flash("error", _short(f"{name} not found, using Windows default", 60), 2.5)`, `tray.notify(...)`. Do not fail the recording (D4).
- `_test_transcribe(wav: Path, seconds: float, cancel: threading.Event) -> str`: `t = self._file_transcriber()` (raises `TranscriptionError` if not set up); `segs = t.transcribe_segments(wav, self.cfg.transcription.language, seconds, lambda _f: None, cancel)`; return `" ".join(s.text for s in segs).strip()`.
- `quit()`: before `overlay.quit`, a step `lambda: self.settings is not None and self.settings.stop_mic_test()` (thread-safe; touches streams only, never Tk).

### 3. `vibeflow/tray.py`

`TrayHooks` gets new fields **with defaults** (so existing constructions keep working): `input_devices: Callable[[], list[str]] = lambda: []`, `default_input_device: Callable[[], str | None] = lambda: None`, `current_input_device: Callable[[], str] = lambda: ""`, `set_input_device: Callable[[str], None] = lambda _n: None`, `refresh_input_devices: Callable[[], bool] = lambda: False`, `open_mic_test: Callable[[], None] = lambda: None`.

New pure-ish helper `_mic_items(self) -> list` used in `_menu_items`, placed as `M("Microphone", pystray.Menu(*mic_items))` right after "Transcription":
1. `"Windows default (<default name>)"` or `"Windows default"` when unknown; radio, checked when `current_input_device() == ""`; action `set_input_device("")`.
2. One radio item per name from `input_devices()` (exceptions logged -> empty list); checked when `audio.match_device(current, names) == name`; action `set_input_device(name)`.
3. If `current` is non-empty and matches nothing: `"<current> (not connected)"`, checked, `enabled=False`.
4. If the list is empty: `"No microphones found"`, `enabled=False`.
5. Separator, `"Refresh list"` -> `act(self._refresh_mics)` (act calls `update_menu()`), `"Test microphone..."` -> `act(h.open_mic_test)`.

`_refresh_mics(self)`: `if not h.refresh_input_devices(): self.notify("VibeFlow", "Microphone list not rescanned: stop the microphone test and any dictation, then try again.")` (D5 feedback; `notify` already swallows errors). A True result needs no message: the rebuilt menu is the feedback.

Import `match_device` from `.audio` lazily inside the helper (audio imports numpy lazily already; keep tray import-light).

### 4. `vibeflow/settings_ui.py`

- `TABS = ("general", "microphone", "transcription", "file", "cleanup", "keys", "about")`, `TAB_TITLES["microphone"] = "Microphone"`.
- `SettingsHooks` new fields after `version`, all with defaults: `default_audio_device: Callable[[], str | None] = lambda: None`, `refresh_audio_devices: Callable[[], bool] = lambda: False`, `test_transcribe: Callable[[Path, float, threading.Event], str] | None = None`.
- Pure helpers (module level, unit-tested):
  - `DEFAULT_DEVICE_LABEL = "Windows default"`.
  - `device_choices(names, default_name, saved) -> list[tuple[str, str]]`: `(label, value)` pairs. First `("Windows default (<default_name>)" or "Windows default", "")`, then `(n, n)` per name; if `saved` is set and `match_device(saved, names)` is None, append `(f"{saved} (not connected)", saved)`.
  - `device_label_for(saved, choices, names) -> str`: label for the saved value; a legacy substring that matches a device shows that device's name (value unchanged until the user picks).
  - `engine_text(cfg) -> str`: moved out of `_file_refresh_engine` so the File and Microphone tabs share it.
- `_build_general`: remove the Audio frame. `form_from_config`/`config_from_form` are unchanged (`audio_device`, `silence_threshold` stay the same keys).
- `_build_microphone(f)`:
  - **Device** frame: hidden `self._var("audio_device", "")` (registered, so Save/dirty still work) + a read-only combo bound to an unregistered `self._mic_label` StringVar; `<<ComboboxSelected>>` maps label -> value into `audio_device` and restarts the meter if it is running. Keep the attribute name `self._device_combo`. A **Refresh** button runs `hooks.refresh_audio_devices()` in a worker (stop the meter first), then `_load_devices()`; status "Device list refreshed" or "Close the test and stop dictating to rescan devices" when it returned False. Gray hint: "Windows default follows the recording device set in Windows Sound settings." `_entry(..., "Silence threshold", "silence_threshold", ...)` moves here.
  - **Test** frame: `ttk.Progressbar` (0..100, length ~260) + level label ("-18 dB  Good"); "Start meter"/"Stop meter" toggle; clip length combo `("3", "5", "10")` seconds, default 5, unregistered; "Record and play back", "Record and transcribe", "Stop" buttons; status label; gray engine line (`engine_text(cfg)` + "(saved settings)"); read-only `tk.Text` (height 3) + "Copy" for the transcript.
- State outside `self._vars` (like the File tab): `self._mic_monitor: audio.InputMonitor | None`, `self._mic_busy: bool`, `self._mic_cancel = threading.Event()`, `self._mic_poll_id`, `self._mic_choices`, `self._mic_quiet_since: float | None` (monotonic time the peak first dropped below 1e-4; owned by the poll loop).
- Meter polling on the Tk thread: `self.top.after(50, self._mic_poll)` while the monitor runs; reads `monitor.levels()`, sets the bar from `audio.meter_percent`. Silence timer: if `peak < 1e-4`, set `_mic_quiet_since` to now when it is None, else reset it to None; `silent_for = now - _mic_quiet_since` (0 when None). `_mic_quiet_since` is reset to None whenever the monitor starts or restarts. Label from `audio.level_db` + `audio.level_verdict(peak, threshold, silent_for)` where the threshold is the **form's** silence threshold (`_float` on parse error -> config value). If no callback for > 2 s: stop the monitor and show "No audio from <device>. Is it unplugged?". Cancel the `after` id on stop.
- Record flows (worker thread `vibeflow-mictest`, results via `self._post(lambda: self._safe_ui(...))`):
  1. Tk thread: if `_mic_busy` return; start the monitor if not running (remember `was_running`); `AudioError` -> status, stop. `begin_capture()`, set busy, status "Recording 5 s... speak now" with a countdown in `_mic_poll`.
  2. Worker: wait `seconds` on `self._mic_cancel.wait(seconds)`; `data = monitor.end_capture()`; cancelled -> "Stopped.".
  3. Play back: `audio.play(data, rate, self._mic_cancel)`; status "Playing back..." then "Done. Peak -12 dB." Peak below threshold adds "Dictation would ignore this recording (silence threshold 0.01)."
  4. Transcribe: `hooks.test_transcribe is None` -> "Transcription test is not available." Else write `%TEMP%/vibeflow/mictest-<ts>.wav` with `audio.write_wav`, call `hooks.test_transcribe(path, seconds, cancel)`, show text (or "No speech recognised."), delete the WAV in `finally`. `TranscriptionCancelled` -> "Stopped."; `TranscriptionError` -> "Transcription failed: <reason>" (includes "not set up" reasons from `make_transcriber`); other exceptions logged + shown.
  5. Finish: clear busy; stop the monitor if `not was_running`.
- "Stop" sets `_mic_cancel`; it is cleared at the start of each run.
- `stop_mic_test()`: public, thread-safe: set `_mic_cancel`, `monitor.stop()`. No Tk calls (the app calls it from `quit()` on another thread). The Tk-side `_mic_stop_ui()` also cancels the `after` and resets the bar.
- Stop the meter when the window hides (`hide()`), on `<<NotebookTabChanged>>` away from Microphone, and before Refresh.
- `_populate`/`_load_devices`: build `self._mic_choices` from `hooks.audio_devices()` + `hooks.default_audio_device()` + the saved value; set combo values to labels and `_mic_label` from `device_label_for`. `_populate` keeps setting the hidden var from the form, so the snapshot stays clean.
- `sync_audio_device(value)` (Tk thread, called after a tray switch): if `audio_device` still equals its snapshot value (user hasn't touched it), set the var and the snapshot entry to `value` and reload the labels; otherwise leave the user's unsaved choice alone.
- `_fit()`: check the window still fits with the new tab (the File tab is the tallest today).

### 5. Packaging, docs

- `packaging/VibeFlow.spec`: no new modules or binaries (all new code is in existing modules; PortAudio already ships). Only verify in the built exe (step 5).
- `README.md`: a "Choose and test the microphone" section (Microphone tab, the three tests, tray submenu, Refresh, not-connected fallback, Windows privacy hint when the meter shows "No signal"). Update "Settings window" (tab list), the Troubleshooting row "No microphone / wrong mic" to point at the tab instead of the `python -c` command, and the project layout line for `audio.py`.

## Threading and device contention

- **Tk thread**: all widget work, meter polling (`after(50)`), starting/stopping the monitor (fast).
- **PortAudio callback thread**: only `_levels` + list append under a lock.
- **Worker thread** (`vibeflow-mictest`): the clip wait, `audio.play`, `write_wav`, `hooks.test_transcribe`. One at a time (`_mic_busy`), separate from `_busy` (downloads) and `_file_busy`.
- **Tray thread**: `_set_input_device` edits `cfg` and saves (as the other tray items do); the Settings view is updated through `overlay.call_soon`.
- **Dictation vs test**: both may hold an input stream at once (shared-mode MME, D9). PortAudio re-init is refused while any VibeFlow stream is open (D5). Every non-callback PortAudio call holds the reentrant `_PA_LOCK`: device listing (`input_devices`, `default_input_name`, so the tray-thread menu rebuild can't run during a re-init on a Settings worker), `refresh_devices`, stream stop/close, and `Recorder.start` / `InputMonitor.start`, which hold it in **one** span across resolve-device **and** open-stream. So a Refresh lands either before the resolve or after the stream is open (and is then refused), never between them, and a hotkey press can't open a stale index. The longest hold is the ~25 ms re-init; the Tk thread may wait that long when it lists devices or starts the meter during a Refresh.
- **Transcriber sharing**: whisper.cpp and cloud use a private instance per test; Parakeet shares the model, and `transcribe_segments` takes the lock per VAD segment, so a dictation waits at most one segment.
- **Device change while recording**: the running `Recorder` keeps its stream. A tray switch mutates the shared `cfg.audio` in place, so the next `start()` reads the new name. A Settings Save / Reload during a recording swaps in a new `AudioConfig`; the next `start_recording` sees `recorder.cfg is not cfg.audio` and rebuilds.
- **Quit**: `stop_mic_test()` closes the monitor and cancels a playback/transcription; worker threads are daemons.

## Error cases

| Case | Behaviour |
|---|---|
| sounddevice/PortAudio missing | `AudioError("Audio backend unavailable: ...")`: combo shows only "Windows default", test buttons show the error in the status line, tray shows "No microphones found". |
| No input devices | Combo: only "Windows default"; tray: "No microphones found" (disabled); Start meter -> "No microphone found". Dictation unchanged ("No microphone found or input device busy"). |
| Saved device not connected | Combo and tray show "<name> (not connected)" selected. Dictation: one refresh attempt, then Windows default with a one-time flash + notification (D4). Test: "<name> is not connected". |
| Device unplugged during the meter or a clip | Watchdog: no callback for 2 s -> monitor stopped, "No audio from <device>. Is it unplugged?". A clip in progress ends with what was captured, or "No audio captured". |
| Device busy (exclusive mode) / open fails | `AudioError` with the PortAudio text + "Is another app using it in exclusive mode?". |
| Mic blocked by Windows privacy | MME delivers zeros: verdict "No signal", hint "Check Windows Settings > Privacy & security > Microphone". |
| No output device / playback fails | "Could not play back: ...". The clip's peak is still reported. |
| Transcription backend not set up | `make_transcriber` raises `TranscriptionError` -> "Transcription failed: whisper model not found: ... (install it in Settings > Transcription)" etc. |
| Backend fails mid-run (network, whisper exit code) | "Transcription failed: <reason>"; temp WAV deleted. |
| Silent clip | Transcribes anyway, with "Peak below the silence threshold: dictation would ignore this" (whisper may hallucinate on silence). |
| Refresh while a stream is open | Returns False. Settings: status "Close the test and stop dictating to rescan devices". Tray: notification "Microphone list not rescanned: stop the microphone test and any dictation, then try again." |
| Window closed / app quit mid-test | `hide()` stops the meter; `quit()` calls `stop_mic_test()`; posted callbacks are wrapped in `_safe_ui`. |

## Tests

How sounddevice is mocked: the new tests never import the real module. A `FakeSD` (`types.SimpleNamespace` or a small class in the test file) is installed with `monkeypatch.setattr(audio, "_sd", lambda: fake)`. It provides `query_devices(device=None, kind=None)` returning dicts with `name`, `hostapi`, `max_input_channels` (and the default-input dict for `kind="input"`), `_terminate` / `_initialize` that record calls, and `InputStream` / `OutputStream` fakes that store the callback, record `start/stop/close/write` and can be driven by calling `fake_stream.callback(np.array(...), n, None, None)`. Device fixture: the real list above (MME, DirectSound, WASAPI, WDM-KS entries).

`tests/test_audio.py` (step 1):
- `input_devices`: only the default host API, mapper names removed, duplicates removed, `is_default` set; falls back to all host APIs when the default query raises; `AudioError` when `query_devices` raises.
- `match_device`: `""` -> None; exact beats substring (`"Microphone"` vs `"Microphone (USB)"`); case-insensitive; 31-char MME prefix of a longer saved name; substring fallback; no match -> None.
- `resolve_device`: exact name -> MME index; `""` -> mapper index, or None without a mapper; a name only present on WASAPI still resolves (legacy); missing + `fallback=False` -> `AudioError("... is not connected")`; missing + `fallback=True` -> refresh called once, then the default result.
- `refresh_devices`: re-inits when no stream is open; returns False and does not call `_terminate` while a stream is open; counter is back to 0 after `Recorder` start + stop and after an `InputMonitor` start failure.
- Locking: a fake `sd` whose `query_devices` / `InputStream` record `audio._PA_LOCK._is_owned()` (RLock) asserts that `input_devices`, `default_input_name`, `refresh_devices` and both `start()` methods call PortAudio with the lock held. A fake `_initialize` that bumps device indices between calls shows that `Recorder.start` resolves and opens with the same snapshot (resolve + open in one hold).
- `Recorder.start` sets `fell_back` for a missing device and opens the default.
- `InputMonitor`: callback updates `levels()`; capture returns only the blocks between `begin`/`end`; `stop()` is idempotent; open failure -> `AudioError` with the busy hint.
- `play`: writes all samples in 0.1 s chunks; stops early when cancel is set; closes the stream on error.
- `write_wav` round trip; `level_db`, `meter_percent` (0, 1e-3 -> 0, 0.001..1 scaling, 1.0 -> 100), `level_verdict` boundaries.

`tests/test_app_mic.py` (step 2, `DictationApp.__new__` pattern as in `test_app_banner.py`):
- `_set_input_device` sets cfg, calls `_save`, flashes, clears the warned name.
- `start_recording` rebuilds the recorder when `self.cfg.audio` is a different object (Settings Save during a recording) and keeps the same recorder after a tray switch that mutated the shared object in place (fake `_audio.Recorder`).
- Fallback notice once per missing name (fake recorder with `fell_back=True`, two starts -> one notify).
- `_test_transcribe` joins segment texts and passes language, duration, cancel; propagates `TranscriptionError`.
- `_refresh_audio_devices` returns False on `AudioError` and still calls `tray.update_menu`.

`tests/test_tray.py` (step 3): a shared `_hooks(**overrides)` helper; the existing tests keep passing with the defaults. `Tray(hooks)._menu_items()` -> find the "Microphone" item -> `item.submenu.items`; assert labels, `item.checked` and `item.enabled` (both are evaluated properties) for: default selected, a named device selected, legacy substring selected, missing device "(not connected)" item, empty list "No microphones found". "Refresh list" and "Test microphone..." call their hooks when invoked as `item(None)`; "Refresh list" with `refresh_input_devices` returning False calls `notify` once (monkeypatch `tray.notify`), and returning True does not (checked: pystray `MenuItem.__call__(icon)` runs the action; `update_menu()` before `start()` doesn't raise).

`tests/test_settings_ui.py` (step 4):
- Pure: `device_choices` (default label with/without name, missing saved device appended), `device_label_for` (legacy substring shows the device name), `engine_text` for all four backends.
- Window (skip without display): the Microphone tab builds; `_hooks` passes `audio_devices=lambda: ["Mic A"]`, `default_audio_device=lambda: "Mic A"`; picking a label sets `audio_device` and makes the form dirty; Save writes `cfg.audio.device`; a tray `sync_audio_device` on a clean form keeps it clean.
- With `audio.InputMonitor` and `audio.play` monkeypatched to fakes (`settings_ui.audio`): record-and-play runs through `_Pump` and shows "Done"; record-and-transcribe shows the stub hook's text; a `TranscriptionError` from the hook shows "Transcription failed"; Stop cancels; `hook=None` shows "not available"; the temp WAV is deleted. Generalise `_Pump.wait` to take a predicate (`lambda: win._mic_busy`), keeping the File-tab callers working.
- Silence timer: with a fake monitor reporting peak 0 and `time.monotonic` monkeypatched, `_mic_poll` shows "Too quiet"-or-quiet text before 1.5 s and "No signal" after; a loud block resets `_mic_quiet_since` to None.
- `test_window_smoke` still iterates all `TABS` and stays clean.

### Manual checks (step 5)

From source (`.venv\Scripts\python -m vibeflow --console`; the installed `D:\Programs\VibeFlow\VibeFlow.exe` holds the single-instance mutex, so ask the user before stopping it):
1. Settings > Microphone: the list shows one entry per mic (no Sound Mapper, no WDM-KS names); "Windows default (<name>)" names the current default.
2. Start meter: talk, tap the mic, stay silent; check the bar, dB, verdicts; raise the silence threshold above your speech level and see "Too quiet".
3. Record and play back with each mic; Stop mid-clip; Stop mid-playback.
4. Record and transcribe with whisper.cpp, Parakeet, and Groq (if a key is set); then with the backend broken (rename the model) -> readable error.
5. Hotkey dictation while the meter runs, and the meter running during dictation (D9).
6. Unplug a USB/Bluetooth mic during the meter -> watchdog message. Plug it back, Refresh -> it appears.
7. Select the USB mic, save, unplug it, dictate -> flash "not found, using Windows default", text still pasted; replug -> it is used again without a restart.
8. Change the Windows default recording device while VibeFlow runs (device = Windows default), dictate -> the new default is used (D2). If not, apply the D2 fallback and note it.
9. Tray > Microphone: switch devices, check the radio mark, `config.toml` `[audio].device`, and that the open Settings window shows the new choice. "Test microphone..." opens the tab.
10. `scripts/build.ps1`, run `dist\VibeFlow\VibeFlow.exe`, repeat 1-4 and 9 once (PortAudio playback in the frozen build; Windows mic privacy lists VibeFlow.exe).

## Implementation steps (for the `implement` skill)

Interfaces above are fixed, so steps 2, 3 and 4 can run in parallel once step 1 is merged; each touches its own files.

1. **Audio primitives** - `vibeflow/audio.py`, `tests/test_audio.py`. Everything in Design 1. Done when the new audio tests and the existing ones pass.
2. **App wiring** - `vibeflow/app.py`, new `tests/test_app_mic.py`. Design 2. Pass the new hook fields by keyword; they exist with defaults after steps 3/4, so if step 2 lands first, add the fields to `TrayHooks`/`SettingsHooks` exactly as specified there.
3. **Tray submenu** - `vibeflow/tray.py`, `tests/test_tray.py`. Design 3.
4. **Microphone tab** - `vibeflow/settings_ui.py`, `tests/test_settings_ui.py`. Design 4. Largest step; may be split into 4a (tab, move Audio frame, device chooser, Refresh, `sync_audio_device`, helpers) and 4b (meter + record/play + record/transcribe + `stop_mic_test`).
5. **Docs and verification** - `README.md`; full `pytest`; `scripts/build.ps1`; manual checks above. Record what was and wasn't run in `changelog.md` (DONE / NOT DONE / UNTESTED).

## User decisions (were open questions)

- **Q1** Unplugged saved mic: rescan once, fall back to the Windows default, one-time notice (D4).
- **Q2** Input device and Silence threshold move from General into the new Microphone tab (D6).
- **Q3** Record and transcribe shows raw text only, no LLM cleanup (D10).

No open questions remain.
