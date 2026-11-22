import re
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import typer

from misflix.cli import subs
from misflix.cli.subs import _OUTCOMES, _outside_library, _pass_line, _PassNotices, _retry_note, _video_name
from misflix.config.settings import Settings
from misflix.core.services.subtitle_service import LibraryVideo, RunSummary, SubtitleStatus, VideoRecord


def episode(number: int) -> LibraryVideo:
    return LibraryVideo(
        path=Path(f"/lib/House/Season 6/House.S06E{number:02d}.mkv"),
        kind="episode",
        titles=("House", "Dr. House"),
        season=6,
        episode=number,
    )


def record(video: LibraryVideo, status: SubtitleStatus, retry_after: str | None = None) -> VideoRecord:
    return VideoRecord(path=str(video.path), size=1, mtime=1.0, status=status.value, retry_after=retry_after)


class Sent:
    """Junta los avisos en vez de mostrarlos."""

    def __init__(self) -> None:
        self.notices: list[tuple[str, str]] = []

    def __call__(self, title: str, body: str) -> None:
        self.notices.append((title, body))


def test_a_new_video_is_announced_as_soon_as_it_is_done():
    sent = Sent()
    notices = _PassNotices(sent)
    video = episode(3)

    notices.on_result(video, None, record(video, SubtitleStatus.DOWNLOADED))

    assert sent.notices == [("Subtítulo listo", "House 6x03\nSe bajó uno y coincide con el video.")]
    notices.finish()
    assert len(sent.notices) == 1  # no se repite en el aviso del final


def test_a_new_video_without_subtitle_says_when_it_is_retried():
    sent = Sent()
    notices = _PassNotices(sent)
    video = episode(3)

    notices.on_result(video, None, record(video, SubtitleStatus.NOT_FOUND, "2099-12-31T12:00:00+00:00"))

    expected = "House 6x03\nNo se encontró uno que coincida con el video. Se vuelve a intentar el 31/12."
    assert sent.notices == [("Sin subtítulo por ahora", expected)]


def test_new_videos_past_the_limit_are_counted_in_one_notice_at_the_end():
    sent = Sent()
    notices = _PassNotices(sent)
    videos = [episode(number) for number in range(1, 10)]

    for video in videos[:8]:
        notices.on_result(video, None, record(video, SubtitleStatus.DOWNLOADED))
    notices.on_result(videos[8], None, record(videos[8], SubtitleStatus.NOT_FOUND))
    notices.finish()

    assert len(sent.notices) == 6  # 5 uno por uno y uno con el resto
    assert sent.notices[-1] == ("Subtítulos actualizados", "3 subtítulos bajados, 1 sin subtítulo")


def test_the_final_notice_names_the_videos_when_there_are_few():
    sent = Sent()
    notices = _PassNotices(sent)
    downloaded, restored = episode(2), episode(3)

    notices.on_result(
        downloaded, record(downloaded, SubtitleStatus.NOT_FOUND), record(downloaded, SubtitleStatus.DOWNLOADED)
    )
    notices.on_result(restored, record(restored, SubtitleStatus.NOT_FOUND), record(restored, SubtitleStatus.IN_SYNC))
    notices.finish()

    expected = "House 6x02: subtítulo bajado\nHouse 6x03: subtítulo verificado"
    assert sent.notices == [("Subtítulos actualizados", expected)]


@pytest.mark.parametrize(
    ("before", "after", "notified"),
    [
        (SubtitleStatus.UNCERTAIN, SubtitleStatus.UNCERTAIN, False),  # reintento que quedo igual
        (SubtitleStatus.NOT_FOUND, SubtitleStatus.NOT_FOUND, False),
        (SubtitleStatus.DOWNLOADED, SubtitleStatus.IN_SYNC, False),  # reevaluado sin cambios en el .srt
        (SubtitleStatus.NOT_FOUND, SubtitleStatus.SPANISH_AUDIO, False),
        (SubtitleStatus.NOT_FOUND, SubtitleStatus.UNCERTAIN, True),
        (SubtitleStatus.UNCERTAIN, SubtitleStatus.IN_SYNC, True),  # el dudoso se confirmo
        (SubtitleStatus.IN_SYNC, SubtitleStatus.FIXED, True),
        (SubtitleStatus.UNCERTAIN, SubtitleStatus.DOWNLOADED, True),
    ],
)
def test_a_known_video_only_notifies_when_its_subtitles_changed(before, after, notified):
    sent = Sent()
    notices = _PassNotices(sent)
    video = episode(1)

    notices.on_result(video, record(video, before), record(video, after))
    notices.finish()

    assert bool(sent.notices) == notified


def test_every_status_has_a_notice():
    assert set(_OUTCOMES) == set(SubtitleStatus)


def test_video_names_are_short():
    movie = LibraryVideo(
        path=Path("/lib/Peliculas/Kick-Ass (2010)/k.mkv"), kind="movie", titles=("Kick-Ass",), year=2010
    )
    unknown = LibraryVideo(path=Path("/lib/Peliculas/raro.mkv"), kind="movie", titles=())

    assert _video_name(episode(3)) == "House 6x03"
    assert _video_name(movie) == "Kick-Ass (2010)"
    assert _video_name(unknown) == "raro"


def test_a_retry_later_today_says_the_time():
    note = _retry_note(record(episode(1), SubtitleStatus.PENDING, datetime.now(UTC).isoformat()))

    assert re.fullmatch(r" Se vuelve a intentar a las \d\d:\d\d\.", note)


def test_finished_videos_have_no_retry_note():
    assert _retry_note(record(episode(1), SubtitleStatus.DOWNLOADED)) == ""


def test_the_pass_line_says_what_was_done_and_when_the_next_one_is():
    summary = RunSummary(processed=3, by_status=Counter({"descargado": 2, "dudoso": 1}))

    line = _pass_line(summary, timedelta(minutes=15))

    assert line == "pasada terminada: 3 videos evaluados (2 descargado, 1 dudoso); la proxima en 15 min"


def library(root: Path) -> Settings:
    return Settings(
        movies_dir=root / "Peliculas",
        books_dir=root / "Libros",
        series_dir=root / "Series",
        state_dir=root / "estado",
        cache_dir=root / "cache",
    )


def test_paths_outside_the_library_are_detected(tmp_path):
    settings = library(tmp_path)

    assert not _outside_library(tmp_path / "Series" / "House" / "Season 1" / "house.s01e01.mkv", settings)
    assert not _outside_library(tmp_path / "Peliculas", settings)
    assert _outside_library(tmp_path / "Descargas" / "house.s01e01.mkv", settings)
    assert _outside_library(tmp_path / "SeriesViejas" / "x.mkv", settings)  # no alcanza con que empiece igual


def test_run_refuses_a_path_outside_the_library_instead_of_evaluating_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(subs, "get_settings", lambda: library(tmp_path))
    monkeypatch.setattr(subs, "_service", lambda log: pytest.fail("no deberia armar el servicio"))

    with pytest.raises(typer.Exit) as exit_info:
        subs.run(path=tmp_path / "Descargas" / "x.mkv", force=False, no_download=False)

    assert exit_info.value.exit_code == 1
