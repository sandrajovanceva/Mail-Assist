"""The draft lifecycle and the glass-box learning loop."""

import pytest

AI_TEXT = "Hi Elena,\n\nThank you for your message. 14:00 works for me.\n\nBest regards,\nSandra"
USER_TEXT = "Hey — 14:00 works. See you then!"


@pytest.fixture
def stub_drafter(monkeypatch):
    monkeypatch.setattr(
        "app.routers.drafts.llm.draft_reply",
        lambda *args, **kwargs: AI_TEXT,
    )


def make_draft(client, auth, email):
    response = client.post(
        f"/emails/{email.id}/draft", headers=auth, json={"tone": "friendly"}
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_generate_freezes_the_ai_version(client, auth, email, stub_drafter):
    draft = make_draft(client, auth, email)
    assert draft["content"] == AI_TEXT
    assert draft["original_content"] == AI_TEXT  # the comparison baseline
    assert draft["status"] == "pending"
    assert draft["auto_sent"] is False


def test_preferences_are_historical_inputs(client, auth, email, stub_drafter, db, account):
    from app.models import AppSettings, KnowledgeItem
    settings = db.query(AppSettings).filter_by(user_id=account.user_id).first()
    if settings is None:
        settings = AppSettings(user_id=account.user_id)
        db.add(settings)
    settings.style_profile = "Keep routine replies brief."
    knowledge = KnowledgeItem(user_id=account.user_id, kind="tone_rule",
                              title="Greeting", content="Use a friendly greeting.")
    db.add(knowledge)
    db.commit()
    response = client.post(f"/emails/{email.id}/draft", headers=auth,
                           json={"tone": "friendly", "instructions": "Ask about the room."})
    assert response.status_code == 200
    draft = response.json()
    snapshot = draft["preferences_used"]
    assert {"kind": "style_profile", "title": "Writing style profile",
            "content": "Keep routine replies brief."} in snapshot
    assert {"kind": "instructions", "title": "Instructions for this draft",
            "content": "Ask about the room."} in snapshot
    assert {"kind": "tone_rule", "title": "Greeting",
            "content": "Use a friendly greeting."} in snapshot
    settings.style_profile = "Use detailed replies."
    db.delete(knowledge)
    db.commit()
    updated = client.put(f"/drafts/{draft['id']}", headers=auth,
                         json={"content": "Manually edited"}).json()
    assert updated["preferences_used"] == snapshot


def test_legacy_drafts_have_no_invented_preferences(client, auth, email, db):
    from app.models import Draft
    db.add(Draft(email_id=email.id, content="Old draft"))
    db.commit()
    rows = client.get("/drafts", headers=auth).json()
    assert rows[0]["preferences_used"] is None


@pytest.mark.parametrize("previous_status", ["pending", "approved", "rejected"])
def test_regeneration_retires_previous_unsent_version(
    previous_status, client, auth, email, stub_drafter, fake_provider
):
    first = make_draft(client, auth, email)
    client.put(f"/drafts/{first['id']}", headers=auth, json={"status": previous_status})
    latest = make_draft(client, auth, email)
    history = client.get("/drafts", headers=auth, params={"email_id": email.id}).json()
    assert [d["id"] for d in history] == [latest["id"], first["id"]]
    assert history[1]["status"] == "superseded"
    assert history[1]["content"] == first["content"]
    assert history[1]["auto_send_eligible"] is False
    assert client.post(f"/drafts/{first['id']}/send", headers=auth).status_code == 400
    for patch in ({"status": "approved"}, {"status": "pending"}, {"content": "Revived"}):
        assert client.put(f"/drafts/{first['id']}", headers=auth, json=patch).status_code == 400
    assert fake_provider.sent == []
    client.put(f"/drafts/{latest['id']}", headers=auth, json={"status": "approved"})
    result = client.post("/drafts/send-approved", headers=auth).json()
    assert result["sent"] == 1
    assert len(fake_provider.sent) == 1


def test_failed_regeneration_preserves_current_approved_draft(
    client, auth, email, stub_drafter, monkeypatch
):
    from app.services.llm import QuotaExceeded

    draft = make_draft(client, auth, email)
    client.put(f"/drafts/{draft['id']}", headers=auth, json={"status": "approved"})

    def fail(*args, **kwargs):
        raise QuotaExceeded("Quota exhausted")

    monkeypatch.setattr("app.routers.drafts.llm.draft_reply", fail)
    assert client.post(f"/emails/{email.id}/draft", headers=auth, json={}).status_code == 429
    rows = client.get("/drafts", headers=auth, params={"email_id": email.id}).json()
    assert len(rows) == 1
    assert rows[0]["status"] == "approved"


def test_regeneration_preserves_sent_history(
    client, auth, email, stub_drafter, fake_provider
):
    first = make_draft(client, auth, email)
    client.post(f"/drafts/{first['id']}/send", headers=auth)
    make_draft(client, auth, email)
    rows = client.get("/drafts", headers=auth, params={"status": "sent"}).json()
    assert [d["id"] for d in rows] == [first["id"]]
    assert rows[0]["sent_at"]


def test_editing_a_draft_records_a_learning_example(
    client, auth, email, stub_drafter, db
):
    draft = make_draft(client, auth, email)
    client.put(f"/drafts/{draft['id']}", headers=auth, json={"content": USER_TEXT})

    edits = client.get("/draft-edits", headers=auth).json()
    assert len(edits) == 1
    assert edits[0]["original"] == AI_TEXT
    assert edits[0]["edited"] == USER_TEXT
    assert edits[0]["applied"] is False


def test_reverting_an_edit_removes_the_example(client, auth, email, stub_drafter):
    draft = make_draft(client, auth, email)
    client.put(f"/drafts/{draft['id']}", headers=auth, json={"content": USER_TEXT})
    assert len(client.get("/draft-edits", headers=auth).json()) == 1

    client.put(f"/drafts/{draft['id']}", headers=auth, json={"content": AI_TEXT})
    assert client.get("/draft-edits", headers=auth).json() == []


def test_whitespace_only_changes_are_not_learning_examples(
    client, auth, email, stub_drafter
):
    draft = make_draft(client, auth, email)
    client.put(f"/drafts/{draft['id']}", headers=auth, json={"content": AI_TEXT + "\n\n"})
    assert client.get("/draft-edits", headers=auth).json() == []


def test_status_transitions(client, auth, email, stub_drafter):
    draft = make_draft(client, auth, email)

    approved = client.put(f"/drafts/{draft['id']}", headers=auth, json={"status": "approved"})
    assert approved.json()["status"] == "approved"

    rejected = client.put(f"/drafts/{draft['id']}", headers=auth, json={"status": "rejected"})
    assert rejected.json()["status"] == "rejected"

    bad = client.put(f"/drafts/{draft['id']}", headers=auth, json={"status": "banana"})
    assert bad.status_code == 400


def test_a_rejected_draft_cannot_be_sent(client, auth, email, stub_drafter, fake_provider):
    draft = make_draft(client, auth, email)
    client.put(f"/drafts/{draft['id']}", headers=auth, json={"status": "rejected"})

    response = client.post(f"/drafts/{draft['id']}/send", headers=auth)
    assert response.status_code == 400
    assert fake_provider.sent == []


def test_send_threads_the_reply_correctly(
    client, auth, email, stub_drafter, fake_provider
):
    draft = make_draft(client, auth, email)
    response = client.post(f"/drafts/{draft['id']}/send", headers=auth)

    assert response.json()["status"] == "sent"
    assert response.json()["sent_at"]
    sent = fake_provider.sent[0]
    assert sent["to"] == "elena@studio.co"  # display name stripped
    assert sent["thread_id"] == "thread-1"
    assert sent["in_reply_to"] == "<abc@mail.example.com>"  # threads in their client


def test_sending_twice_is_refused(client, auth, email, stub_drafter, fake_provider):
    draft = make_draft(client, auth, email)
    client.post(f"/drafts/{draft['id']}/send", headers=auth)

    second = client.post(f"/drafts/{draft['id']}/send", headers=auth)
    assert second.status_code == 400
    assert len(fake_provider.sent) == 1


def test_a_sent_draft_cannot_be_edited(client, auth, email, stub_drafter, fake_provider):
    draft = make_draft(client, auth, email)
    client.post(f"/drafts/{draft['id']}/send", headers=auth)

    response = client.put(f"/drafts/{draft['id']}", headers=auth, json={"content": "oops"})
    assert response.status_code == 400


def test_another_user_cannot_touch_your_draft(client, auth, email, stub_drafter):
    draft = make_draft(client, auth, email)
    from tests.conftest import register

    other = register(client, email="other@example.com")
    headers = {"Authorization": f"Bearer {other['token']}"}

    assert client.put(f"/drafts/{draft['id']}", headers=headers, json={"content": "x"}).status_code == 404
    assert client.post(f"/drafts/{draft['id']}/send", headers=headers).status_code == 404
    assert client.get("/drafts", headers=headers).json() == []


# ---- applying edits to the style profile ----


def test_apply_edits_rewrites_the_style_profile(
    client, auth, email, stub_drafter, monkeypatch, db, user_token
):
    draft = make_draft(client, auth, email)
    client.put(f"/drafts/{draft['id']}", headers=auth, json={"content": USER_TEXT})

    captured = {}

    def fake_refine(current_profile, edits, db=None, user_id=None):
        captured["count"] = len(edits)
        return "- Writes short, direct replies\n- Skips formal sign-offs"

    monkeypatch.setattr("app.routers.drafts.llm.refine_style_profile", fake_refine)

    response = client.post("/draft-edits/apply", headers=auth)
    assert response.status_code == 200
    assert captured["count"] == 1
    assert "short, direct" in response.json()["style_profile"]
    assert response.json()["applied"] == 1

    # the profile is persisted, and the example is marked as learned
    assert "short, direct" in client.get("/settings", headers=auth).json()["style_profile"]
    assert client.get("/draft-edits", headers=auth, params={"applied": False}).json() == []


def test_apply_with_nothing_new_is_a_clear_error(client, auth):
    response = client.post("/draft-edits/apply", headers=auth)
    assert response.status_code == 400
    assert "no new edits" in response.json()["detail"].lower()


def test_deleting_an_example_stops_it_being_learned(client, auth, email, stub_drafter):
    draft = make_draft(client, auth, email)
    client.put(f"/drafts/{draft['id']}", headers=auth, json={"content": USER_TEXT})
    edit_id = client.get("/draft-edits", headers=auth).json()[0]["id"]

    assert client.delete(f"/draft-edits/{edit_id}", headers=auth).status_code == 200
    assert client.get("/draft-edits", headers=auth).json() == []
