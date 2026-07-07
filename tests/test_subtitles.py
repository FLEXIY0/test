"""Subtitle grouping and SRT generation tests (offline)."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline.subtitles import _timestamp, group_words, write_srt


def w(start, end, text):
    return {"start": start, "end": end, "text": text}


class TestGrouping:
    def test_words_merge_into_one_cue(self):
        cues = group_words([w(0.0, 0.3, "Close"), w(0.35, 0.6, "your"), w(0.65, 1.0, "eyes")])
        assert len(cues) == 1
        assert cues[0]["text"] == "Close your eyes"
        assert cues[0]["start"] == 0.0
        assert cues[0]["end"] == 1.0

    def test_split_on_char_limit(self):
        words = [w(i * 0.4, i * 0.4 + 0.3, "word") for i in range(20)]
        cues = group_words(words)
        assert len(cues) > 1
        assert all(len(c["text"]) <= 42 for c in cues)

    def test_split_on_long_gap(self):
        cues = group_words([w(0.0, 0.4, "First"), w(2.0, 2.4, "second")])
        assert len(cues) == 2

    def test_split_on_duration(self):
        # Slow narration: each word 1.5s apart -> cue capped at 5s
        words = [w(i * 1.5, i * 1.5 + 0.5, "slow") for i in range(6)]
        cues = group_words(words)
        assert all(c["end"] - c["start"] <= 5.5 for c in cues)

    def test_empty(self):
        assert group_words([]) == []


class TestTimestamp:
    def test_format(self):
        assert _timestamp(0.0) == "00:00:00,000"
        assert _timestamp(61.5) == "00:01:01,500"
        assert _timestamp(3661.007) == "01:01:01,007"

    def test_negative_clamped(self):
        assert _timestamp(-1.0) == "00:00:00,000"


class TestWriteSrt:
    def test_offsets_accumulate_across_segments(self, tmp_path):
        seg1 = [w(0.0, 0.5, "Hello"), w(0.6, 1.0, "there")]
        seg2 = [w(0.0, 0.5, "Second"), w(0.6, 1.0, "segment")]
        out = str(tmp_path / "test.srt")
        count = write_srt([seg1, seg2], [10.0, 8.0], out)
        assert count == 2
        content = open(out, encoding="utf-8").read()
        # Second segment's cue must start at 10s (offset by first duration).
        assert "00:00:10,000 --> 00:00:11,000" in content
        assert "Second segment" in content
        assert content.splitlines()[0] == "1"

    def test_cue_clamped_to_segment_duration(self, tmp_path):
        # Word boundary reported past audio end must not bleed into next segment.
        seg = [w(0.0, 12.0, "Overlong")]
        out = str(tmp_path / "clamp.srt")
        write_srt([seg, [w(0.0, 0.5, "next")]], [10.0, 5.0], out)
        content = open(out, encoding="utf-8").read()
        assert "00:00:00,000 --> 00:00:10,000" in content
