from __future__ import annotations

from bs4.element import Tag


def attr(tag: Tag, name: str) -> str:
    """Lee un atributo HTML de `tag` como string.

    Los stubs de BeautifulSoup tipan cualquier atributo como
    `str | list[str]` porque, en teoria, un atributo HTML puede traer varios
    valores (ej. `class="a b"`); en la practica, los atributos que este
    proyecto lee (`href`, `src`, `style`) siempre son un unico string.

    Args:
        tag: Elemento del que leer el atributo. Debe tenerlo (usar
            `tag.get(name)` antes si puede faltar).
        name: Nombre del atributo.

    Returns:
        El valor del atributo como string.
    """
    value = tag[name]
    return value if isinstance(value, str) else "".join(value)
