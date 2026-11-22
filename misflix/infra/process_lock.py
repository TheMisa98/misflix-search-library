from __future__ import annotations

import fcntl
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def exclusive_lock(path: Path) -> Iterator[bool]:
    """Toma un candado exclusivo entre procesos sobre `path`, sin esperar.

    Lo usa el gestor de subtitulos para que nunca corran dos pasadas a la vez
    (ej. el servicio de systemd y un `misflix subs run` manual): las dos
    podrian mover o reescribir el mismo .srt al mismo tiempo. El candado es
    de `flock`, asi que se libera solo si el proceso muere.

    Args:
        path: Archivo de candado (se crea si no existe).

    Yields:
        True si se obtuvo el candado, False si otro proceso ya lo tiene.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)
