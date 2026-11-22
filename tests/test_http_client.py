from __future__ import annotations

import httpx
import pytest

from misflix.infra.http_client import HttpClient


def _with_transport(monkeypatch, handler) -> None:
    real_client_cls = httpx.Client

    def fake_client(*, headers=None, timeout=None, follow_redirects=True):
        return real_client_cls(
            headers=headers, timeout=timeout, follow_redirects=follow_redirects, transport=httpx.MockTransport(handler)
        )

    monkeypatch.setattr(httpx, "Client", fake_client)


def test_get_returns_the_response_on_success(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="ok")

    _with_transport(monkeypatch, handler)

    response = HttpClient().get("https://example.com")

    assert response.status_code == 200
    assert response.text == "ok"


def test_get_raises_on_a_non_2xx_status(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    _with_transport(monkeypatch, handler)

    with pytest.raises(httpx.HTTPStatusError):
        HttpClient().get("https://example.com")


def test_close_closes_the_underlying_client(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200)

    _with_transport(monkeypatch, handler)

    client = HttpClient()
    client.close()

    assert client._client.is_closed


def test_get_retries_a_transient_status_until_it_succeeds(monkeypatch):
    statuses = iter([503, 503, 200])
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(next(statuses), text="ok")

    _with_transport(monkeypatch, handler)
    sleeps: list[float] = []

    response = HttpClient(backoff=1.0, sleep=sleeps.append).get("https://example.com")

    assert response.status_code == 200
    assert len(calls) == 3
    assert sleeps == [1.0, 2.0]


def test_get_gives_up_after_exhausting_the_retries(monkeypatch):
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(503)

    _with_transport(monkeypatch, handler)

    with pytest.raises(httpx.HTTPStatusError):
        HttpClient(retries=2, sleep=lambda _: None).get("https://example.com")
    assert len(calls) == 3


def test_get_does_not_retry_a_non_transient_error(monkeypatch):
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(404)

    _with_transport(monkeypatch, handler)

    with pytest.raises(httpx.HTTPStatusError):
        HttpClient(sleep=lambda _: pytest.fail("no deberia esperar")).get("https://example.com")
    assert len(calls) == 1
