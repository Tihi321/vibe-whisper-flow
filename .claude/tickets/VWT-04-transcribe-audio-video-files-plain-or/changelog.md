# VWT-04 changelog

## 2026-10-23: transcribe audio/video files (plain or timestamped)

VibeFlow can now transcribe an audio or video file from a new **File** tab in Settings. Output is
plain text (default), text with `[hh:mm:ss]` lines, SRT or VTT, with Copy and Save as.

**Repo:** vibe-whisper-flow. **Branch:** `VWT-04_file-transcription`, from `origin/master` at 0df59d0.

### Files changed
- `vibeflow/transcribe.py`
  - New `Segment(start, end, text)` and `ProgressFn`.
  - `LocalWhisper.transcribe_segments`: runs whisper-cli with `-oj -pp`, reads progress from stderr, can be cancelled (kills the process) and parses the JSON offsets (`_segments_from_whisper_json`).
  - `CloudWhisper.transcribe_segments`: splits audio into chunks of 10 min or less, cutting at the quietest 0.5 s in the last 20 s (`_chunk_bounds`). Sends each chunk with `verbose_json` and shifts the timestamps by the chunk offset. Falls back to `json` with chunk-level timing.
  - The dictation `transcribe()` paths are unchanged.
- `vibeflow/parakeet.py`: `ParakeetTranscriber.transcribe_segments` iterates the VAD segments and holds the model lock per segment, so dictation can still run during a file job. Supports progress and cancel between segments.
- `vibeflow/filetranscribe.py` (new):
  - PyAV decoding of the first audio track to a temp 16 kHz mono WAV. Streamed, cancellable, with readable errors for a missing PyAV, an unreadable file or no audio track.
  - `transcribe_file` orchestration.
  - `render()` for the plain, timestamps, srt and vtt formats.
- `vibeflow/app.py`: `_file_transcriber()` reuses the shared Parakeet instance and builds a private instance for the other backends. It is passed to Settings as a hook.
- `vibeflow/settings_ui.py`: new File tab between Transcription and Cleanup.
  - Fields: file picker, format, language and an engine line.
  - Controls: Transcribe/Cancel, a progress bar and status, a result box, Copy and Save as.
  - Its state doesn't affect the Save/dirty logic.
  - Changing the format re-renders without re-running.
  - The engine line refreshes on open and after Save.
- `requirements.txt`, `pyproject.toml`: add `av`.
- `packaging/VibeFlow.spec`: hidden imports `vibeflow.filetranscribe` and `av`; bundle the PyAV/FFmpeg DLLs.
- `README.md`: "Transcribe a file" section.
- Tests:
  - New `tests/test_filetranscribe.py`.
  - `tests/test_transcribe.py`: whisper `-oj` command and JSON parsing, chunk bounds, cloud offsets and fallback, Parakeet segments, lock and cancel.
  - `tests/test_settings_ui.py`: File tab smoke, the dirty check, run, re-render and engine refresh after save.

### Deviations from the plan
- Parakeet tests are in `tests/test_transcribe.py`, which reuses its fake onnx_asr.
- The "Done" status takes the audio length from the last segment's end.
- UI progress updates are de-duplicated per whole percent.

### Verification
- `.venv\Scripts\python -m pytest -q`: 230 passed, 1 skipped. The skip is the existing Tk test that intermittently reports "no display"; other runs gave 231 passed.
- Real whisper.cpp (b4938, ggml-small) on generated speech encoded as mp3 and m4a: offsets, progress parsing and all four formats were correct.
- Real Parakeet on a 34.5 s h264+aac mp4: 12 segments in 0.9 s. A dictation call running in parallel finished in 0.21 s.
- `scripts/build.ps1 -SkipTests` succeeded and the FFmpeg DLLs are in `dist/VibeFlow/_internal/av.libs`.

**Not run:**
- Groq/OpenAI on a file longer than 10 min (needs an API key; only mocked tests).
- Clicking through the File tab in the running app.
- Running the frozen exe (the installed VibeFlow holds the single-instance mutex).
- Cancelling a real Parakeet job: the test file finished before the cancel fired. Cancel is covered by unit tests.

### Follow-ups and limitations
- No LLM cleanup on file transcripts.
- Plain format is one paragraph.
- Cloud backends whose model doesn't return `verbose_json` segments (e.g. gpt-4o-transcribe) get coarse, chunk-level timestamps.

**Commit status:** uncommitted.
