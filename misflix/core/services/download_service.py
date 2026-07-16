from __future__ import annotations

from pathlib import Path

from misflix.core.models import DownloadOption, Media
from misflix.infra.downloader import HttpxDownloader


class DownloadService:
    """Orquesta la descarga de un DownloadOption hacia el filesystem."""

    def __init__(self, downloader: HttpxDownloader | None = None):
        self._downloader = downloader or HttpxDownloader()

    def download(self, media: Media, option: DownloadOption, dest_dir: Path) -> Path:
        dest_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{media.title}{option.extension or ''}"
        dest_path = dest_dir / filename

        self._downloader.download(option.url, dest_path)
        return dest_path
