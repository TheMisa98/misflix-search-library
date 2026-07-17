from __future__ import annotations

import typer

from misflix.cli import search
from misflix.core.models import Media, MediaKind


def make_result(source: str, media_id: str, title: str) -> Media:
    return Media(id=media_id, title=title, kind=MediaKind.MOVIE, source=source, page_url="http://example.com")


class FakeProvider:
    def __init__(self, media: Media):
        self._media = media

    def get_media(self, media_id: str) -> Media:
        return self._media


def _queue_inputs(monkeypatch, values: list[str]) -> None:
    values = iter(values)
    monkeypatch.setattr(typer, "prompt", lambda *a, **k: next(values))


def test_blank_input_ends_the_loop(monkeypatch):
    _queue_inputs(monkeypatch, [""])

    assert search._prompt_pick_or_search_again([], 0, 0, {}) is None


def test_non_numeric_text_is_returned_as_the_next_query(monkeypatch):
    _queue_inputs(monkeypatch, ["otra pelicula"])

    assert search._prompt_pick_or_search_again([], 0, 0, {}) == "otra pelicula"


def test_out_of_range_number_is_treated_as_a_new_query(monkeypatch):
    results = [make_result("a", "1", "Movie One")]
    _queue_inputs(monkeypatch, ["99"])

    assert search._prompt_pick_or_search_again(results, 0, 1, {"a": FakeProvider(results[0])}) == "99"


def test_number_outside_the_visible_page_is_treated_as_a_new_query(monkeypatch):
    results = [make_result("a", "1", "Movie One"), make_result("a", "2", "Movie Two")]
    _queue_inputs(monkeypatch, ["2"])

    # Solo se dibujo el #1 (rango [0, 1)) — el #2 existe en `results` pero no
    # esta visible en esta pagina, asi que se trata como busqueda nueva.
    assert search._prompt_pick_or_search_again(results, 0, 1, {"a": FakeProvider(results[0])}) == "2"


def test_valid_number_downloads_then_keeps_prompting_until_blank(monkeypatch):
    picked_media = make_result("a", "1", "Movie One")
    results = [picked_media]
    providers = {"a": FakeProvider(picked_media)}
    downloaded: list[Media] = []

    monkeypatch.setattr(search, "run_download_flow", lambda provider, media: downloaded.append(media))
    _queue_inputs(monkeypatch, ["1", ""])

    result = search._prompt_pick_or_search_again(results, 0, 1, providers)

    assert result is None
    assert downloaded == [picked_media]


def test_mas_returns_the_more_sentinel_when_theres_a_next_page(monkeypatch):
    results = [make_result("a", "1", "Movie One"), make_result("a", "2", "Movie Two")]
    _queue_inputs(monkeypatch, ["mas"])

    assert search._prompt_pick_or_search_again(results, 0, 1, {}) is search._MORE


def test_mas_is_treated_as_a_new_query_when_theres_no_next_page(monkeypatch):
    results = [make_result("a", "1", "Movie One")]
    _queue_inputs(monkeypatch, ["mas"])

    assert search._prompt_pick_or_search_again(results, 0, 1, {}) == "mas"
