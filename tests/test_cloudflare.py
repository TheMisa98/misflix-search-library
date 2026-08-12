from __future__ import annotations

import re

from misflix.infra.cloudflare import DEFAULT_IMPERSONATE, DEFAULT_USER_AGENT, CloudflareHttpClient


def test_default_user_agent_and_impersonate_name_the_same_firefox_version():
    ua_version = re.search(r"Firefox/(\d+)\.0", DEFAULT_USER_AGENT)
    impersonate_version = re.search(r"firefox(\d+)", DEFAULT_IMPERSONATE)

    assert ua_version is not None
    assert impersonate_version is not None
    assert ua_version.group(1) == impersonate_version.group(1)


class FakeResponse:
    def __init__(self, status_code: int, url: str = "https://example.com", headers: dict | None = None):
        self.status_code = status_code
        self.url = url
        self.headers = headers or {}

    def raise_for_status(self) -> None:
        pass


def test_try_get_returns_response_without_opening_browser_on_success(monkeypatch):
    client = CloudflareHttpClient(domain="zona-leros.com")
    monkeypatch.setattr(client, "_request", lambda url: FakeResponse(200, url="https://www.mediafire.com/x"))
    opened = []
    monkeypatch.setattr("misflix.infra.cloudflare.open_in_browser", opened.append)

    response = client.try_get("https://anomizador.zona-leros.com/l?hs=abc")

    assert response is not None
    assert response.url == "https://www.mediafire.com/x"
    assert opened == []


def test_try_get_returns_none_on_challenge_without_opening_browser(monkeypatch):
    client = CloudflareHttpClient(domain="zona-leros.com")
    monkeypatch.setattr(client, "_request", lambda url: FakeResponse(403))
    opened = []
    monkeypatch.setattr("misflix.infra.cloudflare.open_in_browser", opened.append)

    assert client.try_get("https://anomizador.zona-leros.com/l?hs=abc") is None
    assert opened == []


def test_try_get_returns_none_on_request_error(monkeypatch):
    client = CloudflareHttpClient(domain="zona-leros.com")

    def raise_error(url):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(client, "_request", raise_error)

    assert client.try_get("https://anomizador.zona-leros.com/l?hs=abc") is None
