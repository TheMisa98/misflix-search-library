from __future__ import annotations

from pathlib import Path

from misflix.core.models import DownloadOption, Media, MediaKind
from misflix.core.services.download_service import DownloadService


class FakeDownloader:
    def __init__(self):
        self.calls: list[tuple[str, Path]] = []

    def download(self, url: str, dest_path: Path, on_progress=None) -> None:
        self.calls.append((url, dest_path))


def make_media(title: str) -> Media:
    return Media(id=title, title=title, kind=MediaKind.MOVIE, source="a", page_url="http://example.com")


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


def test_download_without_extension_uses_bare_title(tmp_path):
    downloader = FakeDownloader()
    service = DownloadService(downloader=downloader)
    media = make_media("My Book")
    option = DownloadOption(label="epub", url="http://example.com/book")

    result = service.download(media, option, tmp_path)

    assert result == tmp_path / "My Book"
