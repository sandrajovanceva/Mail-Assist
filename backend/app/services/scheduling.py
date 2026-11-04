"""Fresh availability for meeting-related drafts, shared with the Calendar page."""
import json
import logging
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from ..models import AppSettings, User

log = logging.getLogger(__name__)
MEETING = re.compile(r"\b(meet(?:ing)?|schedule|reschedule|availability|available|appointment|call|состанок|средба|термин|закаж\w*|достап\w*|слобод\w*)\b", re.I)


def calendar_context(db, user_id, email, instructions="") -> str:
    if not MEETING.search(f"{email.subject}\n{email.body_text[:8000]}\n{instructions}"):
        return ""
    from ..routers.calendar import AvailabilityIn, lookup_availability
    row = db.query(AppSettings).filter_by(user_id=user_id).first()
    try:
        saved = json.loads(row.calendar_preferences_json) if row and row.calendar_preferences_json else {}
        zone = ZoneInfo(saved.get("timezone", "Europe/Skopje"))
        query = AvailabilityIn(start_date=datetime.now(zone).date(), **saved)
        result = lookup_availability(query, db.get(User, user_id), db)
        # Keep a few alternatives per date, rather than filling the prompt with adjacent slots.
        options = []
        dates = {}
        for slot in result["slots"]:
            day = slot["start"][:10]
            if dates.get(day, 0) < 3:
                options.append(slot)
                dates[day] = dates.get(day, 0) + 1
        return (
            "Calendar availability checked for this draft: " + json.dumps({
                "timezone": result["timezone"], "checked_at": result["checked_at"],
                "duration_minutes": query.duration_minutes, "days_checked": query.days,
                "available_options": options,
            }) + "\nOnly if this email actually asks to arrange a meeting, suggest suitable options from these "
            "checked times. Preserve dates and timezone. Do not accept a requested time unless it matches "
            "a checked option. If the requested date or meeting length is not covered, ask for confirmation. "
            "No options means no availability was found in this window, not that the entire calendar is busy. "
            "Do not claim a meeting is booked; these times are not reserved."
        )
    except Exception:
        log.warning("Calendar availability could not be checked for draft", exc_info=True)
        return ("Calendar availability could not be verified. If the email asks to arrange a meeting, "
                "say availability needs confirmation. Do not invent times or accept a proposed time as free.")
