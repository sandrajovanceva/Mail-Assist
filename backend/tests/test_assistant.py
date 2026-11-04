import json
import pytest

from app.models import Draft


@pytest.fixture(autouse=True)
def no_live_tool_planning(monkeypatch):
    monkeypatch.setattr("app.routers.assistant.run_tools", lambda *args: ([], []))


def test_requires_login(client):
    assert client.post("/assistant/chat", json={"message": "Hello"}).status_code == 401
    assert client.post("/assistant/chat/stream", json={"message": "Hello"}).status_code == 401


def test_stream_forwards_answer_then_validated_revision(client, auth, email, db, monkeypatch):
    draft = Draft(email_id=email.id, content="Original")
    db.add(draft); db.commit()
    def chunks(*args):
        yield '{"answer":"Hel'
        yield 'lo\\nthere",'
        yield '"suggested_reply":"New reply"}'
    monkeypatch.setattr("app.routers.assistant.llm.stream_assistant", chunks)
    response = client.post("/assistant/chat/stream", headers=auth, json={"email_id": email.id, "message": "Rewrite"})
    events = [json.loads(line) for line in response.text.splitlines()]
    assert ''.join(event["text"] for event in events if event["type"] == "delta") == "Hello\nthere"
    assert events[-1]["type"] == "done"
    assert events[-1]["draft_id"] == draft.id
    db.refresh(draft)
    assert draft.content == "Original"


def test_stream_failure_keeps_partial_text_without_enabling_revision(client, auth, monkeypatch):
    def chunks(*args):
        yield '{"answer":"Partial'
        raise RuntimeError("Network disconnected")
    monkeypatch.setattr("app.routers.assistant.llm.stream_assistant", chunks)
    response = client.post("/assistant/chat/stream", headers=auth, json={"message": "Hello"})
    events = [json.loads(line) for line in response.text.splitlines()]
    assert events[-1]["type"] == "error"
    assert not any(event["type"] == "done" for event in events)


def test_partial_json_answer_handles_split_escapes():
    from app.routers.assistant import partial_answer
    assert partial_answer('{"answer":"Hello\\') == "Hello"
    assert partial_answer('{"answer":"Hello\\nworld') == "Hello\nworld"
    assert partial_answer('{"answer":"\\u04') == ""
    assert partial_answer('{"answer":"\\u0410') == "А"


def test_stream_uses_app_wide_results_without_selected_email(client, auth, monkeypatch):
    sources = [{"id": 12, "subject": "Project review"}]
    monkeypatch.setattr("app.routers.assistant.run_tools", lambda *args: (
        [{"tool": "priorities", "result": {"emails": sources}}], sources))
    def generate(system, prompt, schema, user_id):
        context = json.loads(prompt)["context"]
        assert context["tool_results"][0]["tool"] == "priorities"
        assert "email" not in context
        assert "English question requires an English answer" in system
        yield '{"answer":"Review Project review.","suggested_reply":null}'
    monkeypatch.setattr("app.routers.assistant.llm.stream_assistant", generate)
    response = client.post("/assistant/chat/stream", headers=auth, json={"message": "What needs my attention?"})
    events = [json.loads(line) for line in response.text.splitlines()]
    assert events[-1]["type"] == "done"
    assert events[-1]["sources"] == sources


def test_revision_is_preview_only(client, auth, email, db, monkeypatch):
    snapshot = [{"kind": "tone", "title": "Reply tone", "content": "friendly"}]
    draft = Draft(email_id=email.id, content="Saved reply", preferences_json=json.dumps(snapshot))
    db.add(draft); db.commit()
    def generate(**kwargs):
        context = json.loads(kwargs["prompt"])["context"]
        assert context["saved_draft"]["preferences_supplied_at_generation"] == snapshot
        return '{"answer":"Here is a revision", "suggested_reply":"Short reply"}'
    monkeypatch.setattr("app.routers.assistant.llm._generate", generate)
    response = client.post("/assistant/chat", headers=auth, json={"email_id": email.id, "message": "Shorten it"})
    assert response.status_code == 200
    assert response.json()["suggested_reply"] == "Short reply"
    db.refresh(draft)
    assert draft.content == "Saved reply"


def test_missing_email_rejected_before_model_call(client, auth, monkeypatch):
    def fail(**kwargs):
        raise AssertionError("Unauthorized context must never reach model")
    monkeypatch.setattr("app.routers.assistant.llm._generate", fail)
    assert client.post("/assistant/chat", headers=auth, json={"email_id": 999999, "message": "Read"}).status_code == 404


def test_sent_draft_cannot_be_revised(client, auth, email, db, monkeypatch):
    db.add(Draft(email_id=email.id, content="Sent reply", status="sent")); db.commit()
    monkeypatch.setattr("app.routers.assistant.llm._generate", lambda **kw: '{"answer":"Suggestion", "suggested_reply":"Changed"}')
    response = client.post("/assistant/chat", headers=auth, json={"email_id": email.id, "message": "Rewrite"})
    assert response.json()["suggested_reply"] is None
