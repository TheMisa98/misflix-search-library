from __future__ import annotations

from misflix.core.models import Media
from misflix.core.ports import SourceProvider


class SearchService:
    """Orquesta la busqueda entre uno o varios providers registrados."""

    def __init__(self, providers: dict[str, SourceProvider]):
        self._providers = providers

    def search(self, query: str, source: str | None = None) -> list[Media]:
        if source:
            targets = [self._providers[source]]
        else:
            targets = list(self._providers.values())

        results: list[Media] = []
        for provider in targets:
            results.extend(provider.search(query))
        return results
