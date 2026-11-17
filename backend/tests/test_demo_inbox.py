from app.models import Draft, EmailAccount, EmailMessage


def test_samples_are_repeatable_isolated_and_cannot_send(client, auth, db, monkeypatch):
    def unexpected_send(*args, **kwargs):
        raise AssertionError("Sample mail must never reach Gmail")
    monkeypatch.setattr("app.services.gmail.GmailProvider.send_reply", unexpected_send)
    first = client.post("/dev/seed", headers=auth)
    assert first.status_code == 200
    assert first.json()["created"] == 30
    assert client.post("/dev/seed", headers=auth).json()["created"] == 0
    samples = client.get("/emails", headers=auth).json()
    assert len(samples) == 30
    assert all(row["is_demo"] for row in samples)
    assert {row["category"] for row in samples} == {"important", "routine", "newsletter", "spam"}
    assert any(row["needs_reply"] for row in samples)
    assert any(row["needs_attention"] for row in samples)
    demo = db.query(EmailAccount).filter_by(provider="demo").one()
    real = EmailAccount(user_id=demo.user_id, provider="gmail",
                        email_address="real@example.com", credentials_json="{}")
    db.add(real)
    db.flush()
    message = EmailMessage(account_id=real.id, provider_message_id="real-1",
                           thread_id="real-1", sender="sender@example.com", subject="Real mail")
    db.add(message)
    db.commit()
    assert not client.get(f"/emails/{message.id}", headers=auth).json()["is_demo"]
    draft = db.query(Draft).filter_by(status="pending").first()
    response = client.post(f"/drafts/{draft.id}/send", headers=auth)
    assert response.status_code == 400
    assert client.post("/dev/clear", headers=auth).json()["deleted"] == 30
    assert client.get(f"/emails/{message.id}", headers=auth).status_code == 200


def test_presentation_sync_waits_then_combines_samples_with_gmail(client, auth, db, account, fake_provider, monkeypatch):
    from app.config import settings
    from app.routers import emails
    from app.services.provider import FetchedEmail
    from datetime import datetime, timezone
    monkeypatch.setattr(settings, "local_demo_sync", True)
    delays = []

    def wait(seconds):
        assert client.get("/emails", headers=auth).json() == []
        delays.append(seconds)

    monkeypatch.setattr(emails.time, "sleep", wait)
    fake_provider.inbox = [FetchedEmail(
        provider_message_id="new-real", thread_id="new-real", sender="news@example.com",
        recipient=account.email_address, subject="Real incoming newsletter", snippet="News",
        body_text="News", received_at=datetime.now(timezone.utc), is_bulk=True)]
    monkeypatch.setattr(emails, "_classify_one", lambda *args: {
        "category": "newsletter", "needs_attention": False, "reason": "Test classification"})
    real = EmailMessage(account_id=account.id, provider_message_id="existing-real",
                        thread_id="existing-real", sender="sender@example.com")
    db.add(real)
    db.commit()
    assert client.get("/dev/config", headers=auth).json()["local_demo_sync"] is True
    assert client.get("/emails/counts", headers=auth).json()["all"] == 0
    result = client.post("/emails/sync", headers=auth)
    assert result.status_code == 200
    job = client.get(f"/emails/sync/{result.json()['id']}", headers=auth).json()
    assert job["status"] == "done" and job["new_count"] == 31
    assert delays == [10]
    rows = client.get("/emails", headers=auth).json()
    assert len(rows) == 32
    assert sum(not row["is_demo"] for row in rows) == 2
    assert client.get("/emails/counts", headers=auth).json()["all"] == 32
    assert client.post("/emails/sync", headers=auth).status_code == 200
    assert delays == [10]
    assert db.get(EmailMessage, real.id) is not None
    # No rows are deleted or changed when ordinary Gmail mode is restored.
    monkeypatch.setattr(settings, "local_demo_sync", False)
    assert len(client.get("/emails", headers=auth).json()) == 32


def test_prepared_replies_and_demo_calendar_do_not_call_external_services(client, auth, db, monkeypatch):
    from app.config import settings
    from datetime import datetime, timedelta
    from app.services import llm
    monkeypatch.setattr(settings, "local_demo_replies", True)
    monkeypatch.setattr(settings, "local_demo_calendar", True)

    def unexpected(*args, **kwargs):
        raise AssertionError("Demo data must not use an external service")

    monkeypatch.setattr(llm, "get_client", unexpected)
    monkeypatch.setattr("app.routers.calendar.build", unexpected)
    assert client.post("/dev/seed", headers=auth).status_code == 200
    messages = client.get("/emails", headers=auth).json()
    reply_message = next(row for row in messages if row["needs_reply"])
    generated = client.post(f"/emails/{reply_message['id']}/draft", headers=auth, json={})
    assert generated.status_code == 200
    draft = generated.json()
    assert any(item["kind"] == "demo" for item in draft["preferences_used"])
    assert not draft["auto_send_eligible"]
    assert client.get("/calendar/status", headers=auth).json()["demo"] is True
    tomorrow = (datetime.now().date() + timedelta(days=1)).isoformat()
    result = client.post("/calendar/availability", headers=auth,
                         json={"start_date": tomorrow, "days": 7})
    assert result.status_code == 200
    body = result.json()
    assert body["demo"] and body["busy_events"] and body["slots"]
    for slot in body["slots"]:
        assert not any(slot["start"] < event["end"] and slot["end"] > event["start"]
                       for event in body["busy_events"])
