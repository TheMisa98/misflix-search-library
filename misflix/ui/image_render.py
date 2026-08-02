from __future__ import annotations

import os
import re
import select
import shutil
import subprocess
import sys
import tempfile
import termios
import tty
from collections.abc import Callable
from io import StringIO
from pathlib import Path
from typing import NamedTuple

from rich.console import Console, RenderableType

FetchBytes = Callable[[str], bytes]

_CURSOR_POS_RE = re.compile(r"\[(\d+);(\d+)R")
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


class Card(NamedTuple):
    """Un resultado a dibujar: su texto renderizado y su portada.

    Attributes:
        renderable: Contenido rich (panel, etc.) a dibujar arriba de la portada.
        cover_url: Url de la portada, o None si no tiene.
        fetch_bytes: Callback para bajar la portada, si el provider la
            necesita (ver `CoverRenderer._icat`).
    """

    renderable: RenderableType
    cover_url: str | None
    fetch_bytes: FetchBytes | None = None


def _query_cursor_position(timeout: float = 0.5) -> tuple[int, int] | None:
    """Consulta la posicion actual del cursor via Device Status Report.

    Args:
        timeout: Segundos a esperar la respuesta de la terminal.

    Returns:
        `(fila, columna)` 1-indexed, o None si no hay una terminal
        interactiva real (pipes, tests) o no responde a tiempo.
    """
    if not sys.stdout.isatty() or not sys.stdin.isatty():
        return None

    fd = sys.stdin.fileno()
    try:
        old_settings = termios.tcgetattr(fd)
    except termios.error:
        return None

    try:
        tty.setcbreak(fd)
        sys.stdout.write("\x1b[6n")
        sys.stdout.flush()

        response = b""
        while b"R" not in response:
            ready, _, _ = select.select([fd], [], [], timeout)
            if not ready:
                return None
            response += os.read(fd, 1)

        match = _CURSOR_POS_RE.search(response.decode(errors="replace"))
        return (int(match.group(1)), int(match.group(2))) if match else None
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)


def _render_lines(renderable: RenderableType, width: int) -> list[str]:
    """Renderiza `renderable` a texto plano (con codigos ANSI) de `width` columnas.

    Args:
        renderable: Contenido rich a renderizar.
        width: Ancho en columnas a forzar.

    Returns:
        Las lineas renderizadas.
    """
    buffer = StringIO()
    Console(file=buffer, width=width, force_terminal=True, color_system="standard").print(renderable)
    return buffer.getvalue().splitlines()


def _terminal_rows() -> int | None:
    """Alto de la terminal actual, en filas.

    Returns:
        La cantidad de filas, o None si no se pudo determinar (sin tty).
    """
    try:
        return os.get_terminal_size().lines
    except OSError:
        return None


def _terminal_cols() -> int | None:
    """Ancho de la terminal actual, en columnas.

    Returns:
        La cantidad de columnas, o None si no se pudo determinar (sin tty).
    """
    try:
        return os.get_terminal_size().columns
    except OSError:
        return None


def _center_col(width: int, term_cols: int | None) -> int:
    """Columna (1-indexed) donde arrancar para centrar algo de `width` celdas.

    Args:
        width: Ancho, en celdas, de lo que se quiere centrar.
        term_cols: Ancho de la terminal, o None si no se pudo determinar (en
            ese caso no hay como centrar, se pega a la izquierda).

    Returns:
        La columna 1-indexed donde arrancar a dibujar.
    """
    if term_cols is None or term_cols <= width:
        return 1
    return (term_cols - width) // 2 + 1


def _ensure_fresh_top() -> tuple[int, int] | None:
    """Scrollea todo el contenido previo fuera de pantalla y arranca en la fila 1.

    Ojo: imprimir lineas en blanco NO "crea" espacio debajo del cursor — el cursor
    converge al ultimo renglon visible y se queda ahi (todo lo anterior se va
    scrolleando hacia arriba). Por eso hay que scrollear todo el contenido previo
    fuera de la pantalla primero, y despues posicionar el cursor en la fila 1 con
    CUP absoluto (asi queda el maximo espacio posible debajo, sin hueco arriba).
    Se llama una sola vez por bloque a dibujar (no por fila), porque las imagenes
    puestas con `--place` quedan clavadas a una fila fija: si despues se imprime
    mas texto y la terminal hace scroll, ese texto nuevo termina "debajo" de la
    imagen en vez de despues de ella.

    Returns:
        La posicion `(fila, columna)` del cursor tras el scroll, o None si no
        hay una terminal interactiva real.
    """
    if _query_cursor_position() is None:
        return None

    try:
        term_rows = os.get_terminal_size().lines
    except OSError:
        return _query_cursor_position()

    sys.stdout.write("\n" * term_rows)
    sys.stdout.write("\x1b[1;1H")
    sys.stdout.flush()
    return _query_cursor_position()


class CoverRenderer:
    """Renderiza portadas en la terminal usando `kitten icat` (protocolo grafico de Kitty)."""

    def render_grid(
        self,
        cards: list[Card],
        columns: int = 2,
        card_width: int = 36,
        image_width: int = 32,
        image_height: int = 16,
        col_gap: int = 3,
        row_gap: int = 2,
        centered: bool = False,
    ) -> int:
        """Dibuja `cards` en una grilla de `columns` por fila.

        El texto (panel, etc.) va arriba y su portada, mas grande, debajo.
        Si no se puede ubicar el cursor (sin tty interactiva) o no hay
        `kitten`, cae de vuelta a un listado simple apilado, una card debajo
        de otra.

        Las imagenes puestas con `--place` quedan clavadas a una fila fija
        de pantalla (ver `_ensure_fresh_top`) — no hacen scroll como el
        texto normal — asi que una fila que cae mas alla del alto real de la
        terminal no aparece "mas abajo despues de scrollear": se clampea
        encima de lo ya dibujado y lo tapa, volviendolo todo ilegible (visto
        en vivo con una busqueda de varios resultados con texto largo). Por
        eso, en vez de asumir espacio infinito debajo, esto corta apenas una
        fila no entra en lo que queda de pantalla — el caller
        (`views.show_results`) usa el valor de retorno para avisar cuantas
        quedaron afuera y limitar que se pueda elegir solo entre las que
        realmente se ven.

        Args:
            cards: Cards a dibujar, en orden.
            columns: Cantidad de columnas por fila.
            card_width: Ancho en columnas de cada card.
            image_width: Ancho en celdas de cada portada.
            image_height: Alto en celdas de cada portada.
            col_gap: Separacion horizontal entre columnas.
            row_gap: Separacion vertical entre filas.
            centered: True para centrar cada card en el ancho de la
                terminal en vez de pegarla a la izquierda — pensado para
                `columns=1` (la ficha de un solo resultado, ver
                `prompts.show_cover`); con mas de una columna cada card se
                centra por separado, lo que no da una grilla prolija. El
                texto y la portada de una misma card se centran cada uno
                por su cuenta segun su propio ancho (el panel de texto
                puede ser mas angosto que `image_width`, o viceversa), no
                comparten columna como en el caso sin centrar.

        Returns:
            Cuantas cards se llegaron a dibujar (puede ser menos que
            `len(cards)` si no entraban todas en la pantalla).
        """
        if shutil.which("kitten") is None:
            self._render_stacked(cards, card_width)
            return len(cards)

        rows = [
            [(card, _render_lines(card.renderable, card_width)) for card in cards[i : i + columns]]
            for i in range(0, len(cards), columns)
        ]

        start = _ensure_fresh_top()
        if start is None:
            self._render_stacked(cards, card_width)
            return len(cards)

        row_cursor, start_col = start
        term_rows = _terminal_rows()
        term_cols = _terminal_cols() if centered else None
        rendered = 0

        for row_index, row in enumerate(rows):
            max_lines = max((len(lines) for _, lines in row), default=0)
            # La primera fila siempre se dibuja aunque no entre del todo — es
            # preferible una fila recortada a no mostrar nada.
            if row_index > 0 and term_rows is not None and row_cursor + max_lines + image_height - 1 > term_rows:
                break

            for j, (_card, lines) in enumerate(row):
                if centered:
                    panel_width = max((len(_ANSI_RE.sub("", line)) for line in lines), default=0)
                    col = _center_col(panel_width, term_cols)
                else:
                    col = start_col + j * (card_width + col_gap)
                for offset, line in enumerate(lines):
                    sys.stdout.write(f"\x1b[{row_cursor + offset};{col}H{line}")
            sys.stdout.flush()

            image_row = row_cursor + max_lines
            for j, (card, _lines) in enumerate(row):
                if not card.cover_url:
                    continue
                col = _center_col(image_width, term_cols) if centered else start_col + j * (card_width + col_gap)
                place = f"{image_width}x{image_height}@{col - 1}x{image_row - 1}"
                self._safe_icat(["--place", place], card.cover_url, card.fetch_bytes)

            rendered += len(row)
            row_cursor = image_row + image_height + row_gap

        sys.stdout.write(f"\x1b[{row_cursor};1H")
        sys.stdout.flush()
        return rendered

    def _render_stacked(self, cards: list[Card], card_width: int) -> None:
        """Dibuja `cards` una debajo de otra, sin grilla (fallback sin `kitten`/tty).

        Args:
            cards: Cards a dibujar, en orden.
            card_width: Ancho en columnas de cada card.
        """
        for card in cards:
            for line in _render_lines(card.renderable, card_width):
                print(line)
            if card.cover_url:
                self._safe_icat([], card.cover_url, card.fetch_bytes)

    def _safe_icat(self, extra_args: list[str], cover_url: str, fetch_bytes: FetchBytes | None) -> None:
        """Como `_icat`, pero atrapa el fallo y avisa en vez de propagarlo.

        Args:
            extra_args: Argumentos extra para `kitten icat`.
            cover_url: Url de la portada.
            fetch_bytes: Callback para bajar la portada, ver `_icat`.
        """
        try:
            self._icat(extra_args, cover_url, fetch_bytes)
        except (OSError, subprocess.CalledProcessError) as exc:
            print(f"  (no se pudo mostrar la portada: {exc})")

    def _icat(self, extra_args: list[str], cover_url: str, fetch_bytes: FetchBytes | None) -> None:
        """Invoca `kitten icat` para dibujar una portada.

        Args:
            extra_args: Argumentos extra (ej. `--place`) para `kitten icat`.
            cover_url: Url de la portada.
            fetch_bytes: Si se da, se usa para bajar los bytes de la portada
                y pasarselos a `kitten` como archivo temporal, en vez de que
                `kitten` la pida el mismo — necesario para portadas detras
                de un Cloudflare que `kitten` no puede pasar por su cuenta.

        Raises:
            subprocess.CalledProcessError: Si `kitten icat` termina con error.
        """
        args = ["kitten", "icat", *extra_args]

        if fetch_bytes is None:
            subprocess.run([*args, cover_url], check=True)
            return

        # Algunos repos sirven las portadas detras del mismo Cloudflare del resto
        # del sitio: `kitten icat` no puede pasar ese challenge por su cuenta, asi
        # que bajamos los bytes con el cliente del provider y le pasamos un archivo.
        suffix = Path(cover_url.split("?", 1)[0]).suffix or ".jpg"
        with tempfile.NamedTemporaryFile(suffix=suffix) as tmp:
            tmp.write(fetch_bytes(cover_url))
            tmp.flush()
            subprocess.run([*args, tmp.name], check=True)
