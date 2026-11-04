"""Auto-pilot guards. These are the rules that decide whether mail leaves the
mailbox without a human looking at it, so they are tested exhaustively."""

from datetime import datetime, timedelta, timezone

import pytest

from app.models import AppSettings, Draft, EmailMessage
from app.security import decode_token
from app.services import autosend, llm


@pytest.fixture
def email(email):
    """These cases start with mail that the reply decision has approved."""
    email.needs_reply = True
    return email


@pytest.fixture
def fake_provider(fake_provider, monkeypatch):
    monkeypatch.setattr(fake_provider, "thread_last_sender", lambda thread_id: "elena@studio.co")
    return fake_provider


@pytest.fixture(autouse=True)
def automatic_draft(db, email, monkeypatch):
    draft = Draft(email_id=email.id, content="Sure, 14:00 works.",
                  original_content="Sure, 14:00 works.", status="pending",
                  auto_send_eligible=True)
    db.add(draft)
    db.commit()
    def unexpected_generation(*args, **kwargs):
        pytest.fail("Auto-send must never generate a reply")
    monkeypatch.setattr(llm, "draft_reply", unexpected_generation)
    return draft


@pytest.fixture
def settings_row(db, user_token):
    row = AppSettings(
        user_id=decode_token(user_token),
        user_name="Sandra",
        auto_send_enabled=True,
        auto_send_categories="routine,newsletter",
        auto_send_daily_limit=5,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def test_important_mail_is_never_eligible(email, settings_row):
    email.category = "important"
    assert autosend.is_eligible(email, settings_row) is False


def test_mail_flagged_for_attention_is_never_eligible(email, settings_row):
    email.needs_attention = True  # even though the category is allowed
    assert autosend.is_eligible(email, settings_row) is False


def test_categories_outside_the_allow_list_are_skipped(email, settings_row):
    email.category = "spam"
    assert autosend.is_eligible(email, settings_row) is False


def test_disabled_means_disabled(email, settings_row):
    settings_row.auto_send_enabled = False
    assert autosend.is_eligible(email, settings_row) is False


def test_no_settings_row_means_no_auto_send(email):
    assert autosend.is_eligible(email, None) is False


def test_a_routine_email_is_eligible(email, settings_row):
    assert autosend.is_eligible(email, settings_row) is True


def test_important_cannot_be_smuggled_into_the_allow_list(settings_row):
    settings_row.auto_send_categories = "routine,important"
    assert "important" not in autosend.allowed_categories(settings_row)


def test_auto_send_sends_existing_draft_and_records(
    db, email, settings_row, fake_provider, monkeypatch, user_token
):
    user_id = decode_token(user_token)

    draft = autosend.maybe_auto_send(db, user_id, email, settings_row, fake_provider)

    assert draft is not None
    assert draft.status == "sent"
    assert draft.auto_sent is True
    assert draft.original_content == draft.content  # nothing was edited by a human
    sent = fake_provider.sent[0]
    assert sent["to"] == "elena@studio.co"
    assert sent["in_reply_to"] == "<abc@mail.example.com>"


def test_the_daily_limit_stops_a_runaway(
    db, email, settings_row, fake_provider, monkeypatch, user_token, account
):
    user_id = decode_token(user_token)
    settings_row.auto_send_daily_limit = 1

    # one already went out in the last 24h
    previous = EmailMessage(
        account_id=account.id,
        provider_message_id="msg-prev",
        thread_id="t",
        sender="x@y.com",
        subject="s",
        body_text="b",
    )
    db.add(previous)
    db.commit()
    db.add(
        Draft(
            email_id=previous.id,
            content="ok",
            status="sent",
            auto_sent=True,
            sent_at=datetime.now(timezone.utc) - timedelta(hours=2),
        )
    )
    db.commit()

    assert autosend.maybe_auto_send(db, user_id, email, settings_row, fake_provider) is None
    assert fake_provider.sent == []


def test_yesterdays_sends_do_not_count_against_today(
    db, email, settings_row, fake_provider, monkeypatch, user_token, account
):
    user_id = decode_token(user_token)
    settings_row.auto_send_daily_limit = 1

    previous = EmailMessage(
        account_id=account.id,
        provider_message_id="msg-old",
        thread_id="t",
        sender="x@y.com",
        subject="s",
        body_text="b",
    )
    db.add(previous)
    db.commit()
    db.add(
        Draft(
            email_id=previous.id,
            content="ok",
            status="sent",
            auto_sent=True,
            sent_at=datetime.now(timezone.utc) - timedelta(days=3),
        )
    )
    db.commit()

    assert autosend.maybe_auto_send(db, user_id, email, settings_row, fake_provider) is not None


@pytest.mark.parametrize("case", ["no_reply_needed", "bulk", "no_reply_sender", "newsletter", "spam"])
def test_no_reply_cases_never_draft_or_send(
    case, db, email, settings_row, fake_provider, monkeypatch, user_token
):
    # Exercise the full service even if a stale needs_reply flag says True.
    if case == "no_reply_needed":
        email.needs_reply = False
    elif case == "bulk":
        email.is_bulk = True
    elif case == "no_reply_sender":
        email.sender = "Shop <noreply@shop.com>"
    else:
        email.category = case
        settings_row.auto_send_categories = case

    def unexpected_draft(*args, **kwargs):
        pytest.fail("No-reply mail must not spend a drafting call")

    monkeypatch.setattr(llm, "draft_reply", unexpected_draft)
    assert autosend.maybe_auto_send(
        db, decode_token(user_token), email, settings_row, fake_provider
    ) is None
    assert fake_provider.sent == []
    assert db.query(Draft).filter_by(email_id=email.id).count() == 1


@pytest.mark.parametrize("last_sender", ["Sandra <sandra@gmail.com>", None])
def test_answered_or_unknown_thread_never_drafts_or_sends(
    last_sender, db, email, settings_row, fake_provider, monkeypatch, user_token
):
    monkeypatch.setattr(fake_provider, "thread_last_sender", lambda thread_id: last_sender)

    def unexpected_draft(*args, **kwargs):
        pytest.fail("Answered or unverifiable threads must not be auto-drafted for sending")

    monkeypatch.setattr(llm, "draft_reply", unexpected_draft)
    assert autosend.maybe_auto_send(
        db, decode_token(user_token), email, settings_row, fake_provider
    ) is None
    assert fake_provider.sent == []


def test_user_edit_during_thread_lookup_prevents_send(
    db, email, settings_row, automatic_draft, fake_provider, monkeypatch, user_token
):
    def lookup(thread_id):
        automatic_draft.auto_send_eligible = False
        db.commit()
        return "elena@studio.co"
    monkeypatch.setattr(fake_provider, "thread_last_sender", lookup)
    assert autosend.maybe_auto_send(
        db, decode_token(user_token), email, settings_row, fake_provider
    ) is None
    assert fake_provider.sent == []


def test_thread_lookup_error_prevents_send(
    db, email, settings_row, fake_provider, monkeypatch, user_token
):
    def fail_lookup(thread_id):
        raise RuntimeError("Mailbox unavailable")

    monkeypatch.setattr(fake_provider, "thread_last_sender", fail_lookup)
    with pytest.raises(RuntimeError, match="Mailbox unavailable"):
        autosend.maybe_auto_send(db, decode_token(user_token), email, settings_row, fake_provider)
    assert fake_provider.sent == []


@pytest.mark.parametrize("case", ["absent", "manual", "edited", "empty", "rejected", "approved", "sent", "multiple", "archived"])
def test_only_one_untouched_automatic_pending_draft_can_send(
    case, db, email, settings_row, automatic_draft, fake_provider, user_token
):
    draft = automatic_draft
    if case == "absent":
        db.delete(draft)
    elif case == "manual":
        draft.auto_send_eligible = False
    elif case == "edited":
        draft.content = "My own words"
    elif case == "empty":
        draft.content = draft.original_content = " "
    elif case in ("rejected", "approved", "sent"):
        draft.status = case
    elif case == "multiple":
        db.add(Draft(email_id=email.id, content="Another version", status="pending"))
    else:
        email.is_archived = True
    db.commit()
    before = db.query(Draft).count()
    assert autosend.maybe_auto_send(db, decode_token(user_token), email, settings_row, fake_provider) is None
    assert fake_provider.sent == []
    assert db.query(Draft).count() == before


def test_sends_same_row_and_content_without_generating(
    db, email, settings_row, automatic_draft, fake_provider, user_token
):
    settings_row.auto_draft_enabled = False
    db.commit()
    result = autosend.maybe_auto_send(db, decode_token(user_token), email, settings_row, fake_provider)
    assert result.id == automatic_draft.id
    assert result.status == "sent"
    assert result.auto_send_eligible is False
    assert fake_provider.sent[0]["body"] == "Sure, 14:00 works."
    assert db.query(Draft).filter_by(email_id=email.id).count() == 1
    assert autosend.maybe_auto_send(db, decode_token(user_token), email, settings_row, fake_provider) is None
    assert len(fake_provider.sent) == 1


@pytest.mark.parametrize("patch", [{"content": "Edited"}, {"status": "rejected"}, {"status": "approved"}])
def test_user_changes_permanently_disable_auto_send(
    patch, client, auth, db, email, settings_row, automatic_draft, fake_provider, user_token
):
    assert client.put(f"/drafts/{automatic_draft.id}", headers=auth, json=patch).status_code == 200
    # Reverting the content/status must not silently opt the draft back in.
    assert client.put(f"/drafts/{automatic_draft.id}", headers=auth,
                      json={"content": automatic_draft.original_content, "status": "pending"}).status_code == 200
    db.refresh(automatic_draft)
    assert automatic_draft.auto_send_eligible is False
    assert autosend.maybe_auto_send(db, decode_token(user_token), email, settings_row, fake_provider) is None
    assert fake_provider.sent == []


@pytest.mark.parametrize("draft_enabled", [False, True])
@pytest.mark.parametrize("send_enabled", [False, True])
@pytest.mark.parametrize("existing", [False, True])
def test_sync_respects_independent_draft_and_send_settings(
    draft_enabled, send_enabled, existing, client, auth, db, email,
    settings_row, automatic_draft, fake_provider, monkeypatch
):
    settings_row.auto_draft_enabled = draft_enabled
    settings_row.auto_send_enabled = send_enabled
    if not existing:
        db.delete(automatic_draft)
    db.commit()
    generations = []

    def generate(*args, **kwargs):
        generations.append(True)
        return "New automatic reply"

    monkeypatch.setattr(llm, "draft_reply", generate)
    response = client.post("/emails/sync", headers=auth)
    assert response.status_code == 200
    job = client.get(f"/emails/sync/{response.json()['id']}", headers=auth).json()
    assert job["status"] == "done", job
    should_exist = existing or draft_enabled
    should_send = should_exist and send_enabled
    assert len(generations) == int(draft_enabled and not existing)
    assert len(fake_provider.sent) == int(should_send)
    assert job["drafted_count"] == len(generations)
    assert job["auto_sent_count"] == int(should_send)
    db.expire_all()
    drafts = db.query(Draft).filter_by(email_id=email.id).all()
    assert len(drafts) == int(should_exist)
    if drafts:
        assert drafts[0].status == ("sent" if should_send else "pending")
