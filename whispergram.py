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

__version__ = "0.1.0"

# Telegram media types whose audio we transcribe, mapped to their display label.
_KIND_LABEL = {
    "voice_message": "voice",
    "video_message": "video-note",
    "audio_file": "audio",
}

# A transcribe(path) -> text callable. Injected so the heavy faster-whisper dependency
# stays out of the pure mapping logic (and so that logic is unit-testable offline).
Transcriber = Callable[[str], str]


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
) -> Tuple[List[str], Counter]:
    """Turn Telegram messages into merged transcript lines, chronological order preserved.

    *transcribe* maps an audio file path to its transcript text. Missing media is never sent
    to it. Returns ``(lines, stats)`` where ``stats`` counts each outcome category.
    """
    transcribe_types = set(_KIND_LABEL) if audio_files else {"voice_message", "video_message"}
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


def load_model(model_name: str, device: str):
    """Load a faster-whisper model, falling back from CUDA to CPU on any load failure."""
    _register_cuda_dll_dirs()
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        sys.exit(
            "faster-whisper is not installed. Run `pip install -r requirements.txt`, "
            "or use --dry-run to preview the merge without transcribing."
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

    json_path = find_json(args.export_dir)
    out_path = args.out or os.path.join(args.export_dir, "merged_chat.md")
    print(f"Export: {os.path.basename(json_path)}")

    if args.dry_run:
        print("Dry run: not loading the model; voice/video notes are not transcribed.")

        def transcribe(_path: str) -> str:
            return "[dry-run - not transcribed]"
    else:
        model = load_model(args.model, args.device)
        transcribe = make_transcriber(model, args.lang)

    with open(json_path, encoding="utf-8") as fh:
        data = json.load(fh)

    lines, stats = build_transcript(
        data.get("messages", []),
        args.export_dir,
        transcribe,
        media_markers=not args.no_media_markers,
        audio_files=args.audio_files,
    )

    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")

    print(
        f"\nOK  {out_path}\n"
        f"    {len(lines)} lines | {stats['transcribed']} transcribed | "
        f"{stats['missing']} not exported | {stats['media']} other media | {stats['text']} text"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
