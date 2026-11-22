"""Escuchar el audio y leer la imagen para saber si un video ya esta en español.

Ver `core/subtitles/detection.py` para el por que. Aca se corren las
herramientas del sistema:

- **Whisper** (`whisper-cpp`, paquete de Fedora) identifica el idioma
  hablado en un par de fragmentos cortos de cada pista de audio. Los
  fragmentos se sacan con `ffmpeg -ss` antes de `-i` (busqueda directa), asi
  que no se lee el archivo entero.
- **Tesseract** (con el paquete de idioma español) lee la franja inferior de
  varios fotogramas, decodificados con aceleracion por hardware cuando la
  hay (`-hwaccel auto`: la GPU si el driver la soporta, si no la CPU).

Si alguna herramienta no esta instalada, esa deteccion simplemente no se
hace (`available` lo informa) — nunca corta el proceso.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import httpx

from misflix.core.subtitles.detection import (
    LanguageGuess,
    burned_subtitle_language,
    parse_whisper_language,
    spoken_language,
)
from misflix.core.subtitles.media import MediaInfo, StreamInfo
from misflix.core.subtitles.text import detect_text_language

_WHISPER_BINARIES = ("whisper-cli", "whisper-cpp", "whisper.cpp")
# Modelo multilingue "base" (~148MB): identifica el idioma bien y rapido;
# "tiny" se confunde mas con audio con musica de fondo.
WHISPER_MODEL_URL = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-base.bin"
_WHISPER_MODEL_MIN_BYTES = 100 * 1024 * 1024
_CLIP_POSITIONS = (0.3, 0.5, 0.7)
_CLIP_SECONDS = 25
_MAX_AUDIO_TRACKS = 4
_FRAME_POSITIONS = tuple(0.08 + 0.84 * i / 13 for i in range(14))
# Con esto ya alcanza para decidir; no hace falta leer el resto de los fotogramas.
_ENOUGH_OCR_WORDS = 60


@dataclass(frozen=True)
class SpanishDetection:
    """Resultado de escuchar/leer un video.

    Attributes:
        audio_languages: Idioma hablado detectado por pista de audio (indice
            de pista -> idioma, o None si no hubo acuerdo).
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
        self._whisper = next((path for name in _WHISPER_BINARIES if (path := shutil.which(name))), None)
        self._tesseract = shutil.which("tesseract")

    @property
    def tools(self) -> frozenset[str]:
        """Detecciones posibles con lo que hay instalado ("voz", "ocr")."""
        found = set()
        if self._whisper:
            found.add("voz")
        if self._tesseract and _has_spanish_ocr(self._tesseract):
            found.add("ocr")
        return frozenset(found)

    def detect(self, video: Path, info: MediaInfo) -> SpanishDetection:
        """Escucha las pistas de audio y lee los fotogramas de `video`.

        Args:
            video: Archivo de video.
            info: Sus pistas (ver `infra/media_probe.py`).

        Returns:
            Lo detectado. Una deteccion cuya herramienta no esta instalada
            queda vacia (y no figura en `tools`).
        """
        tools = self.tools
        languages: dict[int, str | None] = {}
        if "voz" in tools and info.duration > 0 and self._ensure_model():
            for stream in info.of_kind("audio")[:_MAX_AUDIO_TRACKS]:
                languages[stream.index] = self._audio_language(video, stream, info.duration)
        burned = self._burned_language(video, info.duration) if "ocr" in tools and info.duration > 0 else None
        return SpanishDetection(audio_languages=languages, burned_language=burned, tools=tools)

    def _audio_language(self, video: Path, stream: StreamInfo, duration: float) -> str | None:
        assert self._whisper is not None
        guesses: list[LanguageGuess] = []
        with tempfile.TemporaryDirectory(prefix="misflix-voz-") as tmp:
            clip = Path(tmp) / "fragmento.wav"
            for position in _CLIP_POSITIONS:
                start = max(0.0, duration * position)
                if _run(clip_command(video, stream, start, clip)) is None:
                    continue
                output = _run([self._whisper, "-m", str(self.model_path), "-f", str(clip), "-l", "auto", "-dl"])
                guess = parse_whisper_language(output or "")
                if guess is not None:
                    guesses.append(guess)
        return spoken_language(guesses) if guesses else None

    def _burned_language(self, video: Path, duration: float) -> str | None:
        assert self._tesseract is not None
        texts: list[str] = []
        with tempfile.TemporaryDirectory(prefix="misflix-ocr-") as tmp:
            frame = Path(tmp) / "fotograma.png"
            for position in _FRAME_POSITIONS:
                if _run(frame_command(video, duration * position, frame)) is None or not frame.exists():
                    continue
                text = _run([self._tesseract, str(frame), "stdout", "-l", "spa", "--psm", "6"]) or ""
                texts.append(text)
                if detect_text_language("\n".join(texts), min_words=_ENOUGH_OCR_WORDS) is not None:
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
    """Comando de ffmpeg que saca la franja inferior de un fotograma, lista para OCR.

    Se queda con el 30% de abajo (donde van los subtitulos), lo agranda y lo
    binariza: el texto claro (los subtitulos suelen ser blancos o amarillos
    con borde oscuro) queda negro sobre blanco, que es lo que mejor lee
    Tesseract.

    Args:
        video: Archivo de video.
        at: Segundo del fotograma.
        out: Imagen de salida.

    Returns:
        El comando.
    """
    return [
        "ffmpeg", "-v", "error", "-nostdin", "-y", "-hwaccel", "auto", "-ss", f"{at:.2f}", "-i", str(video),
        "-frames:v", "1", "-vf", "crop=iw:ih*0.30:0:ih*0.70,scale=1600:-2,format=gray,lut=c0='if(gt(val,185),0,255)'",
        str(out),
    ]  # fmt: skip


def _has_spanish_ocr(tesseract: str) -> bool:
    output = _run([tesseract, "--list-langs"]) or ""
    return "spa" in output.split()


def _run(command: list[str]) -> str | None:
    """Corre un comando con prioridad baja; devuelve stdout+stderr, o None si fallo."""
    prefix = (["nice", "-n", "19"] if shutil.which("nice") else []) + (
        ["ionice", "-c", "3"] if shutil.which("ionice") else []
    )
    try:
        result = subprocess.run(prefix + command, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=300)
    except OSError, subprocess.SubprocessError:
        return None
    if result.returncode != 0:
        return None
    return (result.stdout or "") + (result.stderr or "")
