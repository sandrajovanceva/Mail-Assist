"""The OAuth `state` round trip.

Google's PKCE flow generates a code verifier when the authorization URL is
built, then needs it back in the callback — a different request, with a
different Flow object. Losing it fails the token exchange with a confusing
`invalid_grant: Missing code verifier`, so the round trip is worth pinning.
"""

import pytest
from fastapi import HTTPException

from app.security import create_oauth_state, decode_oauth_state


def test_state_round_trips_user_and_verifier():
    state = create_oauth_state(42, "verifier-abc123")
    assert decode_oauth_state(state) == (42, "verifier-abc123")


def test_tampered_state_is_rejected():
    state = create_oauth_state(42, "verifier-abc123")
    with pytest.raises(HTTPException) as caught:
        decode_oauth_state(state[:-4] + "aaaa")
    assert caught.value.status_code == 400


def test_garbage_state_is_rejected():
    with pytest.raises(HTTPException):
        decode_oauth_state("not-a-jwt")


def test_state_signed_with_another_key_is_rejected():
    import jwt

    forged = jwt.encode({"sub": "1", "cv": "x"}, "some-other-secret", algorithm="HS256")
    with pytest.raises(HTTPException):
        decode_oauth_state(forged)


def test_expired_state_is_rejected(monkeypatch):
    from datetime import timedelta

    import app.security as security

    monkeypatch.setattr(security, "OAUTH_STATE_LIFETIME", timedelta(seconds=-1))
    with pytest.raises(HTTPException):
        decode_oauth_state(create_oauth_state(1, "v"))


def test_login_redirect_sends_a_pkce_challenge(client, user_token, monkeypatch):
    """The authorization URL must carry a code_challenge, and the state we can
    unpack back into the same user."""
    from urllib.parse import parse_qs, urlparse

    from app.config import settings

    monkeypatch.setattr(settings, "google_client_id", "test-client-id")
    monkeypatch.setattr(settings, "google_client_secret", "test-secret")

    response = client.post(
        "/auth/google/login", headers={"Authorization": f"Bearer {user_token}"}
    )
    assert response.status_code == 200
    params = parse_qs(urlparse(response.json()["url"]).query)

    assert params["code_challenge_method"] == ["S256"]
    assert params["code_challenge"][0]
    assert params["access_type"] == ["offline"]  # needed for a refresh token

    user_id, verifier = decode_oauth_state(params["state"][0])
    assert user_id == 1
    assert len(verifier) >= 43  # PKCE minimum
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie and "Max-Age=600" in cookie and "SameSite=lax" in cookie


def test_forwarded_login_link_cannot_start_a_connection(client, user_token):
    assert client.get(f"/auth/google/login?token={user_token}").status_code == 405
    assert client.post(f"/auth/google/login?token={user_token}").status_code == 401


def test_oauth_cookie_is_secure_with_https_callback(client, auth, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "google_client_id", "test-client")
    monkeypatch.setattr(settings, "google_redirect_uri", "https://mailassist.example/auth/google/callback")
    response = client.post("/auth/google/login", headers=auth)
    assert "Secure" in response.headers["set-cookie"]


def test_google_cancellation_returns_to_app_and_clears_cookie(client, auth, db, monkeypatch):
    from urllib.parse import parse_qs, urlparse
    from app.config import settings
    from app.routers import auth as router
    from app.models import EmailAccount
    monkeypatch.setattr(settings, "google_client_id", "test-client")
    started = client.post("/auth/google/login", headers=auth)
    state = parse_qs(urlparse(started.json()["url"]).query)["state"][0]
    monkeypatch.setattr(router, "_flow", lambda **kwargs: pytest.fail("Cancelled OAuth must not exchange a token"))
    response = client.get("/auth/google/callback", params={"state": state, "error": "access_denied"}, follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"].startswith(settings.frontend_url)
    assert "cancelled" in parse_qs(urlparse(response.headers["location"]).query)["auth_error"][0]
    assert client.cookies.get(router.OAUTH_COOKIE) is None
    assert db.query(EmailAccount).count() == 0


@pytest.mark.parametrize("cookie", [None, "different-browser"])
def test_callback_rejects_missing_or_other_browser_cookie(client, cookie, monkeypatch):
    from app.routers import auth
    monkeypatch.setattr(auth, "_flow", lambda **kwargs: pytest.fail("Must reject before contacting Google"))
    if cookie:
        client.cookies.set(auth.OAUTH_COOKIE, cookie, path=auth.OAUTH_COOKIE_PATH)
    state = create_oauth_state(1, "verifier")
    response = client.get("/auth/google/callback", params={"code": "code", "state": state})
    assert response.status_code == 400


@pytest.mark.parametrize("fails", [False, True])
def test_browser_bound_callback_consumes_cookie(client, auth, db, monkeypatch, fails):
    from types import SimpleNamespace
    from unittest.mock import MagicMock
    from app.routers import auth as router
    from app.config import settings
    from app.models import EmailAccount
    from urllib.parse import parse_qs, urlparse

    monkeypatch.setattr(settings, "google_client_id", "test-client")
    response = client.post("/auth/google/login", headers=auth)
    state = parse_qs(urlparse(response.json()["url"]).query)["state"][0]
    assert client.cookies.get(router.OAUTH_COOKIE) == state
    flow = MagicMock()
    if fails:
        flow.fetch_token.side_effect = RuntimeError("exchange failed")
    flow.credentials = SimpleNamespace()
    monkeypatch.setattr(router, "_flow", lambda **kwargs: flow)
    monkeypatch.setattr(router, "credentials_to_json", lambda creds: "{}")
    service = MagicMock()
    service.userinfo.return_value.get.return_value.execute.return_value = {"email": "connected@example.com"}
    monkeypatch.setattr(router, "build", lambda *args, **kwargs: service)
    response = client.get("/auth/google/callback", params={"code": "code", "state": state}, follow_redirects=False)
    assert response.status_code == 307
    assert client.cookies.get(router.OAUTH_COOKIE) is None
    assert db.query(EmailAccount).count() == (0 if fails else 1)
    assert client.get("/auth/google/callback", params={"code": "code", "state": state}).status_code == 400
