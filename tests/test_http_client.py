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
