from __future__ import annotations

import httpx

from misflix.infra import imdb


def _mock_client(monkeypatch, handler):
    original_get = httpx.get

    def fake_get(url, **kwargs):
        request = httpx.Request("GET", url)
        return handler(request)

    monkeypatch.setattr(httpx, "get", fake_get)
    return original_get


def test_resolve_title_returns_first_feature_match(monkeypatch):
    body = (
        '{"d": ['
        '{"l": "Designing the World of Blade Runner 2049", "qid": "video", "y": 2018},'
        '{"l": "Blade Runner 2049", "qid": "movie", "y": 2017}'
        "]}"
    )
    _mock_client(monkeypatch, lambda request: httpx.Response(200, text=body, request=request))

    assert imdb.resolve_title("Blade Runner 2049 online hd") == ("Blade Runner 2049", 2017)


def test_resolve_title_returns_none_when_no_movie_in_results(monkeypatch):
    body = '{"d": [{"l": "Some Documentary", "qid": "tvSeries", "y": 2020}]}'
    _mock_client(monkeypatch, lambda request: httpx.Response(200, text=body, request=request))

    assert imdb.resolve_title("something obscure") is None


def test_resolve_title_without_year_hint_prefers_most_popular_movie(monkeypatch):
    # El endpoint de sugerencias no ordena por popularidad: sin un anio para
    # desambiguar, hay que elegir por rank (mas bajo = mas popular), no por orden
    # de aparicion.
    body = (
        '{"d": ['
        '{"l": "Dad Wanted", "qid": "movie", "y": 2020, "rank": 118099},'
        '{"l": "Wanted", "qid": "movie", "y": 2008, "rank": 3882}'
        "]}"
    )
    _mock_client(monkeypatch, lambda request: httpx.Response(200, text=body, request=request))

    assert imdb.resolve_title("se busca") == ("Wanted", 2008)


def test_resolve_title_with_year_hint_prefers_exact_year_over_popularity(monkeypatch):
    # Ambigüedad real: "se busca" trae varios candidatos, incluida una pelicula
    # mucho mas popular (rank mas bajo) que no coincide con el anio scrapeado.
    body = (
        '{"d": ['
        '{"l": "Sixteen Candles", "qid": "movie", "y": 1984, "rank": 3553},'
        '{"l": "Wanted", "qid": "movie", "y": 2008, "rank": 3882},'
        '{"l": "Dad Wanted", "qid": "movie", "y": 2020, "rank": 118099}'
        "]}"
    )
    _mock_client(monkeypatch, lambda request: httpx.Response(200, text=body, request=request))

    assert imdb.resolve_title("se busca", year_hint=2008) == ("Wanted", 2008)


def test_resolve_title_with_year_hint_allows_one_year_of_slack(monkeypatch):
    body = '{"d": [{"l": "Some Movie", "qid": "movie", "y": 2009, "rank": 500}]}'
    _mock_client(monkeypatch, lambda request: httpx.Response(200, text=body, request=request))

    assert imdb.resolve_title("some movie", year_hint=2008) == ("Some Movie", 2009)


def test_resolve_title_falls_back_to_popularity_when_no_year_matches(monkeypatch):
    body = (
        '{"d": ['
        '{"l": "Far Off Year", "qid": "movie", "y": 1950, "rank": 900},'
        '{"l": "Closer But Not Matching", "qid": "movie", "y": 2000, "rank": 100}'
        "]}"
    )
    _mock_client(monkeypatch, lambda request: httpx.Response(200, text=body, request=request))

    assert imdb.resolve_title("whatever", year_hint=2020) == ("Closer But Not Matching", 2000)


def test_resolve_title_returns_none_on_http_error(monkeypatch):
    def raise_error(request):
        raise httpx.ConnectError("boom", request=request)

    _mock_client(monkeypatch, raise_error)

    assert imdb.resolve_title("anything") is None


def test_resolve_title_returns_none_on_bad_json(monkeypatch):
    _mock_client(monkeypatch, lambda request: httpx.Response(200, text="not json", request=request))

    assert imdb.resolve_title("anything") is None
