from __future__ import annotations

from misflix.core.ports import SourceProvider

_PROVIDERS: dict[str, SourceProvider] = {}


def register(provider: SourceProvider) -> None:
    _PROVIDERS[provider.name] = provider


def get_provider(name: str) -> SourceProvider:
    try:
        return _PROVIDERS[name]
    except KeyError:
        raise ValueError(f"No hay un provider registrado con el nombre '{name}'") from None


def get_all_providers() -> dict[str, SourceProvider]:
    return dict(_PROVIDERS)


# Cada modulo de provider real debe registrarse aqui, ej.:
# from misflix.providers.repo_peliculas_x import PeliculasXProvider
# register(PeliculasXProvider())
