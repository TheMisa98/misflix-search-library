from __future__ import annotations

from collections.abc import Callable

from rich.console import Console
from rich.panel import Panel

from misflix.core.models import Media
from misflix.ui.image_render import Card, CoverRenderer, FetchBytes

console = Console()

# Cada resultado puede venir de una fuente distinta; esta funcion decide,
# para un media dado, con que cliente HTTP bajar su portada (o None para
# dejar que kitten la pida el mismo).
FetcherFor = Callable[[Media], "FetchBytes | None"]


def show_results(results: list[Media], fetcher_for: FetcherFor | None = None, start: int = 0) -> tuple[int, int]:
    """Dibuja tantos resultados como entren en la pantalla, a partir de `start`.

    `start` es 0-based, para paginar (ver `cli/search.py`); cuantos entran
    depende del alto real de la terminal (ver `CoverRenderer.render_grid`).
    Cada card se numera con su posicion absoluta en `results`
    (`start`+offset+1), no con su posicion dentro de la pagina, asi el
    numero de un resultado no cambia entre paginas.

    Args:
        results: Resultados completos a paginar (no solo los de esta pagina).
        fetcher_for: Callback que, para un `Media`, devuelve el cliente HTTP
            con el que bajar su portada (o None para dejar que `kitten` la
            pida el mismo).
        start: Indice (0-based) desde donde empezar a dibujar.

    Returns:
        El rango `[start, end)` efectivamente dibujado — el caller debe
        usarlo, no `len(results)`, para saber que numeros son validos
        elegir: mostrar #1-#4 pero dejar elegir hasta #8 confundiria al
        usuario con resultados invisibles.
    """
    if not results:
        console.print(Panel("No se encontraron resultados.", border_style="yellow", expand=False))
        return (0, 0)

    cards = []
    for offset, media in enumerate(results[start:]):
        i = start + offset + 1
        year = f" ({media.year})" if media.year else ""
        author = f"[dim]{media.author}[/dim]\n" if media.author else ""
        body = (
            f"[bold]{media.title}[/bold]{year}\n"
            f"{author}"
            f"[dim]{media.kind.value} · {media.source}[/dim]\n"
            f"[dim]id: {media.id}[/dim]"
        )
        panel = Panel(body, title=f"#{i}", title_align="left", border_style="cyan", expand=False)
        fetch_bytes = fetcher_for(media) if (fetcher_for and media.cover_url) else None
        cards.append(Card(panel, media.cover_url, fetch_bytes))

    rendered = CoverRenderer().render_grid(cards, columns=2)
    end = start + rendered
    if end < len(results):
        console.print(
            Panel(
                f"Mostrando {start + 1}-{end} de {len(results)} resultados. "
                "Escribi 'mas' para ver los siguientes, o afina la busqueda.",
                border_style="yellow",
                expand=False,
            )
        )
    return (start, end)
