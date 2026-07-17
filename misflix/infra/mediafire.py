from __future__ import annotations

import httpx
from bs4 import BeautifulSoup

from misflix.infra.http_client import DEFAULT_HEADERS


class MediaFireResolveError(RuntimeError):
    """No se encontro el boton de descarga en la pagina de Mediafire."""


def is_mediafire_url(url: str) -> bool:
    """Indica si `url` apunta a mediafire.com.

    Args:
        url: Url a revisar.

    Returns:
        True si es un link de mediafire.com.
    """
    return "mediafire.com" in url


def resolve_direct_url(page_url: str) -> str:
    """Resuelve la pagina de un archivo de Mediafire a su link directo de descarga.

    Mediafire no esta detras de Cloudflare: la pagina del archivo trae el
    link directo (`download*.mediafire.com`) en el boton `#downloadButton`.

    Args:
        page_url: Url de la pagina del archivo en Mediafire.

    Returns:
        El link directo de descarga.

    Raises:
        MediaFireResolveError: Si el link esta caido (dominio caido, timeout,
            404) o el archivo ya no esta disponible (la pagina carga pero sin
            boton de descarga — Mediafire la reemplaza por un aviso de "file
            no longer available"). Se tira la misma excepcion en los dos
            casos para que el llamador solo tenga que manejar una.
    """
    try:
        with httpx.Client(headers=DEFAULT_HEADERS, timeout=15.0, follow_redirects=True) as client:
            response = client.get(page_url)
            response.raise_for_status()
    except httpx.HTTPError as exc:
        raise MediaFireResolveError(f"No se pudo acceder a {page_url}: {exc}") from exc

    soup = BeautifulSoup(response.text, "lxml")
    button = soup.select_one("a#downloadButton[href]")
    if not button:
        raise MediaFireResolveError(
            f"El link parece caido o el archivo ya no esta disponible en Mediafire: {page_url}"
        )
    return button["href"]
