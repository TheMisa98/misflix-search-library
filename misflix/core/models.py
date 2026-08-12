from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class MediaKind(StrEnum):
    """Tipo de contenido que puede devolver un provider."""

    MOVIE = "movie"
    BOOK = "book"
    SERIES = "series"


# Agrupan los MediaKind que corresponden a cada comando de CLI ("movies"/
# "books" en cli/search.py y cli/download.py). Un provider puede mezclar
# varios kinds a la vez (zona-leros trae MOVIE y SERIES), asi que el comando
# "movies" filtra por lo que cada resultado *es*, no por que provider lo
# trajo — ver SearchService.search.
MOVIE_KINDS = frozenset({MediaKind.MOVIE, MediaKind.SERIES})
BOOK_KINDS = frozenset({MediaKind.BOOK})


@dataclass(frozen=True)
class DownloadOption:
    """Una variante descargable de un Media (ej. calidad, formato).

    Attributes:
        label: Texto para mostrar al usuario (ej. "MEGA (1080p)").
        url: Link a la opcion. Puede ser un link directo o, para un source
            protegido, uno que hay que resolver a mano (ver
            `opens_externally`).
        size_bytes: Tamaño del archivo, si el provider lo expone.
        extension: Extension de archivo (con punto, ej. ".mkv"), si se conoce
            de antemano.
        opens_externally: True si la descarga no se puede completar sola y
            hay que terminarla a mano en un navegador (ej. un ad-locker que
            exige resolver un captcha).
    """

    label: str
    url: str
    size_bytes: int | None = None
    extension: str | None = None
    opens_externally: bool = False


@dataclass
class Media:
    """Un resultado de busqueda: una pelicula, serie/episodio o libro encontrado en un repo.

    Attributes:
        id: Identificador dentro de su `source`. El formato es especifico de
            cada provider (ver, por ejemplo, los prefijos `series:`/
            `episode:` que usa zona-leros para desambiguar el tipo de pagina).
        title: Titulo tal como lo scrapeo el provider.
        kind: Que tipo de contenido es.
        source: Nombre del provider que lo devolvio (ver `providers/registry.py`).
        page_url: URL de la ficha en el sitio de origen.
        cover_url: URL de la portada, si el provider la expone.
        year: Año de estreno/publicacion, si se pudo scrapear.
        author: Autor(es), si el provider los expone (tipicamente libros;
            varios autores van juntos en un solo string, ej. "Brian Herbert,
            Frank Herbert").
        synopsis: Resumen/sinopsis, si el provider lo expone.
    """

    id: str
    title: str
    kind: MediaKind
    source: str
    page_url: str
    cover_url: str | None = None
    year: int | None = None
    author: str | None = None
    synopsis: str | None = None
