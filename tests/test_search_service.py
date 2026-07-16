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


def make_media(title: str, source: str) -> Media:
    return Media(id=title, title=title, kind=MediaKind.MOVIE, source=source, page_url="http://example.com")


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
