"""Prepared local presentation data. Never contacts Google or an LLM."""
import re
from datetime import datetime, time, timedelta
from email.utils import parseaddr


def prepared_reply(email, app_settings=None):
    macedonian = bool(re.search(r"[ЃѓЌќЉљЊњЏџЖжШшЧчЦц]", email.body_text))
    text = (email.subject + " " + email.body_text).lower()
    if macedonian:
        greeting = "Здраво,"
        if any(word in text for word in ("состанок", "термин", "средба")):
            body = "Ви благодарам за предлогот за состанок. Ќе проверам дали сум слободна во предложениот термин и ќе ви потврдам."
        elif any(word in text for word in ("корекции", "диплом", "труд")):
            body = "Ви благодарам за повратните информации. Ќе ги разгледам забелешките и ќе ги подготвам потребните корекции. Ќе ви ја испратам ажурираната верзија за преглед."
        elif "презентациј" in text:
            body = "Ви благодарам за прашањата за презентацијата. Ќе го проверам времетраењето и потребната опрема и ќе ви испратам потврда со сите детали."
        else:
            body = "Ви благодарам за пораката. Ќе ги разгледам информациите и ќе ви одговорам со потребните детали."
        closing = "Поздрав,"
    else:
        name = parseaddr(email.sender)[0].split()
        greeting = f"Hello {name[0]}," if name else "Hello,"
        if any(word in text for word in ("meeting", "call", "schedule")):
            body = "Thank you for suggesting a meeting. I will check whether I am free at the proposed time and confirm shortly."
        elif any(word in text for word in ("thesis", "review", "feedback")):
            body = "Thank you for the feedback. I will review the points you raised and prepare the necessary updates. I will send the revised version for your review."
        elif any(word in text for word in ("invoice", "payment", "amount")):
            body = "Thank you for flagging this. I will check the figures against our records and confirm the correct details before proceeding."
        else:
            body = "Thank you for your message. I will review the details and get back to you with the information you requested."
        closing = "Best regards,"
    signature = app_settings.signature if app_settings else ""
    if not signature:
        name = app_settings.user_name if app_settings else ""
        signature = f"{closing}\n{name}".strip()
    return f"{greeting}\n\n{body}\n\n{signature}"


def calendar_events(start_date, days, zone):
    events = []
    for offset in range(days):
        day = start_date + timedelta(days=offset)
        if day.weekday() >= 5:
            continue
        for hour, title in ((10, "Project check-in"), (14, "Thesis preparation")):
            start = datetime.combine(day, time(hour), zone)
            events.append({"title": title, "start": start.isoformat(),
                           "end": (start + timedelta(hours=1)).isoformat()})
    return events
