# Flujo de git

## Ramas

Trabajo nuevo en `feature/<slug>` o `fix/<slug>`, nunca directo en `master` (ver
decisión del 2026-08-10 en `docs/DECISIONS.md` — antes de esa fecha todo se commiteó
directo a `master`). Nombre corto, en inglés o español, que describa el cambio
(`feature/lectulandia-author-pages`, `fix/season-pack-part-detection`).

La rama se crea automáticamente en cuanto se identifica que la tarea es un fix o
feature — antes de empezar a escribir código, sin pedir permiso para crearla — para
no terminar construyendo directo sobre `master` por accidente.

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

Al pushear una rama por primera vez, se crea el PR en el momento con
`gh pr create --title "..." --body "..."` — nunca se deja que el usuario tenga que
entrar a GitHub a crearlo a mano ni a escribirle el resumen desde cero. Título y
cuerpo siguen la misma convención que un commit grande (ver más abajo: resumen
imperativo + *por qué*), así el PR ya queda con contenido real de entrada, listo para
editar si hace falta y mergear con squash cuando el feature esté completo.

El mismo `gh pr create` (o un `gh pr edit` después) también deja:

- **Assignee**: siempre el usuario (`--add-assignee @me`).
- **Label**: uno según el prefijo de la rama — `fix/<slug>` → `bug`, `feature/<slug>`
  → `enhancement`, salvo que el cambio sea solo de documentación (ej. archivos bajo
  `docs/`), en cuyo caso va `documentation` independientemente del prefijo.
- **Reviewers/Projects/Milestone**: no aplican en este repo (un solo colaborador, sin
  milestones ni projects configurados) — se dejan vacíos salvo que el usuario pida
  algo puntual.

### Limpieza local de ramas

La rama local se borra apenas está pusheada y su PR ya creado — **no hace falta
esperar a que se mergee**. Una vez pusheada, la rama vive respaldada en GitHub (el PR
la mantiene viva aunque se borre en local); si hace falta retomarla, se trae de
vuelta con `git fetch origin <rama> && git switch -c <rama> origin/<rama>`.

```bash
git switch master
git branch -D <rama>       # -D: todavia no esta mergeada a master, -d la rechazaria
```

Al final de una sesión con varias ramas de por medio, de paso:

```bash
git pull --ff-only
git remote prune origin    # saca del local las ramas remotas que GitHub ya borro (post-merge)
```

Las ramas que no sean de este flujo (ej. `test-*`, experimentos sueltos) no se tocan.

## Convención de commits

- **Siempre en español** (ver regla de Idioma en `CLAUDE.md` — aplica también a
  mensajes de commit y a título/cuerpo de PRs, no solo a las respuestas al usuario).
- Línea de resumen: modo imperativo, sin punto final (ej. `Documentar arquitectura y
  comandos en CLAUDE.md`, `Separar CLI en capas con providers y agregar suite de
  tests inicial`). Dice *qué* cambió, corto para leerse bien en `git log --oneline`.
- Línea en blanco, luego un cuerpo explicando *por qué* — la motivación/contexto, no
  una repetición del diff. Para un commit que toca varias cosas no relacionadas,
  partir el cuerpo en bullets `- ` (uno por asunto) en vez de un párrafo grande.
- Nunca agregar `Co-Authored-By: Claude` (ni ninguna variante) — el autor siempre es
  el usuario.

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
