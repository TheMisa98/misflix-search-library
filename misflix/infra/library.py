"""Recorrer la biblioteca en disco: que videos hay, que son y que subtitulos tienen al lado.

La biblioteca sigue la convencion Plex/Kodi (ver `docs/ARCHITECTURE.md`):
`<movies_dir>/<Titulo (Año)>/<video>` y
`<series_dir>/<Serie>/Season NN/<video>`, con los `movie.nfo`/`tvshow.nfo`
que escriben Jellyfin/Radarr/Sonarr — de ahi salen el titulo original, el
traducido, el año y el id de IMDb (verificado en vivo: el `tvshow.nfo` de
"House" dice title="Dr. House", originaltitle="House", imdb tt0412142; y en
subdivx la serie figura como "House M.D.").
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ElementTree
from dataclasses import dataclass
from pathlib import Path

from misflix.core.subtitles.matching import parse_episode_range

VIDEO_EXTENSIONS = frozenset({".mkv", ".mp4", ".avi", ".m4v", ".mov", ".wmv", ".ts"})
# "sample" de los releases y clips de extras: no son el video real.
_MIN_VIDEO_BYTES = 50 * 1024 * 1024
_IGNORED_DIRS = frozenset({"extras", "featurettes", "samples", "sample", "behind the scenes", "bitorrent"})
_FOLDER_YEAR_RE = re.compile(r"^(?P<title>.*?)\s*\((?P<year>\d{4})\)\s*$")
_IMDB_RE = re.compile(r"\btt\d{6,10}\b")
_SUBTITLE_LANGUAGE_TAGS = {
    "es": "es", "spa": "es", "esp": "es", "es-419": "es", "es-es": "es", "es-mx": "es", "lat": "es", "latino": "es",
    "castellano": "es", "spanish": "es", "en": "en", "eng": "en", "english": "en", "pt": "pt", "por": "pt",
    "pt-br": "pt", "fr": "fr", "fre": "fr", "fra": "fr", "it": "it", "ita": "it", "de": "de", "ger": "de",
}  # fmt: skip


@dataclass(frozen=True)
class NfoInfo:
    """Datos de un `movie.nfo`/`tvshow.nfo`.

    Attributes:
        titles: Titulo original primero, despues el localizado (sin repetir).
        year: Año, si figura.
        imdb_id: Id de IMDb, si figura.
    """

    titles: tuple[str, ...]
    year: int | None
    imdb_id: str | None


@dataclass(frozen=True)
class LibraryVideo:
    """Un video de la biblioteca, identificado.

    Attributes:
        path: Ruta del archivo.
        kind: "movie" o "episode".
        titles: Titulos con los que buscarlo (original primero).
        year: Año, si se conoce.
        imdb_id: Id de IMDb, si se conoce (en series: el de la serie).
        season: Temporada (solo episodios).
        episode: Episodio (solo episodios).
    """

    path: Path
    kind: str
    titles: tuple[str, ...]
    year: int | None = None
    imdb_id: str | None = None
    season: int | None = None
    episode: int | None = None


@dataclass(frozen=True)
class SidecarSubtitle:
    """Un subtitulo externo junto a un video (`<video>.<tags>.srt`).

    Attributes:
        path: Ruta del .srt.
        language: Idioma declarado en el nombre (`es`, `en`, ...) o None si
            el nombre no lo dice.
    """

    path: Path
    language: str | None


def scan_library(movies_dir: Path, series_dir: Path) -> list[LibraryVideo]:
    """Recorre peliculas y series y devuelve cada video identificado.

    Un episodio sin codigo de episodio reconocible en el nombre (ej. un
    "capitulo especial" suelto) se omite: no hay forma de buscarle
    subtitulos.

    Args:
        movies_dir: Carpeta de peliculas.
        series_dir: Carpeta de series.

    Returns:
        Los videos encontrados, ordenados por ruta.
    """
    videos: list[LibraryVideo] = []
    nfo_cache: dict[Path, NfoInfo | None] = {}
    for path in _video_files(movies_dir):
        videos.append(_identify_movie(path, nfo_cache))
    for path in _video_files(series_dir):
        video = _identify_episode(path, series_dir, nfo_cache)
        if video is not None:
            videos.append(video)
    return sorted(videos, key=lambda video: str(video.path))


def sidecar_subtitles(video: Path) -> list[SidecarSubtitle]:
    """Lista los .srt externos de `video` (`<stem>.srt`, `<stem>.es.srt`, `<stem>.hi.srt`...).

    Args:
        video: Archivo de video.

    Returns:
        Los subtitulos encontrados, ordenados por nombre.
    """
    stem = video.stem
    found = []
    try:
        siblings = sorted(video.parent.iterdir())
    except OSError:
        return []
    for path in siblings:
        if path.suffix.lower() != ".srt" or not path.name.startswith(stem + "."):
            continue
        tags = [tag.lower() for tag in path.name[len(stem) + 1 : -len(".srt")].split(".") if tag]
        language = next((_SUBTITLE_LANGUAGE_TAGS[tag] for tag in tags if tag in _SUBTITLE_LANGUAGE_TAGS), None)
        found.append(SidecarSubtitle(path=path, language=language))
    return found


def read_nfo(path: Path) -> NfoInfo | None:
    """Lee titulo(s), año e IMDb de un .nfo de Kodi/Jellyfin.

    Tolera .nfo que no son XML valido (algunos traen una URL suelta al
    final): si el parseo falla, cae a expresiones regulares.

    Args:
        path: Archivo .nfo.

    Returns:
        Los datos, o None si el archivo no existe o no se puede leer.
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    values: dict[str, str] = {}
    try:
        root = ElementTree.fromstring(text[: text.rfind(">") + 1])
        for tag in ("title", "originaltitle", "year", "imdbid", "imdb_id", "id"):
            element = root.find(tag)
            if element is not None and element.text:
                values[tag] = element.text.strip()
        for uniqueid in root.findall("uniqueid"):
            if uniqueid.get("type") == "imdb" and uniqueid.text:
                values["imdb_id"] = uniqueid.text.strip()
    except ElementTree.ParseError:
        for tag in ("title", "originaltitle", "year"):
            match = re.search(rf"<{tag}>([^<]+)</{tag}>", text)
            if match:
                values[tag] = match.group(1).strip()
    imdb = next(
        (m.group(0) for key in ("imdb_id", "imdbid", "id") if (m := _IMDB_RE.search(values.get(key, "")))), None
    )
    if imdb is None and (match := _IMDB_RE.search(text)):
        imdb = match.group(0)
    titles = tuple(dict.fromkeys(t for t in (values.get("originaltitle"), values.get("title")) if t))
    year = values.get("year", "")
    return NfoInfo(titles=titles, year=int(year) if year.isdigit() else None, imdb_id=imdb)


def _video_files(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    files = []
    for path in root.rglob("*"):
        if path.suffix.lower() not in VIDEO_EXTENSIONS:
            continue
        if any(part.lower() in _IGNORED_DIRS for part in path.relative_to(root).parts[:-1]):
            continue
        try:
            if path.is_file() and path.stat().st_size >= _MIN_VIDEO_BYTES:
                files.append(path)
        except OSError:
            continue
    return files


def _identify_movie(path: Path, nfo_cache: dict[Path, NfoInfo | None]) -> LibraryVideo:
    folder = path.parent
    match = _FOLDER_YEAR_RE.match(folder.name.strip())
    folder_title = match["title"] if match else folder.name.strip()
    folder_year = int(match["year"]) if match else None
    nfo = _cached_nfo(folder / "movie.nfo", nfo_cache) or _cached_nfo(path.with_suffix(".nfo"), nfo_cache)
    titles = tuple(dict.fromkeys([*(nfo.titles if nfo else ()), folder_title]))
    return LibraryVideo(
        path=path,
        kind="movie",
        titles=titles,
        year=(nfo.year if nfo and nfo.year else folder_year),
        imdb_id=nfo.imdb_id if nfo else None,
    )


def _identify_episode(path: Path, series_dir: Path, nfo_cache: dict[Path, NfoInfo | None]) -> LibraryVideo | None:
    episodes = parse_episode_range(path.name)
    if episodes is None:
        return None
    show_dir = series_dir / path.relative_to(series_dir).parts[0]
    nfo = _cached_nfo(show_dir / "tvshow.nfo", nfo_cache)
    folder_title = show_dir.name.strip().rstrip(".")
    match = _FOLDER_YEAR_RE.match(folder_title)
    if match:
        folder_title = match["title"]
    titles = tuple(dict.fromkeys([*(nfo.titles if nfo else ()), folder_title]))
    return LibraryVideo(
        path=path,
        kind="episode",
        titles=titles,
        year=nfo.year if nfo else None,
        imdb_id=nfo.imdb_id if nfo else None,
        season=episodes.season,
        episode=episodes.first,
    )


def _cached_nfo(path: Path, cache: dict[Path, NfoInfo | None]) -> NfoInfo | None:
    if path not in cache:
        cache[path] = read_nfo(path) if path.is_file() else None
    return cache[path]
