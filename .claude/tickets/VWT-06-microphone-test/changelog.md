# VWT-06 changelog

## 2026-10-31 - VWT-06: choose and test the microphone

Outcome: mic chooser in Settings (new Microphone tab) and in the tray, with live meter, record + play back and record + transcribe tests. Automated tests pass; real-audio checks still need a human.

### vibe-whisper-flow (branch `VWT-06_microphone-test`, from `origin/master`)
- `vibeflow/audio.py`: device list limited to the default host API (mapper and duplicates dropped); saved-name matching (exact, 31-char MME prefix, substring, legacy fallback); `resolve_device` refreshes PortAudio once, then falls back to the Windows default (`Recorder.fell_back`) or raises "not connected"; `refresh_devices` refused while a stream is open; new `InputMonitor`, `play`, `write_wav`, `level_db`, `meter_percent`, `level_verdict`, `default_input_name`.
- `vibeflow/app.py`: tray/settings hooks for devices; `_set_input_device` saves and syncs Settings; `_refresh_audio_devices`; `_test_transcribe` uses the saved backend (raw text); recorder rebuilt when `cfg.audio` changes; one-time notice per missing mic; quit stops a running mic test.
- `vibeflow/tray.py`: "Microphone" submenu (radio items, "(not connected)", "No microphones found", "Refresh list", "Test microphone...").
- `vibeflow/settings_ui.py`: Microphone tab (read-only device combo, Refresh, silence threshold moved from General, meter with silence timer and unplug watchdog, record/play, record/transcribe, Stop, Copy); meter stops on hide, tab switch and Refresh.
- `README.md`: "Choose and test the microphone" section, tab list, troubleshooting row, layout line.
- Tests: `tests/test_audio.py`, `tests/test_tray.py`, `tests/test_settings_ui.py`, new `tests/test_app_mic.py`.

### Deviations
- None in behaviour. `resolve_device` wraps an internal `_resolve()` returning `(index, fell_back)`; `engine_text` returns only the engine description. No change needed to `packaging/VibeFlow.spec` or `scripts/build.ps1`.

### Verification
- `.venv\Scripts\python -m pytest -q`: 292 passed, 1 skipped (intermittent no-display Tk skip, also on older tests).
- Not run: manual checks 1-10 (real mic meter, playback, transcription, unplug, Windows-default follow-through, all in source and in the exe). Exe not built.

### Follow-ups
- Run the manual checks from the plan (step 5b).

### Commit status
- Committed on `VWT-06_microphone-test` as df37938; not pushed.

- Fix: play() only decrements the open-stream counter if start() succeeded (stream still closed); test added in tests/test_audio.py.
