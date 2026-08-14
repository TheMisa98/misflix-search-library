from __future__ import annotations

import subprocess
from pathlib import Path

from misflix.infra.filesystem import part_number

_VIDEO_EXTENSIONS = {".mkv", ".mp4", ".avi", ".m4v", ".mov", ".wmv"}


class ExtractionError(RuntimeError):
    """Fallo al extraer un .rar (contraseña incorrecta, volumen faltante, etc)."""


def _part_sort_key(path: Path) -> tuple[int, int]:
    number = part_number(path.name)
    return (0, number) if number is not None else (1, 0)


def extract_rar(movie_dir: Path, password: str = "zonaleros") -> bool:
    """Extrae los .rar en `movie_dir` con unrar.

    Misma idea que el alias `extract-zone` = `unrar x -pzonaleros *.rar`;
    hace falta `unrar`, no `rar`, por incompatibilidad con archivos con
    contraseña.

    Solo se le pasa a unrar el primer volumen (`.part1.rar`), no todos: si se
    le dan todos como argumentos separados, unrar trata cada uno como el
    inicio de un archivo distinto, y al "empezar" desde un volumen que no es
    el primero (`.part2.rar` en adelante) tira "No files to extract" y
    termina en error — aunque el primero ya haya extraido todo bien.
    Apuntando solo al primero, unrar encuentra y encadena el resto de los
    volumenes el solo.

    Args:
        movie_dir: Carpeta donde estan los .rar a extraer.
        password: Contraseña de los archivos.

    Returns:
        False si no habia ningun .rar para extraer; True si se extrajo.

    Raises:
        ExtractionError: Si unrar termina con error.
    """
    rar_files = sorted(movie_dir.glob("*.rar"), key=_part_sort_key)
    if not rar_files:
        return False

    result = subprocess.run(
        ["unrar", "x", "-y", f"-p{password}", rar_files[0].name],
        cwd=movie_dir,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise ExtractionError(result.stderr.strip() or result.stdout.strip())
    return True


def find_existing_video(movie_dir: Path, stem: str) -> Path | None:
    """Video ya organizado con nombre `stem` en `movie_dir`, si existe.

    Permite reconocer un item ya bajado y extraido en una corrida anterior
    (ej. un lote de episodios cortado a mitad de camino) sin necesitar un
    estado aparte: el archivo final en disco, dejado ahi por `flatten_video`/
    `flatten_all_videos`, ya es la fuente de verdad.

    Args:
        movie_dir: Carpeta donde buscar (no recursivo: un video ya
            organizado vive siempre en la raiz, no en una subcarpeta).
        stem: Nombre de archivo (sin extension) a buscar.

    Returns:
        La ruta encontrada, o None si no hay ningun video con ese nombre.
    """
    for ext in _VIDEO_EXTENSIONS:
        candidate = movie_dir / f"{stem}{ext}"
        if candidate.exists():
            return candidate
    return None


def flatten_video(movie_dir: Path, target_stem: str) -> Path | None:
    """Mueve el video mas grande de `movie_dir` a su raiz, renombrado.

    El .rar puede extraer directo un video o una subcarpeta con el video
    adentro; esto busca el video mas grande encontrado (recursivo), lo mueve
    a la raiz de `movie_dir` renombrado a `target_stem` + su extension, y
    limpia las subcarpetas que queden vacias.

    `unrar` restaura el mtime guardado dentro del .rar al extraer (tipicamente
    la fecha en que el uploader original armo el archivo, no la de hoy) — se
    actualiza a la fecha actual antes de devolverlo para que Jellyfin (que usa
    el mtime del archivo como "fecha agregada" a falta de otro dato) lo
    reconozca como recien agregado en vez de mostrarlo con años de atraso.

    Args:
        movie_dir: Carpeta donde buscar videos (recursivo).
        target_stem: Nombre (sin extension) para el video movido.

    Returns:
        La ruta final del video, o None si no se encontro ninguno.
    """
    candidates = [p for p in movie_dir.rglob("*") if p.is_file() and p.suffix.lower() in _VIDEO_EXTENSIONS]
    if not candidates:
        return None

    video = max(candidates, key=lambda p: p.stat().st_size)
    dest = movie_dir / f"{target_stem}{video.suffix.lower()}"
    if video != dest:
        video.rename(dest)
    dest.touch()

    for entry in sorted(movie_dir.iterdir(), reverse=True):
        if entry.is_dir():
            try:
                entry.rmdir()
            except OSError:
                pass

    return dest


def flatten_all_videos(movie_dir: Path) -> list[Path]:
    """Mueve todos los videos de `movie_dir` a su raiz, conservando sus nombres.

    Como `flatten_video`, pero para un pack de varios episodios (ej. una
    temporada completa): mueve TODOS los videos encontrados (recursivo) a la
    raiz de `movie_dir`, conservando sus nombres originales en vez de
    renombrarlos a un unico `target_stem` (el pack ya suele traerlos bien
    nombrados por episodio). Limpia las subcarpetas que queden vacias. Cada
    video queda con el mtime actualizado a la fecha actual — ver el porque en
    `flatten_video`.

    Args:
        movie_dir: Carpeta donde buscar videos (recursivo).

    Returns:
        Rutas finales de los videos movidos, ordenadas.
    """
    candidates = [p for p in movie_dir.rglob("*") if p.is_file() and p.suffix.lower() in _VIDEO_EXTENSIONS]

    moved = []
    for index, video in enumerate(candidates):
        dest = movie_dir / video.name
        if dest.exists() and dest != video:
            dest = movie_dir / f"{video.stem}_{index}{video.suffix}"
        if video != dest:
            video.rename(dest)
        dest.touch()
        moved.append(dest)

    for entry in sorted(movie_dir.iterdir(), reverse=True):
        if entry.is_dir():
            try:
                entry.rmdir()
            except OSError:
                pass

    return sorted(moved)


def delete_rar_parts(movie_dir: Path) -> None:
    """Borra los .rar de `movie_dir`.

    Se llama solo despues de confirmar que el video ya quedo extraido y
    movido a su lugar (`flatten_video`/`flatten_all_videos` devolvio algo).

    Args:
        movie_dir: Carpeta donde borrar los .rar.
    """
    for rar_file in movie_dir.glob("*.rar"):
        rar_file.unlink()
