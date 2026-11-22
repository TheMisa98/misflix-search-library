from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


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
    """

    movies_dir: Path
    books_dir: Path
    series_dir: Path
    state_dir: Path
    cache_dir: Path


@lru_cache
def get_settings() -> Settings:
    """Lee la configuracion desde `.env`/el entorno, cacheada tras la primera llamada.

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
    )
