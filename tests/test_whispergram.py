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
    _photo_reader,
    build_transcript,
    extract_text,
    find_json,
    is_missing_media,
    main,
    media_marker,
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
def test_version_is_semver():
    parts = __version__.split(".")
    assert len(parts) == 3 and all(p.isdigit() for p in parts)
