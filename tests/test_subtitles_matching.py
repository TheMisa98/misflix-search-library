from __future__ import annotations

import pytest

from misflix.core.models import SubtitleCandidate
from misflix.core.subtitles.matching import (
    EpisodeRange,
    matches_episode,
    matches_movie,
    normalize,
    parse_episode_range,
    pick_subtitle_files,
    rank_candidates,
    release_profile,
    title_matches,
)


def candidate(title: str, description: str = "", downloads: int = 0, imdb_id: str | None = None, id_: str = "1"):
    return SubtitleCandidate(
        id=id_, source="subdivx", title=title, description=description, downloads=downloads, imdb_id=imdb_id
    )


HOUSE_TITLES = ["House", "Dr. House"]


def test_normalize_strips_accents_punctuation_and_case():
    assert normalize("Dr. House: M.D. — Teoría & Práctica") == "dr house m d teoria and practica"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("House M.D. S01E01", EpisodeRange(1, 1, 1)),
        ("House M.D. S01E01-11", EpisodeRange(1, 1, 11)),
        ("Lost s02e01-e05", EpisodeRange(2, 1, 5)),
        ("The Big Bang Theory - 7x1 - The Hofstadter Insufficiency.mkv", EpisodeRange(7, 1, 1)),
        ("Show 1x01-1x05", EpisodeRange(1, 1, 5)),
        ("house.s01e03.1080p.bluray.x264-filmhd.srt", EpisodeRange(1, 3, 3)),
        ("Alpha House S01E01 - Pilot", EpisodeRange(1, 1, 1)),
    ],
)
def test_parse_episode_range(text, expected):
    assert parse_episode_range(text) == expected


def test_parse_episode_range_without_code_is_none():
    assert parse_episode_range("Allied (2016) Bluray-1080p") is None


@pytest.mark.parametrize(
    "title", ["House M.D. S01E01", "House MD S01E01", "Dr. House S01E01", "House (2004) S01E01", "house s01e01"]
)
def test_title_matches_accepts_the_same_show_with_harmless_extras(title):
    assert title_matches(title, HOUSE_TITLES)


@pytest.mark.parametrize(
    "title",
    ["House of Cards (2013) S01E01", "Bleak House S01E01", "House Husbands (AU) S01E01", "Desperate Housewives S01E01"],
)
def test_title_matches_rejects_other_shows_containing_the_word(title):
    assert not title_matches(title, HOUSE_TITLES)


def test_matches_episode_accepts_packs_covering_the_episode():
    pack = candidate("House M.D. S01E01-11")

    assert matches_episode(pack, HOUSE_TITLES, 1, 5) == EpisodeRange(1, 1, 11)
    assert matches_episode(pack, HOUSE_TITLES, 1, 12) is None
    assert matches_episode(pack, HOUSE_TITLES, 2, 5) is None


def test_matches_episode_rejects_forced_subtitles():
    forced = candidate("House of Guinness S01E01 - FORZADO")
    forced_in_description = candidate("House M.D. S01E01", "Subtítulo forzado para la versión doblada")

    assert matches_episode(forced, ["House of Guinness"], 1, 1) is None
    assert matches_episode(forced_in_description, HOUSE_TITLES, 1, 1) is None


def test_matches_movie_prefers_the_imdb_id_when_both_sides_have_it():
    assert matches_movie(candidate("Allied (2016)", imdb_id="tt3640424"), ["Aliados"], 2016, "tt3640424")
    assert not matches_movie(candidate("Allied (2016)", imdb_id="tt0000001"), ["Allied"], 2016, "tt3640424")


def test_matches_movie_falls_back_to_title_and_year():
    assert matches_movie(candidate("Allied  (2016)"), ["Allied"], 2016, None)
    assert matches_movie(candidate("Allied (2017)"), ["Allied"], 2016, None)  # ±1 por fechas de estreno
    assert not matches_movie(candidate("Allied (1999)"), ["Allied"], 2016, None)
    assert not matches_movie(candidate("Allied S01E01"), ["Allied"], 2016, None)


def test_release_profile_reads_group_source_resolution_and_service():
    profile = release_profile("house.s01e01.1080p.bluray.x264-filmhd.mkv")
    web = release_profile("Show.S01E01.2160p.AMZN.WEB-DL.DDP5.1.H.265-NTb")
    radarr = release_profile("Allied (2016) Bluray-1080p")

    assert (profile.group, profile.source, profile.resolution) == ("filmhd", "bluray", "1080p")
    assert (web.group, web.source, web.service) == ("ntb", "webdl", "amzn")
    assert (radarr.group, radarr.source, radarr.resolution) == (None, "bluray", "1080p")


def test_rank_candidates_puts_the_same_release_group_first_even_with_fewer_downloads():
    local = release_profile("house.s01e01.1080p.bluray.x264-filmhd.mkv")
    hdtv = candidate("House M.D. S01E01", "para la versión HDTV", downloads=19015, id_="hdtv")
    filmhd = candidate("House M.D. S01E01", "Sincronizado para la version bluray.x264-filmhd", downloads=219, id_="f")
    webdl = candidate("House M.D. S01E01", "Para House.M.D.S01E01.720p.WEB-DL.DD5.1.h.264-TjHD", 3188, id_="w")
    bluray = candidate("House M.D. S01E01-04", "Solo para House M.D Season 1 Bluray 720p", downloads=1464, id_="b")

    ranked = rank_candidates([hdtv, webdl, bluray, filmhd], local)

    assert [c.id for c in ranked] == ["f", "b", "hdtv", "w"]


def test_pick_subtitle_files_for_an_episode_inside_a_pack():
    names = ["House 1x01.srt", "House 1x02.srt", "subs/House.S01E03.srt", "leeme.txt"]

    assert pick_subtitle_files(names, 1, 2) == ["House 1x02.srt"]
    assert pick_subtitle_files(names, 1, 3) == ["subs/House.S01E03.srt"]
    assert pick_subtitle_files(names, 1, 9) == []


def test_pick_subtitle_files_single_unnumbered_file_and_movies():
    assert pick_subtitle_files(["Pilot.srt"], 1, 1) == ["Pilot.srt"]
    assert pick_subtitle_files(["cd1.srt", "cd2.srt", "nfo.txt"], None, None) == ["cd1.srt", "cd2.srt"]


def test_title_matches_alternate_titles_after_aka():
    # titulo real de subdivx para Tokyo Godfathers (verificado en vivo)
    title = "Tôkyô goddofâzâzu (2003) aka Tokyo Godfathers"

    assert title_matches(title, ["Tokyo Godfathers"])
    assert title_matches(title, ["Tôkyô goddofâzâzu"])
    assert matches_movie(
        candidate(title), ["東京ゴッドファーザーズ", "Héroes al rescate", "Tokyo Godfathers"], 2003, None
    )


def test_mentioned_durations():
    from misflix.core.subtitles.matching import mentioned_durations

    assert mentioned_durations("Para la edición Bluray de 1:31:46 de duración") == [5506.0]
    assert mentioned_durations("dura 92 min, la otra 1:32:53") == [5573.0, 5520.0]
    assert mentioned_durations("versión 1080p de 2,3 GB") == []


def test_rank_candidates_prefers_the_cut_with_the_same_duration():
    local = release_profile("Tokyo Godfathers (2003) Remux-1080p.mkv")
    bluray = candidate("Tokyo Godfathers (2003)", "Para la edición Bluray de 1:31:46 de duración", 1546, id_="old")
    remaster = candidate(
        "Tokyo Godfathers (2003)", "sincronizados para versiones Remastered de 1:32:52", 863, id_="new"
    )

    assert [c.id for c in rank_candidates([bluray, remaster], local, duration=5572.0)] == ["new", "old"]
    assert [c.id for c in rank_candidates([bluray, remaster], local, duration=5506.5)] == ["old", "new"]


def test_a_different_cut_from_the_same_group_loses_to_the_same_duration():
    # caso real: el REMUX local (1:31:46) y dos candidatos de subdivx
    local = release_profile("Tokyo Godfathers (2003) Remux-1080p.mkv", "Tokyo.Godfathers.2003.1080p.REMUX.DTS-HD-FGT")
    remastered = candidate(
        "Tokyo Godfathers (2003)", "Para la versión REMASTERED.1080p.BluRay.x264.DTS-FGT dura 1:32:53", 355, id_="r"
    )
    same_cut = candidate("Tokyo Godfathers (2003)", "Para la edición Bluray de 1:31:46 de duración", 1546, id_="b")

    assert [c.id for c in rank_candidates([remastered, same_cut], local, duration=5506.8)] == ["b", "r"]
