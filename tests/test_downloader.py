from __future__ import annotations

import httpx
import pytest

from misflix.infra.downloader import DownloadError, HttpxDownloader


def _with_transport(monkeypatch, handler) -> None:
    def fake_stream(method, url, follow_redirects=True, timeout=None, headers=None):
        client = httpx.Client(transport=httpx.MockTransport(handler))
        return client.stream(method, url, follow_redirects=follow_redirects, headers=headers)

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


def test_download_raises_download_error_on_http_status_error(tmp_path, monkeypatch):
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


def test_download_resumes_from_existing_partial_file_with_range_header(tmp_path, monkeypatch):
    dest = tmp_path / "movie.rar"
    dest.write_bytes(b"hello ")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Range"] == "bytes=6-"
        return httpx.Response(206, content=b"world", headers={"Content-Length": "5"})

    _with_transport(monkeypatch, handler)
    calls: list[tuple[int, int]] = []

    HttpxDownloader().download("https://example.com/movie.rar", dest, on_progress=lambda d, t: calls.append((d, t)))

    assert dest.read_bytes() == b"hello world"
    assert calls[-1] == (11, 11)


def test_download_treats_416_as_already_complete(tmp_path, monkeypatch):
    dest = tmp_path / "movie.rar"
    dest.write_bytes(b"hello world")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(416)

    _with_transport(monkeypatch, handler)
    calls: list[tuple[int, int]] = []

    HttpxDownloader().download("https://example.com/movie.rar", dest, on_progress=lambda d, t: calls.append((d, t)))

    assert dest.read_bytes() == b"hello world"
    assert calls == [(11, 11)]


def test_download_restarts_from_scratch_when_server_ignores_range(tmp_path, monkeypatch):
    dest = tmp_path / "movie.rar"
    dest.write_bytes(b"stale partial")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"fresh full file", headers={"Content-Length": "15"})

    _with_transport(monkeypatch, handler)

    HttpxDownloader().download("https://example.com/movie.rar", dest)

    assert dest.read_bytes() == b"fresh full file"


def test_download_keeps_partial_file_on_error_mid_stream(tmp_path, monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        def body():
            yield b"partial chunk"
            raise httpx.ReadTimeout("timed out", request=request)

        return httpx.Response(200, content=body())

    _with_transport(monkeypatch, handler)
    dest = tmp_path / "movie.rar"

    with pytest.raises(DownloadError):
        HttpxDownloader().download("https://example.com/movie.rar", dest)

    assert dest.read_bytes() == b"partial chunk"
