from __future__ import annotations

import os
from pathlib import Path

import pytest

from misflix.config import settings as settings_module
from misflix.config.settings import get_settings, load_config_files, user_config_file

NAMES = ("MISFLIX_MOVIES_DIR", "MISFLIX_BOOKS_DIR", "MISFLIX_SERIES_DIR")


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


@pytest.fixture
def clean_env(monkeypatch):
    """Sin las variables de misflix; lo que carguen los tests se deshace al terminar."""
    for name in NAMES:
        monkeypatch.setenv(name, "")  # registra el valor original para restaurarlo
        monkeypatch.delenv(name)


def test_the_first_file_that_defines_a_variable_wins_and_the_environment_wins_over_all(
    tmp_path, clean_env, monkeypatch
):
    repo = tmp_path / "repo.env"
    repo.write_text("MISFLIX_MOVIES_DIR=/repo/Peliculas\n")
    user = tmp_path / "user.env"
    user.write_text(
        "MISFLIX_MOVIES_DIR=/user/Peliculas\nMISFLIX_SERIES_DIR=/user/Series\nMISFLIX_BOOKS_DIR=/user/Libros\n"
    )
    monkeypatch.setenv("MISFLIX_BOOKS_DIR", "/systemd/Libros")  # ej. la unidad del servicio

    load_config_files([repo, tmp_path / "no-existe.env", user])

    assert os.environ["MISFLIX_MOVIES_DIR"] == "/repo/Peliculas"
    assert os.environ["MISFLIX_SERIES_DIR"] == "/user/Series"
    assert os.environ["MISFLIX_BOOKS_DIR"] == "/systemd/Libros"


def test_the_user_file_follows_xdg_config_home(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    assert user_config_file() == tmp_path / "misflix" / ".env"


def test_the_installed_command_reads_the_user_file(tmp_path, clean_env, monkeypatch):
    """El `misflix` instalado no tiene el `.env` del repo al lado: lee ~/.config/misflix/.env."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setattr(settings_module, "_REPO_ENV", tmp_path / "site-packages" / ".env")
    user_config_file().parent.mkdir()
    user_config_file().write_text("MISFLIX_SERIES_DIR=/mnt/misflix/Series\n")
    get_settings.cache_clear()

    load_config_files(settings_module.existing_config_files())
    settings = get_settings()

    assert settings.series_dir == Path("/mnt/misflix/Series")
    assert settings.config_files == (user_config_file(),)
    get_settings.cache_clear()
