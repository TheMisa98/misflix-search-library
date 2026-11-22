"""Subtitulos en español para la biblioteca: detectar que falta, verificar lo que hay, corregir y bajar.

Ver `docs/ARCHITECTURE.md` § subtitulos para el flujo completo y
`core/subtitles/` para la logica pura (sincronia, emparejamiento). Este
modulo solo orquesta: decide que hacer con cada video, compone las
utilidades de `infra/` (ffprobe/ffmpeg, extraccion de .zip/.rar, SQLite,
recorrido de la biblioteca) y le pide candidatos al `SubtitleProvider`.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path

from misflix.core.models import DownloadedFile, SubtitleCandidate
from misflix.core.ports import SubtitleProvider, SubtitleProviderError
from misflix.core.subtitles.matching import (
    matches_episode,
    matches_movie,
    normalize,
    parse_episode_range,
    pick_subtitle_files,
    rank_candidates,
    release_profile,
)
from misflix.core.subtitles.media import (
    MediaInfo,
    has_spanish_audio,
    has_spanish_subtitle_track,
    pick_dialogue_audio,
    pick_reference_subtitle,
    release_says_spanish,
)
from misflix.core.subtitles.speech import LEVEL_STEP_SECONDS, detect_speech
from misflix.core.subtitles.srt import Cue, decode_subtitle, format_srt, parse_srt
from misflix.core.subtitles.sync import (
    SyncReport,
    SyncVerdict,
    apply_alignment,
    check_against_speech,
    check_against_subtitles,
)
from misflix.core.subtitles.text import detect_language, dialogue_cues, drop_credits
from misflix.infra.archives import ExtractionError, extract_subtitle_archive
from misflix.infra.cloudflare import CloudflareBlockedError
from misflix.infra.library import LibraryVideo, SidecarSubtitle, scan_library, sidecar_subtitles
from misflix.infra.media_probe import (
    MediaProbeError,
    available_audio_decoders,
    extract_reference,
    levels_from_bytes,
    levels_to_bytes,
    probe,
)
from misflix.infra.notify import notify
from misflix.infra.process_lock import exclusive_lock
from misflix.infra.spanish_detection import SpanishDetection, SpanishDetector
from misflix.infra.subtitle_store import ActionRecord, SubtitleStore, VideoRecord

# Re-exportados para `cli/` (que no importa `infra/`, ver CLAUDE.md).
__all__ = [
    "ActionRecord",
    "CloudflareBlockedError",
    "LibraryVideo",
    "RunSummary",
    "SubtitleService",
    "SubtitleStatus",
    "VideoRecord",
    "create_subtitle_service",
    "notify",
    "open_store",
]

_REFERENCE_CACHE_VERSION = 1
_MAX_CANDIDATES_PER_VIDEO = 6
_SEARCH_CACHE_AGE = timedelta(days=3)
_RETRY_NOT_FOUND = timedelta(days=7)
_RETRY_ERROR = timedelta(days=1)
# Si el repo no se pudo consultar (Cloudflare sin resolver, o `--no-download`),
# lo que faltaba bajar se reintenta pronto, no en una semana.
_RETRY_PENDING = timedelta(hours=1)
# Un subtitulo de referencia con menos de esto (lineas de dialogo por
# minuto) es una pista forzada o incompleta: no sirve para sincronizar.
_MIN_REFERENCE_LINES_PER_MINUTE = 2.0
_UNCERTAIN_SUFFIX = ".es.dudoso"


class SubtitleStatus(StrEnum):
    """Estado de un video respecto de sus subtitulos en español."""

    SPANISH_AUDIO = "audio_es"
    SPANISH_TRACK = "sub_es_incrustado"
    SPANISH_RELEASE = "release_es"
    SPANISH_BURNED = "subs_quemados"
    IN_SYNC = "ok"
    FIXED = "corregido"
    DOWNLOADED = "descargado"
    UNCERTAIN = "dudoso"
    NOT_FOUND = "no_encontrado"
    PENDING = "pendiente"
    NO_REFERENCE = "sin_referencia"
    ERROR = "error"


# Ya en español: no hace falta nada mas.
_ALREADY_SPANISH = {
    SubtitleStatus.SPANISH_AUDIO,
    SubtitleStatus.SPANISH_TRACK,
    SubtitleStatus.SPANISH_RELEASE,
    SubtitleStatus.SPANISH_BURNED,
}
# Estados terminales: no se reevaluan salvo que cambie el video o sus .srt.
_DONE = _ALREADY_SPANISH | {SubtitleStatus.IN_SYNC, SubtitleStatus.FIXED, SubtitleStatus.DOWNLOADED}


@dataclass
class RunSummary:
    """Resultado de una pasada sobre la biblioteca.

    Attributes:
        scanned: Videos encontrados.
        processed: Videos evaluados en esta pasada.
        by_status: Cantidad de videos evaluados por estado resultante.
        aborted: Motivo si el repo de subtitulos quedo bloqueado (la pasada
            siguio sin descargas).
        busy: True si no se hizo nada porque habia otra pasada en curso.
    """

    scanned: int = 0
    processed: int = 0
    by_status: Counter[str] = field(default_factory=Counter)
    aborted: str | None = None
    busy: bool = False


@dataclass(frozen=True)
class _Reference:
    """Contra que verificar un subtitulo de un video concreto."""

    subtitles: tuple[Cue, ...] | None
    speech: tuple[tuple[float, float], ...] | None
    duration: float

    @property
    def available(self) -> bool:
        return bool(self.subtitles or self.speech)

    def check(self, cues: Sequence[Cue]) -> SyncReport | None:
        """Verifica `cues`: contra el subtitulo de referencia y, si no es concluyente, contra la voz."""
        primary = check_against_subtitles(cues, self.subtitles) if self.subtitles else None
        if self.speech and (primary is None or primary.verdict == SyncVerdict.UNCERTAIN):
            secondary = check_against_speech(cues, self.speech, self.duration)
            if primary is None or secondary.verdict != SyncVerdict.UNCERTAIN:
                return secondary
        return primary


@dataclass(frozen=True)
class _Found:
    """Subtitulo bajado que verifico, ya escrito junto al video."""

    path: Path
    report: SyncReport
    source: str  # "subdivx #123", o "subdivx #123, subido como S06E02" si vino de un vecino


class SubtitleService:
    """Detecta, verifica, corrige y baja subtitulos en español para la biblioteca."""

    def __init__(
        self,
        provider: SubtitleProvider,
        store: SubtitleStore,
        cache_dir: Path,
        backup_dir: Path,
        log: Callable[[str], None] = lambda _: None,
        max_candidates: int = _MAX_CANDIDATES_PER_VIDEO,
        detector: SpanishDetector | None = None,
    ):
        """Inicializa el servicio.

        Args:
            provider: Repo de subtitulos donde buscar.
            store: Estado persistente.
            cache_dir: Cache de referencias extraidas y descargas.
            backup_dir: Donde mover los .srt reemplazados o descartados.
            log: Donde escribir el progreso (consola o journal).
            max_candidates: Candidatos nuevos a probar por video y pasada.
            detector: Detector de doblaje/subtitulos quemados (Whisper +
                Tesseract); sin el, solo se miran los metadatos.
        """
        self.provider = provider
        self.store = store
        self.cache_dir = cache_dir
        self.backup_dir = backup_dir
        self.log = log
        self.max_candidates = max_candidates
        self.detector = detector
        self._tools: frozenset[str] = detector.tools if detector else frozenset()
        self._run_stamp = datetime.now(UTC).strftime("%Y-%m-%d_%H%M%S")

    def run(
        self,
        movies_dir: Path,
        series_dir: Path,
        path_prefix: Path | None = None,
        force: bool = False,
        download: bool = True,
        on_progress: Callable[[int, int, LibraryVideo], None] | None = None,
    ) -> RunSummary:
        """Hace una pasada sobre la biblioteca (o la parte bajo `path_prefix`).

        Solo procesa los videos que lo necesitan (ver `needs_work`), del mas
        nuevo al mas viejo, asi lo recien bajado tiene subtitulos primero.
        Si subdivx queda bloqueado por Cloudflare (el usuario no resolvio la
        verificacion a tiempo), el resto de la pasada sigue sin descargas:
        lo que faltaba bajar queda `pendiente` y se reintenta en una hora.

        Args:
            movies_dir: Carpeta de peliculas.
            series_dir: Carpeta de series.
            path_prefix: Si se da, solo videos bajo esta ruta.
            force: Reevaluar aunque el estado guardado diga que esta listo.
            download: Si es False, solo verifica/corrige lo que ya hay.
            on_progress: Callback `(indice, total, video)` antes de cada video.

        Returns:
            El resumen de la pasada (`aborted` dice si el repo quedo bloqueado;
            `busy`, si no se hizo nada porque habia otra pasada en curso).
        """
        with exclusive_lock(self.store.path.with_name("subs.lock")) as acquired:
            if not acquired:
                return RunSummary(busy=True)
            return self._run(movies_dir, series_dir, path_prefix, force, download, on_progress)

    def _run(
        self,
        movies_dir: Path,
        series_dir: Path,
        path_prefix: Path | None,
        force: bool,
        download: bool,
        on_progress: Callable[[int, int, LibraryVideo], None] | None,
    ) -> RunSummary:
        self._run_stamp = datetime.now(UTC).strftime("%Y-%m-%d_%H%M%S")
        # se recalcula por pasada: el daemon corre dias y el usuario puede
        # instalar Whisper/Tesseract en el medio
        self._tools = self.detector.tools if self.detector else frozenset()
        missing = {"voz": "whisper-cpp", "ocr": "tesseract + tesseract-langpack-spa"}
        for tool, package in missing.items():
            if tool not in self._tools:
                self.log(f"aviso: sin {package} no se detectan doblajes/subtitulos quemados ({tool})")
        videos = scan_library(movies_dir, series_dir)
        if path_prefix is not None:
            videos = [v for v in videos if v.path == path_prefix or path_prefix in v.path.parents]
        summary = RunSummary(scanned=len(videos))
        pending = [video for video in videos if force or self.needs_work(video)]
        pending.sort(key=_mtime, reverse=True)
        for index, video in enumerate(pending, start=1):
            if on_progress:
                on_progress(index, len(pending), video)
            try:
                record = self.process(video, download=download)
            except CloudflareBlockedError as exc:
                # Sin repo no se puede bajar nada, pero verificar/corregir lo
                # que ya hay no necesita red: se sigue sin descargas.
                summary.aborted = str(exc)
                download = False
                record = self.process(video, download=False)
            except FileNotFoundError:
                continue  # se borro/movio durante la pasada
            except Exception as exc:  # un video roto no puede tirar abajo el daemon
                self.log(f"{'error':>18}  {video.path.name}  error inesperado: {exc!r}")
                summary.by_status[SubtitleStatus.ERROR] += 1
                continue
            summary.processed += 1
            summary.by_status[record.status] += 1
        return summary

    def needs_work(self, video: LibraryVideo) -> bool:
        """Indica si `video` tiene que (re)evaluarse.

        Args:
            video: Video de la biblioteca.

        Returns:
            True si nunca se evaluo, si el archivo o sus .srt cambiaron, o si
            su estado admite reintento y ya paso el plazo.
        """
        record = self.store.get_video(video.path)
        if record is None:
            return True
        try:
            stat = video.path.stat()
        except OSError:
            return False
        if (record.size, record.mtime) != (stat.st_size, stat.st_mtime):
            return True
        if record.sidecars != _sidecar_signature(video.path):
            return True
        if record.status not in _ALREADY_SPANISH and not self._tools <= _tools_of(record):
            return True  # se instalo una deteccion que no se corrio cuando se evaluo
        if record.status in _DONE:
            return False
        return record.retry_after is None or datetime.fromisoformat(record.retry_after) <= datetime.now(UTC)

    def process(self, video: LibraryVideo, download: bool = True) -> VideoRecord:
        """Evalua un video y deja sus subtitulos en español en el mejor estado posible.

        Args:
            video: Video de la biblioteca.
            download: Si es False, no busca en el repo (solo verifica/corrige).

        Returns:
            El estado resultante (tambien guardado en la base).

        Raises:
            CloudflareBlockedError: Si el repo de subtitulos quedo bloqueado.
        """
        stat = video.path.stat()
        base = VideoRecord(path=str(video.path), size=stat.st_size, mtime=stat.st_mtime, status="")
        try:
            record = self._process(video, base, download)
        except SubtitleProviderError as exc:
            record = _with(base, SubtitleStatus.PENDING, str(exc)[:300], retry=_RETRY_PENDING)
        except (MediaProbeError, ExtractionError, OSError, ValueError) as exc:
            record = _with(base, SubtitleStatus.ERROR, str(exc)[:300], retry=_RETRY_ERROR)
        record = replace(record, sidecars=_sidecar_signature(video.path), detected=",".join(sorted(self._tools)))
        self.store.put_video(record)
        self.log(f"{record.status:>18}  {video.path.name}  {record.detail}")
        return record

    def sync_file(self, video_path: Path, subtitle_path: Path) -> tuple[SyncReport | None, Path | None]:
        """Verifica y, si hace falta, sincroniza un .srt puntual contra su video.

        Pensado para el comando manual `misflix subs sync`: el resultado se
        escribe como `<video>.es.srt` (respaldando lo que hubiera ahi).

        Args:
            video_path: Video.
            subtitle_path: Subtitulo a verificar/sincronizar.

        Returns:
            El reporte de sincronia (None si no hay contra que verificar) y
            el archivo escrito (None si no se escribio nada).
        """
        stat = video_path.stat()
        info = self._probe(video_path, stat.st_size, stat.st_mtime)
        reference = self._reference(video_path, info, stat.st_size, stat.st_mtime)
        cues = _read_cues(subtitle_path)
        report = reference.check(cues) if reference.available else None
        if report is None or report.verdict not in (SyncVerdict.IN_SYNC, SyncVerdict.FIXABLE):
            return report, None
        fixed = apply_alignment(cues, report) if report.verdict == SyncVerdict.FIXABLE else drop_credits(cues)
        written = self._write(video_path, fixed, f"sincronizado a mano desde {subtitle_path.name}")
        return report, written

    # --- flujo de un video -------------------------------------------------

    def _process(self, video: LibraryVideo, base: VideoRecord, download: bool) -> VideoRecord:
        info = self._probe(video.path, base.size, base.mtime)
        if has_spanish_audio(info):
            return _with(base, SubtitleStatus.SPANISH_AUDIO, "tiene doblaje en español")
        if has_spanish_subtitle_track(info):
            return _with(base, SubtitleStatus.SPANISH_TRACK, "trae subtitulos en español incrustados")
        if release_says_spanish(video.path.name):
            return _with(base, SubtitleStatus.SPANISH_RELEASE, "el nombre del archivo indica español")
        detection = self._detect_spanish(video.path, base.size, base.mtime, info)
        if detection is not None and detection.spanish_audio:
            return _with(base, SubtitleStatus.SPANISH_AUDIO, "doblaje en español detectado al escuchar el audio")
        if detection is not None and detection.burned_language == "es":
            return _with(base, SubtitleStatus.SPANISH_BURNED, "subtitulos en español quemados en la imagen")

        reference = self._reference(video.path, info, base.size, base.mtime)
        if not reference.available:
            return _with(base, SubtitleStatus.NO_REFERENCE, "sin subtitulo ni audio para verificar", _RETRY_NOT_FOUND)

        existing = self._review_existing(video, reference)
        good = [item for item in existing if item[1].verdict in (SyncVerdict.IN_SYNC, SyncVerdict.FIXABLE)]
        if good:
            path, report = max(good, key=lambda item: item[1].score)
            status = SubtitleStatus.IN_SYNC if report.verdict == SyncVerdict.IN_SYNC else SubtitleStatus.FIXED
            detail = "el subtitulo existente esta bien" if status == SubtitleStatus.IN_SYNC else "se resincronizo"
            return _with(base, status, detail, subtitle=path, report=report)

        if not download:
            uncertain = [item for item in existing if item[1].verdict == SyncVerdict.UNCERTAIN]
            if uncertain:
                path, report = uncertain[0]
                detail = "verificacion no concluyente"
                return _with(base, SubtitleStatus.UNCERTAIN, detail, _RETRY_NOT_FOUND, path, report)
            return _with(base, SubtitleStatus.PENDING, "falta bajar un subtitulo", _RETRY_PENDING)

        found, offered = self._download_best(video, info, reference)
        if found is not None:
            for path, _ in existing:  # los dudosos que habia: ya hay uno verificado
                if path.exists() and path != found.path:
                    self._backup(video.path, path, "reemplazado por uno verificado")
            return _with(base, SubtitleStatus.DOWNLOADED, found.source, None, found.path, found.report)

        uncertain = [item for item in existing if item[1].verdict == SyncVerdict.UNCERTAIN]
        if uncertain:
            path, report = uncertain[0]
            return _with(base, SubtitleStatus.UNCERTAIN, "verificacion no concluyente", _RETRY_NOT_FOUND, path, report)
        if offered == 0:
            what = "este episodio" if video.kind == "episode" else "esta pelicula"
            detail = f"{self.provider.name} no tiene subtitulos de {what}"
        elif offered == 1:
            detail = "el unico candidato no verifico"
        else:
            detail = f"ninguno de los {offered} candidatos verifico"
        return _with(base, SubtitleStatus.NOT_FOUND, detail, _RETRY_NOT_FOUND)

    def _review_existing(self, video: LibraryVideo, reference: _Reference) -> list[tuple[Path, SyncReport]]:
        """Verifica cada .srt en español que ya esta junto al video y actua segun el veredicto.

        Bien sincronizado: se deja. Desfasado: se corrige en el mismo archivo
        (respaldando el original). De otro episodio/serie: se mueve al
        respaldo. Dudoso: se deja como esta.
        """
        reviewed: list[tuple[Path, SyncReport]] = []
        for sidecar in self._spanish_sidecars(video.path):
            cues = _read_cues(sidecar.path)
            report = reference.check(cues)
            if report is None:
                continue
            if report.verdict == SyncVerdict.FIXABLE:
                fixed = apply_alignment(cues, report)
                self._backup(video.path, sidecar.path, "original antes de resincronizar")
                _write_srt(sidecar.path, fixed)
                self.store.log_action(video.path, "resincronizado", sidecar.path, detail=_describe(report))
            elif report.verdict == SyncVerdict.WRONG:
                self._backup(video.path, sidecar.path, f"no corresponde al video ({_describe(report)})")
                continue
            reviewed.append((sidecar.path, report))
        return reviewed

    def _download_best(self, video: LibraryVideo, info: MediaInfo, reference: _Reference) -> tuple[_Found | None, int]:
        """Prueba candidatos del repo en orden hasta que uno verifique y lo escribe.

        Primero los del episodio exacto y, si ninguno verifica, los de los
        episodios vecinos: la numeracion de la biblioteca (la de TVDB) y la de
        los subtitulos (la de la emision) se corren en uno cuando un episodio
        doble cuenta como uno en una y como dos en la otra. Verificado en vivo:
        en House T6 "Broken" es E01+E02 en la biblioteca y un solo E01 en
        subdivx, asi que el "S06E03" de subdivx es "The Tyrant" y el E03 local
        es "Epic Fail". El verificador descarta los vecinos que no son.

        Returns:
            Lo escrito (None si nada verifico) y cuantos candidatos ofrecio el
            repo para el episodio exacto (o la pelicula).
        """
        best_uncertain: tuple[float, list[Cue], SyncReport, SubtitleCandidate] | None = None
        offered = 0
        for episode in _episodes_to_try(video):
            candidates = self._candidates(video, info, episode)
            numbered_as = None
            if episode != video.episode and video.season is not None and episode is not None:
                numbered_as = _episode_code(video.season, episode)
            else:
                offered = len(candidates)
            tried = 0
            for candidate in candidates:
                if tried >= self.max_candidates:
                    break
                # un pack se prueba una vez por numeracion: de el sale otro .srt
                key = f"{candidate.id}@{numbered_as}" if numbered_as else candidate.id
                if self.store.was_tried(video.path, candidate.source, key):
                    continue
                tried += 1
                try:
                    outcome = self._try_candidate(video, candidate, reference, episode)
                except SubtitleProviderError as exc:
                    # no se registra como probado: puede ser pasajero (verificado en vivo:
                    # 404 durante una actualizacion del sitio)
                    self.log(f"  no se pudo bajar {candidate.source} #{candidate.id}: {exc}")
                    continue
                if outcome is None:
                    # no se registra como probado: el archivo queda en cache y una
                    # version mejor de `pick_subtitle_files` puede encontrarle el .srt
                    self.log(f"  {candidate.source} #{candidate.id}: no se encontro el .srt del video adentro")
                    continue
                cues, report = outcome
                self.store.add_attempt(video.path, candidate.source, key, report.verdict, report.score)
                if report.verdict in (SyncVerdict.IN_SYNC, SyncVerdict.FIXABLE):
                    fixed = (
                        apply_alignment(cues, report) if report.verdict == SyncVerdict.FIXABLE else drop_credits(cues)
                    )
                    source = f"{candidate.source} #{candidate.id}"
                    if numbered_as:
                        source += f", subido como {numbered_as}"
                    written = self._write(video.path, fixed, f"{source} ({_describe(report)})")
                    return _Found(written, report, source), offered
                if report.verdict == SyncVerdict.UNCERTAIN and (
                    best_uncertain is None or report.score > best_uncertain[0]
                ):
                    best_uncertain = (report.score, cues, report, candidate)

        if best_uncertain is not None and not self._spanish_sidecars(video.path):
            # Mejor algo para probar que nada: queda marcado en el nombre y en el estado.
            _, cues, report, candidate = best_uncertain
            fixed = apply_alignment(cues, report)
            self._write(video.path, fixed, f"dudoso: {candidate.source} #{candidate.id}", suffix=_UNCERTAIN_SUFFIX)
        return None, offered

    def _try_candidate(
        self, video: LibraryVideo, candidate: SubtitleCandidate, reference: _Reference, episode: int | None
    ) -> tuple[list[Cue], SyncReport] | None:
        """Baja un candidato, elige el/los .srt en español para `episode` y devuelve el mejor verificado."""
        downloaded = self._download(candidate)
        with tempfile.TemporaryDirectory(prefix="misflix-subs-") as tmp:
            try:
                files = extract_subtitle_archive(downloaded.filename, downloaded.content, Path(tmp) / "x")
            except ExtractionError as exc:
                self.log(f"  no se pudo extraer {candidate.source} #{candidate.id}: {exc}")
                return None
            root = Path(tmp) / "x"
            names = [str(path.relative_to(root)) for path in files]
            chosen = pick_subtitle_files(names, video.season, episode, parse_episode_range(candidate.title))
            best: tuple[list[Cue], SyncReport] | None = None
            for name in chosen:
                cues = _read_cues(root / name)
                if detect_language(cues) not in ("es", None):
                    continue
                report = reference.check(cues)
                if report is not None and (best is None or _rank(report) > _rank(best[1])):
                    best = (cues, report)
            return best

    def _candidates(self, video: LibraryVideo, info: MediaInfo, episode: int | None) -> list[SubtitleCandidate]:
        found: dict[tuple[str, str], SubtitleCandidate] = {}
        for query in _queries(video, episode):
            for candidate in self._search(query):
                if video.kind == "episode" and video.season is not None and episode is not None:
                    ok = matches_episode(candidate, video.titles, video.season, episode) is not None
                else:
                    ok = matches_movie(candidate, video.titles, video.year, video.imdb_id)
                if ok:
                    found.setdefault((candidate.source, candidate.id), candidate)
        profile = release_profile(video.path.name, info.format_title or "")
        return rank_candidates(list(found.values()), profile, info.duration or None)

    def _search(self, query: str) -> list[SubtitleCandidate]:
        cached = self.store.cached_search(self.provider.name, query, _SEARCH_CACHE_AGE)
        if cached is not None:
            return cached
        results = self.provider.search(query)
        self.store.put_search(self.provider.name, query, results)
        return results

    def _download(self, candidate: SubtitleCandidate) -> DownloadedFile:
        """Baja un candidato, cacheando el archivo: un pack de temporada se usa para todos sus episodios."""
        folder = self.cache_dir / "descargas"
        data_path = folder / f"{candidate.source}-{candidate.id}.bin"
        name_path = folder / f"{candidate.source}-{candidate.id}.name"
        if data_path.exists() and name_path.exists():
            return DownloadedFile(filename=name_path.read_text(), content=data_path.read_bytes())
        downloaded = self.provider.download(candidate)
        folder.mkdir(parents=True, exist_ok=True)
        data_path.write_bytes(downloaded.content)
        name_path.write_text(downloaded.filename)
        return downloaded

    # --- referencias y ffprobe (cacheados) ----------------------------------

    def _detect_spanish(self, path: Path, size: int, mtime: float, info: MediaInfo) -> SpanishDetection | None:
        """Escucha el audio y lee la imagen (cacheado; solo si hay herramientas para hacerlo)."""
        if self.detector is None or not self._tools:
            return None
        cached = self.store.get_detection(path, size, mtime)
        if cached is not None and self._tools <= cached.tools:
            return cached
        self.log(f"  escuchando el audio y leyendo subtitulos quemados de {path.name}...")
        detection = self.detector.detect(path, info)
        self.store.put_detection(path, size, mtime, detection)
        return detection

    def _probe(self, path: Path, size: int, mtime: float) -> MediaInfo:
        cached = self.store.get_probe(path, size, mtime)
        if cached is not None:
            return cached
        info = probe(path)
        self.store.put_probe(path, size, mtime, info)
        return info

    def _reference(self, path: Path, info: MediaInfo, size: int, mtime: float) -> _Reference:
        """Subtitulo de referencia y voz del video, extraidos una sola vez y cacheados."""
        folder = self.cache_dir / "referencias" / hashlib.sha1(str(path).encode()).hexdigest()[:20]
        meta_path = folder / "meta.json"
        expected = {"version": _REFERENCE_CACHE_VERSION, "size": size, "mtime": mtime}
        try:
            cached = json.loads(meta_path.read_text()) == expected
        except OSError, ValueError:
            cached = False

        if not cached:
            subtitle_stream = pick_reference_subtitle(info)
            audio_stream = pick_dialogue_audio(info, available_audio_decoders() or None)
            folder.mkdir(parents=True, exist_ok=True)
            for stale in folder.iterdir():
                stale.unlink()
            if subtitle_stream is not None or audio_stream is not None:
                self.log(f"  leyendo referencia de {path.name} (puede tardar ~1 min)...")
                try:
                    extracted = extract_reference(path, subtitle_stream, audio_stream, folder)
                except MediaProbeError:
                    if subtitle_stream is None or audio_stream is None:
                        raise
                    # el audio no se pudo decodificar: alcanza con el subtitulo
                    self.log("  el audio no se pudo leer; se usa solo el subtitulo de referencia")
                    extracted = extract_reference(path, subtitle_stream, None, folder)
                (folder / "niveles.txt").unlink(missing_ok=True)
                if extracted.subtitle_text:
                    (folder / "referencia.srt").write_text(extracted.subtitle_text, encoding="utf-8")
                else:
                    (folder / "referencia.srt").unlink(missing_ok=True)
                (folder / "niveles.bin").write_bytes(levels_to_bytes(extracted.levels_db))
            meta_path.write_text(json.dumps(expected))

        subtitles: tuple[Cue, ...] | None = None
        reference_path = folder / "referencia.srt"
        duration = info.duration
        if reference_path.exists():
            dialogue = dialogue_cues(parse_srt(reference_path.read_text(encoding="utf-8", errors="replace")))
            minutes = max(duration, dialogue[-1].end if dialogue else 0.0) / 60
            if dialogue and len(dialogue) >= _MIN_REFERENCE_LINES_PER_MINUTE * minutes:
                subtitles = tuple(dialogue)
        levels_path = folder / "niveles.bin"
        levels = levels_from_bytes(levels_path.read_bytes()) if levels_path.exists() else []
        speech = tuple(detect_speech(levels)) if levels else None
        return _Reference(
            subtitles=subtitles,
            speech=speech or None,
            duration=duration or len(levels) * LEVEL_STEP_SECONDS,
        )

    # --- archivos -----------------------------------------------------------

    def _spanish_sidecars(self, video_path: Path) -> list[SidecarSubtitle]:
        spanish = []
        for sidecar in sidecar_subtitles(video_path):
            language = sidecar.language
            if language is None:
                try:
                    language = detect_language(_read_cues(sidecar.path))
                except OSError:
                    continue
            if language == "es":
                spanish.append(sidecar)
        return spanish

    def _write(self, video_path: Path, cues: Sequence[Cue], detail: str, suffix: str = ".es") -> Path:
        target = video_path.with_name(f"{video_path.stem}{suffix}.srt")
        if target.exists():
            self._backup(video_path, target, "reemplazado")
        _write_srt(target, cues)
        self.store.log_action(video_path, "escrito", target, detail=detail)
        return target

    def _backup(self, video_path: Path, file: Path, reason: str) -> Path:
        destination = self.backup_dir / self._run_stamp / file.relative_to(file.anchor)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(file, destination)
        self.store.log_action(video_path, "respaldado", file, destination, reason)
        return destination


def create_subtitle_service(
    provider: SubtitleProvider, state_dir: Path, cache_dir: Path, log: Callable[[str], None] = lambda _: None
) -> SubtitleService:
    """Arma un `SubtitleService` con la base y carpetas por defecto bajo `state_dir`/`cache_dir`.

    Args:
        provider: Repo de subtitulos.
        state_dir: Carpeta de estado (`~/.local/state/misflix`).
        cache_dir: Carpeta de cache (`~/.cache/misflix`).
        log: Donde escribir el progreso.

    Returns:
        El servicio listo para usar.
    """
    store = SubtitleStore(state_dir / "subs.db")
    detector = SpanishDetector(cache_dir / "subs", log)
    return SubtitleService(provider, store, cache_dir / "subs", state_dir / "subs-respaldo", log, detector=detector)


def open_store(state_dir: Path) -> SubtitleStore:
    """Abre la base de estado sin armar el servicio (para `misflix subs status`).

    Args:
        state_dir: Carpeta de estado.

    Returns:
        La base.
    """
    return SubtitleStore(state_dir / "subs.db")


def _episodes_to_try(video: LibraryVideo) -> list[int | None]:
    """El episodio del video y despues sus vecinos (ver `_download_best`); `[None]` para una pelicula."""
    if video.kind != "episode" or video.season is None or video.episode is None:
        return [None]
    return [video.episode, *(n for n in (video.episode - 1, video.episode + 1) if n >= 1)]


def _episode_code(season: int, episode: int) -> str:
    return f"S{season:02d}E{episode:02d}"


def _queries(video: LibraryVideo, episode: int | None) -> list[str]:
    titles = [title for title in video.titles if normalize(title)][:2]
    if video.kind == "episode" and video.season is not None and episode is not None:
        code = _episode_code(video.season, episode)
        # el primer episodio de la temporada trae tambien los packs ("S01E01-11")
        pack = _episode_code(video.season, 1)
        return list(dict.fromkeys([f"{t} {code}" for t in titles] + [f"{t} {pack}" for t in titles]))
    return list(dict.fromkeys(f"{t} {video.year}" if video.year else t for t in titles))


def _tools_of(record: VideoRecord) -> frozenset[str]:
    return frozenset(filter(None, record.detected.split(",")))


def _read_cues(path: Path) -> list[Cue]:
    return parse_srt(decode_subtitle(path.read_bytes()))


def _write_srt(path: Path, cues: Sequence[Cue]) -> None:
    path.write_text(format_srt(cues), encoding="utf-8")


def _sidecar_signature(video_path: Path) -> str:
    parts = []
    for sidecar in sidecar_subtitles(video_path):
        try:
            parts.append(f"{sidecar.path.name}:{sidecar.path.stat().st_size}")
        except OSError:
            continue
    return "|".join(parts)


def _mtime(video: LibraryVideo) -> float:
    try:
        return video.path.stat().st_mtime
    except OSError:
        return 0.0


def _rank(report: SyncReport) -> tuple[int, float]:
    order = {SyncVerdict.IN_SYNC: 3, SyncVerdict.FIXABLE: 2, SyncVerdict.UNCERTAIN: 1, SyncVerdict.WRONG: 0}
    return order[report.verdict], report.score


def _describe(report: SyncReport) -> str:
    return (
        f"{report.verdict} contra {report.reference}, puntaje {report.score:+.2f}, "
        f"{report.alignment.cuts} cortes, ratio {report.alignment.ratio:.4f}"
    )


def _with(
    base: VideoRecord,
    status: SubtitleStatus,
    detail: str,
    retry: timedelta | None = None,
    subtitle: Path | None = None,
    report: SyncReport | None = None,
) -> VideoRecord:
    retry_after = (datetime.now(UTC) + retry).isoformat(timespec="seconds") if retry else None
    return replace(
        base,
        status=status.value,
        detail=detail,
        subtitle_path=str(subtitle) if subtitle else None,
        reference=report.reference.value if report else None,
        score=round(report.score, 3) if report else None,
        retry_after=retry_after,
    )
