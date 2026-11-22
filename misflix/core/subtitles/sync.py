"""Alinear un subtitulo contra una referencia y decidir si quedo bien sincronizado.

Dos referencias posibles, en orden de preferencia:

- **Otro subtitulo ya sincronizado con el video** (tipicamente el ingles que
  viene incrustado en el .mkv): se alinean los *inicios* de los cues, porque
  dos subtitulos del mismo episodio — aunque esten en idiomas distintos —
  arrancan sus lineas casi en los mismos instantes.
- **La voz detectada en el audio** (ver `speech.py`): se maximiza cuanto de la
  duracion de cada cue cae sobre voz.

En ambos casos el alineador asigna a cada cue un desfase elegido entre
candidatos (picos del histograma de diferencias de tiempo), permitiendo
*cortes* — tramos con desfases distintos, para versiones con cortes
comerciales o escenas de mas/de menos — pero cobrando una penalizacion por
cada corte y prohibiendo saltos de mas de `MAX_JUMP_SECONDS` entre tramos.
Sin esos dos limites el alineador "fuerza" cualquier subtitulo sobre
cualquier video (verificado en vivo: un subtitulo de *Safe House* llegaba a
puntuar parecido a uno correcto de *House* saltando cientos de segundos
entre tramos).

Para decidir si el subtitulo corresponde de verdad a ese video, el puntaje
tras alinear se compara contra un **modelo nulo**: el mismo subtitulo
invertido en el tiempo (mismo ritmo y duraciones de lineas, pero sin
relacion real con el dialogo), alineado con el mismo algoritmo. Asi el
umbral no depende de cuanto dialogo tenga el episodio (en una serie muy
hablada, cualquier cosa cae "sobre voz" por azar).
"""

from __future__ import annotations

import bisect
from collections import Counter, deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from misflix.core.subtitles.srt import Cue
from misflix.core.subtitles.text import dialogue_cues, drop_credits

# Conversiones de framerate tipicas entre versiones (PAL 25fps vs NTSC
# 23.976/24fps): un subtitulo hecho para un rip PAL se "estira" respecto del
# video NTSC y ningun desfase constante lo arregla.
FRAMERATE_RATIOS = (1.0, 25 / 23.976, 23.976 / 25, 24 / 23.976, 23.976 / 24, 25 / 24, 24 / 25)
MAX_JUMP_SECONDS = 20.0
MIN_DIALOGUE_CUES = 20

_BIN_SECONDS = 0.1
_FINE_STEPS = (-0.06, -0.03, 0.0, 0.03, 0.06)
_WINDOW_CUES = 40
_PEAKS_PER_WINDOW = 4
_GLOBAL_PEAKS = 10
_RATIOS_TO_REFINE = 2
_MAX_BACKWARDS_OVERLAP = 0.5

# Un cue cuenta como "ya sincronizado" si el alineador lo movio menos que esto.
_IN_SYNC_TOLERANCE = 0.25
_IN_SYNC_FRACTION = 0.8


class ReferenceKind(StrEnum):
    """Contra que se alineo el subtitulo."""

    SUBTITLES = "subtitulos"
    SPEECH = "audio"


class SyncVerdict(StrEnum):
    """Resultado de verificar un subtitulo contra su video."""

    IN_SYNC = "sincronizado"
    FIXABLE = "corregible"
    UNCERTAIN = "dudoso"
    WRONG = "no_coincide"


@dataclass(frozen=True)
class Alignment:
    """Transformacion de tiempos para cada cue de dialogo.

    El tiempo `t` del cue `i` pasa a `t * ratio + offsets[i]`.

    Attributes:
        ratio: Factor de escala (conversion de framerate), 1.0 si no hace falta.
        offsets: Desfase en segundos de cada cue de dialogo, en orden.
    """

    ratio: float
    offsets: tuple[float, ...]

    @property
    def cuts(self) -> int:
        """Cantidad de cortes (cambios de desfase entre cues consecutivos)."""
        return sum(1 for a, b in zip(self.offsets, self.offsets[1:], strict=False) if a != b)


@dataclass(frozen=True)
class SyncReport:
    """Veredicto de sincronia de un subtitulo contra su video.

    Attributes:
        verdict: Que hacer con el subtitulo (ver `SyncVerdict`).
        reference: Contra que se alineo.
        score: Puntaje tras alinear menos el del modelo nulo; mas alto es
            mejor (ver `_THRESHOLDS` para los cortes de cada referencia).
        in_sync_fraction: Fraccion de cues que el alineador no necesito mover
            (mas de `_IN_SYNC_TOLERANCE`).
        alignment: La transformacion encontrada, sobre `dialogue`.
        dialogue: Los cues de dialogo que se alinearon (los que miden la
            sincronia; ver `text.is_dialogue`).
    """

    verdict: SyncVerdict
    reference: ReferenceKind
    score: float
    in_sync_fraction: float
    alignment: Alignment
    dialogue: tuple[Cue, ...]


# (umbral para "coincide", umbral para "no coincide"); entre ambos: dudoso.
# Calibrado en vivo con House S01E01 (4 subtitulos correctos de distintas
# versiones vs 2 de otra serie que habia en la biblioteca): contra el
# subtitulo ingles, los correctos dan +0.60..+0.64 y los de otra serie
# +0.01..+0.03; contra la voz, los correctos +0.34..+0.43 y los de otra serie
# +0.00..+0.05.
_THRESHOLDS = {
    ReferenceKind.SUBTITLES: (0.30, 0.12),
    ReferenceKind.SPEECH: (0.20, 0.10),
}


class _Scorer(Protocol):
    kind: ReferenceKind
    penalty: float
    max_offset: float
    anchors: list[float]

    def gain(self, start: float, end: float) -> float: ...

    def metric(self, starts: Sequence[float], ends: Sequence[float]) -> float: ...


class _SubtitleScorer:
    """Puntua cues contra los inicios de un subtitulo de referencia."""

    kind = ReferenceKind.SUBTITLES
    # Un cue alineado suma hasta 1.0: un corte tiene que justificarse con
    # varias lineas mejor alineadas.
    penalty = 6.0
    max_offset = 300.0
    _soft_tolerance = 0.5
    _hard_tolerance = 0.3

    def __init__(self, reference: Sequence[Cue]):
        self.anchors = sorted(cue.start for cue in reference)

    def gain(self, start: float, end: float) -> float:
        return max(0.0, 1.0 - _distance_to_nearest(self.anchors, start) / self._soft_tolerance)

    def metric(self, starts: Sequence[float], ends: Sequence[float]) -> float:
        hits = sum(1 for start in starts if _distance_to_nearest(self.anchors, start) <= self._hard_tolerance)
        return hits / len(starts) if starts else 0.0


class _SpeechScorer:
    """Alinea por cuanto de cada cue cae sobre voz; verifica por inicios de voz.

    La alineacion maximiza la *cobertura* (fraccion de cada cue sobre voz),
    que guia bien hacia el desfase correcto. Pero como metrica de
    verificacion es gruesa: verificado en vivo, un subtitulo de otra serie
    lograba +0.16 de cobertura sobre el nulo eligiendo un framerate y un
    desfase de -205s que hacian coincidir sus zonas densas de dialogo con las
    del episodio. La metrica final es entonces la fraccion de cues que
    *arrancan* junto a un inicio de voz (tras un silencio), que no se puede
    forzar a escala gruesa: +0.34..+0.43 los correctos vs +0.00..+0.05 los de
    otra serie.
    """

    kind = ReferenceKind.SPEECH
    # La ganancia por cue es cobertura menos azar (~0.3 como mucho en una
    # serie muy hablada), asi que la penalizacion esta en esa escala.
    penalty = 4.0
    # Contra la voz, un desfase mas grande que esto solo aparece forzando un
    # subtitulo que no corresponde; los resumenes "previamente en..." o intros
    # distintas mueven bastante menos.
    max_offset = 120.0
    _onset_tolerance = 0.3

    def __init__(self, speech: Sequence[tuple[float, float]], duration: float):
        self._timeline = _Timeline(speech)
        self.anchors = [start for start, _ in self._timeline.intervals]
        self._chance = min(0.95, self._timeline.total / duration) if duration > 0 else 0.5

    def gain(self, start: float, end: float) -> float:
        length = end - start
        if length <= 0:
            return 0.0
        return self._timeline.covered(start, end) / length - self._chance

    def metric(self, starts: Sequence[float], ends: Sequence[float]) -> float:
        hits = sum(1 for start in starts if _distance_to_nearest(self.anchors, start) <= self._onset_tolerance)
        return hits / len(starts) if starts else 0.0


class _Timeline:
    """Union de intervalos con consulta rapida de "cuanto de [a, b) cubre"."""

    def __init__(self, intervals: Sequence[tuple[float, float]]):
        merged: list[list[float]] = []
        for start, end in sorted(intervals):
            if merged and start <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], end)
            else:
                merged.append([start, end])
        self.intervals = [(start, end) for start, end in merged]
        self._starts = [start for start, _ in self.intervals]
        self.total = sum(end - start for start, end in self.intervals)

    def covered(self, start: float, end: float) -> float:
        index = max(0, bisect.bisect_right(self._starts, start) - 1)
        total = 0.0
        while index < len(self.intervals) and self.intervals[index][0] < end:
            a, b = self.intervals[index]
            total += max(0.0, min(b, end) - max(a, start))
            index += 1
        return total


def check_against_subtitles(cues: Sequence[Cue], reference: Sequence[Cue]) -> SyncReport:
    """Alinea `cues` contra otro subtitulo ya sincronizado y emite un veredicto.

    Args:
        cues: Subtitulo a verificar (ej. el español bajado de subdivx).
        reference: Subtitulo ya sincronizado con el video, en cualquier
            idioma (ej. el ingles incrustado en el .mkv).

    Returns:
        El reporte, con la alineacion lista para `apply_alignment`.
    """
    return _check(cues, _SubtitleScorer(dialogue_cues(reference)))


def check_against_speech(cues: Sequence[Cue], speech: Sequence[tuple[float, float]], duration: float) -> SyncReport:
    """Alinea `cues` contra la voz detectada en el audio y emite un veredicto.

    Args:
        cues: Subtitulo a verificar.
        speech: Intervalos de voz (ver `speech.detect_speech`).
        duration: Duracion total del audio analizado, en segundos.

    Returns:
        El reporte, con la alineacion lista para `apply_alignment`.
    """
    return _check(cues, _SpeechScorer(speech, duration))


def apply_alignment(cues: Sequence[Cue], report: SyncReport) -> list[Cue]:
    """Aplica la alineacion de `report` a todo el subtitulo.

    Los cues de dialogo usan su propio desfase; el resto (anotaciones tipo
    `[musica]`, que no se alinearon) toma el del cue de dialogo mas cercano.
    Los creditos del uploader se descartan. Si tras mover los tramos dos
    cues quedan pisados, el anterior se recorta para que no se superpongan.

    Args:
        cues: Subtitulo completo original (el mismo que se verifico).
        report: Resultado de `check_against_subtitles`/`check_against_speech`.

    Returns:
        El subtitulo con los tiempos corregidos, ordenado.
    """
    dialogue = report.dialogue
    if not dialogue:
        return drop_credits(cues)
    dialogue_starts = [cue.start for cue in dialogue]
    ratio = report.alignment.ratio
    moved = []
    for cue in drop_credits(cues):
        index = _nearest_index(dialogue_starts, cue.start)
        offset = report.alignment.offsets[index]
        start = cue.start * ratio + offset
        end = cue.end * ratio + offset
        if end <= 0:
            continue
        moved.append(Cue(start=max(0.0, start), end=end, text=cue.text))
    moved.sort(key=lambda cue: (cue.start, cue.end))

    fixed: list[Cue] = []
    for cue in moved:
        if fixed and fixed[-1].end > cue.start:
            previous = fixed[-1]
            fixed[-1] = Cue(start=previous.start, end=max(previous.start + 0.05, cue.start - 0.001), text=previous.text)
        fixed.append(cue)
    return fixed


def _check(cues: Sequence[Cue], scorer: _Scorer) -> SyncReport:
    dialogue = tuple(dialogue_cues(cues))
    identity = Alignment(ratio=1.0, offsets=tuple(0.0 for _ in dialogue))
    if len(dialogue) < MIN_DIALOGUE_CUES or len(scorer.anchors) < MIN_DIALOGUE_CUES:
        return SyncReport(SyncVerdict.UNCERTAIN, scorer.kind, 0.0, 0.0, identity, dialogue)

    alignment, value = _best_alignment(dialogue, scorer)
    _, null_value = _best_alignment(_reversed(dialogue), scorer)
    score = value - null_value

    in_sync = sum(
        1
        for cue, offset in zip(dialogue, alignment.offsets, strict=True)
        if abs(cue.start * alignment.ratio + offset - cue.start) <= _IN_SYNC_TOLERANCE
    ) / len(dialogue)

    match_threshold, wrong_threshold = _THRESHOLDS[scorer.kind]
    if score >= match_threshold:
        verdict = SyncVerdict.IN_SYNC if in_sync >= _IN_SYNC_FRACTION else SyncVerdict.FIXABLE
    elif score <= wrong_threshold:
        verdict = SyncVerdict.WRONG
    else:
        verdict = SyncVerdict.UNCERTAIN
    return SyncReport(verdict, scorer.kind, score, in_sync, alignment, dialogue)


def _best_alignment(cues: Sequence[Cue], scorer: _Scorer) -> tuple[Alignment, float]:
    """Prueba los ratios de framerate mas prometedores y se queda con el mejor."""
    ranked = sorted(
        FRAMERATE_RATIOS,
        key=lambda ratio: _histogram_peak([cue.start * ratio for cue in cues], scorer.anchors, scorer.max_offset),
        reverse=True,
    )
    best: tuple[Alignment, float] | None = None
    for ratio in ranked[:_RATIOS_TO_REFINE]:
        starts = [cue.start * ratio for cue in cues]
        ends = [cue.end * ratio for cue in cues]
        candidates = _candidate_offsets(starts, scorer.anchors, scorer.max_offset)
        offsets = _dynamic_alignment(starts, ends, candidates, scorer)
        value = scorer.metric(
            [s + o for s, o in zip(starts, offsets, strict=True)], [e + o for e, o in zip(ends, offsets, strict=True)]
        )
        if best is None or value > best[1]:
            best = (Alignment(ratio=ratio, offsets=tuple(offsets)), value)
    assert best is not None  # FRAMERATE_RATIOS nunca esta vacio
    return best


def _dynamic_alignment(
    starts: Sequence[float], ends: Sequence[float], offsets: Sequence[float], scorer: _Scorer
) -> list[float]:
    """Elige un desfase por cue maximizando ganancia menos penalizacion por corte.

    Programacion dinamica sobre los cues en orden: en cada paso, cada
    desfase candidato puede seguir con el mismo desfase del cue anterior, o
    "cortar" desde el mejor desfase previo a menos de `MAX_JUMP_SECONDS`
    (pagando `scorer.penalty`), siempre que el corte no mande el cue antes
    que el anterior (no se reordenan lineas).
    """
    count = len(offsets)
    gain: Callable[[int, int], float] = lambda i, k: scorer.gain(starts[i] + offsets[k], ends[i] + offsets[k])  # noqa: E731
    totals = [gain(0, k) for k in range(count)]
    back: list[list[int]] = [list(range(count))]
    for i in range(1, len(starts)):
        window_best = _window_argmax(offsets, totals, MAX_JUMP_SECONDS)
        row: list[int] = []
        new_totals: list[float] = []
        for k in range(count):
            j = window_best[k]
            jump_total = totals[j] - scorer.penalty
            keeps_order = starts[i] + offsets[k] >= starts[i - 1] + offsets[j] - _MAX_BACKWARDS_OVERLAP
            if j != k and jump_total > totals[k] and keeps_order:
                new_totals.append(jump_total + gain(i, k))
                row.append(j)
            else:
                new_totals.append(totals[k] + gain(i, k))
                row.append(k)
        totals = new_totals
        back.append(row)

    k = max(range(count), key=lambda index: totals[index])
    path = [k]
    for i in range(len(starts) - 1, 0, -1):
        k = back[i][k]
        path.append(k)
    path.reverse()
    return [offsets[index] for index in path]


def _candidate_offsets(starts: Sequence[float], anchors: Sequence[float], max_offset: float) -> list[float]:
    """Desfases candidatos: picos del histograma de (ancla - inicio), global y por ventanas.

    Las ventanas hacen que un tramo corto con un desfase propio (ej. despues
    de un corte comercial) aporte su pico aunque globalmente pese poco.
    """
    global_votes: Counter[int] = Counter()
    picked: set[int] = set()
    for window_start in range(0, len(starts), _WINDOW_CUES // 2):
        votes = _offset_votes(starts[window_start : window_start + _WINDOW_CUES], anchors, max_offset)
        global_votes.update(votes)
        picked.update(bin_ for bin_, _ in votes.most_common(_PEAKS_PER_WINDOW))
    picked.update(bin_ for bin_, _ in global_votes.most_common(_GLOBAL_PEAKS))
    return sorted({round(bin_ * _BIN_SECONDS + step, 3) for bin_ in picked for step in _FINE_STEPS})


def _histogram_peak(starts: Sequence[float], anchors: Sequence[float], max_offset: float) -> int:
    votes = _offset_votes(starts, anchors, max_offset)
    return max(votes.values(), default=0)


def _offset_votes(starts: Sequence[float], anchors: Sequence[float], max_offset: float) -> Counter[int]:
    votes: Counter[int] = Counter()
    for start in starts:
        low = bisect.bisect_left(anchors, start - max_offset)
        high = bisect.bisect_right(anchors, start + max_offset)
        for anchor in anchors[low:high]:
            votes[round((anchor - start) / _BIN_SECONDS)] += 1
    return votes


def _window_argmax(positions: Sequence[float], values: Sequence[float], radius: float) -> list[int]:
    """Para cada `k`, el indice del mayor `values[j]` con `|positions[j] - positions[k]| <= radius`.

    `positions` tiene que estar ordenado. Ventana deslizante con una deque
    monotona: O(n) en vez de O(n^2).
    """
    result: list[int] = []
    window: deque[int] = deque()
    low = high = 0
    for position in positions:
        while high < len(positions) and positions[high] <= position + radius:
            while window and values[window[-1]] <= values[high]:
                window.pop()
            window.append(high)
            high += 1
        while positions[low] < position - radius:
            low += 1
        while window[0] < low:
            window.popleft()
        result.append(window[0])
    return result


def _reversed(cues: Sequence[Cue]) -> list[Cue]:
    end = max(cue.end for cue in cues)
    return sorted((Cue(start=end - cue.end, end=end - cue.start, text=cue.text) for cue in cues), key=lambda c: c.start)


def _distance_to_nearest(sorted_values: Sequence[float], value: float) -> float:
    index = bisect.bisect_left(sorted_values, value)
    best = float("inf")
    for candidate in (index - 1, index):
        if 0 <= candidate < len(sorted_values):
            best = min(best, abs(sorted_values[candidate] - value))
    return best


def _nearest_index(sorted_values: Sequence[float], value: float) -> int:
    index = bisect.bisect_left(sorted_values, value)
    if index == 0:
        return 0
    if index == len(sorted_values):
        return len(sorted_values) - 1
    return index if sorted_values[index] - value < value - sorted_values[index - 1] else index - 1
