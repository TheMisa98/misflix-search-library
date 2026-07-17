from __future__ import annotations

import httpx
import pytest

from misflix.infra import antupload


def test_is_antupload_url():
    assert antupload.is_antupload_url("https://www.antupload.com/file/abc123/")
    assert not antupload.is_antupload_url("https://www.mediafire.com/file/abc/movie.rar/file")


def _with_client(monkeypatch, handler) -> None:
    original_client = httpx.Client
    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kwargs: original_client(transport=httpx.MockTransport(handler), **kwargs),
    )


def test_download_follows_the_download_button_with_a_referer(tmp_path, monkeypatch):
    page_url = "https://www.antupload.com/file/abc123/"
    requests_seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests_seen.append(request)
        if str(request.url) == page_url:
            return httpx.Response(
                200,
                text='<html><body><a id="downloadB" href="/filed/abc123/My+Book">DOWNLOAD</a></body></html>',
            )
        return httpx.Response(200, content=b"epub bytes", headers={"Content-Length": "10"})

    _with_client(monkeypatch, handler)
    dest = tmp_path / "My Book.epub"

    antupload.download(page_url, dest)

    assert dest.read_bytes() == b"epub bytes"
    filed_request = next(r for r in requests_seen if "/filed/" in str(r.url))
    assert filed_request.headers["referer"] == page_url


def test_download_reports_progress(tmp_path, monkeypatch):
    page_url = "https://www.antupload.com/file/abc123/"

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == page_url:
            return httpx.Response(
                200,
                text='<html><body><a id="downloadB" href="/filed/abc123/My+Book">DOWNLOAD</a></body></html>',
            )
        return httpx.Response(200, content=b"x" * 100, headers={"Content-Length": "100"})

    _with_client(monkeypatch, handler)
    dest = tmp_path / "My Book.epub"
    calls: list[tuple[int, int]] = []

    antupload.download(page_url, dest, on_progress=lambda d, t: calls.append((d, t)))

    assert calls
    assert calls[-1] == (100, 100)


def test_download_raises_when_the_download_button_is_missing(tmp_path, monkeypatch):
    page_url = "https://www.antupload.com/file/abc123/"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html><body>file not found</body></html>")

    _with_client(monkeypatch, handler)
    dest = tmp_path / "My Book.epub"

    with pytest.raises(antupload.AntuploadResolveError):
        antupload.download(page_url, dest)


def test_download_raises_when_the_page_is_unreachable(tmp_path, monkeypatch):
    page_url = "https://www.antupload.com/file/abc123/"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="not found")

    _with_client(monkeypatch, handler)
    dest = tmp_path / "My Book.epub"

    with pytest.raises(antupload.AntuploadResolveError):
        antupload.download(page_url, dest)


def test_download_removes_partial_file_when_the_stream_fails(tmp_path, monkeypatch):
    page_url = "https://www.antupload.com/file/abc123/"

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == page_url:
            return httpx.Response(
                200,
                text='<html><body><a id="downloadB" href="/filed/abc123/My+Book">DOWNLOAD</a></body></html>',
            )
        return httpx.Response(500, text="server error")

    _with_client(monkeypatch, handler)
    dest = tmp_path / "My Book.epub"
    dest.write_bytes(b"partial")

    with pytest.raises(antupload.AntuploadResolveError):
        antupload.download(page_url, dest)

    assert not dest.exists()
