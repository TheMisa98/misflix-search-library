from __future__ import annotations

import httpx

# User-Agent de navegador de escritorio comun, compartido por todos los
# clientes HTTP del proyecto (infra/imdb.py, infra/antupload.py,
# infra/mediafire.py) para no repetir el mismo dict en cada modulo. A
# diferencia de infra/cloudflare.py, estos sitios no estan detras de
# Cloudflare y no atan ninguna cookie a este valor exacto - alcanza con que
# sea un User-Agent de navegador real y no demasiado viejo, sin necesidad de
# detectarlo en vivo. Bump manual ocasional (sin fecha fija): la version de
# Chrome de abajo se tomo de los perfiles de impersonate que trae curl_cffi
# (ver infra/cloudflare.py) como referencia de "version reciente y real",
# aunque ningun impersonate se use aca (es httpx plano).
DEFAULT_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0 Safari/537.36")
}


class HttpClient:
    """Wrapper delgado sobre httpx con headers y timeouts por defecto."""

    def __init__(self, timeout: float = 15.0):
        """Inicializa el cliente.

        Args:
            timeout: Timeout en segundos para cada request.
        """
        self._client = httpx.Client(headers=DEFAULT_HEADERS, timeout=timeout, follow_redirects=True)

    def get(self, url: str) -> httpx.Response:
        """Hace un GET a `url` y valida el status code.

        Args:
            url: Url a pedir.

        Returns:
            La respuesta HTTP.

        Raises:
            httpx.HTTPStatusError: Si la respuesta no es 2xx.
        """
        response = self._client.get(url)
        response.raise_for_status()
        return response

    def close(self) -> None:
        """Cierra la conexion subyacente."""
        self._client.close()
