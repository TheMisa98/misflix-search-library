import re
from collections.abc import Callable
from collections.abc import Set as AbstractSet
from pathlib import Path

import typer
from rich.progress import Progress

from misflix.config.settings import get_settings
from misflix.core.models import BOOK_KINDS, MOVIE_KINDS, DownloadOption, Media, MediaKind
from misflix.core.ports import ProgressCallback, SeriesProvider, SourceProvider
from misflix.core.services.download_service import (
    LINK_ERRORS,
    DownloadService,
    ExtractionError,
    ProgressFactory,
    group_episodes_by_season,
    is_mediafire_url,
    parse_episode_code,
    sanitize_filename,
    season_folder_name,
)
from misflix.providers.registry import get_provider
from misflix.ui import prompts
from misflix.ui.image_render import FetchBytes

app = typer.Typer(help="Descargar un resultado previamente encontrado.")


def run_with_retry[T](action: Callable[[], T], description: str, progress: Progress | None = None) -> T | None:
    """Ejecuta `action` y, si falla con un link caido, pregunta si reintentar.

    `action` es tipicamente una descarga que puede fallar con uno de
    `LINK_ERRORS` — en la practica, casi siempre un timeout de red a mitad
    de una descarga de varios GB, no necesariamente un link caido. Sin este
    reintento, despues de un timeout el flujo volvia directo al prompt de
    busqueda sin ofrecer nada mas — confuso, porque un numero ahi se
    interpreta como resultado de busqueda, no como "reintentar la opcion 4"
    (se vio pasar en vivo: eligio "4" pensando en la opcion de descarga y
    termino disparando una busqueda nueva por "4"). Reintentar reusa la
    misma opcion/urls ya resueltas, asi que no hace falta volver a abrir el
    navegador ni pegar los mismos links de nuevo.

    Args:
        action: Callable sin argumentos a ejecutar (y, si falla, reintentar).
        description: Texto para el mensaje de error si `action` falla.
        progress: Si `action` comparte una barra de progreso con otros items
            del mismo batch (ver `_download_season_packs_batch`), se pausa
            mientras se pregunta: el refresco en vivo de rich compite por la
            terminal con el prompt si se lo deja andando. Cuando `action`
            arma su propia barra, no hace falta pasarlo aca — el `with` ya
            se cierra solo al propagarse la excepcion, antes de llegar aca.

    Returns:
        El resultado de `action`, o None si el usuario decide no reintentar
        mas.
    """
    while True:
        try:
            return action()
        except LINK_ERRORS as exc:
            if progress is not None:
                progress.stop()
            prompts.console.print(f"[red]No se pudo descargar {description} ({exc}).[/red]")
            retry = typer.confirm("Reintentar la descarga?", default=True)
            if progress is not None:
                progress.start()
            if not retry:
                return None


def progress_callback(progress: Progress, description: str) -> ProgressCallback:
    """Crea una tarea en `progress` y devuelve el callback que la actualiza.

    Centraliza el trio "crear tarea, definir on_progress, devolverlo" que se
    repetia identico en cada punto de descarga de este modulo.

    Args:
        progress: Barra de progreso donde crear la tarea.
        description: Texto a mostrar para la tarea.

    Returns:
        Callback `(bytes_descargados, bytes_totales)` que actualiza esa
        tarea a medida que se llama.
    """
    task_id = progress.add_task(description, total=None)

    def on_progress(downloaded: int, total_bytes: int) -> None:
        progress.update(task_id, total=total_bytes or None, completed=downloaded)

    return on_progress


def part_progress_factory(progress: Progress) -> ProgressFactory:
    """Fabrica de progreso para `DownloadService.download_parts`.

    Crea una tarea "Parte N/total" por parte, todas en la misma `progress`.

    Args:
        progress: Barra de progreso donde crear cada tarea.

    Returns:
        Fabrica `(indice, total) -> on_progress` para pasarle a
        `download_parts`.
    """
    return lambda index, total: progress_callback(progress, f"Parte {index}/{total}")


def _fixed_progress_factory(callback: ProgressCallback) -> ProgressFactory:
    """Adapta un callback ya creado a la firma de `ProgressFactory`.

    Para cuando `download_parts` se llama con una sola url dentro de un loop
    que ya gestiona su propia tarea de progreso (ver
    `_download_season_packs_batch`), en vez de dejar que `download_parts`
    cree una tarea nueva por parte.

    Args:
        callback: Callback de progreso ya creado.

    Returns:
        Fabrica que ignora `(indice, total)` y siempre devuelve `callback`.
    """
    return lambda index, total: callback


def _try_resolve_without_browser(provider: SourceProvider, option: DownloadOption) -> list[str] | None:
    """Prueba resolver `option.url` sin abrir el navegador.

    El `option.url` de zona-leros SIEMPRE es del ad-locker
    (`anomizador.zona-leros.com`), nunca un link directo — pero no todos
    exigen un Turnstile: los de algunos episodios resultan ser solo una
    cadena de redirects HTTP hasta Mediafire, sin ningun desafio real de por
    medio (verificado en vivo), mientras que otros (peliculas, y otros
    episodios) si lo piden. Por eso esto prueba un GET directo antes de
    comprometerse al paso manual — `CloudflareHttpClient.try_get` no escala
    a abrir el navegador si sale desafiado, asi que esto nunca dispara el
    paso manual por su cuenta.

    Args:
        provider: Provider que resolvio `option` (debe tener un atributo
            `http` con `try_get`; si no, se asume que hace falta el paso
            manual).
        option: Opcion `opens_externally` a intentar resolver.

    Returns:
        Una lista de un solo url (el link directo de Mediafire), o None si
        no se pudo resolver sin navegador (ahi si hace falta el paso manual
        de siempre).
    """
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
    """Extrae el codigo de temporada/episodio de la url de un archivo.

    Asi vienen nombrados los packs de temporada de zona-leros, donde cada
    url es el .rar completo de un episodio, no un volumen mas de un archivo
    mas grande.

    Args:
        url: Url del archivo, ej. ".../RCKYMRTS01E01_ZL.rar/file".

    Returns:
        `(temporada, episodio)`, o None si la url no trae ese codigo.
    """
    match = _URL_EPISODE_CODE_RE.search(url)
    return (int(match.group(1)), int(match.group(2))) if match else None


def extract_and_flatten(service: DownloadService, stem: str, dest_dir: Path) -> Path | None:
    """Extrae el .rar bajado en `dest_dir/<stem>` y deja el video en `dest_dir`.

    Mediafire a veces sirve el video ya sin comprimir (`extract_and_organize`
    reconoce ese caso tambien). `extract_and_organize` nombra el video igual
    que la carpeta que lo contiene, asi que esto lo mueve un nivel hacia
    arriba (a la raiz de `dest_dir`) y borra la subcarpeta ya vacia.

    Args:
        service: Servicio con el que extraer/organizar.
        stem: Carpeta (bajo `dest_dir`) donde se bajo el archivo, y nombre
            de archivo a usar para el video.
        dest_dir: Carpeta donde debe quedar el video ya organizado.

    Returns:
        La ruta final del video, o None si no se encontro ninguno
        reconocible (con lo que se haya bajado intacto, para revisar a
        mano) o si la extraccion fallo (se avisa al usuario con el comando
        para hacerlo a mano).
    """
    try:
        video_path = service.extract_and_organize(stem, dest_dir)
    except ExtractionError as exc:
        prompts.console.print(
            f"[red]No se pudo extraer {stem} ({exc}).[/red]\n"
            f'[dim]Podes hacerlo a mano: cd "{dest_dir / stem}" && unrar x -pzonaleros *.rar[/dim]'
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


def download_and_extract_episode(
    service: DownloadService, media: Media, urls: list[str], dest_dir: Path, stem: str
) -> Path | None:
    """Baja uno o mas links de un unico episodio y lo deja extraido/organizado.

    Con su propia barra de progreso (una por episodio, no compartida con
    otros items del lote). A diferencia del camino de pelicula
    (`DownloadService.extract_and_organize`, que deja el video dentro de su
    propia subcarpeta), aca se aplana un nivel (`extract_and_flatten`)
    porque un episodio no tiene subcarpeta propia — termina directo en
    `dest_dir` (la carpeta de la temporada). Usado tanto por
    `_download_episodes_batch` (episodio scrapeado de un provider) como por
    `cli/links.py` (episodio insertado a mano).

    Args:
        service: Servicio con el que bajar/extraer.
        media: Media al que pertenecen `urls` (usado por `download_parts`
            para nombrar la descarga si hace falta).
        urls: Uno o mas links (partes) de este unico episodio.
        dest_dir: Carpeta de la temporada donde debe quedar el video.
        stem: Nombre de archivo (sin extension) del episodio.

    Returns:
        La ruta final del video, o None si se salteo (link caido y el
        usuario declino reintentar) o no se encontro un video reconocible.
    """

    def try_download_parts() -> list[Path]:
        with prompts.progress_bar() as progress:
            return service.download_parts(
                media, urls, dest_dir, folder_name=stem, progress_factory=part_progress_factory(progress)
            )

    result = run_with_retry(try_download_parts, description=stem)
    if result is None:
        prompts.console.print(f"[yellow]{stem}: se salta.[/yellow]")
        return None

    with prompts.console.status("[cyan]Extrayendo con unrar...[/cyan]"):
        final_path = extract_and_flatten(service, stem, dest_dir)

    if final_path:
        prompts.console.print(f"[green]Listo:[/green] {final_path}")
    return final_path


def run_download_flow(provider: SourceProvider, media: Media) -> None:
    """Flujo completo de descarga para un media ya resuelto.

    Muestra la portada, deja elegir una opcion, y la baja (streaming
    directo, o abrir en el navegador + pegar links + progreso). Una serie no
    tiene descargas propias (`get_download_options` viene vacio), asi que en
    ese caso `_download_series` se hace cargo de elegir que bajar (pack de
    temporada, episodio suelto, o en lote) y de todo el resto por su cuenta;
    lo que sigue en esta funcion es entonces solo el camino de
    pelicula/libro. Para un libro, elegir el formato (epub/pdf) ya es la
    unica decision que hay que tomar (no hay servidor/calidad para elegir,
    ni carpeta destino que configurar — siempre es `settings.books_dir`), asi
    que no se repite esa eleccion como una confirmacion aparte: se avisa
    donde va a quedar y se descarga directo (ver `prompts.choose_option`).

    Args:
        provider: Provider que resolvio `media`.
        media: Media ya resuelto (via `get_media`), a descargar.
    """
    http = getattr(provider, "http", None)
    fetch_bytes = (lambda url: http.get(url).content) if http else None
    prompts.show_cover(media, fetch_bytes=fetch_bytes)

    options = provider.get_download_options(media)
    if not options:
        _download_series(provider, media, fetch_bytes)
        return

    option = prompts.choose_option(options, kind=media.kind)

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
                return service.download_parts(
                    media, urls, dest_dir, folder_name=folder_name, progress_factory=part_progress_factory(progress)
                )

        saved_paths = run_with_retry(try_download_parts, description=media.title)
        if saved_paths is None:
            return

        for path in saved_paths:
            typer.echo(f"Guardado en {path}")

        with prompts.console.status("[cyan]Extrayendo con unrar...[/cyan]"):
            extraction_error: ExtractionError | None
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
                f'[dim]Podes hacerlo a mano: cd "{dest_dir / folder_name}" && '
                f"unrar x -pzonaleros *.rar[/dim]"
            )
        elif video_path:
            prompts.console.print(f"[green]Listo:[/green] {video_path}")
        return

    if media.kind == MediaKind.BOOK:
        prompts.console.print(f"[dim]Descargando en {dest_dir}[/dim]")
    elif not prompts.confirm_download(media, option, dest_dir):
        return

    def try_download() -> Path | None:
        with prompts.progress_bar() as progress:
            on_progress = progress_callback(progress, media.title)
            return service.download(media, option, dest_dir, on_progress=on_progress)

    dest_path = run_with_retry(try_download, description=media.title)
    if dest_path:
        prompts.console.print(f"[green]Guardado en {dest_path}[/green]")


def _download_series(provider: SourceProvider, series: Media, fetch_bytes: FetchBytes | None) -> None:
    """Elige que bajar de una serie y descarga.

    Una serie no tiene descargas propias, asi que primero hay que elegir que
    bajar — el pack de una temporada (o de todas las que tengan pack), un
    episodio suelto, o todos los episodios de una temporada / de la serie
    entera en lote (uno por uno, para series o sitios que no ofrecen ningun
    pack armado) — y despues bajarlo del todo aca mismo: a diferencia de una
    pelicula/libro, no hay una unica opcion que darle de vuelta a
    `run_download_flow`.

    Args:
        provider: Provider que resolvio `series`.
        series: Media de la ficha de la serie.
        fetch_bytes: Callback para bajar portadas (ver `run_download_flow`).
    """
    if series.kind != MediaKind.SERIES:
        typer.echo("No hay opciones de descarga disponibles para este resultado.")
        return

    if not isinstance(provider, SeriesProvider):
        typer.echo("No hay opciones de descarga disponibles para este resultado.")
        return

    seasons_with_packs = provider.get_seasons(series)
    mode = prompts.choose_series_mode(seasons_with_packs)

    if mode == "season_pack":
        season_number = prompts.choose_season(seasons_with_packs)
        _download_season_packs_batch(provider, series, [season_number])
        return

    if mode == "all_season_packs":
        _download_season_packs_batch(provider, series, seasons_with_packs)
        return

    episodes = provider.get_episodes(series)
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


def _download_season_packs_batch(provider: SeriesProvider, series: Media, seasons: list[int]) -> None:
    """Descarga uno o mas packs de temporada completa.

    Descarga uno atras del otro sin volver a preguntar nada salvo, cuando
    hay mas de uno, si arrancar. El ad-locker exige un Turnstile fresco por
    pagina, asi que el paso manual (abrir navegador, pegar links) sigue
    siendo por temporada — pero la pagina resultante lista un .rar
    independiente por episodio, no un unico archivo partido en volumenes,
    asi que cada uno se baja y se extrae por separado (ver el comentario mas
    abajo) y terminan en
    `<series_dir>/<Serie>/Season NN/<Serie> - SxxEyy.ext` — el mismo lugar
    donde caeria un episodio bajado suelto.

    Args:
        provider: Provider que resolvio `series`.
        series: Media de la ficha de la serie.
        seasons: Numeros de temporada a descargar, en orden.
    """
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

            def try_download_season(
                _option: DownloadOption = option, _season_media: Media = season_media, _folder_name: str = folder_name
            ) -> Path | None:
                with prompts.progress_bar() as progress:
                    on_progress = progress_callback(progress, _folder_name)
                    return service.download(
                        _season_media, _option, series_dir, filename_stem=_folder_name, on_progress=on_progress
                    )

            dest_path = run_with_retry(try_download_season, description=f"la temporada {season_number}")
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

                def try_download_episode_part(
                    _url: str = url,
                    _url_index: int = url_index,
                    _stem: str = stem,
                    _season_media: Media = season_media,
                    _season_dir: Path = season_dir,
                    _urls: list[str] = urls,
                ) -> list[Path]:
                    on_progress = progress_callback(progress, f"Episodio {_url_index}/{len(_urls)}")
                    return service.download_parts(
                        _season_media,
                        [_url],
                        _season_dir,
                        folder_name=_stem,
                        progress_factory=_fixed_progress_factory(on_progress),
                    )

                result = run_with_retry(try_download_episode_part, description=stem, progress=progress)
                if result is None:
                    prompts.console.print(f"[yellow]{stem}: se salta.[/yellow]")
                    continue

                final_path = extract_and_flatten(service, stem, season_dir)
                if final_path:
                    prompts.console.print(f"[green]Listo:[/green] {final_path}")


def _download_episodes_batch(
    provider: SourceProvider, series: Media, episodes: list[Media], fetch_bytes: FetchBytes | None
) -> None:
    """Descarga uno o mas episodios sueltos.

    Descarga uno atras del otro, sin volver a preguntar el modo en cada uno
    (salvo, cuando hay mas de uno, confirmar una vez si arrancar). El
    ad-locker exige un Turnstile fresco por pagina, asi que el paso manual
    (abrir navegador, pegar links) no se puede evitar por episodio — esto
    automatiza todo lo demas y deja los videos organizados en
    `<series_dir>/<Serie>/Season NN/<Serie> - SxxEyy.ext`, reusando el
    preferido de servidor (MEGA/MEDIAFIRE) elegido en el primer episodio
    para el resto, cuando este disponible. Tambien es el camino que usa un
    episodio suelto elegido a mano (lote de uno), asi que un episodio
    siempre termina en el mismo lugar sin importar si se pidio uno solo o
    todos de una.

    Args:
        provider: Provider que resolvio `series`/`episodes`.
        series: Media de la ficha de la serie a la que pertenecen.
        episodes: Episodios a descargar (se ordenan por codigo SxE antes de
            arrancar).
        fetch_bytes: Callback para bajar portadas (ver `run_download_flow`).
    """
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

            def try_download(
                _option: DownloadOption = option,
                _episode: Media = episode,
                _season_dir: Path = season_dir,
                _stem: str = stem,
            ) -> Path | None:
                with prompts.progress_bar() as progress:
                    on_progress = progress_callback(progress, _stem)
                    return service.download(
                        _episode, _option, _season_dir, filename_stem=_stem, on_progress=on_progress
                    )

            dest_path = run_with_retry(try_download, description=episode.title)
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

        download_and_extract_episode(service, episode, urls, season_dir, stem)


@app.command("movies")
def movies(source: str, media_id: str) -> None:
    """Descarga una pelicula o serie ya identificada.

    Args:
        source: Nombre del repo (ver `misflix search movies`).
        media_id: Id del resultado dentro de ese repo.
    """
    _download(source, media_id, kinds=MOVIE_KINDS, kind_label="una pelicula o serie")


@app.command("books")
def books(source: str, media_id: str) -> None:
    """Descarga un libro ya identificado.

    Args:
        source: Nombre del repo (ver `misflix search books`).
        media_id: Id del resultado dentro de ese repo.
    """
    _download(source, media_id, kinds=BOOK_KINDS, kind_label="un libro")


def _download(source: str, media_id: str, kinds: AbstractSet[MediaKind], kind_label: str) -> None:
    """Resuelve `media_id` en `source` y arranca el flujo de descarga si el tipo coincide.

    Args:
        source: Nombre del repo.
        media_id: Id del resultado dentro de ese repo.
        kinds: `MediaKind`s validos para el comando que llamo (movies/books).
        kind_label: Texto para el mensaje de error si el tipo no coincide.

    Raises:
        typer.Exit: Si `media.kind` no esta en `kinds`.
    """
    provider = get_provider(source)
    media = provider.get_media(media_id)
    if media.kind not in kinds:
        typer.echo(f"'{media_id}' en '{source}' es {media.kind.value}, no {kind_label}.")
        raise typer.Exit(code=1)
    run_download_flow(provider, media)
