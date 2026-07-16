from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class MediaKind(str, Enum):
    MOVIE = "movie"
    BOOK = "book"


@dataclass(frozen=True)
class DownloadOption:
    """Una variante descargable de un Media (ej. calidad, formato)."""

    label: str
    url: str
    size_bytes: int | None = None
    extension: str | None = None


@dataclass
class Media:
    """Un resultado de busqueda: una pelicula o libro encontrado en un repo."""

    id: str
    title: str
    kind: MediaKind
    source: str
    page_url: str
    cover_url: str | None = None
    year: int | None = None
    extra: dict = field(default_factory=dict)
