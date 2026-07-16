from __future__ import annotations

import re
from pathlib import Path


def sanitize_filename(name: str) -> str:
    """Quita caracteres invalidos para nombres de archivo."""
    return re.sub(r'[<>:"/\\|?*]', "_", name).strip()


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path
