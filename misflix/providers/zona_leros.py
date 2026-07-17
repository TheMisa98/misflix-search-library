from __future__ import annotations

import re
from urllib.parse import quote

from bs4 import BeautifulSoup

from misflix.core.models import DownloadOption, Media, MediaKind
from misflix.infra.cloudflare import CloudflareHttpClient
from misflix.providers.base import StaticProvider

BASE_URL = "https://www.zona-leros.com"

_ALLOWED_HOSTS = {"MEGA", "MEDIAFIRE"}

_BG_URL_RE = re.compile(r"url\(([^)]+)\)")
_YEAR_RE = re.compile(r"(\d{4})")
_SIZE_RE = re.compile(r"([\d.]+)\s*(GB|MB|KB)", re.IGNORECASE)
_SIZE_MULTIPLIERS = {"KB": 1024, "MB": 1024**2, "GB": 1024**3}
_DETAIL_TITLE_SUFFIX_RE = re.compile(r"\s+online\s*hd$", re.IGNORECASE)
_SEASON_CAPI_RE = re.compile(r"(\d+)x")

_SERIES_ID_PREFIX = "series:"
_EPISODE_ID_PREFIX = "episode:"


class ZonaLerosProvider(StaticProvider):
    """Provider para zona-leros.com (peliculas y series). El sitio esta detras de
    un Cloudflare Managed Challenge, asi que usa CloudflareHttpClient en vez del
    HttpClient por defecto de StaticProvider.

    Una serie no tiene links de descarga propios (esos viven en cada episodio), asi
    que el `id` de un Media indica de que tipo de pagina viene, ya que `get_media`
    solo recibe un string: sin prefijo es una pelicula (`/peliculas/<id>`), con
    prefijo `series:` es la ficha de una serie (`/series/<id>`) y con `episode:` es
    un episodio puntual (`/series/episode/<id>`, que si tiene descargas)."""

    name = "zona-leros"
    kinds = {MediaKind.MOVIE, MediaKind.SERIES}

    def __init__(self, http_client: CloudflareHttpClient | None = None):
        self.http = http_client or CloudflareHttpClient(domain="zona-leros.com")

    def search(self, query: str) -> list[Media]:
        url = f"{BASE_URL}/search?q={quote(query)}"
        soup = BeautifulSoup(self.http.get(url).text, "lxml")

        results = []
        for article in soup.select("ul.ListAnimes article.Anime"):
            link = article.select_one("a[href]")
            title_el = article.select_one("h3.Title")
            if link is None or title_el is None:
                continue

            href = link["href"]
            if "/peliculas/" in href and "/genero/" not in href:
                kind, media_id = MediaKind.MOVIE, _media_id_from_url(href)
            elif "/series/" in href and "/genero/" not in href and "/episode/" not in href:
                kind, media_id = MediaKind.SERIES, _SERIES_ID_PREFIX + _media_id_from_url(href)
            else:
                continue

            img = article.select_one("figure img")
            results.append(
                Media(
                    id=media_id,
                    title=title_el.get_text(strip=True),
                    kind=kind,
                    source=self.name,
                    page_url=href,
                    cover_url=img["src"] if img else None,
                )
            )
        return results

    def get_media(self, media_id: str) -> Media:
        if media_id.startswith(_EPISODE_ID_PREFIX):
            slug = media_id.removeprefix(_EPISODE_ID_PREFIX)
            return self._parse_media_page(f"{BASE_URL}/series/episode/{slug}", media_id, MediaKind.SERIES)
        if media_id.startswith(_SERIES_ID_PREFIX):
            slug = media_id.removeprefix(_SERIES_ID_PREFIX)
            return self._parse_media_page(f"{BASE_URL}/series/{slug}", media_id, MediaKind.SERIES)
        return self._parse_media_page(f"{BASE_URL}/peliculas/{media_id}", media_id, MediaKind.MOVIE)

    def _parse_media_page(self, url: str, media_id: str, kind: MediaKind) -> Media:
        soup = BeautifulSoup(self.http.get(url).text, "lxml")

        title_el = soup.select_one("div.Container h1.Title") or soup.select_one("h1.Title")

        return Media(
            id=media_id,
            title=_clean_detail_title(title_el.get_text(strip=True)) if title_el else media_id,
            kind=kind,
            source=self.name,
            page_url=url,
            cover_url=_parse_cover(soup),
            year=_parse_year(soup),
        )

    def get_episodes(self, series: Media) -> list[Media]:
        """Lista todos los episodios (de todas las temporadas) de una ficha de
        serie. Solo tiene sentido para un Media obtenido de `search`/`get_media`
        con id `series:...` — una serie no tiene descargas propias, hay que elegir
        un episodio primero."""
        soup = BeautifulSoup(self.http.get(series.page_url).text, "lxml")

        episodes = []
        seen_urls: set[str] = set()
        for item in soup.select("ul.ListEpisodios li a[href]"):
            href = item["href"]
            if "/series/episode/" not in href or href in seen_urls:
                continue
            seen_urls.add(href)

            capi = item.select_one(".Capi")
            label = capi.get_text(strip=True) if capi else ""
            img = item.select_one("img")
            episodes.append(
                Media(
                    id=_EPISODE_ID_PREFIX + _media_id_from_url(href),
                    title=f"{series.title} {label}".strip(),
                    kind=MediaKind.SERIES,
                    source=self.name,
                    page_url=href,
                    cover_url=img["src"] if img else None,
                )
            )
        return episodes

    def get_seasons(self, series: Media) -> list[int]:
        """Temporadas con un pack completo para descargar de una. No todas las
        series lo ofrecen (algunas, como Breaking Bad, solo tienen episodios
        sueltos) — vacio si esta no tiene ninguna."""
        soup = BeautifulSoup(self.http.get(series.page_url).text, "lxml")
        return sorted(_season_download_blocks(soup).keys())

    def get_season_download_options(self, series: Media, season: int) -> list[DownloadOption]:
        soup = BeautifulSoup(self.http.get(series.page_url).text, "lxml")
        links = _season_download_blocks(soup).get(season, [])

        options = []
        for link in links:
            host = link.get_text(strip=True)
            if host.upper() not in _ALLOWED_HOSTS:
                continue
            options.append(DownloadOption(label=host.upper(), url=link["href"], opens_externally=True))
        return options

    def get_download_options(self, media: Media) -> list[DownloadOption]:
        soup = BeautifulSoup(self.http.get(media.page_url).text, "lxml")

        if media.id.startswith(_EPISODE_ID_PREFIX):
            return _episode_download_options(soup)
        if media.id.startswith(_SERIES_ID_PREFIX):
            # Una ficha de serie no tiene descargas propias: los packs de temporada
            # se resuelven aparte con get_seasons/get_season_download_options, y
            # los episodios sueltos con get_episodes.
            return []
        return _movie_download_options(soup)


def _movie_download_options(soup: BeautifulSoup) -> list[DownloadOption]:
    # Las peliculas con varias calidades (4K/1080p/720p) repiten tabla+botones
    # dentro de un modal por calidad; sin eso, la pagina trae un unico bloque. Una
    # ficha de serie (a diferencia de un episodio) no tiene ninguno de los dos, asi
    # que esto naturalmente devuelve una lista vacia.
    sections = soup.select("div.modal.fade[id^='calidad-']") or [soup]

    options = []
    for section in sections:
        quality_label = _quality_label(section)
        quality = _parse_quality(section)
        size_bytes = _parse_size_bytes(quality.get("Tamaño", ""))
        extension = f".{quality['Formato:'].lower()}" if quality.get("Formato:") else None

        for link in section.select("a.download-link[href]"):
            host = link.get_text(strip=True)
            if host.upper() not in _ALLOWED_HOSTS:
                continue
            label = f"{host} ({quality_label})" if quality_label else host
            options.append(
                DownloadOption(
                    label=label,
                    url=link["href"],
                    size_bytes=size_bytes,
                    extension=extension,
                    opens_externally=True,
                )
            )
    return options


def _episode_download_options(soup: BeautifulSoup) -> list[DownloadOption]:
    # La tabla de un episodio trae una fila por servidor (SERVIDOR/TAMAÑO/IDIOMA/
    # CALIDAD/DESCARGAR) en vez de un bloque de calidad con varios botones — el
    # nombre del servidor esta en la primera celda de la fila, no en el link.
    options = []
    for row in soup.select("table.RTbl tbody tr"):
        cells = row.select("td")
        if len(cells) < 5:
            continue

        host = cells[0].get_text(strip=True)
        if host.upper() not in _ALLOWED_HOSTS:
            continue

        link = cells[4].select_one("a.download-link[href]")
        if not link:
            continue

        quality = cells[3].get_text(strip=True)
        label = f"{host.upper()} ({quality})" if quality else host.upper()
        options.append(
            DownloadOption(
                label=label,
                url=link["href"],
                size_bytes=_parse_size_bytes(cells[1].get_text(strip=True)),
                extension=None,
                opens_externally=True,
            )
        )
    return options


def _season_download_blocks(soup: BeautifulSoup) -> dict[int, list]:
    """Empareja cada bloque de temporada (`ul.ListEpisodios`) con su(s) bloque(s) de
    descarga de pack completo (`div#dw`) inmediatamente siguientes, recorriendo el
    documento en orden. No hay contenedor que agrupe ambos, asi que la asociacion
    es por posicion: el numero de temporada sale del primer episodio listado, y
    los botones de descarga que aparecen antes de la proxima lista de episodios le
    corresponden a esa temporada. El widget de "Top Descargas" (que trae series
    recomendadas, no episodios) se descarta porque sus `<li>` no tienen links a
    `/series/episode/`, asi que nunca fija una temporada "actual" valida."""
    blocks: dict[int, list] = {}
    current_season: int | None = None

    for node in soup.select("ul.ListEpisodios, div#dw, div.Comprar#dw"):
        if node.name == "ul":
            episode_links = node.select("a[href*='/series/episode/']")
            if not episode_links:
                current_season = None
                continue
            capi = episode_links[0].select_one(".Capi")
            match = _SEASON_CAPI_RE.match(capi.get_text(strip=True)) if capi else None
            current_season = int(match.group(1)) if match else None
            continue

        if current_season is not None:
            blocks.setdefault(current_season, []).extend(node.select("a.download-link[href]"))

    return blocks


def _media_id_from_url(url: str) -> str:
    return url.rstrip("/").rsplit("/", 1)[-1]


def _clean_detail_title(title: str) -> str:
    """La ficha de detalle le pega un sufijo de SEO tipo "... online hd" al h1
    (el listado de busqueda no lo trae); sacarlo ademas de mejorar el titulo,
    evita que una busqueda en IMDb con esa basura no encuentre nada."""
    return _DETAIL_TITLE_SUFFIX_RE.sub("", title).strip()


def _parse_cover(soup: BeautifulSoup) -> str | None:
    """El `og:image` es el mismo poster (proporcion normal) que usa el listado de
    busqueda. El fondo de `div.Bg` es un banner panoramico decorativo (bastante mas
    ancho que alto) pensado para el hero de la pagina, no para mostrarse como
    portada — Kitty lo termina recortando al encajarlo en una caja de miniatura."""
    og_image = soup.select_one('meta[property="og:image"]')
    if og_image and og_image.get("content"):
        return og_image["content"]

    bg = soup.select_one("div.Bg[style]")
    bg_match = _BG_URL_RE.search(bg["style"]) if bg else None
    return bg_match.group(1).strip("'\"") if bg_match else None


def _parse_year(soup: BeautifulSoup) -> int | None:
    label = soup.find("span", class_="TxtMAY", string=lambda s: bool(s and "ESTRENO" in s.upper()))
    if not label:
        return None
    value = label.find_next_sibling("span", class_="TxtDES")
    if not value:
        return None
    match = _YEAR_RE.search(value.get_text())
    return int(match.group(1)) if match else None


def _quality_label(section) -> str | None:
    top = section.select_one(".Top.fa-flag")
    if not top:
        return None
    text = top.get_text(strip=True)
    return text.removeprefix("Calidad").strip() or None


def _parse_quality(section) -> dict[str, str]:
    table = section.select_one("table.RTbl")
    if not table:
        return {}
    row = table.select_one("tbody tr")
    if not row:
        return {}
    headers = [th.get_text(strip=True) for th in table.select("thead th")]
    cells = [td.get_text(strip=True) for td in row.select("td")]
    return dict(zip(headers, cells))


def _parse_size_bytes(size_text: str) -> int | None:
    match = _SIZE_RE.match(size_text)
    if not match:
        return None
    value, unit = match.groups()
    return int(float(value) * _SIZE_MULTIPLIERS[unit.upper()])
