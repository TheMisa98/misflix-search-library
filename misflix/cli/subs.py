"""Comandos `misflix subs ...`: subtitulos en español para la biblioteca."""

from __future__ import annotations

import shutil
import subprocess
import sys
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Annotated

import typer

from misflix.config.settings import Settings, get_settings, user_config_file
from misflix.core.services.subtitle_service import (
    LibraryVideo,
    RunSummary,
    SubtitleService,
    SubtitleStatus,
    VideoRecord,
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
    settings = get_settings()
    if path is not None and _outside_library(path, settings):
        view.info(
            f"[yellow]{path} no está dentro de la biblioteca[/yellow] (películas: {settings.movies_dir}, "
            f"series: {settings.series_dir}). Revisa MISFLIX_MOVIES_DIR/MISFLIX_SERIES_DIR en {user_config_file()}."
        )
        raise typer.Exit(1)
    service = _service(log=view.info)
    summary = _run_once(service, path, force=force, download=not no_download, verbose=True)
    if summary.busy:
        view.info(
            "[yellow]Hay otra pasada en curso (probablemente el servicio).[/yellow] Esperá a que termine, "
            f"o detenelo con: systemctl --user stop {_SERVICE_NAME}"
        )
        raise typer.Exit(1)
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
        notices = _PassNotices(notify)
        summary = _run_once(service, None, force=False, download=True, verbose=False, on_result=notices.on_result)
        notices.finish()
        if summary.busy:
            print("hay otra pasada en curso (manual); se reintenta en el proximo intervalo", flush=True)
        wait = timedelta(minutes=interval)
        if summary.aborted:
            blocked_backoff = min(_MAX_BLOCKED_BACKOFF, max(timedelta(hours=1), blocked_backoff * 2))
            wait = max(wait, blocked_backoff)
            print(f"subdivx bloqueado ({summary.aborted}); proximo intento en {wait}", flush=True)
        else:
            blocked_backoff = timedelta(0)
        if summary.processed:
            print(_pass_line(summary, wait), flush=True)
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


def _run_once(
    service: SubtitleService,
    path: Path | None,
    force: bool,
    download: bool,
    verbose: bool,
    on_result: Callable[[LibraryVideo, VideoRecord | None, VideoRecord], None] | None = None,
) -> RunSummary:
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
        on_result=on_result,
    )


def _outside_library(path: Path, settings: Settings) -> bool:
    """Indica si `path` no cae bajo las carpetas de la biblioteca (una pasada ahi no evaluaria nada)."""
    target = path.resolve()
    roots = (settings.movies_dir.resolve(), settings.series_dir.resolve())
    return not any(target == root or root in target.parents for root in roots)


def _pass_line(summary: RunSummary, wait: timedelta) -> str:
    """Linea del log al terminar una pasada del daemon (asi se sabe que termino y cuando sigue)."""
    counts = ", ".join(f"{count} {status}" for status, count in summary.by_status.most_common())
    minutes = int(wait.total_seconds() // 60)
    return f"pasada terminada: {summary.processed} videos evaluados ({counts}); la proxima en {minutes} min"


# --- avisos de escritorio del daemon ------------------------------------------

# Videos nuevos que avisan uno por uno en una misma pasada; los demas van en
# el aviso del final (agregar una temporada entera no llena la pantalla).
_MAX_NEW_VIDEO_NOTICES = 5
# Hasta cuantos cambios nombra uno por uno el aviso del final (si hay mas, los cuenta).
_MAX_NAMED_CHANGES = 3
# Estados en los que el video no tiene un subtitulo en español que sirva.
_WITHOUT_SUBTITLE = {
    SubtitleStatus.UNCERTAIN,
    SubtitleStatus.NOT_FOUND,
    SubtitleStatus.PENDING,
    SubtitleStatus.NO_REFERENCE,
    SubtitleStatus.ERROR,
}


@dataclass(frozen=True)
class _Outcome:
    """Como se cuenta un estado en los avisos de escritorio.

    Attributes:
        title: Titulo del aviso de un video nuevo.
        sentence: Que paso con el video.
        one: Como se cuenta en el aviso del final ("1 subtítulo bajado").
        many: Lo mismo en plural ("3 subtítulos bajados").
    """

    title: str
    sentence: str
    one: str
    many: str


_OUTCOMES = {
    SubtitleStatus.DOWNLOADED: _Outcome(
        "Subtítulo listo", "Se bajó uno y coincide con el video.", "subtítulo bajado", "subtítulos bajados"
    ),
    SubtitleStatus.IN_SYNC: _Outcome(
        "Subtítulo listo", "Ya tenía uno en español y está bien.", "subtítulo verificado", "subtítulos verificados"
    ),
    SubtitleStatus.FIXED: _Outcome(
        "Subtítulo listo",
        "Ya tenía uno en español; se resincronizó.",
        "subtítulo resincronizado",
        "subtítulos resincronizados",
    ),
    SubtitleStatus.UNCERTAIN: _Outcome(
        "Subtítulo dudoso",
        "Hay uno, pero no se pudo confirmar que sea de este video.",
        "subtítulo dudoso",
        "subtítulos dudosos",
    ),
    SubtitleStatus.NOT_FOUND: _Outcome(
        "Sin subtítulo por ahora", "No se encontró uno que coincida con el video.", "sin subtítulo", "sin subtítulo"
    ),
    SubtitleStatus.NO_REFERENCE: _Outcome(
        "Sin subtítulo por ahora",
        "No tiene audio ni subtítulo en inglés contra qué verificar.",
        "sin subtítulo",
        "sin subtítulo",
    ),
    SubtitleStatus.PENDING: _Outcome("Subtítulo pendiente", "No se pudo buscar en subdivx.", "pendiente", "pendientes"),
    SubtitleStatus.ERROR: _Outcome("No se pudo revisar", "Hubo un error al leer el video.", "con error", "con error"),
    SubtitleStatus.SPANISH_AUDIO: _Outcome(
        "Ya está en español", "Tiene doblaje en español.", "ya en español", "ya en español"
    ),
    SubtitleStatus.SPANISH_TRACK: _Outcome(
        "Ya está en español", "Trae subtítulos en español incrustados.", "ya en español", "ya en español"
    ),
    SubtitleStatus.SPANISH_RELEASE: _Outcome(
        "Ya está en español", "El nombre del archivo dice que está en español.", "ya en español", "ya en español"
    ),
    SubtitleStatus.SPANISH_BURNED: _Outcome(
        "Ya está en español", "Tiene subtítulos en español en la imagen.", "ya en español", "ya en español"
    ),
}


class _PassNotices:
    """Avisos de escritorio de una pasada del daemon.

    Cada video nuevo (nunca evaluado) avisa apenas se termina con el, con su
    resultado, hasta `_MAX_NEW_VIDEO_NOTICES` por pasada. Lo demas que cambio
    los subtitulos de la biblioteca (bajados, resincronizados, restaurados,
    dudosos nuevos) y los nuevos que pasaron el tope van en un solo aviso al
    final. Un video que se reevalua y queda igual (un dudoso que sigue
    dudoso) no avisa.
    """

    def __init__(self, send: Callable[[str, str], None]) -> None:
        self._send = send
        self._new_notices = 0
        self._rest: list[tuple[LibraryVideo, VideoRecord]] = []

    def on_result(self, video: LibraryVideo, previous: VideoRecord | None, record: VideoRecord) -> None:
        """Avisa (o anota para el final) el resultado de un video; es el `on_result` de `SubtitleService.run`."""
        if previous is None and self._new_notices < _MAX_NEW_VIDEO_NOTICES:
            self._new_notices += 1
            outcome = _OUTCOMES[SubtitleStatus(record.status)]
            self._send(outcome.title, f"{_video_name(video)}\n{outcome.sentence}{_retry_note(record)}")
        elif previous is None or _changed_subtitles(previous, record):
            self._rest.append((video, record))

    def finish(self) -> None:
        """Manda el aviso del final de la pasada con lo que no aviso uno por uno, si hay algo."""
        if not self._rest:
            return
        outcomes = [(video, _OUTCOMES[SubtitleStatus(record.status)]) for video, record in self._rest]
        if len(outcomes) <= _MAX_NAMED_CHANGES:
            body = "\n".join(f"{_video_name(video)}: {outcome.one}" for video, outcome in outcomes)
        else:
            counts = Counter((outcome.one, outcome.many) for _, outcome in outcomes)
            body = ", ".join(f"{count} {one if count == 1 else many}" for (one, many), count in counts.most_common())
        self._send("Subtítulos actualizados", body)
        self._rest.clear()


def _changed_subtitles(previous: VideoRecord, record: VideoRecord) -> bool:
    """Indica si la pasada cambio los subtitulos de un video ya conocido (y vale un aviso)."""
    if record.status in (SubtitleStatus.DOWNLOADED, SubtitleStatus.FIXED):
        return True  # se escribio un .srt en esta pasada
    if record.status == SubtitleStatus.IN_SYNC:
        return previous.status in _WITHOUT_SUBTITLE  # restaurado, dudoso confirmado o agregado a mano
    return record.status == SubtitleStatus.UNCERTAIN and previous.status != SubtitleStatus.UNCERTAIN


def _video_name(video: LibraryVideo) -> str:
    """Nombre corto para un aviso: "House 6x03", "Kick-Ass (2010)"."""
    title = video.titles[0] if video.titles else video.path.stem
    if video.kind == "episode" and video.season is not None and video.episode is not None:
        return f"{title} {video.season}x{video.episode:02d}"
    return f"{title} ({video.year})" if video.year else title


def _retry_note(record: VideoRecord) -> str:
    """Cuando se vuelve a intentar, si el estado lo admite (" Se vuelve a intentar el 29/11.")."""
    if record.retry_after is None:
        return ""
    when = datetime.fromisoformat(record.retry_after).astimezone()
    if when.date() == datetime.now().astimezone().date():
        return f" Se vuelve a intentar a las {when:%H:%M}."
    return f" Se vuelve a intentar el {when:%d/%m}."
