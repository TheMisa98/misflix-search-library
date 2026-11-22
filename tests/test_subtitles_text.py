from __future__ import annotations

import pytest

from misflix.core.subtitles.srt import Cue
from misflix.core.subtitles.text import (
    detect_language,
    dialogue_cues,
    drop_credits,
    is_credit,
    is_dialogue,
    plain_text,
)


@pytest.mark.parametrize(
    "text",
    [
        "Subtítulos por aRGENTeaM",
        "Traducido y sincronizado por Pepe",
        "www.subdivx.com",
        "<font color=#ff0>Synced and corrected by someone</font>",
        "Visitá https://example.org",
    ],
)
def test_is_credit_detects_uploader_credits(text):
    assert is_credit(text)


@pytest.mark.parametrize("text", ["¿Quién te tradujo eso?", "Sincronicemos los relojes.", "Lo corregí ayer."])
def test_is_credit_keeps_dialogue_that_mentions_the_same_words(text):
    assert not is_credit(text)


@pytest.mark.parametrize("text", ["[música]", "(PUERTA SE CIERRA)", "♪ ♪", "<i>[suspira]</i>\n(ríe)", "  "])
def test_is_dialogue_rejects_sound_descriptions_and_music(text):
    assert not is_dialogue(text)


@pytest.mark.parametrize("text", ["Hola.", "[Juan] Vení acá.", "- ¿Qué?\n[música]", "<i>Nunca.</i>"])
def test_is_dialogue_accepts_lines_with_spoken_text(text):
    assert is_dialogue(text)


def test_plain_text_strips_html_and_ass_tags():
    assert plain_text("{\\an8}<i>Hola</i> <font color='red'>vos</font>") == "Hola vos"


def test_dialogue_cues_and_drop_credits_filter_differently():
    cues = [
        Cue(0, 1, "Subtítulos por aRGENTeaM"),
        Cue(2, 3, "[música]"),
        Cue(4, 5, "Hola."),
    ]

    assert [c.text for c in dialogue_cues(cues)] == ["Hola."]
    # el archivo final conserva las anotaciones, solo pierde los creditos
    assert [c.text for c in drop_credits(cues)] == ["[música]", "Hola."]


def _cues(text: str) -> list[Cue]:
    return [Cue(i, i + 1, line) for i, line in enumerate(text.split(". "))]


def test_detect_language_recognizes_spanish_and_english():
    spanish = _cues(
        "Que no, que no es eso. Por qué me decís que está bien si no lo está. "
        "Yo quiero que usted se vaya ahora. Pero eso no es para mí, señor. "
        "Hay nada que hacer aquí, también lo sé. Cómo estoy? Bien, muy bien, gracias. "
        "Lo que te digo es que la casa y el auto son de los dos"
    )
    english = _cues(
        "I don't know what you are talking about. It's not my fault, you know that. "
        "We have to go right now, there is no time. Do you think she can do this? "
        "I'm just saying that the plan is not working. What is this all about? "
        "He was here on the night of the party with your friends"
    )

    assert detect_language(spanish) == "es"
    assert detect_language(english) == "en"


def test_detect_language_needs_enough_text():
    assert detect_language(_cues("Hola. Chau")) is None
