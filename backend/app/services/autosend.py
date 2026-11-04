"""Send existing, untouched background drafts to eligible mail without asking.

Deliberately conservative. Four independent guards have to agree before a
message leaves the mailbox unattended:

1. the user turned it on, and the email's category is on their allow-list;
2. the classifier did *not* flag the email as needing personal attention;
3. the user's daily auto-send budget isn't used up.
4. a reply is needed, and the thread is still unanswered immediately before sending.

Only one existing, untouched background draft may be sent. This service never
calls the model or creates a draft.

Anything auto-sent is recorded with `auto_sent=True` so the UI can show exactly
what went out on the user's behalf — and so the evaluation chapter can measure
how often auto-pilot was right.
"""

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from ..models import AppSettings, Draft, EmailAccount, EmailMessage
from . import needs_reply
from .provider import EmailProvider
from .delivery import deliver

log = logging.getLogger(__name__)

# 'important' is never eligible, whatever the user puts in their settings.
NEVER_AUTO_SEND = {"important"}


def allowed_categories(app_settings: AppSettings) -> set[str]:
    raw = (app_settings.auto_send_categories or "").split(",")
    return {c.strip() for c in raw if c.strip()} - NEVER_AUTO_SEND


def sent_today(db: Session, user_id: int) -> int:
    since = datetime.now(timezone.utc) - timedelta(days=1)
    return (
        db.query(Draft)
        .join(EmailMessage)
        .join(EmailAccount)
        .filter(
            EmailAccount.user_id == user_id,
            Draft.auto_sent.is_(True),
            Draft.sent_at >= since,
        )
        .count()
    )


def is_eligible(email: EmailMessage, app_settings: AppSettings | None) -> bool:
    if not app_settings or not app_settings.auto_send_enabled:
        return False
    if email.needs_attention:
        return False
    if not email.needs_reply:
        return False
    decision = needs_reply.decide(
        sender=email.sender,
        category=email.category,
        is_bulk=email.is_bulk,
        last_thread_sender=None,
        account_address=email.account.email_address,
    )
    if not decision.needs_reply:
        return False
    return email.category in allowed_categories(app_settings)


def maybe_auto_send(
    db: Session,
    user_id: int,
    email: EmailMessage,
    app_settings: AppSettings | None,
    provider: EmailProvider,
) -> Draft | None:
    """Send an existing automatic draft if every guard passes; never generate one."""
    if email.account.user_id != user_id or email.is_archived:
        return None
    if not is_eligible(email, app_settings):
        return None
    if sent_today(db, user_id) >= app_settings.auto_send_daily_limit:
        log.info("Auto-send daily limit reached for user %s", user_id)
        return None


    # Ambiguous/multiple versions require human review. This also prevents an
    # old pending copy being auto-sent after another version was already sent.
    drafts = db.query(Draft).filter(
        Draft.email_id == email.id, Draft.status != "superseded"
    ).all()
    if len(drafts) != 1:
        return None
    draft = drafts[0]

    def untouched() -> bool:
        return (
            draft.auto_send_eligible
            and draft.status == "pending"
            and not draft.auto_sent
            and draft.sent_at is None
            and bool(draft.content.strip())
            and draft.content == draft.original_content
        )

    if not untouched():
        return None

    def thread_still_needs_reply() -> bool:
        # Unknown thread state is acceptable for drafting, but not for sending
        # unattended. Provider lookup failures propagate without sending.
        last_sender = provider.thread_last_sender(email.thread_id)
        if not last_sender:
            log.info("Skipping auto-send for email %s: unknown thread state", email.id)
            return False
        return needs_reply.decide(
            sender=email.sender,
            category=email.category,
            is_bulk=email.is_bulk,
            last_thread_sender=last_sender,
            account_address=email.account.email_address,
        ).needs_reply

    if not thread_still_needs_reply():
        return None

    # A user may have saved/rejected this draft during the provider lookup.
    db.refresh(draft)
    if not untouched():
        return None

    return deliver(db, draft, provider, automatic=True)
