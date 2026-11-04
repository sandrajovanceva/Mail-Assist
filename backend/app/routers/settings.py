from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import AppSettings, KnowledgeItem, User
from ..schemas import KnowledgeItemIn, KnowledgeItemOut, SettingsIn, SettingsOut
from ..security import get_current_user
from ..services import llm
from ..services.autosend import NEVER_AUTO_SEND
from ..services.provider import ProviderError
from ..services.registry import get_provider, real_account

router = APIRouter(prefix="/settings", tags=["settings"])

TONES = ("professional", "friendly", "formal", "casual")


def _settings_row(db: Session, user: User) -> AppSettings:
    row = db.query(AppSettings).filter_by(user_id=user.id).first()
    if not row:
        row = AppSettings(user_id=user.id, user_name=user.name)
        db.add(row)
        db.commit()
        db.refresh(row)
    return row


@router.get("", response_model=SettingsOut)
def get_settings(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return _settings_row(db, user)


@router.put("", response_model=SettingsOut)
def update_settings(
    body: SettingsIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    if body.default_tone not in TONES:
        raise HTTPException(400, f"Tone must be one of {', '.join(TONES)}")
    if body.auto_send_daily_limit < 0 or body.auto_draft_limit < 0:
        raise HTTPException(400, "Limits cannot be negative")

    requested = {c.strip() for c in body.auto_send_categories.split(",") if c.strip()}
    if requested & NEVER_AUTO_SEND:
        raise HTTPException(
            400,
            "Important mail can never be auto-sent — it always needs your approval.",
        )

    row = _settings_row(db, user)
    if row.signature != body.signature:
        row.demo_signature_seeded = False
    if row.style_profile != body.style_profile:
        row.demo_style_seeded = False
    row.user_name = body.user_name
    row.signature = body.signature
    row.default_tone = body.default_tone
    row.style_profile = body.style_profile
    row.auto_draft_enabled = body.auto_draft_enabled
    row.auto_draft_limit = body.auto_draft_limit
    row.auto_send_enabled = body.auto_send_enabled
    row.auto_send_categories = ",".join(sorted(requested))
    row.auto_send_daily_limit = body.auto_send_daily_limit
    db.commit()
    db.refresh(row)
    return row


@router.post("/style/learn", response_model=SettingsOut)
def learn_style(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Analyze the user's sent emails and store an AI-written style guide."""
    try:
        account = real_account(db, user.id)
        provider = get_provider(db, account)
    except ProviderError as exc:
        raise HTTPException(400, str(exc)) from exc

    sent = provider.fetch_sent_bodies(max_results=30)
    if not sent:
        raise HTTPException(400, "No sent emails found to learn from.")

    row = _settings_row(db, user)
    try:
        row.style_profile = llm.analyze_style(sent, db=db, user_id=user.id)
        row.demo_style_seeded = False
    except llm.QuotaExceeded as exc:
        raise HTTPException(429, str(exc)) from exc
    db.commit()
    db.refresh(row)
    return row


@router.get("/knowledge", response_model=list[KnowledgeItemOut])
def list_knowledge(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return (
        db.query(KnowledgeItem)
        .filter_by(user_id=user.id)
        .order_by(KnowledgeItem.created_at.desc())
        .all()
    )


@router.post("/knowledge", response_model=KnowledgeItemOut)
def add_knowledge(
    body: KnowledgeItemIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    if body.kind not in ("template", "tone_rule", "fact"):
        raise HTTPException(400, "kind must be template, tone_rule, or fact")
    item = KnowledgeItem(user_id=user.id, kind=body.kind, title=body.title, content=body.content)
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


@router.delete("/knowledge/{item_id}")
def delete_knowledge(
    item_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    item = db.query(KnowledgeItem).filter_by(id=item_id, user_id=user.id).first()
    if not item:
        raise HTTPException(404, "Not found")
    db.delete(item)
    db.commit()
    return {"deleted": item_id}
