from __future__ import annotations


def escape_postgrest_literal(value: str) -> str:
    """Escape a value for safe embedding in a PostgREST filter expression
    (e.g. the string passed to `.or_()`).

    PostgREST's filter mini-language treats `,`, `.`, `(`, `)` as syntax
    (they separate/nest conditions), so a raw user value containing them can
    inject extra filter conditions. Wrapping the value in double quotes turns
    it into a literal; backslash and double-quote must then be escaped so
    they can't terminate the quoted literal early.
    """
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'
