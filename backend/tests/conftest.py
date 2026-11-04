"""Shared test setup.

The database URL is set *before* the app is imported, so the whole app boots
against a throwaway SQLite file instead of the developer's real database.
No test in this suite makes a network call: Gemini and Gmail are both replaced
with fakes.
"""

import os
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="mailassist-tests-"))
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP / 'test.db'}"
os.environ["GEMINI_API_KEY"] = "test-key-not-used"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.database import Base, SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import EmailAccount, EmailMessage  # noqa: E402
from app.services.provider import EmailProvider, ThreadMessage  # noqa: E402


class FakeProvider(EmailProvider):
    """Stands in for Gmail. Records what was sent so tests can assert on it."""

    def __init__(self, account=None, db=None):
        self.account = account
        self.sent: list[dict] = []
        self.inbox: list = []
        self.sent_bodies: list[str] = ["Hi there,\n\nSounds good to me.\n\nCheers"]

    def fetch_recent(self, max_results: int = 20):
        return self.inbox[:max_results]

    def fetch_since(self, cursor: str):
        return None  # always ask the caller to do a full fetch

    def current_cursor(self) -> str:
        return "cursor-1"

    def fetch_sent_bodies(self, max_results: int = 30):
        return self.sent_bodies

    def fetch_thread(self, thread_id: str):
        return [ThreadMessage(sender="a@b.com", date="today", body_text="hello")]

    def send_reply(self, to, subject, body, thread_id=None, in_reply_to=""):
        self.sent.append(
            {
                "to": to,
                "subject": subject,
                "body": body,
                "thread_id": thread_id,
                "in_reply_to": in_reply_to,
            }
        )
        return "sent-id-1"


@pytest.fixture(autouse=True)
def clean_db():
    """Every test starts from an empty database."""
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def register(client, email="sandra@example.com", password="hunter2hunter2", name="Sandra"):
    response = client.post(
        "/auth/register", json={"email": email, "password": password, "name": name}
    )
    assert response.status_code == 200, response.text
    return response.json()


@pytest.fixture
def user_token(client):
    return register(client)["token"]


@pytest.fixture
def auth(user_token):
    return {"Authorization": f"Bearer {user_token}"}


@pytest.fixture
def account(db, user_token):
    """A connected (fake) Gmail mailbox for the first registered user."""
    from app.security import decode_token

    row = EmailAccount(
        user_id=decode_token(user_token),
        provider="gmail",
        email_address="sandra@gmail.com",
        credentials_json="{}",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@pytest.fixture
def email(db, account):
    row = EmailMessage(
        account_id=account.id,
        provider_message_id="msg-1",
        thread_id="thread-1",
        message_id_header="<abc@mail.example.com>",
        sender="Elena Petrova <elena@studio.co>",
        recipient="sandra@gmail.com",
        subject="Rescheduling tomorrow's sync",
        snippet="Could we move our 10:00 call to 14:00?",
        body_text="Hi! Could we move our 10:00 call to 14:00?",
        category="routine",
        needs_attention=False,
        classification_reason="Simple scheduling change.",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@pytest.fixture
def fake_provider(monkeypatch):
    """Route every provider lookup in the routers to one shared fake."""
    provider = FakeProvider()

    def _get_provider(db, account):
        provider.account = account
        return provider

    for module in ("app.routers.emails", "app.routers.drafts", "app.routers.settings"):
        monkeypatch.setattr(f"{module}.get_provider", _get_provider)
    return provider
