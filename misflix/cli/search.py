from collections.abc import Set as AbstractSet

import typer

from misflix.cli.download import run_download_flow
from misflix.core.models import BOOK_KINDS, MOVIE_KINDS, Author, Media, MediaKind
from misflix.core.ports import SourceProvider
from misflix.core.services.search_service import SearchService, exact_author_match
from misflix.providers.registry import get_all_providers
from misflix.ui import prompts, views
from misflix.ui.image_render import FetchBytes

app = typer.Typer(help="Buscar peliculas, series o libros en los repos configurados.")

_PROMPT = "Elegi un numero para descargar, escribi para buscar otra cosa, o enter para salir ›"
_PROMPT_WITH_MORE = (
    "Elegi un numero para descargar, escribi 'mas' para ver los siguientes, "
    "escribi para buscar otra cosa, o enter para salir ›"
)
_MORE_COMMANDS = {"mas", "más"}
_AUTHOR_RETRY_PROMPT = "Escribi otro autor para buscar, o enter para salir ›"


class _More:
    """Sentinel: el usuario pidio ver la siguiente pagina de resultados.

    Tipo propio (en vez de `object()`) para que el chequeo `is _MORE` en
    `_search_loop` deje a `mypy` angostar el resto del flujo a `str | None`.
    """


# Distinguye "el usuario pidio la siguiente pagina" de una busqueda nueva de
# texto (que tambien es un str) sin usar un valor magico como None.
_MORE = _More()


@app.command("movies")
def movies(
    query: str,
    source: str = typer.Option(None, help="Limitar la busqueda a un repo especifico."),
) -> None:
    """Busca peliculas o series.

    Muestra los resultados y permite descargar alguno o volver a buscar con
    otro texto (sin reiniciar el comando) hasta que se deja en blanco.

    Args:
        query: Texto de busqueda.
        source: Si se da, restringe la busqueda a ese repo unicamente.
    """
    _search_loop(query, source, MOVIE_KINDS)


@app.command("books")
def books(
    query: str,
    source: str = typer.Option(None, help="Limitar la busqueda a un repo especifico."),
    author: bool = typer.Option(
        False,
        "--author",
        "--autor",
        "-a",
        help="Tratar la busqueda como un autor y traer solo los libros de su pagina.",
    ),
) -> None:
    """Busca libros.

    Muestra los resultados y permite descargar alguno o volver a buscar con
    otro texto (sin reiniciar el comando) hasta que se deja en blanco.

    Args:
        query: Texto de busqueda.
        source: Si se da, restringe la busqueda a ese repo unicamente.
        author: Si es True, `query` se trata como un autor: en vez de la
            busqueda normal (texto + autores + series emparejados, todo
            junto) se muestran solo los libros de la pagina de ese autor.
    """
    if author:
        _author_loop(query, source)
    else:
        _search_loop(query, source, BOOK_KINDS)


def _search_loop(query: str, source: str | None, kinds: AbstractSet[MediaKind]) -> None:
    """Busca `query`, muestra resultados y deja elegir uno o buscar de nuevo.

    Repite hasta que el usuario deja la respuesta en blanco (ver
    `_prompt_pick_or_search_again`).

    Args:
        query: Primera busqueda a ejecutar.
        source: Si se da, restringe la busqueda a ese repo unicamente.
        kinds: `MediaKind`s a los que restringir los resultados.
    """
    providers = get_all_providers()
    service = SearchService(providers=providers)

    next_query: str | None = query
    while next_query:
        results = service.search(next_query, source=source, kinds=kinds)
        next_query = _browse_results(results, providers)


def _author_loop(query: str, source: str | None) -> None:
    """Busca `query` como autor y muestra solo los libros de su pagina.

    Si el repo empareja varios autores (lectulandia empareja por substring:
    "king" trae 34), se elige directo el que coincide exacto con `query`
    (ver `exact_author_match`) y, si ninguno lo hace, se pregunta cual.
    Escribir texto en vez de un numero en el listado de libros busca otro
    autor, igual que `_search_loop` busca otro texto.

    Args:
        query: Primer autor a buscar.
        source: Si se da, restringe la busqueda a ese repo unicamente.
    """
    providers = get_all_providers()
    service = SearchService(providers=providers)

    next_query: str | None = query
    while next_query:
        authors = service.search_authors(next_query, source=source, kinds=BOOK_KINDS)
        if not authors:
            views.show_notice(f"No se encontro ningun autor para '{next_query}'.")
            next_query = typer.prompt(_AUTHOR_RETRY_PROMPT, default="", show_default=False).strip() or None
            continue

        picked = _pick_author(authors, next_query)
        books = service.get_author_books(picked)
        views.show_notice(f"Libros de {picked.name} ({picked.source}): {len(books)}")
        next_query = _browse_results(books, providers)


def _pick_author(authors: list[Author], query: str) -> Author:
    """Elige el autor a mostrar: el unico, el exacto, o el que elija el usuario.

    Args:
        authors: Autores emparejados (al menos uno).
        query: Texto que escribio el usuario.

    Returns:
        El autor elegido.
    """
    if len(authors) == 1:
        return authors[0]
    return exact_author_match(authors, query) or prompts.choose_author(authors)


def _browse_results(results: list[Media], providers: dict[str, SourceProvider]) -> str | None:
    """Muestra `results` paginados y deja descargar hasta que se busca otra cosa.

    Args:
        results: Resultados a mostrar.
        providers: Providers registrados (para la portada y la descarga).

    Returns:
        La proxima query que escribio el usuario, o None si dejo en blanco.
    """

    def fetcher_for(media: Media) -> FetchBytes | None:
        """Cliente HTTP con el que bajar la portada de `media`, si el provider tiene uno."""
        http = getattr(providers.get(media.source), "http", None)
        return (lambda url: http.get(url).content) if http else None

    start = 0
    while True:
        start, end = views.show_results(results, fetcher_for=fetcher_for, start=start)
        action = _prompt_pick_or_search_again(results, start, end, providers)
        if isinstance(action, _More):
            start = end
            continue
        return action


def _prompt_pick_or_search_again(
    results: list[Media], start: int, end: int, providers: dict[str, SourceProvider]
) -> str | _More | None:
    """Pide un numero para descargar, 'mas' para paginar, o texto para buscar de nuevo.

    `[start, end)` es el rango de `results` efectivamente dibujado en
    pantalla (ver `views.show_results`, que pagina cuando no entran todos de
    una). Mientras se elija un numero valido *dentro de ese rango*, descarga
    ese resultado (la ficha completa trae mas datos que el resultado liviano
    de la busqueda, como el anio para desambiguar en IMDb) y vuelve a
    preguntar sin salir. Cualquier otra cosa — texto, o un numero fuera del
    rango visible — se interpreta como una busqueda nueva.

    Args:
        results: Resultados completos de la busqueda actual (no solo los
            dibujados en esta pagina).
        start: Inicio (inclusive) del rango efectivamente dibujado.
        end: Fin (exclusivo) del rango efectivamente dibujado.
        providers: Providers registrados, para resolver la ficha completa
            del resultado elegido.

    Returns:
        None si el usuario dejo la respuesta en blanco (fin del comando); el
        sentinel `_MORE` si pidio la siguiente pagina y quedaba mas por
        mostrar; o un `str` con la proxima query a buscar (texto libre, o un
        numero fuera del rango visible).
    """
    has_more = end < len(results)
    prompt_text = _PROMPT_WITH_MORE if has_more else _PROMPT
    while True:
        raw = typer.prompt(prompt_text, default="", show_default=False).strip()
        if not raw:
            return None

        if has_more and raw.lower() in _MORE_COMMANDS:
            return _MORE

        try:
            index = int(raw) - 1
        except ValueError:
            return raw
        if not (start <= index < end):
            return raw

        picked = results[index]
        provider = providers[picked.source]
        media = provider.get_media(picked.id)
        run_download_flow(provider, media)
