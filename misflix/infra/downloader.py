from __future__ import annotations

from pathlib import Path
from typing import Callable

import httpx

ProgressCallback = Callable[[int, int], None]


class DownloadError(RuntimeError):
    """Fallo al descargar un archivo: link caido, 404, conexion cortada, etc."""


class HttpxDownloader:
    """Descarga archivos grandes en streaming, reportando progreso."""

    def download(self, url: str, dest_path: Path, on_progress: ProgressCallback | None = None) -> None:
        """Descarga `url` a `dest_path`, en streaming.

        Args:
            url: Link directo al archivo.
            dest_path: Ruta destino en disco.
            on_progress: Callback `(bytes_descargados, bytes_totales)`
                invocado a medida que llegan datos. `bytes_totales` puede
                ser 0 si el servidor no informo `Content-Length`.

        Raises:
            DownloadError: Si la descarga falla (link caido, conexion
                cortada, etc.). El archivo parcial se borra antes de
                relanzar, para que un `.rar` cortado a mitad de camino no
                quede tirado con la extension intacta.
        """
        try:
            with httpx.stream("GET", url, follow_redirects=True) as response:
                response.raise_for_status()
                total = int(response.headers.get("Content-Length", 0))
                downloaded = 0

                with open(dest_path, "wb") as f:
                    for chunk in response.iter_bytes():
                        f.write(chunk)
                        downloaded += len(chunk)
                        if on_progress:
                            on_progress(downloaded, total)
        except httpx.HTTPError as exc:
            # Sin esto, un link caido a mitad de descarga deja un archivo parcial
            # con la misma extension (.rar, etc.) que despues `extract_rar` intenta
            # tratar como si fuera un volumen mas, con resultados confusos.
            dest_path.unlink(missing_ok=True)
            raise DownloadError(f"No se pudo descargar {url}: {exc}") from exc
