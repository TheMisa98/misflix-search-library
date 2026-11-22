"""Logica pura de subtitulos: formato .srt, limpieza, deteccion de voz, sincronia y emparejamiento.

Nada de este paquete toca la red ni el disco: recibe texto/numeros y devuelve
texto/numeros, asi se prueba entero sin fixtures de video. Lo que si depende de
ffmpeg/subdivx/el filesystem vive en `infra/` y `providers/`, y se orquesta desde
`core/services/subtitle_service.py`.
"""
