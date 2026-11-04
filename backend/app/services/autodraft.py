"""Write a reply ahead of time for mail that wants one.

The difference from `autosend` is the whole point: this produces a draft and
stops. Sending is handled separately, either by the user or by auto-send when
enabled. Only these untouched background drafts are eligible for auto-send.

Only called for messages `needs_reply` already approved, and capped per sync by
`auto_draft_limit` — drafting uses the expensive model, and a free-tier key has
a hard daily request ceiling.
"""

import logging

from sqlalchemy.orm import Session

from ..models import AppSettings, Draft, EmailMessage, KnowledgeItem
from . import llm
from .draft_preferences import snapshot
from .scheduling import calendar_context

log = logging.getLogger(__name__)


def draft_for(
    db: Session,
    user_id: int,
    email: EmailMessage,
    app_settings: AppSettings | None,
) -> Draft | None:
    """Generate and store a pending draft. Returns None if one already exists."""
    existing = db.query(Draft).filter_by(email_id=email.id).first()
    if existing:
        return None

    knowledge = db.query(KnowledgeItem).filter_by(user_id=user_id).all()
    tone = app_settings.default_tone if app_settings else "professional"

    calendar = calendar_context(db, user_id, email)
    preferences = snapshot(tone, app_settings, knowledge, calendar=calendar)
    content = llm.draft_reply(
        email, tone, app_settings, knowledge, extra_instructions=calendar, db=db, user_id=user_id
    )

    draft = Draft(
        email_id=email.id,
        content=content,
        original_content=content,
        preferences_json=preferences,
        tone=tone,
        status="pending",
        auto_send_eligible=True,
    )
    db.add(draft)
    db.commit()
    log.info("Drafted a reply for email %s (%s)", email.id, email.subject[:40])
    return draft
