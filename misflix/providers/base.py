from __future__ import annotations

from abc import ABC, abstractmethod

from misflix.core.models import DownloadOption, Media
from misflix.infra.http_client import HttpClient


class BaseProvider(ABC):
    """Base comun para todos los providers. No instanciar directamente.

    Attributes:
        name: Nombre unico con el que el provider se registra (ver
            `providers/registry.py`).
    """

    name: str

    @abstractmethod
    def search(self, query: str) -> list[Media]:
        """Busca `query` en el repo.

        Args:
            query: Texto de busqueda.

        Returns:
            Resultados encontrados.
        """
        ...

    @abstractmethod
    def get_media(self, media_id: str) -> Media:
        """Resuelve la ficha completa de `media_id`.

        Args:
            media_id: Id devuelto por `search`.

        Returns:
            El `Media` con todos los datos disponibles en su ficha.
        """
        ...

    @abstractmethod
    def get_download_options(self, media: Media) -> list[DownloadOption]:
        """Lista las variantes descargables de `media`.

        Args:
            media: Media ya resuelto via `get_media`.

        Returns:
            Opciones de descarga disponibles.
        """
        ...


class StaticProvider(BaseProvider):
    """Provider para repos servidos como HTML plano (httpx + BeautifulSoup)."""

    def __init__(self, http_client: HttpClient | None = None):
        """Inicializa el provider.

        Args:
            http_client: Cliente HTTP a usar. Por defecto, `HttpClient`.
        """
        self.http = http_client or HttpClient()
