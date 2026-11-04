import json
from app.models import EmailAccount, EmailMessage, User
from app.services.assistant_tools import ToolCall, execute, run_tools


def test_inbox_search_and_priorities_stay_in_own_account(db, account, email):
    other = User(email="private@example.com", name="Private", password_hash="unused")
    db.add(other); db.flush()
    mailbox = EmailAccount(user_id=other.id, email_address="private@gmail.com", credentials_json="{}")
    db.add(mailbox); db.flush()
    private = EmailMessage(account_id=mailbox.id, provider_message_id="private", thread_id="private",
                           sender="Secret", subject=email.subject, body_text="Private", needs_attention=True)
    db.add(private)
    email.needs_attention = True
    db.commit()
    for tool in ("search_mail", "priorities"):
        result = execute(ToolCall(tool=tool), db, account.user_id)
        assert result["total_matches"] == 1
        assert result["emails"][0]["id"] == email.id
    assert execute(ToolCall(tool="read_mail", email_id=private.id), db, account.user_id) == {"error": "Email not found."}


def test_search_counts_pagination_and_literal_queries(db, account, email):
    assert execute(ToolCall(tool="search_mail", query="%"), db, account.user_id)["total_matches"] == 0
    result = execute(ToolCall(tool="search_mail", query="Elena", offset=1), db, account.user_id)
    assert result["total_matches"] == 1
    assert result["emails"] == []
    email.is_archived = True; db.commit()
    assert execute(ToolCall(tool="search_mail"), db, account.user_id)["total_matches"] == 0
    assert execute(ToolCall(tool="search_mail", include_archived=True), db, account.user_id)["total_matches"] == 1


def test_planning_runs_tools_and_returns_real_sources(db, account, email, monkeypatch):
    def plan(**kwargs):
        assert kwargs["operation"] == "assistant_plan"
        assert "today" in json.loads(kwargs["prompt"])
        return '{"calls":[{"tool":"search_mail","query":"Elena"}]}'
    monkeypatch.setattr("app.services.assistant_tools.llm._generate", plan)
    results, sources = run_tools("Find Elena's mail", [], None, db, account.user_id)
    assert results[0]["result"]["emails"][0]["id"] == email.id
    assert sources == [{"id": email.id, "subject": email.subject}]


def test_calendar_reads_fresh_data_without_changing_preferences(db, account, monkeypatch):
    def lookup(query, user, session):
        assert user.id == account.user_id
        assert str(query.start_date) == "2030-01-07"
        assert query.duration_minutes == 45
        return {"slots": [], "timezone": query.timezone, "checked_at": "2030-01-07T08:00:00Z", "email": account.email_address}
    monkeypatch.setattr("app.routers.calendar.lookup_availability", lookup)
    result = execute(ToolCall(tool="calendar", start_date="2030-01-07", duration_minutes=45), db, account.user_id)
    assert result["total_available_slots"] == 0
    assert result["reserved"] is False


def test_provider_failure_is_reported_as_error_not_free_time(db, account, monkeypatch):
    monkeypatch.setattr("app.services.assistant_tools.llm._generate", lambda **kwargs: '{"calls":[{"tool":"calendar"}]}')
    def unavailable(*args):
        raise RuntimeError("Provider offline")
    monkeypatch.setattr("app.routers.calendar.lookup_availability", unavailable)
    results, sources = run_tools("When am I free?", [], None, db, account.user_id)
    assert "error" in results[0]["result"]
    assert "slots" not in results[0]["result"]
    assert sources == []
