# misflix-search

CLI interactivo para buscar y descargar peliculas y libros desde tus propios repos,
con vista previa de portadas directamente en la terminal (protocolo grafico de Kitty).

## Estado

En construccion. La arquitectura base ya esta montada (ver abajo); falta implementar
providers reales para tus repos.

## Arquitectura

El proyecto separa el dominio (que hace la app) de los detalles tecnicos (como lo hace),
para que agregar un repo nuevo no obligue a tocar el CLI ni la UI.

```
misflix_search/
├── main.py                        # entry point, llama a misflix.cli.app
├── misflix/
│   ├── cli/                       # Typer: parseo de comandos, nada de logica pesada
│   │   ├── app.py                 # Typer() raiz, registra subcomandos
│   │   ├── search.py              # `misflix search run <query>`
│   │   ├── download.py            # `misflix download run <source> <id>`
│   │   └── config.py              # `misflix config show`
│   │
│   ├── core/                      # dominio puro, sin dependencias externas
│   │   ├── models.py              # Media, DownloadOption, MediaKind
│   │   ├── ports.py                # Protocols: SourceProvider, Downloader, CoverRenderer
│   │   └── services/
│   │       ├── search_service.py    # reparte una busqueda entre providers
│   │       └── download_service.py  # orquesta una descarga
│   │
│   ├── providers/                 # un modulo por repo/fuente scrapeable
│   │   ├── base.py                # StaticProvider (httpx+bs4) / DynamicProvider (playwright)
│   │   └── registry.py            # nombre -> instancia de provider
│   │
│   ├── infra/                     # detalles tecnicos (red, browser, filesystem)
│   │   ├── http_client.py         # wrapper de httpx
│   │   ├── browser.py             # wrapper de Playwright
│   │   ├── downloader.py          # descarga en streaming con progreso
│   │   └── filesystem.py          # sanitizar nombres, crear carpetas
│   │
│   ├── ui/                        # presentacion en terminal
│   │   ├── image_render.py        # portadas via `kitten icat` (subprocess, protocolo Kitty)
│   │   ├── views.py               # tablas de resultados (rich)
│   │   └── prompts.py             # confirmaciones, elegir opcion/carpeta destino
│   │
│   └── config/
│       └── settings.py            # settings desde .env (carpetas de descarga, etc.)
│
├── tests/
└── .env.example
```

**Regla de dependencia:** `core/` no importa nada de `infra/`, `ui/` ni `providers/`
directamente — solo define contratos (`Protocol`) en `ports.py`. Eso permite testear
la orquestacion sin red ni terminal real, y cambiar cualquier detalle tecnico sin
tocar el dominio.

**Agregar un repo nuevo** = un archivo en `providers/` (heredando de `StaticProvider`
o `DynamicProvider` segun si el sitio necesita JS) + una linea de registro en
`providers/registry.py`. No se toca CLI, servicios ni UI.

**Renderizado de portadas:** `CoverRenderer` no usa ninguna libreria Python de
por medio — llama al binario `kitten icat` (incluido con Kitty) via `subprocess`,
que ya sabe descargar la URL y hablar el protocolo grafico directamente. Se
descarto `term-image` porque fija `Pillow<11`, lo que rompe la instalacion en
Python 3.14 (Pillow 10.x no tiene wheels para esa version). Esto requiere correr
el CLI dentro de una terminal Kitty con `kitten` en el `PATH`.

## Setup

Requiere Kitty como terminal (con `kitten` disponible en el `PATH`).

```bash
uv sync
uv run playwright install chromium   # solo si vas a usar providers dinamicos
cp .env.example .env
```

## Uso

```bash
uv run main.py search run "nombre"
uv run main.py download run <source> <id>
uv run main.py config show
```

## Variables de entorno

Ver `.env.example`:

- `MISFLIX_MOVIES_DIR` — carpeta por defecto para peliculas.
- `MISFLIX_BOOKS_DIR` — carpeta por defecto para libros.

## Tests

```bash
uv run pytest
```

Por ahora cubren `core/` e `infra/filesystem.py` (la logica sin dependencias
externas): `SearchService`, `DownloadService`, `sanitize_filename`/`ensure_dir`
y el `registry` de providers. Los providers reales (scraping) todavia no tienen
tests porque no existen implementaciones concretas — cuando se agregue el primer
provider conviene testear su parseo con HTML/fixtures grabados, no contra la red.
