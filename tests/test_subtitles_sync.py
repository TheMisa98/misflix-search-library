from __future__ import annotations

import random

import pytest

from misflix.core.subtitles.srt import Cue
from misflix.core.subtitles.sync import (
    MIN_DIALOGUE_CUES,
    Alignment,
    ReferenceKind,
    SyncReport,
    SyncVerdict,
    apply_alignment,
    check_against_speech,
    check_against_subtitles,
)


def make_timeline(seed: int, count: int = 220, start: float = 30.0) -> list[Cue]:
    """Subtitulo sintetico con ritmo de dialogo (lineas de 1-4s, pausas de 0.2-3s)."""
    rng = random.Random(seed)
    cues = []
    t = start
    for i in range(count):
        t += rng.uniform(0.2, 3.0)
        duration = rng.uniform(1.0, 4.0)
        cues.append(Cue(start=round(t, 3), end=round(t + duration, 3), text=f"linea {i}"))
        t += duration
    return cues


def other_language(cues: list[Cue], seed: int = 99) -> list[Cue]:
    """El mismo dialogo "traducido": mismos tiempos con un jitter chico (otra segmentacion)."""
    rng = random.Random(seed)
    return [
        Cue(start=c.start + rng.uniform(-0.05, 0.05), end=c.end + rng.uniform(-0.05, 0.05), text=f"línea {i}")
        for i, c in enumerate(cues)
    ]


def shifted(cues: list[Cue], offset: float, ratio: float = 1.0) -> list[Cue]:
    return [Cue(start=c.start * ratio + offset, end=c.end * ratio + offset, text=c.text) for c in cues]


def speech_from(cues: list[Cue]) -> list[tuple[float, float]]:
    """Voz "detectada": los intervalos hablados, uniendo pausas cortas como hace el VAD."""
    merged: list[list[float]] = []
    for c in cues:
        if merged and c.start - merged[-1][1] <= 0.3:
            merged[-1][1] = c.end
        else:
            merged.append([c.start, c.end])
    return [(a, b) for a, b in merged]


def max_start_error(fixed: list[Cue], truth: list[Cue]) -> float:
    return max(abs(a.start - b.start) for a, b in zip(fixed, truth, strict=True))


REFERENCE = make_timeline(seed=1)
SPANISH = other_language(REFERENCE)
DURATION = REFERENCE[-1].end + 30


def test_identical_timing_is_in_sync():
    report = check_against_subtitles(SPANISH, REFERENCE)

    assert report.verdict == SyncVerdict.IN_SYNC
    assert report.reference == ReferenceKind.SUBTITLES


def test_constant_offset_is_fixable_and_apply_alignment_corrects_it():
    late = shifted(SPANISH, 3.2)

    report = check_against_subtitles(late, REFERENCE)
    fixed = apply_alignment(late, report)

    assert report.verdict == SyncVerdict.FIXABLE
    assert max_start_error(fixed, SPANISH) < 0.1


def test_split_offsets_from_a_different_cut_are_fixable():
    # la segunda mitad corrida 8s mas (ej. una version con una escena extra)
    half = len(SPANISH) // 2
    late = shifted(SPANISH[:half], 2.0) + shifted(SPANISH[half:], 10.0)

    report = check_against_subtitles(late, REFERENCE)
    fixed = apply_alignment(late, report)

    assert report.verdict == SyncVerdict.FIXABLE
    assert report.alignment.cuts >= 1
    assert max_start_error(fixed, SPANISH) < 0.1


def test_framerate_conversion_is_detected():
    pal = shifted(SPANISH, 1.0, ratio=23.976 / 25)

    report = check_against_subtitles(pal, REFERENCE)
    fixed = apply_alignment(pal, report)

    assert report.verdict == SyncVerdict.FIXABLE
    assert report.alignment.ratio == pytest.approx(25 / 23.976)
    assert max_start_error(fixed, SPANISH) < 0.15


def test_subtitle_from_another_episode_is_wrong():
    unrelated = make_timeline(seed=7)

    report = check_against_subtitles(unrelated, REFERENCE)

    assert report.verdict == SyncVerdict.WRONG


def test_speech_reference_fixes_an_offset():
    late = shifted(SPANISH, -4.5)

    report = check_against_speech(late, speech_from(REFERENCE), DURATION)
    fixed = apply_alignment(late, report)

    assert report.reference == ReferenceKind.SPEECH
    assert report.verdict == SyncVerdict.FIXABLE
    assert max_start_error(fixed, SPANISH) < 0.2


def test_speech_reference_rejects_another_episode():
    report = check_against_speech(make_timeline(seed=7), speech_from(REFERENCE), DURATION)

    assert report.verdict == SyncVerdict.WRONG


def test_too_few_dialogue_cues_is_uncertain_and_untouched():
    few = SPANISH[: MIN_DIALOGUE_CUES - 1]

    report = check_against_subtitles(few, REFERENCE)

    assert report.verdict == SyncVerdict.UNCERTAIN
    assert apply_alignment(few, report) == few


def test_apply_alignment_moves_annotations_with_the_nearest_dialogue_and_drops_credits():
    cues = [
        Cue(0.0, 2.0, "Subtítulos por aRGENTeaM"),
        Cue(10.0, 11.0, "Hola."),
        Cue(11.5, 12.0, "[música]"),
        Cue(20.0, 21.0, "Chau."),
    ]
    dialogue = (cues[1], cues[3])
    report = SyncReport(
        verdict=SyncVerdict.FIXABLE,
        reference=ReferenceKind.SUBTITLES,
        score=0.5,
        in_sync_fraction=0.0,
        alignment=Alignment(ratio=1.0, offsets=(1.0, -2.0)),
        dialogue=dialogue,
    )

    fixed = apply_alignment(cues, report)

    assert [(c.start, c.end, c.text) for c in fixed] == [
        (11.0, 12.0, "Hola."),
        (12.5, 13.0, "[música]"),
        (18.0, 19.0, "Chau."),
    ]


def test_apply_alignment_trims_overlaps_created_by_a_cut():
    cues = [Cue(10.0, 13.0, "Uno."), Cue(14.0, 15.0, "Dos.")]
    report = SyncReport(
        verdict=SyncVerdict.FIXABLE,
        reference=ReferenceKind.SUBTITLES,
        score=0.5,
        in_sync_fraction=0.0,
        alignment=Alignment(ratio=1.0, offsets=(0.0, -2.0)),
        dialogue=tuple(cues),
    )

    fixed = apply_alignment(cues, report)

    assert fixed[0].end <= fixed[1].start == 12.0


def test_alignment_cuts_counts_offset_changes():
    assert Alignment(ratio=1.0, offsets=(1.0, 1.0, 2.0, 2.0, 1.0)).cuts == 2
