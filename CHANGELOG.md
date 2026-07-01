# Changelog

All notable changes to this project will be documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/) and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [1.3.1] - 2026-07-01

### Fixed
- **Crash on a full Instagram export** (`AttributeError: 'list' object has no attribute 'get'`).
  Pointing `--menu` or a bare run at an Instagram "Download your information" tree crashed because some
  export JSONs (e.g. `messages/your_chat_information.json`) are top-level **lists**, not objects.
  Hardened every JSON-shape assumption across the discovery *and* transcription paths — `is_instagram_export`,
  `_chat_summary`, `_has_export_json`, and `_process_export` now skip any JSON that isn't a dict, and
  `_normalize_instagram` tolerates non-dict message/media/sticker/share entries instead of crashing.
  Discovery also isolates per-folder errors so one odd file can never abort a whole-tree scan.
  Verified end-to-end on a real 412-file export: scans cleanly to 411 chats, and individual threads
  transcribe without error.

---

## [1.3.0] - 2026-07-01

### Added
- **Date range in the `--menu` chat picker.** Each discovered chat now shows the span of its messages
  (`YYYY-MM-DD` for a single day, else `first..last`) alongside the voice/photo/video counts, so
  same-named exports — e.g. several `Анастасія` folders from different days — are easy to tell apart.
- **`--sort` for the menu order**: `voice` (most voice notes, default), `messages` (most messages),
  `recent` (most recent last message), or `name` (A-Z) — e.g. `whispergram --menu --sort recent`.

---

## [1.2.0] - 2026-07-01

### Fixed
- **GPU runs hanging at `0%` on 4 GB cards.** With current CTranslate2 (cuDNN 9), `large-v3` in
  float16 (~3 GB weights + workspace) may not fit a 4 GB GPU, and CTranslate2 **hangs** at
  `large-v3 on cuda (float16)` / `0%` instead of erroring. Fixed by automatic int8 selection (below).
  CPU runs were never affected.

### Added
- **`--compute-type`** (`auto` | `float16` | `int8_float16` | `int8` | `float32`) to choose
  CTranslate2's precision, plus **automatic int8 on low-VRAM GPUs**. whispergram now reads free VRAM
  (via `nvidia-smi`) and, below ~5 GB, loads `int8_float16` (~1.6 GB) automatically — so `large-v3`
  fits and runs on the GPU with near-identical quality, printing a one-line note. `auto` still means
  int8 on CPU and float16 on a roomy GPU; pass `--compute-type float16` to force the old behavior.

---

## [1.1.0] - 2026-07-01

### Added
- **Auto-open the chat picker in a parent folder.** Running `whispergram` in a folder that isn't a
  chat export itself but *contains* exports below it — an Instagram `your_instagram_activity` root,
  or a folder holding several Telegram `ChatExport_*` folders — now automatically opens the
  interactive picker (the `--menu` experience) instead of printing "no .json export found" and
  stopping. Without an interactive terminal (a cron job or a pipe) it prints a one-line hint to
  re-run with `--menu` rather than hanging. Running directly inside a single export folder is
  unchanged. `run_menu` accepts the already-discovered chats, so the folder is scanned only once.

---

## [1.0.0] - 2026-07-01

First **stable** release. whispergram now treats **Instagram DMs as a first-class platform** alongside
**Telegram**, with balanced, up-to-date documentation for both. No changes to the transcription/merge
engine or the CLI surface — existing commands and output are unchanged; this release marks the tool
as stable.

### Changed
- **Documentation rebalanced for two platforms.** The README gives Telegram and Instagram equal,
  first-class treatment — parallel export guides (Telegram Desktop JSON *and* Instagram "Download your
  information"), platform-agnostic run examples, a dedicated Instagram how-to, and both platforms
  covered across the feature list, round-trip section, limitations and FAQ.
- **Round-trip validated on both platforms** against real exports: a 20,156-message Telegram export
  (→ 20,136 transcript lines, zero dropped) and an 1,860-message Instagram thread (141 voice notes
  transcribed, zero dropped). Confirmed that Instagram voice notes transcribe **by default** (no
  `--audio-files` needed) and that the progress-bar total always equals the real work performed.
- **Packaging metadata**: `description` and `keywords` now cover Instagram / direct messages, and the
  project's Development Status is promoted to **Production/Stable**.

### Fixed
- **`.gitignore` hardening**: also ignores `round_video_messages/`, `video_messages/` and
  `*_thumb.jpg`, so Telegram round-video thumbnails can never be committed alongside a private export.

---

## [0.10.0] - 2026-06-30

### Added
- **Interactive `--menu`** — the easy way, no flags to remember. Run `whispergram --menu` in a folder
  that holds your exports and it scans for every **Telegram and Instagram** chat, lists them with
  platform + name + voice/photo/video counts (voice-heavy first), and lets you pick which to
  transcribe plus a quality preset: **"Everything, best models"** (recommended default — transcribe
  voice+video, describe photos/stickers/GIFs, OCR), "Voice & video only", or Custom. Scanning a real
  260-thread Instagram inbox takes ~4 s.

---

## [0.9.0] - 2026-06-30

### Added
- **Instagram DM support.** Point whispergram at an Instagram messages export thread folder
  (`your_instagram_activity/messages/inbox/<thread>/`) and it's auto-detected and merged with the
  same pipeline — voice messages transcribed (Whisper), photos/videos/GIFs described (Qwen2-VL/BLIP),
  shared Reels/posts rendered inline as `[shared reel/post by <author>: <link>]` markers. Instagram's
  JSON quirks are handled: the latin-1/UTF-8 **mojibake** is repaired (Cyrillic/emoji read correctly)
  and paginated `message_*.json` files are merged and sorted chronologically. Validated against a real
  6.6k-message export — every media item mapped, all media paths resolved, zero mojibake leaked.

### Notes
- Instagram doesn't include shared Reels' video in the export (only the link), so reels appear as
  markers, not transcripts. End-to-end-encrypted chats need Instagram's separate encrypted-chat
  download, not the standard "Download your information" export.

---

## [0.8.4] - 2026-06-26

### Added
- **Keeps the machine awake during a run** so a long overnight job isn't interrupted by idle-sleep
  (Windows; a no-op on other OSes). It blocks idle-sleep only, not closing the laptop lid. Sleep is
  restored when the run finishes. (A near-complete ~19 h run had stopped at 98% to idle-sleep — the
  resume cache meant nothing was lost, but the run now simply doesn't get cut off.)

---

## [0.8.3] - 2026-06-26

### Fixed
- **A single unreadable audio/video file no longer aborts the whole folder.** The transcription path
  now catches per-file errors (e.g. `IndexError` from a video with no audio stream) and marks just
  that item `[transcription failed]`, mirroring the describe path — so a large export with one bad
  file still completes instead of dying mid-queue.
- **1-frame video stickers (`.webm`) no longer error with "division by zero"** during frame
  sampling; the frame picker now handles the single-frame case (factored into a tested
  `_sample_indices` helper).

---

## [0.8.2] - 2026-06-26

### Fixed
- **`--ocr` now finds Tesseract automatically on Windows** even when it isn't on PATH (the
  UB-Mannheim installer doesn't add it) — checks the standard `C:\Program Files\Tesseract-OCR`
  locations. OCR still needs the language packs for your `--ocr-lang` (e.g. `ukr`, `rus`).
- OCR failures are reported **once** instead of once per photo, so a misconfigured Tesseract no
  longer floods the output (photos are still scene-described regardless).

---

## [0.8.1] - 2026-06-26

### Fixed
- Telegram animated stickers (`.tgs`, a Lottie vector format no local vision model can open) are now
  left as plain `(sticker)` markers instead of spamming `describe failed on ….tgs` for each one.
  They're also no longer counted as describe jobs, so the progress bar total stays accurate.

---

## [0.8.0] - 2026-06-26

### Added
- **`--batch-size N`** — batched transcription via faster-whisper's `BatchedInferencePipeline`, a
  large speedup on a **GPU** (try `8` or `16`) using the same model weights. Each chunk is decoded
  independently (slightly less cross-segment context), so the default stays `0` = sequential — the
  best quality, especially for connected Ukrainian/Russian speech.

### Fixed
- Export JSON is read as `utf-8-sig`, so a `result.json` with a leading UTF-8 BOM no longer crashes
  the run with `JSONDecodeError`.

---

## [0.7.0] - 2026-06-26

### Added
- **Resumable runs.** Every transcript/caption is cached to `.whispergram_cache.json` in the export
  folder and flushed after each file, so closing the terminal or a crash no longer loses progress —
  re-running continues where it left off (done files are skipped). `--no-cache` disables it; the
  cache key includes the model, so switching models recomputes. Only successful (non-empty) results
  are cached, so a transient model failure never poisons later runs, and the key uses each file's
  export-relative path (not just its name) so same-named files in different subfolders can't collide.
- **Queue multiple folders.** `whispergram folderA folderB folderC` processes them **sequentially**
  (GPU-safe — no parallel VRAM pile-up) and **loads the models only once**, reusing them across
  folders. `--out-dir DIR` collects each transcript as `DIR/<chat name>.md`; otherwise each folder
  keeps its own `merged_chat.md`. Two chats with the same name are disambiguated (`Work (2).md`)
  instead of overwriting, and a folder that fails (corrupt export, write error) is skipped — the rest
  of the queue still runs and the exit code is non-zero.
- **Progress bar** with live file counts + ETA (`28/47 [02:14<01:31], audio_28.ogg`) via tqdm (now a
  declared dependency); falls back to a `[28/47]` counter line if tqdm is unavailable.

### Changed
- The per-file `transcribing …` / `describing …` prints are replaced by the single progress bar.
- `tqdm` is now a core dependency (was relied on transitively).
- `--out` and `--out-dir` are mutually exclusive (`--out` names one file; `--out-dir` collects a
  queue).

---

## [0.6.0] - 2026-06-25

### Changed
- **The best *installed* describer is now used by default — no flag needed.** If the `[describe-hq]`
  extra (Qwen2-VL) is present, photos **and stickers + GIFs** are captioned automatically; otherwise
  the lighter BLIP (`[describe]`) captions photos. `--describe-hq` still forces HQ, `--no-describe`
  turns captioning off. So: install the quality you want, then just run `whispergram`.

### Added
- **Clear error for the Windows PyTorch/cuDNN conflict.** A CUDA build of torch can clash with
  faster-whisper's cuDNN (cryptic `OSError: [WinError 127] … cudnn_*.dll` on import). whispergram now
  catches it and prints the one-line fix (reinstall CPU torch) and points to the new README
  **GPU on Windows** section, which documents the two stable GPU configurations.

### Docs
- Telegram export guide: ticking **Stickers / GIFs** is now worthwhile when you use `--describe-hq`
  (so the files download and can be captioned), where before it was pointless.

---

## [0.5.0] - 2026-06-25

### Added
- **`--describe-hq`** — a high-quality describer (Qwen2-VL-2B) that is far better than BLIP on
  cartoons, characters and *actions*, and that **also captions stickers and GIFs/animations**.
  GIFs/animations are read **multi-frame** (several evenly-sampled frames), so it captures the
  motion rather than guessing from one still. Renders as `(animation, described): ...` and
  `(sticker 😅, described): ...`. Opt-in via `pip install whispergram[describe-hq]`. Heavier
  (~4.4 GB) and slow on CPU; fast on a CUDA GPU. Verified end-to-end on a real sticker + GIF with
  torch 2.12 + transformers 5.12.

### Changed
- `build_transcript` gained `media_describe` / `describe_media` to caption non-photo media; the
  default behaviour (BLIP, photos only) is unchanged. The lighter default describer stays BLIP.

---

## [0.4.2] - 2026-06-25

### Added
- **`--offline`** — forces the model libraries to use only already-downloaded weights and make
  **zero network calls** (`HF_HUB_OFFLINE` / `TRANSFORMERS_OFFLINE`). With telemetry now off by
  default, this gives a provable "no network, no telemetry" run once the models are cached.
- **`--describe-model`** — pick the BLIP caption model, e.g.
  `Salesforce/blip-image-captioning-base` for a faster/lighter run.

### Changed
- **Hugging Face telemetry is disabled by default** (`HF_HUB_DISABLE_TELEMETRY=1`) — the libraries
  no longer send anonymized usage pings (model/library names + versions; never your content).
- **Default caption model is now BLIP-large** (`Salesforce/blip-image-captioning-large`) for
  noticeably richer captions than BLIP-base. Verified end-to-end on torch 2.12 + transformers 5.12.
- Photo captioning now **degrades gracefully** if the model can't be loaded (e.g. `--offline` with
  nothing cached): photos fall back to a `(photo)` marker instead of crashing the run.

---

## [0.4.1] - 2026-06-25

### Fixed
- **Photo descriptions now actually run on transformers 5.x.** The 0.4.0 describer loaded SmolVLM
  through the `Auto*` classes, which fail on transformers 5.x with
  `ValueError: Unrecognized image processor` (the model's `preprocessor_config.json` no longer
  resolves through `AutoProcessor` / `AutoImageProcessor`, and the `image-to-text` pipeline task was
  removed in 5.x). Switched to **BLIP** (`Salesforce/blip-image-captioning-base`, BSD-3) loaded via
  its dedicated `BlipProcessor` / `BlipForConditionalGeneration` classes — no Auto-resolution.
  Verified end-to-end on torch 2.12 + transformers 5.12. Captions are a short scene gist; the
  `[describe]` extra no longer needs `num2words`.

---

## [0.4.0] - 2026-06-25

### Changed
- **Photo scene captions are now ON by default** and run automatically whenever the optional
  `[describe]` extra is installed — no flag needed. Pass **`--no-describe`** to skip them (no model
  load or download). If the extra isn't installed, photos fall back to a plain `(photo)` marker with
  a one-line hint, so a default install never breaks. The model loads **lazily on the first photo**,
  so a photo-less chat never triggers the download.
- **Describe backend switched from llama.cpp to transformers + SmolVLM-500M.** This fixes the real
  Windows failures the llama.cpp path hit: `llama-cpp-python` needing a C++ compiler to build, and a
  `STATUS_ILLEGAL_INSTRUCTION` crash from CPU-SIMD-mismatched prebuilt wheels. `transformers` +
  `torch` ship prebuilt wheels for every platform, dispatch CPU instructions at runtime, and use the
  GPU automatically when present.

### Removed
- The opt-in `--describe` flag — captioning is the default now; use `--no-describe` to opt out.

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
