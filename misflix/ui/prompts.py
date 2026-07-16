from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console

from misflix.core.models import DownloadOption, Media
from misflix.ui.image_render import CoverRenderer

console = Console()


def show_cover(media: Media) -> None:
    console.print(f"[bold]{media.title}[/bold] ({media.kind.value}) - {media.source}")
    if media.cover_url:
        CoverRenderer().render_url(media.cover_url)


def choose_option(options: list[DownloadOption]) -> DownloadOption:
    for i, option in enumerate(options, start=1):
        console.print(f"{i}. {option.label}")

    index = typer.prompt("Elige una opcion", type=int)
    return options[index - 1]


def choose_destination(media: Media) -> Path:
    default = Path.cwd()
    raw = typer.prompt("Donde quieres guardarlo?", default=str(default))
    return Path(raw).expanduser()


def confirm_download(media: Media, option: DownloadOption, dest_dir: Path) -> bool:
    console.print(f"Se descargara [bold]{media.title}[/bold] ({option.label}) en [cyan]{dest_dir}[/cyan]")
    return typer.confirm("Confirmar descarga?")
