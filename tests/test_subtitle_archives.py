from __future__ import annotations

import io
import subprocess
import zipfile
from pathlib import Path

import pytest

from misflix.infra import archives
from misflix.infra.archives import ExtractionError, extract_subtitle_archive


def make_zip(members: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    return buffer.getvalue()


def test_extracts_subtitles_from_a_zip_ignoring_other_files(tmp_path):
    content = make_zip({"House 1x01 - Pilot.srt": b"1\n", "leeme.txt": b"hola", "carpeta/": b""})

    extracted = extract_subtitle_archive("x.zip", content, tmp_path / "out")

    assert [p.name for p in extracted] == ["House 1x01 - Pilot.srt"]


def test_zip_members_are_extracted_flat_so_nothing_escapes_the_destination(tmp_path):
    content = make_zip({"../../evil.srt": b"x", "/abs/path.srt": b"y", "sub\\win.srt": b"z"})

    extracted = extract_subtitle_archive("x.zip", content, tmp_path / "out")

    assert sorted(p.name for p in extracted) == ["evil.srt", "path.srt", "win.srt"]
    assert all(p.parent == tmp_path / "out" for p in extracted)
    assert not (tmp_path / "evil.srt").exists()


def test_nested_archives_are_opened(tmp_path):
    inner = make_zip({"S01E02.srt": b"2"})
    content = make_zip({"S01E01.srt": b"1", "resto.zip": inner})

    extracted = extract_subtitle_archive("pack.zip", content, tmp_path / "out")

    assert [p.name for p in extracted] == ["S01E01.srt", "S01E02.srt"]


def test_duplicate_names_get_a_suffix(tmp_path):
    content = make_zip({"a/sub.srt": b"1", "b/sub.srt": b"2"})

    extracted = extract_subtitle_archive("x.zip", content, tmp_path / "out")

    assert sorted(p.name for p in extracted) == ["sub (2).srt", "sub.srt"]


def test_a_plain_srt_is_saved_as_is(tmp_path):
    extracted = extract_subtitle_archive("Movie.srt", b"1\n00:00:01,000 --> 00:00:02,000\nHola\n", tmp_path / "out")

    assert [p.name for p in extracted] == ["Movie.srt"]


def test_unknown_or_broken_content_raises(tmp_path):
    with pytest.raises(ExtractionError):
        extract_subtitle_archive("x.bin", b"\x00\x01garbage", tmp_path / "a")
    with pytest.raises(ExtractionError):
        extract_subtitle_archive("x.zip", b"PK\x03\x04broken", tmp_path / "b")


def test_rar_is_extracted_with_unrar_without_paths(tmp_path, monkeypatch):
    seen: list[list[str]] = []

    def fake_run(command, **kwargs):
        seen.append(command)
        out = Path(command[-1])
        (out / "House.S01E01.srt").write_bytes(b"1\n")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(archives.subprocess, "run", fake_run)

    extracted = extract_subtitle_archive("x.rar", b"Rar!\x1a\x07\x00rest", tmp_path / "out")

    assert [p.name for p in extracted] == ["House.S01E01.srt"]
    assert seen[0][:2] == ["unrar", "e"]  # "e" = sin rutas


def test_rar_errors_raise_extraction_error(tmp_path, monkeypatch):
    monkeypatch.setattr(
        archives.subprocess, "run", lambda command, **kwargs: subprocess.CompletedProcess(command, 3, "", "CRC failed")
    )

    with pytest.raises(ExtractionError, match="CRC failed"):
        extract_subtitle_archive("x.rar", b"Rar!\x1a\x07\x00rest", tmp_path / "out")
