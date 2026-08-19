from __future__ import annotations
from datetime import datetime, timezone

import pytest

from utils import decode_keyset_cursor, encode_keyset_cursor, escape_postgrest_literal


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


# --- keyset cursor -------------------------------------------------------


def test_decode_is_the_inverse_of_encode():
    marker = datetime(2026, 8, 14, 10, 0, 0, tzinfo=timezone.utc)
    row_id = "11111111-1111-1111-1111-111111111111"

    cursor = encode_keyset_cursor(marker, row_id)
    marker_raw, id_raw = decode_keyset_cursor(cursor)

    assert marker_raw == marker.isoformat()
    assert id_raw == row_id


def test_decode_rejects_malformed_base64():
    with pytest.raises(ValueError):
        decode_keyset_cursor("not-valid-base64!!")


def test_decode_rejects_a_non_uuid_id_half():
    marker = datetime(2026, 8, 14, 10, 0, 0, tzinfo=timezone.utc)
    cursor = encode_keyset_cursor(marker, "not-a-uuid")

    with pytest.raises(ValueError):
        decode_keyset_cursor(cursor)


def test_decode_rejects_a_non_datetime_marker_half():
    import base64

    cursor = base64.urlsafe_b64encode(
        b"not-a-datetime|11111111-1111-1111-1111-111111111111"
    ).decode()

    with pytest.raises(ValueError):
        decode_keyset_cursor(cursor)
