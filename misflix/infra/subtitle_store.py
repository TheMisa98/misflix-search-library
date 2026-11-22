"""Estado persistente del gestor de subtitulos, en SQLite.

SQLite y no un JSON porque el proceso en segundo plano y los comandos
manuales (`misflix subs status`, `misflix subs run --path ...`) pueden
escribir a la vez: SQLite serializa esas escrituras (modo WAL + timeout)
donde un JSON reescrito entero perderia una de las dos, o quedaria
corrupto si se corta a mitad de escritura. Viene con Python, asi que no es
una dependencia nueva (ver `docs/DECISIONS.md`).

Guarda:

- `videos`: el estado de cada video (con size/mtime para detectar si el
  archivo cambio y hay que reevaluarlo).
- `probes`: la salida de ffprobe cacheada (leer la cabecera de ~1700 videos
  en el disco NTFS tarda ~2 minutos; con cache, un rescan es casi gratis).
- `attempts`: cada candidato probado y su veredicto, para no volver a bajar
  uno que ya se sabe que no sirve.
- `searches`: busquedas cacheadas por un tiempo (subdivx no cambia tan seguido).
- `actions`: registro de cada archivo escrito o movido a respaldo, para
  poder deshacer a mano.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from misflix.core.models import SubtitleCandidate
from misflix.core.subtitles.media import MediaInfo, StreamInfo
from misflix.infra.spanish_detection import SpanishDetection

_SCHEMA = """
CREATE TABLE IF NOT EXISTS videos (
    path TEXT PRIMARY KEY,
    size INTEGER NOT NULL,
    mtime REAL NOT NULL,
    status TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '',
    subtitle_path TEXT,
    reference TEXT,
    score REAL,
    updated_at TEXT NOT NULL,
    retry_after TEXT,
    sidecars TEXT NOT NULL DEFAULT '',
    detected TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS detections (
    path TEXT PRIMARY KEY,
    size INTEGER NOT NULL,
    mtime REAL NOT NULL,
    tools TEXT NOT NULL,
    audio_languages TEXT NOT NULL,
    burned_language TEXT
);
CREATE TABLE IF NOT EXISTS probes (
    path TEXT PRIMARY KEY,
    size INTEGER NOT NULL,
    mtime REAL NOT NULL,
    info TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS attempts (
    video_path TEXT NOT NULL,
    source TEXT NOT NULL,
    candidate_id TEXT NOT NULL,
    verdict TEXT NOT NULL,
    score REAL,
    tried_at TEXT NOT NULL,
    PRIMARY KEY (video_path, source, candidate_id)
);
CREATE TABLE IF NOT EXISTS searches (
    source TEXT NOT NULL,
    query TEXT NOT NULL,
    results TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    PRIMARY KEY (source, query)
);
CREATE TABLE IF NOT EXISTS actions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT NOT NULL,
    video_path TEXT NOT NULL,
    action TEXT NOT NULL,
    file TEXT,
    backup TEXT,
    detail TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS videos_status ON videos (status);
"""


@dataclass(frozen=True)
class VideoRecord:
    """Estado guardado de un video.

    Attributes:
        path: Ruta del video.
        size: Tamaño del archivo cuando se evaluo.
        mtime: mtime del archivo cuando se evaluo.
        status: Estado (ver `SubtitleStatus` en `core/services/subtitle_service.py`).
        detail: Explicacion legible del estado.
        subtitle_path: Subtitulo español resultante, si hay.
        reference: Contra que se verifico ("subtitulos"/"audio"), si se verifico.
        score: Puntaje de la verificacion, si se verifico.
        updated_at: Cuando se actualizo (ISO, UTC).
        retry_after: Antes de esto no se reintenta (ISO, UTC), o None.
        sidecars: Firma de los .srt que habia al lado del video al evaluarlo
            (si cambia, por ejemplo porque el usuario agrego uno a mano, el
            video se reevalua).
        detected: Detecciones de español que se pudieron correr al evaluarlo
            ("ocr,voz"); si despues se instala una herramienta que faltaba,
            el video se reevalua.
    """

    path: str
    size: int
    mtime: float
    status: str
    detail: str = ""
    subtitle_path: str | None = None
    reference: str | None = None
    score: float | None = None
    updated_at: str = ""
    retry_after: str | None = None
    sidecars: str = ""
    detected: str = ""


@dataclass(frozen=True)
class ActionRecord:
    """Un cambio hecho en la biblioteca.

    Attributes:
        at: Cuando (ISO, UTC).
        video_path: Video afectado.
        action: Que se hizo ("escrito", "respaldado", ...).
        file: Archivo escrito o movido.
        backup: Donde quedo la copia de respaldo, si hubo.
        detail: Explicacion.
    """

    at: str
    video_path: str
    action: str
    file: str | None
    backup: str | None
    detail: str


class SubtitleStore:
    """Acceso a la base SQLite de estado de subtitulos."""

    def __init__(self, path: Path):
        """Abre (o crea) la base en `path`.

        Args:
            path: Archivo SQLite.
        """
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        with self._connect() as conn:
            conn.executescript(_SCHEMA)
            # bases creadas antes de existir la columna `detected`
            columns = {row[1] for row in conn.execute("PRAGMA table_info(videos)")}
            if "detected" not in columns:
                conn.execute("ALTER TABLE videos ADD COLUMN detected TEXT NOT NULL DEFAULT ''")

    def get_probe(self, path: Path, size: int, mtime: float) -> MediaInfo | None:
        """Salida de ffprobe cacheada de `path`, si el archivo no cambio desde entonces.

        Args:
            path: Video.
            size: Tamaño actual.
            mtime: mtime actual.

        Returns:
            Los datos cacheados, o None si no hay o estan desactualizados.
        """
        with self._connect() as conn:
            row = conn.execute(
                "SELECT info FROM probes WHERE path = ? AND size = ? AND mtime = ?", (str(path), size, mtime)
            ).fetchone()
        if row is None:
            return None
        data = json.loads(row[0])
        return MediaInfo(
            duration=data["duration"],
            streams=tuple(StreamInfo(**stream) for stream in data["streams"]),
            format_title=data.get("format_title"),
        )

    def put_probe(self, path: Path, size: int, mtime: float, info: MediaInfo) -> None:
        """Cachea la salida de ffprobe de `path`.

        Args:
            path: Video.
            size: Tamaño actual.
            mtime: mtime actual.
            info: Datos a guardar.
        """
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO probes (path, size, mtime, info) VALUES (?, ?, ?, ?)",
                (str(path), size, mtime, json.dumps(asdict(info))),
            )

    def get_detection(self, path: Path, size: int, mtime: float) -> SpanishDetection | None:
        """Deteccion de español cacheada de `path`, si el archivo no cambio desde entonces.

        Args:
            path: Video.
            size: Tamaño actual.
            mtime: mtime actual.

        Returns:
            La deteccion cacheada, o None.
        """
        with self._connect() as conn:
            row = conn.execute(
                "SELECT tools, audio_languages, burned_language FROM detections"
                " WHERE path = ? AND size = ? AND mtime = ?",
                (str(path), size, mtime),
            ).fetchone()
        if row is None:
            return None
        return SpanishDetection(
            audio_languages={int(index): language for index, language in json.loads(row[1]).items()},
            burned_language=row[2],
            tools=frozenset(filter(None, row[0].split(","))),
        )

    def put_detection(self, path: Path, size: int, mtime: float, detection: SpanishDetection) -> None:
        """Cachea la deteccion de español de `path`.

        Args:
            path: Video.
            size: Tamaño actual.
            mtime: mtime actual.
            detection: Resultado a guardar.
        """
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO detections VALUES (?, ?, ?, ?, ?, ?)",
                (
                    str(path),
                    size,
                    mtime,
                    ",".join(sorted(detection.tools)),
                    json.dumps(detection.audio_languages),
                    detection.burned_language,
                ),
            )

    def get_video(self, path: Path) -> VideoRecord | None:
        """Estado guardado de `path`.

        Args:
            path: Video.

        Returns:
            El registro, o None si nunca se evaluo.
        """
        with self._connect() as conn:
            row = conn.execute(f"SELECT {_VIDEO_COLUMNS} FROM videos WHERE path = ?", (str(path),)).fetchone()
        return VideoRecord(*row) if row else None

    def put_video(self, record: VideoRecord) -> None:
        """Guarda (o reemplaza) el estado de un video, sellando `updated_at`.

        Args:
            record: Estado a guardar.
        """
        values = asdict(record) | {"updated_at": _now()}
        with self._connect() as conn:
            conn.execute(
                f"INSERT OR REPLACE INTO videos ({_VIDEO_COLUMNS}) VALUES ({', '.join('?' * len(values))})",
                tuple(values.values()),
            )

    def videos(self, statuses: Sequence[str] | None = None, path_prefix: str | None = None) -> list[VideoRecord]:
        """Lista estados guardados, opcionalmente filtrados.

        Args:
            statuses: Si se da, solo esos estados.
            path_prefix: Si se da, solo videos bajo esa ruta.

        Returns:
            Los registros, ordenados por ruta.
        """
        query = f"SELECT {_VIDEO_COLUMNS} FROM videos WHERE 1 = 1"
        params: list[str] = []
        if statuses:
            query += f" AND status IN ({', '.join('?' * len(statuses))})"
            params += list(statuses)
        if path_prefix:
            query += " AND path LIKE ? ESCAPE '\\'"
            params.append(_like_prefix(path_prefix))
        with self._connect() as conn:
            return [VideoRecord(*row) for row in conn.execute(query + " ORDER BY path", params)]

    def summary(self) -> dict[str, int]:
        """Cantidad de videos por estado.

        Returns:
            Estado -> cantidad.
        """
        with self._connect() as conn:
            return dict(conn.execute("SELECT status, COUNT(*) FROM videos GROUP BY status ORDER BY status"))

    def was_tried(self, video_path: Path, source: str, candidate_id: str) -> bool:
        """Indica si ese candidato ya se probo para ese video.

        Args:
            video_path: Video.
            source: Provider del candidato.
            candidate_id: Id del candidato.

        Returns:
            True si ya hay un intento registrado.
        """
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM attempts WHERE video_path = ? AND source = ? AND candidate_id = ?",
                (str(video_path), source, candidate_id),
            ).fetchone()
        return row is not None

    def add_attempt(self, video_path: Path, source: str, candidate_id: str, verdict: str, score: float | None) -> None:
        """Registra el resultado de probar un candidato.

        Args:
            video_path: Video.
            source: Provider del candidato.
            candidate_id: Id del candidato.
            verdict: Veredicto (ver `SyncVerdict`) o motivo de descarte.
            score: Puntaje de la verificacion, si la hubo.
        """
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO attempts VALUES (?, ?, ?, ?, ?, ?)",
                (str(video_path), source, candidate_id, verdict, score, _now()),
            )

    def cached_search(self, source: str, query: str, max_age: timedelta) -> list[SubtitleCandidate] | None:
        """Resultados cacheados de una busqueda, si no son mas viejos que `max_age`.

        Args:
            source: Provider.
            query: Texto buscado.
            max_age: Antiguedad maxima aceptada.

        Returns:
            Los candidatos, o None si no hay cache vigente.
        """
        with self._connect() as conn:
            row = conn.execute(
                "SELECT results, fetched_at FROM searches WHERE source = ? AND query = ?", (source, query)
            ).fetchone()
        if row is None or datetime.fromisoformat(row[1]) < datetime.now(UTC) - max_age:
            return None
        return [SubtitleCandidate(**item) for item in json.loads(row[0])]

    def put_search(self, source: str, query: str, results: Sequence[SubtitleCandidate]) -> None:
        """Cachea los resultados de una busqueda.

        Args:
            source: Provider.
            query: Texto buscado.
            results: Candidatos devueltos.
        """
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO searches VALUES (?, ?, ?, ?)",
                (source, query, json.dumps([asdict(result) for result in results]), _now()),
            )

    def log_action(
        self, video_path: Path, action: str, file: Path | None = None, backup: Path | None = None, detail: str = ""
    ) -> None:
        """Registra un cambio hecho en la biblioteca.

        Args:
            video_path: Video afectado.
            action: Que se hizo.
            file: Archivo escrito o movido.
            backup: Donde quedo la copia de respaldo.
            detail: Explicacion.
        """
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO actions (at, video_path, action, file, backup, detail) VALUES (?, ?, ?, ?, ?, ?)",
                (_now(), str(video_path), action, str(file) if file else None, str(backup) if backup else None, detail),
            )

    def recent_actions(self, limit: int = 50) -> list[ActionRecord]:
        """Ultimos cambios hechos en la biblioteca, del mas nuevo al mas viejo.

        Args:
            limit: Cantidad maxima.

        Returns:
            Los cambios.
        """
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT at, video_path, action, file, backup, detail FROM actions ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [ActionRecord(*row) for row in rows]

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=30)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            with conn:
                yield conn
        finally:
            conn.close()


_VIDEO_COLUMNS = (
    "path, size, mtime, status, detail, subtitle_path, reference, score, updated_at, retry_after, sidecars, detected"
)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _like_prefix(prefix: str) -> str:
    escaped = prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return escaped + "%"
