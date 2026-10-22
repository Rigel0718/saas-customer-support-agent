from uuid import uuid4

import pytest
from sqlalchemy.exc import OperationalError

from tests.conftest import headers


def create(client, token="owner-token"):
    response = client.post(
        "/support/requests",
        headers=headers(token),
        json={"content": "  팀원 초대가 안됩니다  "},
    )
    assert response.status_code == 201
    return response.json()


def test_create_get_traces_and_openapi(client):
    body = create(client)
    assert body["content"] == "팀원 초대가 안됩니다"
    assert body["status"] == "pending"
    for key in ("started_at", "finished_at", "diagnosis", "error_code"):
        assert body[key] is None
    for token in ("owner-token", "admin-token", "member-token"):
        assert (
            client.get(f"/support/requests/{body['id']}", headers=headers(token)).json()
            == body
        )
        assert client.get(
            f"/support/requests/{body['id']}/traces", headers=headers(token)
        ).json() == {"items": []}
    schema = client.get("/openapi.json").json()
    assert "/support/requests" in schema["paths"]
    assert set(schema["components"]["schemas"]["RequestDTO"]["properties"]) == set(body)


@pytest.mark.parametrize(
    "body",
    [
        {"content": ""},
        {"content": " "},
        {"content": "a" * 4001},
        {"content": "문의", "organization_id": str(uuid4())},
        {"content": "문의", "user_id": str(uuid4())},
        {"content": "문의", "role": "owner"},
    ],
)
def test_bad_body(client, body):
    assert (
        client.post("/support/requests", headers=headers(), json=body).status_code
        == 422
    )


def test_scope_and_auth(client):
    body = create(client, "member-token")
    assert body["requester_user_id"].endswith("0103")
    other = str(uuid4())
    for suffix in ("", "/traces"):
        path = f"/support/requests/{body['id']}{suffix}"
        missing = f"/support/requests/{other}{suffix}"
        assert client.get(path).status_code == 401
        response = client.get(
            path,
            headers=headers("expired-token")
            | {"X-Organization-ID": body["organization_id"]},
        )
        assert response.status_code == 404
        assert (
            response.json()
            == client.get(missing, headers=headers("expired-token")).json()
        )
        assert (
            client.get(f"/support/requests/bad{suffix}", headers=headers()).status_code
            == 422
        )


def test_create_rollback(client, session, monkeypatch):
    rollback = session.rollback
    called = []

    def fail():
        raise OperationalError("SECRET SQL", {}, Exception("SECRET"))

    def track():
        called.append(True)
        rollback()

    monkeypatch.setattr(session, "commit", fail)
    monkeypatch.setattr(session, "rollback", track)
    response = client.post(
        "/support/requests", headers=headers(), json={"content": "문의"}
    )
    assert response.status_code == 503 and called
    assert "SECRET" not in response.text
