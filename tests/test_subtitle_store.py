from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from misflix.core.models import SubtitleCandidate
from misflix.core.subtitles.media import MediaInfo, StreamInfo
from misflix.infra.subtitle_store import SubtitleStore, VideoRecord


def test_probe_cache_is_invalidated_when_the_file_changes(tmp_path):
    store = SubtitleStore(tmp_path / "subs.db")
    info = MediaInfo(
        duration=12.5, streams=(StreamInfo(1, "audio", "aac", language="eng", channels=6),), format_title="t"
    )

    store.put_probe(Path("/v.mkv"), 100, 1.0, info)

    assert store.get_probe(Path("/v.mkv"), 100, 1.0) == info
    assert store.get_probe(Path("/v.mkv"), 101, 1.0) is None


def test_video_records_round_trip_and_filter(tmp_path):
    store = SubtitleStore(tmp_path / "subs.db")
    store.put_video(VideoRecord(path="/lib/House/a.mkv", size=1, mtime=2.0, status="ok", score=0.6))
    store.put_video(VideoRecord(path="/lib/House/b.mkv", size=1, mtime=2.0, status="dudoso"))
    store.put_video(VideoRecord(path="/lib/Grimm_x/c.mkv", size=1, mtime=2.0, status="dudoso"))

    record = store.get_video(Path("/lib/House/a.mkv"))

    assert record is not None and record.score == 0.6 and record.updated_at
    assert [r.path for r in store.videos(["dudoso"])] == ["/lib/Grimm_x/c.mkv", "/lib/House/b.mkv"]
    assert [r.path for r in store.videos(None, "/lib/House")] == ["/lib/House/a.mkv", "/lib/House/b.mkv"]
    # "_" no es comodin en el prefijo
    assert store.videos(None, "/lib/Grimm_") != store.videos(None, "/lib/GrimmX")
    assert store.summary() == {"dudoso": 2, "ok": 1}


def test_attempts_searches_and_actions(tmp_path):
    store = SubtitleStore(tmp_path / "subs.db")
    candidate = SubtitleCandidate(id="1", source="subdivx", title="House M.D. S01E01", description="d", downloads=3)

    store.add_attempt(Path("/v.mkv"), "subdivx", "1", "no_coincide", 0.02)
    store.put_search("subdivx", "house s01e01", [candidate])
    store.log_action(Path("/v.mkv"), "respaldado", Path("/v.srt"), Path("/bk/v.srt"), "no corresponde")

    assert store.was_tried(Path("/v.mkv"), "subdivx", "1")
    assert not store.was_tried(Path("/v.mkv"), "subdivx", "2")
    assert store.cached_search("subdivx", "house s01e01", timedelta(days=1)) == [candidate]
    assert store.cached_search("subdivx", "house s01e01", timedelta(seconds=-1)) is None
    assert store.cached_search("subdivx", "otra", timedelta(days=1)) is None
    action = store.recent_actions()[0]
    assert (action.action, action.file, action.backup) == ("respaldado", "/v.srt", "/bk/v.srt")
