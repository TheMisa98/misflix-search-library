from __future__ import annotations

from misflix.core.models import Media, MediaKind
from misflix.ui import views


def make_media(title: str) -> Media:
    return Media(id=title, title=title, kind=MediaKind.MOVIE, source="a", page_url="http://example.com")


def test_show_results_returns_everything_when_it_all_fits(monkeypatch):
    monkeypatch.setattr(views.CoverRenderer, "render_grid", lambda self, cards, columns=2: len(cards))

    results = [make_media("A"), make_media("B")]
    shown = views.show_results(results)

    assert shown == (0, 2)


def test_show_results_truncates_to_what_was_actually_rendered(monkeypatch):
    monkeypatch.setattr(views.CoverRenderer, "render_grid", lambda self, cards, columns=2: 1)

    results = [make_media("A"), make_media("B"), make_media("C")]
    shown = views.show_results(results)

    assert shown == (0, 1)


def test_show_results_returns_empty_range_without_results():
    assert views.show_results([]) == (0, 0)


def test_show_results_pages_from_a_given_start_and_numbers_cards_absolutely(monkeypatch):
    seen_card_counts = []

    def fake_render_grid(self, cards, columns=2):
        seen_card_counts.append(len(cards))
        return 1

    monkeypatch.setattr(views.CoverRenderer, "render_grid", fake_render_grid)

    results = [make_media("A"), make_media("B"), make_media("C")]
    shown = views.show_results(results, start=1)

    assert shown == (1, 2)
    # Solo se le pasan a render_grid los resultados desde `start` en adelante.
    assert seen_card_counts == [2]
