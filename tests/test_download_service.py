from __future__ import annotations

from pathlib import Path

import pytest

from misflix.core.models import DownloadOption, Media, MediaKind
from misflix.core.services.download_service import (
    DownloadService,
    group_episodes_by_season,
    parse_episode_code,
    season_folder_name,
)
from misflix.infra.archives import ExtractionError


class FakeDownloader:
    def __init__(self):
        self.calls: list[tuple[str, Path]] = []
        self.progress_callbacks: list = []

    def download(self, url: str, dest_path: Path, on_progress=None) -> None:
        self.calls.append((url, dest_path))
        self.progress_callbacks.append(on_progress)


def make_media(title: str, year: int | None = None) -> Media:
    return Media(id=title, title=title, kind=MediaKind.MOVIE, source="a", page_url="http://example.com", year=year)


def make_episode(title: str) -> Media:
    return Media(id=f"episode:{title}", title=title, kind=MediaKind.SERIES, source="a", page_url="http://example.com")


@pytest.fixture(autouse=True)
def no_real_imdb_lookups(monkeypatch):
    """resolve_folder_name pega a IMDb por red; los tests que no la ejercitan a
    proposito no deberian depender de eso, asi que por defecto no encuentra nada."""
    monkeypatch.setattr("misflix.core.services.download_service.resolve_title", lambda query, year_hint=None: None)


def test_download_creates_dest_dir_and_delegates_to_downloader(tmp_path):
    downloader = FakeDownloader()
    service = DownloadService(downloader=downloader)
    media = make_media("My Movie")
    option = DownloadOption(label="1080p", url="http://example.com/movie.mp4", extension=".mp4")
    dest_dir = tmp_path / "movies"

    result = service.download(media, option, dest_dir)

    assert dest_dir.exists()
    assert result == dest_dir / "My Movie.mp4"
    assert downloader.calls == [(option.url, result)]


def test_download_passes_on_progress_through_to_the_downloader(tmp_path):
    downloader = FakeDownloader()
    service = DownloadService(downloader=downloader)
    media = make_media("My Movie")
    option = DownloadOption(label="1080p", url="http://example.com/movie.mp4", extension=".mp4")
    callback = lambda downloaded, total: None  # noqa: E731

    service.download(media, option, tmp_path, on_progress=callback)

    assert downloader.progress_callbacks == [callback]


def test_download_without_extension_uses_bare_title(tmp_path):
    downloader = FakeDownloader()
    service = DownloadService(downloader=downloader)
    media = make_media("My Book")
    option = DownloadOption(label="epub", url="http://example.com/book")

    result = service.download(media, option, tmp_path)

    assert result == tmp_path / "My Book"


def test_download_opens_externally_instead_of_streaming(tmp_path, monkeypatch):
    opened_urls: list[str] = []
    monkeypatch.setattr(
        "misflix.core.services.download_service.open_in_browser",
        opened_urls.append,
    )
    downloader = FakeDownloader()
    service = DownloadService(downloader=downloader)
    media = make_media("My Movie")
    option = DownloadOption(label="MEGA", url="http://example.com/redirect", opens_externally=True)

    result = service.download(media, option, tmp_path)

    assert result is None
    assert opened_urls == [option.url]
    assert downloader.calls == []


def test_download_delegates_antupload_urls_to_the_antupload_resolver(tmp_path, monkeypatch):
    calls: list[tuple[str, Path]] = []
    monkeypatch.setattr(
        "misflix.core.services.download_service.download_from_antupload",
        lambda url, dest_path, on_progress=None: calls.append((url, dest_path)),
    )
    downloader = FakeDownloader()
    service = DownloadService(downloader=downloader)
    media = make_media("My Book")
    option = DownloadOption(label="EPUB", url="https://www.antupload.com/file/abc123/", extension=".epub")

    result = service.download(media, option, tmp_path)

    assert result == tmp_path / "My Book.epub"
    assert calls == [(option.url, result)]
    assert downloader.calls == []


def test_resolve_folder_name_uses_imdb_title_and_year_when_found(monkeypatch):
    monkeypatch.setattr(
        "misflix.core.services.download_service.resolve_title",
        lambda query, year_hint=None: ("Blade Runner 2049", 2017),
    )
    service = DownloadService(downloader=FakeDownloader())
    media = make_media("Blade Runner 2049 online hd")

    assert service.resolve_folder_name(media) == "Blade Runner 2049 (2017)"


def test_resolve_folder_name_falls_back_to_media_title_and_year_without_imdb_match():
    service = DownloadService(downloader=FakeDownloader())
    media = make_media("Some Obscure Title", year=2020)

    assert service.resolve_folder_name(media) == "Some Obscure Title (2020)"


def test_resolve_folder_name_skips_imdb_for_series_episodes(monkeypatch):
    def fail_if_called(query, year_hint=None):
        raise AssertionError("no deberia buscar un episodio de serie en IMDb como si fuera pelicula")

    monkeypatch.setattr("misflix.core.services.download_service.resolve_title", fail_if_called)
    service = DownloadService(downloader=FakeDownloader())
    episode = Media(
        id="episode:breaking-bad-5-1-h",
        title="Breaking Bad 5x1",
        kind=MediaKind.SERIES,
        source="zona-leros",
        page_url="http://example.com",
    )

    assert service.resolve_folder_name(episode) == "Breaking Bad 5x1"


def test_resolve_folder_name_without_any_year_uses_bare_title():
    service = DownloadService(downloader=FakeDownloader())
    media = make_media("Some Obscure Title")

    assert service.resolve_folder_name(media) == "Some Obscure Title"


def test_download_parts_single_url_has_no_part_suffix(tmp_path):
    downloader = FakeDownloader()
    service = DownloadService(downloader=downloader)
    media = make_media("My Movie")

    saved = service.download_parts(media, ["http://example.com/movie.rar"], tmp_path, folder_name="My Movie")

    expected = tmp_path / "My Movie" / "My Movie.rar"
    assert saved == [expected]
    assert downloader.calls == [("http://example.com/movie.rar", expected)]


def test_download_parts_creates_a_subfolder_named_after_the_movie(tmp_path):
    downloader = FakeDownloader()
    service = DownloadService(downloader=downloader)
    media = make_media("My Movie")

    service.download_parts(media, ["http://example.com/movie.rar"], tmp_path, folder_name="My Movie")

    assert (tmp_path / "My Movie").is_dir()


def test_download_parts_uses_resolved_folder_name_when_not_given(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "misflix.core.services.download_service.resolve_title",
        lambda query, year_hint=None: ("Blade Runner 2049", 2017),
    )
    downloader = FakeDownloader()
    service = DownloadService(downloader=downloader)
    media = make_media("Blade Runner 2049 online hd")

    saved = service.download_parts(media, ["http://example.com/movie.rar"], tmp_path)

    expected = tmp_path / "Blade Runner 2049 (2017)" / "Blade Runner 2049 (2017).rar"
    assert saved == [expected]


def test_download_parts_multiple_urls_get_sequential_part_suffix(tmp_path):
    downloader = FakeDownloader()
    service = DownloadService(downloader=downloader)
    media = make_media("My Movie")
    urls = ["http://example.com/movie.part1.rar", "http://example.com/movie.part2.rar"]

    saved = service.download_parts(media, urls, tmp_path, folder_name="My Movie")

    movie_dir = tmp_path / "My Movie"
    assert saved == [movie_dir / "My Movie.part1.rar", movie_dir / "My Movie.part2.rar"]
    assert [call[0] for call in downloader.calls] == urls


def test_download_parts_resolves_mediafire_pages(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "misflix.core.services.download_service.resolve_direct_url",
        lambda url: "https://download.mediafire.com/direct/movie.rar",
    )
    downloader = FakeDownloader()
    service = DownloadService(downloader=downloader)
    media = make_media("My Movie")

    saved = service.download_parts(
        media, ["https://www.mediafire.com/file/abc/movie.rar/file"], tmp_path, folder_name="My Movie"
    )

    expected = tmp_path / "My Movie" / "My Movie.rar"
    assert saved == [expected]
    assert downloader.calls == [("https://download.mediafire.com/direct/movie.rar", expected)]


def test_download_parts_uses_progress_factory_per_part(tmp_path):
    downloader = FakeDownloader()
    service = DownloadService(downloader=downloader)
    media = make_media("My Movie")
    urls = ["http://example.com/movie.part1.rar", "http://example.com/movie.part2.rar"]
    calls: list[tuple[int, int]] = []

    def progress_factory(index: int, total: int):
        calls.append((index, total))
        return lambda downloaded, total_bytes: None

    service.download_parts(media, urls, tmp_path, folder_name="My Movie", progress_factory=progress_factory)

    assert calls == [(1, 2), (2, 2)]


def test_download_parts_starts_progress_before_resolving_mediafire(tmp_path, monkeypatch):
    """La tarea de progreso tiene que aparecer antes de resolver cada pagina de
    Mediafire (un GET bloqueante), no despues: si no, un pack de varias partes se
    ve como si no pasara nada entre una parte y la siguiente."""
    events: list[str] = []

    monkeypatch.setattr(
        "misflix.core.services.download_service.resolve_direct_url",
        lambda url: events.append("resolve") or "https://download.mediafire.com/direct/movie.rar",
    )
    downloader = FakeDownloader()
    service = DownloadService(downloader=downloader)
    media = make_media("My Movie")

    def progress_factory(index: int, total: int):
        events.append("progress_started")
        return lambda downloaded, total_bytes: None

    service.download_parts(
        media,
        ["https://www.mediafire.com/file/abc/movie.rar/file"],
        tmp_path,
        folder_name="My Movie",
        progress_factory=progress_factory,
    )

    assert events == ["progress_started", "resolve"]


def test_extract_and_organize_returns_none_when_nothing_to_extract(tmp_path, monkeypatch):
    monkeypatch.setattr("misflix.core.services.download_service.extract_rar", lambda movie_dir: False)
    service = DownloadService(downloader=FakeDownloader())

    assert service.extract_and_organize("My Movie", tmp_path) is None


def test_extract_and_organize_flattens_and_deletes_rars_after_a_successful_extraction(tmp_path, monkeypatch):
    monkeypatch.setattr("misflix.core.services.download_service.extract_rar", lambda movie_dir: True)
    flattened = tmp_path / "My Movie" / "My Movie.mkv"
    monkeypatch.setattr(
        "misflix.core.services.download_service.flatten_video",
        lambda movie_dir, target_stem: flattened,
    )
    deleted_dirs = []
    monkeypatch.setattr(
        "misflix.core.services.download_service.delete_rar_parts",
        deleted_dirs.append,
    )
    service = DownloadService(downloader=FakeDownloader())

    assert service.extract_and_organize("My Movie", tmp_path) == flattened
    assert deleted_dirs == [tmp_path / "My Movie"]


def test_extract_and_organize_keeps_rars_when_no_video_was_found(tmp_path, monkeypatch):
    monkeypatch.setattr("misflix.core.services.download_service.extract_rar", lambda movie_dir: True)
    monkeypatch.setattr(
        "misflix.core.services.download_service.flatten_video",
        lambda movie_dir, target_stem: None,
    )
    deleted_dirs = []
    monkeypatch.setattr(
        "misflix.core.services.download_service.delete_rar_parts",
        deleted_dirs.append,
    )
    service = DownloadService(downloader=FakeDownloader())

    assert service.extract_and_organize("My Movie", tmp_path) is None
    assert deleted_dirs == []


def test_extract_and_organize_finds_an_already_uncompressed_video_without_a_rar(tmp_path, monkeypatch):
    """Mediafire a veces sirve el video directo, sin comprimir en un .rar (visto
    en produccion) — `extract_and_organize` tiene que reconocerlo igual, no solo
    el caso de un .rar extraido."""
    monkeypatch.setattr("misflix.core.services.download_service.extract_rar", lambda movie_dir: False)
    movie_dir = tmp_path / "My Episode"
    movie_dir.mkdir()
    video = movie_dir / "My Episode.mkv"
    video.write_bytes(b"fake video content")
    service = DownloadService(downloader=FakeDownloader())

    assert service.extract_and_organize("My Episode", tmp_path) == video
    assert video.exists()


def test_extract_and_organize_propagates_extraction_error(tmp_path, monkeypatch):
    def raise_error(movie_dir):
        raise ExtractionError("wrong password")

    monkeypatch.setattr("misflix.core.services.download_service.extract_rar", raise_error)
    service = DownloadService(downloader=FakeDownloader())

    with pytest.raises(ExtractionError):
        service.extract_and_organize("My Movie", tmp_path)


def test_download_uses_filename_stem_override_when_given(tmp_path):
    downloader = FakeDownloader()
    service = DownloadService(downloader=downloader)
    media = make_media("Breaking Bad 5x1")
    option = DownloadOption(label="1080p", url="http://example.com/ep.mp4", extension=".mp4")

    result = service.download(media, option, tmp_path, filename_stem="Breaking Bad - S05E01")

    assert result == tmp_path / "Breaking Bad - S05E01.mp4"


def test_parse_episode_code_extracts_season_and_episode():
    assert parse_episode_code("Breaking Bad 5x1") == (5, 1)
    assert parse_episode_code("Breaking Bad 12x03") == (12, 3)


def test_parse_episode_code_returns_none_without_a_code():
    assert parse_episode_code("Breaking Bad") is None


def test_group_episodes_by_season_groups_by_parsed_code():
    episodes = [make_episode("Show 1x1"), make_episode("Show 1x2"), make_episode("Show 2x1")]

    groups = group_episodes_by_season(episodes)

    assert set(groups) == {1, 2}
    assert [e.title for e in groups[1]] == ["Show 1x1", "Show 1x2"]
    assert [e.title for e in groups[2]] == ["Show 2x1"]


def test_group_episodes_by_season_buckets_unparsable_titles_under_zero():
    episodes = [make_episode("Special episode")]

    groups = group_episodes_by_season(episodes)

    assert set(groups) == {0}


def test_season_folder_name_is_zero_padded_and_in_english():
    assert season_folder_name(5) == "Season 05"
    assert season_folder_name(12) == "Season 12"


def test_resolve_episode_stem_formats_plex_style_code():
    service = DownloadService(downloader=FakeDownloader())

    assert service.resolve_episode_stem("Breaking Bad", "Breaking Bad 5x1") == "Breaking Bad - S05E01"


def test_resolve_episode_stem_falls_back_to_episode_title_without_a_code():
    service = DownloadService(downloader=FakeDownloader())

    assert service.resolve_episode_stem("Some Show", "Especial de Navidad") == "Especial de Navidad"


def test_resolve_season_dir_groups_under_a_padded_season_folder(tmp_path):
    service = DownloadService(downloader=FakeDownloader())

    result = service.resolve_season_dir("Breaking Bad", "Breaking Bad 5x1", tmp_path)

    assert result == tmp_path / "Breaking Bad" / "Season 05"


def test_resolve_season_dir_falls_back_to_season_zero_without_a_code(tmp_path):
    service = DownloadService(downloader=FakeDownloader())

    result = service.resolve_season_dir("Some Show", "Especial de Navidad", tmp_path)

    assert result == tmp_path / "Some Show" / "Season 00"


def test_already_downloaded_finds_an_existing_organized_video(tmp_path):
    (tmp_path / "Breaking Bad - S05E01.mkv").write_bytes(b"x")
    service = DownloadService(downloader=FakeDownloader())

    result = service.already_downloaded(tmp_path, "Breaking Bad - S05E01")

    assert result == tmp_path / "Breaking Bad - S05E01.mkv"


def test_already_downloaded_returns_none_when_nothing_is_there(tmp_path):
    service = DownloadService(downloader=FakeDownloader())

    assert service.already_downloaded(tmp_path, "Breaking Bad - S05E01") is None
