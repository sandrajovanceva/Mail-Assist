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

    response = client.get(
        f"/auth/google/login?token={user_token}", follow_redirects=False
    )
    assert response.status_code == 307
    params = parse_qs(urlparse(response.headers["location"]).query)

    assert params["code_challenge_method"] == ["S256"]
    assert params["code_challenge"][0]
    assert params["access_type"] == ["offline"]  # needed for a refresh token

    user_id, verifier = decode_oauth_state(params["state"][0])
    assert user_id == 1
    assert len(verifier) >= 43  # PKCE minimum
