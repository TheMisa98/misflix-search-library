from __future__ import annotations

import re
from pathlib import Path

# Rutas de instalacion Firefox-based conocidas en este entorno (mismos navegadores que
# `browser_launch._BROWSER_BINARIES`). No se intenta cubrir otros SO: este proyecto es
# de uso personal en Linux.
_PLATFORM_INI_PATHS = [
    "/opt/zen/platform.ini",
    "/opt/firefox/platform.ini",
    "/usr/lib64/zen-browser/platform.ini",
    "/usr/lib/zen-browser/platform.ini",
    "/usr/lib64/firefox/platform.ini",
    "/usr/lib/firefox/platform.ini",
]

_MILESTONE_RE = re.compile(r"^Milestone=(\d+)", re.MULTILINE)


def detect_firefox_major_version() -> str | None:
    """Lee la version mayor de Gecko/Firefox realmente instalada.

    Zen/Firefox se auto-actualizan (rolling release) y Cloudflare ata la cookie
    `cf_clearance` al User-Agent exacto que resolvio el desafio, asi que hardcodear
    una version en el User-Agent que arma `infra/cloudflare.py` queda desactualizada
    apenas el navegador real avanza (verificado en vivo, ver `docs/DECISIONS.md`).

    Returns:
        La version mayor (ej. `"153"`), o `None` si no se encontro `platform.ini` en
        ninguna de las rutas de instalacion conocidas.
    """
    for path in _PLATFORM_INI_PATHS:
        ini_path = Path(path)
        if not ini_path.exists():
            continue
        match = _MILESTONE_RE.search(ini_path.read_text())
        if match:
            return match.group(1)
    return None
