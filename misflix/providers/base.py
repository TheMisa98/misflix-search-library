from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Protocol

from misflix.core.models import DownloadOption, Media
from misflix.infra.http_client import HttpClient


class _HasText(Protocol):
    @property
    def text(self) -> str:
        """El html crudo de la respuesta."""
        ...


class HttpGetClient(Protocol):
    """Contrato minimo que necesita un `StaticProvider` para pedir paginas.

    Tipado de forma estructural (en vez de contra `HttpClient` directo) para
    que un provider detras de proteccion extra (ej. `ZonaLerosProvider` con
    `CloudflareHttpClient`) pueda usar su propio cliente sin heredar un tipo
    de atributo incompatible con el de la clase base.
    """

    def get(self, url: str) -> _HasText:
        """Pide `url` y devuelve una respuesta con `.text` (el html crudo).

        Args:
            url: Url a pedir.

        Returns:
            La respuesta, con al menos un atributo `.text`.
        """
        ...


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

    http: HttpGetClient

    def __init__(self, http_client: HttpGetClient | None = None):
        """Inicializa el provider.

        Args:
            http_client: Cliente HTTP a usar. Por defecto, `HttpClient`.
        """
        self.http = http_client or HttpClient()
