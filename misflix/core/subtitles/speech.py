from __future__ import annotations

from collections.abc import Sequence

# Cada nivel es el RMS (dBFS) de una ventana de 50ms del canal de dialogo
# (ver `infra/media_probe.py`, que los extrae con ffmpeg).
LEVEL_STEP_SECONDS = 0.05
SILENCE_DB = -120.0

# Calibrado en vivo contra House S01E01 (audio 5.1, subtitulo ingles
# incrustado como verdad): umbral = piso de ruido + 12dB detecta voz con
# ~74% de precision y ~73% de recall frente a los intervalos del subtitulo, y
# el desfase que sale de alinear contra eso coincide con el de la referencia
# (-0.15s vs -0.14s, +1.85s vs +1.82s). Por debajo (+6/+9dB) la musica
# empieza a contar como voz; por encima (+15/+18dB) se pierden frases dichas
# en voz baja.
_THRESHOLD_ABOVE_FLOOR_DB = 12.0
# El "piso" es el percentil 10 de los niveles audibles: el ruido de fondo de
# ese canal en particular, asi el umbral se adapta a cada mezcla en vez de
# ser un dBFS fijo.
_FLOOR_PERCENTILE = 0.10
_MAX_GAP_SECONDS = 0.3
_MIN_SEGMENT_SECONDS = 0.2


def detect_speech(levels_db: Sequence[float], step: float = LEVEL_STEP_SECONDS) -> list[tuple[float, float]]:
    """Convierte niveles RMS por ventana en intervalos de voz.

    Pensado para el canal central de una mezcla 5.1/7.1, donde va el dialogo
    casi sin musica ni efectos — por eso un simple umbral de energia
    adaptativo alcanza, a diferencia de aplicarlo sobre la mezcla completa
    (que es lo que confunde al sincronizador por audio: arranca el subtitulo
    donde empieza a sonar *cualquier cosa*).

    Args:
        levels_db: Nivel RMS en dBFS de cada ventana consecutiva
            (`SILENCE_DB` o menos para silencio digital).
        step: Duracion de cada ventana, en segundos.

    Returns:
        Intervalos `(inicio, fin)` en segundos, ordenados y sin solaparse.
        Vacio si no hay nada audible.
    """
    audible = sorted(level for level in levels_db if level > SILENCE_DB + 20)
    if not audible:
        return []
    floor = audible[int(len(audible) * _FLOOR_PERCENTILE)]
    threshold = floor + _THRESHOLD_ABOVE_FLOOR_DB

    segments: list[list[float]] = []
    for index, level in enumerate(levels_db):
        if level < threshold:
            continue
        start = index * step
        if segments and start - segments[-1][1] <= _MAX_GAP_SECONDS:
            segments[-1][1] = start + step
        else:
            segments.append([start, start + step])
    return [(start, end) for start, end in segments if end - start >= _MIN_SEGMENT_SECONDS]
