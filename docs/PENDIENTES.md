# Pendientes del proyecto

Solo lo que **hay que hacer**. Corte: **2026-11-22**. Lo que se cierra se borra de aquí en
la misma rama que lo cierra. Lo que se decide al hacerlo va a `docs/DECISIONS.md`, y cómo
queda hecho, a `docs/ARCHITECTURE.md`.

**Estado** dice quién tiene la siguiente acción: *listo para trabajar* o *espera al
usuario*.

| # | Área | Qué hay que hacer | Estado | Fuente |
|---|---|---|---|---|
| `P-1` | Subtítulos | **Verificar mejor contra la voz.** Contra la voz sola, el 14 % de los subtítulos correctos no se distingue del azar (calibración del 2026-11-22). Por eso 30 subtítulos restaurados siguen `dudoso`: de Grimm, The Office, Silicon Valley, The Bear y una película, todos sin subtítulo inglés con qué comparar. Propuesta: detectar el habla con el VAD de Silero que trae libwhisper 1.8.1 (`whisper_vad_*`) en vez de con los niveles de volumen, recalibrar y subir `VERIFIER_VERSION` para que los dudosos se reevalúen solos | listo para trabajar | [La voz sola nunca saca un subtítulo](DECISIONS.md) (2026-11-22) |
| `P-2` | Infraestructura | **Dockerizar.** Propuesta: empezar por el servicio de subtítulos, porque el CLI interactivo necesita la terminal Kitty del host para las portadas. Puntos abiertos en las notas de abajo | listo para trabajar | pedido del usuario (2026-11-22) |
| `P-3` | Infraestructura | **Base de datos real (PostgreSQL o MongoDB)** en vez de SQLite. Hoy solo `misflix subs` guarda estado, en `~/.local/state/misflix/subs.db`: videos, intentos, acciones y búsquedas en caché. Va de la mano con `P-2` (la base en su propio contenedor) | espera al usuario (elegir PostgreSQL o MongoDB) | pedido del usuario (2026-11-22), `misflix/infra/subtitle_store.py` |

## Notas para arrancar

### P-1 — Verificar mejor contra la voz

- Hoy el habla se detecta por volumen: `detect_speech` (`core/subtitles/speech.py`)
  trabaja sobre los niveles que extrae ffmpeg. La verificación compara los inicios de
  línea del subtítulo con esos tramos (`check_against_speech` en `core/subtitles/sync.py`).
- El VAD de Silero de whisper.cpp necesita su propio modelo en formato ggml, aparte
  del de whisper.
- Hay que recalibrar como el 2026-11-22, con pares subtítulo–video correctos y de otro
  episodio sacados de la biblioteca. La regla tiene que seguir sin aceptar ningún
  subtítulo de otro episodio.
- Al subir `VERIFIER_VERSION`, la pasada siguiente reevalúa los `dudoso` y
  `no_encontrado`. Los intentos fallidos se vuelven a verificar sin bajar nada: las
  descargas están en caché.

### P-2 — Dockerizar

- **Dependencias del sistema:** `ffmpeg`/`ffprobe`, `unrar`, `whisper-cpp` y
  `tesseract` + `tesseract-langpack-spa`.
- **Cloudflare:** hoy el servicio abre el navegador del host (Zen/Firefox) y lee la
  cookie `cf_clearance` de su perfil (`infra/cloudflare.py`,
  `infra/browser_cookies.py`). Desde un contenedor no puede abrir ese navegador.
  Opción: montar el perfil de solo lectura y avisar para que la verificación se
  resuelva en el host.
- **Avisos de escritorio:** `notify-send` necesita el bus de sesión D-Bus del host.
- **Carpetas:**
  - la biblioteca (`/mnt/misflix`, NTFS vía `fuseblk`): montarla con el mismo uid
    para no romper permisos;
  - estado, caché y configuración (`~/.local/state/misflix`, `~/.cache/misflix`,
    `~/.config/misflix/.env`): como volúmenes.
- **GPU (opcional):** el OCR decodifica unos pocos fotogramas con `-hwaccel auto`;
  para usar VAAPI hay que pasar `/dev/dri`.
- **Servicio actual:** corre como servicio de usuario de systemd
  (`misflix subs install-service`). Falta decidir si el contenedor lo reemplaza.

### P-3 — Base de datos real

- El acceso a la base está todo en `infra/subtitle_store.py`, que hoy es una sola
  implementación sin `Protocol`. Si convive con SQLite (por ejemplo, para los tests),
  hace falta un `Protocol` en `core/ports.py`: es la regla DIP de `CLAUDE.md`.
- Los datos son tablas relacionadas (un video tiene intentos y acciones) y el esquema
  ya se migra al arrancar. Eso encaja más con PostgreSQL que con MongoDB.
- El stack está cerrado: antes de agregar el driver, registrar la decisión en
  `docs/DECISIONS.md`.
- Hay que pasar los datos de la `subs.db` actual al motor nuevo. Si no, se pierden los
  intentos ya probados y el registro de respaldos, que es lo que permite restaurar.
