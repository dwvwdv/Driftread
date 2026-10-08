from types import SimpleNamespace
from postgrest.exceptions import APIError

ID = "11111111-1111-1111-1111-111111111111"
TARGET = "22222222-2222-2222-2222-222222222222"
AUTH = {"X-API-Key": "test-admin-key"}


def test_admin_event_requires_authorization_before_db_access(client):
    c, db = client
    response = c.post(
        "/api/admin/events",
        json={"kind": "fact", "title": "Fact"},
        headers={"X-API-Key": "bad"},
    )
    assert response.status_code == 403
    db.table.assert_not_called()
    response = c.patch(
        f"/api/admin/events/{ID}",
        json={"expected_version": 1},
        headers={"X-API-Key": "bad"},
    )
    assert response.status_code == 403
    db.rpc.assert_not_called()


def test_member_change_and_relation_are_one_rpc(client):
    c, db = client
    db.rpc.return_value = SimpleNamespace(
        execute=lambda: SimpleNamespace(data={"id": ID, "version": 2})
    )
    response = c.patch(
        f"/api/admin/events/{ID}",
        headers=AUTH,
        json={
            "expected_version": 1,
            "members": [{"id": TARGET, "excluded": True}],
            "relation_target": TARGET,
            "relation": "UNRELATED",
        },
    )
    assert response.status_code == 200
    name, params = db.rpc.call_args.args
    assert name == "mutate_manual_event" and params["p_expected_version"] == 1
    assert params["p_members"] == [{"id": TARGET, "excluded": True}]
    assert params["p_relation"] == "UNRELATED"


def test_version_conflict_is_reviewable_409(client):
    c, db = client
    db.rpc.return_value.execute.side_effect = APIError(
        {"code": "40001", "message": "sensitive", "details": None, "hint": None}
    )
    response = c.patch(
        f"/api/admin/events/{ID}", headers=AUTH, json={"expected_version": 1}
    )
    assert (
        response.status_code == 409 and "Version conflict" in response.json()["detail"]
    )
    assert "sensitive" not in response.text


def test_public_read_never_requests_admin_projection(client):
    c, db = client
    db.rpc.return_value = SimpleNamespace(execute=lambda: SimpleNamespace(data=None))
    assert c.get(f"/api/events/{ID}").status_code == 404
    assert db.rpc.call_args.args[1] == {"p_id": ID, "p_admin": False}


def test_relation_taxonomy_and_merged_alias_contract(client):
    c, db = client
    assert (
        c.patch(
            f"/api/admin/events/{ID}",
            headers=AUTH,
            json={
                "expected_version": 1,
                "relation": "SEMANTIC_GUESS",
                "relation_target": TARGET,
            },
        ).status_code
        == 422
    )
    assert (
        c.patch(
            f"/api/admin/events/{ID}",
            headers=AUTH,
            json={"expected_version": 1, "relation": "SAME_EVENT"},
        ).status_code
        == 422
    )
    db.rpc.return_value = SimpleNamespace(
        execute=lambda: SimpleNamespace(data={"id": TARGET, "requested_id": ID})
    )
    response = c.get(f"/api/events/{ID}")
    assert response.json()["id"] == TARGET and response.json()["requested_id"] == ID
