"""Reglas sobre las pistas de un video: que cuenta como español y que sirve de referencia.

Los datos vienen de `ffprobe` (ver `infra/media_probe.py`); aca solo se decide.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

TEXT_SUBTITLE_CODECS = frozenset({"subrip", "ass", "ssa", "mov_text", "webvtt", "text"})
_SPANISH_CODES = frozenset({"spa", "es", "esl", "esp", "es-419", "es-es", "es-mx", "es-la", "spanish"})
# El codigo de idioma no alcanza: en la biblioteca hay pistas `und` tituladas
# "Latino"/"Inglés" (verificado en vivo, The Big Bang Theory).
_SPANISH_TITLE_RE = re.compile(r"\b(latino|latam|castellano|espa[ñn]ol|spanish|lat|esp)\b", re.IGNORECASE)
_NOT_SPANISH_TITLE_RE = re.compile(r"\b(ingl[eé]s|english|eng|original|japon[eé]s|japanese)\b", re.IGNORECASE)
# Un release doblado o con subtitulos quemados lo dice en el nombre
# ("...CASTELLANO CAPÍTULO ESPECIAL...", "...SUBTITULADO ESPAÑOL...").
_SPANISH_RELEASE_RE = re.compile(
    r"\b(castellano|latino|dual|subtitulad[oa]|sub\s*esp|espa[ñn]ol|spanish|multi[\s.-]?lang)\b", re.IGNORECASE
)
_HEARING_IMPAIRED_TITLE_RE = re.compile(r"\b(sdh|hi|cc|hearing)\b", re.IGNORECASE)


@dataclass(frozen=True)
class StreamInfo:
    """Una pista de un archivo de video, segun ffprobe.

    Attributes:
        index: Indice global de la pista en el archivo (el de `-map 0:<index>`).
        kind: "video", "audio" o "subtitle".
        codec: Codec ("subrip", "hdmv_pgs_subtitle", "dts", ...).
        language: Codigo de idioma del tag, en minusculas, o None/"und".
        title: Titulo de la pista (ej. "Latino", "SDH"), si tiene.
        forced: Si la pista es "forzada" (solo partes en otro idioma).
        hearing_impaired: Si es para hipoacusicos (por disposicion o titulo).
        channels: Cantidad de canales de audio.
        channel_layout: Layout de audio ("5.1(side)", "stereo", ...).
        default: Si es la pista por defecto.
    """

    index: int
    kind: str
    codec: str
    language: str | None = None
    title: str | None = None
    forced: bool = False
    hearing_impaired: bool = False
    channels: int | None = None
    channel_layout: str | None = None
    default: bool = False


@dataclass(frozen=True)
class MediaInfo:
    """Lo que importa de un video para decidir sobre subtitulos.

    Attributes:
        duration: Duracion en segundos (0 si ffprobe no la informa).
        streams: Todas las pistas.
        format_title: Tag de titulo del contenedor (a veces el nombre del release).
    """

    duration: float
    streams: tuple[StreamInfo, ...]
    format_title: str | None = None

    def of_kind(self, kind: str) -> list[StreamInfo]:
        """Pistas de un tipo ("audio", "subtitle", ...).

        Args:
            kind: Tipo de pista.

        Returns:
            Las pistas de ese tipo, en orden.
        """
        return [stream for stream in self.streams if stream.kind == kind]


def is_spanish(stream: StreamInfo) -> bool:
    """Indica si una pista esta en español, por codigo de idioma o por su titulo.

    Args:
        stream: Pista a revisar.

    Returns:
        True si es español (cualquier variante).
    """
    if (stream.language or "").lower() in _SPANISH_CODES:
        return True
    # sin etiqueta (o mal etiquetada), decide el titulo: "Latino" si,
    # "Inglés" no — pero una pista `spa` titulada "Original" (una pelicula
    # hablada en español) ya devolvio True arriba.
    title = stream.title or ""
    return not _NOT_SPANISH_TITLE_RE.search(title) and bool(_SPANISH_TITLE_RE.search(title))


def has_spanish_audio(info: MediaInfo) -> bool:
    """Indica si el video trae alguna pista de audio en español (doblaje).

    Args:
        info: Datos del video.

    Returns:
        True si hay audio en español.
    """
    return any(is_spanish(stream) for stream in info.of_kind("audio"))


def has_spanish_subtitle_track(info: MediaInfo) -> bool:
    """Indica si el video trae subtitulos completos en español incrustados.

    Las pistas forzadas (solo carteles/partes en otro idioma) no cuentan.

    Args:
        info: Datos del video.

    Returns:
        True si hay una pista de subtitulos en español no forzada.
    """
    return any(is_spanish(stream) and not stream.forced for stream in info.of_kind("subtitle"))


def release_says_spanish(name: str) -> bool:
    """Indica si el nombre del archivo declara doblaje o subtitulos quemados en español.

    Args:
        name: Nombre del archivo de video.

    Returns:
        True para nombres como "...CASTELLANO..." o "...SUBTITULADO ESPAÑOL...".
    """
    return bool(_SPANISH_RELEASE_RE.search(name.replace("_", " ").replace(".", " ")))


def pick_reference_subtitle(info: MediaInfo) -> StreamInfo | None:
    """Elige la pista de subtitulos incrustada que sirve de referencia de sincronia.

    Tiene que ser de texto (los PGS/VobSub son imagenes) y no española ni
    forzada. Se prefiere la que no es para hipoacusicos (menos lineas de
    efectos) y, entre esas, la de ingles o la primera.

    Args:
        info: Datos del video.

    Returns:
        La pista elegida, o None si no hay ninguna utilizable.
    """
    usable = [
        stream
        for stream in info.of_kind("subtitle")
        if stream.codec in TEXT_SUBTITLE_CODECS and not stream.forced and not is_spanish(stream)
    ]
    if not usable:
        return None
    return min(
        usable,
        key=lambda s: (_is_hearing_impaired(s), (s.language or "") not in {"eng", "en"}, s.index),
    )


def pick_dialogue_audio(info: MediaInfo) -> StreamInfo | None:
    """Elige la pista de audio donde escuchar el dialogo original.

    Se prefiere una que no sea española (el subtitulo español se sincroniza
    contra el audio original), con canal central (5.1/7.1, ver
    `has_center_channel`) y la marcada por defecto.

    Args:
        info: Datos del video.

    Returns:
        La pista elegida, o None si el video no tiene audio.
    """
    audio = info.of_kind("audio")
    if not audio:
        return None
    return min(audio, key=lambda s: (is_spanish(s), not has_center_channel(s), not s.default, s.index))


def has_center_channel(stream: StreamInfo) -> bool:
    """Indica si la pista tiene canal central (donde va el dialogo en una mezcla 5.1/7.1).

    Args:
        stream: Pista de audio.

    Returns:
        True para 5.1, 6.1, 7.1 o cualquier pista de 6 canales o mas.
    """
    layout = stream.channel_layout or ""
    return any(tag in layout for tag in ("5.1", "6.1", "7.1", "3.0", "4.0")) or (stream.channels or 0) >= 6


def _is_hearing_impaired(stream: StreamInfo) -> bool:
    return stream.hearing_impaired or bool(_HEARING_IMPAIRED_TITLE_RE.search(stream.title or ""))
