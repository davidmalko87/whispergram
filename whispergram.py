#!/usr/bin/env python3
"""whispergram - transcribe Telegram voice/video notes and merge them into the text chat.

Reads a Telegram Desktop JSON export and produces a single chronological transcript with
voice/video notes and text interleaved, each line tagged by sender + timestamp. Transcription
runs locally and offline with faster-whisper - your chat audio and transcripts never leave your
machine, and no API key or login is ever required.

Purpose : Make an audio-heavy Telegram chat fully readable (and LLM-readable) as one file.
Author  : David Malko <davidmalko87@gmail.com>
Created : 2026-06-23
Version : see __version__ below
License : MIT
"""

from __future__ import annotations

import argparse
import datetime
import glob
import json
import os
import shutil
import subprocess
import sys
from collections import Counter
from typing import Callable, Iterable, List, Optional, Tuple

__version__ = "1.3.0"

# Telegram media types whose audio we can transcribe, mapped to their display label.
_KIND_LABEL = {
    "voice_message": "voice",
    "video_message": "video-note",
    "audio_file": "audio",
    "video_file": "video",
}

# Injected callables, so the heavy faster-whisper / OCR dependencies stay out of the pure
# mapping logic (and so that logic stays unit-testable offline).
Transcriber = Callable[[str], str]  # audio/video path -> transcript text
Describer = Callable[[str], str]    # image path -> extracted text (OCR or caption)


# --------------------------------------------------------------------------------------
# Pure mapping logic (no faster-whisper, no I/O beyond os.path.exists) - fully testable.
# --------------------------------------------------------------------------------------
def find_json(export_dir: str) -> str:
    """Return the Telegram export JSON in *export_dir*.

    Prefers an exact ``result.json`` (Telegram's canonical name), then any filename
    containing ``result``, then the first JSON found (deterministic, sorted).
    """
    cands = sorted(glob.glob(os.path.join(export_dir, "*.json")))
    if not cands:
        sys.exit(f"No .json export found in {os.path.abspath(export_dir)}")
    exact = next((c for c in cands if os.path.basename(c).lower() == "result.json"), None)
    if exact:
        return exact
    return next((c for c in cands if "result" in os.path.basename(c).lower()), cands[0])


# --------------------------------------------------------------------------------------
# Instagram DM export reader
#
# Instagram's "Download your information" (JSON) uses a different schema from Telegram and two
# quirks we normalise away here, then hand the result to the same build_transcript pipeline:
#   1. text is mojibaked - UTF-8 stored as latin-1-escaped bytes (Cyrillic/emoji come out garbled),
#   2. a thread is paginated across message_1.json, message_2.json, ... newest-first.
# A shared Reel/post is only a link (no video file), so it becomes an inline text marker.
# --------------------------------------------------------------------------------------
def _fix_mojibake(text: str) -> str:
    """Undo Instagram's latin-1-escaped-UTF-8 mangling so Ukrainian/Russian/emoji read correctly.
    A no-op for text that is already valid (e.g. plain ASCII or correctly-encoded Cyrillic)."""
    if not text:
        return text
    try:
        return text.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return text


def _ig_timestamp(ms: int) -> str:
    """Instagram's epoch-millis -> the ``YYYY-MM-DDTHH:MM:SS`` string build_transcript expects."""
    try:
        return datetime.datetime.fromtimestamp(ms / 1000).strftime("%Y-%m-%dT%H:%M:%S")
    except (OverflowError, OSError, ValueError):
        return ""


def _ig_media_path(uri: str) -> str:
    """Instagram media ``uri`` is a full export-root-relative path; keep the last two components
    (``<subfolder>/<file>``, e.g. ``audio/audioclip-….mp4``) so it resolves against the thread
    folder build_transcript is pointed at."""
    if not uri:
        return ""
    parts = uri.replace("\\", "/").rstrip("/").split("/")
    return "/".join(parts[-2:]) if len(parts) >= 2 else parts[-1]


def is_instagram_export(export_dir: str) -> bool:
    """True if *export_dir* looks like an Instagram DM thread export (a ``message_1.json`` whose
    messages carry ``sender_name``/``timestamp_ms`` rather than Telegram's ``type``/``date``)."""
    m1 = os.path.join(export_dir, "message_1.json")
    if not os.path.exists(m1):
        return False
    try:
        with open(m1, encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        return False
    msgs = data.get("messages")
    if not isinstance(msgs, list):
        return False
    return "participants" in data or bool(msgs and "sender_name" in msgs[0])


def _normalize_instagram(export_dir: str) -> Tuple[List[dict], str]:
    """Load every ``message_*.json`` in an Instagram thread folder, merge + sort chronologically,
    fix the encoding, and convert to the internal (Telegram-shaped) message list. Each media item
    becomes its own message; a shared reel/post becomes an inline text marker. Returns
    ``(messages, chat_name)``."""
    raw: List[dict] = []
    title = ""
    for f in sorted(glob.glob(os.path.join(export_dir, "message_*.json"))):
        try:
            with open(f, encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception:
            continue
        title = title or data.get("title") or ""
        raw.extend(data.get("messages", []))
    raw.sort(key=lambda m: m.get("timestamp_ms", 0))  # export is newest-first; we want oldest-first

    out: List[dict] = []
    for m in raw:
        ts = _ig_timestamp(m.get("timestamp_ms", 0))
        who = _fix_mojibake(m.get("sender_name") or "Unknown")
        base = {"type": "message", "date": ts, "from": who}

        def media_msg(kind, uri):
            return {**base, "media_type": kind, "file": _ig_media_path(uri)}

        for a in m.get("audio_files") or []:
            out.append(media_msg("voice_message", a.get("uri")))
        for v in m.get("videos") or []:
            out.append(media_msg("video_file", v.get("uri")))
        for p in m.get("photos") or []:
            out.append({**base, "photo": _ig_media_path(p.get("uri"))})
        for g in m.get("gifs") or []:
            out.append(media_msg("animation", g.get("uri")))
        sticker = m.get("sticker")
        if sticker and sticker.get("uri"):
            out.append(media_msg("sticker", sticker["uri"]))
        share = m.get("share")
        if share and (share.get("link") or share.get("share_text")):
            owner = _fix_mojibake(share.get("original_content_owner") or "")
            link = share.get("link") or ""
            marker = "shared reel/post" + (f" by {owner}" if owner else "")
            line = f"[{marker}{(': ' + link) if link else ''}]"
            extra = _fix_mojibake(share.get("share_text") or "").strip()
            out.append({**base, "text": f"{line} {extra}".strip()})
        content = _fix_mojibake(m.get("content") or "").strip()
        if content:
            out.append({**base, "text": content})

    return out, _fix_mojibake(title) or os.path.basename(os.path.abspath(export_dir))


def extract_text(msg: dict) -> str:
    """Reconstruct a message's plain text, handling all three Telegram shapes safely.

    Telegram stores text as ``text_entities`` (a list of typed runs), a plain ``str``, or a
    mixed ``list`` of strings and entity dicts. ``text_entities`` is preferred: it is always
    present in modern exports and normalises links, mentions and custom emoji into ``text``.
    A ``null`` text value or a non-dict entity never crashes the run - it contributes ``""``.
    """
    entities = msg.get("text_entities")
    if entities:
        return "".join(
            (e.get("text") or "") if isinstance(e, dict) else (e if isinstance(e, str) else "")
            for e in entities
        ).strip()
    raw = msg.get("text")
    if isinstance(raw, str):
        return raw.strip()
    if isinstance(raw, list):
        return "".join(
            p if isinstance(p, str) else (p.get("text") or "") if isinstance(p, dict) else ""
            for p in raw
        ).strip()
    return ""


def is_missing_media(file_field: Optional[str], path: str) -> bool:
    """True when a media file was not downloaded in the export.

    Telegram writes a ``(File not included...)`` placeholder into ``file`` when media was
    excluded from the export; otherwise the field is a real relative path.
    """
    field = file_field or ""
    if not field or "not included" in field.lower():
        return True
    return not os.path.exists(path)


def media_marker(msg: dict) -> str:
    """Short label for a non-transcribed media message (sticker/photo/file/...), or ''.

    Keeps the merged timeline faithful: without this, stickers, photos, files, locations,
    polls, contacts and so on with no caption would vanish silently, leaving misleading gaps
    when the transcript is read by a human or an LLM. Returns '' only for a message that
    genuinely carries no content (e.g. a reaction-only message).
    """
    media_type = msg.get("media_type")
    if media_type == "sticker":
        return f"sticker {msg.get('sticker_emoji', '')}".strip()
    if media_type == "animation":
        return "animation"
    if media_type == "video_file":
        return "video"
    if media_type == "audio_file":
        performer = msg.get("performer")
        title = msg.get("title") or msg.get("file_name")
        if performer and title:
            return f"audio: {performer} - {title}"
        return f"audio: {title or 'file'}"
    if msg.get("photo"):
        return "photo"
    if msg.get("location_information") or msg.get("place_name"):
        return "location"
    if msg.get("poll"):
        return "poll"
    if msg.get("contact_information") or msg.get("contact_vcard"):
        return "contact"
    if msg.get("game_title") or msg.get("game_description"):
        return "game"
    if msg.get("invoice_information"):
        return "invoice"
    if msg.get("file_name"):
        return f"file: {msg['file_name']}"
    if media_type:
        return media_type
    # An attachment we did not specifically name (e.g. expired media) - still mark it.
    if msg.get("file") or msg.get("thumbnail"):
        return "media"
    return ""


def _transcribe_types(audio_files: bool, video_files: bool) -> frozenset:
    """Media types transcribed given the opt-in flags. Shared by build_transcript and count_jobs
    so the progress total can never disagree with what's actually processed."""
    types = {"voice_message", "video_message"}
    if audio_files:
        types.add("audio_file")
    if video_files:
        types.add("video_file")
    return frozenset(types)


# Telegram animated stickers are Lottie vector files (.tgs); no local image/video model can open
# them, so they're left as plain markers rather than attempted (and not counted as describe jobs).
_UNDESCRIBABLE_EXT = frozenset({".tgs"})


def _is_describable(path: str) -> bool:
    """False for media a local vision model can't open (e.g. a `.tgs` Lottie sticker)."""
    return os.path.splitext(path)[1].lower() not in _UNDESCRIBABLE_EXT


def build_transcript(
    messages: Iterable[dict],
    export_dir: str,
    transcribe: Transcriber,
    *,
    media_markers: bool = True,
    audio_files: bool = False,
    video_files: bool = False,
    describe: Optional[Describer] = None,
    photo_label: str = "text",
    media_describe: Optional[Describer] = None,
    describe_media: frozenset = frozenset(),
    on_job: Optional[Callable[[str], None]] = None,
) -> Tuple[List[str], Counter]:
    """Turn Telegram messages into merged transcript lines, chronological order preserved.

    *transcribe* maps an audio/video path to its transcript text; *describe* (optional) maps a
    photo path to extracted text (OCR or a caption). Missing media is never sent to either.
    Returns ``(lines, stats)`` where ``stats`` counts each outcome category.
    """
    transcribe_types = _transcribe_types(audio_files, video_files)
    lines: List[str] = []
    stats: Counter = Counter()

    for msg in messages:
        if msg.get("type") != "message":
            stats["service"] += 1
            continue

        ts = (msg.get("date") or "")[:16].replace("T", " ")
        who = msg.get("from") or "Unknown"
        text = extract_text(msg)
        media_type = msg.get("media_type")

        if media_type in transcribe_types:
            duration = msg.get("duration_seconds", "?")
            file_field = msg.get("file") or ""
            path = os.path.join(export_dir, file_field)
            if is_missing_media(file_field, path):
                body = "[not exported]"
                stats["missing"] += 1
            else:
                if on_job is not None:
                    on_job(os.path.basename(path))
                body = transcribe(path)
                stats["transcribed"] += 1
            line = f"[{ts}] {who} ({_KIND_LABEL[media_type]} {duration}s): {body}"
            if text:
                line += f" | caption: {text}"
            lines.append(line)
            continue

        if describe is not None and msg.get("photo"):
            photo_field = msg.get("photo") or ""
            photo_path = os.path.join(export_dir, photo_field)
            if not is_missing_media(photo_field, photo_path):
                if on_job is not None:
                    on_job(os.path.basename(photo_path))
                extracted = describe(photo_path).strip()
                if extracted:
                    line = f"[{ts}] {who} (photo, {photo_label}): {extracted}"
                    if text:
                        line += f" | caption: {text}"
                    lines.append(line)
                    stats["described"] += 1
                    continue
            # photo missing or nothing extracted -> fall through to the plain (photo) marker

        if media_describe is not None and media_type in describe_media and not msg.get("photo"):
            # `not photo` keeps this exclusive with the photo block above, mirroring count_jobs
            # so on_job fires at most once per message (the bar never exceeds its total).
            file_field = msg.get("file") or ""
            media_path = os.path.join(export_dir, file_field)
            present = file_field and not is_missing_media(file_field, media_path)
            if present and _is_describable(media_path):
                if on_job is not None:
                    on_job(os.path.basename(media_path))
                extracted = media_describe(media_path).strip()
                if extracted:
                    marker = media_marker(msg)
                    line = f"[{ts}] {who} ({marker}, described): {extracted}"
                    if text:
                        line += f" | caption: {text}"
                    lines.append(line)
                    stats["described"] += 1
                    continue
            # missing or nothing extracted -> fall through to the plain marker

        marker = media_marker(msg) if media_markers else ""
        if marker:
            file_field = msg.get("file") or ""
            if file_field and is_missing_media(file_field, os.path.join(export_dir, file_field)):
                marker += " [not exported]"
            line = f"[{ts}] {who} ({marker})"
            if text:
                line += f": {text}"
            lines.append(line)
            stats["media"] += 1
            continue

        if text:
            lines.append(f"[{ts}] {who}: {text}")
            stats["text"] += 1
        else:
            stats["empty"] += 1

    return lines, stats


def count_jobs(
    messages: Iterable[dict],
    export_dir: str,
    *,
    audio_files: bool = False,
    video_files: bool = False,
    describe_photos: bool = False,
    describe_media: frozenset = frozenset(),
) -> int:
    """Count media items that will be transcribed/described with a present file - the total for
    the progress bar. Mirrors build_transcript's three model-call sites exactly.
    """
    transcribe_types = _transcribe_types(audio_files, video_files)
    n = 0
    for msg in messages:
        if msg.get("type") != "message":
            continue
        media_type = msg.get("media_type")
        if media_type in transcribe_types:
            f = msg.get("file") or ""
            if f and not is_missing_media(f, os.path.join(export_dir, f)):
                n += 1
            continue
        if describe_photos and msg.get("photo"):
            pf = msg.get("photo") or ""
            if not is_missing_media(pf, os.path.join(export_dir, pf)):
                n += 1
            continue
        if describe_media and media_type in describe_media:
            f = msg.get("file") or ""
            p = os.path.join(export_dir, f)
            if f and not is_missing_media(f, p) and _is_describable(p):
                n += 1
    return n


def _photo_reader(
    describer: Optional[Describer], ocr: Optional[Describer]
) -> Tuple[Optional[Describer], str]:
    """Combine an optional scene describer and an optional OCR reader into one photo reader.

    Returns ``(describe, photo_label)`` for build_transcript: a vision-model caption is labelled
    ``described``, OCR text alone is labelled ``text``, and when both are enabled they are merged
    as ``<caption> | text: <ocr>``. Returns ``(None, "text")`` when neither is enabled.
    """
    if describer and ocr:
        def describe(path: str) -> str:
            caption = describer(path).strip()
            in_image = ocr(path).strip()
            if caption and in_image:
                return f"{caption} | text: {in_image}"
            return caption or (f"text: {in_image}" if in_image else "")
        return describe, "described"
    if describer:
        return describer, "described"
    if ocr:
        return ocr, "text"
    return None, "text"


# --------------------------------------------------------------------------------------
# Runtime transcription (faster-whisper) - imported lazily so the module loads, and the
# pure logic above is testable, without the heavy dependency installed.
# --------------------------------------------------------------------------------------
def _register_cuda_dll_dirs() -> None:
    """Windows only: add pip-installed CUDA DLL dirs to the loader path. No-op elsewhere."""
    if os.name != "nt":
        return
    import site

    roots = list(site.getsitepackages())
    try:
        roots.append(site.getusersitepackages())
    except Exception:
        pass
    roots.append(os.path.join(sys.prefix, "Lib", "site-packages"))
    for root in roots:
        for dll in glob.glob(os.path.join(root, "nvidia", "**", "*.dll"), recursive=True):
            dll_dir = os.path.dirname(dll)
            if os.path.isdir(dll_dir):
                try:
                    os.add_dll_directory(dll_dir)
                except OSError:
                    pass


def _setup_cuda_windows() -> None:
    """Copy pip-installed CUDA DLLs next to CTranslate2's binary - the reliable Windows fix.

    CTranslate2 loads cuBLAS/cuDNN lazily in native code that ignores ``os.add_dll_directory``,
    so the surest fix is to place the DLLs inside the package dir (always searched first).
    """
    import shutil
    import site

    import ctranslate2

    ct_dir = os.path.dirname(ctranslate2.__file__)
    roots = site.getsitepackages()
    try:
        roots.append(site.getusersitepackages())
    except Exception:
        pass
    copied = 0
    for root in roots:
        for dll in glob.glob(os.path.join(root, "nvidia", "**", "*.dll"), recursive=True):
            try:
                shutil.copy2(dll, ct_dir)
                copied += 1
            except Exception:
                pass
    print(f"Copied {copied} CUDA DLLs into {ct_dir}")
    print("Now run the script normally with --device cuda.")


def _configure_hf_env(offline: bool) -> None:
    """Privacy defaults for the Hugging Face libraries the models load through.

    Disables anonymized usage telemetry (model/library names + versions - never your content) by
    default. With *offline*, forces the libraries to use only already-downloaded models and make
    zero network calls. Call before any model is loaded.
    """
    os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
    if offline:
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"


# Below this much free VRAM, large-v3 in float16 (~3 GB weights + cuDNN 9 workspace) may not fit -
# CTranslate2 can *hang* rather than error; int8_float16 (~1.6 GB) fits with near-identical quality.
_LOW_VRAM_MIB = 5000


def _gpu_free_mib() -> Optional[int]:
    """Free VRAM (MiB) on the first CUDA GPU via ``nvidia-smi``, or ``None`` if it can't be read.
    Used to auto-pick an int8 compute type on small GPUs where float16 large-v3 won't fit."""
    exe = shutil.which("nvidia-smi")
    if not exe:
        return None
    try:
        out = subprocess.run([exe, "--query-gpu=memory.free", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=6)
        return int(out.stdout.strip().splitlines()[0])
    except Exception:
        return None


def _resolve_compute_type(device: str, requested: str) -> str:
    """Pick the CTranslate2 compute type. An explicit *requested* type wins; ``auto`` means int8 on
    CPU, and float16 on GPU - except on a low-VRAM GPU, where int8_float16 is chosen so large-v3
    actually fits (a 4 GB card can otherwise hang loading float16 large-v3)."""
    if requested and requested != "auto":
        return requested
    if device != "cuda":
        return "int8"
    free = _gpu_free_mib()
    if free is not None and free < _LOW_VRAM_MIB:
        print(f"Low GPU memory ({free} MiB free): using int8_float16 so large-v3 fits on the GPU "
              f"(pass --compute-type float16 to override).")
        return "int8_float16"
    return "float16"


def load_model(model_name: str, device: str, compute_type: str = "auto"):
    """Load a faster-whisper model, falling back from CUDA to CPU on any load failure."""
    _register_cuda_dll_dirs()
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        sys.exit(
            "faster-whisper is not installed. Run `pip install -r requirements.txt`, "
            "or use --dry-run to preview the merge without transcribing."
        )
    except OSError as exc:
        sys.exit(
            "Failed to load faster-whisper's CUDA libraries - usually a PyTorch/CUDA clash on "
            f"Windows (a GPU build of torch fighting faster-whisper's cuDNN):\n  {exc}\n"
            "Fix: reinstall the CPU build of torch -\n"
            "  pip install --force-reinstall torch torchvision "
            "--index-url https://download.pytorch.org/whl/cpu\n"
            "See the README 'GPU on Windows' section for the stable GPU setups."
        )

    compute = _resolve_compute_type(device, compute_type)
    try:
        model = WhisperModel(model_name, device=device, compute_type=compute)
        print(f"Model: {model_name} on {device} ({compute})")
    except Exception as exc:
        print(f"{device} failed ({exc}); falling back to CPU")
        model = WhisperModel(model_name, device="cpu", compute_type="int8")
    return model


def _cache_key(engine: str, path: str, export_dir: str) -> str:
    """Cache key for a media file: engine + export-relative path + size (cheap, re-export-safe).

    The relative path (not just the basename) keeps two same-named files in different Telegram
    subfolders - e.g. voice_messages/a.ogg vs round_video_messages/a.ogg - from colliding.
    """
    try:
        size = os.path.getsize(path)
    except OSError:
        size = 0
    try:
        rel = os.path.relpath(path, export_dir).replace(os.sep, "/")
    except ValueError:  # different drive on Windows -> fall back to the basename
        rel = os.path.basename(path)
    return f"{engine}\x1f{rel}\x1f{size}"


class _Cache:
    """On-disk cache of transcripts/captions, flushed after each item so runs are **resumable**.

    Keyed by engine + export-relative path + size, so re-running an export continues where it left
    off, and switching the model (a different engine string) recomputes. A ``None`` path disables
    it. Only non-empty results are stored, so a transient failure never poisons later runs.
    """

    def __init__(self, path: Optional[str]):
        self.path = path
        self.data: dict = {}
        if path and os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as fh:
                    self.data = json.load(fh).get("entries", {})
            except Exception:
                self.data = {}

    def get(self, key: str) -> Optional[str]:
        return self.data.get(key)

    def put(self, key: str, value: str) -> None:
        self.data[key] = value
        if not self.path:
            return
        tmp = self.path + ".tmp"
        try:  # atomic write so an interruption never corrupts the cache
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump({"version": 1, "entries": self.data}, fh, ensure_ascii=False)
            os.replace(tmp, self.path)
        except Exception:
            pass


def _with_cache(
    fn: Describer, cache: "Optional[_Cache]", engine: str, export_dir: str
) -> Describer:
    """Wrap a transcribe/describe callable so results persist (resume) keyed by *engine*."""
    if cache is None:
        return fn

    def wrapped(path: str) -> str:
        key = _cache_key(engine, path, export_dir)
        hit = cache.get(key)
        if hit is not None:
            return hit
        result = fn(path)
        # Only persist real results: an empty string means the model was disabled or errored
        # (GPU OOM, --offline with no weights, Tesseract failure). Caching it would make the
        # blank a permanent "hit" and never retry; leaving it out lets the next run recompute.
        if result:
            cache.put(key, result)
        return result

    return wrapped


def make_transcriber(model, lang: Optional[str], batch_size: int = 0) -> Transcriber:
    """Build a caching ``transcribe(path) -> text`` closure over a loaded model.

    With ``batch_size`` > 1 the audio is decoded through faster-whisper's
    ``BatchedInferencePipeline`` - several segments at once, a large speedup on a GPU. Each chunk
    is decoded independently (no cross-segment context), a small quality trade-off, so the default
    (``batch_size`` 0/1) keeps the sequential decode that's best for connected uk/ru speech.
    """
    cache: dict = {}
    batched = batch_size and batch_size > 1
    if batched:
        from faster_whisper import BatchedInferencePipeline
        engine = BatchedInferencePipeline(model=model)

    def transcribe(path: str) -> str:
        if path not in cache:
            try:
                if batched:
                    segments, _ = engine.transcribe(path, language=lang, vad_filter=True,
                                                    batch_size=batch_size)
                else:
                    segments, _ = model.transcribe(path, language=lang, vad_filter=True)
                cache[path] = " ".join(s.text.strip() for s in segments).strip() or "[no speech]"
            except Exception as exc:
                # One unreadable file (no audio stream, odd container) must NOT abort the whole
                # folder's queue - mark it and move on, mirroring the describe path.
                print(f"    transcribe failed on {os.path.basename(path)}: {exc}")
                cache[path] = "[transcription failed]"
        return cache[path]

    return transcribe


def _find_tesseract() -> Optional[str]:
    """Locate the Tesseract binary when it isn't on PATH (the UB-Mannheim Windows installer
    doesn't add it). Returns a path to use as pytesseract's ``tesseract_cmd``, or ``None`` if it's
    already on PATH (or nowhere obvious)."""
    if shutil.which("tesseract"):
        return None  # already discoverable
    for cand in (
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Programs\Tesseract-OCR\tesseract.exe"),
    ):
        if os.path.isfile(cand):
            return cand
    return None


def make_ocr(lang: str) -> Describer:
    """Build a caching OCR ``describe(image_path) -> text`` closure (local Tesseract).

    The result is collapsed to a single line. Needs the Tesseract binary (auto-found on Windows if
    not on PATH) plus the language data packs for *lang* (e.g. ``ukr``, ``rus``); install the Python
    deps with ``pip install whispergram[ocr]``. A photo Tesseract cannot read returns ``""``.
    """
    try:
        import pytesseract
        from PIL import Image
    except ImportError:
        sys.exit(
            "OCR needs pytesseract + Pillow: `pip install whispergram[ocr]`, and the Tesseract "
            "binary (with language packs, e.g. ukr/rus). Or drop --ocr."
        )

    found = _find_tesseract()
    if found:
        pytesseract.pytesseract.tesseract_cmd = found

    cache: dict = {}
    warned = {"done": False}

    def describe(path: str) -> str:
        if path not in cache:
            try:
                raw = pytesseract.image_to_string(Image.open(path), lang=lang)
            except Exception as exc:
                if not warned["done"]:  # report the cause once, not per photo
                    print(f"    OCR unavailable ({exc}); photos are still scene-described, "
                          f"OCR text skipped.")
                    warned["done"] = True
                raw = ""
            cache[path] = " ".join(raw.split())
        return cache[path]

    return describe


def _hq_available() -> bool:
    """True if the high-quality describer's deps ([describe-hq]) are importable.

    Distinguished from the lighter [describe] (BLIP) by ``torchvision`` (Qwen2-VL's processor
    needs it). Used to auto-select the best *installed* describer with no flag.
    """
    import importlib.util

    return all(
        importlib.util.find_spec(m) is not None
        for m in ("torch", "transformers", "torchvision")
    )


def _sample_indices(total: int, n: int) -> List[int]:
    """Evenly-spaced frame indices (in order) for sampling *n* of *total* frames. Handles the
    single-frame case without dividing by zero (the bug a 1-frame .webm sticker hit)."""
    n = min(max(n, 1), max(total, 1))
    if n == 1:
        return [max(total - 1, 0) // 2]
    return sorted({round(i * (total - 1) / (n - 1)) for i in range(n)})


def _extract_frames(path: str, max_frames: int) -> list:
    """Return PIL frames from a media file: one for stills, up to *max_frames* evenly sampled
    (in order) for videos/GIFs (.mp4/.webm/...). Used so describers can caption animations.
    """
    from PIL import Image

    if os.path.splitext(path)[1].lower() in (".mp4", ".webm", ".mov", ".mkv", ".gif"):
        import av

        frames = [f.to_image() for f in av.open(path).decode(video=0)]
        if not frames:
            return []
        return [frames[i] for i in _sample_indices(len(frames), max_frames)]
    return [Image.open(path).convert("RGB")]


def make_describer(
    model_id: str = "Salesforce/blip-image-captioning-large",
) -> Optional[Describer]:
    """Build a caching scene-caption ``describe(image_path) -> text`` closure, or ``None``.

    Runs a small local image-captioning model (BLIP, BSD-3) via transformers, loaded through its
    dedicated ``BlipProcessor`` / ``BlipForConditionalGeneration`` classes (the Auto-classes do
    not resolve cleanly on transformers 5.x). Weights download once from Hugging Face then run
    offline - on the GPU if available, else the CPU. Captioning is on by default; if the optional
    deps are missing this returns ``None``, and if the model can't be loaded (e.g. ``--offline``
    with nothing cached) photos degrade to a plain ``(photo)`` marker - neither fails the run. The
    model loads lazily on the first photo, so a photo-less chat never triggers the download.
    Captions are a short, English, best-effort gist of the scene, never literal content (use --ocr
    for the text inside an image). Default is BLIP-large; pass a lighter id such as
    ``Salesforce/blip-image-captioning-base`` via ``--describe-model`` for speed. Enable with
    ``pip install whispergram[describe]``.
    """
    try:
        import torch
        from PIL import Image
        from transformers import BlipForConditionalGeneration, BlipProcessor
    except ImportError:
        print(
            "Note: photos are not described - enable scene captions with "
            "`pip install whispergram[describe]`, or pass --no-describe to silence this."
        )
        return None

    state: dict = {}
    cache: dict = {}

    def describe(path: str) -> str:
        if path in cache:
            return cache[path]
        if state.get("disabled"):
            return ""
        if "model" not in state:  # lazy one-time load on the first photo
            try:
                device = "cuda" if torch.cuda.is_available() else "cpu"
                print(f"Describer: {model_id} on {device} (model loads/downloads on first photo)")
                model = BlipForConditionalGeneration.from_pretrained(model_id)
                state["proc"] = BlipProcessor.from_pretrained(model_id)
                state["model"] = model.to(device).eval()
                state["device"] = device
            except Exception as exc:
                print(f"Note: photo captioning disabled ({type(exc).__name__}); "
                      "photos shown as plain markers.")
                state["disabled"] = True
                return ""
        try:
            image = Image.open(path).convert("RGB")
            inputs = state["proc"](image, return_tensors="pt").to(state["device"])
            with torch.no_grad():
                generated = state["model"].generate(**inputs, max_new_tokens=40, num_beams=3)
            caption = state["proc"].decode(generated[0], skip_special_tokens=True).strip()
        except Exception as exc:
            print(f"    describe failed on {os.path.basename(path)}: {exc}")
            caption = ""
        cache[path] = " ".join(caption.split())
        return cache[path]

    return describe


def make_hq_describer(
    model_id: str = "Qwen/Qwen2-VL-2B-Instruct",
    max_frames: int = 6,
) -> Optional[Describer]:
    """High-quality scene captioner (Qwen2-VL) - single frame for stills, multi-frame for GIFs.

    Far better than BLIP on cartoons, characters and *actions* (it reads the motion across frames),
    but it is heavier (~4.4 GB) and slow on CPU - fast on a CUDA GPU. Opt-in via
    ``pip install whispergram[describe-hq]`` + ``--describe-hq``. Returns ``None`` if the deps are
    missing, and degrades to a marker if the model can't load. Captions are still best-effort - a
    grounded guess, never literal fact.
    """
    try:
        import torch
        from transformers import AutoProcessor, Qwen2VLForConditionalGeneration
    except ImportError:
        print(
            "Note: high-quality captions need `pip install whispergram[describe-hq]`; "
            "media shown as plain markers (or drop --describe-hq for the lighter describer)."
        )
        return None

    state: dict = {}
    cache: dict = {}

    def describe(path: str) -> str:
        if path in cache:
            return cache[path]
        if state.get("disabled"):
            return ""
        if "model" not in state:  # lazy one-time load on the first media item
            try:
                device = "cuda" if torch.cuda.is_available() else "cpu"
                print(f"Describer (HQ): {model_id} on {device} (loads/downloads on first use)")
                state["proc"] = AutoProcessor.from_pretrained(
                    model_id, min_pixels=128 * 28 * 28, max_pixels=384 * 28 * 28)
                model = Qwen2VLForConditionalGeneration.from_pretrained(
                    model_id, torch_dtype=torch.float32)
                state["model"] = model.to(device).eval()
                state["device"] = device
            except Exception as exc:
                print(f"Note: HQ captioning disabled ({type(exc).__name__}); using markers.")
                state["disabled"] = True
                return ""
        try:
            frames = _extract_frames(path, max_frames)
            if not frames:
                caption = ""
            else:
                proc = state["proc"]
                if len(frames) > 1:
                    prompt = ("These images are frames, in order, from a short animation. "
                              "Concisely describe in one sentence the main subject, what they "
                              "are wearing, the action, and any visible setting or sign. Only "
                              "describe what is clearly visible; do not invent details.")
                else:
                    prompt = ("Concisely describe in one sentence the main subject, what they are "
                              "wearing, and what they are doing. Only describe what is clearly "
                              "visible; do not invent details.")
                content = [{"type": "image"} for _ in frames] + [{"type": "text", "text": prompt}]
                text = proc.apply_chat_template(
                    [{"role": "user", "content": content}],
                    tokenize=False, add_generation_prompt=True)
                inputs = proc(text=[text], images=frames, return_tensors="pt").to(state["device"])
                with torch.no_grad():
                    ids = state["model"].generate(**inputs, max_new_tokens=90, do_sample=False)
                trimmed = ids[0][len(inputs.input_ids[0]):]
                caption = proc.decode(trimmed, skip_special_tokens=True).strip()
        except Exception as exc:
            print(f"    describe failed on {os.path.basename(path)}: {exc}")
            caption = ""
        cache[path] = " ".join(caption.split())
        return cache[path]

    return describe


# --------------------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------------------
def _parse_args(argv: Optional[List[str]]) -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        prog="whispergram",
        description="Merge Telegram voice/video notes into the text chat as one transcript.",
    )
    ap.add_argument("export_dirs", nargs="*", default=["."], metavar="export_dir",
                    help="Telegram/Instagram export folder(s); pass several to queue (default: .)")
    ap.add_argument("--menu", action="store_true",
                    help="interactive picker: scan a folder for chats and choose which to "
                         "transcribe and how - the easy way, no flags to remember")
    ap.add_argument("--sort", default="voice", choices=["voice", "messages", "recent", "name"],
                    help="menu order: voice (most voice notes, default), messages (most messages), "
                         "recent (most recent last message), name (A-Z)")
    ap.add_argument("--device", default="cuda", choices=["cuda", "cpu"],
                    help="cuda (GPU) or cpu; auto-falls back to cpu (default: cuda)")
    ap.add_argument("--model", default="large-v3",
                    help="whisper model: large-v3, large-v3-turbo, medium ... (default: large-v3)")
    ap.add_argument("--compute-type", default="auto",
                    help="CTranslate2 compute type: auto, float16, int8_float16, int8, float32. "
                         "auto = int8 on CPU, float16 on GPU (int8_float16 on low-VRAM GPUs so "
                         "large-v3 fits). Use int8_float16 if a GPU run hangs on a <=4 GB card")
    ap.add_argument("--lang", default=None,
                    help="force a language code (uk, ru, en ...); default: auto-detect")
    ap.add_argument("--batch-size", type=int, default=0, metavar="N",
                    help="batch audio segments for a big GPU speedup (try 8 or 16); needs a GPU. "
                         "Default 0 = sequential, best quality (esp. uk/ru)")
    ap.add_argument("--out", default=None,
                    help="output file for a single folder (default: <export_dir>/merged_chat.md)")
    ap.add_argument("--out-dir", default=None,
                    help="collect each folder's transcript here as '<chat name>.md' (for queues)")
    ap.add_argument("--no-cache", action="store_true",
                    help="don't read/write the per-folder resume cache (.whispergram_cache.json)")
    ap.add_argument("--audio-files", action="store_true",
                    help="also transcribe audio_file messages (music, memos); off by default")
    ap.add_argument("--video-files", action="store_true",
                    help="also transcribe regular video files' audio track; off by default")
    ap.add_argument("--ocr", action="store_true",
                    help="extract text from photos with local OCR (Tesseract); off by default")
    ap.add_argument("--ocr-lang", default="eng",
                    help="Tesseract language(s) for --ocr, e.g. eng or ukr+rus+eng (default: eng)")
    ap.add_argument("--no-describe", action="store_true",
                    help="skip photo scene captions (no model load/download); on by default "
                         "when the [describe] extra is installed")
    ap.add_argument("--describe-model", default="Salesforce/blip-image-captioning-large",
                    help="BLIP captioning model id (default: blip-large; "
                         "use ...-base for faster/lighter)")
    ap.add_argument("--describe-hq", action="store_true",
                    help="force the HQ describer (Qwen2-VL) + sticker/GIF captions; "
                         "auto-used by default when the [describe-hq] extra is installed")
    ap.add_argument("--offline", action="store_true",
                    help="use only already-downloaded models and make zero network calls")
    ap.add_argument("--no-media-markers", action="store_true",
                    help="omit (sticker)/(photo)/(file) markers for non-voice media")
    ap.add_argument("--dry-run", action="store_true",
                    help="map the chat without transcribing (no model load); preview the merge")
    ap.add_argument("--setup-cuda-windows", action="store_true",
                    help="copy CUDA DLLs next to ctranslate2 then exit (Windows GPU fix)")
    ap.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return ap.parse_args(argv)


def _safe_name(name: str) -> str:
    """Filesystem-safe filename stem from a chat name (falls back to 'chat')."""
    cleaned = "".join("_" if c in '<>:"/\\|?*' else c for c in (name or "")).strip().strip(".")
    return cleaned[:120] or "chat"


def _resolve_out(export_dir: str, data: dict, args: argparse.Namespace) -> str:
    """Where this folder's merged transcript is written."""
    if args.out_dir:
        name = _safe_name(data.get("name") or os.path.basename(os.path.abspath(export_dir)))
        return os.path.join(args.out_dir, f"{name}.md")
    if args.out:
        return args.out
    return os.path.join(export_dir, "merged_chat.md")


def _dedupe_output(out_path: str, used: set) -> str:
    """Never silently overwrite an earlier folder's transcript when two chats resolve to the same
    filename (common in --out-dir queues: duplicate chat names, sanitized-equal names). Append
    ' (2)', ' (3)', ... and tell the user. ``used`` holds normalized paths claimed this run."""
    def norm(p: str) -> str:
        return os.path.normcase(os.path.abspath(p))

    if norm(out_path) not in used:
        used.add(norm(out_path))
        return out_path
    root, ext = os.path.splitext(out_path)
    i = 2
    while norm(f"{root} ({i}){ext}") in used:
        i += 1
    deduped = f"{root} ({i}){ext}"
    used.add(norm(deduped))
    print(f"  note: '{os.path.basename(out_path)}' already written this run; "
          f"saving as '{os.path.basename(deduped)}' instead (duplicate chat name)")
    return deduped


def _progress(total: int):
    """Return ``(bar, on_job)`` for the media work - a tqdm bar if available, else a counter
    print. ``(None, None)`` when there is nothing to process."""
    if total <= 0:
        return None, None
    try:
        from tqdm import tqdm
    except ImportError:
        done = [0]

        def on_job(label: str) -> None:
            done[0] += 1
            print(f"  [{done[0]}/{total}] {label}", flush=True)

        return None, on_job

    bar = tqdm(total=total, unit="file", dynamic_ncols=True,
               bar_format="{l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}]{postfix}")

    def on_job(label: str) -> None:
        bar.set_postfix_str(label, refresh=False)
        bar.update(1)

    return bar, on_job


def _process_export(export_dir, *, args, transcribe, describe, photo_label, media_describe,
                    describe_media, engines, cache_enabled, used_outputs) -> bool:
    """Transcribe one export folder into its merged file. Returns False if skipped (no JSON)."""
    if not glob.glob(os.path.join(export_dir, "*.json")):
        print(f"  skip {export_dir}: no .json export found")
        return False
    if is_instagram_export(export_dir):
        messages, chat_name = _normalize_instagram(export_dir)
        data = {"name": chat_name, "messages": messages}
        print(f"  Instagram export: {chat_name} ({len(messages)} items)")
    else:
        json_path = find_json(export_dir)
        with open(json_path, encoding="utf-8-sig") as fh:  # utf-8-sig tolerates a leading BOM
            data = json.load(fh)
        messages = data.get("messages", [])
    out_path = _dedupe_output(_resolve_out(export_dir, data, args), used_outputs)

    cache_path = os.path.join(export_dir, ".whispergram_cache.json") if cache_enabled else None
    cache = _Cache(cache_path)
    t = _with_cache(transcribe, cache, engines["whisper"], export_dir)
    d = (_with_cache(describe, cache, engines["photo"], export_dir)
         if describe is not None else None)
    m = (_with_cache(media_describe, cache, engines["media"], export_dir)
         if media_describe is not None else None)

    total = count_jobs(messages, export_dir, audio_files=args.audio_files,
                       video_files=args.video_files, describe_photos=d is not None,
                       describe_media=describe_media)
    bar, on_job = _progress(total)

    lines, stats = build_transcript(
        messages, export_dir, t,
        media_markers=not args.no_media_markers,
        audio_files=args.audio_files, video_files=args.video_files,
        describe=d, photo_label=photo_label,
        media_describe=m, describe_media=describe_media,
        on_job=on_job,
    )
    if bar is not None:
        bar.close()

    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    print(f"OK  {out_path}  ({len(lines)} lines, {stats['transcribed']} transcribed, "
          f"{stats['described']} described, {stats['missing']} not exported)")
    return True


# --------------------------------------------------------------------------------------
# Interactive menu - scan a folder for chats and pick what to transcribe, no flags to remember
# --------------------------------------------------------------------------------------
_TG_MEDIA_DIRS = frozenset({"voice_messages", "video_files", "round_video_messages",
                            "photos", "stickers", "files", "audio", "videos", "gifs"})


def _date_span(messages: Iterable[dict]) -> Tuple[str, str]:
    """``(first, last)`` message date as ``YYYY-MM-DD`` across *messages*, or ``('', '')`` if none
    carry a date. ISO date strings sort chronologically, so min/max on the day string is correct."""
    days = sorted((m.get("date") or "")[:10] for m in messages if m.get("date"))
    return (days[0], days[-1]) if days else ("", "")


def _fmt_dates(first: str, last: str) -> str:
    """Compact date-span label for the menu: ``YYYY-MM-DD`` for a single day, else
    ``first..last`` (ASCII-only), or ``''`` when no dates are known."""
    if not first:
        return ""
    return first if first == last else f"{first}..{last}"


def _sort_chats(chats: List[dict], key: str) -> List[dict]:
    """Order discovered chats for the menu. ``voice`` (default) = most voice notes first;
    ``messages`` = most messages first; ``recent`` = most recent last message first; ``name`` = A-Z.
    Ties fall back to voice then total so the order is always deterministic."""
    if key == "name":
        return sorted(chats, key=lambda c: (c.get("name") or "").lower())
    keyers = {
        "voice": lambda c: (c.get("voice", 0), c.get("total", 0)),
        "messages": lambda c: (c.get("total", 0), c.get("voice", 0)),
        "recent": lambda c: (c.get("last", ""), c.get("voice", 0), c.get("total", 0)),
    }
    return sorted(chats, key=keyers.get(key, keyers["voice"]), reverse=True)


def _chat_summary(export_dir: str) -> Optional[dict]:
    """Identify a Telegram or Instagram export folder and return a one-line summary (platform, name,
    voice/photo/video counts, date span), or ``None`` if it isn't a chat export."""
    if is_instagram_export(export_dir):
        msgs, name = _normalize_instagram(export_dir)
        first, last = _date_span(msgs)
        return {"dir": export_dir, "platform": "Instagram", "name": name, "total": len(msgs),
                "voice": sum(1 for m in msgs if m.get("media_type") == "voice_message"),
                "photo": sum(1 for m in msgs if m.get("photo")),
                "video": sum(1 for m in msgs if m.get("media_type") == "video_file"),
                "first": first, "last": last}
    for j in sorted(glob.glob(os.path.join(export_dir, "*.json"))):
        try:
            with open(j, encoding="utf-8-sig") as fh:
                data = json.load(fh)
        except Exception:
            continue
        msgs = data.get("messages")
        if not isinstance(msgs, list):
            continue
        if data.get("name") or (msgs and isinstance(msgs[0], dict) and msgs[0].get("type")):
            md = [m for m in msgs if isinstance(m, dict)]
            first, last = _date_span(md)
            return {"dir": export_dir, "platform": "Telegram", "total": len(md),
                    "name": data.get("name") or os.path.basename(os.path.abspath(export_dir)),
                    "voice": sum(1 for m in md if m.get("media_type") in ("voice_message",
                                                                          "video_message")),
                    "photo": sum(1 for m in md if m.get("photo")),
                    "video": sum(1 for m in md if m.get("media_type") == "video_file"),
                    "first": first, "last": last}
    return None


def _has_export_json(export_dir: str) -> bool:
    """True if *export_dir* is itself a chat export - an Instagram thread (its ``message_*.json``)
    or a Telegram export (a ``*.json`` here whose top level carries a ``messages`` list) - and not
    just a parent folder that *contains* nested exports. So a bare run only falls into the picker
    when this folder isn't a chat on its own (a stray non-export ``*.json`` doesn't count)."""
    if is_instagram_export(export_dir):
        return True
    for j in sorted(glob.glob(os.path.join(export_dir, "*.json"))):
        try:
            with open(j, encoding="utf-8-sig") as fh:
                data = json.load(fh)
        except Exception:
            continue
        if isinstance(data.get("messages"), list):
            return True
    return False


def _discover_chats(root: str) -> List[dict]:
    """Find every Telegram/Instagram chat export under *root*, sorted voice-heavy first."""
    candidates = []
    for dirpath, dirs, files in os.walk(root):
        if any(f.endswith(".json") for f in files):
            candidates.append(dirpath)
            if "message_1.json" in files or "result.json" in files:
                dirs[:] = [d for d in dirs if d not in _TG_MEDIA_DIRS]
    chats = []
    for i, d in enumerate(candidates, 1):
        print(f"\r  scanning {i}/{len(candidates)} ...", end="", flush=True)
        s = _chat_summary(d)
        if s:
            chats.append(s)
    if candidates:
        print()
    chats.sort(key=lambda c: (c["voice"], c["total"]), reverse=True)
    return chats


def _parse_selection(text: str, n: int) -> List[int]:
    """Parse a menu selection like ``1,3-5`` or ``all`` into a sorted list of 1-based indices."""
    text = text.strip().lower()
    if text in ("", "all", "*"):
        return list(range(1, n + 1))
    picked = set()
    for part in text.replace(" ", "").split(","):
        if "-" in part:
            a, _, b = part.partition("-")
            if a.isdigit() and b.isdigit():
                picked.update(range(int(a), int(b) + 1))
        elif part.isdigit():
            picked.add(int(part))
    return sorted(i for i in picked if 1 <= i <= n)


def _ask_yes(prompt: str, default: bool) -> bool:
    ans = input(f"  {prompt} [{'Y/n' if default else 'y/N'}]: ").strip().lower()
    return default if not ans else ans.startswith("y")


def run_menu(args: argparse.Namespace,
             chats: Optional[List[dict]] = None) -> Tuple[List[str], argparse.Namespace]:
    """Interactive picker: scan for chats, let the user choose which + a quality preset, and set the
    matching options on *args*. Returns ``(selected_dirs, args)`` (empty list = nothing to do).

    *chats* may be a pre-discovered list (e.g. from the auto-menu fallback in ``main``) to skip a
    redundant second scan; when ``None`` the folder is scanned here."""
    root = args.export_dirs[0] if args.export_dirs else "."
    if chats is None:
        print(f"Scanning {os.path.abspath(root)} for chats ...")
        chats = _discover_chats(root)
    if not chats:
        print("No Telegram or Instagram exports found here. cd into the folder that contains them.")
        return [], args
    chats = _sort_chats(chats, getattr(args, "sort", "voice"))
    print(f"\nFound {len(chats)} chat(s):\n")
    print(f"  {'#':>3}  {'platform':<9} {'voice':>5} {'photo':>5} {'video':>5}  "
          f"{'dates':<22}  name")
    for i, c in enumerate(chats, 1):
        print(f"  {i:>3}  {c['platform']:<9} {c['voice']:>5} {c['photo']:>5} {c['video']:>5}  "
              f"{_fmt_dates(c.get('first', ''), c.get('last', '')):<22}  {c['name']}")

    chosen = [chats[i - 1] for i in
              _parse_selection(input("\nWhich chats? (e.g. 1,3-5 or 'all') [all]: "), len(chats))]
    if not chosen:
        print("Nothing selected.")
        return [], args

    print("\nWhat to include:")
    print("  1. Everything, best models  - transcribe voice+video, describe photos/stickers/GIFs, "
          "OCR  [recommended]")
    print("  2. Voice & video only       - fast; no image descriptions or OCR")
    print("  3. Custom")
    preset = input("Choose [1]: ").strip() or "1"
    if preset == "2":
        args.no_describe, args.video_files, args.ocr = True, True, False
    elif preset == "3":
        args.no_describe = not _ask_yes("Describe photos/stickers/GIFs (vision model)?", True)
        args.describe_hq = (not args.no_describe) and _ask_yes(
            "Use the high-quality describer (Qwen2-VL; also captions stickers/GIFs)?", True)
        args.video_files = _ask_yes("Transcribe regular videos' audio?", True)
        args.ocr = _ask_yes("OCR text from photos/screenshots (needs Tesseract)?", False)
        if args.ocr:
            args.ocr_lang = input(f"  OCR languages [{args.ocr_lang}]: ").strip() or args.ocr_lang
    else:  # 1 - everything, best models
        args.no_describe, args.describe_hq, args.video_files, args.ocr = False, True, True, True
        args.ocr_lang = (input(f"OCR languages (e.g. ukr+rus+eng) [{args.ocr_lang}]: ").strip()
                         or args.ocr_lang)

    default_out = os.path.abspath(os.path.join(root, "transcripts"))
    args.out_dir = input(f"\nOutput folder [{default_out}]: ").strip() or default_out
    print(f"\n-> {len(chosen)} chat(s) -> {args.out_dir}")
    input("Press Enter to start (Ctrl+C to cancel) ... ")
    return [c["dir"] for c in chosen], args


def _stdin_isatty() -> bool:
    """Whether we have an interactive terminal to prompt on. Defensive: ``sys.stdin`` can be
    ``None`` (pythonw / detached) or closed, so never let the check itself raise."""
    try:
        return sys.stdin is not None and sys.stdin.isatty()
    except (AttributeError, ValueError):
        return False


def _prevent_sleep():
    """Keep the system awake during a long run so idle-sleep doesn't interrupt it. Windows only
    (``SetThreadExecutionState``); a harmless no-op elsewhere. Returns a restore callable.

    Blocks *idle* sleep, not closing the laptop lid - that's a separate OS policy.
    """
    if os.name != "nt":
        return lambda: None
    try:
        import ctypes

        es_continuous = 0x80000000
        es_system_required = 0x00000001
        ctypes.windll.kernel32.SetThreadExecutionState(es_continuous | es_system_required)
        return lambda: ctypes.windll.kernel32.SetThreadExecutionState(es_continuous)
    except Exception:
        return lambda: None


def main(argv: Optional[List[str]] = None) -> int:
    """Entry point. Returns a process exit code."""
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    except (AttributeError, ValueError):
        pass

    args = _parse_args(argv)

    if args.setup_cuda_windows:
        _setup_cuda_windows()
        return 0

    export_dirs = args.export_dirs or ["."]
    for d in export_dirs:
        if not os.path.isdir(d):
            sys.exit(f"Export folder not found: {os.path.abspath(d)}")

    # Fall into the interactive picker when the target folder isn't a chat export itself but
    # contains nested exports (e.g. an Instagram `your_instagram_activity` root, or a folder holding
    # several Telegram `ChatExport_*` folders) - so a bare run there isn't a dead end. The chats we
    # discover here are handed to run_menu to avoid a second scan. Non-interactively (no TTY) we
    # can't prompt, so we point the user at --menu instead of hanging.
    # Only a SINGLE target auto-opens the picker: passing several folders is an explicit queue, so
    # we don't hijack it into a menu even if none are exports.
    chats: Optional[List[dict]] = None
    if not args.menu and len(export_dirs) == 1 and not _has_export_json(export_dirs[0]):
        chats = _discover_chats(export_dirs[0])
        if chats:
            if _stdin_isatty():
                print(f"\nNo chat export in this folder, but found {len(chats)} nested below "
                      f"- opening the picker.")
                args.menu = True
            else:
                sys.exit(f"No chat export in {os.path.abspath(export_dirs[0])}, but found "
                         f"{len(chats)} nested below. Re-run with --menu to pick them, or point "
                         f"whispergram at a specific chat folder.")

    if args.menu:
        if not _stdin_isatty():
            sys.exit("--menu needs an interactive terminal.")
        try:
            selected, args = run_menu(args, chats=chats)
        except (KeyboardInterrupt, EOFError, StopIteration):
            print("\nCancelled.")
            return 0
        if not selected:
            return 0
        export_dirs = selected

    if args.out and args.out_dir:
        sys.exit("--out and --out-dir are mutually exclusive: --out names one file, "
                 "--out-dir collects a queue as '<chat name>.md'.")
    if len(export_dirs) > 1 and args.out:
        sys.exit("--out is for a single folder; use --out-dir to collect several transcripts.")

    _configure_hf_env(args.offline)

    # Build the transcriber/describer ONCE - models load once and are reused across queued folders.
    use_hq = (not args.no_describe) and (args.describe_hq or _hq_available())
    describer: Optional[Describer] = None
    ocr: Optional[Describer] = None
    if args.dry_run:
        print("Dry run: not loading models; media is not transcribed, described, or read.")

        def transcribe(_path: str) -> str:
            return "[dry-run - not transcribed]"

        if not args.no_describe:
            def describer(_path: str) -> str:
                return "[dry-run - not described]"

        if args.ocr:
            def ocr(_path: str) -> str:
                return "[dry-run - not read]"
    else:
        model = load_model(args.model, args.device, args.compute_type)
        transcribe = make_transcriber(model, args.lang, args.batch_size)
        if args.batch_size and args.batch_size > 1:
            note = "" if args.device == "cuda" else " (needs a GPU to actually help)"
            print(f"Batched inference: batch_size={args.batch_size}{note}")
        if not args.no_describe:
            describer = make_hq_describer() if use_hq else make_describer(args.describe_model)
            if describer is None and use_hq:  # HQ deps present but load failed -> light fallback
                describer = make_describer(args.describe_model)
                use_hq = False
        if args.ocr:
            ocr = make_ocr(args.ocr_lang)

    describe, photo_label = _photo_reader(describer, ocr)
    media_describe = describer if use_hq else None
    describe_media = frozenset({"sticker", "animation"}) if use_hq else frozenset()

    desc_id = "qwen2-vl" if use_hq else f"blip:{args.describe_model}"
    engines = {
        "whisper": f"whisper:{args.model}:{args.lang}",
        "photo": f"photo:{desc_id}:{'ocr:' + args.ocr_lang if args.ocr else 'noocr'}",
        "media": f"media:{desc_id}",
    }
    cache_enabled = (not args.no_cache) and (not args.dry_run)
    if args.out_dir:
        os.makedirs(args.out_dir, exist_ok=True)

    used_outputs: set = set()
    failures: List[str] = []
    restore_sleep = _prevent_sleep()  # don't let an idle-sleep interrupt a long overnight run
    try:
        for i, export_dir in enumerate(export_dirs, 1):
            if len(export_dirs) > 1:
                print(f"\n[{i}/{len(export_dirs)}] {export_dir}")
            try:
                # Isolate each folder: one bad export (corrupt JSON, a decode/write error) must
                # not abort the rest of an overnight queue. The resume cache keeps finished work.
                _process_export(
                    export_dir, args=args, transcribe=transcribe, describe=describe,
                    photo_label=photo_label, media_describe=media_describe,
                    describe_media=describe_media, engines=engines, cache_enabled=cache_enabled,
                    used_outputs=used_outputs)
            except Exception as exc:  # noqa: BLE001 - keep the queue going, report at the end
                failures.append(export_dir)
                print(f"FAILED {export_dir}: {type(exc).__name__}: {exc}")
    finally:
        restore_sleep()

    if failures:
        print(f"\n{len(failures)} of {len(export_dirs)} folder(s) failed: {', '.join(failures)}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
