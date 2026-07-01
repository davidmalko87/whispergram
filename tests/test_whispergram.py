# tests/test_whispergram.py - offline unit tests for the merge/mapping logic.
# Author: David Malko

"""Pure-logic tests that run without faster-whisper, ffmpeg, a GPU, or any model download.

The transcription dependency is injected as a fake callable, so the entire chat-to-transcript
mapping is exercised here. These tests are what CI runs on the Python 3.9-3.13 matrix.
"""

import json
import os

import pytest

import whispergram
from whispergram import (
    __version__,
    _Cache,
    _cache_key,
    _dedupe_output,
    _discover_chats,
    _fix_mojibake,
    _has_export_json,
    _ig_media_path,
    _normalize_instagram,
    _parse_args,
    _parse_selection,
    _photo_reader,
    _safe_name,
    _with_cache,
    build_transcript,
    count_jobs,
    extract_text,
    find_json,
    is_instagram_export,
    is_missing_media,
    main,
    make_transcriber,
    media_marker,
    run_menu,
)

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "sample_export")

# The exact merged output for the synthetic fixture under a dry run. Shared by the
# build_transcript test and the main() end-to-end test so the "lossless mapping" claim has
# a single, precise guard covering every documented media type.
EXPECTED_FIXTURE_LINES = [
    "[2026-06-20 12:33] Alex: hey, did you get the files?",
    "[2026-06-20 12:33] You: yep, check https://example.com thanks",
    "[2026-06-20 12:34] Alex (voice 6s): [not exported]",
    "[2026-06-20 12:35] Alex (video-note 8s): [not exported]",
    "[2026-06-20 12:35] You (sticker OK)",
    "[2026-06-20 12:36] Alex (photo): the whiteboard from today",
    "[2026-06-20 12:36] You (animation)",
    "[2026-06-20 12:36] Alex (video)",
    "[2026-06-20 12:37] You (audio: Band - Song)",
    "[2026-06-20 12:37] Alex (file: report.pdf [not exported]): see this",
    "[2026-06-20 12:38] Alex (location)",
    "[2026-06-20 12:38] You (poll)",
    "[2026-06-20 12:38] Alex (contact)",
]


def fake_transcribe(path):
    """Stand-in for Whisper: deterministic, records nothing but the basename."""
    return f"<transcript of {os.path.basename(path)}>"


def fake_describe(path):
    """Stand-in for OCR: deterministic, returns synthetic 'image text'."""
    return f"<ocr of {os.path.basename(path)}>"


# --- extract_text: the three Telegram text shapes, defensively ------------------------
def test_extract_text_from_entities():
    msg = {"text_entities": [{"type": "plain", "text": "hello "},
                             {"type": "link", "text": "https://x.io"}]}
    assert extract_text(msg) == "hello https://x.io"


def test_extract_text_plain_string():
    assert extract_text({"text": "  just a string  "}) == "just a string"


def test_extract_text_mixed_list():
    msg = {"text": ["see ", {"type": "link", "text": "https://x.io"}, " now"]}
    assert extract_text(msg) == "see https://x.io now"


def test_extract_text_list_starts_with_entity():
    assert extract_text({"text": [{"type": "mention", "text": "@me"}, " hi"]}) == "@me hi"


def test_extract_text_entities_preferred_over_raw():
    msg = {"text": "ignored", "text_entities": [{"type": "plain", "text": "used"}]}
    assert extract_text(msg) == "used"


def test_extract_text_empty():
    assert extract_text({}) == ""
    assert extract_text({"text": ""}) == ""
    assert extract_text({"text_entities": []}) == ""


def test_extract_text_null_values_never_crash():
    assert extract_text({"text_entities": [{"type": "plain", "text": None}]}) == ""
    assert extract_text({"text": ["a", {"type": "link", "text": None}, "b"]}) == "ab"
    assert extract_text({"text": None}) == ""
    assert extract_text({"text_entities": [{"type": "plain"}]}) == ""  # 'text' key absent


def test_extract_text_non_dict_entity_does_not_crash():
    assert extract_text({"text_entities": ["bare", {"type": "plain", "text": "x"}]}) == "barex"


# --- is_missing_media -----------------------------------------------------------------
def test_missing_media_placeholder():
    assert is_missing_media("(File not included. Change data exporting settings to download.)", "x")


def test_missing_media_empty_field():
    assert is_missing_media("", "x")
    assert is_missing_media(None, "x")


def test_missing_media_nonexistent_path():
    assert is_missing_media("voice_messages/a.ogg", "/no/such/file.ogg")


def test_present_media(tmp_path):
    f = tmp_path / "a.ogg"
    f.write_bytes(b"not real audio")
    assert is_missing_media("a.ogg", str(f)) is False


# --- media_marker ---------------------------------------------------------------------
def test_marker_sticker():
    assert media_marker({"media_type": "sticker", "sticker_emoji": ":)"}) == "sticker :)"


def test_marker_sticker_without_emoji():
    assert media_marker({"media_type": "sticker"}) == "sticker"


def test_marker_photo():
    assert media_marker({"photo": "photos/p.jpg"}) == "photo"


def test_marker_animation_and_video():
    assert media_marker({"media_type": "animation"}) == "animation"
    assert media_marker({"media_type": "video_file"}) == "video"


@pytest.mark.parametrize("msg, expected", [
    ({"media_type": "audio_file", "performer": "Band", "title": "Song"}, "audio: Band - Song"),
    ({"media_type": "audio_file", "title": "Song"}, "audio: Song"),
    ({"media_type": "audio_file", "performer": "Band"}, "audio: file"),
    ({"media_type": "audio_file", "file_name": "clip.mp3"}, "audio: clip.mp3"),
    ({"media_type": "audio_file"}, "audio: file"),
])
def test_marker_audio_file_fallbacks(msg, expected):
    assert media_marker(msg) == expected


def test_marker_document():
    assert media_marker({"file_name": "report.pdf"}) == "file: report.pdf"


def test_marker_location_poll_contact_game_invoice():
    assert media_marker({"location_information": {"latitude": 1}}) == "location"
    assert media_marker({"place_name": "Cafe"}) == "location"
    assert media_marker({"poll": {"question": "q"}}) == "poll"
    assert media_marker({"contact_information": {"first_name": "S"}}) == "contact"
    assert media_marker({"game_title": "Chess"}) == "game"
    assert media_marker({"invoice_information": {"amount": 1}}) == "invoice"


def test_marker_generic_attachment():
    assert media_marker({"file": "x.bin", "thumbnail": "t.jpg"}) == "media"


def test_marker_none_for_plain_text():
    assert media_marker({"text": "hi"}) == ""


# --- build_transcript: the heart of the tool ------------------------------------------
def test_build_transcript_interleaving(tmp_path):
    audio = tmp_path / "voice_messages"
    audio.mkdir()
    (audio / "a.ogg").write_bytes(b"x")
    messages = [
        {"type": "message", "date": "2026-06-20T10:00:00", "from": "Alex",
         "text_entities": [{"type": "plain", "text": "morning"}]},
        {"type": "message", "date": "2026-06-20T10:01:00", "from": "Alex",
         "media_type": "voice_message", "duration_seconds": 5,
         "file": "voice_messages/a.ogg"},
        {"type": "message", "date": "2026-06-20T10:02:00", "from": "Bo",
         "media_type": "voice_message", "duration_seconds": 7,
         "file": "(File not included. Change data exporting settings to download.)"},
        {"type": "service", "action": "phone_call"},
    ]
    lines, stats = build_transcript(messages, str(tmp_path), fake_transcribe)
    assert lines == [
        "[2026-06-20 10:00] Alex: morning",
        "[2026-06-20 10:01] Alex (voice 5s): <transcript of a.ogg>",
        "[2026-06-20 10:02] Bo (voice 7s): [not exported]",
    ]
    assert stats["text"] == 1
    assert stats["transcribed"] == 1
    assert stats["missing"] == 1
    assert stats["service"] == 1


def test_missing_media_never_transcribed():
    """The transcriber must never be called for not-exported media."""
    calls = []

    def spy(path):
        calls.append(path)
        return "x"

    messages = [{"type": "message", "date": "2026-06-20T10:00:00", "from": "A",
                 "media_type": "voice_message",
                 "file": "(File not included. Change data exporting settings to download.)"}]
    build_transcript(messages, ".", spy)
    assert calls == []


def test_media_markers_toggle_drops_marker_but_keeps_voice(tmp_path):
    (tmp_path / "a.ogg").write_bytes(b"x")
    messages = [
        {"type": "message", "date": "2026-06-20T10:00:00", "from": "A",
         "media_type": "voice_message", "duration_seconds": 5, "file": "a.ogg"},
        {"type": "message", "date": "2026-06-20T10:01:00", "from": "A",
         "media_type": "sticker", "sticker_emoji": ":)"},
    ]
    on, _ = build_transcript(messages, str(tmp_path), fake_transcribe, media_markers=True)
    off, stats = build_transcript(messages, str(tmp_path), fake_transcribe, media_markers=False)
    assert on[1] == "[2026-06-20 10:01] A (sticker :))"
    # markers off: the sticker line is gone, but the voice note is STILL transcribed
    assert off == ["[2026-06-20 10:00] A (voice 5s): <transcript of a.ogg>"]
    assert stats["transcribed"] == 1
    assert "media" not in stats


def test_photo_with_caption():
    messages = [{"type": "message", "date": "2026-06-20T10:00:00", "from": "A",
                 "photo": "photos/p.jpg",
                 "text_entities": [{"type": "plain", "text": "look"}]}]
    lines, _ = build_transcript(messages, ".", fake_transcribe)
    assert lines == ["[2026-06-20 10:00] A (photo): look"]


def test_document_present_with_caption(tmp_path):
    (tmp_path / "files").mkdir()
    (tmp_path / "files" / "report.pdf").write_bytes(b"x")
    messages = [{"type": "message", "date": "2026-06-20T10:00:00", "from": "A",
                 "file": "files/report.pdf", "file_name": "report.pdf",
                 "text_entities": [{"type": "plain", "text": "see this"}]}]
    lines, _ = build_transcript(messages, str(tmp_path), fake_transcribe)
    assert lines == ["[2026-06-20 10:00] A (file: report.pdf): see this"]


def test_document_not_exported_is_flagged():
    messages = [{"type": "message", "date": "2026-06-20T10:00:00", "from": "A",
                 "file": "(File not included. Change data exporting settings to download.)",
                 "file_name": "report.pdf"}]
    lines, _ = build_transcript(messages, ".", fake_transcribe)
    assert lines == ["[2026-06-20 10:00] A (file: report.pdf [not exported])"]


def test_voice_caption_suffix(tmp_path):
    (tmp_path / "a.ogg").write_bytes(b"x")
    messages = [{"type": "message", "date": "2026-06-20T10:00:00", "from": "A",
                 "media_type": "voice_message", "duration_seconds": 5, "file": "a.ogg",
                 "text_entities": [{"type": "plain", "text": "my note"}]}]
    lines, _ = build_transcript(messages, str(tmp_path), fake_transcribe)
    assert lines == ["[2026-06-20 10:00] A (voice 5s): <transcript of a.ogg> | caption: my note"]


def test_fallback_duration_and_sender(tmp_path):
    (tmp_path / "a.ogg").write_bytes(b"x")
    messages = [
        {"type": "message", "date": "2026-06-20T10:00:00",  # no 'from', no 'duration_seconds'
         "media_type": "voice_message", "file": "a.ogg"},
        {"type": "message", "date": "2026-06-20T10:01:00"},  # no content -> empty
    ]
    lines, stats = build_transcript(messages, str(tmp_path), fake_transcribe)
    assert lines == ["[2026-06-20 10:00] Unknown (voice ?s): <transcript of a.ogg>"]
    assert stats["empty"] == 1


def test_location_poll_contact_not_dropped():
    messages = [
        {"type": "message", "date": "2026-06-20T10:00:00", "from": "A",
         "location_information": {"latitude": 1}},
        {"type": "message", "date": "2026-06-20T10:01:00", "from": "A", "poll": {"question": "q"}},
        {"type": "message", "date": "2026-06-20T10:02:00", "from": "A",
         "contact_information": {"first_name": "S"}},
    ]
    lines, stats = build_transcript(messages, ".", fake_transcribe)
    assert lines == [
        "[2026-06-20 10:00] A (location)",
        "[2026-06-20 10:01] A (poll)",
        "[2026-06-20 10:02] A (contact)",
    ]
    assert stats["media"] == 3
    assert "empty" not in stats


def test_audio_files_opt_in(tmp_path):
    (tmp_path / "song.mp3").write_bytes(b"x")
    messages = [{"type": "message", "date": "2026-06-20T10:00:00", "from": "A",
                 "media_type": "audio_file", "duration_seconds": 200,
                 "performer": "Band", "title": "Song", "file": "song.mp3"}]
    default, _ = build_transcript(messages, str(tmp_path), fake_transcribe)
    assert default == ["[2026-06-20 10:00] A (audio: Band - Song)"]
    opted, stats = build_transcript(messages, str(tmp_path), fake_transcribe, audio_files=True)
    assert opted == ["[2026-06-20 10:00] A (audio 200s): <transcript of song.mp3>"]
    assert stats["transcribed"] == 1


# --- video files (regular videos, not round video notes) ------------------------------
def test_video_file_marker_by_default():
    messages = [{"type": "message", "date": "2026-06-20T10:00:00", "from": "A",
                 "media_type": "video_file"}]
    lines, _ = build_transcript(messages, ".", fake_transcribe)
    assert lines == ["[2026-06-20 10:00] A (video)"]


def test_video_file_transcribed_when_enabled(tmp_path):
    (tmp_path / "v.mp4").write_bytes(b"x")
    messages = [{"type": "message", "date": "2026-06-20T10:00:00", "from": "A",
                 "media_type": "video_file", "duration_seconds": 12, "file": "v.mp4"}]
    lines, stats = build_transcript(messages, str(tmp_path), fake_transcribe, video_files=True)
    assert lines == ["[2026-06-20 10:00] A (video 12s): <transcript of v.mp4>"]
    assert stats["transcribed"] == 1


def test_video_files_flag_does_not_pull_in_audio_files(tmp_path):
    """--video-files must not also transcribe audio_file (each flag is independent)."""
    (tmp_path / "song.mp3").write_bytes(b"x")
    messages = [{"type": "message", "date": "2026-06-20T10:00:00", "from": "A",
                 "media_type": "audio_file", "performer": "B", "title": "S", "file": "song.mp3"}]
    lines, _ = build_transcript(messages, str(tmp_path), fake_transcribe, video_files=True)
    assert lines == ["[2026-06-20 10:00] A (audio: B - S)"]


# --- photo OCR (injected describer) ---------------------------------------------------
def test_photo_ocr_when_enabled(tmp_path):
    (tmp_path / "photos").mkdir()
    (tmp_path / "photos" / "p.jpg").write_bytes(b"x")
    messages = [{"type": "message", "date": "2026-06-20T10:00:00", "from": "A",
                 "photo": "photos/p.jpg"}]
    lines, stats = build_transcript(
        messages, str(tmp_path), fake_transcribe, describe=fake_describe)
    assert lines == ["[2026-06-20 10:00] A (photo, text): <ocr of p.jpg>"]
    assert stats["described"] == 1


def test_photo_ocr_with_caption(tmp_path):
    (tmp_path / "p.jpg").write_bytes(b"x")
    messages = [{"type": "message", "date": "2026-06-20T10:00:00", "from": "A", "photo": "p.jpg",
                 "text_entities": [{"type": "plain", "text": "look"}]}]
    lines, _ = build_transcript(messages, str(tmp_path), fake_transcribe, describe=fake_describe)
    assert lines == ["[2026-06-20 10:00] A (photo, text): <ocr of p.jpg> | caption: look"]


def test_photo_ocr_empty_falls_back_to_marker(tmp_path):
    (tmp_path / "p.jpg").write_bytes(b"x")
    messages = [{"type": "message", "date": "2026-06-20T10:00:00", "from": "A", "photo": "p.jpg",
                 "text_entities": [{"type": "plain", "text": "hi"}]}]
    lines, stats = build_transcript(
        messages, str(tmp_path), fake_transcribe, describe=lambda p: "   ")
    assert lines == ["[2026-06-20 10:00] A (photo): hi"]
    assert stats["media"] == 1 and "described" not in stats


def test_photo_ocr_missing_file_not_called():
    calls = []

    def spy(path):
        calls.append(path)
        return "text"

    messages = [{"type": "message", "date": "2026-06-20T10:00:00", "from": "A",
                 "photo": "(File not included. Change data exporting settings to download.)"}]
    lines, _ = build_transcript(messages, ".", fake_transcribe, describe=spy)
    assert calls == []
    assert lines == ["[2026-06-20 10:00] A (photo)"]


def test_photo_unchanged_without_describer():
    """With no describer, photos stay plain markers (default behaviour, backward compatible)."""
    messages = [{"type": "message", "date": "2026-06-20T10:00:00", "from": "A", "photo": "p.jpg",
                 "text_entities": [{"type": "plain", "text": "hi"}]}]
    lines, _ = build_transcript(messages, ".", fake_transcribe)
    assert lines == ["[2026-06-20 10:00] A (photo): hi"]


# --- HQ media describe: stickers + animations (the --describe-hq path) ----------------
def test_media_describe_sticker(tmp_path):
    (tmp_path / "stickers").mkdir()
    (tmp_path / "stickers" / "s.webp").write_bytes(b"x")
    messages = [{"type": "message", "date": "2026-06-20T10:00:00", "from": "A",
                 "media_type": "sticker", "sticker_emoji": ":)", "file": "stickers/s.webp"}]
    lines, stats = build_transcript(
        messages, str(tmp_path), fake_transcribe,
        media_describe=fake_describe, describe_media=frozenset({"sticker"}))
    assert lines == ["[2026-06-20 10:00] A (sticker :), described): <ocr of s.webp>"]
    assert stats["described"] == 1


def test_media_describe_animation(tmp_path):
    (tmp_path / "video_files").mkdir()
    (tmp_path / "video_files" / "g.mp4").write_bytes(b"x")
    messages = [{"type": "message", "date": "2026-06-20T10:00:00", "from": "A",
                 "media_type": "animation", "file": "video_files/g.mp4"}]
    lines, _ = build_transcript(
        messages, str(tmp_path), fake_transcribe,
        media_describe=fake_describe, describe_media=frozenset({"animation"}))
    assert lines == ["[2026-06-20 10:00] A (animation, described): <ocr of g.mp4>"]


def test_media_describe_off_by_default():
    """Without describe_media, stickers/animations stay plain markers (default behaviour)."""
    messages = [{"type": "message", "date": "2026-06-20T10:00:00", "from": "A",
                 "media_type": "sticker", "sticker_emoji": ":)"}]
    lines, _ = build_transcript(messages, ".", fake_transcribe, media_describe=fake_describe)
    assert lines == ["[2026-06-20 10:00] A (sticker :))"]


def test_media_describe_missing_file_not_called():
    calls = []

    def spy(path):
        calls.append(path)
        return "x"

    messages = [{"type": "message", "date": "2026-06-20T10:00:00", "from": "A",
                 "media_type": "animation",
                 "file": "(File not included. Change data exporting settings to download.)"}]
    lines, _ = build_transcript(messages, ".", fake_transcribe,
                                media_describe=spy, describe_media=frozenset({"animation"}))
    assert calls == []
    assert lines == ["[2026-06-20 10:00] A (animation [not exported])"]


# --- _photo_reader: composing scene-describe (--describe) with OCR (--ocr) -------------
def test_photo_reader_describe_only():
    fn, label = _photo_reader(lambda p: "a cat on a sofa", None)
    assert label == "described"
    assert fn("x.jpg") == "a cat on a sofa"


def test_photo_reader_ocr_only():
    fn, label = _photo_reader(None, lambda p: "INVOICE 42")
    assert label == "text"
    assert fn("x.jpg") == "INVOICE 42"


def test_photo_reader_both_merges():
    fn, label = _photo_reader(lambda p: "a receipt", lambda p: "Total 9.99")
    assert label == "described"
    assert fn("x.jpg") == "a receipt | text: Total 9.99"


def test_photo_reader_both_with_one_side_empty():
    only_text, _ = _photo_reader(lambda p: "   ", lambda p: "Total 9.99")
    assert only_text("x.jpg") == "text: Total 9.99"
    only_caption, _ = _photo_reader(lambda p: "a receipt", lambda p: "   ")
    assert only_caption("x.jpg") == "a receipt"


def test_photo_reader_neither():
    fn, label = _photo_reader(None, None)
    assert fn is None and label == "text"


def test_build_transcript_describe_plus_ocr(tmp_path):
    (tmp_path / "p.jpg").write_bytes(b"x")
    describe, label = _photo_reader(lambda p: "a whiteboard", lambda p: "Sprint")
    messages = [{"type": "message", "date": "2026-06-20T10:00:00", "from": "A", "photo": "p.jpg"}]
    lines, stats = build_transcript(
        messages, str(tmp_path), fake_transcribe, describe=describe, photo_label=label)
    assert lines == ["[2026-06-20 10:00] A (photo, described): a whiteboard | text: Sprint"]
    assert stats["described"] == 1


# --- find_json ------------------------------------------------------------------------
def test_find_json_prefers_exact_result(tmp_path):
    (tmp_path / "result_v2.json").write_text("{}")
    (tmp_path / "result.json").write_text("{}")
    assert os.path.basename(find_json(str(tmp_path))) == "result.json"


def test_find_json_substring_when_no_exact(tmp_path):
    (tmp_path / "chat_result_1.json").write_text("{}")
    (tmp_path / "zzz.json").write_text("{}")
    assert os.path.basename(find_json(str(tmp_path))) == "chat_result_1.json"


def test_find_json_sorted_first_when_no_result(tmp_path):
    (tmp_path / "b.json").write_text("{}")
    (tmp_path / "a.json").write_text("{}")
    assert os.path.basename(find_json(str(tmp_path))) == "a.json"


def test_find_json_single(tmp_path):
    (tmp_path / "export_anything.json").write_text("{}")
    assert os.path.basename(find_json(str(tmp_path))) == "export_anything.json"


def test_find_json_none_exits(tmp_path):
    with pytest.raises(SystemExit):
        find_json(str(tmp_path))


# --- end-to-end over the committed synthetic fixture ----------------------------------
def test_sample_fixture_lossless_mapping():
    with open(os.path.join(FIXTURE, "result.json"), encoding="utf-8") as fh:
        data = json.load(fh)
    lines, stats = build_transcript(
        data["messages"], FIXTURE, lambda p: "[dry-run - not transcribed]"
    )
    assert lines == EXPECTED_FIXTURE_LINES
    # Every content-bearing message is represented; only the service + 1 empty are skipped.
    assert dict(stats) == {"text": 2, "missing": 2, "media": 9, "service": 1, "empty": 1}


# --- CLI: main() end-to-end -----------------------------------------------------------
def test_main_dry_run_writes_file(tmp_path):
    out = tmp_path / "merged.md"
    rc = main(["--dry-run", FIXTURE, "--out", str(out)])
    assert rc == 0
    assert out.read_text(encoding="utf-8") == "\n".join(EXPECTED_FIXTURE_LINES) + "\n"


def test_main_missing_dir_exits(tmp_path):
    with pytest.raises(SystemExit):
        main(["--dry-run", str(tmp_path / "does_not_exist")])


def test_main_describes_photos_by_default(tmp_path):
    """Photo captioning is on by default; --no-describe turns it off (dry-run uses a stub)."""
    (tmp_path / "photos").mkdir()
    (tmp_path / "photos" / "p.jpg").write_bytes(b"x")
    (tmp_path / "result.json").write_text(json.dumps({"messages": [
        {"type": "message", "date": "2026-06-20T10:00:00", "from": "A", "photo": "photos/p.jpg"}]}))
    out = tmp_path / "m.md"

    main(["--dry-run", str(tmp_path), "--out", str(out)])
    assert out.read_text(encoding="utf-8").strip() == (
        "[2026-06-20 10:00] A (photo, described): [dry-run - not described]")

    main(["--dry-run", "--no-describe", str(tmp_path), "--out", str(out)])
    assert out.read_text(encoding="utf-8").strip() == "[2026-06-20 10:00] A (photo)"


def test_main_auto_uses_hq_when_available(tmp_path, monkeypatch):
    """By default (no flag), the HQ describer + sticker/GIF captions are used iff [describe-hq] is
    installed; otherwise stickers/GIFs stay plain markers."""
    (tmp_path / "stickers").mkdir()
    (tmp_path / "stickers" / "s.webp").write_bytes(b"x")
    (tmp_path / "result.json").write_text(json.dumps({"messages": [
        {"type": "message", "date": "2026-06-20T10:00:00", "from": "A",
         "media_type": "sticker", "sticker_emoji": ":)", "file": "stickers/s.webp"}]}))
    out = tmp_path / "m.md"

    monkeypatch.setattr(whispergram, "_hq_available", lambda: True)
    main(["--dry-run", str(tmp_path), "--out", str(out)])
    assert out.read_text(encoding="utf-8").strip() == (
        "[2026-06-20 10:00] A (sticker :), described): [dry-run - not described]")

    monkeypatch.setattr(whispergram, "_hq_available", lambda: False)
    main(["--dry-run", str(tmp_path), "--out", str(out)])
    assert out.read_text(encoding="utf-8").strip() == "[2026-06-20 10:00] A (sticker :))"


def test_configure_hf_env(monkeypatch):
    """Telemetry is disabled by default; --offline forces cache-only, zero-network env vars."""
    from whispergram import _configure_hf_env
    for var in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE", "HF_HUB_DISABLE_TELEMETRY"):
        monkeypatch.delenv(var, raising=False)

    _configure_hf_env(offline=False)
    assert os.environ["HF_HUB_DISABLE_TELEMETRY"] == "1"
    assert "HF_HUB_OFFLINE" not in os.environ

    _configure_hf_env(offline=True)
    assert os.environ["HF_HUB_OFFLINE"] == "1"
    assert os.environ["TRANSFORMERS_OFFLINE"] == "1"


# --- metadata -------------------------------------------------------------------------
# --- resume cache + progress total + queue (v0.7.0) -----------------------------------
def test_cache_persists_and_reloads(tmp_path):
    p = str(tmp_path / ".whispergram_cache.json")
    _Cache(p).put("k1", "hello")          # write + flush
    c = _Cache(p)
    c.put("k2", "world")
    fresh = _Cache(p)                     # a later run reads what was flushed -> resume
    assert fresh.get("k1") == "hello"
    assert fresh.get("k2") == "world"
    assert fresh.get("missing") is None


def test_cache_none_path_is_memory_only():
    c = _Cache(None)
    c.put("k", "v")
    assert c.get("k") == "v"  # works in-memory; nothing persisted


def test_with_cache_skips_recompute(tmp_path):
    calls = []

    def fn(path):
        calls.append(path)
        return f"<{os.path.basename(path)}>"

    a = str(tmp_path / "a.ogg")
    (tmp_path / "a.ogg").write_bytes(b"x")
    cpath = str(tmp_path / "c.json")
    wrapped = _with_cache(fn, _Cache(cpath), "whisper:large-v3:None", str(tmp_path))
    assert wrapped(a) == "<a.ogg>"
    assert wrapped(a) == "<a.ogg>"
    assert calls == [a]  # computed once; second call served from cache

    calls.clear()  # a NEW run with a reloaded cache also skips recompute (resume)
    wrapped2 = _with_cache(fn, _Cache(cpath), "whisper:large-v3:None", str(tmp_path))
    assert wrapped2(a) == "<a.ogg>"
    assert calls == []


def test_with_cache_none_disables():
    def fn(_p):
        return "x"
    assert _with_cache(fn, None, "eng", ".") is fn  # no cache -> passthrough


def test_with_cache_skips_empty_results(tmp_path):
    """A failed/disabled describer returns '' - that must NOT be cached, or a later run with a
    working model would forever read the empty hit and never re-caption the file."""
    state = {"working": False}

    def fn(_path):
        return "a real caption" if state["working"] else ""

    img = str(tmp_path / "p.jpg")
    (tmp_path / "p.jpg").write_bytes(b"x")
    cpath = str(tmp_path / "c.json")
    w1 = _with_cache(fn, _Cache(cpath), "photo:eng", str(tmp_path))
    assert w1(img) == ""  # model disabled this run; nothing persisted

    state["working"] = True  # next run, model works -> must recompute, not serve the empty ""
    w2 = _with_cache(fn, _Cache(cpath), "photo:eng", str(tmp_path))
    assert w2(img) == "a real caption"


def test_cache_key_distinguishes_subfolders(tmp_path):
    """Same basename + same size in different Telegram subfolders must not collide."""
    for sub in ("voice_messages", "round_video_messages"):
        (tmp_path / sub).mkdir()
        (tmp_path / sub / "a.ogg").write_bytes(b"1234")  # identical name and size
    k1 = _cache_key("whisper:x", str(tmp_path / "voice_messages" / "a.ogg"), str(tmp_path))
    k2 = _cache_key("whisper:x", str(tmp_path / "round_video_messages" / "a.ogg"), str(tmp_path))
    assert k1 != k2


def test_count_jobs_matches_on_job(tmp_path):
    (tmp_path / "voice_messages").mkdir()
    (tmp_path / "voice_messages" / "a.ogg").write_bytes(b"x")
    (tmp_path / "photos").mkdir()
    (tmp_path / "photos" / "p.jpg").write_bytes(b"x")
    messages = [
        {"type": "message", "date": "2026-06-20T10:00:00", "from": "A",
         "media_type": "voice_message", "file": "voice_messages/a.ogg"},
        {"type": "message", "date": "2026-06-20T10:01:00", "from": "A", "photo": "photos/p.jpg"},
        {"type": "message", "date": "2026-06-20T10:02:00", "from": "A",
         "text_entities": [{"type": "plain", "text": "hi"}]},
        {"type": "message", "date": "2026-06-20T10:03:00", "from": "A",
         "media_type": "voice_message",
         "file": "(File not included. Change data exporting settings to download.)"},
    ]
    total = count_jobs(messages, str(tmp_path), describe_photos=True)
    calls = []
    build_transcript(messages, str(tmp_path), fake_transcribe, describe=fake_describe,
                     on_job=lambda label: calls.append(label))
    assert total == len(calls) == 2  # present voice + photo; missing voice + text excluded


def test_safe_name():
    out = _safe_name('Anastasia / Tinder: <work>')
    assert not any(c in out for c in '<>:"/\\|?*')
    assert _safe_name("") == "chat"
    assert _safe_name(None) == "chat"


def test_main_queue_two_folders(tmp_path):
    for sub, name in (("a", "Alice"), ("b", "Bob")):
        d = tmp_path / sub
        d.mkdir()
        (d / "result.json").write_text(json.dumps({"name": name, "messages": [
            {"type": "message", "date": "2026-06-20T10:00:00", "from": "X",
             "text_entities": [{"type": "plain", "text": "hi"}]}]}))
    out_dir = tmp_path / "merged"
    main(["--dry-run", "--no-describe", str(tmp_path / "a"), str(tmp_path / "b"),
          "--out-dir", str(out_dir)])
    assert (out_dir / "Alice.md").read_text(encoding="utf-8").strip() == "[2026-06-20 10:00] X: hi"
    assert (out_dir / "Bob.md").read_text(encoding="utf-8").strip() == "[2026-06-20 10:00] X: hi"


def _write_export(folder, chat_name, text):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "result.json").write_text(json.dumps({"name": chat_name, "messages": [
        {"type": "message", "date": "2026-06-20T10:00:00", "from": "X",
         "text_entities": [{"type": "plain", "text": text}]}]}))


def test_dedupe_output_avoids_overwrite(tmp_path):
    used = set()
    a = str(tmp_path / "Work.md")
    b = str(tmp_path / "Work.md")
    assert _dedupe_output(a, used) == a              # first claim keeps the name
    assert _dedupe_output(b, used) == str(tmp_path / "Work (2).md")  # second is disambiguated
    assert _dedupe_output(b, used) == str(tmp_path / "Work (3).md")  # and again


def test_main_out_dir_same_chat_name_no_data_loss(tmp_path):
    """Two folders whose chats share a name must NOT clobber each other in --out-dir."""
    _write_export(tmp_path / "a", "Work", "from A")
    _write_export(tmp_path / "b", "Work", "from B")
    out_dir = tmp_path / "merged"
    rc = main(["--dry-run", "--no-describe", str(tmp_path / "a"), str(tmp_path / "b"),
               "--out-dir", str(out_dir)])
    assert rc == 0
    assert "from A" in (out_dir / "Work.md").read_text(encoding="utf-8")
    assert "from B" in (out_dir / "Work (2).md").read_text(encoding="utf-8")


def test_main_queue_continues_after_bad_folder(tmp_path):
    """A corrupt export must not abort the queue; good folders still run, exit code is non-zero."""
    _write_export(tmp_path / "good1", "G1", "one")
    (tmp_path / "bad").mkdir()
    (tmp_path / "bad" / "result.json").write_text("{ this is not valid json ")
    _write_export(tmp_path / "good2", "G2", "two")
    out_dir = tmp_path / "merged"
    rc = main(["--dry-run", "--no-describe",
               str(tmp_path / "good1"), str(tmp_path / "bad"), str(tmp_path / "good2"),
               "--out-dir", str(out_dir)])
    assert rc == 1  # the bad folder is reported as a failure
    assert "one" in (out_dir / "G1.md").read_text(encoding="utf-8")   # folder before the bad one
    assert "two" in (out_dir / "G2.md").read_text(encoding="utf-8")   # AND the one after it


def test_out_and_out_dir_mutually_exclusive(tmp_path):
    _write_export(tmp_path / "a", "A", "hi")
    with pytest.raises(SystemExit):
        main(["--dry-run", "--no-describe", str(tmp_path / "a"),
              "--out", str(tmp_path / "x.md"), "--out-dir", str(tmp_path / "out")])


# --- round-trip: rich export -> full pipeline -> exact diff -> prove resume ------------
# The transcriber/describer can't run offline, so they are injected as deterministic, call-
# counting fakes. Everything else (cache, queue, progress, output, every media branch) is the
# real code path. This is the "only a verified round-trip counts" check: build a chat that uses
# every documented media type, run whispergram for real, and diff the merged file line-for-line.
_RICH_MESSAGES = [
    {"date": "2026-06-20T12:00:00", "from": "Alex", "text": "hello"},
    {"date": "2026-06-20T12:01:00", "from": "Alex", "media_type": "voice_message",
     "file": "voice_messages/v1.ogg", "duration_seconds": 6},
    {"date": "2026-06-20T12:02:00", "from": "You", "media_type": "video_message",
     "file": "round_video_messages/r1.mp4", "duration_seconds": 8},
    {"date": "2026-06-20T12:03:00", "from": "Alex", "media_type": "audio_file",
     "file": "files/song.mp3", "duration_seconds": 200},
    {"date": "2026-06-20T12:04:00", "from": "You", "media_type": "video_file",
     "file": "video_files/clip.mp4", "duration_seconds": 12},
    {"date": "2026-06-20T12:05:00", "from": "Alex", "photo": "photos/p.jpg", "text": "whiteboard"},
    {"date": "2026-06-20T12:06:00", "from": "You", "media_type": "sticker",
     "file": "stickers/s.webp"},
    {"date": "2026-06-20T12:07:00", "from": "Alex", "media_type": "animation",
     "file": "files/g.mp4"},
    {"date": "2026-06-20T12:08:00", "from": "You", "media_type": "voice_message",
     "file": "(File not included. Change data exporting settings to download.)",
     "duration_seconds": 3},
    {"date": "2026-06-20T12:09:00", "from": "Alex", "text": "bye"},
]

_RICH_EXPECTED = [
    "[2026-06-20 12:00] Alex: hello",
    "[2026-06-20 12:01] Alex (voice 6s): <t:v1.ogg>",
    "[2026-06-20 12:02] You (video-note 8s): <t:r1.mp4>",
    "[2026-06-20 12:03] Alex (audio 200s): <t:song.mp3>",
    "[2026-06-20 12:04] You (video 12s): <t:clip.mp4>",
    "[2026-06-20 12:05] Alex (photo, described): <d:p.jpg> | caption: whiteboard",
    "[2026-06-20 12:06] You (sticker, described): <d:s.webp>",
    "[2026-06-20 12:07] Alex (animation, described): <d:g.mp4>",
    "[2026-06-20 12:08] You (voice 3s): [not exported]",
    "[2026-06-20 12:09] Alex: bye",
]


def _build_rich_export(folder):
    """Write the rich export + every referenced media file so is_missing_media() sees them."""
    folder.mkdir(parents=True, exist_ok=True)
    for msg in _RICH_MESSAGES:
        ref = msg.get("file") or msg.get("photo") or ""
        if ref and "(" not in ref:  # skip the not-exported placeholder
            f = folder / ref
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_bytes(b"media-bytes")
    messages = [
        {"type": "message", **m,
         **({"text_entities": [{"type": "plain", "text": m["text"]}]} if "text" in m else {})}
        for m in _RICH_MESSAGES
    ]
    for m in messages:
        m.pop("text", None)
    (folder / "result.json").write_text(
        json.dumps({"name": "Rich Chat", "messages": messages}), encoding="utf-8")


def _inject_fake_models(monkeypatch, t_calls, d_calls):
    """Replace the heavy factories with deterministic, call-recording fakes (HQ path: the
    describer also captions stickers/GIFs, so all three model-call sites are exercised)."""
    def fake_t(path):
        t_calls.append(os.path.basename(path))
        return f"<t:{os.path.basename(path)}>"

    def fake_d(path):
        d_calls.append(os.path.basename(path))
        return f"<d:{os.path.basename(path)}>"

    monkeypatch.setattr(whispergram, "load_model", lambda *a, **k: object())
    monkeypatch.setattr(whispergram, "make_transcriber", lambda *a, **k: fake_t)
    monkeypatch.setattr(whispergram, "_hq_available", lambda: True)
    monkeypatch.setattr(whispergram, "make_hq_describer", lambda *a, **k: fake_d)


def test_round_trip_every_media_type(tmp_path, monkeypatch):
    export = tmp_path / "ChatExport"
    _build_rich_export(export)
    out = tmp_path / "merged.md"
    t_calls, d_calls = [], []
    _inject_fake_models(monkeypatch, t_calls, d_calls)

    rc = main(["--audio-files", "--video-files", str(export), "--out", str(out)])

    assert rc == 0
    lines = out.read_text(encoding="utf-8").splitlines()
    assert lines == _RICH_EXPECTED                       # exact line-for-line fidelity
    assert t_calls == ["v1.ogg", "r1.mp4", "song.mp3", "clip.mp4"]  # 4 present, missing skipped
    assert d_calls == ["p.jpg", "s.webp", "g.mp4"]       # photo + sticker + animation


def test_round_trip_resume_recomputes_nothing(tmp_path, monkeypatch):
    """Second run over the same export must serve every item from the on-disk cache."""
    export = tmp_path / "ChatExport"
    _build_rich_export(export)
    out = tmp_path / "merged.md"

    t1, d1 = [], []
    _inject_fake_models(monkeypatch, t1, d1)
    main(["--audio-files", "--video-files", str(export), "--out", str(out)])
    assert (t1, d1) != ([], [])                          # first run did the work
    assert (export / ".whispergram_cache.json").exists()

    t2, d2 = [], []                                      # a fresh run, fresh call recorders
    _inject_fake_models(monkeypatch, t2, d2)
    main(["--audio-files", "--video-files", str(export), "--out", str(out)])
    assert t2 == [] and d2 == []                         # resume: nothing recomputed
    assert out.read_text(encoding="utf-8").splitlines() == _RICH_EXPECTED  # identical output


# --- batched inference + BOM tolerance (v0.8.0) ---------------------------------------
class _FakeSeg:
    def __init__(self, text):
        self.text = text


class _FakeModel:
    """Stand-in for a loaded WhisperModel: records calls, returns fixed segments."""
    def __init__(self):
        self.calls = []

    def transcribe(self, path, language=None, vad_filter=True, **kw):
        self.calls.append({"path": path, "batch_size": kw.get("batch_size")})
        return [_FakeSeg(" hello "), _FakeSeg("world ")], None


def test_make_transcriber_sequential_default():
    """batch_size 0 uses the plain model.transcribe path (no faster-whisper import needed)."""
    m = _FakeModel()
    transcribe = make_transcriber(m, None)            # default batch_size=0
    assert transcribe("a.ogg") == "hello world"
    assert transcribe("a.ogg") == "hello world"       # cached, model called once
    assert len(m.calls) == 1
    assert m.calls[0]["batch_size"] is None           # plain model gets no batch_size kwarg


def test_make_transcriber_empty_is_no_speech():
    class Silent(_FakeModel):
        def transcribe(self, path, language=None, vad_filter=True, **kw):
            return [], None
    assert make_transcriber(Silent(), None)("x.ogg") == "[no speech]"


def test_make_transcriber_one_bad_file_does_not_abort():
    """A file that makes the model raise (e.g. a video with no audio) must degrade to a marker,
    not propagate and kill the whole folder's queue."""
    class Boom(_FakeModel):
        def transcribe(self, path, language=None, vad_filter=True, **kw):
            raise IndexError("tuple index out of range")
    assert make_transcriber(Boom(), None)("video_13.mp4") == "[transcription failed]"


def test_sample_indices_handles_single_frame():
    # the .webm 1-frame case that used to divide by zero
    assert whispergram._sample_indices(1, 6) == [0]
    assert whispergram._sample_indices(0, 6) == [0]      # empty guard, no crash
    assert whispergram._sample_indices(5, 1) == [2]      # max_frames<=1 -> middle frame
    idxs = whispergram._sample_indices(10, 6)            # normal: 6 in-order, in-range indices
    assert idxs == sorted(idxs) and len(idxs) == 6 and idxs[0] == 0 and idxs[-1] == 9
    assert all(0 <= i < 10 for i in idxs)


def test_batch_size_flag_parses():
    assert _parse_args(["."]).batch_size == 0               # default off
    assert _parse_args(["--batch-size", "16", "."]).batch_size == 16


def test_main_tolerates_utf8_bom_json(tmp_path):
    """Some exports/edited JSON carry a UTF-8 BOM; it must not crash the run."""
    d = tmp_path / "exp"
    d.mkdir()
    payload = {"name": "BOM", "messages": [
        {"type": "message", "date": "2026-06-20T10:00:00", "from": "X",
         "text_entities": [{"type": "plain", "text": "hi"}]}]}
    (d / "result.json").write_text(json.dumps(payload), encoding="utf-8-sig")  # writes a BOM
    out = tmp_path / "merged.md"
    rc = main(["--dry-run", "--no-describe", str(d), "--out", str(out)])
    assert rc == 0
    assert out.read_text(encoding="utf-8").strip() == "[2026-06-20 10:00] X: hi"


def test_tgs_sticker_left_as_marker(tmp_path):
    """.tgs (Lottie animated stickers) can't be opened by a vision model: skip the describe
    attempt, render a plain marker, and don't count it as a job (keeps the bar total honest)."""
    (tmp_path / "stickers").mkdir()
    (tmp_path / "stickers" / "a.tgs").write_bytes(b"x")    # present but undescribable
    (tmp_path / "stickers" / "b.webp").write_bytes(b"x")   # present + describable
    messages = [
        {"type": "message", "date": "2026-06-20T10:00:00", "from": "A",
         "media_type": "sticker", "file": "stickers/a.tgs"},
        {"type": "message", "date": "2026-06-20T10:01:00", "from": "A",
         "media_type": "sticker", "file": "stickers/b.webp"},
    ]
    dm = frozenset({"sticker", "animation"})
    total = count_jobs(messages, str(tmp_path), describe_media=dm)
    calls = []
    lines, _ = build_transcript(messages, str(tmp_path), fake_transcribe,
                                media_describe=fake_describe, describe_media=dm,
                                on_job=lambda label: calls.append(label))
    assert total == len(calls) == 1                        # only the .webp is a describe job
    assert calls == ["b.webp"]
    assert "[2026-06-20 10:00] A (sticker)" in lines       # .tgs -> plain marker, not described
    assert any("(sticker, described): <ocr of b.webp>" in ln for ln in lines)


def test_is_describable():
    assert whispergram._is_describable("x/AnimatedSticker.tgs") is False
    assert whispergram._is_describable("x/photo.JPG") is True
    assert whispergram._is_describable("x/sticker.webp") is True


def test_find_tesseract(monkeypatch):
    from whispergram import _find_tesseract
    # already on PATH -> None (pytesseract's default discovery is fine)
    monkeypatch.setattr(whispergram.shutil, "which", lambda _n: "C:/x/tesseract.exe")
    assert _find_tesseract() is None
    # not on PATH but present at the standard install location -> return that path
    monkeypatch.setattr(whispergram.shutil, "which", lambda _n: None)
    std = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
    monkeypatch.setattr(whispergram.os.path, "isfile", lambda p: p == std)
    assert _find_tesseract() == std
    # not on PATH and nowhere standard -> None
    monkeypatch.setattr(whispergram.os.path, "isfile", lambda _p: False)
    assert _find_tesseract() is None


def test_prevent_sleep_returns_callable():
    """Off Windows it's a no-op; either way it returns a callable restore fn that doesn't raise."""
    restore = whispergram._prevent_sleep()
    assert callable(restore)
    restore()  # must not raise


# --- Instagram reader (v0.9.0) ---------------------------------------------------------
def _moji(s):
    """Simulate Instagram's latin-1-escaped-UTF-8 mangling of a string."""
    return s.encode("utf-8").decode("latin-1")


def test_fix_mojibake_roundtrip():
    assert _fix_mojibake(_moji("привіт 💋")) == "привіт 💋"   # mojibaked -> repaired
    assert _fix_mojibake("plain ascii") == "plain ascii"      # ascii unchanged
    assert _fix_mojibake("вже коректний") == "вже коректний"  # already-correct Cyrillic unchanged
    assert _fix_mojibake("") == ""


def test_ig_media_path():
    full = "your_instagram_activity/messages/inbox/x/audio/clip.mp4"
    assert _ig_media_path(full) == "audio/clip.mp4"           # last two components
    assert _ig_media_path("photos/p.jpg") == "photos/p.jpg"
    assert _ig_media_path("") == ""


def _write_ig_thread(folder):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "audio").mkdir()
    (folder / "audio" / "v.mp4").write_bytes(b"x")
    (folder / "photos").mkdir()
    (folder / "photos" / "p.jpg").write_bytes(b"x")
    pre = "your_instagram_activity/messages/inbox/maria/"
    data = {
        "participants": [{"name": "Maria"}, {"name": "David"}],
        "title": _moji("Марічка"),
        "messages": [  # deliberately newest-first / out of order
            {"sender_name": "David", "timestamp_ms": 3000, "content": _moji("привіт")},
            {"sender_name": _moji("Марічка"), "timestamp_ms": 1000,
             "audio_files": [{"uri": pre + "audio/v.mp4"}]},
            {"sender_name": "David", "timestamp_ms": 2000,
             "share": {"link": "https://www.instagram.com/reel/ABC/",
                       "original_content_owner": "someone", "share_text": _moji("кіно")}},
            {"sender_name": _moji("Марічка"), "timestamp_ms": 1500,
             "photos": [{"uri": pre + "photos/p.jpg"}], "content": _moji("дивись")},
            {"sender_name": "David", "timestamp_ms": 2500,
             "reactions": [{"actor": "Maria", "reaction": "love"}]},  # reaction-only -> no line
        ],
    }
    (folder / "message_1.json").write_text(json.dumps(data), encoding="utf-8")


def test_is_instagram_export(tmp_path):
    ig = tmp_path / "ig"
    _write_ig_thread(ig)
    assert is_instagram_export(str(ig)) is True
    assert is_instagram_export(FIXTURE) is False             # the Telegram fixture
    assert is_instagram_export(str(tmp_path / "nope")) is False


def test_normalize_instagram(tmp_path):
    ig = tmp_path / "ig"
    _write_ig_thread(ig)
    msgs, name = _normalize_instagram(str(ig))
    assert name == "Марічка"                                 # title decoded
    assert [m["date"] for m in msgs] == sorted(m["date"] for m in msgs)  # chronological
    voice = [m for m in msgs if m.get("media_type") == "voice_message"]
    photo = [m for m in msgs if m.get("photo")]
    share = [m for m in msgs if "[shared reel/post" in (m.get("text") or "")]
    assert voice and voice[0]["file"] == "audio/v.mp4"       # path = last 2 components
    assert photo and photo[0]["photo"] == "photos/p.jpg"
    assert share and "instagram.com/reel/ABC" in share[0]["text"] and "someone" in share[0]["text"]
    assert any(m.get("from") == "Марічка" for m in msgs)     # sender decoded
    assert any((m.get("text") or "") == "привіт" for m in msgs)  # content decoded
    assert not any((m.get("text") or "") == "love" for m in msgs)  # reaction-only dropped


def test_main_instagram_end_to_end(tmp_path):
    ig = tmp_path / "ChatExport_ig"
    _write_ig_thread(ig)
    out = tmp_path / "merged.md"
    rc = main(["--dry-run", "--no-describe", str(ig), "--out", str(out)])
    assert rc == 0
    text = out.read_text(encoding="utf-8")
    assert "привіт" in text and "дивись" in text             # decoded content
    assert "instagram.com/reel/ABC" in text                  # shared reel rendered inline
    assert "Ð" not in text and "Ñ" not in text               # no mojibake leaked through


# --- interactive menu (v0.10.0) -------------------------------------------------------
def test_parse_selection():
    assert _parse_selection("all", 5) == [1, 2, 3, 4, 5]
    assert _parse_selection("", 3) == [1, 2, 3]
    assert _parse_selection("1,3-5", 6) == [1, 3, 4, 5]
    assert _parse_selection("2 , 4", 4) == [2, 4]
    assert _parse_selection("9", 3) == []            # out-of-range dropped
    assert _parse_selection("nonsense", 3) == []


def _write_tg_chat(folder, name):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "result.json").write_text(json.dumps({"name": name, "messages": [
        {"type": "message", "date": "2026-06-20T10:00:00", "from": name,
         "media_type": "voice_message", "file": "voice_messages/a.ogg"}]}))


def test_discover_chats(tmp_path):
    _write_tg_chat(tmp_path / "tg", "Alex")
    _write_ig_thread(tmp_path / "inbox" / "maria_1")
    chats = _discover_chats(str(tmp_path))
    assert len(chats) == 2
    assert {c["platform"] for c in chats} == {"Telegram", "Instagram"}
    assert any(c["name"] == "Марічка" for c in chats)        # IG title decoded
    assert any(c["name"] == "Alex" and c["voice"] == 1 for c in chats)


def test_run_menu_everything_preset(tmp_path, monkeypatch):
    _write_tg_chat(tmp_path / "tg", "Alex")
    _write_ig_thread(tmp_path / "inbox" / "maria_1")
    # answers: selection, preset, ocr-langs, output dir, press-enter-to-start
    answers = iter(["all", "1", "ukr+rus+eng", str(tmp_path / "out"), ""])
    monkeypatch.setattr("builtins.input", lambda *a: next(answers))
    ns = _parse_args(["--menu", str(tmp_path)])
    dirs, ns = run_menu(ns)
    assert len(dirs) == 2
    assert ns.no_describe is False and ns.describe_hq and ns.video_files and ns.ocr
    assert ns.ocr_lang == "ukr+rus+eng"
    assert os.path.abspath(ns.out_dir) == os.path.abspath(str(tmp_path / "out"))


def test_run_menu_voice_only_and_subset(tmp_path, monkeypatch):
    _write_tg_chat(tmp_path / "a", "A")
    _write_tg_chat(tmp_path / "b", "B")
    answers = iter(["1", "2", "", ""])  # chat 1, preset 2 (voice+video), default out, go
    monkeypatch.setattr("builtins.input", lambda *a: next(answers))
    ns = _parse_args(["--menu", str(tmp_path)])
    dirs, ns = run_menu(ns)
    assert len(dirs) == 1                              # only one chat selected
    assert ns.no_describe is True and ns.video_files is True and ns.ocr is False


class _FakeStdin:
    """Minimal stand-in so tests can control whether main() thinks it has an interactive TTY."""

    def __init__(self, tty):
        self._tty = tty

    def isatty(self):
        return self._tty


def test_has_export_json(tmp_path):
    _write_tg_chat(tmp_path / "tg", "Alex")
    _write_ig_thread(tmp_path / "inbox" / "maria")
    assert _has_export_json(str(tmp_path / "tg")) is True          # Telegram export folder itself
    assert _has_export_json(str(tmp_path / "inbox" / "maria")) is True  # Instagram thread folder
    assert _has_export_json(str(tmp_path)) is False                # parent: no JSON directly inside
    stray = tmp_path / "stray"
    stray.mkdir()
    (stray / "config.json").write_text(json.dumps({"setting": 1}))  # a non-export JSON ...
    assert _has_export_json(str(stray)) is False                    # ... doesn't count as an export


def test_main_auto_menu_when_folder_has_only_nested_exports(tmp_path, monkeypatch):
    """A bare run in a parent folder (no export JSON directly inside, e.g. an Instagram
    `your_instagram_activity` root) auto-opens the picker instead of dead-ending."""
    _write_tg_chat(tmp_path / "tg", "Alex")
    _write_ig_thread(tmp_path / "inbox" / "maria")
    out = tmp_path / "out"
    # run_menu answers: selection, preset (2 = voice/video only), output dir, press-enter-to-start
    answers = iter(["all", "2", str(out), ""])
    monkeypatch.setattr("builtins.input", lambda *a: next(answers))
    monkeypatch.setattr(whispergram.sys, "stdin", _FakeStdin(True))

    rc = main(["--dry-run", str(tmp_path)])          # no --menu flag; detection kicks in
    assert rc == 0
    assert len(list(out.glob("*.md"))) == 2          # both nested chats were transcribed


def test_main_auto_menu_ignores_stray_json_in_parent(tmp_path, monkeypatch):
    """A non-export *.json in the parent (e.g. config.json) must NOT suppress auto-discovery."""
    (tmp_path / "notes.json").write_text(json.dumps({"hello": "world"}))
    _write_tg_chat(tmp_path / "tg", "Alex")
    out = tmp_path / "out"
    answers = iter(["all", "2", str(out), ""])
    monkeypatch.setattr("builtins.input", lambda *a: next(answers))
    monkeypatch.setattr(whispergram.sys, "stdin", _FakeStdin(True))
    rc = main(["--dry-run", str(tmp_path)])
    assert rc == 0
    assert len(list(out.glob("*.md"))) == 1          # the nested chat was still found + transcribed


def test_main_does_not_auto_menu_for_multiple_dirs(tmp_path, monkeypatch):
    """Passing several folders is an explicit queue - never hijacked into the picker, even if none
    are exports (they just skip as before)."""
    def _boom(*_a, **_k):
        raise AssertionError("discovery must not run for a multi-folder queue")

    monkeypatch.setattr(whispergram, "_discover_chats", _boom)
    monkeypatch.setattr(whispergram.sys, "stdin", _FakeStdin(True))
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    rc = main(["--dry-run", str(a), str(b), "--out-dir", str(tmp_path / "o")])
    assert rc == 0          # both skip (no export), queue completes; discovery never fires


def test_main_hints_when_nested_but_not_interactive(tmp_path, monkeypatch):
    """Without a TTY we can't prompt, so main points the user at --menu rather than hanging."""
    _write_tg_chat(tmp_path / "tg", "Alex")
    monkeypatch.setattr(whispergram.sys, "stdin", _FakeStdin(False))
    with pytest.raises(SystemExit) as exc:
        main(["--dry-run", str(tmp_path)])
    assert "--menu" in str(exc.value)


def test_main_does_not_auto_menu_when_folder_is_an_export(tmp_path, monkeypatch):
    """When the folder is itself a chat export, it's processed directly - discovery never runs."""
    def _boom(*_a, **_k):
        raise AssertionError("_discover_chats must not run for a real export folder")

    monkeypatch.setattr(whispergram, "_discover_chats", _boom)
    out = tmp_path / "m.md"
    rc = main(["--dry-run", FIXTURE, "--out", str(out)])
    assert rc == 0 and out.exists()


def test_run_menu_uses_provided_chats_without_rescanning(tmp_path, monkeypatch):
    """run_menu(chats=...) reuses a pre-discovered list (the auto-menu path) - no second scan."""
    def _boom(*_a, **_k):
        raise AssertionError("run_menu must not scan when chats are provided")

    monkeypatch.setattr(whispergram, "_discover_chats", _boom)
    _write_tg_chat(tmp_path / "a", "A")
    chats = [{"dir": str(tmp_path / "a"), "platform": "Telegram", "name": "A",
              "total": 1, "voice": 1, "photo": 0, "video": 0}]
    answers = iter(["1", "2", "", ""])
    monkeypatch.setattr("builtins.input", lambda *a: next(answers))
    ns = _parse_args(["--menu", str(tmp_path)])
    dirs, ns = run_menu(ns, chats=chats)
    assert dirs == [str(tmp_path / "a")]


def test_resolve_compute_type_cpu_and_explicit():
    assert whispergram._resolve_compute_type("cpu", "auto") == "int8"       # CPU auto -> int8
    assert whispergram._resolve_compute_type("cpu", "float32") == "float32"  # explicit wins on CPU
    assert whispergram._resolve_compute_type("cuda", "int8") == "int8"      # explicit wins on GPU


def test_resolve_compute_type_gpu_auto_by_vram(monkeypatch):
    monkeypatch.setattr(whispergram, "_gpu_free_mib", lambda: 8000)
    assert whispergram._resolve_compute_type("cuda", "auto") == "float16"      # roomy GPU
    monkeypatch.setattr(whispergram, "_gpu_free_mib", lambda: 4096)
    assert whispergram._resolve_compute_type("cuda", "auto") == "int8_float16"  # 4 GB card -> int8
    monkeypatch.setattr(whispergram, "_gpu_free_mib", lambda: None)
    assert whispergram._resolve_compute_type("cuda", "auto") == "float16"      # unknown -> default


def test_gpu_free_mib_none_without_nvidia_smi(monkeypatch):
    monkeypatch.setattr(whispergram.shutil, "which", lambda _n: None)
    assert whispergram._gpu_free_mib() is None


def test_compute_type_flag_parsed():
    assert _parse_args(["--compute-type", "int8_float16"]).compute_type == "int8_float16"
    assert _parse_args([]).compute_type == "auto"                             # default is auto


def test_date_span_and_fmt():
    msgs = [{"date": "2025-03-02T00:00:00"}, {"foo": 1}, {"date": "2025-01-05T12:00:00"}]
    assert whispergram._date_span(msgs) == ("2025-01-05", "2025-03-02")   # min/max, undated ignored
    assert whispergram._date_span([{"foo": 1}]) == ("", "")              # none dated
    assert whispergram._fmt_dates("2026-06-20", "2026-06-20") == "2026-06-20"          # one day
    assert whispergram._fmt_dates("2024-01-01", "2024-12-31") == "2024-01-01..2024-12-31"  # range
    assert whispergram._fmt_dates("", "") == ""                                        # unknown


def test_chat_summary_includes_date_span(tmp_path):
    d = tmp_path / "tg"
    d.mkdir()
    (d / "result.json").write_text(json.dumps({"name": "Span", "messages": [
        {"type": "message", "date": "2024-06-21T10:00:00", "from": "A", "text": "hi"},
        {"type": "message", "date": "2025-11-01T09:30:00", "from": "A", "text": "bye"},
    ]}))
    s = whispergram._chat_summary(str(d))
    assert s["first"] == "2024-06-21" and s["last"] == "2025-11-01"


def test_sort_chats_orders():
    chats = [
        {"name": "Bravo", "voice": 5, "total": 10, "last": "2025-01-01"},
        {"name": "alpha", "voice": 2, "total": 99, "last": "2026-12-31"},
        {"name": "Charlie", "voice": 5, "total": 3, "last": "2024-06-01"},
    ]
    names = lambda k: [c["name"] for c in whispergram._sort_chats(chats, k)]  # noqa: E731
    assert names("voice") == ["Bravo", "Charlie", "alpha"]      # voice desc, total tiebreak
    assert names("messages") == ["alpha", "Bravo", "Charlie"]   # total desc
    assert names("recent") == ["alpha", "Bravo", "Charlie"]     # last date desc
    assert names("name") == ["alpha", "Bravo", "Charlie"]       # A-Z, case-insensitive
    assert names("nonsense")[0] == "Bravo"                      # unknown key -> voice default


def test_sort_flag_parsed():
    assert _parse_args(["--sort", "recent"]).sort == "recent"
    assert _parse_args([]).sort == "voice"                      # default


def test_version_is_semver():
    parts = __version__.split(".")
    assert len(parts) == 3 and all(p.isdigit() for p in parts)


def test_version_matches_pyproject():
    """__version__ and pyproject's version must stay in lockstep (the publish workflow + build
    rely on it)."""
    import re
    pyproject = os.path.join(os.path.dirname(__file__), "..", "pyproject.toml")
    with open(pyproject, encoding="utf-8") as fh:
        match = re.search(r'^version = "([^"]+)"', fh.read(), re.MULTILINE)
    assert match and match.group(1) == __version__
