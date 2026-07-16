from __future__ import annotations

import pytest

from misflix.providers import registry


@pytest.fixture(autouse=True)
def clean_registry():
    registry._PROVIDERS.clear()
    yield
    registry._PROVIDERS.clear()


class FakeProvider:
    def __init__(self, name: str):
        self.name = name


def test_register_and_get_provider():
    provider = FakeProvider("repo-x")
    registry.register(provider)

    assert registry.get_provider("repo-x") is provider


def test_get_unknown_provider_raises_value_error():
    with pytest.raises(ValueError, match="repo-x"):
        registry.get_provider("repo-x")


def test_get_all_providers_returns_a_copy():
    registry.register(FakeProvider("repo-x"))

    all_providers = registry.get_all_providers()
    all_providers["repo-y"] = FakeProvider("repo-y")

    assert "repo-y" not in registry.get_all_providers()
