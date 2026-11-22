"""Comandos `misflix subs ...`: subtitulos en español para la biblioteca."""

from __future__ import annotations

import shutil
import subprocess
import sys
import time
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path
from typing import Annotated

import typer

from misflix.config.settings import get_settings
from misflix.core.services.subtitle_service import (
    LibraryVideo,
    RunSummary,
    SubtitleService,
    create_subtitle_service,
    notify,
    open_store,
)
from misflix.providers.registry import get_subtitle_provider
from misflix.ui import subtitles as view

app = typer.Typer(help="Subtítulos en español para la biblioteca: verificar, corregir y bajar de subdivx.")

_PROBLEM_STATUSES = ["dudoso", "no_encontrado", "sin_referencia", "error", "pendiente"]
_SERVICE_NAME = "misflix-subs.service"
# Si subdivx quedo bloqueado (nadie resolvio Cloudflare), no reabrir el
# navegador en cada pasada: esperar cada vez mas, hasta este tope.
_MAX_BLOCKED_BACKOFF = timedelta(hours=8)


@app.command("run")
def run(
    path: Annotated[
        Path | None, typer.Argument(help="Solo videos bajo esta ruta (carpeta de una serie, temporada...).")
    ] = None,
    force: bool = typer.Option(False, "--force", help="Reevaluar aunque ya esté listo."),
    no_download: bool = typer.Option(False, "--no-download", help="Solo verificar/corregir lo que ya hay."),
) -> None:
    """Hace una pasada: verifica los subtítulos existentes, corrige los desfasados y baja los que faltan.

    Args:
        path: Si se da, solo videos bajo esta ruta.
        force: Reevaluar aunque el estado guardado diga que está listo.
        no_download: No buscar en subdivx.
    """
    service = _service(log=view.info)
    summary = _run_once(service, path, force=force, download=not no_download, verbose=True)
    view.show_counts(summary.by_status, f"Pasada: {summary.processed} de {summary.scanned} videos evaluados")
    if summary.aborted:
        view.info(f"[yellow]subdivx no disponible: {summary.aborted}[/yellow]")


@app.command("daemon")
def daemon(interval: int = typer.Option(15, help="Minutos entre pasadas.")) -> None:
    """Corre pasadas para siempre (lo que ejecuta el servicio de systemd).

    Args:
        interval: Minutos de espera entre pasadas.
    """
    service = _service(log=lambda message: print(message, flush=True))
    blocked_backoff = timedelta(0)
    while True:
        summary = _run_once(service, None, force=False, download=True, verbose=False)
        changed = {s: summary.by_status[s] for s in ("descargado", "corregido", "dudoso") if summary.by_status[s]}
        if changed:
            notify("Subtítulos actualizados", ", ".join(f"{count} {status}" for status, count in changed.items()))
        wait = timedelta(minutes=interval)
        if summary.aborted:
            blocked_backoff = min(_MAX_BLOCKED_BACKOFF, max(timedelta(hours=1), blocked_backoff * 2))
            wait = max(wait, blocked_backoff)
            print(f"subdivx bloqueado ({summary.aborted}); proximo intento en {wait}", flush=True)
        else:
            blocked_backoff = timedelta(0)
        time.sleep(wait.total_seconds())


@app.command("status")
def status(
    path: Annotated[Path | None, typer.Argument(help="Solo videos bajo esta ruta.")] = None,
    all_rows: bool = typer.Option(False, "--todos", help="Listar todos los videos, no solo los que tienen problemas."),
    actions: int = typer.Option(10, "--cambios", help="Cuántos de los últimos cambios mostrar."),
) -> None:
    """Muestra el estado guardado: cuántos videos hay en cada estado y cuáles necesitan atención.

    Args:
        path: Si se da, solo videos bajo esta ruta.
        all_rows: Listar todos los videos.
        actions: Cantidad de cambios recientes a mostrar.
    """
    store = open_store(get_settings().state_dir)
    prefix = str(path.resolve()) if path else None
    records = store.videos(None if all_rows else _PROBLEM_STATUSES, prefix)
    view.show_counts(store.summary(), "Subtítulos en español")
    view.show_videos([(r.path, r.status, r.detail) for r in records], "Videos" if all_rows else "Necesitan atención")
    view.show_actions([(a.at, a.action, a.file, a.backup, a.detail) for a in store.recent_actions(actions)])


@app.command("sync")
def sync(
    video: Annotated[Path, typer.Argument(exists=True, dir_okay=False, help="Archivo de video.")],
    subtitle: Annotated[Path, typer.Argument(exists=True, dir_okay=False, help="Subtítulo .srt a sincronizar.")],
) -> None:
    """Verifica y sincroniza un .srt puntual contra su video (lo escribe como <video>.es.srt).

    Args:
        video: Archivo de video.
        subtitle: Subtítulo a sincronizar.
    """
    service = _service(log=view.info)
    report, written = service.sync_file(video, subtitle)
    if report is None:
        view.info("[red]El video no tiene subtítulo ni audio contra el cual verificar.[/red]")
        raise typer.Exit(1)
    view.info(
        f"Veredicto: [bold]{report.verdict}[/bold] contra {report.reference} "
        f"(puntaje {report.score:+.2f}, {report.alignment.cuts} cortes, ratio {report.alignment.ratio:.4f})"
    )
    if written is None:
        view.info("[yellow]No se escribió nada: el subtítulo no corresponde al video o no es concluyente.[/yellow]")
        raise typer.Exit(1)
    view.info(f"[green]Escrito:[/green] {written}")


@app.command("install-service")
def install_service(interval: int = typer.Option(15, help="Minutos entre pasadas.")) -> None:
    """Instala y arranca el servicio de systemd (de usuario) que corre `misflix subs daemon`.

    Args:
        interval: Minutos entre pasadas.
    """
    executable = shutil.which("misflix") or sys.argv[0]
    settings = get_settings()
    missing = [str(d) for d in (settings.movies_dir, settings.series_dir) if not d.is_dir()]
    if missing:
        view.info(
            f"[red]No existen {', '.join(missing)}.[/red] Definí MISFLIX_MOVIES_DIR/MISFLIX_SERIES_DIR "
            "(o corré esto desde el repo, donde está el .env) antes de instalar el servicio."
        )
        raise typer.Exit(1)
    unit_path = Path.home() / ".config" / "systemd" / "user" / _SERVICE_NAME
    unit_path.parent.mkdir(parents=True, exist_ok=True)
    unit_path.write_text(
        "[Unit]\n"
        "Description=misflix: subtitulos en español para la biblioteca (subdivx)\n"
        "After=graphical-session.target\n\n"
        "[Service]\n"
        f"ExecStart={executable} subs daemon --interval {interval}\n"
        f"Environment=MISFLIX_MOVIES_DIR={settings.movies_dir}\n"
        f"Environment=MISFLIX_SERIES_DIR={settings.series_dir}\n"
        f"Environment=MISFLIX_BOOKS_DIR={settings.books_dir}\n"
        "Restart=on-failure\n"
        "RestartSec=60\n"
        "Nice=15\n"
        "IOSchedulingClass=idle\n\n"
        "[Install]\n"
        "WantedBy=default.target\n"
    )
    for command in (["daemon-reload"], ["enable", "--now", _SERVICE_NAME], ["restart", _SERVICE_NAME]):
        subprocess.run(["systemctl", "--user", *command], check=True)
    view.info(f"[green]Servicio instalado y corriendo:[/green] {unit_path}")
    view.info(f"Logs: journalctl --user -u {_SERVICE_NAME} -f")


def _service(log: Callable[[str], None]) -> SubtitleService:
    settings = get_settings()

    def on_challenge(domain: str) -> None:
        notify(
            "misflix: subdivx pide verificación", f"Se abrió {domain} en el navegador: resolvé el captcha.", "critical"
        )

    return create_subtitle_service(get_subtitle_provider(on_challenge), settings.state_dir, settings.cache_dir, log)


def _run_once(service: SubtitleService, path: Path | None, force: bool, download: bool, verbose: bool) -> RunSummary:
    settings = get_settings()

    def progress(index: int, total: int, video: LibraryVideo) -> None:
        if verbose:
            view.info(f"[dim]({index}/{total}) {video.path.name}[/dim]")

    return service.run(
        settings.movies_dir,
        settings.series_dir,
        path_prefix=path.resolve() if path else None,
        force=force,
        download=download,
        on_progress=progress,
    )
