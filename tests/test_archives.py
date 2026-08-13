from __future__ import annotations

import subprocess

import pytest

from misflix.infra import archives


def test_extract_rar_returns_false_when_no_rar_files(tmp_path):
    assert archives.extract_rar(tmp_path) is False


def test_extract_rar_invokes_unrar_with_password_and_only_the_first_part(tmp_path, monkeypatch):
    # Pasarle TODOS los volumenes como argumentos separados hace que unrar trate
    # cada uno como el inicio de un archivo distinto, y falle al "empezar" desde
    # part2.rar en adelante. Solo se le pasa el primero; el resto los encadena solo.
    (tmp_path / "movie.part2.rar").write_bytes(b"")
    (tmp_path / "movie.part1.rar").write_bytes(b"")

    calls = []

    def fake_run(cmd, cwd, capture_output, text):
        calls.append((cmd, cwd))
        return subprocess.CompletedProcess(cmd, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(archives.subprocess, "run", fake_run)

    assert archives.extract_rar(tmp_path, password="zonaleros") is True
    (cmd, cwd) = calls[0]
    assert cmd == ["unrar", "x", "-y", "-pzonaleros", "movie.part1.rar"]
    assert cwd == tmp_path


def test_extract_rar_raises_on_unrar_failure(tmp_path, monkeypatch):
    (tmp_path / "movie.rar").write_bytes(b"")

    def fake_run(cmd, cwd, capture_output, text):
        return subprocess.CompletedProcess(cmd, returncode=1, stdout="", stderr="Wrong password")

    monkeypatch.setattr(archives.subprocess, "run", fake_run)

    with pytest.raises(archives.ExtractionError, match="Wrong password"):
        archives.extract_rar(tmp_path)


def test_flatten_video_returns_none_without_any_video_file(tmp_path):
    assert archives.flatten_video(tmp_path, "My Movie") is None


def test_flatten_video_moves_and_renames_a_direct_file(tmp_path):
    (tmp_path / "random_name.mkv").write_bytes(b"x" * 100)

    result = archives.flatten_video(tmp_path, "My Movie")

    assert result == tmp_path / "My Movie.mkv"
    assert result.exists()


def test_flatten_video_moves_file_out_of_a_nested_subfolder_and_cleans_it_up(tmp_path):
    nested = tmp_path / "Movie.Extracted.Folder"
    nested.mkdir()
    (nested / "movie.mp4").write_bytes(b"x" * 100)

    result = archives.flatten_video(tmp_path, "My Movie")

    assert result == tmp_path / "My Movie.mp4"
    assert result.exists()
    assert not nested.exists()


def test_flatten_video_picks_the_largest_candidate(tmp_path):
    (tmp_path / "sample.mkv").write_bytes(b"x" * 10)
    (tmp_path / "movie.mkv").write_bytes(b"x" * 1000)

    result = archives.flatten_video(tmp_path, "My Movie")

    assert result == tmp_path / "My Movie.mkv"
    assert result.read_bytes() == b"x" * 1000


def test_flatten_all_videos_moves_every_episode_keeping_original_names(tmp_path):
    season_folder = tmp_path / "Rick y Morty S09"
    season_folder.mkdir()
    (season_folder / "Rick.y.Morty.S09E01.mkv").write_bytes(b"x")
    (season_folder / "Rick.y.Morty.S09E02.mkv").write_bytes(b"x")

    result = archives.flatten_all_videos(tmp_path)

    assert sorted(p.name for p in result) == ["Rick.y.Morty.S09E01.mkv", "Rick.y.Morty.S09E02.mkv"]
    assert all(p.parent == tmp_path for p in result)
    assert not season_folder.exists()


def test_flatten_all_videos_avoids_overwriting_name_collisions(tmp_path):
    (tmp_path / "episode.mkv").write_bytes(b"already here")
    nested = tmp_path / "extracted"
    nested.mkdir()
    (nested / "episode.mkv").write_bytes(b"from the pack")

    result = archives.flatten_all_videos(tmp_path)

    assert len(result) == 2
    assert (tmp_path / "episode.mkv").read_bytes() == b"already here"
    assert any(p.read_bytes() == b"from the pack" for p in tmp_path.glob("*.mkv"))
    assert len(list(tmp_path.glob("*.mkv"))) == 2


def test_delete_rar_parts_removes_every_rar_in_the_folder(tmp_path):
    (tmp_path / "movie.part1.rar").write_bytes(b"")
    (tmp_path / "movie.part2.rar").write_bytes(b"")
    (tmp_path / "My Movie.mkv").write_bytes(b"x")

    archives.delete_rar_parts(tmp_path)

    assert list(tmp_path.glob("*.rar")) == []
    assert (tmp_path / "My Movie.mkv").exists()


def test_find_existing_video_returns_the_path_when_present(tmp_path):
    (tmp_path / "Breaking Bad - S05E01.mkv").write_bytes(b"x")

    result = archives.find_existing_video(tmp_path, "Breaking Bad - S05E01")

    assert result == tmp_path / "Breaking Bad - S05E01.mkv"


def test_find_existing_video_returns_none_when_missing(tmp_path):
    assert archives.find_existing_video(tmp_path, "Breaking Bad - S05E01") is None


def test_find_existing_video_ignores_a_partial_rar_still_downloading(tmp_path):
    (tmp_path / "Breaking Bad - S05E01.rar").write_bytes(b"partial")

    assert archives.find_existing_video(tmp_path, "Breaking Bad - S05E01") is None


def test_find_existing_video_does_not_look_inside_subfolders(tmp_path):
    nested = tmp_path / "extracted"
    nested.mkdir()
    (nested / "Breaking Bad - S05E01.mkv").write_bytes(b"x")

    assert archives.find_existing_video(tmp_path, "Breaking Bad - S05E01") is None
