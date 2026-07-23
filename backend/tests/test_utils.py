from __future__ import annotations

from utils import escape_postgrest_literal


def test_escape_plain_value_is_quoted():
    assert escape_postgrest_literal("hello") == '"hello"'


def test_escape_neutralizes_comma_injection():
    # A raw comma would otherwise let a value break out of an `or_()`
    # expression and append extra filter conditions.
    out = escape_postgrest_literal('x,id.neq.0')
    assert out == '"x,id.neq.0"'
    assert "," in out  # comma survives, but only inside the quoted literal


def test_escape_handles_double_quotes():
    assert escape_postgrest_literal('say "hi"') == '"say \\"hi\\""'


def test_escape_handles_backslashes():
    assert escape_postgrest_literal("a\\b") == '"a\\\\b"'


def test_escape_preserves_ilike_wildcards():
    out = escape_postgrest_literal("%term%")
    assert out == '"%term%"'
