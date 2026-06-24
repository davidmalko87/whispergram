# Changelog

All notable changes to this project will be documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/) and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [0.3.1] - 2026-06-25

### Changed
- Documentation and package metadata now lead with **photo support**: the README hero and
  example-output blocks show described + OCR'd photos (`(photo, described): <scene> | text: ...`),
  the tagline and PyPI description cover OCR and the local vision model (`--describe`) alongside
  voice/video, and `ocr` / `image-captioning` / `screenshot` keywords were added. No code changes.

---

## [0.3.0] - 2026-06-25

### Added
- **`--describe`** — extract the *meaning/scene* of a photo or screenshot with a small **local
  vision model** (SmolVLM2-500M run via llama.cpp — Apache-2.0, **no torch**, ~500 MB downloaded
  once then fully offline). Renders inline as `(photo, described): a whiteboard of sprint tasks`.
  Opt-in: `pip install whispergram[describe]`. **Composes with `--ocr`** →
  `(photo, described): <caption> | text: <in-image text>`. Captions are short, English, and
  best-effort — a guess at the scene, never literal content; `--ocr` remains the source of truth
  for text inside an image. This completes the "Photo descriptions" roadmap item from 0.2.0.

### Changed
- Photo handling now combines an optional scene describer and OCR through a small pure
  `_photo_reader` helper (6 new offline tests, 58 total). `build_transcript` is unchanged.

---

## [0.2.0] - 2026-06-25

### Added
- **`--video-files`** — also transcribe the audio track of regular video files (not just round
  `video_message` notes), through the same faster-whisper path. Off by default; silent GIFs
  (`animation`) stay markers.
- **`--ocr`** (with **`--ocr-lang`**) — extract text from photos with **local Tesseract OCR** and
  place it inline as `(photo, text): ...`. Fully offline; needs the Tesseract binary plus
  `pip install whispergram[ocr]` (`--ocr-lang ukr+rus+eng` for Cyrillic screenshots). Off by
  default; a photo with no readable text falls back to the plain `(photo)` marker, and missing
  photos are never sent to the OCR engine.
- Injectable `describe(path)` photo describer mirroring the transcriber, so the new logic is
  exercised offline with a fake in the test suite (now 52 tests).

### Changed
- `build_transcript` gained `video_files`, `describe`, and `photo_label` keyword arguments; all
  default to the previous behaviour, so existing usage is unchanged.

---

## [0.1.0] - 2026-06-23

Initial public release of **whispergram**. Hardened from a single working script into a tested,
packaged tool, with every fix below verified against a real 770-message Telegram export.

### Added
- **Transcribe Telegram voice notes and round video notes** locally and offline with
  faster-whisper, merged into the text chat as one chronological, sender/timestamp-tagged file.
- **`--dry-run`** — map the whole chat *without* loading a model or transcribing. Lets you
  preview the merge instantly and verify the mapping with no GPU and no model download.
- **Media markers** — stickers, photos, animations, documents, music, **locations, polls and
  contacts** now appear as `(sticker ...)`, `(photo)`, `(file: name.pdf)`, `(location)`, etc.,
  instead of vanishing from the timeline. Disable with `--no-media-markers`.
- **`--audio-files`** — opt in to also transcribe `audio_file` messages (music / long memos);
  off by default so songs are not run through speech recognition.
- **`--version`** flag, and a `whispergram` console entry point (installable via pip).
- **Offline `pytest` suite** (44 tests) covering text reconstruction across all three Telegram
  text shapes, missing-media detection, every media marker, chat interleaving, caption suffixes,
  the audio-file opt-in, JSON discovery tie-breaks, and the full `main()` CLI path. Runs on the
  Python 3.9–3.13 CI matrix with **no** transcription deps.
- **CI** (ruff + pytest) and a tag-triggered **PyPI publish** workflow (trusted publishing) that
  refuses to publish if the git tag and `__version__` disagree.

### Fixed
- **Whole classes of media were silently dropped** — 88 of 770 messages in a real export
  (stickers, photos, animations with no caption) produced no output line at all, and locations,
  polls and contacts would have been dropped too. They are now represented by a marker, so nothing
  content-bearing disappears.
- **`extract_text` could crash on a `null` text value or a non-dict entity** — text
  reconstruction is now fully defensive: a `null` run or a malformed entity contributes `""`
  instead of aborting the whole file. Covers `text_entities` (preferred), a plain `str`, and a
  mixed `list` of strings + entity dicts.
- **The module could not be imported without faster-whisper installed** — the transcription
  dependency is now imported lazily, so the mapping logic (and the tests) load with zero heavy
  deps, and `--dry-run` works on a machine with no GPU and no model.
- **`find_json` could pick the wrong file** — it now prefers an exact `result.json` before any
  substring match.
- Not-exported documents are now flagged `[not exported]` in their marker; output ends with a
  trailing newline; the export folder is validated before work; console output is forced to UTF-8
  so non-ASCII filenames never crash the run on Windows.

### Changed
- Refactored the transcription loop out of `main()` into pure, injectable functions
  (`build_transcript`, `extract_text`, `media_marker`, `is_missing_media`) for testability.
