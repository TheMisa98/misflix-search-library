from __future__ import annotations

from misflix.core.subtitles.speech import SILENCE_DB, detect_speech


def _levels(pattern: list[tuple[float, int]]) -> list[float]:
    """Arma niveles a partir de tramos (dB, cantidad de ventanas de 50ms)."""
    levels: list[float] = []
    for db, count in pattern:
        levels.extend([db] * count)
    return levels


def test_detect_speech_finds_segments_above_the_adaptive_floor():
    # ruido de fondo a -60dB, dos frases a -30dB
    levels = _levels([(-60, 40), (-30, 20), (-60, 40), (-30, 10), (-60, 40)])

    segments = detect_speech(levels)

    assert segments == [(2.0, 3.0), (5.0, 5.5)]


def test_detect_speech_adapts_to_a_louder_noise_floor():
    # el mismo patron 20dB mas fuerte da los mismos tramos: el umbral es relativo al piso
    quiet = detect_speech(_levels([(-60, 40), (-30, 20), (-60, 40)]))
    loud = detect_speech(_levels([(-40, 40), (-10, 20), (-40, 40)]))

    assert quiet == loud == [(2.0, 3.0)]


def test_detect_speech_merges_short_gaps_and_drops_blips():
    # pausa de 0.2s dentro de la frase (se une) y un golpe de 50ms (se descarta)
    levels = _levels([(-60, 40), (-30, 10), (-60, 4), (-30, 10), (-60, 40), (-30, 1), (-60, 40)])

    assert detect_speech(levels) == [(2.0, 3.2)]


def test_detect_speech_of_digital_silence_is_empty():
    assert detect_speech([SILENCE_DB] * 100) == []
    assert detect_speech([]) == []
