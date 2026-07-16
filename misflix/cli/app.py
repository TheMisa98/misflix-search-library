import typer

from misflix.cli import config, download, search

app = typer.Typer(
    name="misflix",
    help="CLI para buscar y descargar peliculas y libros desde tus repos, con vista previa de portadas en la terminal.",
    no_args_is_help=True,
)

app.add_typer(search.app, name="search")
app.add_typer(download.app, name="download")
app.add_typer(config.app, name="config")
