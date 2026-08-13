import pytest
import typer

from misflix.cli import download
from misflix.cli.download import _download, _parse_code_from_url, _try_resolve_without_browser, run_with_retry
from misflix.core.models import DownloadOption, Media, MediaKind
from misflix.infra.downloader import DownloadError


def test_parse_code_from_url_extracts_season_and_episode():
    url = "https://www.mediafire.com/file/fcashd3cwglxqdm/RCKYMRTS01E01_ZL.rar/file"

    assert _parse_code_from_url(url) == (1, 1)


def test_parse_code_from_url_handles_two_digit_numbers():
    url = "https://www.mediafire.com/file/xyz/ShowS12E09_ZL.rar/file"

    assert _parse_code_from_url(url) == (12, 9)


def test_parse_code_from_url_returns_none_without_a_code():
    url = "https://www.mediafire.com/file/xyz/random_upload.rar/file"

    assert _parse_code_from_url(url) is None


class FakeResponse:
    def __init__(self, url: str):
        self.url = url


class FakeHttpClient:
    def __init__(self, response: FakeResponse | None):
        self._response = response

    def try_get(self, url: str) -> FakeResponse | None:
        return self._response


class FakeProvider:
    def __init__(self, http=None):
        self.http = http


def test_try_resolve_without_browser_returns_url_when_probe_lands_on_mediafire():
    option = DownloadOption(label="MEDIAFIRE", url="https://anomizador.zona-leros.com/l?hs=x")
    provider = FakeProvider(http=FakeHttpClient(FakeResponse("https://www.mediafire.com/file/abc/ep.rar/file")))

    assert _try_resolve_without_browser(provider, option) == ["https://www.mediafire.com/file/abc/ep.rar/file"]


def test_try_resolve_without_browser_returns_none_when_probe_fails():
    option = DownloadOption(label="MEDIAFIRE", url="https://anomizador.zona-leros.com/l?hs=x")
    provider = FakeProvider(http=FakeHttpClient(None))

    assert _try_resolve_without_browser(provider, option) is None


def test_try_resolve_without_browser_returns_none_when_probe_lands_elsewhere():
    option = DownloadOption(label="MEGA", url="https://anomizador.zona-leros.com/l?hs=x")
    provider = FakeProvider(http=FakeHttpClient(FakeResponse("https://mega.nz/file/abc123")))

    assert _try_resolve_without_browser(provider, option) is None


def test_try_resolve_without_browser_returns_none_without_a_try_get_method():
    option = DownloadOption(label="MEDIAFIRE", url="https://anomizador.zona-leros.com/l?hs=x")
    provider = FakeProvider(http=object())

    assert _try_resolve_without_browser(provider, option) is None


def test_try_resolve_without_browser_returns_none_without_an_http_client():
    option = DownloadOption(label="MEDIAFIRE", url="https://anomizador.zona-leros.com/l?hs=x")

    assert _try_resolve_without_browser(FakeProvider(), option) is None


class FakeMediaProvider:
    def __init__(self, media: Media):
        self._media = media

    def get_media(self, media_id: str) -> Media:
        return self._media


def _make_media(kind: MediaKind) -> Media:
    return Media(id="x", title="X", kind=kind, source="a", page_url="http://example.com")


def test_download_runs_the_flow_when_the_kind_matches(monkeypatch):
    media = _make_media(MediaKind.MOVIE)
    monkeypatch.setattr(download, "get_provider", lambda source: FakeMediaProvider(media))
    calls = []
    monkeypatch.setattr(download, "run_download_flow", lambda provider, m: calls.append(m))

    _download("a", "x", kinds={MediaKind.MOVIE, MediaKind.SERIES}, kind_label="una pelicula o serie")

    assert calls == [media]


def test_download_rejects_a_mismatched_kind(monkeypatch, capsys):
    media = _make_media(MediaKind.BOOK)
    monkeypatch.setattr(download, "get_provider", lambda source: FakeMediaProvider(media))
    calls = []
    monkeypatch.setattr(download, "run_download_flow", lambda provider, m: calls.append(m))

    with pytest.raises(typer.Exit):
        _download("a", "x", kinds={MediaKind.MOVIE, MediaKind.SERIES}, kind_label="una pelicula o serie")

    assert calls == []
    assert "book" in capsys.readouterr().out


def test_run_with_retry_returns_the_result_without_asking_when_it_succeeds(monkeypatch):
    monkeypatch.setattr(typer, "confirm", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no deberia preguntar")))

    assert run_with_retry(lambda: "ok", description="X") == "ok"


def test_run_with_retry_retries_once_confirmed_and_returns_the_second_attempt(monkeypatch):
    monkeypatch.setattr(typer, "confirm", lambda *a, **k: True)
    attempts = iter([DownloadError("timeout"), "ok"])

    def action():
        result = next(attempts)
        if isinstance(result, Exception):
            raise result
        return result

    assert run_with_retry(action, description="X") == "ok"


def test_run_with_retry_gives_up_when_the_user_declines(monkeypatch):
    monkeypatch.setattr(typer, "confirm", lambda *a, **k: False)

    def action():
        raise DownloadError("timeout")

    assert run_with_retry(action, description="X") is None


def test_run_with_retry_pauses_and_resumes_a_shared_progress_bar(monkeypatch):
    monkeypatch.setattr(typer, "confirm", lambda *a, **k: False)
    events: list[str] = []

    class FakeProgress:
        def stop(self):
            events.append("stop")

        def start(self):
            events.append("start")

    def action():
        raise DownloadError("timeout")

    run_with_retry(action, description="X", progress=FakeProgress())

    assert events == ["stop", "start"]
