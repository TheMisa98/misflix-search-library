from __future__ import annotations

import re
import time
import typing

from curl_cffi import BrowserTypeLiteral
from curl_cffi import requests as curl_requests
from curl_cffi.requests import Response

from misflix.infra.browser_cookies import load_domain_cookies
from misflix.infra.browser_launch import open_in_browser
from misflix.infra.browser_version import detect_firefox_major_version

# Cloudflare ata la cookie `cf_clearance` al User-Agent EXACTO que resolvio el
# desafio, no a la huella TLS (verificado en vivo, ver docs/DECISIONS.md 2026-08-12):
# un cf_clearance valido para el Zen real (rv:153.0) seguia siendo desafiado via
# curl_cffi con un User-Agent de una version distinta, sin importar que tan reciente
# fuera el perfil de impersonate usado. Por eso el User-Agent se detecta en vivo
# contra la instalacion real (`browser_version.py`) en vez de pinearse a mano; el
# perfil de impersonate en cambio no necesita coincidir con esa version (la huella
# TLS de curl_cffi nunca replica al navegador real de todas formas) y simplemente usa
# el `firefoxNNN` mas nuevo que traiga la version instalada de curl_cffi.
_FALLBACK_FIREFOX_VERSION = "147"


def _newest_firefox_impersonate() -> BrowserTypeLiteral:
    """El perfil de impersonate `firefoxNNN` mas nuevo que trae `curl_cffi`.

    Returns:
        El perfil de Firefox mas nuevo disponible, para no quedar pineado a mano cada
        vez que `curl_cffi` agrega perfiles nuevos.
    """
    profiles = [p for p in typing.get_args(BrowserTypeLiteral) if re.fullmatch(r"firefox\d+", p)]
    newest = max(profiles, key=lambda p: int(p.removeprefix("firefox")))
    return typing.cast(BrowserTypeLiteral, newest)


def _default_user_agent() -> str:
    """User-Agent a juego con el Zen/Firefox real instalado.

    Returns:
        El User-Agent con la version mayor real detectada, o con
        `_FALLBACK_FIREFOX_VERSION` si no se pudo detectar la instalacion.
    """
    version = detect_firefox_major_version() or _FALLBACK_FIREFOX_VERSION
    return f"Mozilla/5.0 (X11; Linux x86_64; rv:{version}.0) Gecko/20100101 Firefox/{version}.0"


DEFAULT_USER_AGENT = _default_user_agent()
DEFAULT_IMPERSONATE: BrowserTypeLiteral = _newest_firefox_impersonate()


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
