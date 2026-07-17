from __future__ import annotations

from pathlib import Path
from typing import Callable
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from misflix.infra.http_client import DEFAULT_HEADERS

BASE_URL = "https://www.antupload.com"

ProgressCallback = Callable[[int, int], None]


class AntuploadResolveError(RuntimeError):
    """No se pudo resolver o descargar el archivo desde antupload.com.

    Cubre tanto una pagina caida como una alcanzable pero sin boton de
    descarga, o una descarga que se corto a mitad de camino.
    """


def is_antupload_url(url: str) -> bool:
    """Indica si `url` apunta a antupload.com.

    Args:
        url: Url a revisar.

    Returns:
        True si es un link de antupload.com.
    """
    return "antupload.com" in url


def download(page_url: str, dest_path: Path, on_progress: ProgressCallback | None = None) -> None:
    """Descarga el archivo servido por antupload.com en `page_url`.

    Antupload (el host final detras de los links de Lectulandia — ver
    `providers/lectulandia.py`) exige, ademas del link de descarga real, la
    cookie de sesion de haber visitado antes `page_url` (`/file/<codigo>/`) y
    un header Referer que apunte ahi: probado en vivo, ninguno de los dos
    alcanza por separado (un cliente nuevo con solo el Referer, o con la
    cookie de otra sesion, cae en un redirect de vuelta a `page_url` en vez
    de servir el archivo). Por eso esto hace las dos requests con el mismo
    `httpx.Client` (que ya persiste cookies entre llamadas) y no simplemente
    resuelve a una url suelta para que el downloader generico la baje aparte.

    Args:
        page_url: Url `/file/<codigo>/` de la ficha de descarga.
        dest_path: Ruta destino en disco.
        on_progress: Callback `(bytes_descargados, bytes_totales)` invocado
            a medida que llegan datos.

    Raises:
        AntuploadResolveError: Si la pagina no responde, no trae boton de
            descarga, o la descarga se corta a mitad de camino.
    """
    with httpx.Client(headers=DEFAULT_HEADERS, timeout=15.0, follow_redirects=True) as client:
        try:
            response = client.get(page_url)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise AntuploadResolveError(f"No se pudo acceder a {page_url}: {exc}") from exc

        soup = BeautifulSoup(response.text, "lxml")
        button = soup.select_one("a#downloadB[href]")
        if not button:
            raise AntuploadResolveError(f"No se encontro el boton de descarga en {page_url}")
        direct_url = urljoin(BASE_URL, button["href"])

        try:
            with client.stream("GET", direct_url, headers={"Referer": page_url}) as stream_response:
                stream_response.raise_for_status()
                total = int(stream_response.headers.get("Content-Length", 0))
                downloaded = 0
                with open(dest_path, "wb") as f:
                    for chunk in stream_response.iter_bytes():
                        f.write(chunk)
                        downloaded += len(chunk)
                        if on_progress:
                            on_progress(downloaded, total)
        except httpx.HTTPError as exc:
            dest_path.unlink(missing_ok=True)
            raise AntuploadResolveError(f"No se pudo descargar {direct_url}: {exc}") from exc
