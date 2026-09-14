from __future__ import annotations
from datetime import datetime, timezone

import pytest

from utils import (
    decode_keyset_cursor,
    decode_rank_cursor,
    encode_keyset_cursor,
    encode_rank_cursor,
    escape_postgrest_literal,
)


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


# --- rank cursor (search) -------------------------------------------------


def test_rank_decode_is_the_inverse_of_encode():
    marker = datetime(2026, 8, 14, 10, 0, 0, tzinfo=timezone.utc)
    row_id = "11111111-1111-1111-1111-111111111111"

    cursor = encode_rank_cursor(0.61234, marker, row_id)
    rank, marker_raw, id_raw = decode_rank_cursor(cursor)

    assert rank == 0.61234
    assert marker_raw == marker.isoformat()
    assert id_raw == row_id


def test_rank_cursor_round_trips_a_float32_upcast_exactly():
    # Mirrors what actually happens in production: Postgres `real` (float4)
    # comes back over JSON as a Python float64 upcast, gets repr()'d into the
    # cursor, and must compare equal to the same float32 value re-parsed on
    # the next page's request.
    from struct import pack, unpack

    rank = unpack("f", pack("f", 0.123456789))[0]
    marker = datetime(2026, 8, 14, 10, 0, 0, tzinfo=timezone.utc)

    cursor = encode_rank_cursor(rank, marker, "11111111-1111-1111-1111-111111111111")
    decoded_rank, _, _ = decode_rank_cursor(cursor)

    assert decoded_rank == rank


def test_rank_decode_rejects_malformed_base64():
    with pytest.raises(ValueError):
        decode_rank_cursor("not-valid-base64!!")


def test_rank_decode_rejects_a_non_float_rank_part():
    import base64

    cursor = base64.urlsafe_b64encode(
        b"not-a-float|2026-08-14T10:00:00+00:00|11111111-1111-1111-1111-111111111111"
    ).decode()

    with pytest.raises(ValueError):
        decode_rank_cursor(cursor)


def test_rank_decode_rejects_a_non_uuid_id_part():
    marker = datetime(2026, 8, 14, 10, 0, 0, tzinfo=timezone.utc)
    cursor = encode_rank_cursor(0.5, marker, "not-a-uuid")

    with pytest.raises(ValueError):
        decode_rank_cursor(cursor)
