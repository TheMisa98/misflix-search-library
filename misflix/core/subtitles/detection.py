"""Decidir si un video ya esta en español aunque sus metadatos no lo digan.

Dos casos verificados en vivo en la biblioteca:

- **Doblaje sin etiquetar**: The Aviator trae una sola pista de audio sin
  idioma y esta doblada. Se identifica el idioma *hablado* con Whisper
  sobre fragmentos cortos del audio (ver `infra/spanish_detection.py`).
- **Subtitulos quemados en la imagen**: Kimetsu no Yaiba T3 trae el audio
  japones bien etiquetado (`jpn`) pero los subtitulos en español estan
  dibujados en el video. Se leen con OCR en la franja inferior de varios
  fotogramas.

Aca solo se interpreta lo que devuelven esas herramientas.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

from misflix.core.subtitles.text import detect_text_language

_WHISPER_LANGUAGE_RE = re.compile(r"auto-detected language:\s*([a-z]{2,3})\s*\(p\s*=\s*([0-9.]+)\)")
# Un fragmento cuenta si Whisper esta al menos asi de seguro del idioma.
_MIN_PROBABILITY = 0.75
# Palabras minimas leidas por OCR para animarse a decidir el idioma de los
# subtitulos quemados (los fotogramas sin dialogo aportan poco o nada).
_MIN_OCR_WORDS = 20


@dataclass(frozen=True)
class LanguageGuess:
    """Idioma que Whisper detecto en un fragmento de audio.

    Attributes:
        language: Codigo ISO 639-1 ("es", "en", "ja", ...).
        probability: Confianza de Whisper (0-1).
    """

    language: str
    probability: float


def parse_whisper_language(output: str) -> LanguageGuess | None:
    """Lee el idioma detectado de la salida de `whisper-cli --detect-language`.

    Args:
        output: Salida (stdout+stderr) de whisper.cpp.

    Returns:
        El idioma y su probabilidad, o None si la salida no lo trae.
    """
    match = _WHISPER_LANGUAGE_RE.search(output)
    if match is None:
        return None
    return LanguageGuess(language=match.group(1), probability=float(match.group(2)))


def spoken_language(guesses: Sequence[LanguageGuess]) -> str | None:
    """Idioma hablado de una pista, a partir de varios fragmentos.

    Solo cuentan los fragmentos en los que Whisper esta seguro; gana el
    idioma con mas de esos votos, si tiene al menos 2 y no empata con el
    segundo. Verificado en
    vivo con Kill the Jockey (hablada en español, con tramos largos de musica
    y ambiente): en un tramo sin dialogo Whisper "detecto" japones al 88%, y
    en otros ingles al 37-67%; los dos fragmentos con dialogo dieron español
    al 85-90%. Con esta regla gana español; exigiendo mayoria de *todos* los
    fragmentos no se decidia nada.

    Args:
        guesses: Lo detectado en cada fragmento.

    Returns:
        El idioma, o None si todavia no hay votos suficientes.
    """
    confident = [guess.language for guess in guesses if guess.probability >= _MIN_PROBABILITY]
    if not confident:
        return None
    ranking = Counter(confident).most_common(2)
    language, votes = ranking[0]
    runner_up = ranking[1][1] if len(ranking) > 1 else 0
    return language if votes >= 2 and votes > runner_up else None


def burned_subtitle_language(ocr_texts: Sequence[str]) -> str | None:
    """Idioma de los subtitulos quemados en la imagen, a partir del texto leido por OCR.

    Args:
        ocr_texts: Texto leido en la franja inferior de cada fotograma.

    Returns:
        El idioma, o None si no se leyo texto suficiente (no hay subtitulos
        quemados, o el OCR no los pudo leer).
    """
    return detect_text_language("\n".join(ocr_texts), min_words=_MIN_OCR_WORDS)
