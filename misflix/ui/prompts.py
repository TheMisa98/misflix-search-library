from __future__ import annotations

import re
import select
import sys
from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.progress import BarColumn, DownloadColumn, Progress, TimeRemainingColumn, TransferSpeedColumn
from rich.table import Table

from misflix.core.models import DownloadOption, Media, MediaKind
from misflix.infra.filesystem import part_number
from misflix.ui.image_render import Card, CoverRenderer, FetchBytes

console = Console()

_URL_RE = re.compile(r"https?://\S+")
_LABEL_RE = re.compile(r"^(?P<host>\S+)(?:\s*\((?P<quality>.+)\))?$")
_HOST_COLORS = {"MEGA": "red", "MEDIAFIRE": "blue"}
_BRACKETED_PASTE_RE = re.compile(r"\x1b\[20[01]~")
_EPISODE_LABEL_RE = re.compile(r"^(?P<show>.*?)\s*(?P<code>\d+x\d+)$")


def show_cover(media: Media, fetch_bytes: FetchBytes | None = None, landscape: bool = False) -> None:
    """Muestra la ficha y portada de `media`.

    El titulo va en el borde del panel (`title=`, centrado — el mismo estilo
    que el panel de sinopsis, mas abajo) en vez de como primera linea del
    cuerpo. La sinopsis (si la hay) se imprime aparte, despues de la
    portada, en vez de ir dentro del panel que ancla la imagen: ese panel
    necesita un ancho fijo para que `render_grid` pueda calcular en que fila
    poner la portada (ver su docstring — la imagen queda clavada a esa fila,
    no hace scroll como el texto), asi que una sinopsis larga ahi adentro se
    envuelve en muchas lineas angostas y empuja la imagen mucho mas abajo de
    lo que en verdad hace falta, a veces mas alla de lo que entra en
    pantalla (verificado en vivo: la portada terminaba mal ubicada).
    Imprimirla despues, a todo el ancho de la consola, evita ese
    acoplamiento del todo. Panel y portada van centrados en el ancho de la
    terminal (`centered=True`, ver `render_grid`) en vez de pegados al
    margen izquierdo, ya que aca siempre es una unica card — y ese ancho fijo
    se calcula a partir de la terminal real (`console.size.width`) en vez de
    usar el `card_width` angosto pensado para la grilla de 2 columnas de
    `views.show_results`: con ese angosto, un titulo largo quedaba cortado a
    la mitad en el borde del panel en vez de envolverse o mostrarse entero
    (verificado en vivo).

    Args:
        media: Media a mostrar.
        fetch_bytes: Callback para bajar la portada, si el provider la
            necesita (ver `cli/search.py`/`cli/download.py`).
        landscape: True para la portada de un episodio suelto: a diferencia
            de una pelicula/serie (poster vertical, ~2:3), zona-leros usa
            ahi una miniatura tipo screenshot horizontal (~3:2 — verificado
            contra el sitio real: 307x207 para un episodio vs. 400x570 para
            una serie). Meter las dos formas en la misma caja vertical le
            queda mal a una de las dos, asi que la caja de un episodio es
            mas ancha que alta en vez de al reves.
    """
    title = f"{media.title} ({media.year})" if media.year else media.title
    body = f"[dim]{media.author}[/dim]\n" if media.author else ""
    body += f"[dim]{media.kind.value} · {media.source}[/dim]"
    panel = Panel(body, title=title, border_style="cyan", expand=False)
    card_width = max(20, console.size.width - 4)

    if not media.cover_url:
        console.print(panel, justify="center")
    else:
        card = Card(panel, media.cover_url, fetch_bytes)
        if landscape:
            CoverRenderer().render_grid(
                [card], columns=1, card_width=card_width, image_width=32, image_height=10, centered=True
            )
        else:
            CoverRenderer().render_grid([card], columns=1, card_width=card_width, centered=True)

    if media.synopsis:
        console.print(Panel(media.synopsis, title="Sinopsis", border_style="cyan", expand=False), justify="center")


def choose_option(options: list[DownloadOption], kind: MediaKind | None = None) -> DownloadOption:
    """Muestra `options` en una tabla y pide elegir una.

    Un libro no tiene servidor/calidad/tamaño que mostrar (lectulandia solo
    ofrece un formato por boton, ya resuelto a antupload — ver
    `providers/lectulandia.py`), asi que para `MediaKind.BOOK` la tabla se
    reduce a "#"/"Formato" en vez de reusar las columnas pensadas para
    pelicula/serie.

    Args:
        options: Opciones de descarga a mostrar.
        kind: Tipo de media al que pertenecen `options`, para elegir el
            formato de tabla. None (peliculas/series, el caso de siempre)
            usa la tabla con servidor/calidad/tamaño.

    Returns:
        La opcion elegida.
    """
    if kind == MediaKind.BOOK:
        table = Table(border_style="cyan", header_style="bold cyan")
        table.add_column("#", justify="right", style="bold", no_wrap=True)
        table.add_column("Formato")
        for i, option in enumerate(options, start=1):
            table.add_row(str(i), option.label)
        console.print(table)
        return options[_prompt_choice("Elegi un formato", len(options)) - 1]

    table = Table(border_style="cyan", header_style="bold cyan")
    table.add_column("#", justify="right", style="bold", no_wrap=True)
    table.add_column("Servidor")
    table.add_column("Calidad", style="magenta")
    table.add_column("Tamaño", justify="right", style="green")

    for i, option in enumerate(options, start=1):
        match = _LABEL_RE.match(option.label)
        host = match.group("host") if match else option.label
        quality = (match.group("quality") if match else None) or "-"
        color = _HOST_COLORS.get(host.upper())
        host_text = f"[{color}]{host}[/{color}]" if color else host
        table.add_row(str(i), host_text, quality, _format_size(option.size_bytes))

    console.print(table)
    return options[_prompt_choice("Elige una opcion", len(options)) - 1]


def choose_episode(episodes: list[Media]) -> Media:
    """Muestra `episodes` en una tabla y pide elegir uno.

    Args:
        episodes: Episodios a mostrar.

    Returns:
        El episodio elegido.
    """
    table = Table(title="Episodios", border_style="cyan", header_style="bold cyan")
    table.add_column("#", justify="right", style="bold", no_wrap=True)
    table.add_column("Capitulo", style="magenta", no_wrap=True)
    table.add_column("Serie")

    for i, episode in enumerate(episodes, start=1):
        match = _EPISODE_LABEL_RE.match(episode.title)
        show, code = (match.group("show"), match.group("code")) if match else (episode.title, "-")
        table.add_row(str(i), code, show)

    console.print(table)
    return episodes[_prompt_choice("Elegi un episodio", len(episodes)) - 1]


_SERIES_MODE_LABELS = {
    "season_pack": "Una temporada completa (pack ya armado por el repo)",
    "all_season_packs": "Toda la serie (todas las temporadas, packs completos)",
    "season_batch": "Todos los episodios de una temporada (uno por uno)",
    "series_batch": "Toda la serie (todos los episodios, uno por uno)",
    "episode": "Un episodio suelto",
}


def choose_series_mode(seasons_with_packs: list[int]) -> str:
    """Pregunta como descargar una serie.

    `season_pack` (el .rar de una temporada ya armado por el repo) solo se
    ofrece si `seasons_with_packs` no esta vacia, y `all_season_packs`
    (todas esas temporadas, una atras de la otra) solo si hay mas de una —
    con una sola temporada con pack ya es lo mismo que `season_pack`. Las
    opciones "por episodio" (que descargan uno por uno, ya que estos sitios
    exigen resolver un captcha nuevo por cada pagina) siempre estan
    disponibles, sea cual sea el repo.

    Args:
        seasons_with_packs: Numeros de temporada que tienen un pack
            completo (ver `SeriesProvider.get_seasons`).

    Returns:
        Uno de `'season_pack'`, `'all_season_packs'`, `'season_batch'`,
        `'series_batch'` o `'episode'`.
    """
    modes = []
    if seasons_with_packs:
        modes.append("season_pack")
        if len(seasons_with_packs) > 1:
            modes.append("all_season_packs")
    modes += ["season_batch", "series_batch", "episode"]

    table = Table(title="Como queres descargar esta serie?", border_style="cyan", header_style="bold cyan")
    table.add_column("#", justify="right", style="bold", no_wrap=True)
    table.add_column("Opcion")
    for i, mode in enumerate(modes, start=1):
        table.add_row(str(i), _SERIES_MODE_LABELS[mode])

    console.print(table)
    return modes[_prompt_choice("Elegi una opcion", len(modes)) - 1]


def confirm_batch_download(count: int, unit: str = "episodios") -> bool:
    """Pregunta, una unica vez, si arrancar un lote de `count` items.

    El Turnstile del ad-locker es fresco por pagina, asi que cada item va a
    necesitar su propio paso manual en el navegador (abrir, resolver, pegar
    links) — pero no hace falta reconfirmar eso en cada elemento del lote.

    Args:
        count: Cantidad de items a procesar.
        unit: Sustantivo a mostrar (ej. "episodios", "temporadas").

    Returns:
        True si el usuario confirmo arrancar.
    """
    console.print(
        Panel(
            f"Se van a procesar {count} {unit}, uno por uno.\n"
            "[dim]Para cada uno se abre el navegador para resolver la verificacion; "
            "pega los links cuando te los muestre y seguimos solos con el siguiente.[/dim]",
            title="Descarga en lote",
            border_style="yellow",
            expand=False,
        )
    )
    return typer.confirm("Arrancar?", default=True)


def option_host(option: DownloadOption) -> str:
    """Nombre de servidor de `option`, sin la calidad entre parentesis.

    Permite reusar la misma preferencia de servidor entre varios episodios
    de un lote sin volver a preguntar en cada uno.

    Args:
        option: Opcion cuya etiqueta (ej. "MEGA (1080p)") inspeccionar.

    Returns:
        El nombre de servidor en mayusculas, ej. "MEGA".
    """
    match = _LABEL_RE.match(option.label)
    return (match.group("host") if match else option.label).upper()


def choose_season(seasons: list[int]) -> int:
    """Muestra `seasons` en una tabla y pide elegir una.

    Args:
        seasons: Numeros de temporada a mostrar.

    Returns:
        El numero de temporada elegido.
    """
    table = Table(title="Temporadas disponibles", border_style="cyan", header_style="bold cyan")
    table.add_column("#", justify="right", style="bold", no_wrap=True)
    table.add_column("Temporada", style="magenta")

    for i, season in enumerate(seasons, start=1):
        table.add_row(str(i), f"Temporada {season}")

    console.print(table)
    return seasons[_prompt_choice("Elegi una temporada", len(seasons)) - 1]


def _prompt_choice(label: str, count: int) -> int:
    """Pide un numero entre 1 y `count`, reintentando hasta que sea valido.

    Args:
        label: Texto del prompt.
        count: Cantidad de opciones disponibles.

    Returns:
        El numero elegido (1-based).
    """
    while True:
        raw = console.input(f"[bold cyan]{label} (1-{count}) ›[/bold cyan] ")
        if raw.strip().isdigit() and 1 <= int(raw.strip()) <= count:
            return int(raw.strip())
        console.print(f"[red]Ingresa un numero entre 1 y {count}.[/red]")


def _format_size(size_bytes: int | None) -> str:
    """Formatea un tamaño en bytes de forma legible (ej. "1.2 GB").

    Args:
        size_bytes: Tamaño en bytes, o None si se desconoce.

    Returns:
        El tamaño formateado, o "-" si `size_bytes` es None.
    """
    if size_bytes is None:
        return "-"
    size = float(size_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} PB"


def confirm_download(media: Media, option: DownloadOption, dest_dir: Path) -> bool:
    """Pide confirmar la descarga directa de `option`.

    Args:
        media: Media al que pertenece la opcion.
        option: Opcion a descargar.
        dest_dir: Carpeta destino a mostrar.

    Returns:
        True si el usuario confirmo.
    """
    body = f"[bold]{media.title}[/bold] · {option.label}\n[dim]destino: {dest_dir}[/dim]"
    console.print(Panel(body, title="Confirmar descarga", border_style="cyan", expand=False))
    return typer.confirm("Confirmar?")


def confirm_open_externally(media: Media, option: DownloadOption) -> bool:
    """Pide confirmar abrir `option` en el navegador para resolverla a mano.

    Args:
        media: Media al que pertenece la opcion.
        option: Opcion `opens_externally` a abrir.

    Returns:
        True si el usuario confirmo.
    """
    body = (
        f"[bold]{media.title}[/bold] · {option.label}\n"
        "[dim]Se abrira en tu navegador para que completes la verificacion y "
        "consigas los links finales.[/dim]"
    )
    console.print(Panel(body, title="Abrir en el navegador", border_style="yellow", expand=False))
    return typer.confirm("Abrir?")


def _read_buffered_line() -> str | None:
    """Lee una linea de stdin solo si ya esta lista, sin bloquear.

    `select` sobre una terminal en modo canonico devuelve "listo" recien
    cuando el driver de la tty ya tiene una linea completa en su cola — asi
    que esto detecta exactamente las lineas que llegaron pegadas junto con
    la anterior, sin arriesgarse a bloquear leyendo de mas.

    Returns:
        La linea leida, o None si no hay ninguna esperando todavia.
    """
    if not sys.stdin.isatty():
        return None
    ready, _, _ = select.select([sys.stdin], [], [], 0)
    return sys.stdin.readline() if ready else None


def _parse_pasted_links(raw_lines: list[str]) -> list[str]:
    """Extrae, dedupe y ordena por numero de parte los links de `raw_lines`.

    Args:
        raw_lines: Lineas crudas tal como se pegaron (pueden traer texto
            alrededor de cada url, y el mismo link repetido mas de una vez).

    Returns:
        Los links detectados, en orden de parte (los que no traen numero de
        parte van al final, en el orden en que se pegaron). Vacio si no se
        detecto ningun link valido.
    """
    seen: set[str] = set()
    urls: list[str] = []
    for match in _URL_RE.findall("\n".join(raw_lines)):
        url = match.rstrip(").,;\"'")
        if url not in seen:
            seen.add(url)
            urls.append(url)

    urls.sort(key=lambda u: (part_number(u) is None, part_number(u) or 0))
    return urls


def _collect_raw_paste_lines() -> list[str]:
    """Lee lineas pegadas por el usuario hasta una linea en blanco.

    Returns:
        Las lineas crudas tal como se pegaron (posiblemente vacia, si el
        usuario dejo la primera linea en blanco de una).
    """
    # Kitty envuelve los pegados con marcadores de "bracketed paste"; si no se
    # desactiva, un paste de varias lineas de un tiron deja basura mezclada entre
    # medio (prompts repetidos, lineas cortadas). Se desactiva mientras leemos.
    sys.stdout.write("\x1b[?2004l")
    sys.stdout.flush()
    try:
        raw_lines: list[str] = []
        while True:
            # Un paste de varias lineas de un tiron llega al buffer de la terminal
            # entero, no de a una: si despues de la primera linea ya hay otra lista
            # para leer sin bloquear, es que vino pegada junto con la anterior, asi
            # que se lee directo (sin volver a imprimir el prompt) para evitar el
            # amontonamiento de "›" que se ve si se reimprime uno por cada linea.
            buffered = _read_buffered_line() if raw_lines else None
            line = buffered if buffered is not None else console.input("[green]›[/green] ")
            line = _BRACKETED_PASTE_RE.sub("", line).rstrip("\n")
            if not line.strip():
                break
            raw_lines.append(line)
    finally:
        sys.stdout.write("\x1b[?2004h")
        sys.stdout.flush()
    return raw_lines


def collect_direct_links() -> list[str]:
    """Pide los links finales resueltos a mano en el navegador.

    Se puede pegar todo de una (varias lineas en un solo paste); los links
    se extraen y ordenan solos por el numero de parte en el nombre de
    archivo (`partN`), si lo tienen. Si se pego algo pero no se reconocio
    ningun link (ej. un paste con basura o un espacio suelto adelante), se
    vuelve a pedir en vez de abortar el flujo de descarga en curso — la
    unica forma de cancelar de una es dejar la primera linea en blanco sin
    haber pegado nada.

    Returns:
        Los links detectados, en orden de parte (los que no traen numero de
        parte van al final, en el orden en que se pegaron). Vacio solo si el
        usuario cancelo (primera linea en blanco sin pegar nada).
    """
    console.print(
        Panel(
            "Volve al navegador, completa la verificacion y copia los links de descarga "
            "que te muestre (MEGA, MEDIAFIRE, etc.).\n"
            "[dim]Podes pegar todos juntos, no hace falta uno por uno ni en orden — "
            "se detectan y ordenan solos por el numero de parte (partN) del archivo.[/dim]",
            title="Pega los links de descarga",
            border_style="green",
            expand=False,
        )
    )

    while True:
        raw_lines = _collect_raw_paste_lines()
        if not raw_lines:
            return []

        urls = _parse_pasted_links(raw_lines)
        if urls:
            break
        console.print(
            "[yellow]No se detecto ningun link en lo que pegaste — probá de nuevo (dejá vacío para cancelar).[/yellow]"
        )

    table = Table(title="Links detectados", border_style="green", header_style="bold green")
    table.add_column("Parte", justify="right")
    table.add_column("Link")
    for url in urls:
        part = part_number(url)
        table.add_row(str(part) if part else "-", url)
    console.print(table)

    return urls


def progress_bar() -> Progress:
    """Crea una barra de progreso de rich para una descarga (o secuencia de partes).

    Returns:
        Un `Progress` listo para usar como context manager.
    """
    return Progress(
        "[progress.description]{task.description}",
        BarColumn(),
        DownloadColumn(),
        TransferSpeedColumn(),
        TimeRemainingColumn(),
        console=console,
    )
