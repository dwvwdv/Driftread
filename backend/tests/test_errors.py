from __future__ import annotations

from postgrest.exceptions import APIError

from errors import map_postgrest_error


def _api_error(code: str | None, details: str | None = None) -> APIError:
    return APIError({"message": "boom", "code": code, "hint": None, "details": details})


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


def test_pgrst116_zero_rows_maps_to_404():
    status_code, body = map_postgrest_error(
        _api_error("PGRST116", "Results contain 0 rows, application/vnd.pgrst.object+json requires 1 row")
    )
    assert status_code == 404


def test_pgrst116_multiple_rows_maps_to_generic_500_not_404():
    # A query written to expect at most one match (.maybe_single()) that
    # actually found several is a data-integrity/query bug, not "not
    # found" — see routers/admin_discovery.py's seed_targets, where
    # multiple discovery_targets rows can share a host (migration 006).
    status_code, body = map_postgrest_error(
        _api_error("PGRST116", "Results contain 2 rows, application/vnd.pgrst.object+json requires 1 row")
    )
    assert status_code == 500
    assert body == {"detail": "Internal server error"}


def test_pgrst116_unparseable_details_falls_back_to_generic_500():
    status_code, body = map_postgrest_error(_api_error("PGRST116", "something unexpected"))
    assert status_code == 500


def test_pgrst116_missing_details_falls_back_to_generic_500():
    status_code, body = map_postgrest_error(_api_error("PGRST116", None))
    assert status_code == 500


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
