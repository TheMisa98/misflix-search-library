"""Identificacion del idioma hablado con la libreria de whisper.cpp, via `ctypes`.

Fedora empaqueta whisper.cpp solo como libreria (`libwhisper.so`, paquete
`whisper-cpp`), sin el programa de linea de comandos — verificado en vivo.
Para identificar el idioma alcanzan cinco funciones de su API C, asi que se
llaman directo con `ctypes` (stdlib) en vez de compilar whisper.cpp a mano.

Este modulo se corre **en un proceso aparte** (`python -m
misflix.infra.whisper_lid MODELO FRAGMENTO.wav ...`, ver
`spanish_detection.py`): es codigo nativo de terceros y, verificado en vivo,
el backend HIP del paquete tira SIGFPE en la RX 5700 XT (gfx1010, sin soporte
oficial de ROCm) y con `HSA_OVERRIDE_GFX_VERSION` se cuelga. Un proceso
aparte, con la GPU oculta (corre en CPU), evita que eso tumbe al daemon.

Imprime una linea JSON por fragmento: `{"language": "es", "probability": 0.97}`.
"""

from __future__ import annotations

import array
import ctypes
import ctypes.util
import glob
import json
import os
import sys
import wave
from pathlib import Path

_LIBRARY_GLOBS = ("/usr/lib64/libwhisper.so*", "/usr/lib/libwhisper.so*", "/usr/lib/*/libwhisper.so*")


def find_library() -> str | None:
    """Ruta de `libwhisper.so`, si esta instalada.

    Returns:
        La ruta, o None.
    """
    found = ctypes.util.find_library("whisper")
    if found:
        return found
    candidates = sorted(path for pattern in _LIBRARY_GLOBS for path in glob.glob(pattern))
    return candidates[-1] if candidates else None


def detect(model: Path, clips: list[Path], threads: int) -> list[tuple[str, float]]:
    """Identifica el idioma de cada fragmento .wav (mono, 16kHz, PCM 16 bits).

    Args:
        model: Modelo ggml de Whisper.
        clips: Fragmentos de audio.
        threads: Hilos de CPU a usar.

    Returns:
        `(idioma, probabilidad)` por fragmento, en orden.

    Raises:
        OSError: Si la libreria o el modelo no se pueden cargar.
    """
    path = find_library()
    if path is None:
        raise OSError("libwhisper.so no esta instalada")
    lib = ctypes.CDLL(path)
    lib.whisper_init_from_file.restype = ctypes.c_void_p
    lib.whisper_init_from_file.argtypes = [ctypes.c_char_p]
    lib.whisper_pcm_to_mel.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_float), ctypes.c_int, ctypes.c_int]
    lib.whisper_lang_auto_detect.argtypes = [
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.POINTER(ctypes.c_float),
    ]
    lib.whisper_lang_max_id.restype = ctypes.c_int
    lib.whisper_lang_str.restype = ctypes.c_char_p
    lib.whisper_lang_str.argtypes = [ctypes.c_int]
    lib.whisper_free.argtypes = [ctypes.c_void_p]

    context = lib.whisper_init_from_file(str(model).encode())
    if not context:
        raise OSError(f"no se pudo cargar el modelo {model}")
    try:
        languages = lib.whisper_lang_max_id() + 1
        results = []
        for clip in clips:
            with wave.open(str(clip)) as audio:
                pcm = array.array("h", audio.readframes(audio.getnframes()))
            samples = (ctypes.c_float * len(pcm))(*(value / 32768.0 for value in pcm))
            if lib.whisper_pcm_to_mel(context, samples, len(pcm), threads) != 0:
                continue
            probabilities = (ctypes.c_float * languages)()
            best = lib.whisper_lang_auto_detect(context, 0, threads, probabilities)
            if best >= 0:
                results.append((lib.whisper_lang_str(best).decode(), float(probabilities[best])))
        return results
    finally:
        lib.whisper_free(context)


def main(argv: list[str]) -> int:
    """Punto de entrada del proceso aparte: `MODELO FRAGMENTO...`.

    Args:
        argv: Argumentos (sin el nombre del programa).

    Returns:
        Codigo de salida.
    """
    if len(argv) < 2:
        print("uso: python -m misflix.infra.whisper_lid MODELO FRAGMENTO.wav...", file=sys.stderr)
        return 2
    threads = max(1, min(8, (os.cpu_count() or 2) // 2))
    for language, probability in detect(Path(argv[0]), [Path(arg) for arg in argv[1:]], threads):
        print(json.dumps({"language": language, "probability": round(probability, 4)}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
