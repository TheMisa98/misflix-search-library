import typer

from misflix.core.services.search_service import SearchService
from misflix.providers.registry import get_all_providers
from misflix.ui import views

app = typer.Typer(help="Buscar peliculas o libros en los repos configurados.")


@app.command("run")
def run(
    query: str,
    source: str = typer.Option(None, help="Limitar la busqueda a un repo especifico."),
):
    """Busca `query` en los repos disponibles y muestra los resultados."""
    service = SearchService(providers=get_all_providers())
    results = service.search(query, source=source)
    views.show_results(results)
