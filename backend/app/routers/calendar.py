"""Read only free/busy lookup for the signed-in user's primary Google calendar."""
from datetime import date, datetime, time, timedelta, timezone
import json
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException
from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import AppSettings, EmailAccount, User
from ..security import get_current_user
from ..services.gmail import CALENDAR_SCOPE, credentials_from_account, credentials_to_json, granted_scopes

router = APIRouter(prefix="/calendar", tags=["calendar"])


def linked_account(db, user):
    return db.query(EmailAccount).filter_by(user_id=user.id, provider="gmail").order_by(
        EmailAccount.created_at.desc(), EmailAccount.id.desc()).first()


@router.get("/status")
def status(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    account = linked_account(db, user)
    result = {"connected": bool(account and CALENDAR_SCOPE in granted_scopes(account)),
              "email": account.email_address if account else None}
    preferences = db.query(AppSettings).filter_by(user_id=user.id).first()
    if preferences and preferences.calendar_preferences_json:
        result["preferences"] = json.loads(preferences.calendar_preferences_json)
    return result


class AvailabilityIn(BaseModel):
    start_date: date
    days: int = Field(default=7, ge=1, le=14)
    timezone: str = "Europe/Skopje"
    work_start: time = time(9)
    work_end: time = time(17)
    duration_minutes: int = Field(default=30, ge=15, le=240)
    weekdays: list[int] = Field(default_factory=lambda: [0, 1, 2, 3, 4])


def available_slots(body: AvailabilityIn, busy: list[dict], now: datetime):
    try:
        zone = ZoneInfo(body.timezone)
    except ZoneInfoNotFoundError:
        raise HTTPException(422, "Choose a valid timezone, for example Europe/Skopje.")
    if body.work_start >= body.work_end or body.work_start.tzinfo or body.work_end.tzinfo:
        raise HTTPException(422, "Working hours must start before they end, within one day.")
    if not body.weekdays or any(day not in range(7) for day in body.weekdays):
        raise HTTPException(422, "Choose at least one valid working day.")
    periods = [(datetime.fromisoformat(item["start"].replace("Z", "+00:00")),
                datetime.fromisoformat(item["end"].replace("Z", "+00:00"))) for item in busy]
    result = []
    duration = timedelta(minutes=body.duration_minutes)
    for offset in range(body.days):
        day = body.start_date + timedelta(days=offset)
        if day.weekday() not in body.weekdays:
            continue
        cursor = datetime.combine(day, body.work_start, zone).astimezone(timezone.utc)
        end = datetime.combine(day, body.work_end, zone).astimezone(timezone.utc)
        while cursor + duration <= end:
            finish = cursor + duration
            if cursor >= now and not any(cursor < stop and finish > start for start, stop in periods):
                result.append({"start": cursor.astimezone(zone).isoformat(),
                               "end": finish.astimezone(zone).isoformat()})
            cursor += duration
    return result


@router.post("/availability")
def availability(body: AvailabilityIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    result = lookup_availability(body, user, db)
    row = db.query(AppSettings).filter_by(user_id=user.id).first()
    if row is None:
        row = AppSettings(user_id=user.id)
        db.add(row)
    row.calendar_preferences_json = json.dumps(body.model_dump(mode="json", exclude={"start_date"}))
    db.commit()
    return result


def lookup_availability(body: AvailabilityIn, user: User, db: Session):
    # Validate before contacting Google. Calendar errors must never appear as free time.
    now = datetime.now(timezone.utc)
    available_slots(body, [], now)
    zone = ZoneInfo(body.timezone)
    account = linked_account(db, user)
    if not account or CALENDAR_SCOPE not in granted_scopes(account):
        raise HTTPException(403, "Connect Google Calendar to check availability.")
    try:
        credentials = credentials_from_account(account)
        if not credentials.valid:
            credentials.refresh(Request())
            account.credentials_json = credentials_to_json(credentials)
            db.commit()
        service = build("calendar", "v3", credentials=credentials, cache_discovery=False)
        data = service.freebusy().query(body={
            "timeMin": datetime.combine(body.start_date, time.min, zone).isoformat(),
            "timeMax": datetime.combine(body.start_date + timedelta(days=body.days), time.min, zone).isoformat(),
            "timeZone": body.timezone, "items": [{"id": "primary"}],
        }).execute()
    except RefreshError:
        raise HTTPException(403, "Reconnect Google Calendar: its permission has expired.")
    except HttpError as exc:
        if exc.resp.status == 403:
            raise HTTPException(403, "Calendar access failed. Enable Google Calendar API in your Google Cloud project and reconnect Calendar.")
        raise HTTPException(502, "Google Calendar is unavailable. Please try again.")
    except (OSError, TimeoutError):
        raise HTTPException(502, "Could not reach Google Calendar. Please try again.")
    calendar = data.get("calendars", {}).get("primary")
    if not calendar or calendar.get("errors") or "busy" not in calendar:
        raise HTTPException(502, "Google could not confirm availability. Please try again.")
    return {"slots": available_slots(body, calendar["busy"], now), "timezone": body.timezone,
            "email": account.email_address, "checked_at": now.isoformat()}
