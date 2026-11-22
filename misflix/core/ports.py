from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Protocol, runtime_checkable

from misflix.core.models import Author, DownloadedFile, DownloadOption, Media, MediaKind, SubtitleCandidate

ProgressCallback = Callable[[int, int], None]


@runtime_checkable
class SourceProvider(Protocol):
    """Contrato que debe cumplir cada repo/fuente scrapeable.

    `kinds` es opcional (probado con `getattr`, ver `SearchService.search`) y
    declara que `MediaKind` puede llegar a devolver `search`. Se usa para
    saltear del todo un provider que no puede producir lo que se esta
    buscando *antes* de llamarlo (evita, por ejemplo, disparar el Cloudflare
    Turnstile de zona-leros al buscar solo libros). Un provider sin `kinds`
    declarado se sigue consultando siempre, como antes de que existiera este
    atributo.

    Las capacidades especificas de series (temporadas, episodios) no forman
    parte de este contrato base porque no todo provider las tiene — ver
    `SeriesProvider`.

    Attributes:
        name: Nombre unico con el que el provider se registra (ver
            `providers/registry.py`).
        kinds: `MediaKind`s que este provider puede llegar a devolver.
    """

    name: str
    kinds: set[MediaKind]

    def search(self, query: str) -> list[Media]:
        """Busca `query` en el repo y devuelve los resultados livianos encontrados.

        Args:
            query: Texto de busqueda tal como lo escribio el usuario.

        Returns:
            Resultados encontrados (sin necesariamente todos los datos que
            trae `get_media` para un resultado puntual).
        """
        ...

    def get_media(self, media_id: str) -> Media:
        """Resuelve la ficha completa de `media_id`.

        Args:
            media_id: Id devuelto por `search` (o pasado a mano por el
                usuario en `cli/download.py`).

        Returns:
            El `Media` con todos los datos disponibles en su ficha.
        """
        ...

    def get_download_options(self, media: Media) -> list[DownloadOption]:
        """Lista las variantes descargables de `media`.

        Args:
            media: Media ya resuelto via `get_media`.

        Returns:
            Opciones de descarga disponibles. Vacio si `media` no tiene
            descargas propias (ej. la ficha de una serie en zona-leros).
        """
        ...


@runtime_checkable
class SeriesProvider(SourceProvider, Protocol):
    """Extension de `SourceProvider` para sources que ademas manejan series.

    Se comprueba con `isinstance(provider, SeriesProvider)` (Protocol
    `runtime_checkable`) en vez de `getattr` con nombres de metodo sueltos,
    para que el chequeo de capacidad quede tipado en un solo lugar en vez de
    repetido por call site.
    """

    def get_seasons(self, series: Media) -> list[int]:
        """Temporadas de `series` que tienen un pack completo para descargar de una.

        Args:
            series: Media de la ficha de la serie (`get_media`/`search`).

        Returns:
            Numeros de temporada con pack. Vacio si ninguna temporada
            ofrece uno.
        """
        ...

    def get_episodes(self, series: Media) -> list[Media]:
        """Lista todos los episodios de `series`, de todas las temporadas.

        Args:
            series: Media de la ficha de la serie (`get_media`/`search`).

        Returns:
            Un `Media` por episodio.
        """
        ...

    def get_season_download_options(self, series: Media, season: int) -> list[DownloadOption]:
        """Opciones de descarga del pack completo de `season`.

        Args:
            series: Media de la ficha de la serie (`get_media`/`search`).
            season: Numero de temporada (debe venir de `get_seasons`).

        Returns:
            Opciones de descarga del pack de esa temporada.
        """
        ...


@runtime_checkable
class AuthorProvider(SourceProvider, Protocol):
    """Extension de `SourceProvider` para sources con una pagina propia por autor.

    Separado del contrato base (ISP): solo tiene sentido en sources que
    exponen esa taxonomia (hoy lectulandia). Se comprueba con
    `isinstance(provider, AuthorProvider)`, igual que `SeriesProvider`.
    """

    def search_authors(self, query: str) -> list[Author]:
        """Autores cuyo nombre coincide con `query`, segun el propio sitio.

        Args:
            query: Texto de busqueda tal como lo escribio el usuario.

        Returns:
            Autores emparejados. Vacio si ninguno coincide.
        """
        ...

    def get_author_books(self, author: Author) -> list[Media]:
        """Todos los libros listados en la pagina de `author`.

        Args:
            author: Autor devuelto por `search_authors`.

        Returns:
            Libros del autor, solo los de su pagina (sin resultados por
            texto ni series).
        """
        ...


@runtime_checkable
class SubtitleProvider(Protocol):
    """Contrato de un repo de subtitulos (hoy subdivx).

    Separado de `SourceProvider` (ISP): un repo de subtitulos no tiene fichas
    de peliculas ni opciones de descarga de video, solo candidatos de
    subtitulo para una busqueda y el archivo (zip/rar/srt) de cada uno.

    Attributes:
        name: Nombre unico del provider.
    """

    name: str

    def search(self, query: str) -> list[SubtitleCandidate]:
        """Busca subtitulos para `query` (ej. "House M.D. S01E01" o "Allied (2016)").

        Args:
            query: Texto de busqueda.

        Returns:
            Candidatos tal como los lista el repo, sin filtrar por titulo:
            la busqueda suele ser por substring y trae otras series/peliculas
            (filtrarlas es responsabilidad de `core/subtitles/matching.py`).
        """
        ...

    def download(self, candidate: SubtitleCandidate) -> DownloadedFile:
        """Descarga el archivo de `candidate`, tal cual lo sirve el repo.

        Args:
            candidate: Candidato devuelto por `search`.

        Returns:
            El archivo (normalmente un .zip o .rar con uno o mas .srt).
        """
        ...


@runtime_checkable
class Downloader(Protocol):
    """Contrato para descargar un archivo a disco, con progreso opcional."""

    def download(self, url: str, dest_path: Path, on_progress: ProgressCallback | None = None) -> None:
        """Descarga `url` a `dest_path`, en streaming.

        Args:
            url: Link directo al archivo.
            dest_path: Ruta destino en disco.
            on_progress: Callback `(bytes_descargados, bytes_totales)`
                invocado a medida que llegan datos. `bytes_totales` puede
                ser 0 si el servidor no informo `Content-Length`.

        Raises:
            DownloadError: Si la descarga falla (link caido, conexion
                cortada, etc.).
        """
        ...
