"""Pre-drafting replies into the queue.

Unlike auto-send, this never puts mail in front of anyone — but it does spend
the expensive model and a limited daily quota, so the guards matter.
"""

import pytest

from app.models import AppSettings, Draft
from app.security import decode_token
from app.services import autodraft

DRAFT_TEXT = "Hi Elena,\n\n14:00 works for me.\n\nSandra"


@pytest.fixture
def stub_drafter(monkeypatch):
    calls = []

    def fake(email, tone, app_settings, knowledge, *args, **kwargs):
        calls.append((email.id, tone))
        return DRAFT_TEXT

    monkeypatch.setattr("app.services.autodraft.llm.draft_reply", fake)
    return calls


@pytest.fixture
def settings_row(db, user_token):
    row = AppSettings(user_id=decode_token(user_token), default_tone="friendly")
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def test_it_creates_a_pending_draft(db, email, settings_row, stub_drafter, user_token):
    draft = autodraft.draft_for(db, decode_token(user_token), email, settings_row)

    assert draft is not None
    assert draft.status == "pending"
    assert draft.content == DRAFT_TEXT
    assert draft.auto_sent is False  # nothing was sent
    assert draft.auto_send_eligible is True
    assert draft.original_content == DRAFT_TEXT  # baseline for edit learning
    assert {"kind": "tone", "title": "Reply tone", "content": "friendly"} in draft.preferences_used
    assert stub_drafter == [(email.id, "friendly")]  # uses the user's tone


def test_it_does_not_draft_twice_for_the_same_email(
    db, email, settings_row, stub_drafter, user_token
):
    user_id = decode_token(user_token)
    autodraft.draft_for(db, user_id, email, settings_row)
    second = autodraft.draft_for(db, user_id, email, settings_row)

    assert second is None
    assert len(stub_drafter) == 1  # no wasted model call
    assert db.query(Draft).filter_by(email_id=email.id).count() == 1


def test_it_does_not_overwrite_a_draft_the_user_is_editing(
    db, email, settings_row, stub_drafter, user_token
):
    db.add(Draft(email_id=email.id, content="my own words", status="pending"))
    db.commit()

    assert autodraft.draft_for(db, decode_token(user_token), email, settings_row) is None
    assert db.query(Draft).filter_by(email_id=email.id).one().content == "my own words"


def test_it_works_without_a_settings_row(db, email, stub_drafter, user_token):
    draft = autodraft.draft_for(db, decode_token(user_token), email, None)
    assert draft is not None
    assert stub_drafter[0][1] == "professional"  # falls back to the default tone


# --- bulk send of approved drafts ------------------------------------------


def test_send_approved_sends_only_approved(client, auth, db, email, account, fake_provider):
    """Approving is meant to stage a batch; this is what finally acts on it."""
    from app.models import Draft, EmailMessage

    other = EmailMessage(
        account_id=account.id,
        provider_message_id="msg-2",
        thread_id="t2",
        sender="Bob <bob@x.com>",
        subject="Second",
        body_text="b",
    )
    db.add(other)
    db.commit()

    db.add(Draft(email_id=email.id, content="approved one", status="approved"))
    db.add(Draft(email_id=other.id, content="still pending", status="pending"))
    db.commit()

    body = client.post("/drafts/send-approved", headers=auth).json()

    assert body["sent"] == 1
    assert body["failed"] == 0
    assert len(fake_provider.sent) == 1
    assert fake_provider.sent[0]["body"] == "approved one"

    statuses = {d.content: d.status for d in db.query(Draft).all()}
    assert statuses["approved one"] == "sent"
    assert statuses["still pending"] == "pending"  # untouched


def test_send_approved_with_nothing_approved(client, auth):
    response = client.post("/drafts/send-approved", headers=auth)
    assert response.status_code == 400
    assert "no approved" in response.json()["detail"].lower()


def test_one_failure_does_not_abandon_the_rest(
    client, auth, db, email, account, fake_provider, monkeypatch
):
    from app.models import Draft, EmailMessage

    other = EmailMessage(
        account_id=account.id,
        provider_message_id="msg-3",
        thread_id="t3",
        sender="Bob <bob@x.com>",
        subject="Second",
        body_text="b",
    )
    db.add(other)
    db.commit()
    db.add(Draft(email_id=email.id, content="first", status="approved"))
    db.add(Draft(email_id=other.id, content="second", status="approved"))
    db.commit()

    calls = {"n": 0}
    original = fake_provider.send_reply

    def flaky(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("network blip")
        return original(*args, **kwargs)

    monkeypatch.setattr(fake_provider, "send_reply", flaky)

    body = client.post("/drafts/send-approved", headers=auth).json()

    assert body["sent"] == 1
    assert body["failed"] == 1
    assert body["errors"]
