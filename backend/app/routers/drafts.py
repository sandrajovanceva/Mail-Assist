import logging
import re
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import (
    AppSettings,
    Draft,
    DraftEdit,
    EmailAccount,
    EmailMessage,
    KnowledgeItem,
    User,
)
from ..schemas import (
    ApplyEditsOut,
    BulkSendOut,
    DraftCreate,
    DraftEditOut,
    DraftOut,
    DraftUpdate,
)
from ..security import get_current_user
from ..services import llm
from ..services.draft_preferences import snapshot
from ..services.scheduling import calendar_context
from ..services.delivery import deliver, finish_sent
from ..services.provider import ProviderError
from ..services.registry import get_provider

log = logging.getLogger(__name__)
router = APIRouter(tags=["drafts"])

VALID_STATUSES = ("pending", "approved", "rejected")


def reply_address(sender: str) -> str:
    """Extract a bare address from '"Name" <addr@x.com>'."""
    match = re.search(r"<([^>]+)>", sender)
    return match.group(1) if match else sender.strip()


def _owned_email(db: Session, user: User, email_id: int) -> EmailMessage:
    email = (
        db.query(EmailMessage)
        .join(EmailAccount)
        .filter(EmailMessage.id == email_id, EmailAccount.user_id == user.id)
        .first()
    )
    if not email:
        raise HTTPException(404, "Email not found")
    return email


def _owned_draft(db: Session, user: User, draft_id: int) -> Draft:
    draft = (
        db.query(Draft)
        .join(EmailMessage)
        .join(EmailAccount)
        .filter(Draft.id == draft_id, EmailAccount.user_id == user.id)
        .first()
    )
    if not draft:
        raise HTTPException(404, "Draft not found")
    return draft


def _record_edit(db: Session, user: User, draft: Draft) -> None:
    """Keep one 'AI wrote X, user saved Y' example per draft.

    This is the raw material for the glass-box learning loop: the user can see
    every example, and nothing reaches the style profile until they apply it.
    """
    existing = db.query(DraftEdit).filter_by(draft_id=draft.id).first()
    unchanged = draft.content.strip() == (draft.original_content or "").strip()

    if unchanged or not draft.original_content:
        if existing:  # user reverted their edit — drop the stale example
            db.delete(existing)
        return

    if existing:
        existing.edited = draft.content
        existing.applied = False  # changed again: needs re-applying
    else:
        db.add(
            DraftEdit(
                user_id=user.id,
                draft_id=draft.id,
                subject=draft.email.subject,
                original=draft.original_content,
                edited=draft.content,
            )
        )


# --------------------------------------------------------------------------
# drafts
# --------------------------------------------------------------------------


@router.post("/emails/{email_id}/draft", response_model=DraftOut)
def generate_draft(
    email_id: int,
    body: DraftCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    email = _owned_email(db, user, email_id)

    app_settings = db.query(AppSettings).filter_by(user_id=user.id).first()
    knowledge = db.query(KnowledgeItem).filter_by(user_id=user.id).all()
    tone = body.tone or (app_settings.default_tone if app_settings else "professional")

    calendar = calendar_context(db, user.id, email, body.instructions)
    instructions = "\n\n".join(part for part in (body.instructions, calendar) if part)
    preferences = snapshot(tone, app_settings, knowledge, body.instructions, calendar=calendar)
    try:
        content = llm.draft_reply(
            email,
            tone,
            app_settings,
            knowledge,
            instructions,
            db=db,
            user_id=user.id,
        )
    except llm.QuotaExceeded as exc:
        raise HTTPException(429, str(exc)) from exc

    # Retire previous versions only after generation succeeds. Keep their text
    # as history, but never allow a replaced version to be approved or sent.
    db.query(Draft).filter(
        Draft.email_id == email.id,
        Draft.status.in_(VALID_STATUSES),
    ).update({Draft.status: "superseded", Draft.auto_send_eligible: False})

    draft = Draft(
        email_id=email.id,
        content=content,
        original_content=content,  # frozen: what the AI produced, for comparison
        preferences_json=preferences,
        tone=tone,
        status="pending",
    )
    db.add(draft)
    db.commit()
    db.refresh(draft)
    return draft


@router.get("/drafts", response_model=list[DraftOut])
def list_drafts(
    status: str | None = None,
    email_id: int | None = None,
    limit: int = Query(100, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    query = (
        db.query(Draft)
        .join(EmailMessage)
        .join(EmailAccount)
        .filter(EmailAccount.user_id == user.id)
        .order_by(Draft.created_at.desc(), Draft.id.desc())
    )
    if status:
        query = query.filter(Draft.status == status)
    if email_id:
        query = query.filter(Draft.email_id == email_id)
    return query.offset(offset).limit(limit).all()


@router.put("/drafts/{draft_id}", response_model=DraftOut)
def update_draft(
    draft_id: int,
    body: DraftUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    draft = _owned_draft(db, user, draft_id)
    if draft.status in ("sending", "send_unknown"):
        raise HTTPException(409, "Resolve delivery status before editing this draft")
    if draft.status == "sent":
        raise HTTPException(400, "This draft has already been sent")
    if draft.status == "superseded":
        raise HTTPException(400, "This draft was replaced by a newer version")

    changes = {Draft.auto_send_eligible: False}
    if body.content is not None:
        changes[Draft.content] = body.content
    if body.status is not None:
        if body.status not in VALID_STATUSES:
            raise HTTPException(400, f"Status must be one of {', '.join(VALID_STATUSES)}")
        changes[Draft.status] = body.status
    changed = db.query(Draft).filter(
        Draft.id == draft.id, Draft.status.in_(VALID_STATUSES),
        Draft.status == draft.status, Draft.content == draft.content,
    ).update(changes, synchronize_session=False)
    if not changed:
        db.rollback()
        raise HTTPException(409, "Draft changed or is being sent; reload before editing")
    db.refresh(draft)
    if body.content is not None:
        _record_edit(db, user, draft)
    db.commit()
    db.refresh(draft)
    return draft


@router.post("/drafts/send-approved", response_model=BulkSendOut)
def send_approved(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Send every draft the user has approved.

    This is what approving is *for*: review a batch, mark the good ones, send
    them in one go. One failure doesn't abandon the rest — each is reported.
    """
    approved = (
        db.query(Draft)
        .join(EmailMessage)
        .join(EmailAccount)
        .filter(EmailAccount.user_id == user.id, Draft.status == "approved")
        .order_by(Draft.created_at)
        .all()
    )
    if not approved:
        raise HTTPException(400, "No approved drafts to send.")

    sent, failures = 0, []
    for draft in approved:
        db.refresh(draft)
        if draft.status != "approved":
            continue  # regeneration may have retired it since the batch loaded
        email = draft.email
        try:
            provider = get_provider(db, email.account)
            deliver(db, draft, provider)
            _record_edit(db, user, draft)
            db.commit()
        except Exception as exc:  # noqa: BLE001 - keep going, report at the end
            log.warning("Bulk send failed for draft %s: %s", draft.id, exc)
            failures.append(f"{email.subject[:40] or '(no subject)'}: {getattr(exc, 'detail', str(exc))}")
            continue
        draft.status = "sent"
        draft.sent_at = datetime.now(timezone.utc)
        sent += 1
    db.commit()
    return BulkSendOut(sent=sent, failed=len(failures), errors=failures[:5])


@router.post("/drafts/{draft_id}/send", response_model=DraftOut)
def send_draft(
    draft_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    draft = _owned_draft(db, user, draft_id)
    if draft.status == "sent":
        raise HTTPException(400, "Draft already sent")
    if draft.status == "rejected":
        raise HTTPException(400, "This draft was rejected — regenerate before sending")
    if draft.status == "superseded":
        raise HTTPException(400, "This draft was replaced by a newer version")

    email = draft.email
    try:
        provider = get_provider(db, email.account)
        deliver(db, draft, provider)
    except ProviderError as exc:
        raise HTTPException(400, str(exc)) from exc
    _record_edit(db, user, draft)  # capture the final sent wording
    draft.status = "sent"
    draft.sent_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(draft)
    return draft


@router.post("/drafts/{draft_id}/resolve", response_model=DraftOut)
def resolve_delivery(draft_id: int, body: DraftUpdate,
                     user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    draft = _owned_draft(db, user, draft_id)
    if draft.status not in ("sending", "send_unknown") or body.status not in ("sent", "pending"):
        raise HTTPException(400, "Confirm whether this uncertain delivery was sent")
    if draft.status == "sending" and draft.send_started_at and (
        datetime.now(timezone.utc) - draft.send_started_at.replace(tzinfo=timezone.utc)
    ).total_seconds() < 900:
        raise HTTPException(409, "Delivery is still in progress. Wait 15 minutes before resolving an interrupted send.")
    if not db.query(Draft).filter(Draft.id == draft.id, Draft.status == draft.status).update(
        {Draft.status: "resolving"}, synchronize_session=False
    ):
        db.rollback()
        raise HTTPException(409, "Delivery status has already changed")
    db.refresh(draft)
    if body.status == "sent":
        finish_sent(db, draft)
        _record_edit(db, user, draft)
    else:
        draft.status = "pending"
        draft.auto_send_eligible = False
        draft.email.send_claimed = False
    db.commit()
    db.refresh(draft)
    return draft


# --------------------------------------------------------------------------
# glass-box learning: the user's edits, and folding them into their style
# --------------------------------------------------------------------------


@router.get("/draft-edits", response_model=list[DraftEditOut])
def list_edits(
    applied: bool | None = None,
    limit: int = Query(100, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    query = (
        db.query(DraftEdit)
        .filter_by(user_id=user.id)
        .order_by(DraftEdit.created_at.desc())
    )
    if applied is not None:
        query = query.filter(DraftEdit.applied.is_(applied))
    return query.offset(offset).limit(limit).all()


@router.delete("/draft-edits/{edit_id}")
def delete_edit(
    edit_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    """Let the user discard an example they don't want shaping their style."""
    edit = db.query(DraftEdit).filter_by(id=edit_id, user_id=user.id).first()
    if not edit:
        raise HTTPException(404, "Not found")
    db.delete(edit)
    db.commit()
    return {"deleted": edit_id}


@router.post("/draft-edits/apply", response_model=ApplyEditsOut)
def apply_edits(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Rewrite the style profile using the edits the user hasn't applied yet."""
    pending = (
        db.query(DraftEdit)
        .filter_by(user_id=user.id, applied=False)
        .order_by(DraftEdit.created_at.desc())
        .limit(20)
        .all()
    )
    if not pending:
        raise HTTPException(400, "No new edits to learn from.")

    row = db.query(AppSettings).filter_by(user_id=user.id).first()
    if not row:
        row = AppSettings(user_id=user.id, user_name=user.name)
        db.add(row)
        db.flush()

    try:
        row.demo_style_seeded = False
        row.style_profile = llm.refine_style_profile(
            row.style_profile, pending, db=db, user_id=user.id
        )
    except llm.QuotaExceeded as exc:
        raise HTTPException(429, str(exc)) from exc

    for edit in pending:
        edit.applied = True
    db.commit()
    return ApplyEditsOut(applied=len(pending), style_profile=row.style_profile)
