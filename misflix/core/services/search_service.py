from __future__ import annotations

from collections.abc import Set as AbstractSet

from misflix.core.models import Media, MediaKind
from misflix.core.ports import SourceProvider


class SearchService:
    """Orquesta la busqueda entre uno o varios providers registrados."""

    def __init__(self, providers: dict[str, SourceProvider]):
        """Inicializa el servicio.

        Args:
            providers: Providers disponibles, indexados por `provider.name`.
        """
        self._providers = providers

    def search(self, query: str, source: str | None = None, kinds: AbstractSet[MediaKind] | None = None) -> list[Media]:
        """Busca `query` en uno o todos los providers registrados.

        `kinds` filtra el resultado agregado por `Media.kind` (ej. solo
        MOVIE/SERIES o solo BOOK) — asi un provider mixto (zona-leros trae
        peliculas y series) no se filtra por *provider*, sino por lo que cada
        resultado realmente es, separando la busqueda de peliculas/series de
        la de libros aunque convivan en el mismo repo.

        Args:
            query: Texto de busqueda.
            source: Si se da, restringe la busqueda a ese provider unicamente.
            kinds: Si se da, descarta del resultado (y, cuando es posible,
                del conjunto de providers a consultar) todo lo que no sea de
                alguno de estos tipos.

        Returns:
            Resultados agregados de todos los providers consultados.
        """
        if source:
            targets = [self._providers[source]]
        else:
            targets = list(self._providers.values())
            if kinds is not None:
                # Sin esto, buscar solo libros igual dispara la busqueda en
                # zona-leros (Cloudflare Turnstile) aunque sus resultados se
                # descarten despues por kind — un provider que declara `kinds`
                # y no intersecta lo pedido ni siquiera se consulta. Los que no
                # declaran `kinds` (o un test double) se siguen consultando
                # igual, como antes.
                targets = [p for p in targets if getattr(p, "kinds", kinds) & kinds]

        results: list[Media] = []
        for provider in targets:
            results.extend(provider.search(query))

        if kinds is not None:
            results = [r for r in results if r.kind in kinds]
        return results
