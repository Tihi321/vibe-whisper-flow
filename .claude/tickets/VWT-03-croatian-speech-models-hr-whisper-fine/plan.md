# VWT-03: Croatian speech models (hr Whisper fine-tune + Parakeet v3 backend)

Ticket folder: `.claude/tickets/VWT-03-croatian-speech-models-hr-whisper-fine/`.
Branch: `VWT-03_croatian-asr-models` (created from `origin/master` at 7e015d7). Implement via the global `implement` skill.

## Context

The user asked which models handle Croatian. Whisper already supports `hr`, and `hr` is already in the
Settings language list (`vibeflow/settings_ui.py:33`). The problem is accuracy: the default `small` model is
weak on Croatian. Research (FLEURS hr WER, lower is better):

| Model | hr WER | Notes |
|---|---|---|
| whisper large-v3 / large-v3-turbo | 11.1 / 12.7 | already downloadable in the app |
| GoranS/whisper-large-v3-turbo-hr-parla (Croatian fine-tune) | **8.7** | ggml build exists: `JoaoZaokk/whisper-large-v3-turbo-hr-parla-ggml` (Apache-2.0) |
| NVIDIA Parakeet-TDT-0.6B-v3 | 12.5 | 25 EU languages incl. hr, auto language detection, very fast on CPU via `onnx-asr` |

The user picked two things: (1) add the Croatian fine-tune as a downloadable whisper.cpp model, and
(2) add Parakeet v3 as a new local backend.

## Part 1: Croatian fine-tuned Whisper model

The model is a drop-in ggml file for the existing whisper.cpp backend.

**`vibeflow/whisper_setup.py`**
- Keep `MODELS: dict[str, int]`. Add `large-v3-turbo-hr` (874_188_075, q8_0, about the same as f16)
  and `large-v3-turbo-hr-q5` (574_041_195).
- Add `_EXTERNAL: dict[str, tuple[str, str]]`, which maps a model name to (url, sha256). Pin the URLs
  to the repo commit so the files can't change underneath us:
  - base `https://huggingface.co/JoaoZaokk/whisper-large-v3-turbo-hr-parla-ggml/resolve/8280f145eaae746f586e4d681504ca7e3ad95b0f/`
  - q8_0 `ggml-whisper-large-v3-turbo-hr-parla-q8_0.bin`, sha256 `29b73250e1f190bf60fb2394e7b6e76faed417aedbd622bdca6ccb90825be88c`
  - q5_0 `ggml-whisper-large-v3-turbo-hr-parla-q5_0.bin`, sha256 `945a3d2fe71af09fa170b36832821b7cfec0e1b600e62aac847dba2b16f2942c`
- `model_url(name)` returns the `_EXTERNAL` URL when there is one, otherwise the current ggerganov URL.
- `model_filename(name)` stays `ggml-{name}.bin`. Files get renamed on save (`ggml-large-v3-turbo-hr.bin`),
  so the existing `ggml-*.bin` glob in `_refresh_models` (`settings_ui.py:529`) picks them up unchanged.
- `download_model`: for pinned models, compute sha256 while streaming. The simplest way is an optional
  `hasher` argument on `_stream_to`. If the hash doesn't match, delete the `.part` file and raise
  `SetupError`.
- Add `CROATIAN_ONLY = {"large-v3-turbo-hr", "large-v3-turbo-hr-q5"}`. The fine-tune has lost
  English and other languages (the uploader's VERIFY.txt shows English being mangled).

**`vibeflow/transcribe.py`**: in `LocalWhisper._build_command`, if the model filename contains
`-turbo-hr` and the language is `auto`, pass `-l hr`. Auto-detect on a monolingual fine-tune is unreliable.

**`vibeflow/settings_ui.py`**: label the hr rows in the model manager, either with a "Croatian only"
suffix in the Size/Status column or with the display name `large-v3-turbo-hr (Croatian)`, keeping
`iid = name`.

**`scripts/setup-whisper.ps1`**: add both names to `$validModels`, plus the URL override and hash check
(`Get-FileHash`), so the script stays in sync with the Python port.

## Part 2: Parakeet-TDT-0.6B-v3 backend (onnx-asr)

**Dependency**: add `onnx-asr[cpu]` (0.12.0, which pulls in `onnxruntime`) to `requirements.txt` and
`pyproject.toml`. Do not use the `hub` extra, because we download the files ourselves.
Import `onnx_asr` lazily inside the backend, so the app still starts if it's missing; in that case
`available()` returns a readable reason.

**New `vibeflow/parakeet.py`**
- `PARAKEET_FILES`: int8 set from `istupakov/parakeet-tdt-0.6b-v3-onnx`, pinned to a commit. Fetch the
  commit sha and the LFS sha256 values from `https://huggingface.co/api/models/istupakov/parakeet-tdt-0.6b-v3-onnx/tree/main`
  during implementation. Files: `encoder-model.int8.onnx` (652 MB), `decoder_joint-model.int8.onnx` (18 MB),
  `nemo128.onnx`, `vocab.txt`, `config.json`. Total is about 670 MB.
- Silero VAD model files for long recordings. Parakeet only takes about 20–30 s per pass, and dictation
  can run up to 300 s. Check onnx-asr's VAD loader and the files it needs at https://istupakov.github.io/onnx-asr/
  and download them the same way.
- `download(dest_dir, progress, cancel)`: reuse `whisper_setup._stream_to` with the hash check, using a
  `.part` file and `os.replace`, the same pattern as `download_model`. Report progress across all files
  combined.
- `installed(dest_dir) -> bool`.
- `class ParakeetTranscriber` with the same interface as `Transcriber` (`transcribe(wav, language)`, `cancel()`):
  - `available()`: checks that onnx_asr can be imported and that the model files are present.
  - The model is loaded in a background thread when the transcriber is created
    (`onnx_asr.load_model("nemo-parakeet-tdt-0.6b-v3", path=dir, quantization="int8")`,
    then `.with_vad(...)`). `transcribe` waits for that load to finish. Loading takes seconds, so it
    must stay off the hotkey path.
  - `transcribe` ignores `language`, because Parakeet detects the language itself. It joins the VAD
    segment texts and runs the result through `_clean_output`.
  - `cancel()` sets a flag. onnxruntime inference can't be interrupted, so the result is thrown away
    and `TranscriptionCancelled` is raised afterwards.
  - Threads: pass `cfg.threads` through as onnxruntime `intra_op_num_threads` if onnx-asr exposes
    session options; otherwise use the default.

**`vibeflow/config.py`**: add `ParakeetConfig(model_dir="whisper/models/parakeet-tdt-0.6b-v3", threads=0)`
and a `TranscriptionConfig.parakeet` field. Backend comment becomes `local | parakeet | groq | openai`.
Add a `[transcription.parakeet]` section to `config.example.toml`.

**`vibeflow/transcribe.py`**: `make_transcriber` adds `backend == "parakeet"`, which lazily imports
`.parakeet`, checks `available()`, and raises `TranscriptionError(reason)` with the same behavior as local.

**`vibeflow/settings_ui.py`**
- Add `"parakeet"` to `BACKENDS` and a radio button "Local (Parakeet v3)".
- Model manager: add a `parakeet-tdt-0.6b-v3` row (about 670 MB). In `_refresh_models`,
  `_download_selected` and `_delete_selected`, branch on that iid to use `parakeet.installed/download`
  and `shutil.rmtree`. All other rows keep the whisper path.
- Add a hint label: "Parakeet detects the language automatically; Language setting is ignored."

**Packaging, `packaging/VibeFlow.spec`**: add `"vibeflow.parakeet"` to the hidden imports and
`collect_dynamic_libs("onnxruntime")` / `collect_data_files("onnx_asr")` if needed. This adds about
60 MB to the portable build. Model files stay outside the exe under `whisper/models/`.

**README.md**: a short section on Croatian: which model to pick, and that the hr model is Croatian-only.

## Tests (pytest, follow existing patterns)

- `tests/test_whisper_setup.py`: `model_url` for hr names returns the pinned URL; filename is
  `ggml-large-v3-turbo-hr.bin`; a sha mismatch raises `SetupError` and removes the `.part` file (mock `requests.get`
  as the existing tests do).
- `tests/test_transcribe.py`: hr model plus `auto` produces `-l hr` in the command; an explicit `en` stays `en`;
  `make_transcriber(backend="parakeet")` raises a readable error when files are missing; a fake `onnx_asr`
  module in `sys.modules` tests the ok path, the segment joining and the cancel path.
- `tests/test_settings_ui.py`: `config_from_form` accepts `backend="parakeet"`.
- `tests/test_config.py`: the `[transcription.parakeet]` section round-trips.

## Verification

1. `.venv\Scripts\python -m pytest`: everything green.
2. Real downloads: `python -m vibeflow.whisper_setup --models large-v3-turbo-hr` (hash verifies), then
   Parakeet download from Settings.
3. Accuracy and speed check. Record 3–4 Croatian sentences, plus one English sentence, to WAV
   (16 kHz mono). Run a throwaway script in `.claude/temp/` that transcribes each WAV with small, large-v3-turbo,
   large-v3-turbo-hr and parakeet, then prints the text and seconds per run. Report the table to the user.
4. Live run: `python run.pyw`, switch the backend in Settings to the hr model, then to Parakeet,
   dictate Croatian with the hotkey, and confirm the text is pasted. Also test a recording longer than 30 s on Parakeet (VAD path).
5. `scripts\build.ps1`, then launch the portable exe and confirm Parakeet transcribes. This catches
   missing onnxruntime DLLs.

## Implementation status

Checklist (all done unless noted):
- [x] Part 1: `whisper_setup.py` (MODELS + `_EXTERNAL` pinned URL/sha256, `CROATIAN_ONLY`, `hasher` arg on `_stream_to`, sha check in `download_model` that deletes `.part` and raises `SetupError`), `transcribe.py` (`-l hr` when model name contains `-turbo-hr` and language is auto/empty), `settings_ui.py` ("(Croatian only)" label in the Model column, iid unchanged), `scripts/setup-whisper.ps1` (valid names, pinned URL, `Get-FileHash` check).
- [x] Part 2: new `vibeflow/parakeet.py` (pinned file list incl. Silero VAD, `installed`, `download`, `delete`, `ParakeetTranscriber` with background load), `ParakeetConfig` + `TranscriptionConfig.parakeet`, `config.example.toml` section, `make_transcriber` branch, settings UI (backend radio, hint label, model-manager row with download/delete branch), `requirements.txt` + `pyproject.toml` (`onnx-asr[cpu]`), `packaging/VibeFlow.spec` (hidden imports, `collect_dynamic_libs("onnxruntime")`, `collect_data_files("onnx_asr")`), README (backend bullet + Croatian section).
- [x] Tests added in test_whisper_setup, test_transcribe, test_settings_ui, test_config.
- [x] `scripts\build.ps1` (orchestrator): tests + PyInstaller + zip succeeded; `onnxruntime.dll`, `onnxruntime_providers_shared.dll`, `onnxruntime/` and `onnx_asr/` are bundled in `dist/VibeFlow/_internal`.
- [ ] Not run: launching the portable exe with Parakeet (the user's installed VibeFlow was running and the single-instance mutex blocks a second instance), live hotkey dictation, real Croatian speech, Settings download/delete against real files, `setup-whisper.ps1`.

Verification evidence:
- `.venv\Scripts\python -m pytest -q`: 196 passed.
- `python -m vibeflow.whisper_setup --models large-v3-turbo-hr --dest .claude\temp\whisper`: downloaded 874,188,075 bytes, built-in check passed; independent `sha256sum` = 29b73250...be88c (matches pin).
- `parakeet.download()` into `.claude\temp\whisper\models\parakeet-tdt-0.6b-v3`: all 6 files fetched with sha256 verification (done, exit 0).
- Smoke (CPU, SAPI "Zira" English voice, no Croatian voice installed; 16 kHz mono; short = about 7 s, long = about 80 s):

| Model | short.wav | long.wav (80 s) | Notes |
|---|---|---|---|
| small | not run | not run | no `small` model present locally |
| large-v3-turbo-hr (q8_0, forced `-l hr`) | 4.7 s: "Helo, hvala, hvala, hvala, hvala, hvala, hvala, tj." | 9.7 s: mangled English ("Sentance n. 1. is here ...") | confirms Croatian-only |
| parakeet v3 (int8 + Silero VAD) | 0.2 s: "Hello. This is a short test of the dictation tool. Let us meet on Wednesday at 10 o'clock." | 1.7 s, 1183 chars, all 8 repetitions correct | model load 2.5 s in background thread |

Deviations / notes:
- The Parakeet files all go into one folder (`silero_vad.onnx` sits next to the ASR files), because onnx-asr's `load_vad("silero", path=dir)` globs `silero_vad.onnx` in the given dir. Non-LFS files (`vocab.txt`, `config.json`) are pinned by commit and additionally by a sha256 I computed from the pinned download. `nemo128.onnx` is an LFS file (has its own sha256).
- Parakeet dir comes from `[transcription.parakeet].model_dir` (UI uses `resolve_path` of it) rather than `models_dir/<name>`, so UI downloads land where the config reads.
- `config_from_form` no longer requires a whisper model name when the backend is not `local` (otherwise choosing Parakeet with no ggml model installed would be blocked).
- Threads: `cfg.threads > 0` is passed as onnxruntime `SessionOptions.intra_op_num_threads` via `sess_options`; 0 uses the default. Not exposed in the settings form (config file only).
- Tray menu: initially not changed; RESOLVED in follow-up: `vibeflow/tray.py` now has a "Parakeet v3 (local)" radio item between the whisper models and Groq. No tray test covers menu items, so none added.
- `_stream_to` hasher annotation uses a string type.

