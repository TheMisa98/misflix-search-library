from __future__ import annotations

import pytest

from misflix.core.models import Media, MediaKind
from misflix.core.services.search_service import SearchService


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
