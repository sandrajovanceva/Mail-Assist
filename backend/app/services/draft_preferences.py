"""Historical record of user context supplied to the drafter, not model attribution."""
import json

from ..models import AppSettings, KnowledgeItem


def snapshot(tone: str, settings: AppSettings | None,
             knowledge: list[KnowledgeItem], instructions: str = "", calendar: str = "") -> str:
    entries = [{"kind": "tone", "title": "Reply tone", "content": tone}]
    if settings:
        for field, title in (("user_name", "Your name"), ("signature", "Signature"),
                             ("style_profile", "Writing style profile")):
            value = getattr(settings, field)
            if value:
                entries.append({"kind": field, "title": title, "content": value})
    entries.extend({"kind": item.kind, "title": item.title, "content": item.content}
                   for item in knowledge)
    if instructions:
        entries.append({"kind": "instructions", "title": "Instructions for this draft",
                        "content": instructions})
    if calendar:
        entries.append({"kind": "calendar", "title": "Calendar availability", "content": calendar})
    return json.dumps(entries, ensure_ascii=False)
