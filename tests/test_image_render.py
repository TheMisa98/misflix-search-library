from __future__ import annotations

from rich.panel import Panel

from misflix.ui import image_render
from misflix.ui.image_render import Card, CoverRenderer


def make_card(label: str) -> Card:
    return Card(Panel(label), cover_url=None)


def test_render_grid_returns_all_cards_when_everything_fits(monkeypatch):
    monkeypatch.setattr(image_render.shutil, "which", lambda name: "/usr/bin/kitten")
    monkeypatch.setattr(image_render, "_ensure_fresh_top", lambda: (1, 1))
    monkeypatch.setattr(image_render, "_terminal_rows", lambda: 200)

    cards = [make_card(f"#{i}") for i in range(4)]
    rendered = CoverRenderer().render_grid(cards, columns=2, image_height=16)

    assert rendered == 4


def test_render_grid_truncates_rows_that_do_not_fit_the_terminal(monkeypatch):
    """Reproduce el bug visto en vivo: con muchos resultados y poco alto de
    terminal, una fila que cae mas alla del borde no debe dibujarse — las
    imagenes con --place no scrollean, asi que quedarian clavadas encima de la
    fila anterior en vez de mas abajo."""
    monkeypatch.setattr(image_render.shutil, "which", lambda name: "/usr/bin/kitten")
    monkeypatch.setattr(image_render, "_ensure_fresh_top", lambda: (1, 1))
    monkeypatch.setattr(image_render, "_terminal_rows", lambda: 20)

    cards = [make_card(f"#{i}") for i in range(4)]
    rendered = CoverRenderer().render_grid(cards, columns=2, image_height=16)

    # Con term_rows=20 e image_height=16, solo entra la primera fila (2 cards).
    assert rendered == 2


def test_render_grid_always_draws_at_least_the_first_row(monkeypatch):
    """Aunque ni la primera fila entre del todo, es preferible mostrarla
    recortada a no mostrar nada."""
    monkeypatch.setattr(image_render.shutil, "which", lambda name: "/usr/bin/kitten")
    monkeypatch.setattr(image_render, "_ensure_fresh_top", lambda: (1, 1))
    monkeypatch.setattr(image_render, "_terminal_rows", lambda: 5)

    cards = [make_card(f"#{i}") for i in range(2)]
    rendered = CoverRenderer().render_grid(cards, columns=2, image_height=16)

    assert rendered == 2


def test_render_grid_falls_back_to_stacked_without_kitten(monkeypatch):
    monkeypatch.setattr(image_render.shutil, "which", lambda name: None)

    cards = [make_card(f"#{i}") for i in range(3)]
    rendered = CoverRenderer().render_grid(cards, columns=2)

    assert rendered == 3


def test_render_grid_falls_back_to_stacked_without_a_cursor_position(monkeypatch):
    monkeypatch.setattr(image_render.shutil, "which", lambda name: "/usr/bin/kitten")
    monkeypatch.setattr(image_render, "_ensure_fresh_top", lambda: None)

    cards = [make_card(f"#{i}") for i in range(3)]
    rendered = CoverRenderer().render_grid(cards, columns=2)

    assert rendered == 3


def test_center_col_centers_within_the_terminal_width():
    assert image_render._center_col(width=10, term_cols=100) == 46


def test_center_col_pins_to_the_left_when_wider_than_the_terminal():
    assert image_render._center_col(width=100, term_cols=10) == 1


def test_center_col_pins_to_the_left_without_a_known_terminal_width():
    assert image_render._center_col(width=10, term_cols=None) == 1


class FakeStdout:
    def __init__(self):
        self.written: list[str] = []

    def write(self, s: str) -> None:
        self.written.append(s)

    def flush(self) -> None:
        pass


def test_render_grid_centers_a_single_card_when_centered(monkeypatch):
    monkeypatch.setattr(image_render.shutil, "which", lambda name: "/usr/bin/kitten")
    monkeypatch.setattr(image_render, "_ensure_fresh_top", lambda: (1, 1))
    monkeypatch.setattr(image_render, "_terminal_rows", lambda: 200)
    monkeypatch.setattr(image_render, "_terminal_cols", lambda: 100)
    fake_stdout = FakeStdout()
    monkeypatch.setattr(image_render.sys, "stdout", fake_stdout)

    card = make_card("Hola")
    CoverRenderer().render_grid([card], columns=1, centered=True)

    lines = image_render._render_lines(card.renderable, 36)
    panel_width = max(len(image_render._ANSI_RE.sub("", line)) for line in lines)
    expected_col = image_render._center_col(panel_width, 100)

    assert fake_stdout.written[0].startswith(f"\x1b[1;{expected_col}H")


def test_render_grid_does_not_center_by_default(monkeypatch):
    monkeypatch.setattr(image_render.shutil, "which", lambda name: "/usr/bin/kitten")
    monkeypatch.setattr(image_render, "_ensure_fresh_top", lambda: (1, 1))
    monkeypatch.setattr(image_render, "_terminal_rows", lambda: 200)
    monkeypatch.setattr(image_render, "_terminal_cols", lambda: 100)
    fake_stdout = FakeStdout()
    monkeypatch.setattr(image_render.sys, "stdout", fake_stdout)

    CoverRenderer().render_grid([make_card("Hola")], columns=1)

    assert fake_stdout.written[0].startswith("\x1b[1;1H")
