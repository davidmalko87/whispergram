# whispergram

[![CI](https://github.com/davidmalko87/whispergram/actions/workflows/ci.yml/badge.svg?branch=master)](https://github.com/davidmalko87/whispergram/actions/workflows/ci.yml)
[![PyPI version](https://img.shields.io/pypi/v/whispergram.svg)](https://pypi.org/project/whispergram/)
[![PyPI downloads](https://img.shields.io/pypi/dm/whispergram.svg)](https://pypi.org/project/whispergram/)
[![Python](https://img.shields.io/pypi/pyversions/whispergram.svg?logo=python&logoColor=white)](https://pypi.org/project/whispergram/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20macOS%20%7C%20Linux-lightgrey.svg)](#)
[![Offline](https://img.shields.io/badge/100%25-local%20%26%20offline-success.svg)](#%EF%B8%8F-privacy)
[![Round-trip](https://img.shields.io/badge/round--trip-validated-success.svg)](#-round-trip-validated)
[![Last commit](https://img.shields.io/github/last-commit/davidmalko87/whispergram.svg)](https://github.com/davidmalko87/whispergram/commits/master)
[![GitHub issues](https://img.shields.io/github/issues/davidmalko87/whispergram.svg)](https://github.com/davidmalko87/whispergram/issues)

> **Telegram voice-to-text, locally.** Transcribe Telegram **voice and round video messages** with
> Whisper ([faster-whisper](https://github.com/SYSTRAN/faster-whisper)) and merge them into one
> searchable, LLM-ready chat transcript — **100% offline, no API key, no cloud.**

Every line is tagged by sender and timestamp, with voice notes transcribed inline next to the text:

```
[2026-06-20 12:33] Alex (voice 14s): hey, just finished the thing we talked about
[2026-06-20 12:35] You: nice, send it over
[2026-06-20 12:46] Alex (video-note 8s): here it is
[2026-06-20 12:47] You (photo): looks great
[2026-06-20 12:47] Alex (sticker 👍)
```

---

## Why?

An audio-heavy Telegram chat is unreadable and unsearchable — you cannot grep a voice note, and
you cannot hand a folder of `.ogg` files to an LLM. The alternatives are worse: Telegram Premium
transcribes one message at a time by hand, and cloud speech APIs upload your private audio to a
third party. **whispergram** transcribes **every** voice and video note in one pass, entirely on
your own machine, and weaves them back into the text timeline as a single file you can read,
search, or feed to a model.

---

## Features

| Feature | Description |
|---|---|
| **Voice + video notes** | Both `voice_message` and round `video_message` notes are transcribed inline with the text |
| **One merged file** | A single `merged_chat.md`, chronological, every line tagged `[time] sender` |
| **100% local & offline** | faster-whisper runs on your machine — no upload, no API key, no account |
| **Lossless mapping** | Stickers, photos, animations, documents, music, locations, polls and contacts appear as markers — nothing content-bearing is dropped |
| **Handles missing media** | Notes excluded from the export are clearly marked `[not exported]`, never fed to the model |
| **All text shapes** | Reconstructs plain, rich, and entity-based message text (links, mentions, custom emoji) |
| **Dry-run** | Preview the full merge with `--dry-run` — no model download, no GPU, instant |
| **GPU or CPU** | CUDA with automatic CPU fallback; a one-command Windows CUDA fix is built in |
| **Auto-detect** | Finds the export JSON (any filename) and the language per file |
| **Regular videos** | `--video-files` also transcribes ordinary video files' audio, not just round notes |
| **Photo OCR** | `--ocr` pulls text out of photos with local Tesseract — great for screenshots |
| **Tested** | 52 offline tests on the Python 3.9–3.13 CI matrix |

---

## Quick Start

### 1. Install

**Via PyPI (recommended):**

```bash
pip install whispergram
```

**Or clone for development:**

```bash
git clone https://github.com/davidmalko87/whispergram.git
cd whispergram
pip install -r requirements.txt
```

You also need **ffmpeg** on your PATH:

```bash
# Linux:  sudo apt install ffmpeg
# macOS:  brew install ffmpeg
# Windows: choco install ffmpeg   (or: winget install Gyan.FFmpeg)
```

### 2. Export your chat from Telegram

Telegram **Desktop** → open the chat → ⋮ menu → **Export chat history**:

- Format: **JSON**
- Tick **Voice messages** (and **Video messages** for round notes)

You get a folder with a `.json` file plus `voice_messages/` and `video_files/` subfolders.

### 3. Run

From **inside** the export folder:

```bash
whispergram
# or, without installing:
python whispergram.py
```

…or point it at the folder:

```bash
whispergram "path/to/ChatExport_2026-06-20"
```

The result is `merged_chat.md` in the export folder.

---

## Example output

```
[2026-06-20 12:33] Alex: did you get the files?
[2026-06-20 12:33] You: yep, check https://example.com thanks
[2026-06-20 12:34] Alex (voice 6s): one sec, recording the summary now ...
[2026-06-20 12:35] Alex (video-note 8s): [not exported]
[2026-06-20 12:35] You (sticker 😅)
[2026-06-20 12:36] Alex (photo): the whiteboard from today
```

---

## How each message appears

| Message type | In the merged file |
|---|---|
| Text | `[time] sender: message text` |
| Voice note | `[time] sender (voice 12s): <transcript>` |
| Round video note | `[time] sender (video-note 8s): <transcript>` |
| Voice/video note **with caption** | `[time] sender (voice 12s): <transcript> \| caption: <text>` |
| Voice/video not downloaded | `[time] sender (voice 12s): [not exported]` |
| Sticker | `[time] sender (sticker 😅)` |
| Photo (with caption) | `[time] sender (photo): caption` |
| Animation / GIF | `[time] sender (animation)` |
| Document | `[time] sender (file: report.pdf): caption` |
| Location / poll / contact | `[time] sender (location)` · `(poll)` · `(contact)` |
| Music / audio file | `[time] sender (audio: Artist - Title)` — transcribe with `--audio-files` |
| Regular video file | `[time] sender (video)` — transcribe the audio with `--video-files` |
| Photo **with `--ocr`** | `[time] sender (photo, text): <text found in the image>` |

Markers can be turned off with `--no-media-markers` (voice/video notes are always transcribed).

---

## ✅ Round-trip Validated

The merge has been **validated against a real 770-message Telegram export** (a live, audio-heavy
chat — not a synthetic fixture). Every dimension was diffed against the source JSON:

| Dimension | In export | In merged file | Result |
|---|---|---|---|
| Voice notes (downloaded) | 4 | 4 transcribed | ✅ |
| Round video notes (not downloaded) | 5 | 5 `[not exported]` | ✅ |
| Other media (stickers, photos, animations, videos, audio, …) | 107 | 107 markers | ✅ |
| Text messages | 654 | 654 | ✅ |
| **Messages dropped** | — | **0** | ✅ |

**All 770 messages map to 770 lines** — the per-type counts match the source exactly, and
not-exported notes are never sent to the model. (An earlier version silently dropped 88 of those
messages — every sticker, photo, and caption-less media item — leaving misleading gaps. The
round-trip is what surfaced it.)

> That export is private, so these counts were measured locally and are not reproducible from this
> repo. The synthetic export under [`tests/fixtures/`](tests/fixtures/) reproduces the same
> lossless mapping across every media type and guards it automatically in CI. A faithful merge is
> only proven once it has been run end-to-end and the output diffed back against every message type
> — structural validity alone is not enough.

---

## Known Limitations

These follow from the Telegram export format and from speech recognition itself — not from a lack
of effort in the tool:

| Area | Status | Notes |
|---|---|---|
| Round video notes | Audio only, if downloaded | Telegram often excludes the binary; those show `[not exported]` |
| Music / `audio_file` | Off by default | Opt in with `--audio-files`; songs are otherwise not run through ASR |
| Photo OCR | Text-in-image only | `--ocr` reads visible text (great for screenshots), not a description of the scene; needs Tesseract + language packs |
| Photo descriptions | Roadmap | A local vision model (`--describe`) to caption a photo's *content/meaning* is planned — kept local to preserve the no-cloud promise |
| Speaker labels | Sender only | Each note is attributed to its Telegram sender; no in-audio diarization |
| Timestamps | Minute resolution | Telegram exports `YYYY-MM-DDThh:mm`; seconds are not shown |
| Reactions / edits / replies | Not represented | The merged file is a clean reading transcript, not a full forensic dump |
| Transcription accuracy | Model-dependent | `large-v3` is best for uk/ru; `--lang` forces a language if auto-detect slips |

---

## Options

```bash
whispergram --device cpu --model large-v3-turbo   # no GPU, fast
whispergram --lang uk                             # force a language
whispergram --dry-run                             # preview the merge, no transcription
whispergram --audio-files                         # also transcribe music/long audio files
whispergram --video-files                         # also transcribe regular videos' audio
whispergram --ocr --ocr-lang ukr+rus+eng          # read text from photos (local Tesseract)
whispergram --out result.md                       # custom output path
```

| Flag | Default | Notes |
|---|---|---|
| `--device` | `cuda` | `cuda` or `cpu`; auto-falls back to CPU if the GPU fails |
| `--model` | `large-v3` | try `large-v3-turbo` or `medium` if CPU is slow |
| `--lang` | auto | force a code like `uk`, `ru`, `en` if auto-detect mislabels |
| `--out` | `merged_chat.md` | output file |
| `--audio-files` | off | also transcribe `audio_file` messages (music, long memos) |
| `--video-files` | off | also transcribe regular video files' audio track |
| `--ocr` | off | extract text from photos with local Tesseract OCR |
| `--ocr-lang` | `eng` | Tesseract language(s), e.g. `ukr+rus+eng` |
| `--no-media-markers` | off | omit `(sticker)` / `(photo)` / `(file)` markers |
| `--dry-run` | off | map the chat without loading a model or transcribing |
| `--setup-cuda-windows` | — | copy CUDA DLLs next to ctranslate2, then exit (Windows GPU fix) |

---

## GPU (CUDA) setup

**Linux / macOS:** with a working CUDA install it runs as-is on `--device cuda`.

**Windows** — the common pitfall is `RuntimeError: Library cublas64_12.dll is not found`:

1. Install the CUDA runtime wheels (no full CUDA Toolkit needed):
   ```bash
   pip install nvidia-cublas-cu12 nvidia-cudnn-cu12
   pip install -U "ctranslate2>=4.5"
   ```
2. If it *still* can't find the DLL, copy them next to CTranslate2 (the reliable fix):
   ```bash
   python whispergram.py --setup-cuda-windows
   ```
3. Or skip the GPU entirely: `--device cpu --model large-v3-turbo`.

> CTranslate2 loads cuBLAS/cuDNN lazily in native code that ignores `os.add_dll_directory`,
> which is why placing the DLLs inside the package dir is the dependable solution.

---

## FAQ

**How do I transcribe Telegram voice messages?**
Export the chat from Telegram Desktop as JSON (with voice messages), then run `whispergram` in the
export folder. Every voice note is transcribed with Whisper and merged into the text chat.

**Is it private / offline? Does my audio leave my machine?**
Yes, it is offline. Transcription runs locally with faster-whisper and needs no account or API key.
The tool makes no network calls with your data; faster-whisper downloads the speech model **once**
on first run, then works fully offline. Your chat audio and transcripts never leave your machine.

**Do I need a GPU?**
No. It runs on CPU (`--device cpu`); use `--model large-v3-turbo` for speed. A CUDA GPU is faster.

**Does it handle round video messages / video notes?**
Yes — round `video_message` notes are transcribed from their audio, just like voice notes. Regular
video files are transcribed too with `--video-files`.

**Can it read text from photos / screenshots?**
Yes — `--ocr` runs local Tesseract over photos and drops the extracted text inline as
`(photo, text): ...`. It reads text *in* the image (ideal for screenshots); describing a photo's
scene is on the roadmap via a local vision model. Everything stays offline.

**Which languages work?**
Any language Whisper supports. `large-v3` handles Ukrainian and Russian well; use `--lang uk` (or
`ru`, `en`, …) to force one if auto-detection slips.

**How is this different from Telegram Premium's transcription?**
Premium transcribes one message at a time, by hand, in the app. whispergram transcribes the
**entire** chat in one pass, offline, and produces a single searchable file.

---

## Project Structure

```
whispergram/
├── whispergram.py             # The tool: text reconstruction, mapping, transcription, CLI
├── requirements.txt           # Runtime dependency (faster-whisper)
├── pyproject.toml             # Packaging + ruff + pytest configuration
├── CHANGELOG.md
├── CONTRIBUTING.md
├── LICENSE
├── README.md
│
├── .github/
│   ├── workflows/
│   │   ├── ci.yml             # ruff + pytest on Python 3.9–3.13 (no transcription deps)
│   │   └── publish.yml        # tag v* → verify version → build → PyPI (trusted publishing)
│   ├── ISSUE_TEMPLATE/
│   └── dependabot.yml
│
└── tests/
    ├── test_whispergram.py    # 52 offline tests — no model download or GPU required
    └── fixtures/
        └── sample_export/
            └── result.json    # synthetic export (safe to commit; used by tests + CI)
```

---

## ⚠️ Privacy

This tool processes **private conversations**, and the transcripts it produces are just as
sensitive as the audio. Two rules:

- **Nothing leaves your machine.** Transcription is fully local; the tool makes no network calls
  with your data and needs no credentials.
- **Never commit your exports or transcripts.** The included `.gitignore` blocks chat data
  (`*.json`, audio files, `merged_chat.md`, `ChatExport_*/`) by default — keep it. Build your repo
  in a folder **separate** from any export, keep any `--out` path **inside** the export folder, and
  run `git status` before pushing to confirm only code is staged. The only data file in this repo
  is the synthetic fixture under `tests/fixtures/`.

---

## Requirements

- Python **3.9+**
- [ffmpeg](https://ffmpeg.org/) on your PATH
- [`faster-whisper`](https://pypi.org/project/faster-whisper/) >= 1.0 (`pip install -r requirements.txt`)
- For NVIDIA GPU on Windows: `nvidia-cublas-cu12`, `nvidia-cudnn-cu12`, `ctranslate2>=4.5`
- For `--ocr` (optional): the [Tesseract](https://github.com/tesseract-ocr/tesseract) binary on your PATH (with language packs, e.g. `ukr`, `rus`) plus `pip install whispergram[ocr]`

> The test suite needs none of the above — only `ruff` and `pytest`.

---

## Changelog

See [CHANGELOG.md](CHANGELOG.md) for the full version history.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for the development setup, the privacy rule, and the
versioning / release policy.

## License

[MIT](LICENSE)
