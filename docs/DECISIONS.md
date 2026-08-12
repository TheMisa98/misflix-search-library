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
