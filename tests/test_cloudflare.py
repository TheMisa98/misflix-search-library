from __future__ import annotations

import re

import pytest

from misflix.infra.cloudflare import (
    DEFAULT_IMPERSONATE,
    DEFAULT_USER_AGENT,
    CloudflareBlockedError,
    CloudflareHttpClient,
    _default_user_agent,
    _newest_firefox_impersonate,
)


def test_default_user_agent_matches_the_real_installed_firefox_version(monkeypatch):
    monkeypatch.setattr("misflix.infra.cloudflare.detect_firefox_major_version", lambda: "153")

    assert _default_user_agent() == "Mozilla/5.0 (X11; Linux x86_64; rv:153.0) Gecko/20100101 Firefox/153.0"


def test_default_user_agent_falls_back_when_version_not_detected(monkeypatch):
    monkeypatch.setattr("misflix.infra.cloudflare.detect_firefox_major_version", lambda: None)

    assert "Firefox/147.0" in _default_user_agent()


def test_default_impersonate_is_the_newest_firefox_profile_available():
    assert re.fullmatch(r"firefox\d+", _newest_firefox_impersonate())


def test_module_level_defaults_are_set():
    assert re.search(r"Firefox/\d+\.0", DEFAULT_USER_AGENT)
    assert re.fullmatch(r"firefox\d+", DEFAULT_IMPERSONATE)


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


def test_post_returns_the_response_when_not_challenged(monkeypatch):
    client = CloudflareHttpClient(domain="example.com")
    calls = []

    def fake_post(url, data, headers):
        calls.append((url, data, headers))
        return FakeResponse(200)

    monkeypatch.setattr(client, "_post_request", fake_post)

    response = client.post("https://example.com/inc/ajax.php", {"q": "x"}, {"X-Requested-With": "XMLHttpRequest"})

    assert response.status_code == 200
    assert calls == [("https://example.com/inc/ajax.php", {"q": "x"}, {"X-Requested-With": "XMLHttpRequest"})]


def test_post_resolves_the_challenge_on_the_site_root_and_retries(monkeypatch):
    client = CloudflareHttpClient(domain="example.com")
    responses = iter([FakeResponse(403), FakeResponse(200)])
    monkeypatch.setattr(client, "_post_request", lambda url, data, headers: next(responses))
    resolved: list[str] = []
    monkeypatch.setattr(client, "_resolve_via_browser", resolved.append)

    response = client.post("https://example.com/inc/ajax.php", {})

    assert response.status_code == 200
    assert resolved == ["https://example.com/"]


def test_post_raises_when_still_challenged_after_manual_resolution(monkeypatch):
    client = CloudflareHttpClient(domain="example.com")
    monkeypatch.setattr(client, "_post_request", lambda url, data, headers: FakeResponse(403))
    monkeypatch.setattr(client, "_resolve_via_browser", lambda url: None)

    with pytest.raises(CloudflareBlockedError):
        client.post("https://example.com/inc/ajax.php", {})
