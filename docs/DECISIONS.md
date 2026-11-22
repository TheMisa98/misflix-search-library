# Decisiones

Registro tipo ADR ligero: decisiones no triviales (nueva dependencia, patrón nuevo,
trade-off elegido), con fecha y motivo. Nunca se descarta una decisión en silencio —
si algo cambia, se agrega una entrada nueva que la reemplaza/anula, la vieja no se
borra.

Las entradas marcadas **(reconstruida)** no fueron escritas en el momento; se
reconstruyeron el 2026-08-10 leyendo el código y el `CLAUDE.md` previo al separar este
archivo, para no perder el motivo detrás de decisiones ya tomadas. Fecha aproximada
cuando no hay commit exacto que la ancle.

## 2026-07-16 — Protocols en `core/ports.py` en vez de herencia de clases concretas (reconstruida)

`SourceProvider`, `SeriesProvider` y `Downloader` son `Protocol`s, no clases base.
**Motivo**: permite que `cli/`/`core/services/` dependan de una interfaz mínima
(`DownloadService` tipado contra `Downloader`, no contra `HttpxDownloader`) sin acoplar
el dominio a una implementación concreta — ver DIP en `CLAUDE.md`. `SeriesProvider` es
`@runtime_checkable` específicamente para que `cli/download.py` pueda hacer
`isinstance(provider, SeriesProvider)` en vez de sondear métodos con `getattr`.

## ~2026-08 — Abandonar `DynamicProvider` (Playwright) para sitios detrás de Cloudflare (reconstruida)

Existía una base `DynamicProvider` pensada para sitios que necesitan JS/navegador. Se
eliminó en favor de `infra/cloudflare.py` (`curl_cffi` impersonando el fingerprint TLS
de un navegador real + reuso del cookie `cf_clearance` de un perfil Firefox/Zen real).
**Motivo**: verificado en vivo que Cloudflare fingerprinta la conexión CDP misma, no
solo `navigator.webdriver` — Playwright nunca pasa un Managed Challenge interactivo en
zona-leros ni siquiera en modo headed con click manual. Ver `docs/ARCHITECTURE.md` §
`zona_leros.py`.

## ~2026-07/08 — Providers se prueban contra fixtures HTML, nunca contra la red real en tests (reconstruida)

`tests/fixtures/<provider>/` guarda HTML real capturado a mano; los tests de parseo
corren contra esos archivos. **Motivo**: reproducibilidad y velocidad — un sitio scrapeado
puede cambiar de HTML o estar caído, y un test no debería depender de eso ni disparar
Cloudflare/rate-limits en cada corrida. El comportamiento que sí depende de la red real
(timing de Cloudflare, redirects, cookies) se verifica a mano durante el desarrollo y
queda documentado en `docs/ARCHITECTURE.md` con la marca "verificado en vivo" — no se
intenta automatizar.

## ~2026-07 — No automatizar E2E (reconstruida)

El flujo completo (resolver un Managed Challenge, pegar links a mano cuando el probe
automático falla, terminal Kitty real) tiene pasos que requieren intervención humana
por diseño, no por falta de tiempo. **Motivo**: proyecto personal sin usuarios
externos — automatizar un navegador real contra un captcha interactivo no es una
inversión razonable aquí. El E2E es un smoke-test manual antes de un cambio grande en
el flujo de descarga.

## 2026-08-10 — Separar `CLAUDE.md` en un índice + `docs/`

El `CLAUDE.md` anterior (un solo archivo, ~30KB) mezclaba reglas de trabajo que
siempre aplican con el detalle línea a línea de cada módulo/provider, que solo hace
falta al tocar esa parte puntual. **Motivo**: mismo patrón ya usado en
`obsidian-librero-3d` (otro proyecto del mismo autor) — un `CLAUDE.md` corto y
estable como índice de reglas, con el detalle que cambia más seguido en
`docs/ARCHITECTURE.md`, `docs/DECISIONS.md` y `docs/GIT_WORKFLOW.md`. Facilita
mantener el detalle actualizado en el mismo cambio que lo motiva, en vez de inflar un
único archivo indefinidamente.

## 2026-08-10 — Adoptar convención de ramas `feature/<slug>` / `fix/<slug>`

Hasta ahora todo el trabajo se commiteó directo a `master` (mas algunas ramas
`test-*` para probar modelos LLM locales, sin relación con este flujo). A partir de
esta fecha se adopta la misma convención de `obsidian-librero-3d`: trabajo nuevo en
`feature/<slug>` o `fix/<slug>`, nunca directo en `master`. **Motivo**: consistencia
entre los proyectos del autor y separar historial de trabajo en curso del de
`master`. Si esto no encaja con el flujo real de un proyecto personal de un solo
colaborador, es la primera candidata a revertirse — anotarlo aquí en vez de
abandonarla en silencio si se descarta.

## 2026-08-12 — Commits chicos en la rama, squash merge a `master`

Dentro de una rama `feature/<slug>`/`fix/<slug>` se pushean commits chicos a medida
que se avanza (checkpoints del trabajo en curso, mensajes cortos tipo WIP están bien).
Al terminar el feature, el merge a `master` se hace con el botón **"Squash and
merge"** de GitHub sobre el PR — nunca merge normal ni rebase — así el PR queda con
el historial granular completo (visible en GitHub aunque se borre la rama), y
`master` recibe un único commit siguiendo la convención completa de mensaje (resumen
imperativo + cuerpo explicando el *por qué*, ver § Convención de commits). **Motivo**:
mantener `git log --oneline` en `master` legible (un commit = un feature/fix
terminado) sin perder la trazabilidad paso a paso del trabajo en curso, que queda en
el PR.

## 2026-08-12 — Detectar la version real de Firefox/Zen en vez de pinearla a mano en `infra/cloudflare.py`

`CloudflareBlockedError` volvió a aparecer contra zona-leros.com incluso justo
despues de resolver el checkbox a mano. Diagnostico en vivo (detalle completo en
`docs/ARCHITECTURE.md` § `infra/cloudflare.py`): Cloudflare ata la cookie
`cf_clearance` al **User-Agent exacto** que resolvio el desafio, no a la huella TLS
que imita `curl_cffi`. Zen se autoactualiza (rolling release) y el
`DEFAULT_USER_AGENT` de este cliente estaba pineado a mano a una version que quedo
desactualizada. **Motivo de la solucion elegida**: en vez de volver a pinear un
numero de version nuevo (que se desactualiza de nuevo en la proxima actualizacion de
Zen — ya paso dos veces en el mismo dia con esta bug), `misflix/infra/browser_version.py`
lee la version real instalada desde el `platform.ini` del navegador (`Milestone=`) en
cada corrida del CLI, asi el User-Agent siempre coincide con el navegador que
realmente resuelve el challenge. El perfil de impersonate de `curl_cffi`
(`DEFAULT_IMPERSONATE`) no necesita coincidir con esa version — resultó no ser lo que
Cloudflare estaba chequeando aca — asi que simplemente toma el `firefoxNNN` mas
nuevo que traiga la version instalada de `curl_cffi`, tambien sin pinear a mano.

## 2026-08-12 — No usar GitHub Actions

Se evaluo agregar `.github/workflows/ci.yml` espejando el hook de pre-push (ruff,
mypy, pytest) como parte de una revision general de calidad del repo. **Motivo del
rechazo**: la cuenta de GitHub del autor tiene un problema de facturacion que hace que
correr Actions genere costo — no tiene sentido pagar por una comprobacion que el hook
de pre-push local ya cubre en cada push. Las comprobaciones quedan solo en local, via
`.githooks/pre-push` (ver `docs/GIT_WORKFLOW.md` § Pre-push hook). No proponer CI de
nuevo salvo que el usuario indique que la situacion de facturacion cambio.

## 2026-11-21 — Búsqueda solo por autor como modo aparte (`search books --author`), no como filtro

La búsqueda normal de lectulandia mezcla resultados por texto + libros de todo autor
emparejado + libros de toda serie emparejada, así que explorar un autor puntual trae
mucho ruido ("isaac asimov" incluye toda la serie *Isaac Asimov Magazine*; "asimov"
incluye a Janet Asimov). Se agregó un modo aparte que lista solo la página del autor
(`/autor/<slug>/`), que el sitio ya expone, en vez de filtrar los resultados por texto
por el campo `author` (descartado: los resultados por texto son solo la primera página
de la búsqueda y no traen todos los libros del autor; la página del autor sí).
**Contrato**: un `Protocol` nuevo `AuthorProvider` en `core/ports.py` que extiende
`SourceProvider` (ISP — igual que `SeriesProvider`, un provider de películas no tiene
por qué implementarlo). **Varios autores emparejados**: el sitio empareja por substring
("king" → 34 autores), así que se elige directo el que coincide exacto con la query
(sin mayúsculas/acentos) y si no, se pregunta — en vez de mezclar los libros de todos.

## 2026-11-21 — Reintentos ante 429/5xx pasajeros en `HttpClient`

Verificado en vivo: recorrer seguido las páginas de un autor largo en lectulandia dio un
503 que al reintentar a los pocos segundos devolvía 200. `HttpClient.get` reintenta
429/502/503/504 hasta 3 veces con backoff exponencial (1s, 2s, 4s). Se hizo en el
cliente HTTP (y no en el provider) porque beneficia a todo `StaticProvider` y no
cambia ningún contrato. Sin dependencia nueva (descartado `tenacity` u otra lib de
retry: el bucle es de 5 líneas).

## 2026-11-22 — Gestor de subtítulos propio (subdivx) en vez de Bazarr

Bazarr ya corre en el stack, pero no logra bajar de subdivx: su provider `subx`
depende de una API de terceros con api key (vacía en la config), la web directa está
detrás de un Cloudflare Managed Challenge, y los subtítulos vienen en `.zip`/`.rar`
(packs de temporada con un `.srt` por episodio) que Bazarr no sabe abrir ni ordenar.
Además el sincronizador que el usuario usaba fallaba con frecuencia. Se construyó
`misflix subs` reutilizando lo que ya resolvía este repo (`infra/cloudflare.py` con la
`cf_clearance` del navegador real, `unrar`). Ver `docs/ARCHITECTURE.md` § Subtítulos.

## 2026-11-22 — Sincronía propia en Python puro, sin ffsubsync ni alass

**Descartados**: `ffsubsync` (lo que usa Bazarr) solo corrige un desfase constante
+ framerate, y su detección de voz corre sobre la mezcla completa — justo las fallas
que describió el usuario ("pone el subtítulo donde empieza a sonar el audio en
general y no las voces"; episodios de House "mal sincronizados"). `alass` sí maneja
cortes, pero es un binario externo (Rust) sin verificación de "este subtítulo es de
otro episodio". **Elegido**: alineación por tramos con programación dinámica
(penalización por corte + salto máximo de 20s), contra el subtítulo de texto
incrustado en el video cuando existe y si no contra la voz del **canal central**
(5.1/7.1), más un veredicto contra un modelo nulo (el subtítulo invertido en el
tiempo). Sin dependencias nuevas (~2s por verificación). Calibrado en vivo con House
S01E01; números en `docs/ARCHITECTURE.md`. Hallazgo de esa calibración: los dos
`.srt` "mal sincronizados" de House S01E01 eran en realidad de otra serie (*Safe
House*), no un desfase — por eso el veredicto distingue `no_coincide` de `corregible`.

## 2026-11-22 — Verificar contra audio por arranques de voz, no por cobertura

La alineación contra audio maximiza la cobertura (fracción de cada línea sobre voz),
pero como métrica de verificación se dejaba engañar: un subtítulo de otra serie
lograba +0.16 sobre el nulo eligiendo un framerate y un desfase de −205s que hacían
coincidir sus zonas densas de diálogo. La verificación usa entonces la fracción de
líneas que arrancan junto a un arranque de voz (+0.34…+0.43 correctos vs +0.00…+0.05
de otra serie), y el desfase contra audio se limita a ±120s.

## 2026-11-22 — Estado en SQLite (stdlib), caché aparte

Pedido por el usuario ("¿sqlite, mongo, postgres o json?"). SQLite porque el daemon y
los comandos manuales escriben a la vez (WAL + timeout serializan; un JSON reescrito
entero perdería escrituras o quedaría corrupto si se corta), se puede consultar, y
viene con Python — Mongo/Postgres serían un servidor más para ~2000 filas. El estado
(`~/.local/state/misflix/subs.db`) y la caché regenerable
(`~/.cache/misflix/subs/`: referencias extraídas, archivos bajados) están separados:
borrar la caché solo obliga a releer videos.

## 2026-11-22 — Nunca borrar subtítulos: respaldar, y solo reemplazar por algo verificado

Todo `.srt` que se corrige o descarta se mueve a `~/.local/state/misflix/subs-respaldo/
<fecha>/<ruta original>` y queda registrado en la tabla `actions`. Un subtítulo
existente solo se reemplaza si el nuevo verifica (`sincronizado`/`corregible`); uno
`dudoso` no se toca. Si ningún candidato verifica y el video no tiene nada en español,
el mejor dudoso se escribe como `<video>.es.dudoso.srt` (marcado, no como si fuera
bueno).

## 2026-11-22 — `.rar` con `unrar`, no `7z`

El `7z` de Fedora no trae el códec RAR (verificado en vivo: "Cannot open the file as
archive" con `.rar` de subdivx que `unrar` abre bien). `unrar` ya era requisito del
repo (zona-leros). Se extrae con `unrar e` (sin rutas) por seguridad: el contenido es
de terceros.

## 2026-11-22 — Daemon con polling + servicio de systemd de usuario

La biblioteca está en NTFS vía `fuseblk`, que no emite eventos de inotify: el daemon
hace una pasada cada 15 min (con las cachés de ffprobe/referencias, una pasada sin
trabajo es casi gratis) del video más nuevo al más viejo. Si subdivx queda bloqueado
por Cloudflare (nadie resolvió el captcha), no reabre el navegador en cada pasada:
espera 1h, 2h, 4h… hasta 8h, y mientras tanto sigue verificando/corrigiendo lo que no
necesita red.

## 2026-11-22 — Detectar doblajes y subtítulos quemados escuchando y leyendo el video

Los metadatos no alcanzan (verificado en vivo): *The Aviator* trae una sola pista de
audio sin idioma que está doblada, y *Kimetsu no Yaiba* T3 trae audio `jpn` bien
etiquetado con subtítulos en español dibujados en la imagen. Sin esto el gestor les
bajaba subtítulos que no hacían falta. **Elegido**: paquetes de los repos oficiales de
Fedora (como `ffmpeg`/`unrar`, nada de pip): `whisper-cpp` para identificar el idioma
hablado y `tesseract` + `tesseract-langpack-spa` para leer la franja inferior de algunos
fotogramas. El paquete `whisper-cpp` de Fedora trae solo la librería (`libwhisper.so`,
sin CLI), así que se usa con `ctypes` (stdlib) en un proceso aparte, con el modelo
multilingüe "base" (~148MB, se baja una vez de Hugging Face a la caché).
**Descartados**: `openai-whisper` (arrastra PyTorch, varios GB), `faster-whisper`
(CTranslate2, sin GPU AMD) y compilar whisper.cpp a mano. Whisper solo escucha pistas
**sin** etiqueta de idioma (en CPU cuesta ~10s por fragmento; a un `eng`/`jpn` se le
cree). Corre *antes* de la lectura completa del video (la parte cara), sobre fragmentos
y fotogramas sueltos con búsqueda directa, y se cachea. Si falta una herramienta, esa
detección se saltea y el video se reevalúa cuando aparece.

## 2026-11-22 — Whisper en CPU: el backend HIP no sirve en la RX 5700 XT

**Verificado en vivo**: el `whisper-cpp` de Fedora viene compilado con HIP (ROCm). En
la RX 5700 XT (gfx1010, RDNA1, sin soporte oficial de ROCm) el cómputo tira SIGFPE; con
`HSA_OVERRIDE_GFX_VERSION=10.3.0` se cuelga (más de 10 minutos sin responder). Por eso
`whisper_lid.py` corre en un proceso aparte con `ROCR_VISIBLE_DEVICES=-1`/
`HIP_VISIBLE_DEVICES=-1`: en CPU da los mismos idiomas (The Aviator español 93-98%, House
inglés 95-99.8%, Kimetsu japonés 97-99%) a ~10s por fragmento, y si algún día la
librería nativa falla, se cae ese proceso y no el daemon.

## 2026-11-22 — GPU: aceleración por hardware donde hay video que decodificar

Pedido del usuario: usar la GPU para todo lo que sea decodificar video. Lo único que
decodifica imagen es la lectura de fotogramas para el OCR, que usa
`-hwaccel auto` (la GPU si el driver la soporta, si no la CPU, sin fallar). La
sincronía nunca decodifica video (`ffmpeg -vn`). **Verificado en vivo**: con el
`mesa-va-drivers` de Fedora, VAAPI en la RX 5600/5700 no decodifica H.264 ("No
support for codec h264 profile 100") — hace falta `mesa-va-drivers-freeworld` de
RPM Fusion; para unos pocos fotogramas por video la diferencia es chica.

## 2026-11-22 — Una sola pasada de subtítulos a la vez (`flock`)

Con el servicio corriendo solo y el usuario pudiendo lanzar `misflix subs run` a mano,
dos pasadas podían procesar el mismo video a la vez (mover/reescribir el mismo `.srt`).
SQLite serializa la base, pero no los archivos de la biblioteca. Se usa un `flock`
exclusivo y no bloqueante sobre `subs.lock` junto a la base: es del kernel, se libera
solo si el proceso muere (no quedan candados huérfanos) y no agrega dependencias. La
pasada que no lo obtiene no hace nada (`busy`).

## 2026-11-22 — Probar los episodios vecinos cuando el exacto no verifica

Verificado en vivo: en House T6 todos los episodios desde el E03 quedaban
`no_encontrado`. La biblioteca (numeración de TVDB) parte el estreno doble "Broken" en
E01+E02, y los subtítulos de subdivx de 2009 siguen la numeración de la emisión, donde es
un solo E01: cada "S06Eyy" de subdivx es el E(yy+1) local. Alternativas descartadas:
mapear numeraciones con TVDB/TMDB (otra API y otra dependencia, y no dice qué numeración
usó cada uploader) o buscar por título de episodio (subdivx casi nunca lo pone en el
título del candidato). Elegido: si nada del código exacto verifica, probar los candidatos
de E−1 y E+1 con el mismo verificador, que es el que descarta los que no son (un
subtítulo de otro episodio da ~0). Cuesta poco: las búsquedas y descargas de un vecino
casi siempre ya están en caché, porque son las de ese mismo episodio.

## 2026-11-22 — Verificar por significancia estadística, no por un umbral fijo de puntaje

El umbral fijo (coincide desde +0.30 sobre el modelo nulo) se había calibrado con solo
House S01E01, cuyos subtítulos correctos eran traducciones línea por línea del inglés
(+0.60). Verificado en vivo con House T6: las subidas de 2009, sincronizadas a mano con
~0.35s de imprecisión por línea, son del episodio correcto (la alineación queda con
residuo mediano ~0 en todo el episodio) pero dan +0.12…+0.30, así que quedaban dudosas o
descartadas. Y bajar el umbral no sirve: con pocas líneas el azar llega lejos (+0.24 con
25 líneas de otro episodio). Se calibró con 1322 pares reales y subtítulos recortados
(detalle en `docs/ARCHITECTURE.md` § Sincronía y verificación): en desvíos estándar
sobre el azar, los subtítulos de otro episodio no pasan de 2.5 salvo 3 casos de hasta
4.4 (el de 2 horas de un estreno doble contra otros episodios; 3.5 con pocas líneas) y
los correctos dan 6…35. Regla: coincide desde 6 desvíos y +0.10; no coincide por debajo
de 3. Con los mismos pares, los correctos aceptados pasan de 181 a 242 de 251 contra el
subtítulo inglés y de 32 a 51 de 88 contra la voz, sin ningún falso positivo en 1352
negativos (971 contra el subtítulo, 381 contra la voz). Alternativas medidas y descartadas: métricas que toleran otra segmentación
de líneas (contar también finales que coinciden, continuaciones de una frase partida,
recall de la referencia) suben la señal de los correctos pero también la cola del azar;
en desvíos no separan mejor que la métrica actual, que se mantiene.

## 2026-11-22 — La voz sola nunca saca un subtítulo de la biblioteca

Verificado en vivo: con la verificación anterior, cuando el subtítulo inglés daba
"dudoso", la voz como segunda opinión podía decidir "no coincide", y así se mandaron al
respaldo 48 subtítulos correctos de House que el usuario había bajado a mano (con la
regla nueva verifican todos). Contra la voz sola el 14% de los subtítulos correctos es
indistinguible del azar: alcanza para confirmar, no para descartar. Ahora la voz como
segunda opinión solo confirma, y un "no coincide" que viene solo de la voz deja el
archivo donde está (marcado dudoso) hasta que aparezca uno verificado que lo reemplace.
Para reparar lo hecho, si un video no tiene subtítulo bueno se re-verifican sus
respaldos por "no corresponder" y se restauran los que la verificación actual no
descartaría; no se restaura donde ya hay uno verificado.

## 2026-11-22 — Versión de la verificación en la base

Cada mejora de la verificación cambia qué candidatos sirven, pero los intentos fallidos
quedaban registrados para siempre y los videos `dudoso`/`no_encontrado` esperaban 7
días. Videos e intentos guardan ahora la versión con que se evaluaron
(`VERIFIER_VERSION`, columna `verifier` con migración como la de `detected`): al
subirla, la pasada siguiente reevalúa esos videos y vuelve a verificar los candidatos
que fallaron. Solo cuesta CPU: las descargas están en caché.

## 2026-11-22 — Avisos de escritorio: uno por video nuevo, el resto agrupado y solo si algo cambió

El usuario quiere enterarse cuando el servicio procesa un episodio o película nueva. El
aviso que había era uno al final de cada pasada contando estados ("2 descargado,
1 dudoso"): no decía de qué video y contaba también los reintentos que no cambiaban
nada, así que cada reintento semanal de un dudoso volvía a avisar "1 dudoso".
`SubtitleService.run` ahora entrega cada video con su estado de antes y el de después
(`on_result`), y la CLI decide los avisos. "Nuevo" es "nunca evaluado" (sin fila en la
base): no hace falta guardar nada más, y un archivo renombrado cuenta como nuevo, lo
cual está bien porque para el servicio lo es. Cada video nuevo avisa apenas termina,
con su resultado, hasta 5 por pasada. Agregar una temporada entera, o la primera
pasada sobre una biblioteca grande, terminaría en decenas de avisos: el resto va
contado en un solo aviso al final. Ese aviso también incluye lo que cambió los
subtítulos de videos ya conocidos: bajados, resincronizados, dudosos nuevos o
restaurados. Un video que sigue igual no avisa.

## 2026-11-22 — Configuración del usuario en `~/.config/misflix/.env`

Verificado en vivo: el `misflix` instalado con `uv tool install` no leía el `.env` del
repo. `load_dotenv()` sin argumentos busca el archivo subiendo desde el código que lo
llama, y el instalado vive en la carpeta de herramientas de uv. Sin las carpetas
configuradas usaba las de `~/Descargas`. Así, `misflix subs run RUTA` evaluaba 0
videos sin avisar, y una descarga de películas o series habría ido a
`~/Descargas/Peliculas|Series` en vez de a la biblioteca. No pasó: esas carpetas no
existen. El servicio no se veía afectado porque su unidad de systemd trae las rutas.

Ahora la configuración también se lee de `~/.config/misflix/.env` (`$XDG_CONFIG_HOME`,
el lugar estándar para la configuración del usuario), corra desde donde corra el
comando. El `.env` del repo se busca en una ruta fija junto al código (ya no subiendo
directorios) y sigue teniendo prioridad al correr con `uv run`. Ninguno pisa las
variables de entorno reales. `config show` dice qué archivos se leyeron, y `subs run`
rechaza una ruta fuera de la biblioteca en vez de no hacer nada en silencio.
Descartado: grabar las rutas en el binario al instalarlo. Habría que reinstalar para
cambiar una carpeta.
