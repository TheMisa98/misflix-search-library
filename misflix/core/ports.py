from __future__ import annotations

from typing import Protocol

from misflix.core.models import DownloadOption, Media


class SourceProvider(Protocol):
    """Contrato que debe cumplir cada repo/fuente scrapeable."""

    name: str

    def search(self, query: str) -> list[Media]: ...

    def get_media(self, media_id: str) -> Media: ...

    def get_download_options(self, media: Media) -> list[DownloadOption]: ...


class Downloader(Protocol):
    def download(self, url: str, dest_path: str, on_progress=None) -> None: ...


class CoverRenderer(Protocol):
    def render_url(self, cover_url: str) -> None: ...
