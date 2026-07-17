from __future__ import annotations

import re
from pathlib import Path

# Compartido por infra/archives.py (para ordenar volumenes .partN.rar) y
# ui/prompts.py (para ordenar los links pegados por el usuario segun el
# numero de parte en el nombre de archivo) — antes duplicado en ambos.
_PART_RE = re.compile(r"[._-]part0*(\d+)", re.IGNORECASE)


def sanitize_filename(name: str) -> str:
    """Quita caracteres invalidos para nombres de archivo.

    Args:
        name: Nombre propuesto.

    Returns:
        `name` sin caracteres invalidos en Windows/Linux, sin espacios al
        borde.
    """
    return re.sub(r'[<>:"/\\|?*]', "_", name).strip()


def ensure_dir(path: Path) -> Path:
    """Crea `path` (y sus padres) si no existe.

    Args:
        path: Carpeta a crear.

    Returns:
        `path`, para poder encadenar la llamada.
    """
    path.mkdir(parents=True, exist_ok=True)
    return path


def part_number(name: str) -> int | None:
    """Numero de parte codificado en un nombre de archivo o url.

    Reconoce el patron `partN` (con separador `.`/`_`/`-` antes, ceros a la
    izquierda opcionales) que usan tanto los volumenes `.partN.rar` de un
    archivo bajado como los nombres de archivo que sirve Mediafire.

    Args:
        name: Nombre de archivo o url a inspeccionar.

    Returns:
        El numero de parte, o None si `name` no trae ese patron.
    """
    match = _PART_RE.search(name)
    return int(match.group(1)) if match else None
