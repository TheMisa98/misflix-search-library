from __future__ import annotations

from pathlib import Path

from misflix.infra import library
from misflix.infra.library import read_nfo, scan_library, sidecar_subtitles

TVSHOW_NFO = """<?xml version="1.0" encoding="utf-8" standalone="yes"?>
<tvshow>
  <title>Dr. House</title>
  <originaltitle>House</originaltitle>
  <year>2004</year>
  <uniqueid type="imdb">tt0412142</uniqueid>
  <uniqueid type="tvdb">73255</uniqueid>
</tvshow>
"""


def make_video(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x")
    return path


def test_read_nfo_prefers_the_original_title_and_reads_imdb(tmp_path):
    nfo = tmp_path / "tvshow.nfo"
    nfo.write_text(TVSHOW_NFO)

    info = read_nfo(nfo)

    assert info is not None
    assert info.titles == ("House", "Dr. House")
    assert (info.year, info.imdb_id) == (2004, "tt0412142")


def test_read_nfo_tolerates_trailing_garbage(tmp_path):
    nfo = tmp_path / "movie.nfo"
    nfo.write_text(
        "<movie><title>Aliados</title><originaltitle>Allied</originaltitle><year>2016</year></movie>\n"
        "https://www.imdb.com/title/tt3640424/"
    )

    info = read_nfo(nfo)

    assert info is not None
    assert info.titles == ("Allied", "Aliados")
    assert info.imdb_id == "tt3640424"


def test_scan_library_identifies_movies_and_episodes(tmp_path, monkeypatch):
    monkeypatch.setattr(library, "_MIN_VIDEO_BYTES", 0)
    movies, series = tmp_path / "Peliculas", tmp_path / "Series"
    movie = make_video(movies / "Allied (2016)" / "Allied (2016) Bluray-1080p.mp4")
    (series / "House").mkdir(parents=True)
    (series / "House" / "tvshow.nfo").write_text(TVSHOW_NFO)
    episode = make_video(series / "House" / "Season 1" / "house.s01e03.1080p.bluray.x264-filmhd.mkv")
    make_video(series / "House" / "Season 1" / "Especial sin codigo.mkv")
    make_video(movies / "Allied (2016)" / "Extras" / "making of s01e01.mkv")

    videos = scan_library(movies, series)

    assert [v.path for v in videos] == [movie, episode]
    allied, house = videos
    assert (allied.kind, allied.titles, allied.year) == ("movie", ("Allied",), 2016)
    assert (house.kind, house.titles, house.season, house.episode) == ("episode", ("House", "Dr. House"), 1, 3)
    assert house.imdb_id == "tt0412142"


def test_scan_library_skips_small_samples(tmp_path):
    make_video(tmp_path / "Peliculas" / "X (2000)" / "sample.mkv")

    assert scan_library(tmp_path / "Peliculas", tmp_path / "Series") == []


def test_sidecar_subtitles_reads_the_language_from_the_name(tmp_path):
    video = make_video(tmp_path / "house.s01e01.mkv")
    for name in [
        "house.s01e01.srt",
        "house.s01e01.es.srt",
        "house.s01e01.hi.srt",
        "house.s01e01.eng.forced.srt",
        "house.s01e02.es.srt",
        "house.s01e01.nfo",
    ]:
        (tmp_path / name).write_text("x")

    found = {s.path.name: s.language for s in sidecar_subtitles(video)}

    assert found == {
        "house.s01e01.srt": None,
        "house.s01e01.es.srt": "es",
        "house.s01e01.hi.srt": None,
        "house.s01e01.eng.forced.srt": "en",
    }
