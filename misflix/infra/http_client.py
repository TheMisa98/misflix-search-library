from __future__ import annotations

import time
from collections.abc import Callable

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


# Status que suelen ser pasajeros (rate limit / sobrecarga del origen) y
# vale la pena reintentar. Verificado en vivo: recorrer seguido las 9
# paginas de /autor/isaac-asimov/ en lectulandia devolvio 503 en la novena,
# y la misma url dio 200 al reintentarla un par de segundos despues.
_RETRYABLE_STATUSES = frozenset({429, 502, 503, 504})


class HttpClient:
    """Wrapper delgado sobre httpx con headers, timeouts y reintentos por defecto."""

    def __init__(
        self,
        timeout: float = 15.0,
        retries: int = 3,
        backoff: float = 1.0,
        sleep: Callable[[float], None] = time.sleep,
    ):
        """Inicializa el cliente.

        Args:
            timeout: Timeout en segundos para cada request.
            retries: Reintentos maximos ante un status pasajero (429/5xx de
                gateway), ademas del intento original.
            backoff: Espera base en segundos; se duplica en cada reintento.
            sleep: Funcion de espera (inyectable para tests).
        """
        self._client = httpx.Client(headers=DEFAULT_HEADERS, timeout=timeout, follow_redirects=True)
        self._retries = retries
        self._backoff = backoff
        self._sleep = sleep

    def get(self, url: str) -> httpx.Response:
        """Hace un GET a `url`, reintentando status pasajeros, y valida el status code.

        Args:
            url: Url a pedir.

        Returns:
            La respuesta HTTP.

        Raises:
            httpx.HTTPStatusError: Si la respuesta no es 2xx (tras agotar
                los reintentos, si el status era reintentable).
        """
        response = self._client.get(url)
        for attempt in range(self._retries):
            if response.status_code not in _RETRYABLE_STATUSES:
                break
            self._sleep(self._backoff * 2**attempt)
            response = self._client.get(url)
        response.raise_for_status()
        return response

    def close(self) -> None:
        """Cierra la conexion subyacente."""
        self._client.close()
