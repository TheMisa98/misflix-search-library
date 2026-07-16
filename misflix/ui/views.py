from __future__ import annotations

from rich.console import Console
from rich.table import Table

from misflix.core.models import Media

console = Console()


def show_results(results: list[Media]) -> None:
    table = Table(title="Resultados")
    table.add_column("#", style="cyan", no_wrap=True)
    table.add_column("Titulo")
    table.add_column("Tipo")
    table.add_column("Fuente")

    for i, media in enumerate(results, start=1):
        table.add_row(str(i), media.title, media.kind.value, media.source)

    console.print(table)
