# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commit convention

Never add `Co-Authored-By: Claude` (or any variant) to git commit messages in this repo.

## Commands

```bash
uv sync                              # install dependencies
uv run main.py <command>             # run the CLI (e.g. uv run main.py search run "query")
uv run pytest                        # run the full test suite
uv run pytest tests/test_download_service.py::test_download_creates_dest_dir_and_delegates_to_downloader  # single test
uv run playwright install chromium   # only needed for DynamicProvider-based scrapers
```

Requires Python >=3.14 and a Kitty terminal (`kitten` must be on `PATH`) for cover rendering.

## Architecture

This is an interactive CLI (Typer) for searching and downloading movies/books from
scraped repos, rendering cover art in the terminal via Kitty's graphics protocol. The
codebase is layered so that adding a new scraping source never touches the CLI,
services, or UI:

- **`misflix/cli/`** — Typer commands only. Each module (`search.py`, `download.py`,
  `config.py`) parses args, calls a `core/services` service, and hands the result to
  `ui/`. No scraping or business logic lives here.
- **`misflix/core/`** — framework-agnostic domain layer. `models.py` defines `Media`
  and `DownloadOption`. `ports.py` defines `Protocol`s (`SourceProvider`, `Downloader`,
  `CoverRenderer`) that `infra/`, `providers/`, and `ui/` implement — `core/` never
  imports from those packages. This is what makes `search_service.py` /
  `download_service.py` testable without network or a real terminal (see
  `tests/test_search_service.py`, `tests/test_download_service.py` for the fake-object
  pattern used).
- **`misflix/providers/`** — one module per scraped repo, each implementing
  `SourceProvider` (`search`, `get_media`, `get_download_options`). `base.py` offers two
  starting points: `StaticProvider` (httpx + BeautifulSoup, for plain HTML sites) and
  `DynamicProvider` (Playwright, for JS-rendered/anti-bot sites). New sources register
  themselves in `registry.py`; nothing outside `providers/` needs to change to add one.
- **`misflix/infra/`** — technical details behind the ports: `http_client.py` (httpx
  wrapper), `browser.py` (Playwright session wrapper), `downloader.py` (streaming
  download with a progress callback), `filesystem.py` (filename sanitizing, dir
  creation).
- **`misflix/ui/`** — terminal presentation. `views.py` renders result tables (rich).
  `prompts.py` drives the interactive confirm/choose-destination flow. `image_render.py`
  (`CoverRenderer`) shells out to `kitten icat` via `subprocess` rather than using a
  Python image-terminal library — `term-image` was deliberately rejected because it
  pins `Pillow<11`, which has no wheels for Python 3.14 and forces a from-source build.
  `kitten icat` also accepts a cover URL directly, so this layer has no Pillow/httpx
  dependency of its own.
- **`misflix/config/settings.py`** — reads `MISFLIX_MOVIES_DIR` / `MISFLIX_BOOKS_DIR`
  from `.env` (see `.env.example`) via `python-dotenv`.

No providers for real sites are implemented yet — `providers/` currently only has the
base classes and registry. When adding the first one, prefer testing its HTML parsing
against saved fixtures rather than live network calls.
