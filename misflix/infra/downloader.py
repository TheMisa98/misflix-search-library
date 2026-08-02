from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import httpx

ProgressCallback = Callable[[int, int], None]

# httpx sin timeout explicito usa 5s para conectar/leer/escribir/pool, demasiado
# agresivo para un stream de varios GB por Mediafire: alcanza con que un chunk
# tarde mas de 5s en llegar (throttling, velocidad fluctuante) para que la
# descarga se corte con un timeout que no tiene nada que ver con un link caido.
# Verificado en vivo: timeouts repetidos a mitad de una descarga de 2GB con
# velocidad yendo de 600kB/s a 4MB/s.
_TIMEOUT = httpx.Timeout(connect=15.0, read=60.0, write=60.0, pool=15.0)


class DownloadError(RuntimeError):
    """Fallo al descargar un archivo: link caido, 404, conexion cortada, etc."""


class HttpxDownloader:
    """Descarga archivos grandes en streaming, reportando progreso."""

    def download(self, url: str, dest_path: Path, on_progress: ProgressCallback | None = None) -> None:
        """Descarga `url` a `dest_path`, en streaming.

        Si `dest_path` ya existe (de un intento anterior cortado a mitad de
        camino, tipico de un timeout en una descarga de varios GB), retoma
        la descarga desde ahi con un header `Range` en vez de volver a bajar
        todo desde cero — asi un reintento no vuelve a pagar en tiempo (ni en
        riesgo de otro timeout) los bytes que ya habian llegado bien. Si el
        servidor no soporta rangos e ignora el header (responde 200 en vez de
        206), se cae de vuelta a descargar todo desde cero.

        Args:
            url: Link directo al archivo.
            dest_path: Ruta destino en disco.
            on_progress: Callback `(bytes_descargados, bytes_totales)`
                invocado a medida que llegan datos. `bytes_totales` puede
                ser 0 si el servidor no informo `Content-Length`.

        Raises:
            DownloadError: Si la descarga falla (link caido, conexion
                cortada, etc.). El archivo parcial (si lo hay) se deja en
                disco en vez de borrarse, para que un reintento lo pueda
                retomar con `Range`.
        """
        resume_from = dest_path.stat().st_size if dest_path.exists() else 0
        headers = {"Range": f"bytes={resume_from}-"} if resume_from else {}

        try:
            with httpx.stream("GET", url, follow_redirects=True, timeout=_TIMEOUT, headers=headers) as response:
                if resume_from and response.status_code == 416:
                    # El servidor dice que no hay nada mas alla de lo ya
                    # bajado: el archivo parcial ya estaba completo.
                    if on_progress:
                        on_progress(resume_from, resume_from)
                    return

                resumed = bool(resume_from) and response.status_code == 206
                response.raise_for_status()
                downloaded = resume_from if resumed else 0
                total = int(response.headers.get("Content-Length", 0)) + downloaded

                with open(dest_path, "ab" if resumed else "wb") as f:
                    for chunk in response.iter_bytes():
                        f.write(chunk)
                        downloaded += len(chunk)
                        if on_progress:
                            on_progress(downloaded, total)
        except httpx.HTTPError as exc:
            raise DownloadError(f"No se pudo descargar {url}: {exc}") from exc
