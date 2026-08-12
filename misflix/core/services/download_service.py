from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path

from misflix.core.models import DownloadOption, Media, MediaKind
from misflix.core.ports import Downloader, ProgressCallback
from misflix.infra.antupload import AntuploadResolveError as AntuploadResolveError
from misflix.infra.antupload import download as download_from_antupload
from misflix.infra.antupload import is_antupload_url
from misflix.infra.archives import ExtractionError as ExtractionError
from misflix.infra.archives import delete_rar_parts, extract_rar, flatten_all_videos, flatten_video
from misflix.infra.browser_launch import open_in_browser
from misflix.infra.downloader import DownloadError as DownloadError
from misflix.infra.downloader import HttpxDownloader
from misflix.infra.filesystem import ensure_dir
from misflix.infra.filesystem import sanitize_filename as sanitize_filename
from misflix.infra.imdb import resolve_title
from misflix.infra.mediafire import MediaFireResolveError as MediaFireResolveError
from misflix.infra.mediafire import is_mediafire_url as is_mediafire_url
from misflix.infra.mediafire import resolve_direct_url

ProgressFactory = Callable[[int, int], ProgressCallback]

_EPISODE_CODE_RE = re.compile(r"(\d+)x(\d+)\s*$")

# Errores de un link caido/corte de red a mitad de descarga (antupload, mediafire o el
# downloader generico) - se atrapan juntos en cli/download.py alrededor de cada punto
# donde se baja un archivo, para poder saltear ese item y seguir con el resto en vez de
# tumbar todo el proceso. Re-exportados aca (junto con ExtractionError, is_mediafire_url
# y sanitize_filename arriba) para que cli/ no importe infra/ directamente en absoluto -
# ver "Regla rapida de dependencias" en docs/ARCHITECTURE.md.
LINK_ERRORS: tuple[type[Exception], ...] = (MediaFireResolveError, DownloadError, AntuploadResolveError)


def parse_episode_code(episode_title: str) -> tuple[int, int] | None:
    """Extrae el codigo de temporada/episodio de un titulo scrapeado.

    Args:
        episode_title: Titulo tal como lo trae el provider, ej. "Breaking Bad 5x1".

    Returns:
        `(temporada, episodio)`, o None si el titulo no trae ese codigo
        (algunos providers pueden no traerlo).
    """
    match = _EPISODE_CODE_RE.search(episode_title)
    return (int(match.group(1)), int(match.group(2))) if match else None


def group_episodes_by_season(episodes: list[Media]) -> dict[int, list[Media]]:
    """Agrupa episodios por temporada.

    Usa el codigo SxE del titulo de cada uno (ver `parse_episode_code`); los
    que no traen codigo reconocible caen todos juntos bajo la temporada 0.

    Args:
        episodes: Episodios a agrupar.

    Returns:
        Diccionario temporada -> lista de episodios de esa temporada.
    """
    groups: dict[int, list[Media]] = {}
    for episode in episodes:
        code = parse_episode_code(episode.title)
        season = code[0] if code else 0
        groups.setdefault(season, []).append(episode)
    return groups


def season_folder_name(season: int) -> str:
    """Nombre de carpeta `Season NN` para una temporada.

    Es el formato que esperan Plex/Kodi (y el resto de la biblioteca ya
    organizada a mano en disco) — en ingles y con 2 digitos, sin importar el
    idioma del titulo de la serie.

    Args:
        season: Numero de temporada.

    Returns:
        Nombre de carpeta, ej. "Season 01".
    """
    return f"Season {season:02d}"


class DownloadService:
    """Orquesta la descarga de un DownloadOption hacia el filesystem."""

    def __init__(self, downloader: Downloader | None = None):
        """Inicializa el servicio.

        Args:
            downloader: Implementacion de `Downloader` a usar para
                descargas simples. Por defecto, `HttpxDownloader`.
        """
        self._downloader = downloader or HttpxDownloader()

    def download(
        self,
        media: Media,
        option: DownloadOption,
        dest_dir: Path,
        filename_stem: str | None = None,
        on_progress: ProgressCallback | None = None,
    ) -> Path | None:
        """Descarga `option` a `dest_dir`, o la abre en el navegador si aplica.

        Args:
            media: Media al que pertenece la opcion (usado para el nombre
                de archivo por defecto).
            option: Opcion a descargar.
            dest_dir: Carpeta destino; se crea si no existe.
            filename_stem: Nombre de archivo (sin extension) a usar en vez
                del titulo de `media`.
            on_progress: Callback de progreso, ver `Downloader.download`.

        Returns:
            La ruta del archivo descargado, o None si `option.opens_externally`
            es True (en ese caso se abrio en el navegador y no hay nada que
            descargar todavia).
        """
        if option.opens_externally:
            open_in_browser(option.url)
            return None

        ensure_dir(dest_dir)
        stem = filename_stem or sanitize_filename(media.title)
        filename = f"{stem}{option.extension or ''}"
        dest_path = dest_dir / filename

        # Un link de antupload.com (Lectulandia, ver providers/lectulandia.py)
        # exige una cookie de sesion y un Referer encadenados en las mismas dos
        # requests (ver infra/antupload.py) — el downloader generico solo hace
        # una request suelta sin cookies, asi que no le alcanza.
        if is_antupload_url(option.url):
            download_from_antupload(option.url, dest_path, on_progress=on_progress)
        else:
            self._downloader.download(option.url, dest_path, on_progress=on_progress)
        return dest_path

    def resolve_episode_stem(self, series_title: str, episode_title: str) -> str:
        """Nombre de archivo Plex-style `Serie - SxxEyy` para un episodio suelto.

        Pensado para un episodio bajado fuera de un pack de temporada (donde
        el .rar ya trae sus propios nombres). Si el titulo scrapeado no trae
        un codigo SxE reconocible, cae al titulo del episodio tal cual.

        Args:
            series_title: Titulo de la serie.
            episode_title: Titulo scrapeado del episodio, ej. "Breaking Bad 5x1".

        Returns:
            Nombre de archivo sanitizado, sin extension.
        """
        code = parse_episode_code(episode_title)
        if code is None:
            return sanitize_filename(episode_title)
        season, episode_number = code
        return sanitize_filename(f"{series_title} - S{season:02d}E{episode_number:02d}")

    def resolve_season_dir(self, series_title: str, episode_title: str, base_dir: Path) -> Path:
        """Carpeta `Serie/Season NN` donde organizar un episodio bajado suelto.

        Se arma a partir del codigo SxE del titulo del episodio (ver
        `parse_episode_code`); sin codigo reconocible cae en la temporada 0,
        igual que `group_episodes_by_season`.

        Args:
            series_title: Titulo de la serie.
            episode_title: Titulo scrapeado del episodio.
            base_dir: Carpeta raiz de series (ej. `settings.series_dir`).

        Returns:
            Ruta `base_dir/Serie/Season NN`.
        """
        code = parse_episode_code(episode_title)
        season = code[0] if code else 0
        return base_dir / sanitize_filename(series_title) / season_folder_name(season)

    def resolve_folder_name(self, media: Media) -> str:
        """Nombre de carpeta estilo Plex `Titulo (anio)` para `media`.

        Los titulos scrapeados no siempre traen el anio (o vienen con basura
        tipo "online hd"), asi que primero se intenta el titulo original +
        anio via IMDb; si no hay match se cae al titulo/anio que ya tenia el
        Media. Para un episodio de serie (ej. "Breaking Bad 5x1") no tiene
        sentido buscarlo en IMDb como si fuera una pelicula, asi que ahi se
        usa el titulo tal cual.

        Args:
            media: Media a nombrar.

        Returns:
            Nombre de carpeta sanitizado.
        """
        if media.kind == MediaKind.SERIES:
            return sanitize_filename(media.title)

        resolved = resolve_title(media.title, year_hint=media.year)
        title, year = resolved if resolved else (media.title, media.year)
        name = f"{title} ({year})" if year else title
        return sanitize_filename(name)

    def download_parts(
        self,
        media: Media,
        urls: list[str],
        dest_dir: Path,
        folder_name: str | None = None,
        progress_factory: ProgressFactory | None = None,
    ) -> list[Path]:
        """Descarga `urls` una por una, en orden, en su propia carpeta.

        Resuelve paginas de Mediafire a su link directo antes de bajar cada
        parte — eso es un GET bloqueante por url, asi que `progress_factory`
        se llama antes de resolver, no despues: para un pack de varias
        partes/episodios eso deja al menos la tarea (con progreso
        indeterminado) visible mientras se resuelve cada Mediafire, en vez de
        una pausa muda entre partes.

        Args:
            media: Media al que pertenecen las partes.
            urls: Urls a descargar, en el orden en que deben quedar
                numeradas (parte 1, parte 2, ...).
            dest_dir: Carpeta raiz donde crear `<folder_name>`.
            folder_name: Nombre de carpeta a usar en vez de
                `resolve_folder_name(media)`.
            progress_factory: Fabrica `(indice, total) -> on_progress`
                llamada antes de resolver cada url.

        Returns:
            Rutas de los archivos descargados, en el mismo orden que `urls`.
        """
        title = folder_name or self.resolve_folder_name(media)
        movie_dir = ensure_dir(dest_dir / title)
        multi_part = len(urls) > 1

        saved_paths = []
        for index, url in enumerate(urls, start=1):
            on_progress = progress_factory(index, len(urls)) if progress_factory else None

            direct_url = resolve_direct_url(url) if is_mediafire_url(url) else url
            extension = Path(direct_url.split("?", 1)[0]).suffix
            suffix = f".part{index}" if multi_part else ""
            dest_path = movie_dir / f"{title}{suffix}{extension}"

            self._downloader.download(direct_url, dest_path, on_progress=on_progress)
            saved_paths.append(dest_path)
        return saved_paths

    def extract_and_organize(self, folder_name: str, dest_dir: Path) -> Path | None:
        """Extrae los .rar bajados en `dest_dir/<folder_name>` y organiza el video.

        Usa la contraseña `zonaleros`. Deja el video resultante en la raiz de
        esa carpeta, con el mismo nombre que la carpeta. Mediafire a veces
        sirve el video directo sin comprimir — se llama a `extract_rar` sin
        chequear su resultado: es un no-op si no encuentra ningun .rar, y en
        ese caso el archivo bajado ya es el video a organizar. Si el video
        quedo bien ubicado, borra los .rar originales (si habia).

        Args:
            folder_name: Carpeta (bajo `dest_dir`) donde estan los .rar.
            dest_dir: Carpeta raiz que contiene `folder_name`.

        Returns:
            La ruta del video organizado, o None si no se encontro ningun
            archivo de video reconocible (ni extraido ni suelto) — en ese
            caso los .rar que hubiera quedan intactos por las dudas.
        """
        movie_dir = dest_dir / folder_name
        extract_rar(movie_dir)

        video_path = flatten_video(movie_dir, folder_name)
        if video_path:
            delete_rar_parts(movie_dir)
        return video_path

    def extract_and_organize_season(self, folder_name: str, dest_dir: Path) -> list[Path]:
        """Como `extract_and_organize`, pero para un pack de una temporada completa.

        Conserva todos los videos encontrados (con sus nombres originales) en
        vez de quedarse solo con el mas grande.

        Args:
            folder_name: Carpeta (bajo `dest_dir`) donde estan los .rar.
            dest_dir: Carpeta raiz que contiene `folder_name`.

        Returns:
            Rutas de los videos organizados. Vacio si no se encontro ninguno.
        """
        movie_dir = dest_dir / folder_name
        extract_rar(movie_dir)

        video_paths = flatten_all_videos(movie_dir)
        if video_paths:
            delete_rar_parts(movie_dir)
        return video_paths
