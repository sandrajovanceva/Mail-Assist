import logging
import os
import secrets
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from sqlalchemy.orm import Session

from ..config import settings
from ..database import get_db
from ..models import EmailAccount, User
from ..schemas import AccountOut, AuthResponse, LoginIn, RegisterIn, UserOut
from ..security import (
    create_oauth_state,
    create_token,
    decode_oauth_state,
    get_current_user,
    hash_password,
    verify_password,
)
from ..services.gmail import CALENDAR_SCOPE, GMAIL_SCOPES, credentials_to_json

# If the user unticks one of the permission boxes on the consent screen, Google
# returns fewer scopes than we asked for and oauthlib treats the mismatch as an
# error. That is a legitimate choice (read-only access still runs most of the
# app), so accept it and let the failing operation report the problem instead.
os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")

log = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])


# ---------- platform accounts (email + password) ----------


@router.post("/register", response_model=AuthResponse)
def register(body: RegisterIn, db: Session = Depends(get_db)):
    email = body.email.strip().lower()
    if len(body.password.encode("utf-8")) > 72:
        raise HTTPException(400, "Password must be at most 72 UTF-8 bytes")
    if len(body.password) < 8:
        raise HTTPException(400, "Password must be at least 8 characters")
    if db.query(User).filter_by(email=email).first():
        raise HTTPException(400, "An account with this email already exists")

    user = User(email=email, name=body.name.strip(), password_hash=hash_password(body.password))
    db.add(user)
    db.commit()
    db.refresh(user)
    return AuthResponse(token=create_token(user.id), user=UserOut.model_validate(user))


@router.post("/login", response_model=AuthResponse)
def login(body: LoginIn, db: Session = Depends(get_db)):
    user = db.query(User).filter_by(email=body.email.strip().lower()).first()
    if not user or not verify_password(body.password, user.password_hash):
        raise HTTPException(401, "Wrong email or password")
    return AuthResponse(token=create_token(user.id), user=UserOut.model_validate(user))


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)):
    return user


# ---------- Gmail OAuth (linked to the signed-in user) ----------


def _flow(calendar: bool = False) -> Flow:
    return Flow.from_client_config(
        {
            "web": {
                "client_id": settings.google_client_id,
                "client_secret": settings.google_client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
            }
        },
        scopes=GMAIL_SCOPES + ([CALENDAR_SCOPE] if calendar else []),
        redirect_uri=settings.google_redirect_uri,
    )


OAUTH_COOKIE = "mailassist_oauth_state"
OAUTH_COOKIE_PATH = "/auth/google"


@router.post("/google/login")
def google_login(response: Response, calendar: bool = False, user: User = Depends(get_current_user)):
    """Initiate only from an authenticated request, never a shareable login URL."""
    user_id = user.id

    flow = _flow(calendar)
    # Generate the PKCE verifier ourselves rather than letting the library
    # autogenerate one: it has to survive until the callback, which builds a
    # different Flow object, so it travels in the signed `state`.
    flow.autogenerate_code_verifier = False
    flow.code_verifier = secrets.token_urlsafe(64)  # 86 chars, within PKCE's 43-128

    oauth_state = create_oauth_state(user_id, flow.code_verifier)
    auth_url, _ = flow.authorization_url(
        access_type="offline",  # needed to get a refresh_token
        prompt="consent",
        include_granted_scopes="true",
        state=oauth_state,
    )
    response.set_cookie(
        OAUTH_COOKIE, oauth_state,
        max_age=600, httponly=True, samesite="lax", path=OAUTH_COOKIE_PATH,
        secure=settings.google_redirect_uri.startswith("https://"),
    )
    return {"url": auth_url}


def _clear_oauth_cookie(response):
    response.delete_cookie(OAUTH_COOKIE, path=OAUTH_COOKIE_PATH,
                           secure=settings.google_redirect_uri.startswith("https://"),
                           httponly=True, samesite="lax")
    return response


@router.get("/google/callback")
def google_callback(request: Request, code: str | None = None, state: str = "", error: str | None = None, db: Session = Depends(get_db)):
    browser_state = request.cookies.get(OAUTH_COOKIE, "")
    if not browser_state or not secrets.compare_digest(browser_state.encode(), state.encode()):
        raise HTTPException(400, "This connection was not started in this browser. Try connecting again.")
    user_id, code_verifier = decode_oauth_state(state)
    if error or not code:
        message = "Google connection cancelled. You can try connecting again." if error == "access_denied" else "Google connection did not complete. Please try again."
        return _clear_oauth_cookie(RedirectResponse(f"{settings.frontend_url}?auth_error={quote(message)}"))
    flow = _flow(calendar=True)
    flow.code_verifier = code_verifier
    try:
        flow.fetch_token(code=code)
    except Exception as exc:  # noqa: BLE001 - show the user something useful
        log.warning("Google token exchange failed: %s", exc)
        return _clear_oauth_cookie(RedirectResponse(f"{settings.frontend_url}?auth_error={quote(str(exc)[:200])}"))
    creds = flow.credentials

    oauth2 = build("oauth2", "v2", credentials=creds)
    info = oauth2.userinfo().get().execute()
    email_address = info.get("email", "")
    if not email_address:
        raise HTTPException(400, "Could not determine the account's email address")

    payload = credentials_to_json(creds)

    account = (
        db.query(EmailAccount)
        .filter_by(user_id=user_id, email_address=email_address)
        .first()
    )
    if account:
        from ..models import utcnow
        account.created_at = utcnow()
        account.credentials_json = payload
    else:
        account = EmailAccount(
            user_id=user_id,
            provider="gmail",
            email_address=email_address,
            credentials_json=payload,
        )
        db.add(account)
    db.commit()

    return _clear_oauth_cookie(RedirectResponse(f"{settings.frontend_url}?connected={quote(email_address)}"))


@router.get("/accounts", response_model=list[AccountOut])
def list_accounts(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return db.query(EmailAccount).filter_by(user_id=user.id).order_by(EmailAccount.created_at.desc(), EmailAccount.id.desc()).all()
