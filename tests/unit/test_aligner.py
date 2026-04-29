"""Unit tests for aligner.py pure functions (no audio / no model)."""
import json
import pytest

from aligner import (  # type: ignore
    parse_transcript,
    extract_char_metas,
    assign_raw_word_times,
    build_anchored_timeline,
    map_segments_to_cues,
    polish_subtitle_timeline,
    cues_to_srt_bytes,
    build_quality_report,
    _format_timestamp,
    Cue,
    CharMeta,
)


# ─────────────────────────────────────────────────────────────────────────────
# parse_transcript
# ─────────────────────────────────────────────────────────────────────────────

class TestParseTranscript:
    def test_txt_blank_line_separator(self):
        text = "第一段內容\n\n第二段內容\n\n第三段內容"
        segs = parse_transcript(text, "txt")
        assert segs == ["第一段內容", "第二段內容", "第三段內容"]

    def test_txt_single_newline_when_no_blanks(self):
        text = "第一行\n第二行\n第三行"
        segs = parse_transcript(text, "txt")
        assert segs == ["第一行", "第二行", "第三行"]

    def test_txt_blank_lines_take_priority(self):
        # Has blank lines → split on blanks, each para keeps its inner \n
        text = "第一段第一行\n第一段第二行\n\n第二段"
        segs = parse_transcript(text, "txt")
        assert len(segs) == 2
        assert segs[0] == "第一段第一行\n第一段第二行"
        assert segs[1] == "第二段"

    def test_txt_empty(self):
        assert parse_transcript("", "txt") == []
        assert parse_transcript("   \n  \n", "txt") == []

    def test_txt_single_line(self):
        assert parse_transcript("整段一行不換", "txt") == ["整段一行不換"]

    def test_srt_basic(self):
        srt = (
            "1\n00:00:01,000 --> 00:00:03,500\n第一條字幕\n\n"
            "2\n00:00:04,000 --> 00:00:06,500\n第二條\n字幕\n\n"
        )
        segs = parse_transcript(srt, "srt")
        assert len(segs) == 2
        assert segs[0] == "第一條字幕"
        # Internal newline preserved
        assert "\n" in segs[1]
        assert segs[1].startswith("第二條")

    def test_auto_detects_srt(self):
        srt = "1\n00:00:01,000 --> 00:00:03,500\nHello\n\n"
        segs = parse_transcript(srt, "auto")
        assert segs == ["Hello"]

    def test_auto_detects_txt(self):
        # Looks like txt — no arrow
        text = "第一段\n\n第二段"
        segs = parse_transcript(text, "auto")
        assert segs == ["第一段", "第二段"]

    def test_crlf_line_endings(self):
        text = "段一\r\n\r\n段二"
        segs = parse_transcript(text, "txt")
        assert segs == ["段一", "段二"]


# ─────────────────────────────────────────────────────────────────────────────
# extract_char_metas
# ─────────────────────────────────────────────────────────────────────────────

class TestExtractCharMetas:
    def test_simple(self):
        segs = ["abc", "de"]
        full, metas = extract_char_metas(segs)
        assert full == "abc\nde"
        assert len(metas) == 5
        assert [m.char for m in metas] == ["a", "b", "c", "d", "e"]
        assert [m.seg_idx for m in metas] == [0, 0, 0, 1, 1]
        # text_idx: 'a'=0 'b'=1 'c'=2 '\n'=3 'd'=4 'e'=5
        assert [m.text_idx for m in metas] == [0, 1, 2, 4, 5]

    def test_skips_internal_whitespace(self):
        segs = ["a b", "c"]
        full, metas = extract_char_metas(segs)
        # Space between a and b is whitespace → no meta for it
        assert len(metas) == 3
        assert [m.char for m in metas] == ["a", "b", "c"]

    def test_preserves_internal_newline_position(self):
        # Multi-line cue: segment text is "ab\ncd"
        segs = ["ab\ncd"]
        full, metas = extract_char_metas(segs)
        assert full == "ab\ncd"
        assert len(metas) == 4
        assert [m.text_idx for m in metas] == [0, 1, 3, 4]
        assert all(m.seg_idx == 0 for m in metas)

    def test_empty_segments_drop_metas(self):
        segs = ["a", "   ", "b"]
        full, metas = extract_char_metas(segs)
        assert [m.char for m in metas] == ["a", "b"]
        assert [m.seg_idx for m in metas] == [0, 2]


# ─────────────────────────────────────────────────────────────────────────────
# assign_raw_word_times
# ─────────────────────────────────────────────────────────────────────────────

class TestAssignRawWordTimes:
    def test_chinese_per_char_words(self):
        segs = ["你好", "再見"]
        full, metas = extract_char_metas(segs)
        # Whisper Chinese — one word per char
        raw = [
            {"word": "你", "start": 0.0, "end": 0.5, "probability": 0.9},
            {"word": "好", "start": 0.5, "end": 1.0, "probability": 0.85},
            {"word": "再", "start": 1.5, "end": 2.0, "probability": 0.8},
            {"word": "見", "start": 2.0, "end": 2.5, "probability": 0.75},
        ]
        n = assign_raw_word_times(full, metas, raw)
        assert n == 4
        assert metas[0].start == 0.0 and metas[0].end == 0.5
        assert metas[3].end == 2.5
        assert metas[3].prob == 0.75

    def test_english_multi_char_word(self):
        segs = ["hello world"]
        full, metas = extract_char_metas(segs)
        # Word covers multiple chars
        raw = [
            {"word": "hello", "start": 0.0, "end": 0.6, "probability": 0.9},
            {"word": " world", "start": 0.6, "end": 1.2, "probability": 0.8},
        ]
        n = assign_raw_word_times(full, metas, raw)
        assert n == 10  # h-e-l-l-o-w-o-r-l-d (space skipped)
        # All chars in 'hello' get same time
        for i in range(5):
            assert metas[i].start == 0.0 and metas[i].end == 0.6
        # All chars in 'world' get the second word's time
        for i in range(5, 10):
            assert metas[i].start == 0.6 and metas[i].end == 1.2

    def test_handles_object_attrs(self):
        # Simulate stable-ts WordTiming objects
        class W:
            def __init__(self, w, s, e, p):
                self.word = w
                self.start = s
                self.end = e
                self.probability = p
        segs = ["ab"]
        full, metas = extract_char_metas(segs)
        raw = [W("a", 0.1, 0.2, 0.7), W("b", 0.2, 0.3, 0.6)]
        n = assign_raw_word_times(full, metas, raw)
        assert n == 2
        assert metas[0].prob == 0.7

    def test_skips_empty_words(self):
        segs = ["ab"]
        full, metas = extract_char_metas(segs)
        raw = [
            {"word": "", "start": 0.0, "end": 0.0, "probability": 0.0},
            {"word": " ", "start": 0.0, "end": 0.1, "probability": 0.0},
            {"word": "ab", "start": 0.1, "end": 0.5, "probability": 0.9},
        ]
        n = assign_raw_word_times(full, metas, raw)
        assert n == 2


# ─────────────────────────────────────────────────────────────────────────────
# build_anchored_timeline
# ─────────────────────────────────────────────────────────────────────────────

def _make_metas_with_probs(probs, raw_starts=None, raw_ends=None):
    """Helper: build N CharMeta with given prob values + optional raw times."""
    metas = []
    for i, p in enumerate(probs):
        m = CharMeta(char=chr(ord('a') + i % 26), seg_idx=0, text_idx=i)
        m.prob = p
        m.start = raw_starts[i] if raw_starts else float(i)
        m.end = raw_ends[i] if raw_ends else float(i + 1)
        metas.append(m)
    return metas


class TestBuildAnchoredTimeline:
    def test_all_high_confidence(self):
        metas = _make_metas_with_probs([0.9] * 5, raw_starts=[0, 1, 2, 3, 4],
                                       raw_ends=[1, 2, 3, 4, 5])
        build_anchored_timeline(metas, audio_duration=5.0, anchor_threshold=0.4)
        for m in metas:
            assert m.source == "anchor"
        assert metas[0].start == 0.0
        assert metas[-1].end == 5.0

    def test_mid_section_interpolated(self):
        # chars 0,4 are anchors; 1,2,3 are low-confidence → interpolated between
        metas = _make_metas_with_probs(
            [0.9, 0.05, 0.05, 0.05, 0.9],
            raw_starts=[0, 0.5, 1, 1.5, 4.0],
            raw_ends=[0.5, 0.6, 1.1, 1.6, 4.5],
        )
        build_anchored_timeline(metas, audio_duration=5.0, anchor_threshold=0.4)
        assert metas[0].source == "anchor"
        assert metas[4].source == "anchor"
        assert all(m.source == "interpolated" for m in metas[1:4])
        # interpolated chars span [0.5, 4.0] linearly across 4 slots (3 chars + 2 anchor edges)
        assert metas[1].start == pytest.approx(0.5, abs=0.01)
        assert metas[3].end == pytest.approx(4.0, abs=0.01)
        # Monotonic
        for i in range(len(metas) - 1):
            assert metas[i].end <= metas[i + 1].start + 1e-6

    def test_head_failure_falls_back_to_zero(self):
        # First 3 chars low confidence → edge_fill from t=0
        metas = _make_metas_with_probs(
            [0.05, 0.05, 0.05, 0.9, 0.9],
            raw_starts=[0, 0.1, 0.2, 3.0, 4.0],
            raw_ends=[0.1, 0.2, 0.3, 4.0, 5.0],
        )
        build_anchored_timeline(metas, audio_duration=5.0, anchor_threshold=0.4)
        assert metas[0].source == "edge_fill"
        assert metas[0].start == 0.0
        assert metas[3].source == "anchor"
        # Edge fill chars must end before first anchor's start
        assert metas[2].end <= metas[3].start + 1e-6

    def test_tail_failure_extends_to_duration(self):
        metas = _make_metas_with_probs(
            [0.9, 0.9, 0.05, 0.05, 0.05],
            raw_starts=[0, 1, 1.1, 1.2, 1.3],
            raw_ends=[1, 2, 1.2, 1.3, 1.4],
        )
        build_anchored_timeline(metas, audio_duration=10.0, anchor_threshold=0.4)
        assert metas[1].source == "anchor"
        assert all(m.source == "edge_fill" for m in metas[2:])
        assert metas[-1].end == pytest.approx(10.0, abs=0.01)

    def test_all_low_confidence_linear_spread(self):
        metas = _make_metas_with_probs([0.05] * 4)
        build_anchored_timeline(metas, audio_duration=8.0, anchor_threshold=0.4)
        for m in metas:
            assert m.source == "edge_fill"
        # 4 chars, 8s → 2s each
        assert metas[0].start == 0.0
        assert metas[0].end == pytest.approx(2.0)
        assert metas[3].end == pytest.approx(8.0)

    def test_monotonicity_enforced(self):
        # Plant a deliberate violation: anchor[1]'s start < anchor[0]'s end
        metas = _make_metas_with_probs(
            [0.9, 0.9, 0.9],
            raw_starts=[0.0, 5.0, 2.0],   # anchor[2].start=2.0 < anchor[1].end=6.0
            raw_ends=[1.0, 6.0, 3.0],
        )
        build_anchored_timeline(metas, audio_duration=10.0, anchor_threshold=0.4)
        prev = 0.0
        for m in metas:
            assert m.start >= prev - 1e-6
            assert m.end >= m.start - 1e-6
            prev = m.end

    def test_empty_metas_noop(self):
        metas = []
        build_anchored_timeline(metas, audio_duration=10.0)
        assert metas == []


# ─────────────────────────────────────────────────────────────────────────────
# map_segments_to_cues
# ─────────────────────────────────────────────────────────────────────────────

class TestMapSegmentsToCues:
    def test_three_segments_three_cues(self):
        segs = ["abc", "de", "f"]
        full, metas = extract_char_metas(segs)
        # Manually assign clean times
        for i, m in enumerate(metas):
            m.start = float(i)
            m.end = float(i + 1)
            m.source = "anchor"
        cues = map_segments_to_cues(segs, metas)
        assert len(cues) == 3
        assert cues[0].text == "abc"
        assert cues[0].start == 0.0
        assert cues[0].end == 3.0
        assert cues[1].text == "de"
        assert cues[1].start == 3.0
        assert cues[1].end == 5.0
        assert cues[2].text == "f"
        # Indices renumbered 1..N
        assert [c.index for c in cues] == [1, 2, 3]

    def test_skip_empty_segment_renumbers(self):
        segs = ["a", "   ", "b"]
        full, metas = extract_char_metas(segs)
        for i, m in enumerate(metas):
            m.start = float(i)
            m.end = float(i + 1)
            m.source = "anchor"
        cues = map_segments_to_cues(segs, metas)
        assert len(cues) == 2
        assert cues[0].text == "a"
        assert cues[1].text == "b"
        assert [c.index for c in cues] == [1, 2]

    def test_quality_counts(self):
        segs = ["abc"]
        full, metas = extract_char_metas(segs)
        metas[0].source = "anchor"
        metas[1].source = "interpolated"
        metas[2].source = "edge_fill"
        for i, m in enumerate(metas):
            m.start = float(i); m.end = float(i + 1)
        cues = map_segments_to_cues(segs, metas)
        assert cues[0].anchor_chars == 1
        assert cues[0].interpolated_chars == 1
        assert cues[0].edge_fill_chars == 1


# ─────────────────────────────────────────────────────────────────────────────
# polish_subtitle_timeline
# ─────────────────────────────────────────────────────────────────────────────

class TestPolishSubtitleTimeline:
    def test_frame_snap_2997(self):
        cues = [Cue(index=1, text="a", start=0.123456, end=1.234567)]
        polish_subtitle_timeline(cues, fps=29.97, min_duration=0.0,
                                 min_gap_frames=0)
        # Times must be a multiple of 1/29.97
        frame = 1.0 / 29.97
        assert abs((cues[0].start / frame) - round(cues[0].start / frame)) < 1e-6
        assert abs((cues[0].end / frame) - round(cues[0].end / frame)) < 1e-6

    def test_min_duration_extends_short_cue(self):
        cues = [
            Cue(index=1, text="好", start=0.0, end=0.2),
            Cue(index=2, text="再見", start=5.0, end=6.0),
        ]
        stats = polish_subtitle_timeline(cues, fps=25.0, min_duration=1.0,
                                         min_gap_frames=2)
        assert cues[0].end >= 1.0 - 1e-6
        assert stats["min_duration_extended"] >= 1

    def test_min_duration_blocked_by_next_cue(self):
        # Next cue is at 0.3s — extend can only go to 0.3 - gap
        cues = [
            Cue(index=1, text="a", start=0.0, end=0.1),
            Cue(index=2, text="b", start=0.3, end=1.0),
        ]
        polish_subtitle_timeline(cues, fps=25.0, min_duration=1.0,
                                 min_gap_frames=2)
        gap = 2.0 / 25.0
        # cue 1 end must not exceed cue 2 start - gap
        assert cues[0].end <= cues[1].start - gap + 1e-6
        # And must not invert
        assert cues[0].end >= cues[0].start

    def test_min_gap_inserted(self):
        cues = [
            Cue(index=1, text="a", start=0.0, end=2.0),
            Cue(index=2, text="b", start=2.0, end=4.0),  # touching — needs gap
        ]
        stats = polish_subtitle_timeline(cues, fps=25.0, min_duration=0.0,
                                         min_gap_frames=2)
        gap = 2.0 / 25.0
        assert cues[1].start - cues[0].end >= gap - 1e-6
        assert stats["gap_inserted"] >= 1

    def test_reading_speed_warning(self):
        # 30 chars in 1s = 30 chars/sec → over 18 limit
        cues = [Cue(index=1, text="一二三四五六七八九十一二三四五六七八九十一二三四五六七八九十",
                    start=0.0, end=1.0)]
        stats = polish_subtitle_timeline(cues, fps=25.0, min_duration=0.0,
                                         min_gap_frames=0)
        assert len(stats["reading_speed_warnings"]) == 1
        assert stats["reading_speed_warnings"][0]["index"] == 1

    def test_no_cues_noop(self):
        stats = polish_subtitle_timeline([], fps=25.0)
        assert stats["frame_snapped"] == 0


# ─────────────────────────────────────────────────────────────────────────────
# cues_to_srt_bytes
# ─────────────────────────────────────────────────────────────────────────────

class TestCuesToSrtBytes:
    def test_utf8_bom_and_crlf(self):
        cues = [Cue(index=1, text="你好", start=1.0, end=2.0)]
        b = cues_to_srt_bytes(cues, encoding_bom=True)
        assert b.startswith(b"\xef\xbb\xbf")
        assert b"\r\n" in b

    def test_no_bom_when_disabled(self):
        cues = [Cue(index=1, text="hi", start=1.0, end=2.0)]
        b = cues_to_srt_bytes(cues, encoding_bom=False)
        assert not b.startswith(b"\xef\xbb\xbf")

    def test_timestamp_format(self):
        cues = [Cue(index=1, text="x", start=1.234, end=5.678)]
        b = cues_to_srt_bytes(cues, encoding_bom=False)
        s = b.decode("utf-8")
        assert "00:00:01,234 --> 00:00:05,678" in s

    def test_multiline_text_preserved(self):
        cues = [Cue(index=1, text="第一行\n第二行", start=0.0, end=2.0)]
        b = cues_to_srt_bytes(cues, encoding_bom=False)
        s = b.decode("utf-8")
        assert "第一行" in s
        assert "第二行" in s
        # The two text lines should appear on separate output lines
        lines = s.split("\r\n")
        assert "第一行" in lines
        assert "第二行" in lines

    def test_renumbers_sequentially(self):
        cues = [
            Cue(index=99, text="a", start=0.0, end=1.0),
            Cue(index=100, text="b", start=1.5, end=2.5),
        ]
        b = cues_to_srt_bytes(cues, encoding_bom=False).decode("utf-8")
        # First non-empty token is "1" not "99"
        assert b.lstrip().startswith("1\r\n")
        assert "\r\n2\r\n" in b


# ─────────────────────────────────────────────────────────────────────────────
# _format_timestamp edge cases
# ─────────────────────────────────────────────────────────────────────────────

class TestFormatTimestamp:
    def test_zero(self):
        assert _format_timestamp(0.0) == "00:00:00,000"

    def test_hours(self):
        assert _format_timestamp(3661.5) == "01:01:01,500"

    def test_negative_clamps_to_zero(self):
        assert _format_timestamp(-1.0) == "00:00:00,000"

    def test_millisecond_rounding(self):
        # 1.9999 rounds to 2.000, must not become "00:00:01,1000"
        assert _format_timestamp(1.9999) == "00:00:02,000"


# ─────────────────────────────────────────────────────────────────────────────
# build_quality_report
# ─────────────────────────────────────────────────────────────────────────────

class TestBuildQualityReport:
    def test_ratios_sum_to_one(self):
        segs = ["abc"]
        full, metas = extract_char_metas(segs)
        metas[0].source = "anchor"
        metas[1].source = "interpolated"
        metas[2].source = "edge_fill"
        for i, m in enumerate(metas):
            m.start = float(i); m.end = float(i + 1)
        cues = map_segments_to_cues(segs, metas)
        report = build_quality_report(cues, metas, fps=25.0,
                                      fps_source="auto-detected",
                                      audio_duration=3.0,
                                      polish_stats={})
        a = report["alignment"]
        assert a["anchor_ratio"] + a["interpolated_ratio"] + a["edge_fill_ratio"] == pytest.approx(1.0, abs=0.005)
        assert report["total_chars"] == 3
        assert report["subtitle_count"] == 1

    def test_low_confidence_cue_flagged(self):
        # 4 chars, only 1 anchor → 25% < 40% → flagged
        segs = ["abcd"]
        full, metas = extract_char_metas(segs)
        metas[0].source = "anchor"
        metas[1].source = "interpolated"
        metas[2].source = "interpolated"
        metas[3].source = "interpolated"
        for i, m in enumerate(metas):
            m.start = float(i); m.end = float(i + 1)
        cues = map_segments_to_cues(segs, metas)
        report = build_quality_report(cues, metas, fps=25.0,
                                      fps_source="auto-detected",
                                      audio_duration=4.0, polish_stats={})
        assert len(report["low_confidence_cues"]) == 1
