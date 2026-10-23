# VWT-05: Songs to timed LRC lyrics (for Spodrive)

> Branch: `VWT-05_lrc-lyrics` (from `master`). Part of Spodrive ticket SPD-07 (section G of `spodrive/.claude/tickets/SPD-07-now-playing-panels-lyrics-fullscreen/plan.md`).

## Context

Spodrive (the user's Drive music player) has no lyrics for most of the user's Croatian kids' songs. VibeFlow transcribes them locally into timed `.lrc` files, which Spodrive imports and matches by title/artist tags, then by file name.

## G. VibeFlow: songs → timed LRC (repo `vibe-whisper-flow`, ticket `VWT-05`)

The File tab already decodes audio with PyAV and gets timed segments (`filetranscribe.transcribe_file` → `transcribe_segments` for whisper.cpp and Parakeet). It renders txt/SRT/VTT. Add:
- `vibeflow/filetranscribe.py`: an `"lrc"` format (`FORMATS`, `FORMAT_LABELS["lrc"] = "Lyrics (LRC)"`, `FORMAT_EXT[".lrc"]`).
  - `render(segments, "lrc", meta=None)` writes `[ti:]`, `[ar:]`, `[al:]`, `[length:mm:ss]` and `[re:VibeFlow]` headers, then a `[mm:ss.xx] text` line per segment.
  - It inserts an empty timed line (`[mm:ss.xx]`) at a segment's end when the gap to the next one is over 4 s, so Spodrive shows ♪ during instrumental parts.
  - `read_tags(src)` reads title/artist/album from `container.metadata` with PyAV. The tags are what Spodrive matches on first, since Drive tracks have the same tags.
- File tab (`settings_ui.py` `_build_file`): allow **several files** (`askopenfilenames`). With more than one file, or with the new "Save next to each source file" checkbox (on by default for LRC), each result is written as `<source stem>.lrc` beside its source, one after another, with "File k of n" progress. Cancel stops the batch. Single-file preview, copy and save stay as they are.
- Recommended model hint in the tab when LRC is chosen: `large-v3-turbo-hr` for Croatian songs, Parakeet v3 for mixed languages. Music under the vocals lowers accuracy. The LRC is a draft you can fix in any text editor before importing.
- Tests: `tests/test_filetranscribe.py` (or the existing file) gets LRC render cases (timestamps, gap markers, tag headers, centisecond rounding), a `read_tags` test with a missing-tags fallback, and a batch output-path test.
- README: a "Song lyrics for Spodrive" section covering transcribe → import in Spodrive.


## Verification

- `pytest` passes (new LRC render, read_tags and batch output-path tests).
- Manual: File tab, format LRC, pick 3 songs, check that `<stem>.lrc` is written next to each source.

## Implementation status

- [x] LRC format + read_tags (`filetranscribe.py`; also `output_path`; `render(..., meta=None)` stays backward compatible). Verified by pytest.
- [x] Batch / save-next-to-source in File tab (`settings_ui.py`): `askopenfilenames`, checkbox, hint, "File k of n: name — stage", Cancel, per-file errors. Automated test covers batch + error continue; GUI run with real songs and Cancel mid-batch NOT verified (manual).
- [x] Tests: LRC render, rounding carry, gaps, headers, read_tags (non-media, untagged WAV, tagged MKA), output_path, UI batch test. `pytest tests -q`: 242 passed.
- [x] README: "Song lyrics for Spodrive" section.

Deviations: render takes meta keys title/artist/album plus optional `length` (seconds); `read_tags` also returns `length` from the container duration (omitted when unknown); the UI uses it and falls back to the last segment end only if absent. Single-file LRC preview/Save as also use tags via `_file_meta`. Picking a format sets the "Save next to each source file" checkbox: on for LRC, off for any other format.
