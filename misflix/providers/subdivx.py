from __future__ import annotations

import html
import json
import re
import time
from collections.abc import Callable, Mapping
from typing import Protocol

from misflix.core.models import DownloadedFile, SubtitleCandidate
from misflix.infra.cloudflare import CloudflareHttpClient

BASE_URL = "https://subdivx.com"

_VERSION_RE = re.compile(r'id="vs"[^>]*>\s*([^<\s]+)\s*<')
_FILENAME_RE = re.compile(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)"?', re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")
_AJAX_HEADERS = {"X-Requested-With": "XMLHttpRequest", "Referer": f"{BASE_URL}/"}
# subdivx no publica un limite, pero el proceso en segundo plano puede hacer
# cientos de busquedas seguidas: se espacian los pedidos para no parecer un
# bot agresivo (y no gatillar un desafio de Cloudflare nuevo).
_MIN_SECONDS_BETWEEN_REQUESTS = 1.5


class _Response(Protocol):
    @property
    def text(self) -> str:
        """El cuerpo decodificado."""
        ...

    @property
    def content(self) -> bytes:
        """El cuerpo crudo."""
        ...

    @property
    def headers(self) -> Mapping[str, str | None]:
        """Headers de la respuesta."""
        ...


class FormHttpClient(Protocol):
    """Lo minimo que necesita `SubdivxProvider`: GET y POST de formulario.

    `CloudflareHttpClient` lo cumple; los tests inyectan un doble.
    """

    def get(self, url: str) -> _Response:
        """Pide `url`.

        Args:
            url: Url a pedir.

        Returns:
            La respuesta.
        """
        ...

    def post(self, url: str, data: Mapping[str, str], headers: Mapping[str, str] | None = None) -> _Response:
        """Postea `data` a `url`.

        Args:
            url: Url a la que postear.
            data: Campos del formulario.
            headers: Headers extra.

        Returns:
            La respuesta.
        """
        ...


class SubtitleSearchError(RuntimeError):
    """subdivx respondio algo inesperado (cambio el sitio, o un error del servidor)."""


class SubdivxProvider:
    """Provider de subtitulos para subdivx.com.

    El sitio esta detras del mismo Cloudflare Managed Challenge que
    zona-leros (verificado en vivo: 403 con `cf-mitigated: challenge` para
    un GET plano), asi que usa `CloudflareHttpClient`: reutiliza la
    `cf_clearance` del navegador del usuario y, si vencio, le abre el
    navegador para que la resuelva.

    La busqueda no esta en el HTML: el buscador del sitio (`js/index-min.js`)
    hace un POST a `/inc/ajax.php` con `tabla=resultados`, un campo
    `buscar<version>` — donde `<version>` es el texto de `#vs` en la pagina
    ("v4.53" -> `buscar453`) — y un `token` que se pide antes a
    `/inc/gt.php?gt=1`. Verificado en vivo que todo funciona sin sesion (la
    cookie `sdx` que pone el servidor no hace falta), asi que cada request
    va suelta como el resto de `CloudflareHttpClient`. La descarga es
    `/descargar.php?f=1&id=<id>`, que devuelve el .zip/.rar directo con el
    nombre en `Content-Disposition`.
    """

    name = "subdivx"

    def __init__(
        self,
        http_client: FormHttpClient | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ):
        """Inicializa el provider.

        Args:
            http_client: Cliente HTTP; por defecto uno de Cloudflare para
                `subdivx.com`.
            sleep: Funcion de espera (inyectable para tests).
            clock: Reloj monotono (inyectable para tests).
        """
        self.http: FormHttpClient = http_client or CloudflareHttpClient(domain="subdivx.com")
        self._sleep = sleep
        self._clock = clock
        self._last_request: float | None = None
        self._search_field: str | None = None

    def search(self, query: str) -> list[SubtitleCandidate]:
        """Busca subtitulos para `query`.

        Args:
            query: Texto de busqueda (ej. "House M.D. S01E01").

        Returns:
            Candidatos tal como los lista subdivx.

        Raises:
            SubtitleSearchError: Si la respuesta no tiene el formato esperado.
        """
        field = self._get_search_field()
        token = self._get_token()
        response = self._throttled(
            lambda: self.http.post(
                f"{BASE_URL}/inc/ajax.php",
                {"tabla": "resultados", "filtros": "", field: query, "token": token},
                _AJAX_HEADERS,
            )
        )
        try:
            rows = json.loads(response.text)["aaData"]
        except (ValueError, KeyError, TypeError) as exc:
            raise SubtitleSearchError(f"Respuesta inesperada de subdivx al buscar {query!r}.") from exc
        return [_candidate_from_row(row) for row in rows]

    def download(self, candidate: SubtitleCandidate) -> DownloadedFile:
        """Descarga el archivo de `candidate`.

        Args:
            candidate: Candidato devuelto por `search`.

        Returns:
            El .zip/.rar (o .srt suelto) tal cual lo sirve subdivx.
        """
        response = self._throttled(lambda: self.http.get(f"{BASE_URL}/descargar.php?f=1&id={candidate.id}"))
        match = _FILENAME_RE.search(response.headers.get("content-disposition") or "")
        filename = match.group(1).strip() if match else f"{candidate.id}.bin"
        return DownloadedFile(filename=filename, content=response.content)

    def _get_search_field(self) -> str:
        if self._search_field is None:
            home = self._throttled(lambda: self.http.get(f"{BASE_URL}/"))
            match = _VERSION_RE.search(home.text)
            if match is None:
                raise SubtitleSearchError("No se encontro la version del buscador (#vs) en la home de subdivx.")
            self._search_field = "buscar" + re.sub(r"[v.]", "", match.group(1))
        return self._search_field

    def _get_token(self) -> str:
        response = self._throttled(lambda: self.http.get(f"{BASE_URL}/inc/gt.php?gt=1"))
        try:
            return str(json.loads(response.text)["token"])
        except (ValueError, KeyError, TypeError) as exc:
            raise SubtitleSearchError("subdivx no devolvio un token de busqueda.") from exc

    def _throttled[T](self, request: Callable[[], T]) -> T:
        if self._last_request is not None:
            wait = _MIN_SECONDS_BETWEEN_REQUESTS - (self._clock() - self._last_request)
            if wait > 0:
                self._sleep(wait)
        try:
            return request()
        finally:
            self._last_request = self._clock()


def _candidate_from_row(row: Mapping[str, object]) -> SubtitleCandidate:
    description = _TAG_RE.sub(" ", html.unescape(str(row.get("descripcion") or "")))
    imdb = row.get("imdb")
    return SubtitleCandidate(
        id=str(row["id"]),
        source=SubdivxProvider.name,
        title=" ".join(html.unescape(str(row.get("titulo") or "")).split()),
        description=" ".join(description.split()),
        downloads=_as_int(row.get("descargas")),
        imdb_id=str(imdb) if imdb else None,
        uploader=str(row["nick"]) if row.get("nick") else None,
        files=max(1, _as_int(row.get("cds"))),
    )


def _as_int(value: object) -> int:
    try:
        return int(str(value))
    except ValueError:
        return 0
