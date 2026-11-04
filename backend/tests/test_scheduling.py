import json
from types import SimpleNamespace

from app.models import AppSettings
from app.services.scheduling import calendar_context


def test_non_meeting_mail_does_not_check_calendar(db, monkeypatch):
    def unexpected(*args):
        raise AssertionError("Unnecessary calendar request")
    monkeypatch.setattr("app.routers.calendar.lookup_availability", unexpected)
    assert calendar_context(db, 1, SimpleNamespace(subject="Invoice", body_text="Thank you for your payment.")) == ""


def test_uses_saved_preferences_and_fresh_lookup(db, account, monkeypatch):
    row = AppSettings(user_id=account.user_id, calendar_preferences_json=json.dumps({
        "timezone": "Europe/Skopje", "work_start": "10:00", "work_end": "16:00", "duration_minutes": 45,
    }))
    db.add(row); db.commit()
    def lookup(query, user, session):
        assert user.id == account.user_id
        assert query.work_start.hour == 10
        assert query.duration_minutes == 45
        return {"timezone": query.timezone, "checked_at": "2030-01-07T08:00:00Z", "slots": [
            {"start": "2030-01-07T10:00:00+01:00", "end": "2030-01-07T10:45:00+01:00"}]}
    monkeypatch.setattr("app.routers.calendar.lookup_availability", lookup)
    result = calendar_context(db, account.user_id, SimpleNamespace(subject="Meeting", body_text="When are you free?"))
    assert "2030-01-07T10:00:00+01:00" in result
    assert "45" in result
    assert "not reserved" in result


def test_failed_lookup_does_not_invent_availability(db, account, monkeypatch):
    def fail(*args):
        raise RuntimeError("Provider unavailable")
    monkeypatch.setattr("app.routers.calendar.lookup_availability", fail)
    result = calendar_context(db, account.user_id, SimpleNamespace(subject="Состанок", body_text="Кога си слободна?"))
    assert "could not be verified" in result
    assert "Do not invent times" in result
