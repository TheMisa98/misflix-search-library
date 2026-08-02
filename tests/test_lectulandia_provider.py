from __future__ import annotations

from pathlib import Path

from misflix.core.models import MediaKind
from misflix.providers.lectulandia import LectulandiaProvider

FIXTURES = Path(__file__).parent / "fixtures" / "lectulandia"


class FakeResponse:
    def __init__(self, text: str):
        self.text = text


class FakeHttpClient:
    def __init__(self, text: str):
        self._text = text
        self.requested_urls: list[str] = []

    def get(self, url: str) -> FakeResponse:
        self.requested_urls.append(url)
        return FakeResponse(self._text)


class FakeMultiPageHttpClient:
    def __init__(self, pages_by_url: dict[str, str]):
        self._pages_by_url = pages_by_url
        self.requested_urls: list[str] = []

    def get(self, url: str) -> FakeResponse:
        self.requested_urls.append(url)
        return FakeResponse(self._pages_by_url[url])


def test_search_returns_books():
    html = (FIXTURES / "search_results.html").read_text()
    provider = LectulandiaProvider(http_client=FakeHttpClient(html))

    results = provider.search("dune")

    assert [r.title for r in results] == ["Dune: La saga completa", "Dune", "El mesías de Dune"]
    assert all(r.kind == MediaKind.BOOK for r in results)
    assert all(r.source == "lectulandia" for r in results)


def test_search_parses_id_and_cover_from_result():
    html = (FIXTURES / "search_results.html").read_text()
    provider = LectulandiaProvider(http_client=FakeHttpClient(html))

    results = provider.search("dune")

    dune = next(r for r in results if r.title == "Dune")
    assert dune.id == "dune"
    assert dune.cover_url == "https://assets.lectulandia.com/b/Frank%20Herbert/Dune%20(4598)/small.jpg"
    assert dune.page_url == "https://ww3.lectulandia.com/book/dune/"


def test_search_parses_author_joining_multiple_names():
    html = (FIXTURES / "search_results.html").read_text()
    provider = LectulandiaProvider(http_client=FakeHttpClient(html))

    results = provider.search("dune")

    saga = next(r for r in results if r.title == "Dune: La saga completa")
    dune = next(r for r in results if r.title == "Dune")
    assert saga.author == "Brian Herbert, Frank Herbert"
    assert dune.author == "Frank Herbert"


def test_search_uses_the_query_in_the_url():
    html = (FIXTURES / "search_results.html").read_text()
    client = FakeHttpClient(html)
    provider = LectulandiaProvider(http_client=client)

    provider.search("juego de tronos")

    assert client.requested_urls == ["https://ww3.lectulandia.com/search/juego%20de%20tronos"]


def test_search_follows_the_matched_author_and_merges_their_books():
    client = FakeMultiPageHttpClient(
        {
            "https://ww3.lectulandia.com/search/peter%20watts": (
                FIXTURES / "search_results_with_author.html"
            ).read_text(),
            "https://ww3.lectulandia.com/autor/peter-watts/": (FIXTURES / "author_page.html").read_text(),
            "https://ww3.lectulandia.com/autor/peter-watts/page/2/": (FIXTURES / "author_page_2.html").read_text(),
        }
    )
    provider = LectulandiaProvider(http_client=client)

    results = provider.search("peter watts")

    assert [r.title for r in results] == ["Ad Astra", "Tiempo profundo", "Visión ciega"]
    assert client.requested_urls == [
        "https://ww3.lectulandia.com/search/peter%20watts",
        "https://ww3.lectulandia.com/autor/peter-watts/",
        "https://ww3.lectulandia.com/autor/peter-watts/page/2/",
    ]


def test_search_without_a_matched_author_does_not_request_extra_pages():
    html = (FIXTURES / "search_results.html").read_text()
    client = FakeHttpClient(html)
    provider = LectulandiaProvider(http_client=client)

    provider.search("dune")

    assert client.requested_urls == ["https://ww3.lectulandia.com/search/dune"]


def test_get_media_parses_title_and_cover():
    html = (FIXTURES / "book_detail.html").read_text()
    provider = LectulandiaProvider(http_client=FakeHttpClient(html))

    media = provider.get_media("dune")

    assert media.title == "Dune"
    assert media.kind == MediaKind.BOOK
    assert media.cover_url == "https://assets.lectulandia.com/b/Frank%20Herbert/Dune%20(4598)/big.jpg"
    assert media.page_url == "https://ww3.lectulandia.com/book/dune/"
    assert media.author == "Frank Herbert"
    assert media.synopsis == "Arrakis: un planeta desértico donde el agua es el bien más preciado."


def test_get_media_parses_synopsis_regardless_of_the_inner_markup():
    html = (FIXTURES / "book_detail_alt_synopsis.html").read_text()
    provider = LectulandiaProvider(http_client=FakeHttpClient(html))

    media = provider.get_media("ad-astra")

    assert media.synopsis == "Primera parte. Segunda parte."


def test_get_download_options_decodes_the_antupload_link_code():
    html = (FIXTURES / "book_detail.html").read_text()
    provider = LectulandiaProvider(http_client=FakeHttpClient(html))
    media = provider.get_media("dune")

    options = provider.get_download_options(media)

    assert [o.label for o in options] == ["EPUB", "PDF"]
    assert [o.extension for o in options] == [".epub", ".pdf"]
    assert options[0].url == "https://www.antupload.com/file/C4rEmj7f/"
    assert options[1].url == "https://www.antupload.com/file/cPyN05O8/"


def test_get_download_options_do_not_open_externally():
    html = (FIXTURES / "book_detail.html").read_text()
    provider = LectulandiaProvider(http_client=FakeHttpClient(html))
    media = provider.get_media("dune")

    options = provider.get_download_options(media)

    assert all(o.opens_externally is False for o in options)
