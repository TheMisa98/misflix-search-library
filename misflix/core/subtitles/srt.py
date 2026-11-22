from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

_TIMING_RE = re.compile(
    r"(?P<h1>\d{1,2}):(?P<m1>\d{1,2}):(?P<s1>\d{1,2})[,.](?P<ms1>\d{1,3})\s*-->\s*"
    r"(?P<h2>\d{1,2}):(?P<m2>\d{1,2}):(?P<s2>\d{1,2})[,.](?P<ms2>\d{1,3})"
)
_BLOCK_SEPARATOR_RE = re.compile(r"\n[ \t]*\n")


@dataclass(frozen=True)
class Cue:
    """Una linea de subtitulo con su intervalo de tiempo.

    Attributes:
        start: Inicio, en segundos.
        end: Fin, en segundos.
        text: Texto tal cual (puede tener varias lineas y tags `<i>`).
    """

    start: float
    end: float
    text: str

    @property
    def duration(self) -> float:
        """Duracion en segundos (nunca negativa)."""
        return max(0.0, self.end - self.start)


def decode_subtitle(data: bytes) -> str:
    """Decodifica los bytes de un subtitulo adivinando la codificacion.

    Los subtitulos de subdivx vienen en UTF-8, UTF-16 (con BOM) o, muy seguido,
    en Windows-1252 (los mas viejos). Se prueba en ese orden: un archivo en
    cp1252 con acentos nunca es UTF-8 valido, asi que el orden no confunde uno
    con otro.

    Args:
        data: Contenido crudo del archivo.

    Returns:
        El texto, con saltos de linea normalizados a LF.
    """
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        text = data.decode("utf-16")
    else:
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = data.decode("cp1252", errors="replace")
    return text.replace("\r\n", "\n").replace("\r", "\n")


def parse_srt(text: str) -> list[Cue]:
    """Parsea un .srt tolerando los errores tipicos de los subidos a mano.

    Ignora el numero de secuencia (muchos vienen mal numerados o sin numero),
    acepta `.` en vez de `,` en los milisegundos y descarta bloques sin una
    linea de tiempos valida en vez de fallar.

    Args:
        text: Contenido del .srt ya decodificado (ver `decode_subtitle`).

    Returns:
        Los cues ordenados por inicio, sin los que quedaron con texto vacio.
    """
    cues = []
    for block in _BLOCK_SEPARATOR_RE.split(text.replace("\r\n", "\n").strip()):
        match = _TIMING_RE.search(block)
        if match is None:
            continue
        body = block[match.end() :].strip("\n").strip()
        if not body:
            continue
        cues.append(
            Cue(
                start=_seconds(match["h1"], match["m1"], match["s1"], match["ms1"]),
                end=_seconds(match["h2"], match["m2"], match["s2"], match["ms2"]),
                text=body,
            )
        )
    return sorted(cues, key=lambda cue: (cue.start, cue.end))


def format_srt(cues: Sequence[Cue]) -> str:
    """Serializa `cues` como .srt, renumerando desde 1.

    Args:
        cues: Cues a escribir, en el orden en que deben aparecer.

    Returns:
        El contenido del .srt (UTF-8 al escribirlo a disco).
    """
    blocks = [
        f"{number}\n{_timestamp(cue.start)} --> {_timestamp(cue.end)}\n{cue.text}"
        for number, cue in enumerate(cues, start=1)
    ]
    return "\n\n".join(blocks) + "\n"


def _seconds(hours: str, minutes: str, seconds: str, millis: str) -> float:
    # "5" en el campo de milisegundos es 500ms, no 5ms (se rellena a la derecha).
    return int(hours) * 3600 + int(minutes) * 60 + int(seconds) + int(millis.ljust(3, "0")) / 1000


def _timestamp(value: float) -> str:
    total_ms = max(0, round(value * 1000))
    hours, rest = divmod(total_ms, 3_600_000)
    minutes, rest = divmod(rest, 60_000)
    seconds, millis = divmod(rest, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"
