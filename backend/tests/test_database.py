from unittest.mock import patch

import database


def test_get_client_scopes_postgrest_to_driftread(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_KEY", "test-key")
    database._client = None

    with patch.object(database, "create_client") as create_client:
        expected = create_client.return_value

        assert database.get_client() is expected

    options = create_client.call_args.kwargs["options"]
    assert options.schema == "driftread"

    database._client = None
