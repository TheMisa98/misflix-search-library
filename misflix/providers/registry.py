from __future__ import annotations

from misflix.core.ports import SourceProvider

_PROVIDERS: dict[str, SourceProvider] = {}


def register(provider: SourceProvider) -> None:
    """Registra `provider` bajo su propio `name`, reemplazando uno previo si existia.

    Args:
        provider: Provider a registrar.
    """
    _PROVIDERS[provider.name] = provider


def get_provider(name: str) -> SourceProvider:
    """Busca un provider registrado por nombre.

    Args:
        name: Nombre del provider (`provider.name`).

    Returns:
        El provider registrado.

    Raises:
        ValueError: Si no hay ningun provider registrado con ese nombre.
    """
    try:
        return _PROVIDERS[name]
    except KeyError:
        raise ValueError(f"No hay un provider registrado con el nombre '{name}'") from None


def get_all_providers() -> dict[str, SourceProvider]:
    """Todos los providers registrados.

    Returns:
        Copia del diccionario nombre -> provider (mutarlo no afecta el
        registro real).
    """
    return dict(_PROVIDERS)


def _register_builtin_providers() -> None:
    """Registra los providers que trae el proyecto de fabrica."""
    from misflix.providers.lectulandia import LectulandiaProvider
    from misflix.providers.zona_leros import ZonaLerosProvider

    register(ZonaLerosProvider())
    register(LectulandiaProvider())


_register_builtin_providers()
