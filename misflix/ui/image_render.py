from __future__ import annotations

import shutil
import subprocess


class CoverRenderer:
    """Renderiza portadas en la terminal usando `kitten icat` (protocolo grafico de Kitty)."""

    def render_url(self, cover_url: str) -> None:
        if shutil.which("kitten") is None:
            raise RuntimeError(
                "No se encontro el binario 'kitten'. CoverRenderer requiere una terminal Kitty."
            )
        subprocess.run(["kitten", "icat", cover_url], check=True)
