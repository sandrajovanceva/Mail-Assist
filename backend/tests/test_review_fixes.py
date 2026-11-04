"""Integration regressions for the remaining implementation-review findings."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from types import SimpleNamespace
from pathlib import Path
import sys

import pytest

from app.models import AppSettings, Draft, EmailMessage, KnowledgeItem, LLMCall
from app.security import decode_token
from app.services import llm, usage


def draft_for(db, email, status="pending"):
    draft = Draft(email_id=email.id, content="Hello", original_content="Hello", status=status)
    db.add(draft)
    db.commit()
    return draft


def test_concurrent_send_requests_deliver_once(client, auth, db, email, fake_provider, monkeypatch):
    draft = draft_for(db, email)
    entered, release = Event(), Event()
    original = fake_provider.send_reply

    def blocked(**kwargs):
        entered.set()
        assert release.wait(10)
        return original(**kwargs)

    monkeypatch.setattr(fake_provider, "send_reply", blocked)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(client.post, f"/drafts/{draft.id}/send", headers=auth)
        try:
            assert entered.wait(10)
            second = client.post(f"/drafts/{draft.id}/send", headers=auth)
            assert second.status_code == 409
        finally:
            release.set()
        assert first.result().status_code == 200
    assert len(fake_provider.sent) == 1


def test_uncertain_send_requires_confirmation_before_retry(client, auth, db, email, fake_provider, monkeypatch):
    draft = draft_for(db, email)
    original = fake_provider.send_reply

    def fail(**kwargs):
        raise RuntimeError("connection lost after request")

    monkeypatch.setattr(fake_provider, "send_reply", fail)
    assert client.post(f"/drafts/{draft.id}/send", headers=auth).status_code == 409
    db.refresh(draft)
    assert draft.status == "send_unknown"
    assert client.post(f"/drafts/{draft.id}/send", headers=auth).status_code == 409
    assert client.put(f"/drafts/{draft.id}", headers=auth, json={"status": "pending"}).status_code == 409
    assert client.post(f"/drafts/{draft.id}/resolve", headers=auth, json={"status": "pending"}).status_code == 200
    monkeypatch.setattr(fake_provider, "send_reply", original)
    assert client.post(f"/drafts/{draft.id}/send", headers=auth).status_code == 200
    assert len(fake_provider.sent) == 1


def test_successful_send_answers_thread_and_retires_other_versions(client, auth, db, email, fake_provider):
    email.needs_reply = True
    first = draft_for(db, email)
    second = draft_for(db, email, "approved")
    assert client.post(f"/drafts/{first.id}/send", headers=auth).status_code == 200
    db.refresh(email)
    db.refresh(second)
    assert not email.needs_reply
    assert second.status == "superseded"
    assert client.post(f"/drafts/{second.id}/send", headers=auth).status_code == 400


@pytest.mark.parametrize("password", ["a" * 73, "ж" * 37])
def test_long_password_has_validation_error(client, password):
    response = client.post("/auth/register", json={"email": "long@example.com", "password": password})
    assert response.status_code == 400
    assert "72" in response.json()["detail"]


def test_failed_classification_is_retried_before_drafting(client, auth, db, email, user_token, fake_provider, monkeypatch):
    email.category = ""
    email.classification_reason = "classification failed: quota"
    db.add(AppSettings(user_id=decode_token(user_token), auto_draft_enabled=False))
    db.commit()
    calls = []

    def classify(*args, **kwargs):
        calls.append(args)
        return {"category": "routine", "needs_attention": False, "reason": "Recovered"}

    monkeypatch.setattr(llm, "classify_email", classify)
    response = client.post("/emails/sync", headers=auth)
    assert response.status_code == 200
    db.refresh(email)
    assert email.category == "routine"
    assert email.classification_reason == "Recovered"
    assert len(calls) == 1


def test_full_sync_captures_cursor_before_snapshot(client, auth, account, fake_provider, monkeypatch):
    order = []
    monkeypatch.setattr(fake_provider, "current_cursor", lambda: order.append("cursor") or "before")
    monkeypatch.setattr(fake_provider, "fetch_recent", lambda **kw: order.append("snapshot") or [])
    assert client.post("/emails/sync", headers=auth).status_code == 200
    assert order == ["cursor", "snapshot"]


def test_mailbox_and_draft_pages_are_complete(client, auth, db, email, account):
    for i in range(235):
        db.add(EmailMessage(account_id=account.id, provider_message_id=f"paging-{i}",
                            thread_id=f"paging-{i}", sender="a@b.com"))
        db.add(Draft(email_id=email.id, content=str(i), status="pending"))
    db.commit()
    for endpoint, total in (("/emails", 236), ("/drafts", 235)):
        first = client.get(endpoint, headers=auth, params={"limit": 200}).json()
        second = client.get(endpoint, headers=auth, params={"limit": 200, "offset": 200}).json()
        assert len({r["id"] for r in first + second}) == total


def test_demo_cleanup_preserves_real_and_customized_knowledge(client, auth, db, user_token):
    from app.routers.dev import DEMO_KNOWLEDGE
    real = KnowledgeItem(user_id=decode_token(user_token), **DEMO_KNOWLEDGE[0])
    db.add(real)
    db.commit()
    assert client.post("/dev/seed", headers=auth).status_code == 200
    seeded = db.query(KnowledgeItem).filter_by(demo_seeded=True).first()
    seeded.content = "My customized fact"
    db.commit()
    real_id, customized_id = real.id, seeded.id
    assert client.post("/dev/clear", headers=auth).status_code == 200
    db.expire_all()
    assert db.get(KnowledgeItem, real_id) is not None
    assert db.get(KnowledgeItem, customized_id).content == "My customized fact"


@pytest.mark.parametrize("text", ["", "not JSON", '{"category":"unknown","needs_attention":false,"reason":"x"}'])
def test_unusable_model_output_records_failure_with_tokens(text, db, monkeypatch):
    meta = SimpleNamespace(candidates_token_count=4, thoughts_token_count=0,
                           prompt_token_count=10, cached_content_token_count=0)
    fake = SimpleNamespace(models=SimpleNamespace(generate_content=lambda **kw: SimpleNamespace(text=text, usage_metadata=meta)))
    monkeypatch.setattr(llm, "get_client", lambda: fake)
    with pytest.raises(RuntimeError):
        llm._generate(operation="classify", model="unknown", system="", prompt="",
                      max_output_tokens=10, response_schema=llm.Classification, db=db)
    call = db.query(LLMCall).one()
    assert not call.success
    assert call.input_tokens == 10
    assert call.output_tokens == 4


def test_unknown_price_is_exposed_in_summary(client, auth, db, user_token):
    usage.record_call(db, user_id=decode_token(user_token), operation="draft", model="unpriced", input_tokens=10)
    result = client.get("/usage/summary", headers=auth).json()
    assert result["unpriced_calls"] == 1
    assert result["rows"][0]["unpriced_calls"] == 1


def test_evaluation_cache_uses_current_labels_and_invalidates_changed_input():
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from eval.classification_eval import prediction_fingerprint, score_current_labels
    row = {"id": "x", "sender": "a", "subject": "b", "body": "c", "category": "routine", "needs_attention": False}
    cache = {"x": {"fingerprint": prediction_fingerprint(row), "expected_category": "spam", "predicted_category": "routine"}}
    assert score_current_labels([row], cache)[0]["expected_category"] == "routine"
    assert score_current_labels([{**row, "body": "changed"}], cache) == []


def test_style_metrics_count_cyrillic_words():
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from eval.style_eval import words, compare
    assert words("Здраво Сандра") == ["здраво", "сандра"]
    assert compare("Здраво Сандра", "Здраво Сандра")["vocabulary_overlap"] == 1


def test_send_claim_blocks_edits_until_delivery_finishes(client, auth, db, email, fake_provider, monkeypatch):
    draft = draft_for(db, email)
    original = fake_provider.send_reply

    def sending(**kwargs):
        response = client.put(f"/drafts/{draft.id}", headers=auth, json={"content": "Changed during send"})
        assert response.status_code == 409
        return original(**kwargs)

    monkeypatch.setattr(fake_provider, "send_reply", sending)
    assert client.post(f"/drafts/{draft.id}/send", headers=auth).status_code == 200
    assert fake_provider.sent[0]["body"] == "Hello"


def test_active_send_cannot_be_resolved_early(client, auth, db, email):
    from datetime import datetime, timezone
    draft = draft_for(db, email, "sending")
    draft.send_started_at = datetime.now(timezone.utc)
    email.send_claimed = True
    db.commit()
    assert client.post(f"/drafts/{draft.id}/resolve", headers=auth, json={"status": "pending"}).status_code == 409


def test_failed_reclassification_does_not_create_a_draft(client, auth, db, email, fake_provider, monkeypatch):
    email.category = ""
    email.needs_reply = True
    db.commit()

    def fail(*args, **kwargs):
        raise RuntimeError("Model unavailable")

    monkeypatch.setattr(llm, "classify_email", fail)
    monkeypatch.setattr(llm, "draft_reply", lambda *a, **kw: pytest.fail("Do not draft unclassified mail"))
    assert client.post("/emails/sync", headers=auth).status_code == 200
    assert db.query(Draft).count() == 0


def test_account_list_and_backend_choose_the_same_mailbox(client, auth, db, account, user_token):
    from app.models import EmailAccount
    from app.services.registry import real_account
    from datetime import datetime, timedelta, timezone
    account.created_at = datetime.now(timezone.utc) - timedelta(days=2)
    newer = EmailAccount(user_id=decode_token(user_token), provider="gmail", email_address="new@gmail.com", credentials_json="{}")
    db.add(newer)
    db.commit()
    assert client.get("/auth/accounts", headers=auth).json()[0]["id"] == real_account(db, decode_token(user_token)).id == newer.id


def test_real_settings_matching_demo_values_are_preserved(client, auth, db, user_token):
    from app.routers.dev import DEMO_STYLE_PROFILE, DEMO_SIGNATURE
    row = AppSettings(user_id=decode_token(user_token), style_profile=DEMO_STYLE_PROFILE, signature=DEMO_SIGNATURE)
    db.add(row)
    db.commit()
    client.post("/dev/seed", headers=auth)
    client.post("/dev/clear", headers=auth)
    db.refresh(row)
    assert row.style_profile == DEMO_STYLE_PROFILE
    assert row.signature == DEMO_SIGNATURE


def test_retry_attempts_have_one_logical_operation_id(db, monkeypatch):
    attempts = []
    meta = SimpleNamespace(candidates_token_count=2, thoughts_token_count=0, prompt_token_count=3, cached_content_token_count=0)

    def generate(**kwargs):
        attempts.append(True)
        if len(attempts) == 1:
            raise RuntimeError("temporary outage")
        return SimpleNamespace(text="Hello", usage_metadata=meta)

    monkeypatch.setattr(llm, "get_client", lambda: SimpleNamespace(models=SimpleNamespace(generate_content=generate)))
    monkeypatch.setattr(llm, "_is_retryable", lambda exc: True)
    monkeypatch.setattr(llm.time, "sleep", lambda delay: None)
    assert llm._generate(operation="draft", model="x", system="", prompt="", max_output_tokens=10, db=db) == "Hello"
    records = db.query(LLMCall).order_by(LLMCall.id).all()
    assert len(records) == 2
    assert records[0].operation_id == records[1].operation_id != ""
    assert [r.attempt for r in records] == [1, 2]
    assert [r.success for r in records] == [False, True]
