from __future__ import annotations

import httpx
import pytest

from misflix.infra.downloader import DownloadError, HttpxDownloader


def _with_transport(monkeypatch, handler) -> None:
    def fake_stream(method, url, follow_redirects=True):
        client = httpx.Client(transport=httpx.MockTransport(handler))
        return client.stream(method, url, follow_redirects=follow_redirects)

    monkeypatch.setattr(httpx, "stream", fake_stream)


def test_download_writes_response_bytes(tmp_path, monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"hello world")

    _with_transport(monkeypatch, handler)
    dest = tmp_path / "movie.rar"

    HttpxDownloader().download("https://example.com/movie.rar", dest)

    assert dest.read_bytes() == b"hello world"


def test_download_reports_progress(tmp_path, monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"x" * 100, headers={"Content-Length": "100"})

    _with_transport(monkeypatch, handler)
    dest = tmp_path / "movie.rar"
    calls: list[tuple[int, int]] = []

    HttpxDownloader().download("https://example.com/movie.rar", dest, on_progress=lambda d, t: calls.append((d, t)))

    assert calls
    assert calls[-1] == (100, 100)


def test_download_raises_download_error_and_removes_partial_file_on_http_status_error(tmp_path, monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="not found")

    _with_transport(monkeypatch, handler)
    dest = tmp_path / "movie.rar"

    with pytest.raises(DownloadError):
        HttpxDownloader().download("https://example.com/movie.rar", dest)

    assert not dest.exists()


def test_download_raises_download_error_on_connection_failure(tmp_path, monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    _with_transport(monkeypatch, handler)
    dest = tmp_path / "movie.rar"

    with pytest.raises(DownloadError):
        HttpxDownloader().download("https://example.com/movie.rar", dest)

    assert not dest.exists()
