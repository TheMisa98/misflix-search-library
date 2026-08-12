# Arquitectura

Mapa de módulos, límites entre capas y el detalle de comportamiento real de cada
provider/flujo — incluyendo lo marcado como "verificado en vivo" (comportamiento
confirmado navegando/probando el sitio real, no asumido ni deducido del HTML). Si
cambia el contrato de un `Protocol` en `core/ports.py` o el límite entre capas,
este archivo se actualiza en el mismo cambio (ver regla en `CLAUDE.md`).

## Regla rápida de dependencias (no se rompe)

- `misflix/core/` no importa nada de `infra/` ni de providers concretos: solo
  define `Protocol`s (`ports.py`) y modelos puros (`models.py`). El dominio no
  sabe qué sitio se scrapea ni cómo.
- `misflix/providers/` implementa los `Protocol`s de `core/ports.py`. Es la
  única capa que sabe qué HTML/API concreta corresponde a cada fuente.
- `misflix/infra/` son detalles técnicos (HTTP, Cloudflare, archivos, IMDb)
  consumidos por providers vía composición — `cli/` y `core/` nunca importan
  `infra/` directamente.
- `misflix/cli/` y `misflix/ui/` son la única capa que puede importar
  Typer/Rich/`kitten` — el dominio no sabe que existe una terminal.

This is enforced today by `core/ports.py`'s `Protocol`s (`SourceProvider`,
`SeriesProvider`, `Downloader`) and by `base.HttpGetClient`, a structural
`Protocol` that lets `ZonaLerosProvider` inject `CloudflareHttpClient` where a
plain `StaticProvider` expects `HttpGetClient`, without any concrete class
coupling.

## Capas

This is an interactive CLI (Typer) for searching and downloading movies/series/books
from scraped repos, rendering cover art in the terminal via Kitty's graphics protocol.
The codebase is layered so that adding a new scraping source never touches the CLI,
services, or UI:

- **`misflix/cli/`** — Typer commands only. `search.py` and `download.py` each expose two
  subcommands, `movies` and `books` (`search movies "query"` / `search books "query"`,
  `download movies <source> <id>` / `download books <source> <id>`), splitting by what
  the resolved `Media.kind` actually is rather than by which provider returned it — a
  provider can mix kinds (zona-leros resolves both MOVIE and SERIES, so `movies` covers
  both; `SearchService.search`'s `kinds` filter and `download.py`'s `_download` kind
  check are what enforce the split, see below). `search.py` and `download.py` share a
  single download flow (`download.run_download_flow`) so picking a result straight out
  of `search movies`/`search books` and running `download movies|books <source> <id>`
  behave the same way. After showing results, both search subcommands loop on
  `_prompt_pick_or_search_again`: a valid result number *within the currently drawn
  page* downloads it and asks again; anything else — plain text, or a number outside
  the visible page — is taken as a brand new query and fed straight back into another
  `service.search(...)`, so finding the right result doesn't require exiting and
  re-running the command with a different query. Only a blank answer ends the command.
  When a search returns more results than fit on screen, `views.show_results` (see
  below) truncates what it draws but not `results` itself; `_search_loop` keeps the
  full list and a `start` offset across prompts within the same query, and
  `_prompt_pick_or_search_again` — told `[start, end)`, the range actually drawn —
  returns the `_MORE` sentinel when the user types `mas`/`más` and there's more beyond
  `end`, which makes `_search_loop` redraw starting at `end` (same numbering carries
  over, since cards are labeled with their absolute position in `results`, not their
  position on the page) instead of treating "mas" as a new query; `mas` typed when
  there's nothing left to page just falls through as a literal new search, same as any
  other text. `config.py` prints the resolved settings. No scraping or business logic
  lives here.
  - Dead links don't abort a download: `MediaFireResolveError` (page unreachable, or
    reachable but missing `#downloadButton` — Mediafire's "file no longer available"
    state), `DownloadError` (`infra/downloader.py`; any `httpx.HTTPError` while
    streaming, wrapped so the caller doesn't need to know about `httpx` — also deletes
    the partial file it was writing, so a cut-off download doesn't leave a corrupt `.rar`
    for `extract_rar`'s glob to trip over later), and `AntuploadResolveError` are caught
    together (`_LINK_ERRORS` in `download.py`) around every place a file actually gets
    downloaded — most commonly a network timeout partway through a multi-GB download,
    not necessarily a dead link. Every one of those points goes through `_run_with_retry`
    (verified live: without it, a failed download just dropped the user back at the
    search prompt with no indication of what to do next — confusing enough that a
    number typed there, meant as "retry with option 4", got interpreted as a brand new
    search query instead), which asks to retry (reusing the same already-resolved
    option/urls, so no need to reopen the browser or re-paste links) before giving up;
    `None` from it means the user declined, at which point the caller shows a skip
    message and moves on (batch loops) or simply returns (a lone movie/book), so the
    flow only continues back to the search prompt once a download actually succeeded or
    the user explicitly gave up on retrying — not silently after any failure. When the
    retry loop sits inside a shared `progress_bar()` spanning multiple items (season
    pack episodes), `_run_with_retry` is handed that `Progress` object to pause/resume
    around the confirm prompt, since rich's live refresh otherwise fights the prompt for
    the terminal; the other call sites build a fresh progress bar per attempt inside the
    retried callable itself, so it's simply gone (closed via its own `with`) by the time
    the except block runs. For a lone movie/book/episode/season pick, all this means a
    clean retry-or-bail prompt instead of a silent drop to the search prompt; inside
    `_download_episodes_batch` and `_download_season_packs_batch`, it means that one
    item is skipped (with a message, only after the user declines to retry it) and the
    loop moves on to the next one instead of losing the rest of the batch.
  - For a `MediaKind.SERIES` result, `get_download_options` on the series itself comes
    back empty (a series has no downloads of its own), so `run_download_flow` falls
    back to `_download_series`, which does all the rest of the work itself (it never
    hands options back for `run_download_flow` to keep processing — a series never has
    "one option" the way a movie does). It always asks `prompts.choose_series_mode`
    among up to five modes (built from `seasons_with_packs = get_seasons(series)`, a
    list of season numbers that have a full-season pack — empty for a provider without
    `get_seasons`, or for a series where no season got one):
    - `season_pack` (only offered if `seasons_with_packs` is non-empty) and
      `all_season_packs` (only offered if there's more than one season with a pack)
      both go through `_download_season_packs_batch`, given either a one-season list
      (`season_pack`) or the full `seasons_with_packs` list (`all_season_packs`) — a
      single season is just a batch of one, so both end up in the exact same place on
      disk.
    - `episode` and `season_batch`/`series_batch` all go through
      `_download_episodes_batch` the same way: `episode` calls the provider's
      `get_episodes`, lets the user pick one with `prompts.choose_episode`, and passes
      a one-item list; `season_batch`/`series_batch` pass a whole season's worth (via
      `download_service.group_episodes_by_season`) or every episode of the series.

    Both batch loops (`_download_season_packs_batch`, `_download_episodes_batch`) —
    always a "batch", even for the batch-of-one single-season/single-episode case —
    follow the same shape and cannot remove the one truly manual step: each
    season/episode page needs its own fresh Turnstile solve (see the ad-locker note
    below), so the browser still opens and `collect_direct_links` still runs once per
    item. What they do remove is having to re-run the CLI and re-pick options for
    every item, and (when there's more than one) they ask to start just once
    (`prompts.confirm_batch_download`), reuse whichever host (MEGA/MEDIAFIRE) was
    picked for the first item on every later one where that host is available
    (`prompts.option_host`), and continue past errors/skips instead of aborting the
    whole batch. On disk, both organize a series identically to how the existing
    library under `/mnt/misflix/Misflix/Series` is laid out (Plex/Kodi convention):
    `<series_dir>/<Series>/Season NN/...` — `Season` in English, 2-digit zero-padded
    (`download_service.season_folder_name`), regardless of the series' own language.
    `_download_episodes_batch` names each file `<Series> - SxxEyy.ext` (season/episode
    parsed from the scraped title via `download_service.parse_episode_code`; falls back
    to season `00` and the raw title when a title has no `SxE`-shaped code) and, for a
    non-`opens_externally` episode option, downloads directly via
    `DownloadService.download`'s `filename_stem` override instead of going through the
    extract step.

    `_download_season_packs_batch` does **not** treat the season's urls the way a
    multi-part movie/episode download treats its urls (see below) — a "season pack" ad-
    locker page on zona-leros lists one independent, complete `.rar` per episode, not
    one archive split into `.partN.rar` volumes. Feeding those straight into
    `download_parts`/`extract_and_organize` as if they were volumes of one archive was
    an actual bug found live: `unrar` sees "Season 01.part1.rar" as a complete
    standalone archive (nothing in a genuinely single-volume `.rar`'s own header says
    "look for a continuation"), extracts only that first episode, reports success, and
    the code then deleted *all* the `.rar`s on the assumption extraction had used them
    — silently losing every other episode's still-unextracted archive. Instead, each url
    in the list is downloaded and extracted **individually** (`_extract_and_flatten`,
    shared with `_download_episodes_batch`'s single-episode tail): season/episode number
    comes from `_parse_code_from_url` matching `S\d{1,2}E\d{1,2}` in the url itself (the
    real Mediafire filenames already contain it, e.g. `RCKYMRTS01E01_ZL.rar`), falling
    back to the url's position in the pasted list when a url has no such code.
- **`misflix/core/`** — framework-agnostic domain layer. `models.py` defines `MediaKind`
  (`MOVIE`/`SERIES`/`BOOK`), `Media`, `DownloadOption` (the latter has an
  `opens_externally` flag for sources whose download must be finished by hand in a
  browser), and `MOVIE_KINDS`/`BOOK_KINDS` (the `frozenset[MediaKind]` pairs that back
  the `movies`/`books` split in both `cli/search.py` and `cli/download.py` — kept in one
  place instead of redefined per CLI module). `ports.py` defines the `Protocol`s:
  `SourceProvider` (`search`/`get_media`/`get_download_options` — every provider),
  `SeriesProvider` (extends `SourceProvider` with `get_seasons`/`get_episodes`/
  `get_season_download_options`; `@runtime_checkable`, so `cli/download.py` narrows to
  it with `isinstance(provider, SeriesProvider)` instead of `getattr`-probing each
  method by name), and `Downloader` (the `download(url, dest_path, on_progress)`
  contract `DownloadService` is typed against, instead of the concrete
  `HttpxDownloader`, so the service layer depends on an abstraction rather than a
  specific implementation). `kinds` (a `set[MediaKind]`, e.g.
  `zona_leros.ZonaLerosProvider.kinds = {MOVIE, SERIES}`,
  `lectulandia.LectulandiaProvider.kinds = {BOOK}`) is a plain optional attribute, not
  part of any `Protocol` — still probed with `getattr` in `SearchService.search`, which
  uses it to skip querying a provider *before* calling it at all when none of its
  declared kinds match what's being searched for. This was a real bug, not just an
  optimization: without it, `search books "..."` (no explicit `source`) iterated every
  registered provider including zona-leros, so a books-only search still tripped
  zona-leros's Cloudflare Turnstile (opened the browser) even though its results
  would've been filtered out afterward anyway by `Media.kind`. A provider that doesn't
  declare `kinds` (or a test double) is still queried unconditionally, same as before
  this existed.
  `download_service.py` handles: a single streamed download (`DownloadService.download`,
  with an optional `filename_stem` override used by the batch flows below); movie-only
  `resolve_folder_name` (Plex-style `Titulo (anio)` via IMDb; falls back to the raw
  title/year on the `Media` when there's no IMDb match); `download_parts` (sequential
  multi-part downloads into a folder, resolving Mediafire pages to their direct link
  along the way, with a per-part progress callback); `extract_and_organize` (unpack the
  downloaded `.rar` and flatten the video(s) out of it — see `infra/archives.py`; used
  for every single-item download: movies, single episodes, and now each individual
  episode inside a season pack too). It calls `extract_rar` without checking the
  result — verified live: Mediafire sometimes serves the video file itself, already
  uncompressed, no `.rar` involved at all, so `extract_rar` is a safe no-op in that case
  and `flatten_video` still finds the (already-there) video and organizes it the same
  way; only when *neither* an extracted nor an already-loose video turns up does it
  return `None`. Its sibling `extract_and_organize_season` (keeps *every* video found in
  one `.rar`, for a hypothetical provider whose season pack truly is one combined
  multi-episode archive) currently has no caller, since zona-leros's season packs turned
  out to be one independent `.rar` per episode instead (see above); and
  `resolve_episode_stem` / `resolve_season_dir`
  (Plex-style `Serie - SxxEyy` file naming and `Serie/Season NN` folder for a single
  episode downloaded outside of a season pack). The module-level functions
  `parse_episode_code` (extracts `(season, episode)` from a scraped episode title like
  `"Breaking Bad 5x1"`; `None` when a title has no `SxE`-shaped code),
  `group_episodes_by_season`, and `season_folder_name` (the shared `Season NN` format,
  in English and 2-digit zero-padded — matching the existing hand-organized library, not
  the site's own language) back the batch download flow in `cli/download.py`. See
  `tests/test_search_service.py`, `tests/test_download_service.py` for the fake-object
  pattern used to keep these testable without network access.
- **`misflix/providers/`** — one module per scraped repo, each implementing
  `SourceProvider` (`search`, `get_media`, `get_download_options`, plus `SeriesProvider`'s
  season/episode methods for series sources — see `core/ports.py`). `base.py`'s
  `StaticProvider` is the only base left (httpx + BeautifulSoup, for plain HTML sites;
  the old Playwright-based `DynamicProvider` was removed — see the Cloudflare note
  below); its `http` attribute is typed against `base.HttpGetClient`, a small structural
  `Protocol` (just `get(url) -> <something with .text>`) rather than the concrete
  `HttpClient`, so `ZonaLerosProvider` can hand it a `CloudflareHttpClient` instead
  (different class, same shape) without a type mismatch. New sources register
  themselves in `registry.py`.
  - **`zona_leros.py`** — the one real provider so far, covering both movies and
    series. The site sits behind a Cloudflare Managed Challenge (interactive Turnstile)
    that defeats Playwright even headed with a manual click, because Cloudflare
    fingerprints the CDP connection itself, not just `navigator.webdriver`. It uses
    `infra/cloudflare.py` instead of plain `httpx`. A series page has no downloads of
    its own: `get_media` disambiguates what kind of page an id refers to via prefixes
    (no prefix = movie at `/peliculas/<id>`, `series:` = series ficha at
    `/series/<id>`, `episode:` = a single episode at `/series/episode/<id>`, which does
    have downloads). `get_seasons`/`get_season_download_options` scrape season-pack
    buttons by walking the page in document order and pairing each `ul.ListEpisodios`
    with the `div#dw` download block(s) that follow it (there's no container element
    that groups them — see `_season_download_blocks`); not every series offers season
    packs. `get_download_options`/`get_season_download_options` only surface
    MEGA/MEDIAFIRE buttons (the other hosts are noise); `option.url` for all of them is
    **always** a second-layer ad-locker link (`anomizador.zona-leros.com` → `zpaste.net`
    or similar), never a direct host link — that part of the old docs here was accurate.
    What turned out to be wrong: not every ad-locker link actually gates behind a fresh
    Turnstile solve. Verified live (see `download.py`'s `_try_resolve_without_browser`):
    a MEDIAFIRE ad-locker link for a movie sits behind a real Cloudflare challenge (a
    plain request to it gets `cf-mitigated: challenge`/403), but the *same kind* of link
    for an episode is sometimes nothing more than a plain HTTP redirect chain straight to
    `mediafire.com` — no JS, no challenge, resolved instantly with the existing
    `cf_clearance` cookie. So before committing to the manual step, every
    `opens_externally` option gets one cheap probe first:
    `CloudflareHttpClient.try_get(option.url)` (a single non-escalating request — see
    below, it never opens a browser on its own) and, if the final URL after redirects is
    a `mediafire.com` link, that's used directly, skipping
    `confirm_open_externally`/`collect_direct_links` entirely. Whenever the probe fails
    or lands somewhere else (still on the ad-locker, MEGA, an actual challenge), the user
    finishes it by hand in the browser and pastes the resulting direct links back into
    the CLI as before (see `ui/prompts.collect_direct_links`).
  - **`lectulandia.py`** — book provider (epub/pdf) for lectulandia.com. Unlike
    zona-leros, a plain GET isn't challenged here (verified live), so this is a
    plain `StaticProvider`, no Cloudflare workaround needed. Each format button on
    a book page links to `/download.php?t=<1|2>&d=<code>&ti=<title>`, a page whose
    only job is to show a fake 11-second countdown before redirecting (via a
    `setTimeout` in `uCommon.js`) to `https://www.antupload.com/file/<linkCode>/` —
    verified live, nothing server-side actually depends on waiting out that
    countdown. Better still, `<linkCode>` doesn't need scraping off that page at
    all: it's exactly the `d` query param, base64-encoded (`d=QzRyRW1qN2Yv` decodes
    straight to `C4rEmj7f/`), so `get_download_options` decodes it directly and
    builds the antupload url itself, skipping `/download.php` entirely. Resolving
    that url to actual bytes (antupload needs a session cookie + Referer, see
    `infra/antupload.py`) is `DownloadService.download`'s job, not the provider's —
    every option comes back with `opens_externally=False` since none of this needs
    a browser or manual captcha-solving.
- **`misflix/infra/`** — technical details behind the ports:
  - `http_client.py` — plain httpx wrapper for sites with no bot protection. Also holds
    `DEFAULT_HEADERS` (the shared User-Agent), imported by `imdb.py`, `antupload.py`,
    and `mediafire.py` instead of each redefining the same dict.
  - `soup.py` — one helper, `attr(tag, name)`: BeautifulSoup's stubs type every
    attribute as `str | list[str]` (an HTML attribute can in theory be multi-valued,
    like `class`), but every attribute this project reads (`href`, `src`, `style`,
    `value`) is always a single string in practice. Used everywhere a provider does
    `tag["href"]` instead of indexing the tag directly, so that stays true for the
    type checker too.
  - `cloudflare.py` (`CloudflareHttpClient`) — reuses the `cf_clearance` cookie from a
    real Firefox-based browser profile (see `browser_cookies.py`) and replays requests
    with `curl_cffi` impersonating a real browser's TLS fingerprint (plain httpx/OpenSSL
    gets fingerprinted and blocked too). `get` escalates when a request comes back
    challenged: opens the user's real browser (`browser_launch.py`) on the same URL and
    polls the cookie store until a new `cf_clearance` shows up, then retries
    transparently. `try_get` is the non-escalating sibling used to speculatively probe a
    URL (see the ad-locker note above) — same one-shot request, but returns `None` on a
    challenge instead of ever opening a browser, so callers can safely try it before
    deciding whether the manual step is actually needed.
    `DEFAULT_USER_AGENT`/`DEFAULT_IMPERSONATE` need to name the *same* Firefox version
    as each other — verified live: with Zen auto-updated to 153 and these pinned at
    152/`firefox135`, the manual verification kept "succeeding" (a newer `cf_clearance`
    did show up and get picked correctly — `browser_cookies.load_domain_cookies` picks
    the highest `expiry`, which tracks recency here since this site's clearance
    duration is constant, so an older cookie sitting in a different Firefox container
    was never the actual problem) but every subsequent `curl_cffi` request still came
    back challenged, because the UA claimed one Firefox version while the TLS
    ClientHello (`impersonate`) looked like a much older one — an inconsistent
    fingerprint Cloudflare could flag on its own, independent of the cookie's
    validity. Fixed by bumping both to whatever the newest shared version is (capped by
    what `curl_cffi` ships — currently `firefox147`, older than the real browser, but
    self-consistent). This drifts again every time Zen/Firefox ships a version newer
    than `curl_cffi`'s newest bundled profile — see the comment above the constants for
    how to re-check and re-pin.
    **Open issue, not fully resolved** (2026-08-12): even after that fix and bumping
    `curl-cffi` 0.15.0 → 0.16.0 (newer cookie/header-order WAF-evasion behavior per
    its changelog, still capped at `firefox147`), a real session — real browser loads
    zona-leros fine, no challenge shown — still gets `cf-mitigated: challenge` from
    every `curl_cffi` profile tried (`firefox147`, `firefox144`, `chrome146`,
    `chrome145`), with or without the valid `cf_clearance`/session cookies, on both
    `/search` and `/`. Two live hypotheses, not yet distinguished: (a) zona-leros
    tightened its Cloudflare tier to a fingerprint check (JA3/JA4/HTTP2) no static
    `curl_cffi` impersonation profile currently defeats, independent of cookie
    validity; (b) the burst of ~8 back-to-back diagnostic requests made while
    debugging this tripped Cloudflare's behavioral/rate scoring on this IP, which
    could self-resolve after a cooldown at normal human-paced usage. Retest at normal
    pace (not in a tight loop) before concluding (a).
  - `browser_cookies.py` — reads `cf_clearance` (and friends) straight out of the
    Firefox/Zen `cookies.sqlite` for a given domain, copying the db first since the
    browser holds it locked.
  - `browser_launch.py` — opens a URL in the user's real Zen/Firefox binary (never a
    Playwright-controlled one — see above); also used to open the ad-locker link for
    `opens_externally` options.
  - `mediafire.py` — Mediafire itself isn't Cloudflare-protected; this just scrapes the
    direct `download*.mediafire.com` link out of `#downloadButton` with plain httpx.
    Raises `MediaFireResolveError` — a single exception type regardless of *why* it
    failed (page unreachable, or reachable but the button's missing because the file
    was taken down) — so callers only need to handle one case.
  - `antupload.py` — the host Lectulandia's download buttons funnel through (see
    `providers/lectulandia.py`). Serving the actual file needs two requests tied
    to the *same* session: a GET on `/file/<code>/` (to pick up a session cookie)
    followed by a GET on the `#downloadB` link scraped from that page, sent with a
    `Referer` pointing back at `/file/<code>/` — verified live, the cookie or the
    Referer alone each get a redirect back to `/file/<code>` instead of the file.
    Because of that, `download()` doesn't just resolve a url for the generic
    downloader to fetch separately the way `mediafire.resolve_direct_url` does:
    it opens one `httpx.Client` (which persists cookies across calls on its own)
    and streams the response straight to disk itself, wrapping any failure
    (page down, button missing, stream cut short) into `AntuploadResolveError` —
    `DownloadService.download` special-cases `is_antupload_url(option.url)` to
    call this instead of the plain `HttpxDownloader`.
  - `imdb.py` (`resolve_title`) — looks up a scraped movie title in IMDb's public
    autocomplete endpoint (no API key, no bot-check) to get the canonical title/year
    for `resolve_folder_name`. The endpoint isn't ranked by relevance, so when the
    scraper already knows the year, matches at that year (or ±1) are preferred over the
    "most popular" one before falling back to picking by IMDb rank.
  - `archives.py` — extracts the downloaded `.rar` (via the `unrar` binary,
    `-pzonaleros`, pointed only at the first `.partN.rar` volume so it chains the rest
    itself — passing every volume as a separate arg makes unrar treat each as its own
    archive and fail) and flattens the result: `flatten_video` keeps the single largest
    video file for a movie/episode (renamed to match the folder), `flatten_all_videos`
    keeps every video for a season pack (original names kept). Deletes the `.rar` parts
    only after confirming a video was actually extracted and moved.
  - `downloader.py` — streaming download with a progress callback; wraps any
    `httpx.HTTPError` (dead link, connection cut mid-stream, etc.) into `DownloadError`
    and deletes whatever partial file it had written before re-raising.
  - `filesystem.py` — filename sanitizing, dir creation, and `part_number` (extracts the
    `partN` number from a filename or url — shared by `archives.py`, for sorting `.rar`
    volumes, and `ui/prompts.py`, for sorting pasted links).
- **`misflix/ui/`** — terminal presentation. `views.py` and `prompts.show_cover` render
  covers via `image_render.CoverRenderer.render_grid`, which lays cards (a `rich`
  renderable + its cover) out in a real grid using Kitty's `--place` placement and
  cursor-position queries (`\x1b[6n`) to know where each row starts, falling back to a
  simple stacked list when there's no interactive tty or no `kitten` binary. Covers
  served from a Cloudflare-protected repo can't be handed to `kitten icat` as a bare URL
  (it does its own unauthenticated fetch and gets a 403), so callers pass a
  `fetch_bytes` callback (built from the provider's own HTTP client) and `CoverRenderer`
  writes the bytes to a temp file for `kitten` instead. `render_grid`'s `image_width`/
  `image_height` box is sized for a movie/series poster (~2:3 portrait — verified live:
  400x570); an episode's own cover on zona-leros is a landscape screenshot-style
  thumbnail instead (~3:2 — verified live: 307x207), so `show_cover(..., landscape=True)`
  (used only for a single episode's own card, in `_download_episodes_batch`) passes a
  wider/shorter box instead of the portrait default. `render_grid` positions every row
  with absolute cursor coordinates computed up front (via `_ensure_fresh_top`) instead of
  letting the terminal scroll row by row, because an image placed with `--place` is
  pinned to a fixed screen row and does *not* scroll along with new text (see
  `_ensure_fresh_top`'s own docstring) — so a row that lands beyond the terminal's actual
  height doesn't appear "further down after scrolling" the way plain text would: it gets
  clamped on top of whatever was already drawn, unreadable. Verified live with a search
  that matched several results with long titles/ids: rows past the first two overlapped
  into a garbled mess. Because of that, `render_grid` checks, before drawing each row
  (after the first, which always gets drawn even if it doesn't fully fit — better a
  clipped row than a blank screen), whether it fits in what's left of the real terminal
  height (`_terminal_rows`, `os.get_terminal_size().lines`) and stops there instead of
  continuing to write off-screen coordinates; it returns how many cards it actually
  drew, starting the count from whichever slice of `cards` the caller handed it (see
  below). `views.show_results(results, fetcher_for, start=0)` renders `results[start:]`
  — as many as fit — labeling each card with its *absolute* position in `results`
  (`start`+offset+1, not its position within the page), and returns the half-open
  `[start, end)` range it actually drew (not a truncated list) so a search with more
  matches than fit on screen can be paged instead of just losing the rest: the caller
  (`cli/search.py`'s `_search_loop`) keeps the full `results` and re-invokes
  `show_results` with `start=end` when the user asks for more (see above), and only
  ever lets a typed number resolve to something within the last-drawn `[start, end)` —
  a number for a result that isn't currently on screen (whether cut off this page or
  on a different one) isn't silently misread as picking whatever `results` happens to
  have at that index; it falls through to "not a valid number" and is treated as a new
  search query instead, same as any other out-of-range input. `prompts.py` drives the
  interactive flow: `choose_option` (and `option_host`, extracting just the server name
  from a label like `"MEGA (1080p)"`, reused by the batch flow to stick to one
  preferred host), `choose_series_mode`/`choose_season`/`choose_episode` for series
  (`choose_series_mode` always offers all of `season_pack` — only when packs
  exist —, `season_batch`, `series_batch`, `episode`), `confirm_batch_download` (asked
  once before a batch loop, not per episode), `confirm_download` vs.
  `confirm_open_externally` + `collect_direct_links` (paste-all, auto-sorts by the
  `partN` in the filename; disables Kitty's bracketed-paste wrapping while reading so a
  multi-line paste doesn't interleave with the prompt) and `progress_bar` (per-part rich
  progress).
- **`misflix/config/settings.py`** — reads `MISFLIX_MOVIES_DIR` / `MISFLIX_SERIES_DIR` /
  `MISFLIX_BOOKS_DIR` from `.env` (see `.env.example`) via `python-dotenv`.
  `download.run_download_flow` picks between them based on `media.kind` — no
  destination prompt anymore.

## Agregar un provider nuevo

Preferir probar el parseo de HTML contra fixtures guardadas (ver
`tests/fixtures/zona_leros/`, `tests/fixtures/lectulandia/`) en vez de contra la red
real (ver `docs/DECISIONS.md`). Si el sitio objetivo resulta estar detrás de
Cloudflare, reusar `infra/cloudflare.py` en vez de recurrir a Playwright — no funciona
contra un Managed Challenge por las razones descritas arriba.

El paquete se instala como script (`project.scripts` en `pyproject.toml`, entry point
`misflix.cli.app:app`) construido con `hatchling` — eso es lo que corre
`~/.local/bin/misflix`.
