from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from misflix.core.subtitles.media import StreamInfo
from misflix.infra import media_probe
from misflix.infra.media_probe import (
    MediaProbeError,
    dialogue_filter,
    extract_reference,
    levels_from_bytes,
    levels_to_bytes,
    parse_ffprobe,
    parse_levels,
    probe,
    reference_command,
)

# Recorte real de `ffprobe` sobre House S01E01 (verificado en vivo).
HOUSE_PROBE = {
    "streams": [
        {"index": 0, "codec_name": "h264", "codec_type": "video", "tags": {"language": "eng"}},
        {
            "index": 1,
            "codec_name": "dts",
            "codec_type": "audio",
            "channels": 6,
            "channel_layout": "5.1(side)",
            "disposition": {"default": 1, "forced": 0},
            "tags": {"language": "eng"},
        },
        {
            "index": 2,
            "codec_name": "subrip",
            "codec_type": "subtitle",
            "disposition": {"default": 0, "forced": 0, "hearing_impaired": 0},
            "tags": {"language": "eng", "title": "SDH"},
        },
        {"index": 3, "codec_name": "subrip", "codec_type": "subtitle", "tags": {"language": "und"}},
    ],
    "format": {"duration": "2645.560000", "tags": {"title": "house.s01e01.1080p.bluray.x264-filmhd"}},
}


def test_parse_ffprobe_reads_streams_tags_and_format():
    info = parse_ffprobe(HOUSE_PROBE)

    assert info.duration == pytest.approx(2645.56)
    assert info.format_title == "house.s01e01.1080p.bluray.x264-filmhd"
    audio = info.of_kind("audio")[0]
    assert (audio.index, audio.codec, audio.channels, audio.channel_layout, audio.default) == (
        1,
        "dts",
        6,
        "5.1(side)",
        True,
    )
    subtitles = info.of_kind("subtitle")
    assert subtitles[0].title == "SDH"
    assert subtitles[1].language is None  # "und" no es un idioma


def test_parse_ffprobe_tolerates_missing_fields():
    info = parse_ffprobe({"streams": [{"codec_type": "audio"}], "format": {"duration": "N/A"}})

    assert info.duration == 0.0
    assert info.streams[0].index == 0


def test_probe_raises_when_ffprobe_fails(monkeypatch):
    monkeypatch.setattr(
        media_probe.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a[0], 1, "", "Invalid data")
    )

    with pytest.raises(MediaProbeError, match="Invalid data"):
        probe(Path("x.mkv"))


def test_reference_command_extracts_subtitle_and_center_channel_levels_in_one_pass(tmp_path):
    subtitle = StreamInfo(index=3, kind="subtitle", codec="subrip", language="eng")
    audio = StreamInfo(index=1, kind="audio", codec="dts", channels=6, channel_layout="5.1(side)")

    command = reference_command(Path("/v.mkv"), subtitle, audio, tmp_path / "ref.srt", tmp_path / "lv.txt")

    joined = " ".join(command)
    assert command.count("-i") == 1  # una sola lectura del archivo
    assert "-map 0:3 -c:s srt -f srt" in joined
    assert "-map 0:1 -vn" in joined  # nunca decodifica video
    assert "pan=mono|c0=FC" in joined
    assert "astats=metadata=1:reset=1" in joined


def test_dialogue_filter_by_layout():
    assert dialogue_filter(StreamInfo(1, "audio", "ac3", channels=6, channel_layout="5.1")) == ["pan=mono|c0=FC"]
    assert dialogue_filter(StreamInfo(1, "audio", "ac3", channels=6)) == ["pan=mono|c0=c2"]
    assert dialogue_filter(StreamInfo(1, "audio", "aac", channels=2, channel_layout="stereo")) == [
        "pan=mono|c0=0.5*c0+0.5*c1"
    ]
    assert dialogue_filter(StreamInfo(1, "audio", "aac", channels=1, channel_layout="mono")) == []


def test_parse_levels_handles_silence_and_garbage():
    text = (
        "frame:0    pts:0       pts_time:0\n"
        "lavfi.astats.Overall.RMS_level=-inf\n"
        "frame:1    pts:400     pts_time:0.05\n"
        "lavfi.astats.Overall.RMS_level=-31.5\n"
        "lavfi.astats.Overall.RMS_level=nan\n"
    )

    assert parse_levels(text) == [-120.0, -31.5, -120.0]


def test_levels_round_trip_through_bytes():
    assert levels_from_bytes(levels_to_bytes([-31.4, -120.0, 0.0, -200.0])) == [-31.0, -120.0, -0.0, -120.0]


def test_extract_reference_reads_what_ffmpeg_wrote(tmp_path, monkeypatch):
    def fake_run(command, **kwargs):
        (tmp_path / "referencia.srt").write_text("1\n00:00:01,000 --> 00:00:02,000\nHi\n")
        (tmp_path / "niveles.txt").write_text("lavfi.astats.Overall.RMS_level=-30\n")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(media_probe.subprocess, "run", fake_run)
    subtitle = StreamInfo(index=3, kind="subtitle", codec="subrip")
    audio = StreamInfo(index=1, kind="audio", codec="aac", channels=2)

    extracted = extract_reference(Path("/v.mkv"), subtitle, audio, tmp_path)

    assert "Hi" in extracted.subtitle_text
    assert extracted.levels_db == [-30.0]


def test_extract_reference_raises_on_ffmpeg_error(tmp_path, monkeypatch):
    monkeypatch.setattr(media_probe.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a[0], 1, "", "boom"))

    with pytest.raises(MediaProbeError, match="boom"):
        extract_reference(Path("/v.mkv"), None, StreamInfo(1, "audio", "aac"), tmp_path)
