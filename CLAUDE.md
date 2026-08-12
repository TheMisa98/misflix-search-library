# CLAUDE.md

Este archivo es el índice de reglas que SIEMPRE aplican al trabajar en este repo, sin
que se pidan cada vez. El detalle que cambia con cada feature (arquitectura línea a
línea, decisiones, flujo de git) vive en `docs/` — no lo dupliques aquí.

## Idioma

Responder siempre en español al usuario en este repo, sin importar el idioma del
mensaje o de los comentarios del código.

## Stack (cerrado — no añadir dependencias sin registrar el motivo en `docs/DECISIONS.md`)

- **Python >=3.14**, gestionado con **uv** (entorno + lockfile).
- **Typer** — CLI. **Rich** — output/progress bars en terminal.
- **httpx** + **BeautifulSoup4**/**lxml** — HTTP y parseo HTML para sitios sin
  protección anti-bot.
- **curl_cffi** — impersonar el fingerprint TLS de un navegador real contra
  Cloudflare (ver `docs/ARCHITECTURE.md` § `infra/cloudflare.py`).
- **pytest** — testing. **ruff** — lint + formato + docstrings (convención Google).
  **mypy** — chequeo de tipos.
- **hatchling** — build backend del script instalable (`misflix`).

Requiere terminal **Kitty** (`kitten` en `PATH`) para renderizar portadas — ver
`docs/ARCHITECTURE.md` § `ui/`.

## Arquitectura: monolito modular en capas

Mapa completo, detalle de cada provider/flujo y todo lo "verificado en vivo":
`docs/ARCHITECTURE.md`.

Regla rápida: `core/` no importa clases concretas de `providers/` (solo `Protocol`s y
modelos puros) — nunca sabe qué sitio se scrapea. Con `infra/` la distinción es más
fina: lo que tiene más de una implementación intercambiable va vía `Protocol`
(`Downloader`, `HttpGetClient` — ver DIP más abajo); una utilidad de una sola
implementación (filesystem, extracción de `.rar`, IMDb, resolución de
mediafire/antupload) se compone directo desde `core/services/`, sin `Protocol` de
por medio — no hay nada que inyectar ahí. `cli/` no importa `infra/` en absoluto, ni
siquiera esas utilidades: todo lo que necesita de ahí (tipos de excepción incluidos)
se re-exporta desde `core/services/download_service.py`, el único punto de contacto
entre la capa de comandos y los detalles técnicos. `providers/` implementa los
`Protocol`s de `core/ports.py`; `cli/`/`ui/` son la única capa que puede importar
Typer/Rich/`kitten`.

## SOLID — cómo aplica aquí (no en abstracto)

- **SRP**: cada carpeta de `misflix/` tiene una sola razón para cambiar (`cli`=parseo
  de comandos, `core`=reglas de negocio, `providers`=scraping por sitio,
  `infra`=detalle técnico, `ui`=presentación en terminal).
- **OCP**: una fuente nueva es un módulo nuevo en `providers/` que implementa
  `SourceProvider`/`SeriesProvider` y se registra en `registry.py` — sin tocar
  `cli/`, `core/` ni `ui/`.
- **LSP**: `StaticProvider` es la única base compartida; cualquier provider debe ser
  sustituible donde se espera un `SourceProvider` (o `SeriesProvider`) sin romper el
  `isinstance` narrowing de `cli/download.py`.
- **ISP**: `core/ports.py` separa `SourceProvider` (básico) de `SeriesProvider`
  (extiende con temporadas/episodios) — un provider de libros no implementa métodos
  de series que no le aplican.
- **DIP**: `DownloadService` está tipado contra el `Protocol` `Downloader`, no contra
  `HttpxDownloader` concreto; `StaticProvider.http` está tipado contra
  `HttpGetClient` (`Protocol` estructural), no contra `HttpClient` concreto — así
  `ZonaLerosProvider` inyecta `CloudflareHttpClient` sin romper el tipo.

## Reglas de trabajo (aplican siempre, sin pedirlas)

1. Toda función/módulo nuevo en `core/`, `providers/`, `infra/` se entrega **con
   tests de pytest en el mismo cambio** — no se piden aparte. Un provider nuevo se
   prueba contra fixtures HTML guardadas (`tests/fixtures/<provider>/`), nunca contra
   la red real (ver `docs/DECISIONS.md`).
2. Si cambia el contrato de un `Protocol` en `core/ports.py` o el límite entre capas,
   se actualiza `docs/ARCHITECTURE.md` en el mismo cambio.
3. Toda decisión de arquitectura no trivial (nueva dependencia, cambio de patrón,
   trade-off elegido) se registra en `docs/DECISIONS.md` con fecha y motivo — nunca
   se descarta en silencio.
4. Nada de `Any` explícito; tipos completos en toda función/clase de `misflix/`.
5. No hacer commits ni push salvo que se pida explícitamente.
6. Antes de dar por terminada una tarea: `ruff check`, `ruff format --check`, `mypy`
   y `pytest` deben pasar limpios. Esto además se hace cumplir automáticamente vía el
   hook de pre-push — ver `docs/GIT_WORKFLOW.md` § Pre-push hook. Nunca usar
   `--no-verify` para saltárselo salvo que el usuario lo pida explícitamente.
7. **Los commits de este repo NUNCA llevan trailer de coautoría del asistente**
   (nada de `Co-Authored-By: Claude ...`) — el autor siempre es el usuario.
8. Commits siguen la convención documentada en `docs/GIT_WORKFLOW.md`; el trabajo
   vive en ramas `feature/<slug>` o `fix/<slug>`, nunca directo en `master`. Detalle
   completo, incluyendo por qué se adoptó, en `docs/GIT_WORKFLOW.md` y
   `docs/DECISIONS.md`.

## Testing

- **Unitarios**: pytest contra los `Protocol`s propios de `core/ports.py`, con fakes
  propios — no contra la red real (ver `tests/test_search_service.py`,
  `tests/test_download_service.py` para el patrón de doble usado).
- **Parseo de providers**: contra fixtures HTML guardadas en
  `tests/fixtures/<provider>/`, nunca contra el sitio real en tests/CI. El
  comportamiento que sí depende de la red real (Cloudflare, redirects, cookies) se
  verifica a mano durante el desarrollo y queda documentado en
  `docs/ARCHITECTURE.md` con la marca "verificado en vivo" — no se intenta
  automatizar.
- **E2E**: manual — decisión consciente de no automatizar un CLI interactivo con
  terminal Kitty real y pasos que requieren intervención humana (captcha), dado que
  es un proyecto personal sin usuarios externos. Ver `docs/DECISIONS.md`.

## Comandos

```bash
uv sync                              # instalar dependencias
uv run main.py <command>             # correr el CLI (ej. uv run main.py search movies "query", search books "query")
misflix <command>                    # lo mismo, via el wrapper ~/.local/bin/misflix (sin uv)
uv run pytest                        # suite completa
uv run pytest tests/test_download_service.py::test_download_creates_dest_dir_and_delegates_to_downloader  # un test
uv run ruff check .                  # lint (estilo + orden de imports + docstrings estilo Google en misflix/)
uv run ruff format .                 # auto-formato
uv run mypy                          # chequeo de tipos de misflix/ (scoped vía [tool.mypy] files, tests/ excluido)
```

Docstrings en `misflix/` siguen la convención Google (línea de resumen, luego
`Args:`/`Returns:`/`Raises:`/`Attributes:` según aplique) — forzado por las reglas
`D` de `ruff` (`[tool.ruff.lint.pydocstyle]`, `convention = "google"`). Docstrings
faltantes en one-liners privados no se marcan a propósito (`D100`-`D107` ignorados):
la convención es que un docstring que existe debe tener forma Google, no que toda
función necesite uno.

## Contexto real del usuario (no inventar, esto ya se verificó en el repo)

- Librería organizada en disco según convención Plex/Kodi
  (`<series_dir>/<Series>/Season NN/...`) bajo `/mnt/misflix/Misflix/Series` — ver
  `docs/ARCHITECTURE.md`.
- Variables de entorno `MISFLIX_MOVIES_DIR`/`MISFLIX_SERIES_DIR`/`MISFLIX_BOOKS_DIR`
  en `.env` (ver `.env.example`), leídas por `misflix/config/settings.py`.
- Un solo provider real de películas/series hoy: `zona-leros` (Cloudflare Managed
  Challenge). Libros: `lectulandia`. Antes de agregar uno nuevo, revisar si
  `docs/DECISIONS.md` ya cubre un patrón equivalente (Cloudflare, ad-lockers,
  antupload).

## Wiki interna (docs/)

- `docs/ARCHITECTURE.md` — mapa de módulos, límites entre capas y el detalle
  "verificado en vivo" de cada provider/flujo.
- `docs/DECISIONS.md` — registro de decisiones tipo ADR ligero, incluyendo las
  reconstruidas desde el `CLAUDE.md` anterior con la marca **(reconstruida)**.
- `docs/GIT_WORKFLOW.md` — ramas, convención de commits, detalle del hook de
  pre-push.
- `docs/CHANGELOG.md` — se crea cuando exista la primera release publicada
  (actualmente sin versionar más allá de `0.1.0` en `pyproject.toml`).

Antes de proponer una dependencia, patrón o estructura nueva: revisa si ya hay una
decisión registrada en `docs/DECISIONS.md` que la cubra o la contradiga.
