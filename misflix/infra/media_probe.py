"""ffprobe/ffmpeg: leer las pistas de un video y extraer las referencias de sincronia.

Nada de esto decodifica video: ffprobe solo lee la cabecera, y la extraccion
usa `-vn` (el subtitulo y el audio se demuxean sin tocar la imagen), asi que
no hay trabajo para la GPU. Lo que si cuesta es *leer* el archivo entero
del disco (verificado en vivo: ~45-85s por episodio de 45 min en el disco
NTFS de la biblioteca, casi todo I/O) — por eso la extraccion corre con
prioridad minima de CPU y disco, y el resultado se cachea (ver
`core/services/subtitle_service.py`).
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from misflix.core.subtitles.media import MediaInfo, StreamInfo, has_center_channel
from misflix.core.subtitles.speech import LEVEL_STEP_SECONDS, SILENCE_DB

_LEVEL_KEY = "lavfi.astats.Overall.RMS_level"
_SAMPLE_RATE = 8000
_SAMPLES_PER_LEVEL = round(_SAMPLE_RATE * LEVEL_STEP_SECONDS)


class MediaProbeError(RuntimeError):
    """ffprobe/ffmpeg fallo (archivo dañado, formato no soportado, etc.)."""


@dataclass(frozen=True)
class ExtractedReference:
    """Lo que se saco de un video para sincronizar contra el.

    Attributes:
        subtitle_text: El subtitulo de referencia convertido a .srt, o None.
        levels_db: Nivel RMS (dBFS) del canal de dialogo cada
            `LEVEL_STEP_SECONDS`; vacio si no se extrajo audio.
    """

    subtitle_text: str | None
    levels_db: list[float]


def probe(path: Path) -> MediaInfo:
    """Lee las pistas de `path` con ffprobe (solo la cabecera, es rapido).

    Args:
        path: Archivo de video.

    Returns:
        Los datos del video.

    Raises:
        MediaProbeError: Si ffprobe falla.
    """
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
    )
    if result.returncode != 0:
        raise MediaProbeError(result.stderr.strip() or f"ffprobe fallo con {path}")
    return parse_ffprobe(json.loads(result.stdout or "{}"))


def parse_ffprobe(data: Mapping[str, object]) -> MediaInfo:
    """Convierte la salida JSON de ffprobe en `MediaInfo`.

    Args:
        data: JSON de `ffprobe -show_streams -show_format -of json`.

    Returns:
        Los datos del video.
    """
    streams = []
    raw_streams = data.get("streams")
    for raw in raw_streams if isinstance(raw_streams, list) else []:
        stream = _as_dict(raw)
        tags = _as_dict(stream.get("tags"))
        disposition = _as_dict(stream.get("disposition"))
        language = (_text(tags.get("language")) or "").lower()
        streams.append(
            StreamInfo(
                index=_int(stream.get("index")) or 0,
                kind=_text(stream.get("codec_type")) or "",
                codec=_text(stream.get("codec_name")) or "",
                language=None if language in ("", "und") else language,
                title=_text(tags.get("title")),
                forced=bool(disposition.get("forced")),
                hearing_impaired=bool(disposition.get("hearing_impaired")),
                channels=_int(stream.get("channels")),
                channel_layout=_text(stream.get("channel_layout")),
                default=bool(disposition.get("default")),
            )
        )
    fmt = _as_dict(data.get("format"))
    try:
        duration = float(_text(fmt.get("duration")) or 0.0)
    except ValueError:
        duration = 0.0
    return MediaInfo(
        duration=duration, streams=tuple(streams), format_title=_text(_as_dict(fmt.get("tags")).get("title"))
    )


def _as_dict(value: object) -> dict[str, object]:
    # claves en minusculas: hay archivos con tags `TITLE`/`LANGUAGE` en vez de
    # `title`/`language` (verificado en vivo, The Big Bang Theory 9x17)
    return {str(key).lower(): item for key, item in value.items()} if isinstance(value, dict) else {}


def _text(value: object) -> str | None:
    return str(value) if value is not None and str(value) != "" else None


def _int(value: object) -> int | None:
    try:
        return int(str(value)) if value is not None else None
    except ValueError:
        return None


@lru_cache(maxsize=1)
def available_audio_decoders() -> frozenset[str]:
    """Codecs de audio que el ffmpeg instalado puede decodificar.

    Verificado en vivo: Fedora trae `ffmpeg-free`, sin decodificador de
    E-AC3 (Dolby Digital Plus, muy comun en los WEB-DL) — pedir esa pista
    hace fallar toda la extraccion ("no decoder found for: eac3"). Con esto
    se elige otra pista decodificable, o se trabaja solo con el subtitulo de
    referencia.

    Returns:
        Nombres de codec tal como los reporta ffprobe (`codec_name`:
        "ac3", "dts", "aac", ...). Vacio si no se pudo consultar ffmpeg.
    """
    try:
        result = subprocess.run(
            ["ffmpeg", "-hide_banner", "-decoders"], capture_output=True, text=True, stdin=subprocess.DEVNULL
        )
    except OSError:
        return frozenset()
    return parse_audio_decoders(result.stdout)


def parse_audio_decoders(text: str) -> frozenset[str]:
    """Lee la salida de `ffmpeg -decoders` y devuelve los codecs de audio decodificables.

    Una linea como ` A....D dca  DCA (DTS Coherent Acoustics) (codec dts)`
    aporta tanto el nombre del decodificador (`dca`) como el del codec que
    reporta ffprobe (`dts`).

    Args:
        text: Salida de `ffmpeg -hide_banner -decoders`.

    Returns:
        Los nombres de codec de audio.
    """
    codecs: set[str] = set()
    for line in text.splitlines():
        match = re.match(r"^\s*A[.A-Z]{5}\s+(\S+)\s+(.*)$", line)
        if match is None:
            continue
        codecs.add(match.group(1))
        alias = re.search(r"\(codec (\S+)\)\s*$", match.group(2))
        if alias:
            codecs.add(alias.group(1))
    return frozenset(codecs)


def extract_reference(
    video: Path, subtitle: StreamInfo | None, audio: StreamInfo | None, workdir: Path
) -> ExtractedReference:
    """Extrae en una sola pasada el subtitulo de referencia y los niveles del canal de dialogo.

    Args:
        video: Archivo de video.
        subtitle: Pista de subtitulo de texto a extraer, o None.
        audio: Pista de audio a analizar, o None.
        workdir: Carpeta (existente) para los archivos intermedios.

    Returns:
        Lo extraido.

    Raises:
        MediaProbeError: Si ffmpeg falla.
    """
    subtitle_out = workdir / "referencia.srt"
    levels_out = workdir / "niveles.txt"
    command = reference_command(video, subtitle, audio, subtitle_out, levels_out)
    result = subprocess.run(command, capture_output=True, text=True, stdin=subprocess.DEVNULL)
    if result.returncode != 0:
        raise MediaProbeError(result.stderr.strip()[-500:] or f"ffmpeg fallo con {video}")
    subtitle_text = (
        subtitle_out.read_text(encoding="utf-8", errors="replace") if subtitle and subtitle_out.exists() else None
    )
    levels = parse_levels(levels_out.read_text()) if audio and levels_out.exists() else []
    return ExtractedReference(subtitle_text=subtitle_text, levels_db=levels)


def reference_command(
    video: Path, subtitle: StreamInfo | None, audio: StreamInfo | None, subtitle_out: Path, levels_out: Path
) -> list[str]:
    """Arma el comando de ffmpeg de `extract_reference` (separado para poder probarlo).

    El audio se reduce al canal de dialogo (`dialogue_filter`), se filtra a
    la banda de la voz (150-4000Hz) y se mide su RMS cada 50ms con
    `astats`, volcado a `levels_out` con `ametadata`. Todo corre con `nice`
    e `ionice` en clase idle, para no competir con nada que el usuario este
    haciendo (ej. Jellyfin reproduciendo desde el mismo disco).

    Args:
        video: Archivo de video.
        subtitle: Pista de subtitulo a extraer, o None.
        audio: Pista de audio a analizar, o None.
        subtitle_out: Donde escribir el .srt de referencia.
        levels_out: Donde escribir los niveles.

    Returns:
        El comando listo para `subprocess.run`.
    """
    command = _low_priority_prefix() + ["ffmpeg", "-v", "error", "-nostdin", "-y", "-i", str(video)]
    if subtitle is not None:
        command += ["-map", f"0:{subtitle.index}", "-c:s", "srt", "-f", "srt", str(subtitle_out)]
    if audio is not None:
        audio_filter = ",".join(
            [
                *dialogue_filter(audio),
                "highpass=f=150",
                "lowpass=f=4000",
                f"aresample={_SAMPLE_RATE}",
                f"asetnsamples=n={_SAMPLES_PER_LEVEL}:p=0",
                "astats=metadata=1:reset=1",
                f"ametadata=mode=print:key={_LEVEL_KEY}:file={_escape_filter_path(levels_out)}",
            ]
        )
        command += ["-map", f"0:{audio.index}", "-vn", "-sn", "-dn", "-af", audio_filter, "-f", "null", "-"]
    return command


def dialogue_filter(audio: StreamInfo) -> list[str]:
    """Filtro de ffmpeg que deja solo el canal donde va el dialogo.

    En 5.1/7.1 el dialogo va casi solo en el canal central (FC); quedarse
    con el es lo que separa voz de musica y efectos (ver `speech.py`). En
    estereo no hay canal central: se usa la mezcla de ambos (el dialogo
    suele ir centrado, pero con la musica encima — por eso la sincronia
    contra el audio es menos confiable ahi).

    Args:
        audio: Pista de audio.

    Returns:
        Los pasos del filtro (vacio para audio mono).
    """
    if has_center_channel(audio):
        layout = audio.channel_layout or ""
        # el canal central es FC cuando ffprobe conoce el layout; si no, en
        # el orden estandar de 5.1/7.1 es el tercero (c2)
        return ["pan=mono|c0=FC"] if any(tag in layout for tag in ("5.1", "6.1", "7.1")) else ["pan=mono|c0=c2"]
    if (audio.channels or 2) >= 2:
        return ["pan=mono|c0=0.5*c0+0.5*c1"]
    return []


def parse_levels(text: str) -> list[float]:
    """Lee los niveles que vuelca `ametadata=mode=print` (uno por ventana).

    Args:
        text: Contenido del archivo de niveles.

    Returns:
        Un nivel en dBFS por ventana; `SILENCE_DB` para silencio digital.
    """
    levels = []
    for line in text.splitlines():
        if not line.startswith(_LEVEL_KEY):
            continue
        value = line.split("=", 1)[1].strip()
        try:
            levels.append(max(SILENCE_DB, float(value)))
        except ValueError:
            levels.append(SILENCE_DB)
    return levels


def _low_priority_prefix() -> list[str]:
    prefix: list[str] = []
    if shutil.which("ionice"):
        prefix += ["ionice", "-c", "3"]
    if shutil.which("nice"):
        prefix += ["nice", "-n", "19"]
    return prefix


def _escape_filter_path(path: Path) -> str:
    # dentro de un filtergraph, ':' y '\' y ',' tienen significado; la ruta
    # de cache no deberia tenerlos, pero se escapan por las dudas.
    return str(path).replace("\\", "\\\\").replace(":", "\\:").replace(",", "\\,").replace("'", "\\'")


def levels_to_bytes(levels: Sequence[float]) -> bytes:
    """Comprime niveles a un byte por ventana (dB enteros, -120..0) para cachearlos.

    Args:
        levels: Niveles en dBFS.

    Returns:
        Los bytes a guardar.
    """
    return bytes(min(120, max(0, round(-level))) for level in levels)


def levels_from_bytes(data: bytes) -> list[float]:
    """Inversa de `levels_to_bytes`.

    Args:
        data: Bytes guardados.

    Returns:
        Niveles en dBFS.
    """
    return [-float(value) for value in data]
