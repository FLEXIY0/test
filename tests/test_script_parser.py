"""Unit tests for the script parser (no network, no ffmpeg)."""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline.script_parser import ScriptFormatError, parse_script, parse_script_file

SAMPLE = """\
# TITLE: Test Title Here
# DESCRIPTION: A short description. Do you remember? #tag1 #tag2
# TAGS: one, two, three, four, five
# THUMBNAIL_TEXT: GONE FOREVER

[IMAGE: a vintage diner interior]
First paragraph of narration.
Still the first segment.

[IMAGE: an old drive-in theater]
Second paragraph.
"""


class TestParse:
    def test_headers(self):
        doc = parse_script(SAMPLE)
        assert doc.title == "Test Title Here"
        assert doc.description.startswith("A short description")
        assert doc.tags == ["one", "two", "three", "four", "five"]
        assert doc.thumbnail_text == "GONE FOREVER"

    def test_segments(self):
        doc = parse_script(SAMPLE)
        assert len(doc.segments) == 2
        assert doc.segments[0].image_query == "a vintage diner interior"
        assert "First paragraph" in doc.segments[0].text
        assert "Still the first segment." in doc.segments[0].text
        assert doc.segments[1].image_query == "an old drive-in theater"

    def test_word_count(self):
        doc = parse_script(SAMPLE)
        assert doc.word_count == len(doc.full_text.split())
        assert doc.word_count > 0

    def test_missing_title_raises(self):
        with pytest.raises(ScriptFormatError):
            parse_script("[IMAGE: x]\nsome text\n")

    def test_no_segments_raises(self):
        with pytest.raises(ScriptFormatError):
            parse_script("# TITLE: Only A Title\n")

    def test_hash_line_inside_segment_is_narration(self):
        # A '#' line after segments start is body text, not a header.
        doc = parse_script(
            "# TITLE: T\n\n[IMAGE: q]\nreal line\n# DESCRIPTION: not a header now\n"
        )
        assert doc.description == ""
        assert "not a header now" in doc.segments[0].text

    def test_empty_segment_dropped(self):
        doc = parse_script("# TITLE: T\n\n[IMAGE: a]\n\n[IMAGE: b]\ntext\n")
        assert len(doc.segments) == 1
        assert doc.segments[0].image_query == "b"


def test_parse_bundled_episode():
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    doc = parse_script_file(os.path.join(here, "scripts", "ep01_vanished_places.md"))
    assert len(doc.segments) == 8
    assert doc.thumbnail_text == "GONE FOREVER"
    assert doc.word_count > 500
    assert all(s.image_query and s.text for s in doc.segments)
