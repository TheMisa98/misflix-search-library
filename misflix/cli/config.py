import typer

from misflix.config.settings import get_settings

app = typer.Typer(help="Ver o ajustar la configuracion del CLI.")


@app.command("show")
def show():
    """Muestra la configuracion actual (carpetas de descarga, etc.)."""
    settings = get_settings()
    typer.echo(settings)
