"""Durably claim a delivery before touching Gmail; uncertain sends never retry."""
from datetime import datetime, timezone
from email.utils import parseaddr

from fastapi import HTTPException
from sqlalchemy import update

from ..models import Draft, EmailMessage


def deliver(db, draft, provider, *, automatic=False):
    original_status = draft.status
    if original_status not in ("pending", "approved"):
        raise HTTPException(409, "This draft cannot be sent in its current state")
    if not draft.content.strip():
        raise HTTPException(400, "Cannot send an empty reply")
    if db.query(Draft.id).filter(Draft.email_id == draft.email_id, Draft.status == "sent").first():
        raise HTTPException(409, "This email has already been answered")
    claimed = db.execute(update(EmailMessage).where(
        EmailMessage.id == draft.email_id, EmailMessage.send_claimed.is_(False)
    ).values(send_claimed=True)).rowcount
    conditions = [Draft.id == draft.id, Draft.status == original_status,
                  Draft.content == draft.content]
    if automatic:
        conditions.append(Draft.auto_send_eligible.is_(True))
    if not claimed or not db.execute(update(Draft).where(*conditions).values(
        status="sending", auto_send_eligible=False, send_started_at=datetime.now(timezone.utc)
    )).rowcount:
        db.rollback()
        raise HTTPException(409, "A reply is already sent, being sent, or awaiting delivery confirmation")
    db.commit()  # survives process failure or a second request
    db.refresh(draft)
    email = draft.email
    try:
        sent_id = provider.send_reply(
            to=parseaddr(email.sender)[1] or email.sender.strip(), subject=email.subject,
            body=draft.content, thread_id=email.thread_id,
            in_reply_to=email.message_id_header,
        )
    except Exception as exc:
        draft.status = "send_unknown"
        db.commit()
        raise HTTPException(409, "Delivery could not be confirmed. Check Sent mail before resolving this draft; it will not be retried automatically.") from exc
    draft.provider_sent_id = sent_id or ""
    draft.auto_sent = automatic
    finish_sent(db, draft)
    db.commit()
    db.refresh(draft)
    return draft


def finish_sent(db, draft):
    draft.status = "sent"
    draft.sent_at = datetime.now(timezone.utc)
    draft.auto_send_eligible = False
    email = draft.email
    # A reply answers the conversation up to this message, not newer arrivals.
    db.query(EmailMessage).filter(
        EmailMessage.account_id == email.account_id,
        EmailMessage.thread_id == email.thread_id,
        EmailMessage.received_at <= email.received_at,
    ).update({EmailMessage.needs_reply: False,
              EmailMessage.needs_reply_reason: "you replied in this thread"})
    db.query(Draft).filter(Draft.email_id == email.id, Draft.id != draft.id,
                          Draft.status.in_(["pending", "approved", "rejected"])).update(
        {Draft.status: "superseded", Draft.auto_send_eligible: False})
