from __future__ import annotations

import glob
import shutil
import sqlite3
import tempfile
from pathlib import Path

# Perfiles Firefox-based donde puede vivir una cf_clearance ya resuelta a mano.
_PROFILE_GLOBS = [
    "~/.zen/*/cookies.sqlite",
    "~/.mozilla/firefox/*/cookies.sqlite",
]

# En algunos perfiles `expiry` esta en milisegundos en vez de segundos unix.
# Cualquier valor de 11+ digitos no puede ser una fecha valida en segundos
# durante los proximos milenios, asi que se interpreta como milisegundos.
_MS_THRESHOLD = 10_000_000_000


def _candidate_cookie_dbs() -> list[Path]:
    """Rutas existentes de `cookies.sqlite` entre los perfiles conocidos.

    Returns:
        Rutas encontradas, en el orden en que aparecen `_PROFILE_GLOBS`.
    """
    paths: list[Path] = []
    for pattern in _PROFILE_GLOBS:
        expanded = Path(pattern).expanduser()
        paths.extend(Path(p) for p in glob.glob(str(expanded)))
    return paths


def _normalize_expiry(expiry: int) -> int:
    """Convierte `expiry` a segundos unix si vino en milisegundos.

    Args:
        expiry: Valor crudo de la columna `expiry` de `moz_cookies`.

    Returns:
        `expiry` en segundos unix.
    """
    return expiry // 1000 if expiry > _MS_THRESHOLD else expiry


def _read_cookies(db_path: Path, domain: str) -> list[tuple[str, str, int]]:
    """Lee las cookies de `domain` desde una copia temporal de `db_path`.

    Args:
        db_path: Ruta al `cookies.sqlite` de un perfil.
        domain: Dominio a filtrar (sufijo de `host`).

    Returns:
        Tuplas `(nombre, valor, expiry)` encontradas.
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_db = Path(tmp_dir) / "cookies.sqlite"
        shutil.copy(db_path, tmp_db)
        for suffix in ("-wal", "-shm"):
            side_file = db_path.with_name(db_path.name + suffix)
            if side_file.exists():
                shutil.copy(side_file, Path(tmp_dir) / (tmp_db.name + suffix))

        conn = sqlite3.connect(f"file:{tmp_db}?mode=ro", uri=True)
        try:
            return conn.execute(
                "SELECT name, value, expiry FROM moz_cookies WHERE host LIKE ?",
                (f"%{domain}",),
            ).fetchall()
        finally:
            conn.close()


def load_domain_cookies(domain: str) -> dict[str, str]:
    """Lee la cookie mas reciente de cada nombre para `domain`.

    Busca entre los perfiles Firefox-based disponibles (ej. la
    `cf_clearance` que el usuario resolvio a mano en su navegador normal).

    Args:
        domain: Dominio para el que buscar cookies.

    Returns:
        Diccionario nombre -> valor, con la version mas reciente de cada
        cookie entre todos los perfiles encontrados.
    """
    best: dict[str, tuple[int, str]] = {}
    for db_path in _candidate_cookie_dbs():
        try:
            rows = _read_cookies(db_path, domain)
        except sqlite3.DatabaseError:
            continue
        for name, value, expiry in rows:
            normalized = _normalize_expiry(expiry)
            if name not in best or normalized > best[name][0]:
                best[name] = (normalized, value)
    return {name: value for name, (_, value) in best.items()}
