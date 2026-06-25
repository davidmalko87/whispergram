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
import glob
import json
import os
import sys
from collections import Counter
from typing import Callable, Iterable, List, Optional, Tuple

__version__ = "0.6.0"

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
) -> Tuple[List[str], Counter]:
    """Turn Telegram messages into merged transcript lines, chronological order preserved.

    *transcribe* maps an audio/video path to its transcript text; *describe* (optional) maps a
    photo path to extracted text (OCR or a caption). Missing media is never sent to either.
    Returns ``(lines, stats)`` where ``stats`` counts each outcome category.
    """
    transcribe_types = {"voice_message", "video_message"}
    if audio_files:
        transcribe_types.add("audio_file")
    if video_files:
        transcribe_types.add("video_file")
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
                extracted = describe(photo_path).strip()
                if extracted:
                    line = f"[{ts}] {who} (photo, {photo_label}): {extracted}"
                    if text:
                        line += f" | caption: {text}"
                    lines.append(line)
                    stats["described"] += 1
                    continue
            # photo missing or nothing extracted -> fall through to the plain (photo) marker

        if media_describe is not None and media_type in describe_media:
            file_field = msg.get("file") or ""
            media_path = os.path.join(export_dir, file_field)
            if file_field and not is_missing_media(file_field, media_path):
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


def load_model(model_name: str, device: str):
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

    compute = "float16" if device == "cuda" else "int8"
    try:
        model = WhisperModel(model_name, device=device, compute_type=compute)
        print(f"Model: {model_name} on {device} ({compute})")
    except Exception as exc:
        print(f"{device} failed ({exc}); falling back to CPU")
        model = WhisperModel(model_name, device="cpu", compute_type="int8")
    return model


def make_transcriber(model, lang: Optional[str]) -> Transcriber:
    """Build a caching ``transcribe(path) -> text`` closure over a loaded model."""
    cache: dict = {}

    def transcribe(path: str) -> str:
        if path not in cache:
            print(f"  transcribing {os.path.basename(path)} ...")
            segments, _ = model.transcribe(path, language=lang, vad_filter=True)
            cache[path] = " ".join(s.text.strip() for s in segments).strip() or "[no speech]"
        return cache[path]

    return transcribe


def make_ocr(lang: str) -> Describer:
    """Build a caching OCR ``describe(image_path) -> text`` closure (local Tesseract).

    The result is collapsed to a single line. Needs the Tesseract binary on PATH plus the
    language data packs for *lang* (e.g. ``ukr``, ``rus``); install the Python deps with
    ``pip install whispergram[ocr]``. A photo Tesseract cannot read returns ``""``.
    """
    try:
        import pytesseract
        from PIL import Image
    except ImportError:
        sys.exit(
            "OCR needs pytesseract + Pillow: `pip install whispergram[ocr]`, and the Tesseract "
            "binary on PATH (with language packs, e.g. ukr/rus). Or drop --ocr."
        )

    cache: dict = {}

    def describe(path: str) -> str:
        if path not in cache:
            print(f"  reading {os.path.basename(path)} ...")
            try:
                raw = pytesseract.image_to_string(Image.open(path), lang=lang)
            except Exception as exc:
                print(f"    OCR failed on {os.path.basename(path)}: {exc}")
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
        if max_frames <= 1:
            return [frames[len(frames) // 2]]
        n = min(max_frames, len(frames))
        idxs = sorted({round(i * (len(frames) - 1) / (n - 1)) for i in range(n)})
        return [frames[i] for i in idxs]
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
        print(f"  describing {os.path.basename(path)} ...")
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
        print(f"  describing {os.path.basename(path)} ...")
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
    ap.add_argument("export_dir", nargs="?", default=".",
                    help="Telegram export folder (default: current dir)")
    ap.add_argument("--device", default="cuda", choices=["cuda", "cpu"],
                    help="cuda (GPU) or cpu; auto-falls back to cpu (default: cuda)")
    ap.add_argument("--model", default="large-v3",
                    help="whisper model: large-v3, large-v3-turbo, medium ... (default: large-v3)")
    ap.add_argument("--lang", default=None,
                    help="force a language code (uk, ru, en ...); default: auto-detect")
    ap.add_argument("--out", default=None,
                    help="output file (default: <export_dir>/merged_chat.md)")
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

    if not os.path.isdir(args.export_dir):
        sys.exit(f"Export folder not found: {os.path.abspath(args.export_dir)}")

    _configure_hf_env(args.offline)
    json_path = find_json(args.export_dir)
    out_path = args.out or os.path.join(args.export_dir, "merged_chat.md")
    print(f"Export: {os.path.basename(json_path)}")

    describer: Optional[Describer] = None
    ocr: Optional[Describer] = None
    # Best installed describer by default: HQ (Qwen2-VL, the [describe-hq] extra) if available,
    # else the lighter BLIP. --describe-hq forces HQ; --no-describe turns captioning off.
    use_hq = (not args.no_describe) and (args.describe_hq or _hq_available())

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
        model = load_model(args.model, args.device)
        transcribe = make_transcriber(model, args.lang)
        if not args.no_describe:
            # None (with a hint) if the relevant describe extra isn't installed
            describer = make_hq_describer() if use_hq else make_describer(args.describe_model)
            if describer is None and use_hq:  # HQ deps present but load failed -> light fallback
                describer = make_describer(args.describe_model)
                use_hq = False
        if args.ocr:
            ocr = make_ocr(args.ocr_lang)

    describe, photo_label = _photo_reader(describer, ocr)
    # The HQ describer also captions stickers + GIFs (via the raw, OCR-free describer)
    media_describe = describer if use_hq else None
    describe_media = frozenset({"sticker", "animation"}) if use_hq else frozenset()

    with open(json_path, encoding="utf-8") as fh:
        data = json.load(fh)

    lines, stats = build_transcript(
        data.get("messages", []),
        args.export_dir,
        transcribe,
        media_markers=not args.no_media_markers,
        audio_files=args.audio_files,
        video_files=args.video_files,
        describe=describe,
        photo_label=photo_label,
        media_describe=media_describe,
        describe_media=describe_media,
    )

    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")

    print(
        f"\nOK  {out_path}\n"
        f"    {len(lines)} lines | {stats['transcribed']} transcribed | "
        f"{stats['missing']} not exported | {stats['described']} photos read | "
        f"{stats['media']} other media | {stats['text']} text"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
