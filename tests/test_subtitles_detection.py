from __future__ import annotations

from misflix.core.subtitles.detection import (
    LanguageGuess,
    burned_subtitle_language,
    parse_whisper_language,
    spoken_language,
)


def test_parse_whisper_language_reads_the_auto_detected_line():
    output = (
        "whisper_init_from_file_with_params_no_state: loading model from 'ggml-base.bin'\n"
        "whisper_full_with_state: auto-detected language: es (p = 0.976021)\n"
    )

    assert parse_whisper_language(output) == LanguageGuess("es", 0.976021)
    assert parse_whisper_language("sin deteccion") is None


def test_spoken_language_needs_two_confident_votes_and_no_tie():
    es, en, unsure = LanguageGuess("es", 0.95), LanguageGuess("en", 0.9), LanguageGuess("en", 0.4)

    assert spoken_language([es, es]) == "es"
    assert spoken_language([es, es, en]) == "es"
    assert spoken_language([es]) is None  # un fragmento no alcanza
    assert spoken_language([es, en]) is None
    assert spoken_language([es, unsure, unsure]) is None
    assert spoken_language([es, es, en, en]) is None  # empate
    assert spoken_language([es, es, en, LanguageGuess("ja", 0.9)]) == "es"  # pluralidad


def test_spoken_language_ignores_hallucinations_on_clips_without_dialogue():
    # Kill the Jockey, verificado en vivo: musica/ambiente en 3 de 5 fragmentos
    guesses = [
        LanguageGuess("ja", 0.878),
        LanguageGuess("en", 0.673),
        LanguageGuess("en", 0.366),
        LanguageGuess("es", 0.897),
        LanguageGuess("es", 0.852),
    ]

    assert spoken_language(guesses) == "es"
    assert spoken_language(guesses[:3]) is None  # sin los fragmentos con dialogo todavia no decide


def test_burned_subtitle_language_from_ocr_text():
    # lo que leeria el OCR en los fotogramas de Kimetsu no Yaiba T3 (verificado en vivo)
    spanish = [
        "¿Ha habido alguna vez que yo\nno haya cumplido sus expectativas?",
        "No te preocupes, yo me encargo de eso.",
        "",
        "Lo que te digo es que no hay nada que hacer aquí.",
        "¿Por qué está usted tan seguro?",
    ]

    assert burned_subtitle_language(spanish) == "es"
    assert burned_subtitle_language(["", "|— ii", "WN ~ ."]) is None  # fotogramas sin subtitulos
