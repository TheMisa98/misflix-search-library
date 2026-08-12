# Flujo de git

## Ramas

Trabajo nuevo en `feature/<slug>` o `fix/<slug>`, nunca directo en `master` (ver
decisión del 2026-08-10 en `docs/DECISIONS.md` — antes de esa fecha todo se commiteó
directo a `master`). Nombre corto, en inglés o español, que describa el cambio
(`feature/lectulandia-author-pages`, `fix/season-pack-part-detection`).

### Commits chicos en la rama, squash merge a `master`

Mientras se trabaja en la rama, se pushean commits chicos a medida que se avanza —
son checkpoints del trabajo en curso, no hace falta que cada uno siga la convención
completa de mensaje (un WIP corto está bien). Sirven como historial de lo que se fue
haciendo y como respaldo remoto del trabajo en curso.

Cuando el feature/fix está completo, el merge a `master` se hace con **"Squash and
merge"** en el PR de GitHub (nunca merge normal ni rebase). Recién ahí se escribe el
commit grande que sí sigue la convención completa (resumen imperativo + cuerpo
explicando el *por qué*) y que queda como el único commit de ese feature en
`git log --oneline` de `master`. El historial granular de la rama sigue visible en el
PR de GitHub aunque la rama se borre después del merge. Ver decisión del 2026-08-12 en
`docs/DECISIONS.md`.

## Convención de commits

- Línea de resumen: modo imperativo, sin punto final (ej. `Document architecture and
  commands in CLAUDE.md`, `Scaffold CLI architecture with layered providers and add
  initial test suite`). Dice *qué* cambió, corto para leerse bien en `git log
  --oneline`.
- Línea en blanco, luego un cuerpo explicando *por qué* — la motivación/contexto, no
  una repetición del diff. Para un commit que toca varias cosas no relacionadas,
  partir el cuerpo en bullets `- ` (uno por asunto) en vez de un párrafo grande.
- Nunca agregar `Co-Authored-By: Claude` (ni ninguna variante) — el autor siempre es
  el usuario.
- No hacer commits ni push salvo que se pida explícitamente (regla general del
  asistente, reafirmada aquí por ser repo personal).

## Pre-push hook

`.githooks/pre-push` corre `ruff check`, `ruff format --check`, `mypy` y `pytest`
antes de cada `git push`, y cancela el push si algo falla. Git no lee hooks de
`.githooks/` por sí solo — este repo ya apunta `core.hooksPath` ahí, pero un clon
nuevo necesita habilitarlo una vez:

```bash
git config core.hooksPath .githooks
```

Nunca usar `--no-verify` para saltarse el hook, salvo que el usuario lo pida
explícitamente.
