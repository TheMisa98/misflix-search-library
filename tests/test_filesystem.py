from __future__ import annotations

from misflix.infra.filesystem import ensure_dir, sanitize_filename


def test_sanitize_filename_replaces_invalid_characters():
    assert sanitize_filename('a<b>c:d"e/f\\g|h?i*j') == "a_b_c_d_e_f_g_h_i_j"


def test_sanitize_filename_strips_surrounding_whitespace():
    assert sanitize_filename("  Movie Title  ") == "Movie Title"


def test_sanitize_filename_leaves_valid_names_untouched():
    assert sanitize_filename("Movie Title (2024)") == "Movie Title (2024)"


def test_ensure_dir_creates_nested_directories(tmp_path):
    target = tmp_path / "a" / "b" / "c"

    result = ensure_dir(target)

    assert result == target
    assert target.is_dir()


def test_ensure_dir_is_idempotent(tmp_path):
    target = tmp_path / "existing"
    target.mkdir()

    result = ensure_dir(target)

    assert result == target
