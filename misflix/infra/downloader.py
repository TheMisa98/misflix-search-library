from __future__ import annotations

from pathlib import Path
from typing import Callable

import httpx

ProgressCallback = Callable[[int, int], None]


class HttpxDownloader:
    """Descarga archivos grandes en streaming, reportando progreso."""

    def download(self, url: str, dest_path: Path, on_progress: ProgressCallback | None = None) -> None:
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
