from tests.conftest import register


def test_register_returns_token_and_user(client):
    body = register(client)
    assert body["token"]
    assert body["user"]["email"] == "sandra@example.com"
    assert "password" not in body["user"]


def test_register_rejects_short_password(client):
    response = client.post(
        "/auth/register", json={"email": "a@b.com", "password": "short", "name": ""}
    )
    assert response.status_code == 400


def test_register_rejects_duplicate_email(client):
    register(client)
    response = client.post(
        "/auth/register",
        json={"email": "SANDRA@example.com", "password": "hunter2hunter2", "name": ""},
    )
    assert response.status_code == 400  # matching is case-insensitive


def test_login_wrong_password_is_rejected(client):
    register(client)
    response = client.post(
        "/auth/login", json={"email": "sandra@example.com", "password": "wrongwrongwrong"}
    )
    assert response.status_code == 401


def test_me_requires_a_token(client, auth):
    assert client.get("/auth/me").status_code == 401
    assert client.get("/auth/me", headers=auth).status_code == 200


def test_me_rejects_a_garbage_token(client):
    response = client.get("/auth/me", headers={"Authorization": "Bearer not-a-jwt"})
    assert response.status_code == 401
