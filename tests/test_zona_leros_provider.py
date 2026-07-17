from __future__ import annotations

from pathlib import Path

from misflix.core.models import Media, MediaKind
from misflix.providers.zona_leros import ZonaLerosProvider, _clean_detail_title

FIXTURES = Path(__file__).parent / "fixtures" / "zona_leros"


class FakeResponse:
    def __init__(self, text: str):
        self.text = text


class FakeHttpClient:
    def __init__(self, text: str):
        self._text = text
        self.requested_urls: list[str] = []

    def get(self, url: str) -> FakeResponse:
        self.requested_urls.append(url)
        return FakeResponse(self._text)


def test_search_returns_movies_and_series():
    html = (FIXTURES / "search_results.html").read_text()
    provider = ZonaLerosProvider(http_client=FakeHttpClient(html))

    results = provider.search("avatar")

    assert [(r.title, r.kind) for r in results] == [
        ("Avatar: Fuego y cenizas", MediaKind.MOVIE),
        ("Avatar", MediaKind.MOVIE),
        ("Avatar: La leyenda de Aang", MediaKind.SERIES),
    ]
    assert all(r.source == "zona-leros" for r in results)


def test_search_excludes_games():
    html = (FIXTURES / "search_results.html").read_text()
    provider = ZonaLerosProvider(http_client=FakeHttpClient(html))

    results = provider.search("avatar")

    titles = {r.title for r in results}
    assert "Avatar Frontiers of Pandora Complete Edition" not in titles


def test_search_series_result_has_prefixed_id():
    html = (FIXTURES / "search_results.html").read_text()
    provider = ZonaLerosProvider(http_client=FakeHttpClient(html))

    results = provider.search("avatar")

    series = next(r for r in results if r.kind == MediaKind.SERIES)
    assert series.id == "series:avatar-la-leyenda-de-aang"


def test_search_parses_id_and_cover_from_result():
    html = (FIXTURES / "search_results.html").read_text()
    provider = ZonaLerosProvider(http_client=FakeHttpClient(html))

    results = provider.search("avatar")

    avatar = next(r for r in results if r.title == "Avatar")
    assert avatar.id == "avatar-2009-1080p-latino-ingles-hd-m"
    assert avatar.cover_url == "https://www.zona-leros.com/storage/movies_tumbl/avatar-cover-w2n.jpg"
    assert avatar.page_url == "https://www.zona-leros.com/peliculas/avatar-2009-1080p-latino-ingles-hd-m"


def test_get_media_parses_title_cover_and_year():
    html = (FIXTURES / "movie_detail.html").read_text()
    provider = ZonaLerosProvider(http_client=FakeHttpClient(html))

    media = provider.get_media("blade-runner-2049-hd")

    assert media.title == "Blade Runner 2049"
    assert media.year == 2017
    # El poster (og:image), no el banner panoramico de div.Bg: ese ultimo tiene una
    # relacion de aspecto que Kitty termina recortando en la caja de miniatura.
    assert media.cover_url == "https://www.zona-leros.com/storage/movies_tumbl/blade-runner-2049-cover-qvx.jpg"


def test_clean_detail_title_strips_online_hd_suffix():
    assert _clean_detail_title("Se busca online hd") == "Se busca"
    assert _clean_detail_title("Blade Runner 2049 Online HD") == "Blade Runner 2049"


def test_get_media_falls_back_to_background_banner_without_og_image():
    html = """
    <html><body>
      <div class="Bg" style="background-image:url(https://example.com/banner.jpg)"></div>
      <div class="Container"><h1 class="Title">Some Movie</h1></div>
    </body></html>
    """
    provider = ZonaLerosProvider(http_client=FakeHttpClient(html))

    media = provider.get_media("some-movie")

    assert media.cover_url == "https://example.com/banner.jpg"
    assert _clean_detail_title("Avatar") == "Avatar"


def test_get_download_options_keeps_all_qualities_but_only_mega_and_mediafire():
    html = (FIXTURES / "movie_detail.html").read_text()
    provider = ZonaLerosProvider(http_client=FakeHttpClient(html))
    media = Media(
        id="blade-runner-2049-hd",
        title="Blade Runner 2049",
        kind=MediaKind.MOVIE,
        source="zona-leros",
        page_url="https://www.zona-leros.com/peliculas/blade-runner-2049-hd",
    )

    options = provider.get_download_options(media)

    assert [o.label for o in options] == [
        "MEGA (UHD 4K)",
        "MEDIAFIRE (UHD 4K)",
        "MEGA (FULL HD 1080P)",
        "MEDIAFIRE (FULL HD 1080P)",
    ]
    assert all(o.opens_externally for o in options)
    assert all(o.url.startswith("https://anomizador.zona-leros.com/l?hs=") for o in options)

    mega_1080p = next(o for o in options if o.label == "MEGA (FULL HD 1080P)")
    assert mega_1080p.extension == ".mp4"
    assert mega_1080p.size_bytes == int(4.2 * 1024**3)

    mega_4k = next(o for o in options if o.label == "MEGA (UHD 4K)")
    assert mega_4k.extension == ".mkv"
    assert mega_4k.size_bytes == int(15.9 * 1024**3)


def test_get_media_series_parses_title_cover_and_year():
    html = (FIXTURES / "series_detail.html").read_text()
    provider = ZonaLerosProvider(http_client=FakeHttpClient(html))

    media = provider.get_media("series:breaking-bad-hd-latino-2020-online-gratis-free-zonaleros")

    assert media.title == "Breaking Bad"
    assert media.kind == MediaKind.SERIES
    assert media.year == 2008
    assert media.cover_url == "https://www.zona-leros.com/storage/series_tumbl/breaking-bad-cover-1qs.jpg"
    assert (
        media.page_url == "https://www.zona-leros.com/series/breaking-bad-hd-latino-2020-online-gratis-free-zonaleros"
    )


def test_get_media_episode_uses_episode_url():
    html = (FIXTURES / "episode_detail.html").read_text()
    http = FakeHttpClient(html)
    provider = ZonaLerosProvider(http_client=http)

    media = provider.get_media("episode:breaking-bad-5-1-h")

    assert media.title == "Breaking Bad Temporada 5 Episodio 1"
    assert media.kind == MediaKind.SERIES
    assert http.requested_urls == ["https://www.zona-leros.com/series/episode/breaking-bad-5-1-h"]


def test_get_download_options_is_empty_for_a_series_overview_page():
    """La ficha de la serie no tiene descargas propias, solo cada episodio."""
    html = (FIXTURES / "series_detail.html").read_text()
    provider = ZonaLerosProvider(http_client=FakeHttpClient(html))
    series = Media(
        id="series:breaking-bad-hd-latino-2020-online-gratis-free-zonaleros",
        title="Breaking Bad",
        kind=MediaKind.SERIES,
        source="zona-leros",
        page_url="https://www.zona-leros.com/series/breaking-bad-hd-latino-2020-online-gratis-free-zonaleros",
    )

    assert provider.get_download_options(series) == []


def test_get_episodes_lists_every_season_and_excludes_the_related_series_widget():
    html = (FIXTURES / "series_detail.html").read_text()
    provider = ZonaLerosProvider(http_client=FakeHttpClient(html))
    series = Media(
        id="series:breaking-bad-hd-latino-2020-online-gratis-free-zonaleros",
        title="Breaking Bad",
        kind=MediaKind.SERIES,
        source="zona-leros",
        page_url="https://www.zona-leros.com/series/breaking-bad-hd-latino-2020-online-gratis-free-zonaleros",
    )

    episodes = provider.get_episodes(series)

    assert [e.title for e in episodes] == [
        "Breaking Bad 5x1",
        "Breaking Bad 5x2",
        "Breaking Bad 1x1",
    ]
    assert all(e.id.startswith("episode:") for e in episodes)
    assert all(e.kind == MediaKind.SERIES for e in episodes)
    assert "The Boys" not in [e.title for e in episodes]

    first = episodes[0]
    assert first.id == "episode:breaking-bad-5-1-h"
    assert first.page_url == "https://www.zona-leros.com/series/episode/breaking-bad-5-1-h"
    assert first.cover_url == "https://www.zona-leros.com/storage/episodes_tumbl/breaking-bad-5-1-cover-fey1h.jpg"


def test_get_download_options_for_episode_reads_servidor_column():
    html = (FIXTURES / "episode_detail.html").read_text()
    provider = ZonaLerosProvider(http_client=FakeHttpClient(html))
    episode = Media(
        id="episode:breaking-bad-5-1-h",
        title="Breaking Bad 5x1",
        kind=MediaKind.SERIES,
        source="zona-leros",
        page_url="https://www.zona-leros.com/series/episode/breaking-bad-5-1-h",
    )

    options = provider.get_download_options(episode)

    assert [o.label for o in options] == ["MEGA (1080p)", "MEDIAFIRE (1080p)"]
    assert all(o.opens_externally for o in options)
    assert all(o.size_bytes == int(3.0 * 1024**3) for o in options)
    mega = next(o for o in options if o.label == "MEGA (1080p)")
    assert mega.url == "https://anomizador.zona-leros.com/l?hs=aaa"
