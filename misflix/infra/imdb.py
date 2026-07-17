from __future__ import annotations

from urllib.parse import quote

import httpx

from misflix.infra.http_client import DEFAULT_HEADERS


def resolve_title(query: str, year_hint: int | None = None) -> tuple[str, int | None] | None:
    """Busca `query` en el autocompletado de IMDb.

    No requiere API key ni esta detras de ningun bot-check, a diferencia de
    imdb.com. Devuelve (titulo original, anio) del mejor largometraje
    (`qid == "movie"`) encontrado.

    El endpoint de sugerencias NO ordena por popularidad ni relevancia real —
    puede traer, para una frase generica como "se busca", una pelicula oscura
    (rank ~118000) antes que la que en realidad se busca (ej. "Wanted", rank
    ~3900). Por eso, si se conoce el anio (viene del propio scraping del repo,
    normalmente mas confiable que el titulo), se prioriza el candidato cuyo
    anio coincide (o esta a 1 de diferencia); recien despues se ordena por
    rank.

    Args:
        query: Titulo scrapeado a buscar.
        year_hint: Año de estreno ya conocido, si el scraper lo trae, para
            desambiguar entre resultados con el mismo titulo.

    Returns:
        `(titulo, año)` del mejor match, o None si no hubo ningun largometraje
        entre los resultados o la request fallo.
    """
    first_letter = (query.strip()[:1] or "a").lower()
    url = f"https://v2.sg.media-imdb.com/suggestion/{first_letter}/{quote(query)}.json"

    try:
        response = httpx.get(url, headers=DEFAULT_HEADERS, timeout=10.0)
        response.raise_for_status()
        results = response.json().get("d", [])
    except (httpx.HTTPError, ValueError):
        return None

    movies = [item for item in results if item.get("qid") == "movie"]
    if not movies:
        return None

    def most_popular(candidates: list[dict]) -> dict:
        return min(candidates, key=lambda item: item.get("rank", float("inf")))

    if year_hint is not None:
        exact_year = [m for m in movies if m.get("y") == year_hint]
        if exact_year:
            match = most_popular(exact_year)
            return match.get("l"), match.get("y")

        close_year = [m for m in movies if m.get("y") is not None and abs(m["y"] - year_hint) <= 1]
        if close_year:
            match = most_popular(close_year)
            return match.get("l"), match.get("y")

    match = most_popular(movies)
    return match.get("l"), match.get("y")
