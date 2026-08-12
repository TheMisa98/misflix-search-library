# misflix-search

CLI interactivo para buscar y descargar peliculas, series y libros desde tus propios
repos, con vista previa de portadas directamente en la terminal (protocolo grafico de
Kitty).

## Estado

Providers reales funcionando: `zona-leros` (peliculas y series, detras de Cloudflare)
y `lectulandia` (libros epub/pdf). El flujo completo esta implementado: busqueda con
portadas en grilla, eleccion de temporada completa o episodio suelto para series,
resolucion de los links finales (paso manual por el navegador cuando el sitio lo
exige), descarga con progreso, extraccion automatica de `.rar` y organizacion final de
los videos.

Documentacion mas detallada (mapa de modulos completo, comportamiento "verificado en
vivo" de cada provider, registro de decisiones de arquitectura y el flujo de git) vive
en `docs/`:

- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — mapa de modulos, limites entre capas
  y el detalle linea a linea de cada provider/flujo.
- [`docs/DECISIONS.md`](docs/DECISIONS.md) — registro de decisiones tipo ADR ligero.
- [`docs/GIT_WORKFLOW.md`](docs/GIT_WORKFLOW.md) — ramas, convencion de commits, hook
  de pre-push.

## Arquitectura

El proyecto separa el dominio (que hace la app) de los detalles tecnicos (como lo hace),
para que agregar un repo nuevo no obligue a tocar el CLI ni la UI.

```
misflix_search/
├── main.py                        # entry point, llama a misflix.cli.app
├── misflix/
│   ├── cli/                       # Typer: parseo de comandos, nada de logica pesada
│   │   ├── app.py                 # Typer() raiz, registra subcomandos
│   │   ├── search.py              # `misflix search movies|books <query>` (+ descarga inline)
│   │   ├── download.py            # `misflix download movies|books <source> <id>`, flujo completo
│   │   └── config.py              # `misflix config show`
│   │
│   ├── core/                      # dominio puro, sin dependencias externas
│   │   ├── models.py              # Media, DownloadOption, MediaKind (movie/series/book)
│   │   ├── ports.py               # Protocols: SourceProvider, Downloader, CoverRenderer
│   │   └── services/
│   │       ├── search_service.py    # reparte una busqueda entre providers
│   │       └── download_service.py  # descarga, resuelve nombre de carpeta, extrae .rar
│   │
│   ├── providers/                 # un modulo por repo/fuente scrapeable
│   │   ├── base.py                # StaticProvider (httpx+bs4)
│   │   ├── registry.py            # nombre -> instancia de provider
│   │   ├── zona_leros.py          # peliculas + series, detras de Cloudflare
│   │   └── lectulandia.py         # libros (epub/pdf)
│   │
│   ├── infra/                     # detalles tecnicos (red, extraccion, filesystem)
│   │   ├── http_client.py         # wrapper de httpx (sitios sin bot-protection)
│   │   ├── cloudflare.py          # bypass de Cloudflare via cookie + curl_cffi
│   │   ├── browser_cookies.py     # lee cf_clearance de Firefox/Zen
│   │   ├── browser_launch.py      # abre el navegador real del usuario
│   │   ├── browser_version.py     # detecta la version real de Firefox/Zen instalada
│   │   ├── mediafire.py           # resuelve el link directo de una pagina de Mediafire
│   │   ├── antupload.py           # descarga autenticada de antupload.com (lectulandia)
│   │   ├── imdb.py                # resuelve titulo/anio canonico via IMDb (nombre de carpeta)
│   │   ├── archives.py            # extrae .rar (unrar) y ordena los videos resultantes
│   │   ├── downloader.py          # descarga en streaming con progreso
│   │   ├── filesystem.py          # sanitizar nombres, crear carpetas
│   │   └── soup.py                # helpers de tipado sobre BeautifulSoup
│   │
│   ├── ui/                        # presentacion en terminal
│   │   ├── image_render.py        # portadas en grilla via `kitten icat` (protocolo Kitty)
│   │   ├── views.py               # resultados de busqueda (cards + portada)
│   │   └── prompts.py             # elegir opcion/temporada/episodio, pegar links, progreso
│   │
│   └── config/
│       └── settings.py            # settings desde .env (carpetas de descarga, etc.)
│
├── docs/                          # wiki interna (arquitectura, decisiones, flujo de git)
├── tests/
│   └── fixtures/                  # HTML grabado para testear el parseo sin red
│       ├── zona_leros/
│       └── lectulandia/
└── .env.example
```

**Regla de dependencia:** `core/` no importa clases concretas de `providers/` ni de
`ui/` — solo define contratos (`Protocol`) en `ports.py`. Con `infra/` la distincion es
mas fina: lo intercambiable en runtime (`Downloader`, `HttpGetClient`) va via
`Protocol`; una utilidad tecnica de una sola implementacion (filesystem, extraccion de
`.rar`, IMDb) se compone directo desde `core/services/`. `cli/` no importa `infra/` en
absoluto — todo lo que necesita de ahi se re-exporta desde `core/services/`. Ver el
detalle completo en [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

**Agregar un repo nuevo** = un archivo en `providers/` (heredando de `StaticProvider`)
+ una linea de registro en `providers/registry.py`. Si el sitio ofrece series,
alcanza con sumar `get_seasons` / `get_episodes` / `get_season_download_options` — son
opcionales en `SourceProvider` y se detectan con `getattr`, asi que un provider de solo
peliculas no necesita implementarlos. No se toca CLI, servicios ni UI. Si el sitio esta
detras de Cloudflare, conviene reusar `infra/cloudflare.py` en vez de Playwright: un
Managed Challenge fingerprinea la conexion CDP misma, no solo `navigator.webdriver`,
asi que Playwright no lo esquiva ni con click manual.

**Flujo de descarga de series:** una ficha de serie no tiene links propios. Siempre se
pregunta que bajar, entre hasta 5 opciones (las que no aplican para esa serie ni se
muestran):

- **Una temporada** (solo si el repo ofrece algun pack armado): la pagina del pack trae
  un `.rar` **independiente y completo por episodio** (no un unico archivo partido en
  volumenes), asi que cada uno se baja y se extrae por separado — igual que un episodio
  suelto, terminando en `Series/<Serie>/Season NN/<Serie> - SxxEyy.ext` (el numero de
  episodio sale del propio nombre del archivo/link, si lo trae, o si no del orden en que
  se pegaron los links).
- **Toda la serie, por packs de temporada** (solo si hay mas de una temporada con pack):
  repite la descarga anterior para cada temporada con pack, una atras de la otra, sin
  volver a preguntar — mismo destino que si se hubiera bajado esa temporada sola, asi
  que da igual por cual de las dos vias se bajo.
- **Episodio suelto**: se elige uno de la lista y se baja como una descarga unica (busca
  el video mas grande dentro del `.rar` y lo renombra a `Serie - SxxEyy`, en
  `Series/<Serie>/Season NN/`; si lo que bajo Mediafire no era un `.rar` sino el video
  ya sin comprimir — pasa a veces — tambien lo reconoce y organiza igual).
- **Toda una temporada** o **toda la serie, episodio por episodio**: para series (o
  sitios enteros, como la mayoria de `zona-leros`) que *no* tienen ningun pack armado.
  Encola todos los episodios y repite el flujo de descarga uno atras del otro sin
  volver a preguntar.

Los dos modos "en lote de mas de uno" (varias temporadas con pack, o varios episodios
sueltos) por lo general no eliminan el paso manual del navegador — cada pagina exige un
Turnstile nuevo, asi que hay que resolverlo y pegar los links por cada elemento — pero
evitan relanzar el comando y reelegir todo a mano: preguntan una sola vez si arrancar,
reusan el mismo servidor (MEGA/MEDIAFIRE) elegido para el primer elemento en los
siguientes cuando esta disponible, y siguen con el resto si uno falla o se salta en vez
de abortar todo. Elegir una sola temporada o un solo episodio usa exactamente el mismo
codigo (un "lote de uno"), asi que el resultado en disco es identico sin importar cual
de los caminos se uso.

**Sin navegador cuando se puede evitar:** todo boton MEGA/MEDIAFIRE de zona-leros pasa
por su ad-locker (`anomizador.zona-leros.com`) — pero no todos exigen resolver un
captcha ahi: los de una pelicula si, comprobado, pero los de bastantes episodios
resultan ser nada mas que una cadena de redirects HTTP derecho a Mediafire, sin ningun
desafio real de por medio. Por eso, antes de pedirte que abras el navegador, el CLI
prueba resolver el link solo (un pedido rapido y sin escalar a nada si falla); si
termina en Mediafire, lo baja directo sin que tengas que tocar nada. Si el link
realmente necesita el captcha (lo normal para peliculas y packs de temporada, y para
algunos episodios), sigue pidiendo el paso manual como siempre — este intento previo
nunca abre una pestaña por su cuenta.

La carpeta de cada serie sigue la misma convencion Plex/Kodi que ya usa el resto de la
biblioteca (`Season NN` en ingles, con 2 digitos, sin importar el idioma del titulo):
`Series/<Serie>/Season NN/<Serie> - SxxEyy.ext`, tanto para un episodio suelto como para
cada episodio de un pack de temporada.

**Links caidos:** un link de Mediafire puede estar caido (dominio no responde) o el
archivo puede haber sido dado de baja (la pagina carga pero sin boton de descarga). En
cualquiera de los dos casos se avisa con un mensaje en vez de cortar todo el proceso con
un traceback; en una descarga en lote (varios episodios o varias temporadas), ese item
puntual se salta y el resto sigue bajando normalmente.

**Renderizado de portadas:** la portada de una pelicula/serie es un poster vertical
(~2:3), pero la de un episodio suelto es una miniatura horizontal tipo screenshot
(~3:2) — meterlas en la misma caja de imagen le queda mal a una de las dos, asi que la
portada de un episodio usa una caja mas ancha que alta en vez de al reves.

`CoverRenderer` no usa ninguna libreria Python de por
medio — llama al binario `kitten icat` (incluido con Kitty) via `subprocess`, ubicando
cada card en una grilla real con `--place` y consultas de posicion del cursor. Se
descarto `term-image` porque fija `Pillow<11`, lo que rompe la instalacion en Python
3.14 (Pillow 10.x no tiene wheels para esa version). Esto requiere correr el CLI dentro
de una terminal Kitty con `kitten` en el `PATH`.

## Setup

Requiere Kitty como terminal (con `kitten` disponible en el `PATH`) y el binario
`unrar` (para extraer las descargas).

```bash
uv sync
cp .env.example .env
```

## Uso

```bash
uv run main.py search movies "nombre"          # busca peliculas/series, muestra portadas y permite descargar sin salir
uv run main.py search books "nombre"           # idem, pero solo libros
uv run main.py download movies <source> <id>   # descarga directo, si ya tenes el id
uv run main.py download books <source> <id>    # idem, para un libro
uv run main.py config show
```

Despues de mostrar resultados, `search movies`/`search books` pregunta que descargar. Si
escribis un numero valido, descarga ese resultado y vuelve a preguntar; cualquier otra
cosa — texto, o un numero fuera de rango — se toma como una busqueda nueva (no hace falta
salir y volver a correr el comando si no encontraste lo que buscabas a la primera).
Dejarlo en blanco es lo unico que termina el comando.

## Variables de entorno

Ver `.env.example`:

- `MISFLIX_MOVIES_DIR` — carpeta por defecto para peliculas.
- `MISFLIX_SERIES_DIR` — carpeta por defecto para series.
- `MISFLIX_BOOKS_DIR` — carpeta por defecto para libros.

## Tests

```bash
uv run pytest
```

Cubren `core/` (servicios, con el patron de fake objects para no depender de red ni
terminal), `infra/` sin red real (`filesystem`, `archives`, `imdb`, `mediafire`,
`browser_cookies`, `browser_launch`, `browser_version`, `http_client`, `soup`) y el
parseo HTML de los providers reales (`zona_leros`, `lectulandia`) contra los fixtures
grabados en `tests/fixtures/` en vez de contra la red.
