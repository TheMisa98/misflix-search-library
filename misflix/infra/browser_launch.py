from __future__ import annotations

import shutil
import subprocess
import webbrowser

_BROWSER_BINARIES = ["zen-browser", "zen"]


def open_in_browser(url: str) -> None:
    """Abre `url` en el navegador real del usuario.

    Usa Zen/Firefox si estan disponibles, nunca uno controlado por
    automatizacion: Cloudflare distingue ambos casos.

    Args:
        url: Url a abrir.
    """
    for candidate in _BROWSER_BINARIES:
        binary = shutil.which(candidate)
        if binary:
            subprocess.Popen([binary, url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return
    webbrowser.open(url)
