"""Escuchar el audio y leer la imagen para saber si un video ya esta en español.

Ver `core/subtitles/detection.py` para el por que. Aca se corren las
herramientas del sistema:

- **Whisper** (`libwhisper.so` del paquete `whisper-cpp` de Fedora, via
  `whisper_lid.py` en un proceso aparte) identifica el idioma hablado de las
  pistas de audio **sin etiqueta de idioma** — si la pista dice `eng`/`jpn`
  se le cree. Verificado en vivo con The Aviator (unica pista sin idioma):
  español al 93-98% en los tres fragmentos; House (`und` "Surround"):
  ingles al 95-99.8%. Corre en CPU (~10s por fragmento, la libreria de
  Fedora no usa AVX2): por eso empieza con 2 fragmentos y agrega de a uno
  (hasta 5) solo si todavia no hay decision.
- **Tesseract** (con el paquete de idioma español) lee la franja inferior
  central de hasta 20 fotogramas buscando subtitulos quemados. Verificado en
  vivo con Kimetsu no Yaiba S03E01: 23 palabras confiables, 26% de palabras
  frecuentes del español (el umbral es 12%); House S01E01: 0 palabras.

Los fragmentos y fotogramas se buscan con `-ss` antes de `-i`, asi que no
se lee el archivo entero; los fotogramas se decodifican con `-hwaccel auto`
(la GPU si el driver lo soporta, si no la CPU). Si alguna herramienta no
esta instalada, esa deteccion no se hace (`tools` lo informa) — nunca corta
el proceso.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import httpx

from misflix.core.subtitles.detection import (
    LanguageGuess,
    burned_subtitle_language,
    spoken_language,
)
from misflix.core.subtitles.media import MediaInfo, StreamInfo, is_spanish
from misflix.core.subtitles.text import detect_text_language
from misflix.infra import whisper_lid

# Modelo multilingue "base" (~148MB): identifica bien el idioma; "tiny" se
# confunde mas con musica de fondo.
WHISPER_MODEL_URL = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-base.bin"
_WHISPER_MODEL_MIN_BYTES = 100 * 1024 * 1024
# Se empieza por 2 fragmentos y se agregan de a uno solo si no hay decision
# (en peliculas con tramos largos sin dialogo, ver `spoken_language`).
_CLIP_POSITIONS = (0.35, 0.65, 0.5, 0.2, 0.8)
_FIRST_CLIPS = 2
_CLIP_SECONDS = 25
_MAX_AUDIO_TRACKS = 4
# La GPU AMD de la maquina (RX 5700 XT, gfx1010) no esta soportada por ROCm:
# el backend HIP tira SIGFPE o se cuelga (verificado en vivo). Se oculta.
_CPU_ONLY_ENV = {"ROCR_VISIBLE_DEVICES": "-1", "HIP_VISIBLE_DEVICES": "-1", "CUDA_VISIBLE_DEVICES": "-1"}
_FRAME_POSITIONS = tuple(0.06 + 0.88 * i / 19 for i in range(20))
_MIN_OCR_CONFIDENCE = 60.0
# Con esto ya se puede decidir; no hace falta leer el resto de los fotogramas.
_ENOUGH_OCR_WORDS = 30


@dataclass(frozen=True)
class SpanishDetection:
    """Resultado de escuchar/leer un video.

    Attributes:
        audio_languages: Idioma hablado detectado por pista de audio sin
            etiqueta (indice de pista -> idioma, o None si no hubo acuerdo).
        burned_language: Idioma de los subtitulos quemados en la imagen, o
            None si no se leyo texto suficiente.
        tools: Que detecciones se pudieron hacer ("voz", "ocr").
    """

    audio_languages: dict[int, str | None]
    burned_language: str | None
    tools: frozenset[str]

    @property
    def spanish_audio(self) -> bool:
        """Si alguna pista se escucha en español."""
        return "es" in self.audio_languages.values()


class SpanishDetector:
    """Corre Whisper y Tesseract sobre un video."""

    def __init__(self, cache_dir: Path, log: Callable[[str], None] = lambda _: None):
        """Inicializa el detector.

        Args:
            cache_dir: Donde guardar el modelo de Whisper.
            log: Donde escribir el progreso.
        """
        self.model_path = cache_dir / "whisper" / "ggml-base.bin"
        self.log = log
        self._tesseract = shutil.which("tesseract")

    @property
    def tools(self) -> frozenset[str]:
        """Detecciones posibles con lo que hay instalado ("voz", "ocr")."""
        found = set()
        if whisper_lid.find_library() is not None:
            found.add("voz")
        if self._tesseract and _has_spanish_ocr(self._tesseract):
            found.add("ocr")
        return frozenset(found)

    def detect(self, video: Path, info: MediaInfo) -> SpanishDetection:
        """Escucha las pistas de audio sin idioma y lee los fotogramas de `video`.

        Args:
            video: Archivo de video.
            info: Sus pistas (ver `infra/media_probe.py`).

        Returns:
            Lo detectado. Una deteccion cuya herramienta no esta instalada
            queda vacia (y no figura en `tools`).
        """
        tools = set(self.tools)
        languages: dict[int, str | None] = {}
        untagged = [stream for stream in info.of_kind("audio") if _needs_listening(stream)][:_MAX_AUDIO_TRACKS]
        if "voz" in tools and untagged and info.duration > 0:
            if self._ensure_model():
                for stream in untagged:
                    languages[stream.index] = self._audio_language(video, stream, info.duration)
            else:
                tools.discard("voz")
        burned = self._burned_language(video, info.duration) if "ocr" in tools and info.duration > 0 else None
        return SpanishDetection(audio_languages=languages, burned_language=burned, tools=frozenset(tools))

    def _audio_language(self, video: Path, stream: StreamInfo, duration: float) -> str | None:
        with tempfile.TemporaryDirectory(prefix="misflix-voz-") as tmp:
            guesses = self._listen(video, stream, duration, _CLIP_POSITIONS[:_FIRST_CLIPS], Path(tmp))
            decided = spoken_language(guesses)
            for position in _CLIP_POSITIONS[_FIRST_CLIPS:]:
                if decided is not None:
                    break
                guesses += self._listen(video, stream, duration, (position,), Path(tmp))
                decided = spoken_language(guesses)
        return decided

    def _listen(
        self, video: Path, stream: StreamInfo, duration: float, positions: tuple[float, ...], tmp: Path
    ) -> list[LanguageGuess]:
        clips = []
        for position in positions:
            clip = tmp / f"fragmento-{position:.2f}.wav"
            if _run(clip_command(video, stream, duration * position, clip)) is not None and clip.exists():
                clips.append(clip)
        if not clips:
            return []
        output = _run(
            [sys.executable, "-m", "misflix.infra.whisper_lid", str(self.model_path), *map(str, clips)],
            env=_CPU_ONLY_ENV,
            timeout=900,
        )
        return parse_lid_output(output or "")

    def _burned_language(self, video: Path, duration: float) -> str | None:
        assert self._tesseract is not None
        texts: list[str] = []
        with tempfile.TemporaryDirectory(prefix="misflix-ocr-") as tmp:
            frame = Path(tmp) / "fotograma.png"
            for position in _FRAME_POSITIONS:
                frame.unlink(missing_ok=True)
                if _run(frame_command(video, duration * position, frame)) is None or not frame.exists():
                    continue
                tsv = _run([self._tesseract, str(frame), "stdout", "-l", "spa", "--psm", "6", "tsv"]) or ""
                texts.append(confident_words(tsv))
                if detect_text_language(" ".join(texts), min_words=_ENOUGH_OCR_WORDS) is not None:
                    break
        return burned_subtitle_language(texts)

    def _ensure_model(self) -> bool:
        if self.model_path.exists() and self.model_path.stat().st_size >= _WHISPER_MODEL_MIN_BYTES:
            return True
        self.log(f"  bajando el modelo de Whisper (~148MB) a {self.model_path}...")
        self.model_path.parent.mkdir(parents=True, exist_ok=True)
        partial = self.model_path.with_suffix(".parcial")
        try:
            with httpx.stream("GET", WHISPER_MODEL_URL, follow_redirects=True, timeout=60) as response:
                response.raise_for_status()
                with partial.open("wb") as out:
                    for chunk in response.iter_bytes(1024 * 1024):
                        out.write(chunk)
        except httpx.HTTPError as exc:
            self.log(f"  no se pudo bajar el modelo de Whisper: {exc}")
            partial.unlink(missing_ok=True)
            return False
        if partial.stat().st_size < _WHISPER_MODEL_MIN_BYTES:
            partial.unlink(missing_ok=True)
            return False
        partial.replace(self.model_path)
        return True


def parse_lid_output(output: str) -> list[LanguageGuess]:
    """Lee las lineas JSON que imprime `whisper_lid.py`.

    Args:
        output: Salida del proceso.

    Returns:
        Un `LanguageGuess` por fragmento analizado.
    """
    guesses = []
    for line in output.splitlines():
        try:
            data = json.loads(line)
            guesses.append(LanguageGuess(language=str(data["language"]), probability=float(data["probability"])))
        except ValueError, KeyError, TypeError:
            continue
    return guesses


def confident_words(tsv: str, min_confidence: float = _MIN_OCR_CONFIDENCE) -> str:
    """Se queda con las palabras que Tesseract leyo con confianza (salida `tsv`).

    Sin este filtro, el ruido del fondo (en el anime, zonas claras del
    dibujo) aporta "palabras" sueltas que diluyen la proporcion de palabras
    reales y la deteccion pasaba raspando (verificado en vivo).

    Args:
        tsv: Salida de `tesseract ... tsv`.
        min_confidence: Confianza minima (0-100).

    Returns:
        Las palabras confiables, separadas por espacios.
    """
    words = []
    for line in tsv.splitlines()[1:]:
        columns = line.split("\t")
        if len(columns) != 12 or not columns[11].strip():
            continue
        try:
            if float(columns[10]) >= min_confidence:
                words.append(columns[11].strip())
        except ValueError:
            continue
    return " ".join(words)


def clip_command(video: Path, stream: StreamInfo, start: float, out: Path) -> list[str]:
    """Comando de ffmpeg que saca un fragmento de audio mono 16kHz (lo que espera Whisper).

    Args:
        video: Archivo de video.
        stream: Pista de audio.
        start: Segundo de inicio.
        out: Archivo .wav de salida.

    Returns:
        El comando.
    """
    return [
        "ffmpeg", "-v", "error", "-nostdin", "-y", "-ss", f"{start:.2f}", "-i", str(video),
        "-map", f"0:{stream.index}", "-t", str(_CLIP_SECONDS), "-vn", "-ac", "1", "-ar", "16000",
        "-c:a", "pcm_s16le", str(out),
    ]  # fmt: skip


def frame_command(video: Path, at: float, out: Path) -> list[str]:
    """Comando de ffmpeg que saca la franja inferior central de un fotograma, lista para OCR.

    Se queda con el 30% de abajo y el 80% central del ancho (donde van los
    subtitulos), lo agranda y lo binariza: el texto claro (los subtitulos son
    blancos o amarillos con borde oscuro) queda negro sobre blanco, que es lo
    que mejor lee Tesseract.

    Args:
        video: Archivo de video.
        at: Segundo del fotograma.
        out: Imagen de salida.

    Returns:
        El comando.
    """
    return [
        "ffmpeg", "-v", "error", "-nostdin", "-y", "-hwaccel", "auto", "-ss", f"{at:.2f}", "-i", str(video),
        "-frames:v", "1",
        "-vf", "crop=iw*0.8:ih*0.30:iw*0.1:ih*0.70,scale=1600:-2,format=gray,lut=c0='if(gt(val,185),0,255)'",
        str(out),
    ]  # fmt: skip


def _needs_listening(stream: StreamInfo) -> bool:
    """Una pista se escucha solo si nada dice en que idioma esta (ni el tag ni el titulo)."""
    if stream.language is not None or is_spanish(stream):
        return False
    title = (stream.title or "").lower()
    return not any(word in title for word in ("eng", "ingl", "jap", "jpn", "original", "fre", "fra", "ita", "ger"))


def _has_spanish_ocr(tesseract: str) -> bool:
    output = _run([tesseract, "--list-langs"]) or ""
    return "spa" in output.split()


def _run(command: list[str], env: dict[str, str] | None = None, timeout: float = 300) -> str | None:
    """Corre un comando con prioridad baja; devuelve stdout+stderr, o None si fallo."""
    prefix = (["nice", "-n", "19"] if shutil.which("nice") else []) + (
        ["ionice", "-c", "3"] if shutil.which("ionice") else []
    )
    try:
        result = subprocess.run(
            prefix + command,
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            timeout=timeout,
            env={**os.environ, **env} if env else None,
        )
    except OSError, subprocess.SubprocessError:
        return None
    if result.returncode != 0:
        return None
    return (result.stdout or "") + (result.stderr or "")
