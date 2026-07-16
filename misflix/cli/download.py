import typer

from misflix.core.services.download_service import DownloadService
from misflix.providers.registry import get_provider
from misflix.ui import prompts

app = typer.Typer(help="Descargar un resultado previamente encontrado.")


@app.command("run")
def run(source: str, media_id: str):
    """Descarga el media `media_id` desde el repo `source`, mostrando la portada y pidiendo confirmacion."""
    provider = get_provider(source)
    media = provider.get_media(media_id)

    prompts.show_cover(media)
    options = provider.get_download_options(media)
    option = prompts.choose_option(options)
    dest_dir = prompts.choose_destination(media)

    if not prompts.confirm_download(media, option, dest_dir):
        raise typer.Exit()

    service = DownloadService()
    service.download(media, option, dest_dir)
