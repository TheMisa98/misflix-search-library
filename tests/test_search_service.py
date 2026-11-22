from __future__ import annotations

import pytest

from misflix.core.models import Author, Media, MediaKind
from misflix.core.services.search_service import SearchService, exact_author_match


class FakeProvider:
    def __init__(self, name: str, results: list[Media]):
        self.name = name
        self._results = results
        self.queries: list[str] = []

    def search(self, query: str) -> list[Media]:
        self.queries.append(query)
        return self._results

    def get_media(self, media_id: str) -> Media:
        raise NotImplementedError

    def get_download_options(self, media: Media):
        raise NotImplementedError


def make_media(title: str, source: str, kind: MediaKind = MediaKind.MOVIE) -> Media:
    return Media(id=title, title=title, kind=kind, source=source, page_url="http://example.com")


def test_search_aggregates_results_from_all_providers():
    provider_a = FakeProvider("a", [make_media("Movie A", "a")])
    provider_b = FakeProvider("b", [make_media("Movie B", "b")])
    service = SearchService(providers={"a": provider_a, "b": provider_b})

    results = service.search("query")

    assert {m.title for m in results} == {"Movie A", "Movie B"}
    assert provider_a.queries == ["query"]
    assert provider_b.queries == ["query"]


def test_search_filters_by_source():
    provider_a = FakeProvider("a", [make_media("Movie A", "a")])
    provider_b = FakeProvider("b", [make_media("Movie B", "b")])
    service = SearchService(providers={"a": provider_a, "b": provider_b})

    results = service.search("query", source="a")

    assert [m.title for m in results] == ["Movie A"]
    assert provider_a.queries == ["query"]
    assert provider_b.queries == []


def test_search_unknown_source_raises_key_error():
    service = SearchService(providers={})

    with pytest.raises(KeyError):
        service.search("query", source="missing")


def test_search_filters_by_kind_across_a_mixed_provider():
    mixed_provider = FakeProvider(
        "zona-leros",
        [
            make_media("Movie A", "zona-leros", kind=MediaKind.MOVIE),
            make_media("Series A", "zona-leros", kind=MediaKind.SERIES),
        ],
    )
    book_provider = FakeProvider("lectulandia", [make_media("Book A", "lectulandia", kind=MediaKind.BOOK)])
    service = SearchService(providers={"zona-leros": mixed_provider, "lectulandia": book_provider})

    movies_and_series = service.search("query", kinds={MediaKind.MOVIE, MediaKind.SERIES})
    books = service.search("query", kinds={MediaKind.BOOK})

    assert {m.title for m in movies_and_series} == {"Movie A", "Series A"}
    assert [m.title for m in books] == ["Book A"]


def test_search_without_kinds_returns_everything():
    provider = FakeProvider(
        "a", [make_media("Movie A", "a", kind=MediaKind.MOVIE), make_media("Book A", "a", kind=MediaKind.BOOK)]
    )
    service = SearchService(providers={"a": provider})

    results = service.search("query")

    assert {m.title for m in results} == {"Movie A", "Book A"}


def test_search_skips_providers_whose_declared_kinds_dont_match():
    movies_provider = FakeProvider("zona-leros", [make_media("Movie A", "zona-leros")])
    movies_provider.kinds = {MediaKind.MOVIE, MediaKind.SERIES}
    book_provider = FakeProvider("lectulandia", [make_media("Book A", "lectulandia", kind=MediaKind.BOOK)])
    book_provider.kinds = {MediaKind.BOOK}
    service = SearchService(providers={"zona-leros": movies_provider, "lectulandia": book_provider})

    results = service.search("query", kinds={MediaKind.BOOK})

    assert [m.title for m in results] == ["Book A"]
    assert movies_provider.queries == []
    assert book_provider.queries == ["query"]


def test_search_still_queries_providers_without_a_declared_kinds_attribute():
    provider = FakeProvider("a", [make_media("Movie A", "a")])
    service = SearchService(providers={"a": provider})

    results = service.search("query", kinds={MediaKind.BOOK})

    assert provider.queries == ["query"]
    assert results == []


class FakeAuthorProvider(FakeProvider):
    def __init__(self, name: str, authors: list[Author], books: list[Media]):
        super().__init__(name, [])
        self.kinds = {MediaKind.BOOK}
        self._authors = authors
        self._books = books
        self.author_queries: list[str] = []
        self.requested_authors: list[Author] = []

    def search_authors(self, query: str) -> list[Author]:
        self.author_queries.append(query)
        return self._authors

    def get_author_books(self, author: Author) -> list[Media]:
        self.requested_authors.append(author)
        return self._books


def make_author(name: str, source: str = "lectulandia") -> Author:
    slug = name.lower().replace(" ", "-")
    return Author(id=slug, name=name, source=source, page_url=f"http://example.com/autor/{slug}/")


def test_search_authors_only_queries_providers_that_expose_authors():
    plain = FakeProvider("plain", [])
    plain.kinds = {MediaKind.BOOK}
    author_provider = FakeAuthorProvider("lectulandia", [make_author("Isaac Asimov")], [])
    service = SearchService(providers={"plain": plain, "lectulandia": author_provider})

    authors = service.search_authors("asimov", kinds={MediaKind.BOOK})

    assert [a.name for a in authors] == ["Isaac Asimov"]
    assert author_provider.author_queries == ["asimov"]
    assert plain.queries == []


def test_search_authors_skips_providers_whose_declared_kinds_dont_match():
    author_provider = FakeAuthorProvider("lectulandia", [make_author("Isaac Asimov")], [])
    service = SearchService(providers={"lectulandia": author_provider})

    assert service.search_authors("asimov", kinds={MediaKind.MOVIE}) == []
    assert author_provider.author_queries == []


def test_get_author_books_delegates_to_the_authors_provider():
    author = make_author("Isaac Asimov")
    books = [make_media("Yo, robot", "lectulandia", kind=MediaKind.BOOK)]
    author_provider = FakeAuthorProvider("lectulandia", [author], books)
    service = SearchService(providers={"lectulandia": author_provider})

    assert service.get_author_books(author) == books
    assert author_provider.requested_authors == [author]


def test_get_author_books_rejects_a_provider_without_author_pages():
    service = SearchService(providers={"plain": FakeProvider("plain", [])})

    with pytest.raises(TypeError):
        service.get_author_books(make_author("Isaac Asimov", source="plain"))


def test_exact_author_match_ignores_case_accents_and_extra_spaces():
    authors = [make_author("Stephen W. Hawking"), make_author("Stephen King"), make_author("Tabitha King")]

    assert exact_author_match(authors, "  stephen   KING ") == authors[1]
    assert exact_author_match([make_author("Gabriel García Márquez")], "gabriel garcia marquez") is not None


def test_exact_author_match_returns_none_without_a_single_exact_match():
    authors = [make_author("Isaac Asimov"), make_author("Janet Asimov")]

    assert exact_author_match(authors, "asimov") is None
    assert (
        exact_author_match([make_author("Isaac Asimov", "a"), make_author("Isaac Asimov", "b")], "isaac asimov") is None
    )
