from __future__ import annotations

from collections.abc import Callable

from misflix.core.ports import SourceProvider, SubtitleProvider

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


def get_subtitle_provider(on_challenge: Callable[[str], None] | None = None) -> SubtitleProvider:
    """El repo de subtitulos a usar (hoy, subdivx).

    Args:
        on_challenge: Se llama con el dominio cuando Cloudflare pide
            verificacion y se va a abrir el navegador (ej. para notificar
            desde el proceso en segundo plano).

    Returns:
        El provider de subtitulos.
    """
    from misflix.infra.cloudflare import CloudflareHttpClient
    from misflix.providers.subdivx import SubdivxProvider

    return SubdivxProvider(CloudflareHttpClient(domain="subdivx.com", on_challenge=on_challenge))


def _register_builtin_providers() -> None:
    """Registra los providers que trae el proyecto de fabrica."""
    from misflix.providers.lectulandia import LectulandiaProvider
    from misflix.providers.zona_leros import ZonaLerosProvider

    register(ZonaLerosProvider())
    register(LectulandiaProvider())


_register_builtin_providers()
