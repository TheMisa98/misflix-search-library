from __future__ import annotations

from misflix.core.subtitles.srt import Cue, decode_subtitle, format_srt, parse_srt

SAMPLE = """1
00:00:01,500 --> 00:00:03,000
Hola.

2
00:00:04,000 --> 00:00:06,250
<i>¿Cómo estás?</i>
Bien.
"""


def test_parse_srt_reads_times_and_multiline_text():
    cues = parse_srt(SAMPLE)

    assert cues == [
        Cue(start=1.5, end=3.0, text="Hola."),
        Cue(start=4.0, end=6.25, text="<i>¿Cómo estás?</i>\nBien."),
    ]


def test_parse_srt_tolerates_missing_numbers_dots_and_windows_newlines():
    text = "00:00:01.5 --> 00:00:02.000\r\nUno\r\n\r\n7\r\n00:00:03,000 --> 00:00:04,000\r\nDos\r\n"

    cues = parse_srt(text)

    assert [(c.start, c.end, c.text) for c in cues] == [(1.5, 2.0, "Uno"), (3.0, 4.0, "Dos")]


def test_parse_srt_skips_blocks_without_timing_or_text_and_sorts():
    text = (
        "basura\n\n"
        "2\n00:00:05,000 --> 00:00:06,000\nB\n\n"
        "1\n00:00:01,000 --> 00:00:02,000\nA\n\n"
        "3\n00:00:07,000 --> 00:00:08,000\n"
    )

    assert [c.text for c in parse_srt(text)] == ["A", "B"]


def test_format_srt_round_trips_and_renumbers():
    cues = parse_srt(SAMPLE)

    text = format_srt(cues)

    assert text.startswith("1\n00:00:01,500 --> 00:00:03,000\nHola.\n\n2\n")
    assert parse_srt(text) == cues


def test_format_srt_clamps_negative_times_and_rounds_millis():
    text = format_srt([Cue(start=-0.2, end=3661.0006, text="x")])

    assert "00:00:00,000 --> 01:01:01,001" in text


def test_decode_subtitle_handles_utf8_bom_utf16_and_cp1252():
    assert decode_subtitle("﻿añejo".encode()) == "añejo"
    assert decode_subtitle("añejo".encode("utf-16")) == "añejo"
    assert decode_subtitle("añejo\r\nsí".encode("cp1252")) == "añejo\nsí"


def test_cue_duration_is_never_negative():
    assert Cue(start=2.0, end=1.0, text="x").duration == 0.0
