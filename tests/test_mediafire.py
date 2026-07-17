from __future__ import annotations

import httpx
import pytest

from misflix.infra import mediafire


def test_is_mediafire_url():
    assert mediafire.is_mediafire_url("https://www.mediafire.com/file/abc/movie.rar/file")
    assert not mediafire.is_mediafire_url("https://anomizador.zona-leros.com/l?hs=x")


def test_resolve_direct_url_extracts_download_button_href(monkeypatch):
    html = """
    <html><body>
      <a href="https://download1581.mediafire.com/abc/movie.part1.rar"
         id="downloadButton" rel="nofollow">Download (4.99GB)</a>
    </body></html>
    """

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=html)

    original_client = httpx.Client
    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kwargs: original_client(transport=httpx.MockTransport(handler), **kwargs),
    )

    direct_url = mediafire.resolve_direct_url("https://www.mediafire.com/file/abc/movie.part1.rar/file")

    assert direct_url == "https://download1581.mediafire.com/abc/movie.part1.rar"


def test_resolve_direct_url_raises_when_button_missing(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html><body>no button here</body></html>")

    original_client = httpx.Client
    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kwargs: original_client(transport=httpx.MockTransport(handler), **kwargs),
    )

    with pytest.raises(mediafire.MediaFireResolveError):
        mediafire.resolve_direct_url("https://www.mediafire.com/file/abc/movie.rar/file")


def test_resolve_direct_url_raises_mediafire_error_on_http_status_error(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="not found")

    original_client = httpx.Client
    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kwargs: original_client(transport=httpx.MockTransport(handler), **kwargs),
    )

    with pytest.raises(mediafire.MediaFireResolveError):
        mediafire.resolve_direct_url("https://www.mediafire.com/file/abc/movie.rar/file")


def test_resolve_direct_url_raises_mediafire_error_on_connection_failure(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    original_client = httpx.Client
    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kwargs: original_client(transport=httpx.MockTransport(handler), **kwargs),
    )

    with pytest.raises(mediafire.MediaFireResolveError):
        mediafire.resolve_direct_url("https://www.mediafire.com/file/abc/movie.rar/file")
