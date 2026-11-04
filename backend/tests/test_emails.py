import pytest

from app.models import EmailAccount, EmailMessage
from app.security import decode_token
from tests.conftest import register


def test_list_returns_the_users_mail(client, auth, email):
    rows = client.get("/emails", headers=auth).json()
    assert [r["id"] for r in rows] == [email.id]


def test_list_filters(client, auth, db, account, email):
    db.add(
        EmailMessage(
            account_id=account.id,
            provider_message_id="msg-2",
            thread_id="thread-2",
            sender="Prof <prof@uni.edu>",
            subject="Thesis defense dates",
            snippet="Confirm which date works",
            body_text="Please confirm the date.",
            category="important",
            needs_attention=True,
            is_read=True,
        )
    )
    db.commit()

    def ids(**params):
        return {r["id"] for r in client.get("/emails", headers=auth, params=params).json()}

    assert len(ids()) == 2
    assert len(ids(category="important")) == 1
    assert len(ids(needs_attention=True)) == 1
    assert len(ids(unread=True)) == 1  # the fixture email is unread
    assert len(ids(q="defense")) == 1  # subject search
    assert len(ids(q="10:00")) == 1  # body search
    assert ids(q="nothing-matches-this") == set()


def test_patch_marks_read_and_archived(client, auth, email):
    response = client.patch(f"/emails/{email.id}", headers=auth, json={"is_read": True})
    assert response.json()["is_read"] is True

    client.patch(f"/emails/{email.id}", headers=auth, json={"is_archived": True})
    # archived mail leaves the default listing
    assert client.get("/emails", headers=auth).json() == []
    assert len(client.get("/emails", headers=auth, params={"archived": True}).json()) == 1


def test_another_user_cannot_read_or_patch_your_mail(client, db, email):
    other = register(client, email="other@example.com")
    headers = {"Authorization": f"Bearer {other['token']}"}

    assert client.get(f"/emails/{email.id}", headers=headers).status_code == 404
    assert client.patch(
        f"/emails/{email.id}", headers=headers, json={"is_read": True}
    ).status_code == 404
    assert client.get("/emails", headers=headers).json() == []


def test_sync_without_a_mailbox_is_a_clear_error(client, auth):
    response = client.post("/emails/sync", headers=auth)
    assert response.status_code == 400
    assert "connect" in response.json()["detail"].lower()


def test_summarize_stores_the_summary(client, auth, email, fake_provider, monkeypatch):
    monkeypatch.setattr(
        "app.routers.emails.llm.summarize_thread",
        lambda subject, messages, db=None, user_id=None: "They want to move the call.",
    )
    response = client.post(f"/emails/{email.id}/summarize", headers=auth)
    assert response.json()["summary"] == "They want to move the call."


def test_summarize_surfaces_quota_errors_as_429(
    client, auth, email, fake_provider, monkeypatch
):
    from app.services.llm import QuotaExceeded

    def boom(*args, **kwargs):
        raise QuotaExceeded("Gemini rate limit reached.")

    monkeypatch.setattr("app.routers.emails.llm.summarize_thread", boom)
    response = client.post(f"/emails/{email.id}/summarize", headers=auth)
    assert response.status_code == 429


def test_summarize_on_the_demo_inbox_is_refused(client, auth, db, user_token):
    """The demo inbox has no real provider behind it — say so, don't crash."""
    demo = EmailAccount(
        user_id=decode_token(user_token),
        provider="demo",
        email_address="demo@mailassist.app",
        credentials_json="{}",
    )
    db.add(demo)
    db.commit()
    message = EmailMessage(
        account_id=demo.id,
        provider_message_id="demo-0",
        thread_id="demo-thread-0",
        sender="a@b.com",
        subject="Demo",
        body_text="Demo body",
    )
    db.add(message)
    db.commit()

    response = client.post(f"/emails/{message.id}/summarize", headers=auth)
    assert response.status_code == 400
    assert "demo" in response.json()["detail"].lower()


@pytest.mark.parametrize("path", ["/emails", "/emails/1", "/emails/sync"])
def test_email_endpoints_require_auth(client, path):
    method = client.post if path.endswith("sync") else client.get
    assert method(path).status_code == 401


def test_the_same_provider_message_cannot_be_stored_twice(db, account):
    """Two syncs racing each other must not duplicate a message.

    Both snapshot "what we already have" before either commits, so the database
    is the only place this can be enforced.
    """
    from sqlalchemy.exc import IntegrityError

    def message():
        return EmailMessage(
            account_id=account.id,
            provider_message_id="same-id",
            thread_id="t",
            sender="a@b.com",
            subject="URGENT: Action required on your account",
            body_text="Your password will expire in 30 days.",
        )

    db.add(message())
    db.commit()

    db.add(message())
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
    else:
        raise AssertionError("duplicate message was accepted")

    assert db.query(EmailMessage).filter_by(provider_message_id="same-id").count() == 1


def test_the_same_id_in_two_mailboxes_is_allowed(db, account, user_token):
    """Uniqueness is per account — two mailboxes can legitimately share an id."""
    from app.security import decode_token

    other = EmailAccount(
        user_id=decode_token(user_token),
        provider="gmail",
        email_address="second@gmail.com",
        credentials_json="{}",
    )
    db.add(other)
    db.commit()

    for account_id in (account.id, other.id):
        db.add(
            EmailMessage(
                account_id=account_id,
                provider_message_id="shared-id",
                thread_id="t",
                sender="a@b.com",
                subject="s",
                body_text="b",
            )
        )
    db.commit()
    assert db.query(EmailMessage).filter_by(provider_message_id="shared-id").count() == 2


def test_a_second_sync_returns_the_running_one(client, auth, db, account, monkeypatch):
    """Two syncs must not run at once — that is how duplicates got in."""
    from app.models import SyncJob

    monkeypatch.setattr("app.routers.emails.BackgroundTasks.add_task", lambda *a, **k: None)

    first = client.post("/emails/sync", headers=auth).json()
    second = client.post("/emails/sync", headers=auth).json()

    assert first["id"] == second["id"]
    assert db.query(SyncJob).count() == 1


def test_a_stale_running_job_does_not_block_future_syncs(
    client, auth, db, account, user_token, monkeypatch
):
    """A worker that died must not wedge syncing forever.

    Before this, one hung model call left a job marked "running" and every
    later sync returned that dead job instead of starting a new one.
    """
    from datetime import datetime, timedelta, timezone

    from app.models import SyncJob
    from app.security import decode_token

    monkeypatch.setattr("app.routers.emails.BackgroundTasks.add_task", lambda *a, **k: None)

    stale = SyncJob(
        user_id=decode_token(user_token),
        status="running",
        stage="drafting",
        started_at=datetime.now(timezone.utc) - timedelta(hours=2),
    )
    db.add(stale)
    db.commit()
    stale_id = stale.id

    fresh = client.post("/emails/sync", headers=auth).json()

    assert fresh["id"] != stale_id, "returned the dead job instead of starting a new one"
    db.expire_all()
    abandoned = db.get(SyncJob, stale_id)
    assert abandoned.status == "error"
    assert "timed out" in abandoned.error
