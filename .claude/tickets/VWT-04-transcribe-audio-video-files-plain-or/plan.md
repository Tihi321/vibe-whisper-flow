# VWT-04: Transcribe audio/video files (plain or timestamped)

Ticket folder: `.claude/tickets/VWT-04-transcribe-audio-video-files-plain-or/`.
Branch: `VWT-04_file-transcription` (created from `origin/master` at 0df59d0). Implement via the global `implement` skill.

## Checklist
- [x] 1. Segment type + `transcribe_segments` for LocalWhisper / Parakeet / CloudWhisper (transcribe.py, parakeet.py)
- [x] 2. `vibeflow/filetranscribe.py` (decode, orchestration, formatters)
- [x] 3. App wiring (`_file_transcriber` hook)
- [x] 4. Settings File tab
- [x] 5. Dependency + packaging + README (`scripts/build.ps1 -SkipTests` succeeded; FFmpeg DLLs present in `dist/VibeFlow/_internal/av.libs`)
- [x] Review fix: File tab engine line refreshes after Save
- [x] Tests pass: `.venv\Scripts\python -m pytest -q` -> 230 passed, 1 skipped (pre-existing intermittent Tk "no display" skip; 231 passed on other runs)
- [ ] Live verification. Done: real whisper-cli (b4938, ggml-small) on mp3 + m4a (offsets, progress, all four renders); real Parakeet (model from the installed app folder) on a 34.5 s h264+aac mp4 with the video stream first: 12 segments in 0.9 s, a parallel dictation call finished in 0.21 s. NOT done: Groq/OpenAI chunking (no key), GUI run of the File tab, frozen exe run (installed VibeFlow holds the single-instance mutex).

Deviations / notes
- Parakeet tests live in tests/test_transcribe.py (reuse its fake onnx_asr), not a new test_parakeet file.
- whisper.cpp is run with stdout=DEVNULL (only stderr is read) since results come from the JSON file.
- File tab "Done" line shows audio length from the last segment end (decode duration is not returned by transcribe_file).
- Progress posts to the UI are de-duplicated per whole percent (decode emits a callback per frame).
- Time rounding in formatters adds 1e-6 s to avoid float artefacts (1.2345 s -> 1235 ms).
- Files in this repo use CRLF; all edited/new files were kept CRLF.

## Context

VibeFlow only transcribes live mic dictation. The user wants to transcribe existing audio and video
files, with the transcript either as plain text or with timestamps. Decisions made with the user:

- UI: a new **File** tab in the Settings window (not a separate window).
- Output: shown in the tab with **Copy** and **Save as…** buttons (no auto-save).
- Formats: **Plain text** (default) / **Text with `[hh:mm:ss]` lines** / **SRT** / **VTT**. Timestamps are optional.
- Decoding: bundle **PyAV** (`av`, FFmpeg inside the wheel), so mp3/m4a/mp4/mkv/etc. work offline.

The file job uses the backend saved in Settings (whisper.cpp, Parakeet, Groq, OpenAI). LLM cleanup is
not applied to file transcripts (long text would blow the cleanup context; can be added later).

## Design

### 1. Shared segment type — `vibeflow/transcribe.py`
- Add `@dataclass Segment(start: float, end: float, text: str)` (seconds). Lives here because
  `parakeet.py` already imports from `transcribe.py`.
- Add `ProgressFn = Callable[[float], None]` (fraction 0..1 of the transcription stage).
- Each backend gets a new method, leaving the dictation `transcribe()` untouched:
  `transcribe_segments(wav, language, duration, progress, cancel: threading.Event) -> list[Segment]`.
  Raise `TranscriptionCancelled` when `cancel` is set. Text of each segment goes through `_clean_output`;
  drop empty segments.

**LocalWhisper (whisper.cpp)**
- Extend `_build_command(wav, language, segments=False)`. With `segments=True`: drop `-nt -np -otxt`,
  add `-oj` (JSON with `offsets.from/to` in ms) and `-pp` (progress on stderr). Keep `-l` logic (incl.
  the `-turbo-hr` → `hr` rule) and absolute paths.
- Run with Popen; a reader thread parses stderr lines `progress = NN%` → `progress(NN/100)`. Main loop
  polls `proc.poll()` + `cancel.is_set()` (kill on cancel). Parse `<base>.json` via a pure helper
  `_segments_from_whisper_json(data) -> list[Segment]`; delete the json in `finally`.
- Verify in implementation that `-oj` offsets are populated without `-nt` on the installed b4938 build.

**ParakeetTranscriber**
- Shared with dictation (the model is ~650 MB; don't load it twice). `self._model.recognize(path)` with
  VAD returns a lazy iterator of `SegmentResult(start, end, text)` (`onnx_asr/vad.py:17`).
- Take `self._lock` **per `next()` call**, not for the whole file, so a dictation press waits for at most
  one VAD segment (<~1 s) instead of the whole file. Check `cancel` between segments;
  `progress(seg.end / duration)`. Do not touch `self._cancelled` (that belongs to dictation).

**CloudWhisper (Groq/OpenAI)**
- 25 MB upload limit → split the 16 kHz mono PCM into ≤10 min WAV chunks (~19 MB). Pure helper
  `_chunk_bounds(samples, rate, max_s=600, search_s=20) -> list[tuple[int, int]]` cuts at the quietest
  0.5 s window in the last 20 s of each chunk to avoid splitting words.
- POST each chunk with `response_format=verbose_json`, timeout 300 s; offset each returned
  `segments[].start/end` by the chunk start. If the response has no `segments` (or a 400 for
  `verbose_json`, e.g. gpt-4o-transcribe models), retry that chunk with `json` and emit one
  chunk-level segment (log a warning). Cancel between chunks; progress = chunks done / total.

### 2. New module `vibeflow/filetranscribe.py`
- `FORMATS = ("plain", "timestamps", "srt", "vtt")`, `FORMAT_LABELS` ("Plain text", "Text with
  timestamps", "SRT subtitles", "VTT subtitles"), `FORMAT_EXT` (`.txt`, `.txt`, `.srt`, `.vtt`).
- `MEDIA_PATTERNS` for the file dialog: mp3 wav m4a aac flac ogg opus wma mp4 mkv mov avi webm.
- `decode_to_wav(src, dst, progress, cancel) -> float` (seconds): lazy `import av` (readable error if
  missing, same pattern as `parakeet._onnx_asr_import_error`), pick the first audio stream
  (`TranscriptionError("No audio track in <name>")` if none), `av.AudioResampler(format="s16",
  layout="mono", rate=16000)`, stream frames into a `wave` writer (no full-file buffering; flush the
  resampler at the end). Progress from `frame.time / container.duration`; check `cancel` per frame.
- `transcribe_file(src, transcriber, language, progress: Callable[[str, float | None], None], cancel)
  -> list[Segment]`: decode into `%TEMP%/vibeflow/file-<timestamp>.wav` (stage "Decoding"), then
  `transcriber.transcribe_segments(...)` (stage "Transcribing"), temp WAV removed in `finally`.
- Pure formatters: `render(segments, fmt) -> str`
  - plain: segment texts joined by spaces;
  - timestamps: `[hh:mm:ss] text` per line;
  - srt: numbered blocks, `00:01:02,345 --> 00:01:05,000`;
  - vtt: `WEBVTT` header, `00:01:02.345 --> 00:01:05.000`.

### 3. App wiring — `vibeflow/app.py`
- New method `_file_transcriber()`: if backend is `parakeet` and `self._transcriber` is set, return it
  (shared model); otherwise return a fresh `self._transcribe.make_transcriber(self.cfg.transcription)`
  so a file job never clobbers the dictation instance's `_proc`/cancel state. Raises
  `TranscriptionError` with the usual readable reason when the backend isn't installed.
- Pass it to `SettingsHooks` as `file_transcriber` in `_settings_window()` (`app.py:447`).

### 4. Settings "File" tab — `vibeflow/settings_ui.py`
- `TABS` gets `"file"` after `"transcription"`; `TAB_TITLES["file"] = "File"`. `SettingsHooks` gets
  `file_transcriber: Callable[[], Any]`.
- `_build_file(f)`; its variables are **not** registered via `self._var` (they must not affect
  `_collect`/`_is_dirty`/Save), and `_populate` must not clear the result:
  - File row: read-only entry + "Browse…" (`filedialog.askopenfilename`, Media + All files).
  - Format combobox (readonly, `FORMAT_LABELS`, default Plain text) and Language combobox
    (`LANGUAGES`, initialised from the saved config, not persisted).
  - Gray engine line, refreshed in `_populate`: e.g. "Engine: Local (whisper.cpp), ggml-small.bin —
    change it on the Transcription tab".
  - "Transcribe" (Accent) + "Cancel" buttons, determinate progress bar, status label
    ("Decoding… 40%", "Transcribing… 35%", "Done: 12:30 of audio in 1m 05s", errors).
  - Result `tk.Text` (height ~10, scrollbar) + "Copy" (clipboard) + "Save as…"
    (`asksaveasfilename`, initial dir = source folder, name `<stem><ext>` for the chosen format).
- Own worker (separate from the model-download `_busy`/`_cancel`): thread + its own
  `threading.Event`, results posted back with `self._post`. Keep the last `list[Segment]`; changing the
  format re-renders it without re-running.
- Make sure `_fit()` still sizes the window sensibly with the new tab.

### 5. Dependency and packaging
- Add `av` to `requirements.txt` and `pyproject.toml` dependencies.
- `packaging/VibeFlow.spec`: add `vibeflow.filetranscribe` and `av` to `hiddenimports`; add
  `collect_dynamic_libs("av")` (and `copy_metadata("av")` if needed) so the FFmpeg DLLs ship. Confirm with
  a real `scripts/build.ps1` build that a file transcribes in the frozen exe.
- README: short "Transcribe a file" section.

## Tests
- New `tests/test_filetranscribe.py`: formatters for all four formats (hour rollover, ms rounding, SRT
  comma vs VTT dot, numbering, empty list); `decode_to_wav` on a generated 44.1 kHz stereo WAV →
  16 kHz mono with matching duration (`pytest.importorskip("av")`); non-media file → readable error;
  cancel during decode.
- `tests/test_transcribe.py`: `_build_command(..., segments=True)` has `-oj -pp`, lacks `-nt -otxt`;
  `_segments_from_whisper_json`; `_chunk_bounds` (short file = one chunk, long file splits ≤ max and at
  the quiet spot); cloud `transcribe_segments` with a monkeypatched `requests.post` (offsets applied,
  json fallback).
- Parakeet `transcribe_segments` with a fake model iterator: segments + progress, cancel between
  segments, lock released between segments.
- `tests/test_settings_ui.py`: smoke that the File tab builds, editing its fields doesn't make the
  window dirty, and a run with a stub `file_transcriber` fills the result and re-renders on format change.

## Verification
1. `.venv\Scripts\pip install av` then `.venv\Scripts\python -m pytest`.
2. Run from source (`python -m vibeflow`). The installed `D:\Programs\VibeFlow\VibeFlow.exe` holds the
   single-instance mutex; ask the user before stopping it.
3. In Settings → File, transcribe a short mp3 and an mp4 with whisper.cpp and with Parakeet; check all
   four formats, Copy, Save as (open the .srt in a player), Cancel mid-run, and that hotkey dictation
   still works during a Parakeet file job.
4. Groq: a file longer than 10 min to exercise chunking and timestamp offsets (needs `GROQ_API_KEY`).
5. `scripts/build.ps1`, then repeat step 3 once in the portable build.
