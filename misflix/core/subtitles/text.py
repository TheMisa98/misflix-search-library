from __future__ import annotations

import re
from collections.abc import Iterable, Sequence

from misflix.core.subtitles.srt import Cue

_MARKUP_RE = re.compile(r"<[^>]+>|\{\\[^}]*\}")
# Una linea "solo ruido": toda entre corchetes/parentesis ([musica], (PUERTA SE
# CIERRA)) o hecha solo de simbolos de musica/guiones.
_NON_DIALOGUE_LINE_RE = re.compile(r"^\s*(?:\[[^\]]*\]|\([^)]*\)|[♪♫#*~\s-]+)\s*$")
# Creditos/propaganda que los uploaders meten al principio o al final
# ("Subtitulos por aRGENTeaM", "www.subdivx.com", "Sincronizado por X"). Se
# exige la forma "<accion> por/by" o un sitio conocido, para no tirar dialogo
# real que mencione la palabra suelta.
_CREDIT_RE = re.compile(
    r"(?:https?://|www\.|\.(?:com|net|org|tv)\b|subdivx|argenteam|addic7ed|opensubtitles|tusseries|"
    r"subs-?team|thesubfactory|subt[ií]tulos\.es|"
    r"(?:traducid[oa]s?|traducci[oó]n|sincronizad[oa]s?|sincro(?:nizaci[oó]n)?|resincronizad[oa]s?|"
    r"corregid[oa]s?|correcci[oó]n|ripead[oa]s?|subtitulad[oa]s?|subt[ií]tulos?|adaptad[oa]s?|"
    r"subs|synced|corrected|ripped|translated|subtitles)\s+(?:y\s+\w+\s+)?(?:por|by)\b)",
    re.IGNORECASE,
)

_STOPWORDS = {
    "es": frozenset(
        (
            "que el la los las y es está qué por para pero eso esto muy bien yo tú usted ahora aquí "
            "no un una lo se con del mi te me sí cómo estoy hay nada señor también puedo quiero"
        ).split()
    ),
    "en": frozenset(
        (
            "the you i to and it of is that what in me this we he on for your have don't it's my be "
            "not are with was just i'm know do can they there here all right she so"
        ).split()
    ),
    "pt": frozenset(
        (
            "não você é eu um uma isso está com para mas do da os ele ela muito bem aqui agora "
            "também que de o e se vai tem estou obrigado sim meu minha"
        ).split()
    ),
    "fr": frozenset(
        (
            "je tu vous nous est pas le la les et que qui c'est un une ce il elle pour avec mais "
            "oui non moi toi bien ça"
        ).split()
    ),
    "it": frozenset(
        (
            "che non il di e la è un per sono mi ti lo cosa questo bene sì ma con io tu lei lui anche ancora molto"
        ).split()
    ),
    "de": frozenset(
        (
            "ich du sie nicht das ist und der die es ein eine was wir zu mit ja nein auf mir dich "
            "mich aber auch noch hier"
        ).split()
    ),
}
_WORD_RE = re.compile(r"[a-záéíóúñüàâçèêëîïôœùûãõ']+")
_MIN_WORDS_FOR_LANGUAGE = 40
_MIN_STOPWORD_RATIO = 0.12


def plain_text(text: str) -> str:
    """Saca tags de formato (`<i>`, `<font>`, tags ASS entre llaves) dejando solo el texto visible.

    Args:
        text: Texto de un cue.

    Returns:
        El texto sin markup.
    """
    return _MARKUP_RE.sub("", text)


def is_credit(text: str) -> bool:
    """Indica si un cue es propaganda/creditos del uploader en vez de dialogo.

    Args:
        text: Texto de un cue.

    Returns:
        True para cosas como "Subtitulos por aRGENTeaM" o "www.subdivx.com".
    """
    return bool(_CREDIT_RE.search(plain_text(text)))


def is_dialogue(text: str) -> bool:
    """Indica si un cue tiene dialogo hablado (no solo efectos, musica o creditos).

    Es el filtro que se aplica antes de alinear: un `[musica]` o un credito de
    10 segundos al principio no corresponden a nadie hablando, y si entran al
    alineador lo tiran hacia tramos de musica/efectos (justamente la falla del
    sincronizador por audio general).

    Args:
        text: Texto de un cue.

    Returns:
        True si al menos una linea del cue parece dialogo.
    """
    visible = plain_text(text)
    if not visible.strip() or is_credit(visible):
        return False
    lines = [line for line in visible.splitlines() if line.strip()]
    return not all(_NON_DIALOGUE_LINE_RE.match(line) for line in lines)


def dialogue_cues(cues: Iterable[Cue]) -> list[Cue]:
    """Filtra `cues` dejando solo los de dialogo (ver `is_dialogue`).

    Args:
        cues: Cues a filtrar.

    Returns:
        Los cues de dialogo, en el mismo orden.
    """
    return [cue for cue in cues if is_dialogue(cue.text)]


def drop_credits(cues: Iterable[Cue]) -> list[Cue]:
    """Saca los cues de creditos/propaganda (ver `is_credit`), dejando el resto intacto.

    Se usa para el archivo final: a diferencia de `dialogue_cues`, conserva
    las anotaciones tipo `[musica]` (son parte del subtitulo, solo no sirven
    para alinear).

    Args:
        cues: Cues a filtrar.

    Returns:
        Los cues sin creditos, en el mismo orden.
    """
    return [cue for cue in cues if not is_credit(cue.text)]


def detect_language(cues: Sequence[Cue]) -> str | None:
    """Adivina el idioma de un subtitulo contando palabras frecuentes.

    Hace falta porque el nombre del archivo no es confiable (verificado en
    vivo: en la biblioteca hay `.srt` sin sufijo de idioma, y `.hi.srt` que
    no son para hipoacusicos sino de otra serie entera).

    Args:
        cues: Cues del subtitulo.

    Returns:
        Codigo ISO 639-1 (`es`, `en`, `pt`, `fr`, `it`, `de`), o None si
        hay muy poco texto o ningun idioma se destaca.
    """
    return detect_text_language(" ".join(plain_text(cue.text) for cue in cues))


def detect_text_language(text: str, min_words: int = _MIN_WORDS_FOR_LANGUAGE) -> str | None:
    """Adivina el idioma de un texto libre contando palabras frecuentes.

    Args:
        text: Texto (ej. lo que el OCR leyo en los fotogramas de un video).
        min_words: Minimo de palabras para animarse a decidir.

    Returns:
        Codigo ISO 639-1, o None si hay muy poco texto o ningun idioma se destaca.
    """
    words = _WORD_RE.findall(text.lower())
    if len(words) < min_words:
        return None
    ratios = {lang: sum(word in stopwords for word in words) / len(words) for lang, stopwords in _STOPWORDS.items()}
    best = max(ratios, key=lambda lang: ratios[lang])
    return best if ratios[best] >= _MIN_STOPWORD_RATIO else None
