from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from app.routers.calendar import AvailabilityIn, available_slots
from app.services.gmail import CALENDAR_SCOPE


def test_busy_boundaries_and_past_slots():
    body = AvailabilityIn(start_date=date(2030, 1, 7), days=1, timezone="UTC",
                          work_start="09:00", work_end="11:00")
    busy = [{"start": "2030-01-07T09:30:00Z", "end": "2030-01-07T10:00:00Z"}]
    slots = available_slots(body, busy, datetime(2030, 1, 7, 9, 15, tzinfo=timezone.utc))
    assert [s["start"] for s in slots] == ["2030-01-07T10:00:00+00:00", "2030-01-07T10:30:00+00:00"]


def test_weekends_and_dst_use_local_working_hours():
    body = AvailabilityIn(start_date=date(2030, 3, 29), days=4, timezone="Europe/Skopje",
                          work_start="09:00", work_end="09:30")
    slots = available_slots(body, [], datetime(2030, 1, 1, tzinfo=timezone.utc))
    assert [s["start"] for s in slots] == ["2030-03-29T09:00:00+01:00", "2030-04-01T09:00:00+02:00"]


@pytest.mark.parametrize("changes", [{"timezone": "invalid"}, {"work_start": "18:00"}, {"weekdays": []}, {"weekdays": [7]}])
def test_invalid_preferences_rejected(changes):
    with pytest.raises(HTTPException):
        available_slots(AvailabilityIn(start_date=date(2030, 1, 7), **changes), [], datetime.now(timezone.utc))


def test_calendar_requires_login(client):
    assert client.get("/calendar/status").status_code == 401


def test_ungranted_permission_does_not_query_google(client, auth, account, monkeypatch):
    build = MagicMock()
    monkeypatch.setattr("app.routers.calendar.build", build)
    assert client.get("/calendar/status", headers=auth).json()["connected"] is False
    assert client.post("/calendar/availability", headers=auth, json={"start_date": "2030-01-07"}).status_code == 403
    build.assert_not_called()


def test_only_owned_accounts_used(client, auth, db):
    from app.models import EmailAccount, User
    other = User(email="other@example.com", password_hash="unused", name="Other")
    db.add(other)
    db.flush()
    db.add(EmailAccount(user_id=other.id, provider="gmail", email_address="other@gmail.com",
                        credentials_json='{"scopes": ["' + CALENDAR_SCOPE + '"]}'))
    db.commit()
    assert client.get("/calendar/status", headers=auth).json() == {"connected": False, "email": None}


@pytest.mark.parametrize("calendar_data,expected", [({"busy": []}, 200), ({"errors": [{"reason": "notFound"}]}, 502), (None, 502)])
def test_freebusy_errors_never_become_available_slots(client, auth, account, db, monkeypatch, calendar_data, expected):
    import json
    account.credentials_json = json.dumps({"scopes": [CALENDAR_SCOPE]})
    db.commit()
    monkeypatch.setattr("app.routers.calendar.credentials_from_account", lambda _: SimpleNamespace(valid=True))
    service = MagicMock()
    service.freebusy.return_value.query.return_value.execute.return_value = {"calendars": {"primary": calendar_data}}
    monkeypatch.setattr("app.routers.calendar.build", lambda *args, **kwargs: service)
    response = client.post("/calendar/availability", headers=auth, json={"start_date": "2030-01-07", "days": 1})
    assert response.status_code == expected
    if expected == 200:
        assert response.json()["slots"]
    else:
        assert "slots" not in response.json()


def test_calendar_scope_is_opt_in(client, user_token, monkeypatch):
    from urllib.parse import urlparse, parse_qs
    from app.config import settings
    monkeypatch.setattr(settings, "google_client_id", "test-client")
    for enabled in (False, True):
        response = client.get(f"/auth/google/login?token={user_token}&calendar={str(enabled).lower()}", follow_redirects=False)
        scopes = parse_qs(urlparse(response.headers["location"]).query)["scope"][0].split()
        assert (CALENDAR_SCOPE in scopes) == enabled
