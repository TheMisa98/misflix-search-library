from __future__ import annotations

import subprocess

from misflix.infra import browser_launch


def test_open_in_browser_uses_the_first_available_binary(monkeypatch):
    def fake_which(name: str) -> str | None:
        return "/usr/bin/zen-browser" if name == "zen-browser" else None

    monkeypatch.setattr(browser_launch.shutil, "which", fake_which)
    calls = []
    monkeypatch.setattr(browser_launch.subprocess, "Popen", lambda args, **kwargs: calls.append((args, kwargs)))
    fallback_calls = []
    monkeypatch.setattr(browser_launch.webbrowser, "open", fallback_calls.append)

    browser_launch.open_in_browser("https://example.com")

    assert calls == [
        (["/usr/bin/zen-browser", "https://example.com"], {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL})
    ]
    assert fallback_calls == []


def test_open_in_browser_falls_back_to_the_second_binary(monkeypatch):
    def fake_which(name: str) -> str | None:
        return "/usr/bin/zen" if name == "zen" else None

    monkeypatch.setattr(browser_launch.shutil, "which", fake_which)
    calls = []
    monkeypatch.setattr(browser_launch.subprocess, "Popen", lambda args, **kwargs: calls.append(args))

    browser_launch.open_in_browser("https://example.com")

    assert calls == [["/usr/bin/zen", "https://example.com"]]


def test_open_in_browser_falls_back_to_webbrowser_when_no_binary_found(monkeypatch):
    monkeypatch.setattr(browser_launch.shutil, "which", lambda name: None)
    popen_calls = []
    monkeypatch.setattr(browser_launch.subprocess, "Popen", lambda args, **kwargs: popen_calls.append(args))
    opened = []
    monkeypatch.setattr(browser_launch.webbrowser, "open", opened.append)

    browser_launch.open_in_browser("https://example.com")

    assert popen_calls == []
    assert opened == ["https://example.com"]
