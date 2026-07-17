from __future__ import annotations

import base64
from urllib.parse import parse_qs, quote, urljoin, urlparse

from bs4 import BeautifulSoup

from misflix.core.models import DownloadOption, Media, MediaKind
from misflix.infra.soup import attr
from misflix.providers.base import StaticProvider

BASE_URL = "https://ww3.lectulandia.com"
ANTUPLOAD_BASE_URL = "https://www.antupload.com"

_EXTENSIONS = {"epub": ".epub", "pdf": ".pdf"}


class LectulandiaProvider(StaticProvider):
    """Provider para lectulandia.com (libros, epub/pdf).

    El sitio esta detras de Cloudflare pero no exige un Turnstile para un
    GET plano (verificado en vivo, a diferencia de zona-leros):
    `StaticProvider`/httpx de toda la vida alcanza.

    Cada boton de descarga de la ficha del libro apunta a `/download.php?...`, una
    pagina que solo existe para mostrar una cuenta regresiva falsa de 11s antes de
    redirigir a `https://www.antupload.com/file/<codigo>/` (verificado en vivo:
    ese `setTimeout` es puramente cosmetico, nada del lado del servidor depende de
    esperarlo) — y ese `<codigo>` no hay ni que scrapearlo de esa pagina: es
    exactamente el parametro `d` de la url del boton, en base64 (`d=QzRyRW1qN2Yv`
    decodifica a `C4rEmj7f/`, el mismo codigo que trae `/download.php`). Por eso
    `get_download_options` decodifica `d` directamente y arma la url de antupload
    sin pasar por `/download.php` en absoluto. La resolucion final (antupload
    exige una cookie de sesion + Referer encadenados, ver infra/antupload.py) es
    responsabilidad de `DownloadService.download`, no de este provider.
    """

    name = "lectulandia"
    kinds = {MediaKind.BOOK}

    def search(self, query: str) -> list[Media]:
        """Busca `query` en lectulandia.com.

        Args:
            query: Texto de busqueda.

        Returns:
            Libros encontrados.
        """
        url = f"{BASE_URL}/search/{quote(query)}"
        soup = BeautifulSoup(self.http.get(url).text, "lxml")

        results = []
        for article in soup.select("article.card"):
            link = article.select_one("a.title[href]")
            if link is None:
                continue

            img = article.select_one("img.cover")
            href = attr(link, "href")
            results.append(
                Media(
                    id=_slug_from_url(href),
                    title=link.get_text(strip=True),
                    kind=MediaKind.BOOK,
                    source=self.name,
                    page_url=urljoin(BASE_URL, href),
                    cover_url=attr(img, "src") if img else None,
                )
            )
        return results

    def get_media(self, media_id: str) -> Media:
        """Resuelve la ficha completa de un libro.

        Args:
            media_id: Id del libro (slug de la url).

        Returns:
            El `Media` con titulo y portada, si se encontraron.
        """
        url = f"{BASE_URL}/book/{media_id}/"
        soup = BeautifulSoup(self.http.get(url).text, "lxml")

        title_el = soup.select_one("#title h1")
        img = soup.select_one("#cover img")
        return Media(
            id=media_id,
            title=title_el.get_text(strip=True) if title_el else media_id,
            kind=MediaKind.BOOK,
            source=self.name,
            page_url=url,
            cover_url=attr(img, "src") if img else None,
        )

    def get_download_options(self, media: Media) -> list[DownloadOption]:
        """Lista los formatos descargables (epub/pdf) de un libro.

        Args:
            media: Media del libro, ya resuelto via `get_media`.

        Returns:
            Una opcion por formato disponible, ya apuntando directo a
            antupload.com (ver la clase, arriba).
        """
        soup = BeautifulSoup(self.http.get(media.page_url).text, "lxml")

        options = []
        for link in soup.select("#downloadContainer a[href]"):
            input_el = link.select_one("input[value]")
            label = attr(input_el, "value").strip() if input_el else None
            if not label:
                continue

            link_code = _extract_link_code(attr(link, "href"))
            if link_code is None:
                continue

            options.append(
                DownloadOption(
                    label=label.upper(),
                    url=f"{ANTUPLOAD_BASE_URL}/file/{link_code}",
                    extension=_EXTENSIONS.get(label.lower()),
                )
            )
        return options


def _slug_from_url(url: str) -> str:
    return url.rstrip("/").rsplit("/", 1)[-1]


def _extract_link_code(download_php_href: str) -> str | None:
    query = parse_qs(urlparse(download_php_href).query)
    encoded = query.get("d", [None])[0]
    if not encoded:
        return None
    try:
        return base64.b64decode(encoded).decode()
    except ValueError, UnicodeDecodeError:
        return None
