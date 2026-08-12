from __future__ import annotations

from misflix.infra.browser_version import detect_firefox_major_version


def test_detect_firefox_major_version_reads_milestone_from_platform_ini(tmp_path, monkeypatch):
    platform_ini = tmp_path / "platform.ini"
    platform_ini.write_text("[Build]\nBuildID=20260729085236\nMilestone=153.0.1\n")
    monkeypatch.setattr("misflix.infra.browser_version._PLATFORM_INI_PATHS", [str(platform_ini)])

    assert detect_firefox_major_version() == "153"


def test_detect_firefox_major_version_returns_none_when_no_install_found(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "misflix.infra.browser_version._PLATFORM_INI_PATHS",
        [str(tmp_path / "does-not-exist" / "platform.ini")],
    )

    assert detect_firefox_major_version() is None


def test_detect_firefox_major_version_skips_missing_paths_before_a_valid_one(tmp_path, monkeypatch):
    platform_ini = tmp_path / "platform.ini"
    platform_ini.write_text("[Build]\nMilestone=144.0\n")
    monkeypatch.setattr(
        "misflix.infra.browser_version._PLATFORM_INI_PATHS",
        [str(tmp_path / "missing" / "platform.ini"), str(platform_ini)],
    )

    assert detect_firefox_major_version() == "144"
