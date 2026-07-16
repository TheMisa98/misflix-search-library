from __future__ import annotations

from abc import ABC, abstractmethod

from misflix.core.models import DownloadOption, Media
from misflix.infra.http_client import HttpClient


class BaseProvider(ABC):
    """Base comun para todos los providers. No instanciar directamente."""

    name: str

    @abstractmethod
    def search(self, query: str) -> list[Media]: ...

    @abstractmethod
    def get_media(self, media_id: str) -> Media: ...

    @abstractmethod
    def get_download_options(self, media: Media) -> list[DownloadOption]: ...


class StaticProvider(BaseProvider):
    """Provider para repos servidos como HTML plano (httpx + BeautifulSoup)."""

    def __init__(self, http_client: HttpClient | None = None):
        self.http = http_client or HttpClient()


class DynamicProvider(BaseProvider):
    """Provider para repos que requieren un navegador real (Playwright)."""

    def __init__(self):
        from misflix.infra.browser import BrowserSession

        self.browser = BrowserSession()
