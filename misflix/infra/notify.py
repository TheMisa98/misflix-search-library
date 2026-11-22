from __future__ import annotations

import shutil
import subprocess


def notify(title: str, body: str, urgency: str = "normal") -> None:
    """Muestra una notificacion de escritorio con `notify-send`, si esta disponible.

    El proceso en segundo plano no tiene terminal donde avisar: esta es la
    forma de contarle al usuario que se agregaron subtitulos o que subdivx
    pide verificar Cloudflare. Si `notify-send` no existe o falla (ej. sin
    sesion grafica), se ignora en silencio — nunca debe cortar el proceso.

    Args:
        title: Titulo de la notificacion.
        body: Texto.
        urgency: "low", "normal" o "critical".
    """
    binary = shutil.which("notify-send")
    if binary is None:
        return
    try:
        subprocess.run(
            [binary, "--app-name=misflix", f"--urgency={urgency}", title, body],
            capture_output=True,
            timeout=10,
            stdin=subprocess.DEVNULL,
        )
    except OSError, subprocess.SubprocessError:
        return
