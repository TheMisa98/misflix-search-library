from __future__ import annotations

import io
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from misflix.core.models import DownloadedFile, SubtitleCandidate
from misflix.core.ports import SubtitleProviderError
from misflix.core.services import subtitle_service
from misflix.core.services.subtitle_service import SubtitleService, SubtitleStatus
from misflix.core.subtitles.media import MediaInfo, StreamInfo
from misflix.core.subtitles.srt import Cue, format_srt, parse_srt
from misflix.infra import library
from misflix.infra.cloudflare import CloudflareBlockedError
from misflix.infra.media_probe import ExtractedReference, MediaProbeError
from misflix.infra.spanish_detection import SpanishDetection
from misflix.infra.subtitle_store import SubtitleStore, VideoRecord
from tests.test_subtitles_sync import make_timeline

SPANISH_LINES = [
    "¿Qué estás haciendo aquí?",
    "No lo sé, pero está bien.",
    "Yo quiero que usted se vaya ahora.",
    "Eso no es para mí, señor.",
    "¿Cómo estoy? Muy bien, gracias.",
    "Lo que te digo es que no hay nada.",
]
ENGLISH_LINES = [
    "What are you doing here?",
    "I don't know, but it's fine.",
    "I want you to go right now.",
    "That is not for me, sir.",
]

TIMELINE = make_timeline(seed=1)
ENGLISH = [Cue(c.start, c.end, ENGLISH_LINES[i % len(ENGLISH_LINES)]) for i, c in enumerate(TIMELINE)]
SPANISH = [Cue(c.start, c.end, SPANISH_LINES[i % len(SPANISH_LINES)]) for i, c in enumerate(TIMELINE)]
OTHER_EPISODE = [Cue(c.start, c.end, SPANISH_LINES[i % len(SPANISH_LINES)]) for i, c in enumerate(make_timeline(7))]
DURATION = TIMELINE[-1].end + 30

ENGLISH_INFO = MediaInfo(
    duration=DURATION,
    streams=(
        StreamInfo(1, "audio", "dts", language="eng", channels=6, channel_layout="5.1(side)", default=True),
        StreamInfo(2, "subtitle", "subrip", language="eng"),
    ),
)


def shifted(cues: list[Cue], offset: float) -> list[Cue]:
    return [Cue(c.start + offset, c.end + offset, c.text) for c in cues]


def make_zip(name: str, cues: list[Cue]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(name, format_srt(cues))
    return buffer.getvalue()


class FakeProvider:
    name = "fake"

    def __init__(
        self,
        files: dict[str, tuple[SubtitleCandidate, bytes]] | None = None,
        blocked: bool = False,
        down: bool = False,
        missing: frozenset[str] = frozenset(),
    ):
        self.files = files or {}
        self.blocked = blocked
        self.down = down
        self.missing = missing
        self.searches: list[str] = []
        self.downloads: list[str] = []

    def search(self, query: str) -> list[SubtitleCandidate]:
        if self.blocked:
            raise CloudflareBlockedError("bloqueado")
        if self.down:
            raise SubtitleProviderError("subdivx no respondio")
        self.searches.append(query)
        return [candidate for candidate, _ in self.files.values()]

    def download(self, candidate: SubtitleCandidate) -> DownloadedFile:
        self.downloads.append(candidate.id)
        if candidate.id in self.missing:
            raise SubtitleProviderError("HTTP Error 404")
        return DownloadedFile(filename=f"{candidate.id}.zip", content=self.files[candidate.id][1])


class FakeDetector:
    def __init__(self, audio: str | None = None, burned: str | None = None, tools=frozenset({"voz", "ocr"})):
        self.tools = frozenset(tools)
        self.audio = audio
        self.burned = burned
        self.calls = 0

    def detect(self, video: Path, info: MediaInfo) -> SpanishDetection:
        self.calls += 1
        return SpanishDetection(
            audio_languages={1: self.audio} if "voz" in self.tools else {},
            burned_language=self.burned if "ocr" in self.tools else None,
            tools=self.tools,
        )


def candidate(id_: str, title: str = "House M.D. S01E01", description: str = "", downloads: int = 10):
    return SubtitleCandidate(id=id_, source="fake", title=title, description=description, downloads=downloads)


@pytest.fixture
def lib(tmp_path, monkeypatch):
    """Biblioteca con un episodio de House y ffprobe/ffmpeg reemplazados."""
    monkeypatch.setattr(library, "_MIN_VIDEO_BYTES", 0)
    series = tmp_path / "Series"
    (series / "House").mkdir(parents=True)
    (series / "House" / "tvshow.nfo").write_text(
        "<tvshow><title>Dr. House</title><originaltitle>House</originaltitle></tvshow>"
    )
    video = series / "House" / "Season 1" / "house.s01e01.1080p.bluray.x264-filmhd.mkv"
    video.parent.mkdir()
    video.write_bytes(b"video")

    probes: list[Path] = []
    extractions: list[Path] = []

    def fake_probe(path: Path) -> MediaInfo:
        probes.append(path)
        return ENGLISH_INFO

    def fake_extract(path, subtitle, audio, workdir):
        extractions.append(path)
        return ExtractedReference(subtitle_text=format_srt(ENGLISH) if subtitle else None, levels_db=[])

    monkeypatch.setattr(subtitle_service, "probe", fake_probe)
    monkeypatch.setattr(subtitle_service, "extract_reference", fake_extract)
    monkeypatch.setattr(subtitle_service, "available_audio_decoders", lambda: frozenset({"dts", "ac3", "aac"}))

    class Lib:
        root = tmp_path
        movies = tmp_path / "Peliculas"
        series_dir = series
        episode = video
        probe_calls = probes
        extract_calls = extractions

        @staticmethod
        def service(provider: FakeProvider | None = None, detector: FakeDetector | None = None) -> SubtitleService:
            store = SubtitleStore(tmp_path / "state" / "subs.db")
            return SubtitleService(
                provider or FakeProvider(), store, tmp_path / "cache", tmp_path / "respaldo", detector=detector
            )

        @staticmethod
        def run(service: SubtitleService, **kwargs):
            return service.run(tmp_path / "Peliculas", series, **kwargs)

    return Lib


def read(path: Path) -> list[Cue]:
    return parse_srt(path.read_text(encoding="utf-8"))


def backups(root: Path) -> list[str]:
    return sorted(p.name for p in (root / "respaldo").rglob("*.srt")) if (root / "respaldo").exists() else []


def test_video_with_spanish_audio_is_skipped_without_reading_it(lib, monkeypatch):
    dubbed = MediaInfo(duration=10, streams=(StreamInfo(1, "audio", "aac", title="Latino"),))
    monkeypatch.setattr(subtitle_service, "probe", lambda path: dubbed)
    service = lib.service()

    summary = lib.run(service)

    assert summary.by_status == {SubtitleStatus.SPANISH_AUDIO: 1}
    assert lib.extract_calls == []


def test_existing_subtitle_in_sync_is_left_untouched(lib):
    existing = lib.episode.with_suffix(".srt")
    existing.write_text(format_srt(SPANISH))
    before = existing.read_bytes()

    summary = lib.run(lib.service())

    assert summary.by_status == {SubtitleStatus.IN_SYNC: 1}
    assert existing.read_bytes() == before
    assert backups(lib.root) == []


def test_desynced_existing_subtitle_is_fixed_in_place_with_a_backup(lib):
    existing = lib.episode.with_suffix(".srt")
    existing.write_text(format_srt(shifted(SPANISH, 4.0)))
    service = lib.service()

    summary = lib.run(service)

    assert summary.by_status == {SubtitleStatus.FIXED: 1}
    fixed = read(existing)
    assert max(abs(a.start - b.start) for a, b in zip(fixed, SPANISH, strict=True)) < 0.1
    assert backups(lib.root) == [existing.name]
    assert service.store.recent_actions()[0].action == "resincronizado"


def test_wrong_existing_subtitle_is_backed_up_and_a_verified_one_downloaded(lib):
    wrong = lib.episode.with_name(lib.episode.stem + ".hi.srt")
    wrong.write_text(format_srt(OTHER_EPISODE))
    provider = FakeProvider(
        {
            "other": (candidate("other", "House of Cards S01E01"), make_zip("hoc.srt", SPANISH)),
            "wrong": (candidate("wrong", downloads=99999), make_zip("a.srt", OTHER_EPISODE)),
            "good": (candidate("good", description="para bluray.x264-filmhd"), make_zip("b.srt", shifted(SPANISH, 2))),
        }
    )
    service = lib.service(provider)

    summary = lib.run(service)

    assert summary.by_status == {SubtitleStatus.DOWNLOADED: 1}
    written = lib.episode.with_name(lib.episode.stem + ".es.srt")
    assert max(abs(a.start - b.start) for a, b in zip(read(written), SPANISH, strict=True)) < 0.1
    assert not wrong.exists() and backups(lib.root) == [wrong.name]
    # House of Cards nunca se baja; el del mismo grupo de release se prueba primero
    assert provider.downloads == ["good"]
    record = service.store.get_video(lib.episode)
    assert record is not None and record.subtitle_path == str(written) and record.reference == "subtitulos"


def test_candidates_that_fail_are_recorded_and_not_retried(lib):
    provider = FakeProvider({"wrong": (candidate("wrong"), make_zip("a.srt", OTHER_EPISODE))})
    service = lib.service(provider)

    first = lib.run(service)
    second = lib.run(service, force=True)

    assert first.by_status == {SubtitleStatus.NOT_FOUND: 1}
    assert second.by_status == {SubtitleStatus.NOT_FOUND: 1}
    assert provider.downloads == ["wrong"]
    assert service.store.was_tried(lib.episode, "fake", "wrong")


def test_not_found_is_retried_only_after_the_waiting_period(lib):
    service = lib.service(FakeProvider())

    lib.run(service)

    assert not service.needs_work(_video(lib))
    record = service.store.get_video(lib.episode)
    assert record is not None and record.status == SubtitleStatus.NOT_FOUND
    expired = VideoRecord(**{**record.__dict__, "retry_after": (datetime.now(UTC) - timedelta(1)).isoformat()})
    service.store.put_video(expired)
    assert service.needs_work(_video(lib))


def test_a_new_sidecar_triggers_a_re_evaluation(lib):
    service = lib.service(FakeProvider())
    lib.run(service)
    assert not service.needs_work(_video(lib))

    lib.episode.with_suffix(".srt").write_text(format_srt(SPANISH))

    assert service.needs_work(_video(lib))


def test_blocked_provider_keeps_verifying_and_leaves_the_rest_pending(lib):
    service = lib.service(FakeProvider(blocked=True))

    summary = lib.run(service)

    assert summary.aborted == "bloqueado"
    assert summary.by_status == {SubtitleStatus.PENDING: 1}
    record = service.store.get_video(lib.episode)
    assert record is not None and record.retry_after is not None


def test_reference_extraction_is_cached_between_runs(lib):
    lib.episode.with_suffix(".srt").write_text(format_srt(SPANISH))
    service = lib.service()

    lib.run(service, force=True)
    lib.run(service, force=True)

    assert len(lib.extract_calls) == 1
    assert len(lib.probe_calls) == 1


def test_season_packs_are_downloaded_once(lib):
    second = lib.episode.with_name("house.s01e02.1080p.bluray.x264-filmhd.mkv")
    second.write_bytes(b"video")
    pack = io.BytesIO()
    with zipfile.ZipFile(pack, "w") as archive:
        archive.writestr("House 1x01.srt", format_srt(SPANISH))
        archive.writestr("House 1x02.srt", format_srt(SPANISH))
    provider = FakeProvider({"pack": (candidate("pack", "House M.D. S01E01-11"), pack.getvalue())})
    service = lib.service(provider)

    summary = lib.run(service)

    assert summary.by_status == {SubtitleStatus.DOWNLOADED: 2}
    assert provider.downloads == ["pack"]


def test_sync_file_writes_the_corrected_subtitle(lib, tmp_path):
    loose = tmp_path / "suelto.srt"
    loose.write_text(format_srt(shifted(SPANISH, -3.0)))

    report, written = lib.service().sync_file(lib.episode, loose)

    assert report is not None and written == lib.episode.with_name(lib.episode.stem + ".es.srt")
    assert max(abs(a.start - b.start) for a, b in zip(read(written), SPANISH, strict=True)) < 0.1


def _video(lib) -> library.LibraryVideo:
    return library.scan_library(lib.movies, lib.series_dir)[0]


def test_reference_falls_back_to_the_subtitle_when_the_audio_cannot_be_decoded(lib, monkeypatch):
    calls: list[bool] = []

    def flaky_extract(path, subtitle, audio, workdir):
        calls.append(audio is not None)
        if audio is not None:
            raise MediaProbeError("Decoding requested, but no decoder found for: eac3")
        return ExtractedReference(subtitle_text=format_srt(ENGLISH), levels_db=[])

    monkeypatch.setattr(subtitle_service, "extract_reference", flaky_extract)
    lib.episode.with_suffix(".srt").write_text(format_srt(SPANISH))

    summary = lib.run(lib.service())

    assert calls == [True, False]
    assert summary.by_status == {SubtitleStatus.IN_SYNC: 1}


def test_untagged_dub_detected_by_listening_is_skipped_before_reading_the_whole_video(lib):
    detector = FakeDetector(audio="es")
    service = lib.service(detector=detector)

    summary = lib.run(service)

    assert summary.by_status == {SubtitleStatus.SPANISH_AUDIO: 1}
    assert lib.extract_calls == []  # no hizo falta leer el video entero


def test_burned_in_spanish_subtitles_are_skipped(lib):
    service = lib.service(FakeProvider(), detector=FakeDetector(audio="ja", burned="es"))

    summary = lib.run(service)

    assert summary.by_status == {SubtitleStatus.SPANISH_BURNED: 1}
    assert lib.extract_calls == []


def test_detection_is_cached_between_runs(lib):
    detector = FakeDetector(audio="en")
    lib.episode.with_suffix(".srt").write_text(format_srt(SPANISH))
    service = lib.service(detector=detector)

    lib.run(service, force=True)
    lib.run(service, force=True)

    assert detector.calls == 1


def test_videos_are_re_evaluated_when_a_missing_detection_tool_gets_installed(lib):
    lib.episode.with_suffix(".srt").write_text(format_srt(SPANISH))
    without_ocr = lib.service(detector=FakeDetector(audio="en", tools={"voz"}))
    lib.run(without_ocr)
    assert not without_ocr.needs_work(_video(lib))

    with_ocr = lib.service(detector=FakeDetector(audio="en", burned="es"))
    summary = lib.run(with_ocr)

    assert summary.by_status == {SubtitleStatus.SPANISH_BURNED: 1}


def test_a_candidate_that_fails_to_download_is_skipped_and_not_marked_as_tried(lib):
    provider = FakeProvider(
        {
            "gone": (candidate("gone", description="para bluray.x264-filmhd"), b""),
            "good": (candidate("good"), make_zip("b.srt", shifted(SPANISH, 2))),
        },
        missing=frozenset({"gone"}),
    )
    service = lib.service(provider)

    summary = lib.run(service)

    assert summary.by_status == {SubtitleStatus.DOWNLOADED: 1}
    assert provider.downloads == ["gone", "good"]
    assert not service.store.was_tried(lib.episode, "fake", "gone")


def test_a_provider_outage_leaves_the_video_pending_instead_of_crashing(lib):
    service = lib.service(FakeProvider(down=True))

    summary = lib.run(service)

    assert summary.by_status == {SubtitleStatus.PENDING: 1}
    assert summary.aborted is None


def test_an_unexpected_error_in_one_video_does_not_stop_the_run(lib, monkeypatch):
    second = lib.episode.with_name("house.s01e02.1080p.bluray.x264-filmhd.mkv")
    second.write_bytes(b"video")
    service = lib.service()
    real = service._process

    def flaky(video, base, download):
        if "s01e02" in video.path.name:
            raise KeyError("bug")
        return real(video, base, download)

    monkeypatch.setattr(service, "_process", flaky)
    lib.episode.with_suffix(".srt").write_text(format_srt(SPANISH))

    summary = lib.run(service)

    assert summary.by_status == {SubtitleStatus.IN_SYNC: 1, SubtitleStatus.ERROR: 1}


def test_a_run_does_nothing_while_another_one_holds_the_lock(lib):
    from misflix.infra.process_lock import exclusive_lock

    service = lib.service()

    with exclusive_lock(service.store.path.with_name("subs.lock")):
        summary = lib.run(service)

    assert summary.busy
    assert summary.processed == 0
    assert lib.run(service).processed == 1  # libre otra vez
