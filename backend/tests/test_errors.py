from __future__ import annotations

from postgrest.exceptions import APIError

from errors import map_postgrest_error


def _api_error(code: str | None) -> APIError:
    return APIError({"message": "boom", "code": code, "hint": None, "details": None})


def test_unique_violation_maps_to_409():
    status_code, body = map_postgrest_error(_api_error("23505"))
    assert status_code == 409
    assert body == {"detail": "Resource already exists"}


def test_foreign_key_violation_maps_to_409():
    status_code, body = map_postgrest_error(_api_error("23503"))
    assert status_code == 409


def test_not_null_violation_maps_to_400():
    status_code, body = map_postgrest_error(_api_error("23502"))
    assert status_code == 400


def test_check_violation_maps_to_400():
    status_code, body = map_postgrest_error(_api_error("23514"))
    assert status_code == 400


def test_insufficient_privilege_maps_to_403():
    status_code, body = map_postgrest_error(_api_error("42501"))
    assert status_code == 403


def test_single_row_not_found_maps_to_404():
    status_code, body = map_postgrest_error(_api_error("PGRST116"))
    assert status_code == 404


def test_unrecognized_code_maps_to_generic_500_without_leaking_detail():
    status_code, body = map_postgrest_error(_api_error("99999"))
    assert status_code == 500
    assert body == {"detail": "Internal server error"}
    # The real message/details must never end up in the response body —
    # only the caller's server-side log gets those.
    assert "boom" not in str(body)


def test_missing_code_maps_to_generic_500():
    status_code, body = map_postgrest_error(_api_error(None))
    assert status_code == 500


def test_object_without_code_attribute_maps_to_generic_500():
    # Defensive against postgrest-py versions/edge cases where the
    # exception instance doesn't carry a `code` attribute at all, mirroring
    # this codebase's existing postgrest-py version-tolerance elsewhere
    # (see routers/feeds.py's maybe_single() handling).
    status_code, body = map_postgrest_error(Exception("boom"))
    assert status_code == 500
