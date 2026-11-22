"""Presentacion en terminal del gestor de subtitulos (`misflix subs ...`)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path

from rich.console import Console
from rich.table import Table

console = Console()

# Etiqueta legible y color de cada estado (ver `SubtitleStatus`).
STATUS_LABELS: dict[str, tuple[str, str]] = {
    "audio_es": ("Tiene doblaje en español", "dim"),
    "sub_es_incrustado": ("Trae subtítulos en español incrustados", "dim"),
    "release_es": ("El nombre indica español (doblado/subtitulado)", "dim"),
    "ok": ("Subtítulo existente verificado", "green"),
    "corregido": ("Subtítulo existente resincronizado", "green"),
    "descargado": ("Subtítulo bajado y verificado", "green"),
    "dudoso": ("Verificación no concluyente", "yellow"),
    "pendiente": ("Falta bajar (subdivx no disponible)", "yellow"),
    "no_encontrado": ("Ningún candidato verificó", "red"),
    "sin_referencia": ("Sin subtítulo ni audio para verificar", "red"),
    "error": ("Error", "red"),
}


def show_counts(counts: Mapping[str, int], title: str) -> None:
    """Tabla con la cantidad de videos por estado.

    Args:
        counts: Estado -> cantidad.
        title: Titulo de la tabla.
    """
    table = Table(title=title, border_style="cyan", header_style="bold cyan")
    table.add_column("Estado")
    table.add_column("Videos", justify="right")
    for status, (label, style) in STATUS_LABELS.items():
        if counts.get(status):
            table.add_row(f"[{style}]{label}[/{style}]", str(counts[status]))
    for status, count in counts.items():
        if status not in STATUS_LABELS:
            table.add_row(status, str(count))
    console.print(table)


def show_videos(rows: Sequence[tuple[str, str, str]], title: str) -> None:
    """Tabla de videos con su estado y detalle.

    Args:
        rows: `(ruta, estado, detalle)` por video.
        title: Titulo de la tabla.
    """
    if not rows:
        return
    table = Table(title=title, border_style="cyan", header_style="bold cyan")
    table.add_column("Video", overflow="fold")
    table.add_column("Estado", no_wrap=True)
    table.add_column("Detalle", overflow="fold")
    for path, status, detail in rows:
        label, style = STATUS_LABELS.get(status, (status, ""))
        table.add_row(_short(path), f"[{style}]{status}[/{style}]" if style else status, detail)
    console.print(table)


def show_actions(rows: Sequence[tuple[str, str, str | None, str | None, str]]) -> None:
    """Tabla de los ultimos cambios hechos en la biblioteca.

    Args:
        rows: `(cuando, accion, archivo, respaldo, detalle)`.
    """
    if not rows:
        return
    table = Table(title="Últimos cambios", border_style="cyan", header_style="bold cyan")
    table.add_column("Cuándo", no_wrap=True)
    table.add_column("Acción", no_wrap=True)
    table.add_column("Archivo", overflow="fold")
    table.add_column("Respaldo / detalle", overflow="fold")
    for at, action, file, backup, detail in rows:
        table.add_row(at.replace("T", " ")[:19], action, _short(file or ""), backup or detail)
    console.print(table)


def info(message: str) -> None:
    """Imprime una linea de progreso.

    Args:
        message: Texto.
    """
    console.print(message, highlight=False)


def _short(path: str) -> str:
    parts = Path(path).parts
    return str(Path(*parts[-3:])) if len(parts) > 3 else path
