import typer

from misflix.cli.download import run_download_flow
from misflix.core.models import Media, MediaKind
from misflix.core.ports import SourceProvider
from misflix.core.services.search_service import SearchService
from misflix.providers.registry import get_all_providers
from misflix.ui import views
from misflix.ui.image_render import FetchBytes

app = typer.Typer(help="Buscar peliculas, series o libros en los repos configurados.")

_PROMPT = "Elegi un numero para descargar, escribi para buscar otra cosa, o enter para salir ›"
_PROMPT_WITH_MORE = (
    "Elegi un numero para descargar, escribi 'mas' para ver los siguientes, "
    "escribi para buscar otra cosa, o enter para salir ›"
)
_MORE_COMMANDS = {"mas", "más"}
# Sentinel para distinguir "el usuario pidio la siguiente pagina" de una busqueda
# nueva de texto (que tambien es un str) sin usar un valor magico como None.
_MORE = object()

# Un provider puede traer varios MediaKind a la vez (zona-leros trae MOVIE y
# SERIES) — separar "movies"/"books" filtra por lo que cada resultado realmente
# es (ver SearchService.search), no por que repo lo trajo, asi que "movies"
# sigue mostrando peliculas y series juntas, como ya se hacia antes de separar
# el comando de libros.
_MOVIE_KINDS = {MediaKind.MOVIE, MediaKind.SERIES}
_BOOK_KINDS = {MediaKind.BOOK}


@app.command("movies")
def movies(
    query: str,
    source: str = typer.Option(None, help="Limitar la busqueda a un repo especifico."),
):
    """Busca peliculas o series, muestra los resultados y permite descargar alguno
    o volver a buscar con otro texto (sin reiniciar el comando) hasta que se deja
    en blanco."""
    _search_loop(query, source, _MOVIE_KINDS)


@app.command("books")
def books(
    query: str,
    source: str = typer.Option(None, help="Limitar la busqueda a un repo especifico."),
):
    """Busca libros, muestra los resultados y permite descargar alguno o volver a
    buscar con otro texto (sin reiniciar el comando) hasta que se deja en blanco."""
    _search_loop(query, source, _BOOK_KINDS)


def _search_loop(query: str, source: str | None, kinds: set[MediaKind]) -> None:
    providers = get_all_providers()
    service = SearchService(providers=providers)

    def fetcher_for(media: Media) -> FetchBytes | None:
        http = getattr(providers.get(media.source), "http", None)
        return (lambda url: http.get(url).content) if http else None

    next_query: str | None = query
    while next_query:
        results = service.search(next_query, source=source, kinds=kinds)
        start = 0
        while True:
            start, end = views.show_results(results, fetcher_for=fetcher_for, start=start)
            action = _prompt_pick_or_search_again(results, start, end, providers)
            if action is _MORE:
                start = end
                continue
            next_query = action
            break


def _prompt_pick_or_search_again(
    results: list[Media], start: int, end: int, providers: dict[str, SourceProvider]
) -> str | object | None:
    """`[start, end)` es el rango de `results` efectivamente dibujado en pantalla
    (ver `views.show_results`, que pagina cuando no entran todos de una). Mientras
    se elija un numero valido *dentro de ese rango*, descarga ese resultado (la
    ficha completa trae mas datos que el resultado liviano de la busqueda, como
    el anio para desambiguar en IMDb) y vuelve a preguntar sin salir. Si queda
    mas por mostrar y el usuario escribe 'mas', devuelve el sentinel `_MORE` para
    que el caller pida la siguiente pagina sin tratarlo como una busqueda nueva.
    Cualquier otra cosa — texto, o un numero fuera del rango visible — se
    interpreta como una busqueda nueva y se devuelve como la proxima query; en
    blanco termina."""
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
