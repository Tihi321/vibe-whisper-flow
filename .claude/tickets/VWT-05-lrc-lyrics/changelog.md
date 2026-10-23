# VWT-05 changelog

## 2026-10-23: Songs to timed LRC lyrics (for Spodrive SPD-07)

Branch `VWT-05_lrc-lyrics` (from `origin/master`).

- `vibeflow/filetranscribe.py`
  - New "Lyrics (LRC)" format. Headers `[ti]`/`[ar]`/`[al]` appear when known, plus `[length]` (the real duration) and `[re:VibeFlow]`. Lines are `[mm:ss.xx]`, with centisecond rounding and carry.
  - An empty timed line is added after gaps over 4 s, including the outro.
  - `output_path(src, fmt)` and `read_tags(src)` (title/artist/album/length; it never raises).
- `vibeflow/settings_ui.py`, File tab
  - Several files can be picked. The "Save next to each source file" checkbox is on for LRC and off for other formats, and a hint about Croatian models shows for LRC.
  - A batch runs one file at a time with "File k of n: name — stage". Cancel stops it, and failed files are listed while the batch continues.
  - Single-file LRC preview and save use the tags.
- `tests/test_filetranscribe.py`, `tests/test_settings_ui.py`: LRC render, rounding, gap and outro markers, `read_tags`, `output_path`, and a batch with one failing file.
- `README.md`: a "Song lyrics for Spodrive" section.

**Deviation:** `length` comes from the container duration, falling back to the last segment's end when unknown. This came from the review.

**Verification:** `.venv\Scripts\python.exe -m pytest tests -q` gives 243 passed. Not run: a GUI batch with real songs, Cancel pressed mid-batch, and importing the output into Spodrive.

**Status:** committed and pushed as `4653e48` on `VWT-05_lrc-lyrics`. No PR opened yet.
