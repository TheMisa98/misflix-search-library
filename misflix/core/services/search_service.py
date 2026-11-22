from __future__ import annotations

import unicodedata
from collections.abc import Set as AbstractSet

from misflix.core.models import Author, Media, MediaKind
from misflix.core.ports import AuthorProvider, SourceProvider


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
        results: list[Media] = []
        for provider in self._targets(source, kinds):
            results.extend(provider.search(query))

        if kinds is not None:
            results = [r for r in results if r.kind in kinds]
        return results

    def search_authors(
        self, query: str, source: str | None = None, kinds: AbstractSet[MediaKind] | None = None
    ) -> list[Author]:
        """Busca autores que coincidan con `query`, en los providers que los exponen.

        Solo consulta providers que implementan `AuthorProvider`; el resto
        (ej. zona-leros) se saltea sin llamarlo.

        Args:
            query: Texto de busqueda.
            source: Si se da, restringe la busqueda a ese provider unicamente.
            kinds: Igual que en `search`: saltea providers cuyo `kinds`
                declarado no intersecta lo pedido.

        Returns:
            Autores emparejados de todos los providers consultados.
        """
        authors: list[Author] = []
        for provider in self._targets(source, kinds):
            if isinstance(provider, AuthorProvider):
                authors.extend(provider.search_authors(query))
        return authors

    def get_author_books(self, author: Author) -> list[Media]:
        """Libros de `author`, pedidos al provider que lo devolvio.

        Args:
            author: Autor devuelto por `search_authors`.

        Returns:
            Libros de la pagina del autor.

        Raises:
            KeyError: Si `author.source` no es un provider registrado.
            TypeError: Si ese provider no implementa `AuthorProvider`.
        """
        provider = self._providers[author.source]
        if not isinstance(provider, AuthorProvider):
            raise TypeError(f"El provider {author.source!r} no expone paginas de autor.")
        return provider.get_author_books(author)

    def _targets(self, source: str | None, kinds: AbstractSet[MediaKind] | None) -> list[SourceProvider]:
        if source:
            return [self._providers[source]]
        targets = list(self._providers.values())
        if kinds is not None:
            # Sin esto, buscar solo libros igual dispara la busqueda en
            # zona-leros (Cloudflare Turnstile) aunque sus resultados se
            # descarten despues por kind — un provider que declara `kinds`
            # y no intersecta lo pedido ni siquiera se consulta. Los que no
            # declaran `kinds` (o un test double) se siguen consultando
            # igual, como antes.
            targets = [p for p in targets if getattr(p, "kinds", kinds) & kinds]
        return targets


def exact_author_match(authors: list[Author], query: str) -> Author | None:
    """El unico autor cuyo nombre es exactamente `query`, si lo hay.

    La comparacion ignora mayusculas, acentos y espacios de sobra, asi
    "stephen king" elige "Stephen King" directo aunque el sitio tambien
    empareje "Stephen W. Hawking" o "Tabitha King". Si hay mas de uno con
    el mismo nombre (ej. el mismo autor en dos repos), devuelve None para
    que el caller pregunte.

    Args:
        authors: Autores emparejados por `SearchService.search_authors`.
        query: Texto que escribio el usuario.

    Returns:
        El autor que coincide exacto, o None si ninguno (o mas de uno) lo hace.
    """
    target = _normalize(query)
    matches = [a for a in authors if _normalize(a.name) == target]
    return matches[0] if len(matches) == 1 else None


def _normalize(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    without_accents = "".join(c for c in decomposed if not unicodedata.combining(c))
    return " ".join(without_accents.split())
