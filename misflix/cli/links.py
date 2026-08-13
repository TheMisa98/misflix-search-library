from pathlib import Path

import typer

from misflix.cli.download import ExtractionError, download_and_extract_episode, part_progress_factory, run_with_retry
from misflix.config.settings import get_settings
from misflix.core.models import Media, MediaKind
from misflix.core.services.download_service import DownloadService, sanitize_filename, season_folder_name
from misflix.ui import prompts

app = typer.Typer(help="Insertar links ya resueltos de antes (ej. mediafire) y dejar que Misflix los organice.")

_MANUAL_SOURCE = "manual"

_INSERT_INTRO = (
    "Pega el/los link(s) que ya tenias guardados.\n"
    "[dim]Podes pegar todos juntos, no hace falta uno por uno ni en orden — "
    "se detectan y ordenan solos por el numero de parte (partN) del archivo.[/dim]"
)


def _make_media(title: str, kind: MediaKind) -> Media:
    """Arma un `Media` sintetico para un item insertado a mano (sin provider real).

    Args:
        title: Titulo tal como lo escribio el usuario.
        kind: Tipo de contenido.

    Returns:
        Un `Media` valido para pasarle a `DownloadService` (id/source/page_url
        no importan aca — nada los vuelve a resolver contra un provider).
    """
    return Media(id="manual", title=title, kind=kind, source=_MANUAL_SOURCE, page_url="")


@app.command("movies")
def movies() -> None:
    """Inserta links ya resueltos de una pelicula o serie."""
    if typer.confirm("Es una serie? (No = pelicula)", default=False):
        _insert_series()
    else:
        _insert_movie()


@app.command("books")
def books() -> None:
    """Inserta el link ya resuelto de un libro."""
    _insert_book()


def _insert_movie() -> None:
    """Pide titulo + links de una pelicula y la baja/extrae/organiza.

    Mismo camino que usa `run_download_flow` para una pelicula resuelta via
    ad-locker (`download_parts` + `extract_and_organize`, video dentro de su
    propia subcarpeta `Titulo (anio)/`), salteando el paso de abrir el
    navegador porque el link ya lo tiene el usuario.
    """
    title = typer.prompt("Titulo de la pelicula").strip()
    media = _make_media(title, MediaKind.MOVIE)

    urls = prompts.collect_direct_links(intro=_INSERT_INTRO)
    if not urls:
        return

    settings = get_settings()
    service = DownloadService()
    with prompts.console.status("[cyan]Resolviendo nombre de carpeta...[/cyan]"):
        folder_name = service.resolve_folder_name(media)

    def try_download_parts() -> list[Path]:
        with prompts.progress_bar() as progress:
            return service.download_parts(
                media,
                urls,
                settings.movies_dir,
                folder_name=folder_name,
                progress_factory=part_progress_factory(progress),
            )

    saved_paths = run_with_retry(try_download_parts, description=title)
    if saved_paths is None:
        return
    for path in saved_paths:
        typer.echo(f"Guardado en {path}")

    with prompts.console.status("[cyan]Extrayendo con unrar...[/cyan]"):
        try:
            video_path = service.extract_and_organize(folder_name, settings.movies_dir)
        except ExtractionError as exc:
            prompts.console.print(
                f"[red]No se pudo extraer automaticamente ({exc}).[/red]\n"
                f'[dim]Podes hacerlo a mano: cd "{settings.movies_dir / folder_name}" && '
                f"unrar x -pzonaleros *.rar[/dim]"
            )
            return

    if video_path:
        prompts.console.print(f"[green]Listo:[/green] {video_path}")


def _insert_series() -> None:
    """Pide titulo/temporada de una serie y deriva a pack completo o episodio suelto."""
    series_title = typer.prompt("Titulo de la serie").strip()
    season = typer.prompt("Numero de temporada", type=int)

    if typer.confirm("Es un pack de temporada completa (un link por episodio)?", default=True):
        _insert_season_pack(series_title, season)
    else:
        episode = typer.prompt("Numero de episodio", type=int)
        _insert_single_episode(series_title, season, episode)


def _insert_season_pack(series_title: str, season: int) -> None:
    """Pide un link por episodio (en orden) y baja/extrae la temporada entera.

    Reusa `download_and_extract_episode` (`cli/download.py`) por episodio,
    igual que `_download_season_packs_batch` — cada link es el .rar completo
    de un episodio, no una parte de un archivo mas grande.

    Args:
        series_title: Titulo de la serie.
        season: Numero de temporada.
    """
    start_episode = typer.prompt("Numero del primer episodio pegado", type=int, default=1)
    urls = prompts.collect_direct_links(
        intro=(
            f"Pega un link por episodio, en el orden en que van "
            f"(el primero es el episodio {start_episode}).\n"
            "[dim]No hace falta que traigan el numero de episodio en el nombre — "
            "se numeran en el orden en que los pegues.[/dim]"
        )
    )
    if not urls:
        return

    settings = get_settings()
    service = DownloadService()
    season_dir = settings.series_dir / sanitize_filename(series_title) / season_folder_name(season)
    media = _make_media(f"{series_title} - Temporada {season}", MediaKind.SERIES)

    for index, url in enumerate(urls, start=start_episode):
        stem = sanitize_filename(f"{series_title} - S{season:02d}E{index:02d}")
        download_and_extract_episode(service, media, [url], season_dir, stem)


def _insert_single_episode(series_title: str, season: int, episode: int) -> None:
    """Pide uno o mas links (partes) de un unico episodio y lo baja/extrae.

    Args:
        series_title: Titulo de la serie.
        season: Numero de temporada.
        episode: Numero de episodio.
    """
    urls = prompts.collect_direct_links(intro=_INSERT_INTRO)
    if not urls:
        return

    settings = get_settings()
    service = DownloadService()
    season_dir = settings.series_dir / sanitize_filename(series_title) / season_folder_name(season)
    stem = sanitize_filename(f"{series_title} - S{season:02d}E{episode:02d}")
    media = _make_media(f"{series_title} S{season:02d}E{episode:02d}", MediaKind.SERIES)

    download_and_extract_episode(service, media, urls, season_dir, stem)


def _insert_book() -> None:
    """Pide titulo + link de un libro y lo baja (sin extraer, un ebook no viene rarrado).

    Usa `download_parts` (no `DownloadService.download` directo) para
    heredar la resolucion de paginas de Mediafire a su link directo — a
    diferencia de pelicula/serie, no hace falta una subcarpeta por libro
    (los bajados via lectulandia tampoco la tienen), asi que `folder_name`
    se fuerza al titulo sanitizado en vez de dejar que `download_parts`
    intente resolverlo via IMDb (no tiene sentido para un libro).
    """
    title = typer.prompt("Titulo del libro").strip()
    urls = prompts.collect_direct_links(intro=_INSERT_INTRO)
    if not urls:
        return
    if len(urls) > 1:
        prompts.console.print("[yellow]Un libro no se parte en partes, se usa solo el primer link.[/yellow]")
        urls = urls[:1]

    settings = get_settings()
    service = DownloadService()
    media = _make_media(title, MediaKind.BOOK)
    folder_name = sanitize_filename(title)

    def try_download_parts() -> list[Path]:
        with prompts.progress_bar() as progress:
            return service.download_parts(
                media,
                urls,
                settings.books_dir,
                folder_name=folder_name,
                progress_factory=part_progress_factory(progress),
            )

    saved_paths = run_with_retry(try_download_parts, description=title)
    if saved_paths:
        prompts.console.print(f"[green]Guardado en {saved_paths[0]}[/green]")
