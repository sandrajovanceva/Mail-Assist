"""Password hashing + JWT session tokens + the current-user dependency."""

from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from .config import settings
from .database import get_db

TOKEN_LIFETIME = timedelta(days=7)
# The OAuth round trip is a browser redirect or two — it should not be possible
# to replay a stale one hours later.
OAUTH_STATE_LIFETIME = timedelta(minutes=10)
bearer = HTTPBearer(auto_error=False)


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode(), password_hash.encode())
    except ValueError:
        return False


def create_token(user_id: int) -> str:
    payload = {
        "sub": str(user_id),
        "exp": datetime.now(timezone.utc) + TOKEN_LIFETIME,
    }
    return jwt.encode(payload, settings.secret_key, algorithm="HS256")


def decode_token(token: str) -> int:
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=["HS256"])
        return int(payload["sub"])
    except (jwt.PyJWTError, KeyError, ValueError):
        raise HTTPException(401, "Invalid or expired session")


def create_oauth_state(user_id: int, code_verifier: str) -> str:
    """Pack the OAuth `state` parameter.

    It carries two things across Google's redirect: which user is connecting,
    and the PKCE code verifier. The verifier is generated when we build the
    authorization URL but is only needed later, in the callback — and since
    each request builds its own Flow object, there is nowhere in memory to keep
    it. Signing it into `state` keeps the exchange stateless and tamper-proof.
    """
    payload = {
        "sub": str(user_id),
        "cv": code_verifier,
        "exp": datetime.now(timezone.utc) + OAUTH_STATE_LIFETIME,
    }
    return jwt.encode(payload, settings.secret_key, algorithm="HS256")


def decode_oauth_state(state: str) -> tuple[int, str]:
    try:
        payload = jwt.decode(state, settings.secret_key, algorithms=["HS256"])
        return int(payload["sub"]), payload["cv"]
    except (jwt.PyJWTError, KeyError, ValueError):
        raise HTTPException(400, "This sign-in attempt expired — try connecting again")


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Session = Depends(get_db),
):
    from .models import User

    if credentials is None:
        raise HTTPException(401, "Not signed in")
    user_id = decode_token(credentials.credentials)
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(401, "Account no longer exists")
    return user
