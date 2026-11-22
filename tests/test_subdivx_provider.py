from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

import pytest

from misflix.core.models import SubtitleCandidate
from misflix.core.ports import SubtitleProvider
from misflix.providers.subdivx import SubdivxProvider, SubtitleSearchError

FIXTURES = Path(__file__).parent / "fixtures" / "subdivx"


class FakeResponse:
    def __init__(self, text: str = "", content: bytes = b"", headers: Mapping[str, str] | None = None):
        self.text = text
        self.content = content
        self.headers = dict(headers or {})


class FakeHttp:
    def __init__(self, gets: Mapping[str, FakeResponse], post: FakeResponse | None = None):
        self._gets = gets
        self._post = post
        self.calls: list[tuple] = []

    def get(self, url: str) -> FakeResponse:
        self.calls.append(("GET", url))
        return self._gets[url]

    def post(self, url: str, data: Mapping[str, str], headers: Mapping[str, str] | None = None) -> FakeResponse:
        self.calls.append(("POST", url, dict(data), dict(headers or {})))
        assert self._post is not None
        return self._post


def make_provider(search_fixture: str = "search_house_s01e01.json") -> tuple[SubdivxProvider, FakeHttp]:
    http = FakeHttp(
        gets={
            "https://subdivx.com/": FakeResponse((FIXTURES / "home.html").read_text()),
            "https://subdivx.com/inc/gt.php?gt=1": FakeResponse((FIXTURES / "gt.json").read_text()),
        },
        post=FakeResponse((FIXTURES / search_fixture).read_text()),
    )
    return SubdivxProvider(http_client=http, sleep=lambda _: None), http


def test_provider_implements_the_subtitle_provider_protocol():
    assert isinstance(make_provider()[0], SubtitleProvider)


def test_search_posts_the_versioned_field_and_the_token():
    provider, http = make_provider()

    provider.search("house s01e01")

    method, url, data, headers = http.calls[-1]
    assert (method, url) == ("POST", "https://subdivx.com/inc/ajax.php")
    assert data == {
        "tabla": "resultados",
        "filtros": "",
        "buscar453": "house s01e01",
        "token": "7959d4e99832504718d94af9e353ad7a58b88b8409d4c9920cdf1a6d75c259c6",
    }
    assert headers["X-Requested-With"] == "XMLHttpRequest"


def test_search_maps_rows_to_candidates_unescaping_the_description():
    provider, _ = make_provider()

    results = provider.search("house s01e01")

    filmhd = next(r for r in results if r.id == "625665")
    assert filmhd == SubtitleCandidate(
        id="625665",
        source="subdivx",
        title="House M.D. S01E01",
        description="Sincronizado para la version bluray.x264-filmhd",
        downloads=219,
        imdb_id=None,
        uploader=filmhd.uploader,
        files=1,
    )
    tjhd = next(r for r in results if r.id == "234271")
    assert '"House.M.D.S01E01.Pilot.720p.WEB-DL.DD5.1.h.264-TjHD.mkv' in tjhd.description
    assert "<center>" not in tjhd.description
    # la busqueda es por substring: trae otras series, filtrar es trabajo de matching
    assert {r.title for r in results} >= {"House of Cards (2013) S01E01", "Bleak House S01E01"}


def test_search_reads_the_imdb_id_when_present():
    provider, _ = make_provider("search_allied_2016.json")

    results = provider.search("allied 2016")

    assert {r.imdb_id for r in results} >= {"tt3640424"}


def test_search_field_is_fetched_once_and_the_token_every_time():
    provider, http = make_provider()

    provider.search("a")
    provider.search("b")

    gets = [call[1] for call in http.calls if call[0] == "GET"]
    assert gets.count("https://subdivx.com/") == 1
    assert gets.count("https://subdivx.com/inc/gt.php?gt=1") == 2


def test_requests_are_spaced_out():
    waits: list[float] = []
    now = [100.0]
    http = make_provider()[1]
    provider = SubdivxProvider(http_client=http, sleep=waits.append, clock=lambda: now[0])

    provider.search("a")

    # home -> token -> post, sin que el reloj avance: cada uno espera el minimo entero
    assert waits == [1.5, 1.5]


def test_search_raises_on_an_unexpected_response():
    provider, http = make_provider()
    http._post = FakeResponse("<html>error</html>")

    with pytest.raises(SubtitleSearchError):
        provider.search("x")


def test_search_raises_when_the_page_has_no_version():
    http = FakeHttp(gets={"https://subdivx.com/": FakeResponse("<html></html>")})
    provider = SubdivxProvider(http_client=http, sleep=lambda _: None)

    with pytest.raises(SubtitleSearchError):
        provider.search("x")


def test_download_uses_the_content_disposition_filename():
    http = FakeHttp(
        gets={
            "https://subdivx.com/descargar.php?f=1&id=625665": FakeResponse(
                content=b"Rar!",
                headers={"content-disposition": 'attachment; filename="House M.D. S01E01 - [x] [625665].rar"'},
            )
        }
    )
    provider = SubdivxProvider(http_client=http, sleep=lambda _: None)
    candidate = SubtitleCandidate(id="625665", source="subdivx", title="t", description="", downloads=0)

    downloaded = provider.download(candidate)

    assert downloaded.filename == "House M.D. S01E01 - [x] [625665].rar"
    assert downloaded.content == b"Rar!"


def test_download_falls_back_to_the_id_without_content_disposition():
    http = FakeHttp(gets={"https://subdivx.com/descargar.php?f=1&id=1": FakeResponse(content=b"x")})
    provider = SubdivxProvider(http_client=http, sleep=lambda _: None)

    downloaded = provider.download(SubtitleCandidate(id="1", source="subdivx", title="t", description="", downloads=0))

    assert downloaded.filename == "1.bin"


class VersionChangingHttp(FakeHttp):
    """subdivx actualiza el sitio en medio de una pasada (verificado en vivo: v4.53 -> v4.61)."""

    def __init__(self):
        super().__init__(gets={})
        self.homes = iter(['<div id="vs">v4.53</div>', '<div id="vs">v4.61</div>'])

    def get(self, url: str) -> FakeResponse:
        self.calls.append(("GET", url))
        if url == "https://subdivx.com/":
            return FakeResponse(next(self.homes))
        return FakeResponse('{"token": "t"}')

    def post(self, url, data, headers=None) -> FakeResponse:
        self.calls.append(("POST", url, dict(data), dict(headers or {})))
        if "buscar461" in data:
            return FakeResponse((FIXTURES / "search_house_s01e01.json").read_text())
        return FakeResponse("<html>Error</html>")


def test_search_rereads_the_site_version_when_the_response_is_unexpected():
    http = VersionChangingHttp()
    provider = SubdivxProvider(http_client=http, sleep=lambda _: None)

    results = provider.search("house s01e01")

    assert results
    posts = [call[2] for call in http.calls if call[0] == "POST"]
    assert ["buscar453" in p for p in posts] == [True, False]
    assert "buscar461" in posts[1]


def test_network_errors_become_provider_errors():
    from misflix.core.ports import SubtitleProviderError

    class BrokenHttp(FakeHttp):
        def get(self, url):
            raise OSError("HTTP Error 404")

    provider = SubdivxProvider(http_client=BrokenHttp(gets={}), sleep=lambda _: None)
    candidate = SubtitleCandidate(id="1", source="subdivx", title="t", description="", downloads=0)

    with pytest.raises(SubtitleProviderError, match="404"):
        provider.download(candidate)
