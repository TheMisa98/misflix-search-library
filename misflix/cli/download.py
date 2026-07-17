import re
from pathlib import Path
from typing import Callable, TypeVar

import typer

from misflix.config.settings import get_settings
from misflix.core.models import DownloadOption, Media, MediaKind
from misflix.core.ports import SourceProvider
from misflix.core.services.download_service import (
    DownloadService,
    group_episodes_by_season,
    parse_episode_code,
    season_folder_name,
)
from misflix.infra.antupload import AntuploadResolveError
from misflix.infra.archives import ExtractionError
from misflix.infra.downloader import DownloadError
from misflix.infra.filesystem import sanitize_filename
from misflix.infra.mediafire import MediaFireResolveError, is_mediafire_url
from misflix.providers.registry import get_provider
from misflix.ui import prompts
from misflix.ui.image_render import FetchBytes

app = typer.Typer(help="Descargar un resultado previamente encontrado.")

# Un link caido (Mediafire/antupload lo dio de baja, la pagina no carga, se corta
# a mitad de descarga) no deberia tumbar todo el proceso — se atrapan estos tres
# juntos en cada punto donde se baja un archivo, para poder saltear ese item y
# seguir con el resto (o, para una pelicula/libro suelto, avisar y cortar prolijo).
_LINK_ERRORS = (MediaFireResolveError, DownloadError, AntuploadResolveError)

_T = TypeVar("_T")


def _run_with_retry(action: Callable[[], _T], description: str, progress=None) -> _T | None:
    """Ejecuta `action` (una descarga que puede fallar con uno de `_LINK_ERRORS` —
    en la practica, casi siempre un timeout de red a mitad de una descarga de
    varios GB, no necesariamente un link caido) y, si falla, pregunta si
    reintentar antes de rendirse. Sin esto, despues de un timeout el flujo volvia
    directo al prompt de busqueda sin ofrecer nada mas — confuso, porque un
    numero ahi se interpreta como resultado de busqueda, no como "reintentar la
    opcion 4" (se vio pasar en vivo: eligio "4" pensando en la opcion de
    descarga y termino disparando una busqueda nueva por "4"). Reintentar reusa
    la misma opcion/urls ya resueltas, asi que no hace falta volver a abrir el
    navegador ni pegar los mismos links de nuevo.

    Si `progress` es una barra compartida con otros items del mismo batch (ver
    `_download_season_packs_batch`), se pausa mientras se pregunta: el refresco
    en vivo de rich compite por la terminal con el prompt si se lo deja andando.
    Cuando el intento arma su propia barra (los demas casos), no hace falta
    pasar `progress` — el `with` ya se cierra solo al propagarse la excepcion,
    antes de llegar aca. None si el usuario decide no reintentar mas."""
    while True:
        try:
            return action()
        except _LINK_ERRORS as exc:
            if progress is not None:
                progress.stop()
            prompts.console.print(f"[red]No se pudo descargar {description} ({exc}).[/red]")
            retry = typer.confirm("Reintentar la descarga?", default=True)
            if progress is not None:
                progress.start()
            if not retry:
                return None


def _try_resolve_without_browser(provider: SourceProvider, option: DownloadOption) -> list[str] | None:
    """El `option.url` de zona-leros SIEMPRE es del ad-locker (`anomizador.zona-
    leros.com`), nunca un link directo — pero no todos exigen un Turnstile: los de
    algunos episodios resultan ser solo una cadena de redirects HTTP hasta
    Mediafire, sin ningun desafio real de por medio (verificado en vivo), mientras
    que otros (peliculas, y otros episodios) si lo piden. Por eso esto prueba un
    GET directo antes de comprometerse al paso manual — `CloudflareHttpClient.
    try_get` no escala a abrir el navegador si sale desafiado, asi que esto nunca
    dispara el paso manual por su cuenta — y si el resultado termina en Mediafire,
    se puede resolver y bajar todo sin que el usuario haga nada. None si no se
    pudo (ahi si hace falta el paso manual de siempre)."""
    http = getattr(provider, "http", None)
    try_get = getattr(http, "try_get", None)
    if try_get is None:
        return None

    response = try_get(option.url)
    if response is None:
        return None

    final_url = str(response.url)
    return [final_url] if is_mediafire_url(final_url) else None


_URL_EPISODE_CODE_RE = re.compile(r"[Ss](\d{1,2})[Ee](\d{1,2})")


def _parse_code_from_url(url: str) -> tuple[int, int] | None:
    """(temporada, episodio) si la url del archivo trae un codigo SxE reconocible
    (ej. ".../RCKYMRTS01E01_ZL.rar/file" -> (1, 1)) — asi vienen nombrados los
    packs de temporada de zona-leros, donde cada url es el .rar completo de un
    episodio, no un volumen mas de un archivo mas grande."""
    match = _URL_EPISODE_CODE_RE.search(url)
    return (int(match.group(1)), int(match.group(2))) if match else None


def _extract_and_flatten(service: DownloadService, stem: str, dest_dir: Path) -> Path | None:
    """Extrae el .rar bajado en `dest_dir/<stem>`, si habia alguno (Mediafire a
    veces sirve el video ya sin comprimir — `extract_and_organize` reconoce ese
    caso tambien), y deja el video resultante en la raiz de `dest_dir` (no en su
    propia subcarpeta): `extract_and_organize` nombra el video igual que la
    carpeta que lo contiene, asi que esto lo mueve un nivel hacia arriba y borra
    la subcarpeta ya vacia. None (con lo que se haya bajado intacto, para revisar
    a mano) si no se encontro ningun video reconocible."""
    try:
        video_path = service.extract_and_organize(stem, dest_dir)
    except ExtractionError as exc:
        prompts.console.print(
            f"[red]No se pudo extraer {stem} ({exc}).[/red]\n"
            f"[dim]Podes hacerlo a mano: cd \"{dest_dir / stem}\" && unrar x -pzonaleros *.rar[/dim]"
        )
        return None

    if not video_path:
        prompts.console.print(f"[yellow]{stem}: no se encontro un video reconocible.[/yellow]")
        return None

    final_path = dest_dir / video_path.name
    if video_path != final_path:
        video_path.rename(final_path)
    try:
        video_path.parent.rmdir()
    except OSError:
        pass
    return final_path


def run_download_flow(provider: SourceProvider, media: Media) -> None:
    """Flujo completo de descarga para un media ya resuelto: portada, elegir opcion,
    y bajar (streaming directo, o abrir en el navegador + pegar links + progreso).
    Una serie no tiene descargas propias (`get_download_options` viene vacio), asi
    que en ese caso `_download_series` se hace cargo de elegir que bajar (pack de
    temporada, episodio suelto, o en lote) y de todo el resto por su cuenta; lo que
    sigue en esta funcion es entonces solo el camino de pelicula/libro."""
    http = getattr(provider, "http", None)
    fetch_bytes = (lambda url: http.get(url).content) if http else None
    prompts.show_cover(media, fetch_bytes=fetch_bytes)

    options = provider.get_download_options(media)
    if not options:
        _download_series(provider, media, fetch_bytes)
        return

    option = prompts.choose_option(options)

    settings = get_settings()
    if media.kind == MediaKind.MOVIE:
        dest_dir = settings.movies_dir
    elif media.kind == MediaKind.SERIES:
        dest_dir = settings.series_dir
    else:
        dest_dir = settings.books_dir

    service = DownloadService()

    if option.opens_externally:
        urls = _try_resolve_without_browser(provider, option)
        if urls is None:
            if not prompts.confirm_open_externally(media, option):
                return
            service.download(media, option, dest_dir)

            urls = prompts.collect_direct_links()
            if not urls:
                return
        else:
            prompts.console.print("[dim]Link directo de Mediafire, se resuelve sin navegador.[/dim]")

        with prompts.console.status("[cyan]Resolviendo nombre de carpeta...[/cyan]"):
            folder_name = service.resolve_folder_name(media)

        def try_download_parts() -> list[Path]:
            with prompts.progress_bar() as progress:

                def progress_factory(index: int, total: int):
                    task_id = progress.add_task(f"Parte {index}/{total}", total=None)

                    def on_progress(downloaded: int, total_bytes: int) -> None:
                        progress.update(task_id, total=total_bytes or None, completed=downloaded)

                    return on_progress

                return service.download_parts(
                    media, urls, dest_dir, folder_name=folder_name, progress_factory=progress_factory
                )

        saved_paths = _run_with_retry(try_download_parts, description=media.title)
        if saved_paths is None:
            return

        for path in saved_paths:
            typer.echo(f"Guardado en {path}")

        with prompts.console.status("[cyan]Extrayendo con unrar...[/cyan]"):
            try:
                video_path = service.extract_and_organize(folder_name, dest_dir)
            except ExtractionError as exc:
                video_path = None
                extraction_error = exc
            else:
                extraction_error = None

        if extraction_error:
            prompts.console.print(
                f"[red]No se pudo extraer automaticamente ({extraction_error}).[/red]\n"
                f"[dim]Podes hacerlo a mano: cd \"{dest_dir / folder_name}\" && "
                f"unrar x -pzonaleros *.rar[/dim]"
            )
        elif video_path:
            prompts.console.print(f"[green]Listo:[/green] {video_path}")
        return

    if not prompts.confirm_download(media, option, dest_dir):
        return

    def try_download() -> Path | None:
        with prompts.progress_bar() as progress:
            task_id = progress.add_task(media.title, total=None)

            def on_progress(downloaded: int, total_bytes: int) -> None:
                progress.update(task_id, total=total_bytes or None, completed=downloaded)

            return service.download(media, option, dest_dir, on_progress=on_progress)

    dest_path = _run_with_retry(try_download, description=media.title)
    if dest_path:
        prompts.console.print(f"[green]Guardado en {dest_path}[/green]")


def _download_series(provider: SourceProvider, series: Media, fetch_bytes: FetchBytes | None) -> None:
    """Una serie no tiene descargas propias, asi que primero hay que elegir que
    bajar — el pack de una temporada (o de todas las que tengan pack), un episodio
    suelto, o todos los episodios de una temporada / de la serie entera en lote
    (uno por uno, para series o sitios que no ofrecen ningun pack armado) — y
    despues bajarlo del todo aca mismo: a diferencia de una pelicula/libro, no hay
    una unica opcion que darle de vuelta a `run_download_flow`."""
    if series.kind != MediaKind.SERIES:
        typer.echo("No hay opciones de descarga disponibles para este resultado.")
        return

    get_episodes = getattr(provider, "get_episodes", None)
    if get_episodes is None:
        typer.echo("No hay opciones de descarga disponibles para este resultado.")
        return

    get_seasons = getattr(provider, "get_seasons", None)
    seasons_with_packs = get_seasons(series) if get_seasons else []
    mode = prompts.choose_series_mode(seasons_with_packs)

    if mode == "season_pack":
        season_number = prompts.choose_season(seasons_with_packs)
        _download_season_packs_batch(provider, series, [season_number])
        return

    if mode == "all_season_packs":
        _download_season_packs_batch(provider, series, seasons_with_packs)
        return

    episodes = get_episodes(series)
    if not episodes:
        typer.echo("No se encontraron episodios para esta serie.")
        return

    if mode == "episode":
        episode_stub = prompts.choose_episode(episodes)
        _download_episodes_batch(provider, series, [episode_stub], fetch_bytes)
        return

    if mode == "season_batch":
        groups = group_episodes_by_season(episodes)
        season_number = prompts.choose_season(sorted(groups))
        _download_episodes_batch(provider, series, groups[season_number], fetch_bytes)
        return

    _download_episodes_batch(provider, series, episodes, fetch_bytes)


def _download_season_packs_batch(provider: SourceProvider, series: Media, seasons: list[int]) -> None:
    """Descarga uno o mas packs de temporada, uno atras del otro sin volver a
    preguntar nada salvo, cuando hay mas de uno, si arrancar. El ad-locker exige un
    Turnstile fresco por pagina, asi que el paso manual (abrir navegador, pegar
    links) sigue siendo por temporada — pero la pagina resultante lista un .rar
    independiente por episodio, no un unico archivo partido en volumenes, asi que
    cada uno se baja y se extrae por separado (ver el comentario mas abajo) y
    terminan en `<series_dir>/<Serie>/Season NN/<Serie> - SxxEyy.ext` — el mismo
    lugar donde caeria un episodio bajado suelto."""
    if len(seasons) > 1 and not prompts.confirm_batch_download(len(seasons), unit="temporadas"):
        return

    settings = get_settings()
    service = DownloadService()
    series_dir = settings.series_dir / sanitize_filename(series.title)
    preferred_host: str | None = None

    for index, season_number in enumerate(seasons, start=1):
        season_media = Media(
            id=f"{series.id}:season-{season_number}",
            title=f"{series.title} - Temporada {season_number}",
            kind=MediaKind.SERIES,
            source=series.source,
            page_url=series.page_url,
        )
        if len(seasons) > 1:
            prompts.console.rule(f"Temporada {index}/{len(seasons)}: {season_media.title}")

        options = provider.get_season_download_options(series, season_number)
        if not options:
            prompts.console.print("[yellow]Sin opciones de descarga, se salta.[/yellow]")
            continue

        option = None
        if preferred_host:
            option = next((o for o in options if prompts.option_host(o) == preferred_host), None)
        if option is None:
            option = prompts.choose_option(options)
        preferred_host = prompts.option_host(option)

        folder_name = season_folder_name(season_number)
        season_dir = series_dir / folder_name

        if not option.opens_externally:

            def try_download(_option=option) -> Path | None:
                with prompts.progress_bar() as progress:
                    task_id = progress.add_task(folder_name, total=None)

                    def on_progress(downloaded: int, total_bytes: int) -> None:
                        progress.update(task_id, total=total_bytes or None, completed=downloaded)

                    return service.download(
                        season_media, _option, series_dir, filename_stem=folder_name, on_progress=on_progress
                    )

            dest_path = _run_with_retry(try_download, description=f"la temporada {season_number}")
            if dest_path is None:
                prompts.console.print(f"[yellow]Temporada {season_number}: se salta.[/yellow]")
                continue
            prompts.console.print(f"[green]Guardado en {dest_path}[/green]")
            continue

        urls = _try_resolve_without_browser(provider, option)
        if urls is None:
            service.download(season_media, option, series_dir)

            urls = prompts.collect_direct_links()
            if not urls:
                continue
        else:
            prompts.console.print("[dim]Link directo de Mediafire, se resuelve sin navegador.[/dim]")

        # Un pack de temporada en zona-leros no es un unico archivo partido en
        # volumenes (a diferencia de una pelicula con >1 link): la pagina del
        # ad-locker lista un .rar independiente y completo por episodio. Tratarlas
        # como partes de un mismo unrar (como con una pelicula) hace que unrar
        # extraiga solo el primer episodio y de por exitosa la extraccion, asi que
        # el resto de los .rar se borran sin haberse extraido nunca — cada url se
        # baja y se extrae por separado en vez de eso.
        with prompts.progress_bar() as progress:
            for url_index, url in enumerate(urls, start=1):
                code = _parse_code_from_url(url)
                episode_number = code[1] if code else url_index
                stem = sanitize_filename(f"{series.title} - S{season_number:02d}E{episode_number:02d}")

                def try_download(_url=url, _url_index=url_index, _stem=stem) -> list[Path]:
                    task_id = progress.add_task(f"Episodio {_url_index}/{len(urls)}", total=None)

                    def on_progress(downloaded: int, total_bytes: int, _task_id=task_id) -> None:
                        progress.update(_task_id, total=total_bytes or None, completed=downloaded)

                    def single_progress_factory(_index: int, _total: int, _cb=on_progress):
                        return _cb

                    return service.download_parts(
                        season_media, [_url], season_dir, folder_name=_stem, progress_factory=single_progress_factory
                    )

                result = _run_with_retry(try_download, description=stem, progress=progress)
                if result is None:
                    prompts.console.print(f"[yellow]{stem}: se salta.[/yellow]")
                    continue

                final_path = _extract_and_flatten(service, stem, season_dir)
                if final_path:
                    prompts.console.print(f"[green]Listo:[/green] {final_path}")


def _download_episodes_batch(
    provider: SourceProvider, series: Media, episodes: list[Media], fetch_bytes: FetchBytes | None
) -> None:
    """Descarga uno o mas episodios, uno atras del otro, sin volver a preguntar el
    modo en cada uno (salvo, cuando hay mas de uno, confirmar una vez si arrancar).
    El ad-locker exige un Turnstile fresco por pagina, asi que el paso manual (abrir
    navegador, pegar links) no se puede evitar por episodio — esto automatiza todo
    lo demas y deja los videos organizados en
    `<series_dir>/<Serie>/Season NN/<Serie> - SxxEyy.ext`, reusando el preferido de
    servidor (MEGA/MEDIAFIRE) elegido en el primer episodio para el resto, cuando
    este disponible. Tambien es el camino que usa un episodio suelto elegido a mano
    (lote de uno), asi que un episodio siempre termina en el mismo lugar sin
    importar si se pidio uno solo o todos de una."""
    episodes = sorted(episodes, key=lambda e: parse_episode_code(e.title) or (0, 0))

    if len(episodes) > 1 and not prompts.confirm_batch_download(len(episodes)):
        return

    settings = get_settings()
    service = DownloadService()
    preferred_host: str | None = None

    for index, stub in enumerate(episodes, start=1):
        episode = provider.get_media(stub.id)
        if len(episodes) > 1:
            prompts.console.rule(f"Episodio {index}/{len(episodes)}: {episode.title}")
        prompts.show_cover(episode, fetch_bytes=fetch_bytes, landscape=True)

        options = provider.get_download_options(episode)
        if not options:
            prompts.console.print("[yellow]Sin opciones de descarga, se salta.[/yellow]")
            continue

        option = None
        if preferred_host:
            option = next((o for o in options if prompts.option_host(o) == preferred_host), None)
        if option is None:
            option = prompts.choose_option(options)
        preferred_host = prompts.option_host(option)

        stem = service.resolve_episode_stem(series.title, episode.title)
        season_dir = service.resolve_season_dir(series.title, episode.title, settings.series_dir)

        if not option.opens_externally:

            def try_download(_option=option) -> Path | None:
                with prompts.progress_bar() as progress:
                    task_id = progress.add_task(stem, total=None)

                    def on_progress(downloaded: int, total_bytes: int) -> None:
                        progress.update(task_id, total=total_bytes or None, completed=downloaded)

                    return service.download(episode, _option, season_dir, filename_stem=stem, on_progress=on_progress)

            dest_path = _run_with_retry(try_download, description=episode.title)
            if dest_path is None:
                prompts.console.print(f"[yellow]{episode.title}: se salta.[/yellow]")
                continue
            prompts.console.print(f"[green]Guardado en {dest_path}[/green]")
            continue

        urls = _try_resolve_without_browser(provider, option)
        if urls is None:
            service.download(episode, option, season_dir)

            urls = prompts.collect_direct_links()
            if not urls:
                continue
        else:
            prompts.console.print("[dim]Link directo de Mediafire, se resuelve sin navegador.[/dim]")

        def try_download_parts() -> list[Path]:
            with prompts.progress_bar() as progress:

                def progress_factory(part_index: int, total: int):
                    task_id = progress.add_task(f"Parte {part_index}/{total}", total=None)

                    def on_progress(downloaded: int, total_bytes: int) -> None:
                        progress.update(task_id, total=total_bytes or None, completed=downloaded)

                    return on_progress

                return service.download_parts(
                    episode, urls, season_dir, folder_name=stem, progress_factory=progress_factory
                )

        result = _run_with_retry(try_download_parts, description=episode.title)
        if result is None:
            prompts.console.print(f"[yellow]{episode.title}: se salta.[/yellow]")
            continue

        with prompts.console.status("[cyan]Extrayendo con unrar...[/cyan]"):
            final_path = _extract_and_flatten(service, stem, season_dir)

        if final_path:
            prompts.console.print(f"[green]Listo:[/green] {final_path}")


# Separa el comando por lo que el media resuelto realmente es (ver
# SearchService.search para la misma logica del lado de la busqueda), no por
# que provider lo trajo: zona-leros resuelve tanto MOVIE como SERIES, asi que
# "movies" cubre los dos.
_MOVIE_KINDS = {MediaKind.MOVIE, MediaKind.SERIES}
_BOOK_KINDS = {MediaKind.BOOK}


@app.command("movies")
def movies(source: str, media_id: str):
    """Descarga la pelicula o serie `media_id` desde el repo `source`, mostrando la
    portada y pidiendo confirmacion."""
    _download(source, media_id, kinds=_MOVIE_KINDS, kind_label="una pelicula o serie")


@app.command("books")
def books(source: str, media_id: str):
    """Descarga el libro `media_id` desde el repo `source`, mostrando la portada y
    pidiendo confirmacion."""
    _download(source, media_id, kinds=_BOOK_KINDS, kind_label="un libro")


def _download(source: str, media_id: str, kinds: set[MediaKind], kind_label: str) -> None:
    provider = get_provider(source)
    media = provider.get_media(media_id)
    if media.kind not in kinds:
        typer.echo(f"'{media_id}' en '{source}' es {media.kind.value}, no {kind_label}.")
        raise typer.Exit(code=1)
    run_download_flow(provider, media)
