from __future__ import annotations

from pathlib import Path

from misflix.config.settings import get_settings


def test_get_settings_defaults_to_descargas_when_no_env_var_set(monkeypatch):
    monkeypatch.delenv("MISFLIX_MOVIES_DIR", raising=False)
    monkeypatch.delenv("MISFLIX_BOOKS_DIR", raising=False)
    monkeypatch.delenv("MISFLIX_SERIES_DIR", raising=False)
    get_settings.cache_clear()

    settings = get_settings()

    assert settings.movies_dir == Path("~/Descargas/Peliculas").expanduser()
    assert settings.books_dir == Path("~/Descargas/Libros").expanduser()
    assert settings.series_dir == Path("~/Descargas/Series").expanduser()
    get_settings.cache_clear()


def test_get_settings_reads_overrides_from_env(monkeypatch):
    monkeypatch.setenv("MISFLIX_MOVIES_DIR", "/mnt/misflix/Peliculas")
    monkeypatch.setenv("MISFLIX_BOOKS_DIR", "/mnt/misflix/Libros")
    monkeypatch.setenv("MISFLIX_SERIES_DIR", "/mnt/misflix/Series")
    get_settings.cache_clear()

    settings = get_settings()

    assert settings.movies_dir == Path("/mnt/misflix/Peliculas")
    assert settings.books_dir == Path("/mnt/misflix/Libros")
    assert settings.series_dir == Path("/mnt/misflix/Series")
    get_settings.cache_clear()
