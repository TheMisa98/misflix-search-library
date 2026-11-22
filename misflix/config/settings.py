from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

# El `.env` del repo: sirve al correr desde el codigo fuente (`uv run main.py`).
# El `misflix` instalado (`uv tool install`) vive en otra carpeta y no lo ve.
_REPO_ENV = Path(__file__).resolve().parents[2] / ".env"


def user_config_file() -> Path:
    """Archivo de configuracion del usuario, el que lee el `misflix` instalado corra desde donde corra.

    Returns:
        `$XDG_CONFIG_HOME/misflix/.env` (por defecto `~/.config/misflix/.env`).
    """
    return Path(os.getenv("XDG_CONFIG_HOME", "~/.config")).expanduser() / "misflix" / ".env"


def existing_config_files() -> tuple[Path, ...]:
    """Los `.env` de los que se lee la configuracion, en orden de prioridad (solo los que existen).

    Returns:
        El `.env` del repo (si se corre desde el codigo fuente) y el del
        usuario (ver `user_config_file`), los que existan.
    """
    return tuple(path for path in (_REPO_ENV, user_config_file()) if path.is_file())


def load_config_files(files: Sequence[Path]) -> None:
    """Carga en el entorno las variables de `files`, sin pisar las que ya estan seteadas.

    Gana el primer archivo que define cada variable, y las variables de
    entorno reales (ej. las de la unidad de systemd) ganan siempre.

    Args:
        files: `.env` en orden de prioridad.
    """
    for path in files:
        load_dotenv(path, override=False)


load_config_files(existing_config_files())


@dataclass(frozen=True)
class Settings:
    """Configuracion del CLI leida de variables de entorno.

    Attributes:
        movies_dir: Carpeta destino para peliculas.
        books_dir: Carpeta destino para libros.
        series_dir: Carpeta destino para series.
        state_dir: Estado persistente (la base SQLite de subtitulos y los
            respaldos de los .srt reemplazados).
        cache_dir: Cache regenerable (referencias de sincronia extraidas de
            cada video).
        config_files: Los `.env` que se leyeron (ver `existing_config_files`).
    """

    movies_dir: Path
    books_dir: Path
    series_dir: Path
    state_dir: Path
    cache_dir: Path
    config_files: tuple[Path, ...] = ()


@lru_cache
def get_settings() -> Settings:
    """Lee la configuracion desde el entorno y los `.env`, cacheada tras la primera llamada.

    Returns:
        La configuracion resuelta, con valores por defecto bajo
        `~/Descargas/` para lo que no este seteado.
    """
    return Settings(
        movies_dir=Path(os.getenv("MISFLIX_MOVIES_DIR", "~/Descargas/Peliculas")).expanduser(),
        books_dir=Path(os.getenv("MISFLIX_BOOKS_DIR", "~/Descargas/Libros")).expanduser(),
        series_dir=Path(os.getenv("MISFLIX_SERIES_DIR", "~/Descargas/Series")).expanduser(),
        state_dir=Path(os.getenv("XDG_STATE_HOME", "~/.local/state")).expanduser() / "misflix",
        cache_dir=Path(os.getenv("XDG_CACHE_HOME", "~/.cache")).expanduser() / "misflix",
        config_files=existing_config_files(),
    )
