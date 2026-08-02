from __future__ import annotations

import base64
from urllib.parse import parse_qs, quote, urljoin, urlparse

from bs4 import BeautifulSoup
from bs4.element import Tag

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

        Cuando `query` coincide con un autor o una serie, la propia pagina de
        resultados agrega un bloque `section.content-current` ("Autores:" o
        "Series:") con un link a esa taxonomia (`/autor/<slug>/` o
        `/serie/<slug>/`) — verificado en vivo: buscar "peter watts" solo trae
        un libro por texto, pero ese bloque apunta a `/autor/peter-watts/`,
        que lista sus otros libros. Esa pagina de taxonomia comparte el mismo
        `article.card`/`books-grid` que la busqueda, asi que se parsea igual
        (ver `_parse_book_cards`) y sus libros se agregan a los resultados
        directos (sin duplicar los que ya vinieron por texto).

        Args:
            query: Texto de busqueda.

        Returns:
            Libros encontrados, incluyendo los de cualquier autor/serie que
            la busqueda haya emparejado.
        """
        url = f"{BASE_URL}/search/{quote(query)}"
        soup = BeautifulSoup(self.http.get(url).text, "lxml")

        results = _parse_book_cards(soup, self.name)
        seen_ids = {book.id for book in results}

        seen_taxonomy_urls: set[str] = set()
        for link in soup.select("section.content-current .taxs a.term[href]"):
            taxonomy_url = urljoin(BASE_URL, attr(link, "href"))
            if taxonomy_url in seen_taxonomy_urls:
                continue
            seen_taxonomy_urls.add(taxonomy_url)

            for book in self._fetch_taxonomy_books(taxonomy_url):
                if book.id not in seen_ids:
                    seen_ids.add(book.id)
                    results.append(book)

        return results

    def _fetch_taxonomy_books(self, start_url: str) -> list[Media]:
        """Trae todos los libros de una pagina de autor/serie, con paginacion.

        Args:
            start_url: Url de la primera pagina de la taxonomia.

        Returns:
            Todos los libros listados, siguiendo `a.next.page-numbers` hasta
            que no haya una pagina siguiente.
        """
        books: list[Media] = []
        visited: set[str] = set()
        next_url: str | None = start_url
        while next_url and next_url not in visited:
            visited.add(next_url)
            soup = BeautifulSoup(self.http.get(next_url).text, "lxml")
            books.extend(_parse_book_cards(soup, self.name))

            next_link = soup.select_one("a.next.page-numbers[href]")
            next_url = urljoin(BASE_URL, attr(next_link, "href")) if next_link else None
        return books

    def get_media(self, media_id: str) -> Media:
        """Resuelve la ficha completa de un libro.

        Args:
            media_id: Id del libro (slug de la url).

        Returns:
            El `Media` con titulo, portada, autor(es) y sinopsis, si se
            encontraron.
        """
        url = f"{BASE_URL}/book/{media_id}/"
        soup = BeautifulSoup(self.http.get(url).text, "lxml")

        title_el = soup.select_one("#title h1")
        img = soup.select_one("#cover img")
        synopsis_el = soup.select_one("#sinopsis")
        return Media(
            id=media_id,
            title=title_el.get_text(strip=True) if title_el else media_id,
            kind=MediaKind.BOOK,
            source=self.name,
            page_url=url,
            cover_url=attr(img, "src") if img else None,
            author=_parse_authors(soup.select("#autor a")),
            synopsis=_parse_synopsis(synopsis_el),
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


def _parse_book_cards(soup: BeautifulSoup, source: str) -> list[Media]:
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
                source=source,
                page_url=urljoin(BASE_URL, href),
                cover_url=attr(img, "src") if img else None,
                author=_parse_authors(article.select("div.subdetail a[href^='/autor/']")),
            )
        )
    return results


def _parse_authors(author_links: list[Tag]) -> str | None:
    names = [a.get_text(strip=True) for a in author_links]
    return ", ".join(names) if names else None


def _parse_synopsis(synopsis_el: Tag | None) -> str | None:
    """Extrae el texto de `#sinopsis`, sea cual sea el markup interno.

    Verificado en vivo: el markup adentro de `#sinopsis` no es consistente
    entre libros (`<p class="description">` en unos, `<div class="ali_justi">
    <span>` en otros, un `<span>` suelto con `<br>` como separador de
    parrafos en otros) — usar un selector fijo para el hijo (ej.
    `p.description`) devolvia `None` para varios libros que si tenian
    sinopsis. `get_text(separator=" ")` mas normalizar espacios cubre los
    tres casos por igual sin depender de la estructura interna.
    """
    if synopsis_el is None:
        return None
    text = " ".join(synopsis_el.get_text(separator=" ", strip=True).split())
    return text or None


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
