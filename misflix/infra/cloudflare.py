from __future__ import annotations

import time

from curl_cffi import BrowserTypeLiteral
from curl_cffi import requests as curl_requests
from curl_cffi.requests import Response

from misflix.infra.browser_cookies import load_domain_cookies
from misflix.infra.browser_launch import open_in_browser

DEFAULT_USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64; rv:152.0) Gecko/20100101 Firefox/152.0"
DEFAULT_IMPERSONATE: BrowserTypeLiteral = "firefox135"


class CloudflareBlockedError(RuntimeError):
    """Cloudflare sigue bloqueando la request despues de esperar la verificacion manual."""


class CloudflareHttpClient:
    """Cliente HTTP para sitios detras de un Cloudflare Managed Challenge.

    Playwright (headless o headed, con click manual incluido) queda atascado porque
    Cloudflare detecta la conexion CDP en si misma. La unica via estable es que un
    humano resuelva el checkbox una vez en su navegador normal (Zen/Firefox) y este
    cliente reutilice esa cookie `cf_clearance` con una huella TLS de navegador real
    (curl_cffi impersonate) en vez de la de httpx/OpenSSL, que Cloudflare tambien
    fingerprintea. Cuando la cookie ya no alcanza, abre el navegador del usuario en
    la misma URL y espera a que aparezca una cf_clearance nueva para reintentar sola.

    Attributes:
        domain: Dominio protegido cuyas cookies se reutilizan.
        user_agent: User-Agent a enviar en cada request.
        impersonate: Perfil de huella TLS de `curl_cffi` a imitar.
        timeout: Timeout en segundos por request.
        resolve_timeout: Tiempo maximo a esperar una `cf_clearance` nueva
            tras abrir el navegador.
        poll_interval: Cada cuanto revisar si ya aparecio la cookie nueva.
    """

    def __init__(
        self,
        domain: str,
        user_agent: str = DEFAULT_USER_AGENT,
        impersonate: BrowserTypeLiteral = DEFAULT_IMPERSONATE,
        timeout: float = 15.0,
        resolve_timeout: float = 180.0,
        poll_interval: float = 2.0,
    ):
        """Inicializa el cliente.

        Args:
            domain: Dominio protegido cuyas cookies se reutilizan.
            user_agent: User-Agent a enviar en cada request.
            impersonate: Perfil de huella TLS de `curl_cffi` a imitar.
            timeout: Timeout en segundos por request.
            resolve_timeout: Tiempo maximo a esperar una `cf_clearance`
                nueva tras abrir el navegador.
            poll_interval: Cada cuanto revisar si ya aparecio la cookie
                nueva.
        """
        self.domain = domain
        self.user_agent = user_agent
        self.impersonate = impersonate
        self.timeout = timeout
        self.resolve_timeout = resolve_timeout
        self.poll_interval = poll_interval

    def get(self, url: str) -> Response:
        """Pide `url`, escalando a resolucion manual si sale desafiado.

        Args:
            url: Url a pedir.

        Returns:
            La respuesta HTTP, ya sin desafio.

        Raises:
            CloudflareBlockedError: Si sigue desafiado despues de la
                verificacion manual.
            httpx.HTTPStatusError: Si la respuesta final no es 2xx.
        """
        response = self._request(url)
        if _is_challenge(response):
            self._resolve_via_browser(url)
            response = self._request(url)
            if _is_challenge(response):
                raise CloudflareBlockedError(f"Cloudflare sigue bloqueando {url} despues de la verificacion manual.")
        response.raise_for_status()
        return response

    def try_get(self, url: str) -> Response | None:
        """Como `get`, pero sin escalar a abrir el navegador si sale desafiado.

        Pensado para sondear si un link se puede resolver solo (algunos,
        sobre todo de episodios, son solo una cadena de redirects sin ningun
        desafio real de por medio) antes de comprometerse al paso manual;
        nunca dispara `_resolve_via_browser` por su cuenta, asi que no puede
        abrir una pestaña sin que el llamador lo decida.

        Args:
            url: Url a pedir.

        Returns:
            La respuesta HTTP, o None si salio desafiada o fallo la request.
        """
        try:
            response = self._request(url)
        except Exception:
            return None
        return None if _is_challenge(response) else response

    def _request(self, url: str) -> Response:
        """Hace un GET a `url` impersonando un navegador real.

        Args:
            url: Url a pedir.

        Returns:
            La respuesta HTTP cruda (puede venir desafiada).
        """
        cookies = load_domain_cookies(self.domain)
        return curl_requests.get(
            url,
            impersonate=self.impersonate,
            cookies=cookies,
            headers={"User-Agent": self.user_agent},
            timeout=self.timeout,
        )

    def _resolve_via_browser(self, url: str) -> None:
        """Abre `url` en el navegador del usuario y espera una `cf_clearance` nueva.

        Args:
            url: Url a abrir para que el usuario resuelva el desafio.

        Raises:
            CloudflareBlockedError: Si se agota `resolve_timeout` sin que
                aparezca una cookie nueva.
        """
        baseline = load_domain_cookies(self.domain).get("cf_clearance")
        print(f"[misflix] Cloudflare pide verificacion en {self.domain}. Abriendo el navegador...")
        open_in_browser(url)

        deadline = time.monotonic() + self.resolve_timeout
        while time.monotonic() < deadline:
            time.sleep(self.poll_interval)
            current = load_domain_cookies(self.domain).get("cf_clearance")
            if current and current != baseline:
                print("[misflix] Verificacion resuelta, continuando...")
                return

        raise CloudflareBlockedError(
            f"Se agoto el tiempo de espera ({self.resolve_timeout:.0f}s) esperando que se "
            f"resuelva la verificacion de Cloudflare para {self.domain}."
        )

    def close(self) -> None:
        """No-op: no hay conexion persistente que cerrar (cada request es suelta)."""


def _is_challenge(response: Response) -> bool:
    """Indica si `response` es un Cloudflare Managed Challenge sin resolver.

    Args:
        response: Respuesta HTTP a inspeccionar.

    Returns:
        True si la respuesta es un desafio de Cloudflare.
    """
    return response.headers.get("cf-mitigated") == "challenge" or response.status_code == 403
