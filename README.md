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

> **A Telegram chat — voice, video *and* photos — as one searchable transcript, fully local.**
> Transcribe voice & round-video notes with Whisper ([faster-whisper](https://github.com/SYSTRAN/faster-whisper)),
> **read text from screenshots with OCR**, and **caption photo scenes with a local vision model** —
> all merged into one chronological, LLM-ready file. **100% offline, no API key, no cloud.**

Every line is tagged by sender and timestamp — voice, video **and photos** turned into readable text:

```
[2026-06-20 12:33] Alex (voice 14s): just finished the auth flow, take a look
[2026-06-20 12:35] You: nice, send the diagram
[2026-06-20 12:46] Alex (photo, described): a hand-drawn architecture diagram on a whiteboard | text: Login -> API -> DB
[2026-06-20 12:47] You (video-note 6s): looks great, let's ship it
[2026-06-20 12:47] Alex (sticker 👍)
```

> Photos become text two ways: a local vision model captions the scene (on by default), and `--ocr` reads any text in the image.

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
| **Photo descriptions** | Captioned automatically by the best installed local model — BLIP (`[describe]`) for photos, or Qwen2-VL (`[describe-hq]`) for photos + stickers + GIFs |
| **Resumable** | Progress is cached per file — close the terminal or crash, then re-run and it continues where it left off |
| **Queue folders** | Transcribe many exports in one command (models load once); `--out-dir` collects the results |
| **Progress bar** | Live `done/total` + ETA per folder |
| **Round-trip verified** | A rich synthetic export runs through the full pipeline and is diffed line-for-line; 85 offline tests on the Python 3.9–3.13 CI matrix |

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

Telegram **Desktop** → open the chat → ⋮ menu → **Export chat history**. In the dialog:

- **Format: JSON** (required — whispergram reads the JSON export, not the HTML one).
- Tick the media you want whispergram to use:

| Export option | Tick it? | What whispergram does with it |
|---|---|---|
| **Voice messages** | ✅ | Transcribed — the core feature |
| **Video messages** | ✅ | Round video notes — transcribed |
| **Photos** | ✅ for captions / `--ocr` | Scene-captioned and/or OCR'd; without it, photos show as a plain `(photo)` |
| **Videos** | optional, for `--video-files` | Regular videos — their audio is transcribed |
| **Stickers** | for `--describe-hq` | `(sticker 😅)` comes from JSON; tick to let `--describe-hq` caption the image too |
| **GIFs** | for `--describe-hq` | `(animation)` comes from JSON; tick to let `--describe-hq` caption it (multi-frame) |
| **Files** | ⬜ not needed | Shown as `(file: report.pdf)` from the JSON metadata |

> **⚠️ Drag the "Size limit" slider up.** It defaults to **8 MB**, and any file larger than that is
> **not** downloaded — those messages come out as `[not exported]`. Voice notes are tiny, but video
> notes, videos, and hi-res photos routinely exceed 8 MB, so raise the slider (toward the max) to be
> sure your media actually lands in the export. *(This is the usual reason notes show as `[not exported]`.)*

You get a folder with a `.json` file plus `voice_messages/`, `video_files/`, `photos/` … subfolders
for whatever you ticked.

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

### Best quality (use your GPU)

Audio and video already use the most accurate model — Whisper **large-v3** — on your GPU by default.
For the best **photo, sticker and GIF** captions, just install the HQ extra — it's then used
**automatically, no flag** — and, for speed, put torch on your GPU:

```bash
pip install -U "whispergram[describe-hq]"
# optional, for GPU-fast captions (match your CUDA, e.g. cu121/cu124):
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu124
whispergram        # auto-uses large-v3 + Qwen2-VL; add --ocr --ocr-lang ukr+rus+eng for screenshot text
```

That runs **large-v3** (audio/video) + **Qwen2-VL** (photos, stickers, GIFs). ⚠️ **On Windows, a CUDA
build of torch can clash with faster-whisper's GPU** (cuDNN) — see [GPU on Windows](#gpu-cuda-setup)
for the two reliable setups before installing CUDA torch.

### Queue & resume

Pass **several export folders** to transcribe them back-to-back — the models load **once** and are
reused, and sequential is safe for your GPU:

```bash
whispergram "ChatExport_Anastasia" "ChatExport_Olha" "ChatExport_Work" --out-dir "C:\merged"
# -> C:\merged\Anastasia.md, C:\merged\Olha.md, C:\merged\Work.md
```

Runs are **resumable**: each transcript/caption is cached to `.whispergram_cache.json` in the export
folder as it's produced, so if you close the terminal or it crashes, just **run it again** — finished
files are skipped and it continues where it left off. A progress bar shows `done/total` + ETA:

```
 60%|████████████        | 28/47 [02:14<01:31], audio_28.ogg
```

If two chats share a name, the second is saved as `Work (2).md` rather than overwriting the first,
and a folder that fails (e.g. a corrupt export) is skipped so the rest of the queue still runs.
`--no-cache` disables the cache; `--out FILE` sets a custom path for a single folder (it can't be
combined with `--out-dir`).

---

## Example output

```
[2026-06-20 12:33] Alex: did you get the files?
[2026-06-20 12:34] Alex (voice 6s): one sec, recording the summary now ...
[2026-06-20 12:35] You (photo, described): a screenshot of a calendar app | text: Sprint review - Fri 15:00
[2026-06-20 12:36] Alex (video-note 8s): [not exported]
[2026-06-20 12:36] You (sticker 😅)
```

> Photo captioning is automatic once `whispergram[describe]` is installed; add `--ocr` for the in-image text, or `--no-describe` for a plain `(photo)` marker.

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
| Photo, `--no-describe` | `[time] sender (photo): caption` (plain marker, no captioning) |
| Animation / GIF | `[time] sender (animation)` |
| Document | `[time] sender (file: report.pdf): caption` |
| Location / poll / contact | `[time] sender (location)` · `(poll)` · `(contact)` |
| Music / audio file | `[time] sender (audio: Artist - Title)` — transcribe with `--audio-files` |
| Regular video file | `[time] sender (video)` — transcribe the audio with `--video-files` |
| Photo (default, `[describe]` installed) | `[time] sender (photo, described): a caption of the scene` |
| Photo + `--ocr` | `[time] sender (photo, described): <scene> \| text: <text found in the image>` |
| Photo + `--ocr --no-describe` | `[time] sender (photo, text): <text found in the image>` |
| Sticker / GIF + `--describe-hq` | `[time] sender (sticker 😅, described): …` · `(animation, described): …` |

Markers can be turned off with `--no-media-markers` (voice/video notes are always transcribed).

---

## Describe modes: photos, stickers & GIFs

Image captioning is opt-in via an extra. **The best installed describer is used automatically — no
flag needed:**

| Mode | How to enable | What it captions | Model | Size | Speed |
|---|---|---|---|---|---|
| **Off** | `--no-describe` | nothing (media shown as markers) | — | — | instant |
| **Light** | `pip install whispergram[describe]` | **photos** | BLIP-large | ~1.9 GB | fast on CPU |
| **High-quality (auto)** | `pip install whispergram[describe-hq]` | **photos + stickers + GIFs** (GIFs multi-frame) | Qwen2-VL-2B | ~4.4 GB | slow on CPU / fast on GPU |

- Install the quality you want, then just run `whispergram`: if `[describe-hq]` is present it's used
  automatically (and captions **stickers + GIFs**); otherwise BLIP captions photos. `--describe-hq`
  forces HQ; `--no-describe` turns captioning off.
- **HQ (Qwen2-VL)** is markedly better on cartoons, characters and *actions*, and reads GIFs several
  frames at a time so it catches motion. **BLIP** is a quick photo gist (rough on cartoons).
- Add `--ocr` to also pull any in-image text. Everything is local; captions are best-effort, never
  literal fact. To run the models on your GPU, see [GPU setup](#gpu-cuda-setup).

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
| Photo descriptions | Best-effort, local | On by default with `whispergram[describe]` (BLIP via transformers) — captions are a short, English scene *gist*, not literal fact; `--no-describe` to skip |
| Stickers / GIFs / cartoons | `--describe-hq` only | Local models caption cartoons/memes roughly; `--describe-hq` (Qwen2-VL, multi-frame for GIFs) is much better but heavier — still best-effort, never exact |
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
whispergram --no-describe                         # skip photo scene captions
whispergram --describe-hq                         # better captions + describe stickers/GIFs (Qwen2-VL)
whispergram --offline                             # zero network calls (use cached models only)
whispergram --out result.md                       # custom output path
```

| Flag | Default | Notes |
|---|---|---|
| `--device` | `cuda` | `cuda` or `cpu`; auto-falls back to CPU if the GPU fails |
| `--model` | `large-v3` | try `large-v3-turbo` or `medium` if CPU is slow |
| `--lang` | auto | force a code like `uk`, `ru`, `en` if auto-detect mislabels |
| `--batch-size` | 0 | `N`>1 batches segments for a big **GPU** speedup; 0 = sequential (best quality) |
| `--out` | `merged_chat.md` | output file for a **single** folder (mutually exclusive with `--out-dir`) |
| `--out-dir` | off | collect each queued folder's transcript here as `<chat name>.md` |
| `--no-cache` | off | don't read/write the per-folder `.whispergram_cache.json` resume cache |
| `--audio-files` | off | also transcribe `audio_file` messages (music, long memos) |
| `--video-files` | off | also transcribe regular video files' audio track |
| `--ocr` | off | extract text from photos with local Tesseract OCR |
| `--ocr-lang` | `eng` | Tesseract language(s), e.g. `ukr+rus+eng` |
| `--no-describe` | off | skip photo scene captions (on by default when `[describe]` is installed) |
| `--describe-model` | `blip-large` | BLIP caption model id; use `...-base` for faster/lighter |
| `--describe-hq` | off | high-quality describer (Qwen2-VL) + captions stickers/GIFs; needs `[describe-hq]` |
| `--offline` | off | use only cached models; make zero network calls |
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

**GPU for photo/sticker/GIF captions is separate from Whisper's.** The describe models (BLIP /
Qwen2-VL) use PyTorch, and `pip install` fetches the **CPU** build by default — so captioning runs on
the CPU even when Whisper is on your GPU. For fast captioning (especially `--describe-hq`), install a
CUDA build of torch:

```bash
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu121   # match your CUDA
```

whispergram auto-detects CUDA and moves the caption model to the GPU — no flag needed.

> **⚠️ Windows: a CUDA torch can clash with Whisper-on-GPU.** A CUDA build of torch bundles its own
> cuDNN, which can collide with the cuDNN that faster-whisper (CTranslate2) uses — surfacing as
> `OSError: [WinError 127] … cudnn_*.dll` on startup. Both can't reliably share the GPU out of the
> box, so pick one of these stable setups:
> - **Whisper on GPU + captions on CPU** (default, recommended): keep the **CPU** build of torch
>   — `pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu`. Fast audio,
>   slower captions.
> - **Captions on GPU + Whisper on CPU**: `pip uninstall nvidia-cudnn-cu12 nvidia-cublas-cu12`,
>   install a CUDA torch, and run with `--device cpu`. Fast captions, slower audio.
>
> whispergram prints this guidance if it hits the conflict.

**Both on the GPU — two passes (fastest for big batches).** Because runs are resumable, you can do
each heavy step on the GPU in turn without the two libraries ever colliding:

```bash
# Pass 1 — transcribe everything on the GPU (CPU torch + faster-whisper GPU), no captions:
whispergram <folders> --out-dir DIR --no-describe

# switch torch to CUDA (captions-on-GPU setup):
pip uninstall -y nvidia-cudnn-cu12 nvidia-cublas-cu12
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu124   # match your CUDA

# Pass 2 — caption on the GPU; Whisper runs on CPU but every transcript is already cached, so it's
# instant and only the captioning does work:
whispergram <folders> --out-dir DIR --describe-hq --device cpu
```

Both expensive stages run on the GPU, the resume cache means no transcription is repeated, and the
cuDNN clash never happens because only one library touches the GPU per pass.

### Ukrainian / Russian OCR

`--ocr` needs the Tesseract binary (auto-found on Windows since 0.8.2) **and** the language packs.
On Windows: `winget install UB-Mannheim.TesseractOCR`, then add the `ukr`/`rus` data — either re-run
that installer and tick them, or drop `ukr.traineddata` + `rus.traineddata`
([tessdata_best](https://github.com/tesseract-ocr/tessdata_best)) into a folder and point
`TESSDATA_PREFIX` at it. Verify with `tesseract --list-langs`.

---

## FAQ

**How do I transcribe Telegram voice messages?**
Export the chat from Telegram Desktop as JSON (with voice messages), then run `whispergram` in the
export folder. Every voice note is transcribed with Whisper and merged into the text chat.

**Is it private / offline? Does my audio leave my machine?**
Yes. Transcription, captioning and OCR run locally and need no account or API key. The tool makes no
network calls **with your data** — your chat audio, photos and transcripts never leave your machine.
The only network use is a **one-time download of the model weights** (public files) from Hugging
Face; usage telemetry is **off by default**, and `--offline` forces cache-only with **zero** network
calls once the models are downloaded.

**Do I need a GPU?**
No. It runs on CPU (`--device cpu`); use `--model large-v3-turbo` for speed. A CUDA GPU is faster.

**Does it handle round video messages / video notes?**
Yes — round `video_message` notes are transcribed from their audio, just like voice notes. Regular
video files are transcribed too with `--video-files`.

**Can it read text from photos / screenshots?**
Yes — `--ocr` runs local Tesseract over photos and drops the extracted text inline as
`(photo, text): ...` (ideal for screenshots).

**Can it describe what's *in* a photo, not just the text?**
Yes, and it's **automatic**: once you `pip install whispergram[describe]`, photos are captioned by a
small local model (BLIP via transformers — uses your GPU if you have one, else CPU)
with no flag needed. It composes with `--ocr` to give both the scene and the in-image text. Captions
are a short, English, best-effort gist. Pass `--no-describe` to turn it off, or `--describe-model
Salesforce/blip-image-captioning-base` for a faster/lighter model. The BLIP-large model (~1.9 GB)
downloads once on the first photo, then stays offline.

**Can it describe stickers and GIFs too?**
Yes, with `--describe-hq` (`pip install whispergram[describe-hq]`). That switches to a stronger model
(Qwen2-VL) that's much better on cartoons and *actions*, and it reads GIFs **multi-frame** to catch
the motion — e.g. `(animation, described): a character in a suit walking into an arena`. It's heavier
(~4.4 GB; slow on CPU, fast on GPU), and cartoon/meme captions are still best-effort, never exact.

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
    ├── test_whispergram.py    # 85 offline tests — no model download or GPU required
    └── fixtures/
        └── sample_export/
            └── result.json    # synthetic export (safe to commit; used by tests + CI)
```

---

## ⚠️ Privacy

This tool processes **private conversations**, and the transcripts it produces are just as
sensitive as the audio. Two rules:

- **Nothing leaves your machine.** Transcription, captioning and OCR are fully local; the tool makes
  no network calls with your data and needs no credentials. The only network use is a one-time
  download of public model weights from Hugging Face — telemetry is off by default, and `--offline`
  guarantees zero network calls once the models are cached.
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
- For photo descriptions (optional): `pip install whispergram[describe]` (transformers + torch — prebuilt wheels, no compiler; uses your GPU if present). Captioning is then automatic; the ~1.9 GB BLIP-large model downloads once on the first photo, then runs offline. Use `--describe-model Salesforce/blip-image-captioning-base` for a lighter model, or `--no-describe` to turn it off
- For high-quality captions + sticker/GIF describe (optional): `pip install whispergram[describe-hq]` (adds `torchvision`) and pass `--describe-hq`. Uses Qwen2-VL (~4.4 GB, slow on CPU / fast on GPU)

> The test suite needs none of the above — only `ruff` and `pytest`.

---

## Changelog

See [CHANGELOG.md](CHANGELOG.md) for the full version history.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for the development setup, the privacy rule, and the
versioning / release policy.

## License

[MIT](LICENSE)
