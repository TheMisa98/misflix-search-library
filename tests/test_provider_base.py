from __future__ import annotations

from misflix.core.models import DownloadOption, Media
from misflix.infra.http_client import HttpClient
from misflix.providers.base import StaticProvider


class _FakeHttpClient:
    def get(self, url: str):
        raise NotImplementedError


class _FakeStaticProvider(StaticProvider):
    name = "fake"

    def search(self, query: str) -> list[Media]:
        return []

    def get_media(self, media_id: str) -> Media:
        raise NotImplementedError

    def get_download_options(self, media: Media) -> list[DownloadOption]:
        return []


def test_static_provider_defaults_to_http_client_when_none_given():
    provider = _FakeStaticProvider()

    assert isinstance(provider.http, HttpClient)


def test_static_provider_uses_the_given_http_client():
    fake_http = _FakeHttpClient()

    provider = _FakeStaticProvider(fake_http)

    assert provider.http is fake_http
