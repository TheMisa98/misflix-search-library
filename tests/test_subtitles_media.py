from __future__ import annotations

import pytest

from misflix.core.subtitles.media import (
    MediaInfo,
    StreamInfo,
    has_center_channel,
    has_spanish_audio,
    has_spanish_subtitle_track,
    is_spanish,
    pick_dialogue_audio,
    pick_reference_subtitle,
    release_says_spanish,
)


def audio(index: int, language: str | None = None, title: str | None = None, **kwargs) -> StreamInfo:
    return StreamInfo(index=index, kind="audio", codec="aac", language=language, title=title, **kwargs)


def subtitle(index: int, language: str | None = None, codec: str = "subrip", **kwargs) -> StreamInfo:
    return StreamInfo(index=index, kind="subtitle", codec=codec, language=language, **kwargs)


@pytest.mark.parametrize(
    ("stream", "expected"),
    [
        (audio(1, "spa"), True),
        (audio(1, "es-419"), True),
        (audio(1, None, "Latino"), True),  # pista `und` titulada (The Big Bang Theory, verificado en vivo)
        (audio(1, None, "Español Castellano"), True),
        (audio(1, None, "Inglés"), False),
        (audio(1, "spa", "Original"), True),  # pelicula hablada en español
        (audio(1, "eng", "Surround"), False),
        (audio(1, None, None), False),
    ],
)
def test_is_spanish(stream, expected):
    assert is_spanish(stream) is expected


def test_spanish_audio_and_subtitle_track_detection():
    dubbed = MediaInfo(duration=10, streams=(audio(1, None, "Latino"), audio(2, None, "Inglés")))
    forced_only = MediaInfo(duration=10, streams=(audio(1, "eng"), subtitle(2, "spa", forced=True)))
    full = MediaInfo(duration=10, streams=(audio(1, "eng"), subtitle(2, "spa")))

    assert has_spanish_audio(dubbed)
    assert not has_spanish_subtitle_track(forced_only)
    assert has_spanish_subtitle_track(full)


@pytest.mark.parametrize(
    "name",
    [
        "The Big Bang Theory CASTELLANO CAPÍTULO ESPECIAL (DESPEDIDA).mkv",
        "The Big Bang Theory SUBTITULADO ESPAÑOL CAPÍTULO ESPECIAL.mkv",
        "Movie.2020.DUAL.1080p.mkv",
    ],
)
def test_release_says_spanish(name):
    assert release_says_spanish(name)


def test_release_without_spanish_markers():
    assert not release_says_spanish("house.s01e01.1080p.bluray.x264-filmhd.mkv")


def test_pick_reference_subtitle_prefers_text_non_sdh_english():
    info = MediaInfo(
        duration=10,
        streams=(
            subtitle(2, "eng", title="SDH"),
            subtitle(3, "eng"),
            subtitle(4, "fre"),
            subtitle(5, "eng", codec="hdmv_pgs_subtitle"),
            subtitle(6, "spa"),
        ),
    )

    assert pick_reference_subtitle(info).index == 3


def test_pick_reference_subtitle_ignores_images_forced_and_spanish():
    info = MediaInfo(
        duration=10,
        streams=(subtitle(2, "eng", codec="hdmv_pgs_subtitle"), subtitle(3, "eng", forced=True), subtitle(4, "spa")),
    )

    assert pick_reference_subtitle(info) is None


def test_pick_dialogue_audio_prefers_non_spanish_with_center_channel():
    info = MediaInfo(
        duration=10,
        streams=(
            audio(1, "spa", channels=6, channel_layout="5.1"),
            audio(2, "eng", channels=2, channel_layout="stereo", default=True),
            audio(3, "eng", channels=6, channel_layout="5.1(side)"),
        ),
    )

    assert pick_dialogue_audio(info).index == 3
    assert pick_dialogue_audio(MediaInfo(duration=1, streams=())) is None


@pytest.mark.parametrize(
    ("stream", "expected"),
    [
        (audio(1, channels=6, channel_layout="5.1(side)"), True),
        (audio(1, channels=8, channel_layout="7.1"), True),
        (audio(1, channels=6, channel_layout=None), True),
        (audio(1, channels=2, channel_layout="stereo"), False),
    ],
)
def test_has_center_channel(stream, expected):
    assert has_center_channel(stream) is expected


def test_pick_dialogue_audio_skips_codecs_that_cannot_be_decoded_and_commentaries():
    info = MediaInfo(
        duration=10,
        streams=(
            StreamInfo(1, "audio", "eac3", language="eng", channels=6, channel_layout="5.1"),
            StreamInfo(2, "audio", "ac3", language="eng", title="Director's Commentary", channels=6),
            StreamInfo(3, "audio", "ac3", language="eng", channels=2, channel_layout="stereo"),
        ),
    )

    assert pick_dialogue_audio(info).index == 1
    assert pick_dialogue_audio(info, decodable={"ac3", "dts"}).index == 3
    assert pick_dialogue_audio(info, decodable={"dts"}) is None


def test_bracketed_track_titles_like_the_big_bang_theory_releases():
    # pistas reales de The Big Bang Theory 9x17 (verificado en vivo)
    info = MediaInfo(
        duration=10,
        streams=(
            StreamInfo(1, "audio", "ac3", title="The Celebration Experimentation"),
            StreamInfo(2, "audio", "aac", title="[AAC | 2.0 ENG]"),
            StreamInfo(3, "subtitle", "subrip", title="[SRT | LAT]"),
            StreamInfo(4, "subtitle", "subrip", title="[SRT | ENG]"),
        ),
    )

    assert has_spanish_subtitle_track(info)
    assert pick_reference_subtitle(info).index == 4
