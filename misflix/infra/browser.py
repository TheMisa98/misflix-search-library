from __future__ import annotations

from playwright.sync_api import Browser, Playwright, sync_playwright


class BrowserSession:
    """Wrapper sobre Playwright para providers que necesitan JS renderizado."""

    def __init__(self, headless: bool = True):
        self._playwright: Playwright = sync_playwright().start()
        self._browser: Browser = self._playwright.chromium.launch(headless=headless)

    def new_page(self):
        return self._browser.new_page()

    def close(self) -> None:
        self._browser.close()
        self._playwright.stop()
