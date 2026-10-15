# VWT-03 changelog

## 2026-10-15 (later) — Fix Parakeet in the portable build

Outcome: in the portable exe, picking Parakeet failed with "onnx-asr is not available
(PackageNotFoundError: No package metadata was found for onnx-asr)". Cause: `onnx_asr/__init__.py` calls
`importlib.metadata.version("onnx-asr")` on import, and PyInstaller didn't bundle the dist-info. A frozen exe
has no pip, so the fix is to bundle the metadata, not to offer an install from the UI.

### Files changed
- `packaging/VibeFlow.spec`: `copy_metadata("onnx-asr") + copy_metadata("onnxruntime")` added to datas.
- `vibeflow/parakeet.py`: in the frozen build, the unavailable message no longer says `pip install`. It now
  says the engine is missing from the build.

### Verification
- `scripts\build.ps1`: 196 tests passed, and the build and zip succeeded. `onnx_asr-0.12.0.dist-info` and
  `onnxruntime-1.31.0.dist-info` are in `dist/VibeFlow/_internal`.
- Ran `dist\VibeFlow\VibeFlow.exe` with a temp config (`backend = "parakeet"`, model_dir pointing at the
  installed Parakeet files). The log shows `parakeet model loaded from D:\Programs\VibeFlow\whisper\models\parakeet-tdt-0.6b-v3`
  about 3 s after start.
- Not run: hotkey dictation through the exe.

Commit status: uncommitted.

## 2026-10-15 — Croatian fine-tuned Whisper model + Parakeet v3 backend

Outcome: two new ways to dictate Croatian. One is a downloadable Croatian fine-tune of whisper large-v3-turbo
(ggml, sha256-pinned). The other is a new local backend, NVIDIA Parakeet-TDT-0.6B-v3 (onnx-asr, int8,
with Silero VAD for long recordings). Tests pass and the portable build succeeds.

Repo: vibe-whisper-flow. Branch `VWT-03_croatian-asr-models`, cut from `origin/master` at 7e015d7.

### Files changed
- `vibeflow/whisper_setup.py`:
  - New models `large-v3-turbo-hr` (q8_0, 874 MB) and `large-v3-turbo-hr-q5` (574 MB), downloaded from
    `JoaoZaokk/whisper-large-v3-turbo-hr-parla-ggml` pinned at commit 8280f14.
  - `_stream_to` takes an optional hasher. Pinned downloads are checked against sha256, and a
    mismatch deletes the `.part` file and raises `SetupError`.
  - Added the `CROATIAN_ONLY` set.
- `vibeflow/transcribe.py`:
  - With a `-turbo-hr` model and language `auto` (or empty), whisper-cli gets `-l hr`.
  - `make_transcriber` supports `backend = "parakeet"`, imported lazily.
- `vibeflow/parakeet.py` (new):
  - Pinned file list with sha256: Parakeet int8 encoder/decoder, `nemo128.onnx`, `vocab.txt`,
    `config.json`, plus `silero_vad.onnx`. All go in one folder.
  - `installed`, `download` (combined progress, `.part` + `os.replace`) and `delete`.
  - `ParakeetTranscriber` loads the model on a background thread when it is created, ignores the
    language setting, joins the VAD segments, and handles cancel by discarding the result.
  - If `onnx_asr` can't be imported or the files are missing, it returns a readable reason.
- `vibeflow/config.py`, `config.example.toml`: `[transcription.parakeet]` with `model_dir` and `threads`.
- `vibeflow/settings_ui.py`:
  - "Local (Parakeet v3)" backend radio and a hint that the language setting is ignored.
  - "(Croatian only)" label on the hr rows.
  - A `parakeet-tdt-0.6b-v3` row in the model manager with its own download and delete. Delete is
    blocked while Parakeet is the active backend.
  - The form only requires a whisper model when the backend is `local`.
- `vibeflow/tray.py`: "Parakeet v3 (local)" backend radio item.
- `requirements.txt`, `pyproject.toml`: `onnx-asr[cpu]`.
- `packaging/VibeFlow.spec`: bundles onnxruntime DLLs and onnx_asr data. Hidden imports
  `vibeflow.parakeet` and `onnx_asr`.
- `scripts/setup-whisper.ps1`: hr models with pinned URLs and a `Get-FileHash` check.
- `README.md`: Parakeet backend bullet and a "Croatian" section.
- Tests: `tests/test_whisper_setup.py`, `tests/test_transcribe.py`, `tests/test_settings_ui.py` and `tests/test_config.py`.
  They cover the pinned URL and filename, the checksum mismatch, `-l hr` forcing, the Parakeet
  unavailable/ok/segment-join/cancel paths with a fake `onnx_asr`, the form accepting `parakeet`, and
  the config round-trip.

### Deviations from the plan
- The Silero VAD file sits in the Parakeet folder, because onnx-asr `load_vad("silero", path=dir)` looks for it there.
- `vocab.txt` and `config.json` are not LFS files. They are pinned by commit and by a sha256 computed from the
  pinned download.
- The Parakeet folder comes from `[transcription.parakeet].model_dir`, not `models_dir/<name>`.
- `config_from_form` relaxed for non-`local` backends. Without this, Parakeet couldn't be picked when
  no ggml model was installed.
- Parakeet `threads` is in the config file only (it maps to onnxruntime `intra_op_num_threads` when > 0),
  not in the form.
- Added a tray menu entry, which the plan didn't mention, to keep the tray consistent with Settings.

### Verification
- `.venv\Scripts\python -m pytest -q`: 196 passed.
- `python -m vibeflow.whisper_setup --models large-v3-turbo-hr --dest .claude\temp\whisper`: real download,
  sha256 matches the pin.
- `parakeet.download()`: real download, all 6 files sha-verified.
- Direct transcriber smoke test, CPU, with the English SAPI voice because no Croatian voice is installed:
  - Parakeet: 0.2 s on a 7 s clip and 1.7 s on an 80 s clip (VAD path), both transcribed correctly.
  - hr fine-tune: mangles English as expected (Croatian-only). 4.7 s / 9.7 s.
- `scripts\build.ps1`: tests, PyInstaller and zip succeeded. `onnxruntime.dll`,
  `onnxruntime_providers_shared.dll`, `onnxruntime/` and `onnx_asr/` are present in `dist/VibeFlow/_internal`.

### Not run
- Launching the portable exe with Parakeet. The installed VibeFlow (`D:\Programs\VibeFlow`) was running,
  and the single-instance mutex blocks a second instance.
- Live hotkey dictation, and accuracy on real Croatian speech, which needs a human recording.
- Settings window download and delete against real files.
- `scripts/setup-whisper.ps1`.

### Follow-ups / limitations
- The hr ggml is a third-party conversion (0 downloads at pin time). It is sha-pinned, but its accuracy is
  unverified until tested on real Croatian.
- Parakeet adds about 60 MB of onnxruntime to the portable build. Its model is about 670 MB, downloaded
  separately.

Commit status: committed as f1f4343 on `VWT-03_croatian-asr-models` (not pushed).
