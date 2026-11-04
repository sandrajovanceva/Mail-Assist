from datetime import datetime, timezone
import json

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    """A platform user (signs in with email + password)."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255), default="")
    password_hash: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    accounts: Mapped[list["EmailAccount"]] = relationship(back_populates="user")


class EmailAccount(Base):
    """A connected mailbox (Gmail for now; provider field keeps it extensible)."""

    __tablename__ = "email_accounts"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    provider: Mapped[str] = mapped_column(String(32), default="gmail")
    email_address: Mapped[str] = mapped_column(String(255))
    credentials_json: Mapped[str] = mapped_column(Text)  # OAuth tokens (refresh + access)
    # Gmail's change cursor: lets the next sync ask "what changed since?" instead
    # of re-listing (and re-classifying) the whole inbox.
    last_history_id: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    user: Mapped[User] = relationship(back_populates="accounts")
    emails: Mapped[list["EmailMessage"]] = relationship(back_populates="account")


class EmailMessage(Base):
    __tablename__ = "email_messages"
    # One row per provider message, enforced by the database. Two syncs running
    # at once would otherwise each snapshot "what we already have" before the
    # other commits, and insert the same message twice.
    __table_args__ = (
        Index(
            "ix_email_messages_account_provider_uq",
            "account_id",
            "provider_message_id",
            unique=True,
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("email_accounts.id"))
    provider_message_id: Mapped[str] = mapped_column(String(255), index=True)
    thread_id: Mapped[str] = mapped_column(String(255), index=True)
    # RFC 822 Message-ID of the original — needed so our replies thread correctly
    # in the recipient's client (In-Reply-To / References).
    message_id_header: Mapped[str] = mapped_column(String(512), default="")
    sender: Mapped[str] = mapped_column(String(512))
    recipient: Mapped[str] = mapped_column(String(512), default="")
    subject: Mapped[str] = mapped_column(String(1024), default="")
    snippet: Mapped[str] = mapped_column(Text, default="")
    body_text: Mapped[str] = mapped_column(Text, default="")
    received_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    # mailbox state
    is_read: Mapped[bool] = mapped_column(Boolean, default=False)
    is_archived: Mapped[bool] = mapped_column(Boolean, default=False)
    is_bulk: Mapped[bool] = mapped_column(Boolean, default=False)
    send_claimed: Mapped[bool] = mapped_column(Boolean, default=False)

    # whether a reply is wanted, decided from headers and thread state before
    # any model call — see services/needs_reply.py
    needs_reply: Mapped[bool] = mapped_column(Boolean, default=False)
    needs_reply_reason: Mapped[str] = mapped_column(String(255), default="")

    # AI enrichment
    category: Mapped[str] = mapped_column(String(32), default="")  # important|routine|newsletter|spam
    needs_attention: Mapped[bool] = mapped_column(Boolean, default=False)
    classification_reason: Mapped[str] = mapped_column(Text, default="")
    summary: Mapped[str] = mapped_column(Text, default="")

    account: Mapped[EmailAccount] = relationship(back_populates="emails")
    drafts: Mapped[list["Draft"]] = relationship(back_populates="email")


class Draft(Base):
    """An AI-generated reply awaiting approval (or already sent)."""

    __tablename__ = "drafts"

    id: Mapped[int] = mapped_column(primary_key=True)
    email_id: Mapped[int] = mapped_column(ForeignKey("email_messages.id"))
    content: Mapped[str] = mapped_column(Text)
    original_content: Mapped[str] = mapped_column(Text, default="")  # AI's untouched version
    preferences_json: Mapped[str] = mapped_column(Text, default="")

    @property
    def preferences_used(self) -> list[dict] | None:
        # Older drafts have no historical snapshot; never infer it from current settings.
        return json.loads(self.preferences_json) if self.preferences_json else None

    tone: Mapped[str] = mapped_column(String(32), default="professional")
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending|approved|sent|rejected|superseded
    auto_sent: Mapped[bool] = mapped_column(Boolean, default=False)  # sent without human approval
    # Only background-created drafts untouched by the user may be auto-sent.
    # Existing/manual drafts default to ineligible, including after migration.
    auto_send_eligible: Mapped[bool] = mapped_column(Boolean, default=False)
    provider_sent_id: Mapped[str] = mapped_column(String(255), default="")
    send_started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    email: Mapped[EmailMessage] = relationship(back_populates="drafts")


class DraftEdit(Base):
    """One learning example: what the AI drafted vs. how the user rewrote it.
    Unlike commercial tools, these are visible to the user and only folded into
    the style profile when the user asks ('glass box' learning)."""

    __tablename__ = "draft_edits"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    draft_id: Mapped[int] = mapped_column(ForeignKey("drafts.id"), unique=True)
    subject: Mapped[str] = mapped_column(String(1024), default="")
    original: Mapped[str] = mapped_column(Text)
    edited: Mapped[str] = mapped_column(Text)
    applied: Mapped[bool] = mapped_column(Boolean, default=False)  # folded into style profile?
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class KnowledgeItem(Base):
    """User-editable knowledge base: reply templates, tone rules, and facts
    the drafter should know (e.g. 'our refund policy is 30 days')."""

    __tablename__ = "knowledge_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    kind: Mapped[str] = mapped_column(String(16), default="template")  # template|tone_rule|fact
    title: Mapped[str] = mapped_column(String(255))
    content: Mapped[str] = mapped_column(Text)
    demo_seeded: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class AppSettings(Base):
    """One row per user with their writing preferences."""

    __tablename__ = "app_settings"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True, index=True)
    demo_style_seeded: Mapped[bool] = mapped_column(Boolean, default=False)
    demo_signature_seeded: Mapped[bool] = mapped_column(Boolean, default=False)
    user_name: Mapped[str] = mapped_column(String(255), default="")
    signature: Mapped[str] = mapped_column(Text, default="")
    default_tone: Mapped[str] = mapped_column(String(32), default="professional")
    # AI-learned description of how the user writes (from their sent emails)
    style_profile: Mapped[str] = mapped_column(Text, default="")
    calendar_preferences_json: Mapped[str] = mapped_column(Text, default="")

    # ---- auto-draft: write replies ahead of time, but never send them ----
    # Distinct from auto-send below: this only fills the draft queue, so the
    # user still approves everything. On by default — it is the point of the app.
    auto_draft_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    # per-sync ceiling, so importing a backlog can't drain the daily API quota
    auto_draft_limit: Mapped[int] = mapped_column(Integer, default=5)

    # ---- auto-pilot: draft and send routine mail without asking ----
    auto_send_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    # comma-separated categories eligible for auto-send (never 'important')
    auto_send_categories: Mapped[str] = mapped_column(String(128), default="routine")
    # a safety valve so a bad classification can't blast the whole inbox
    auto_send_daily_limit: Mapped[int] = mapped_column(Integer, default=5)


class SyncJob(Base):
    """Progress record for a background inbox sync, polled by the frontend."""

    __tablename__ = "sync_jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    status: Mapped[str] = mapped_column(String(16), default="running")  # running|done|error
    stage: Mapped[str] = mapped_column(String(64), default="starting")
    total: Mapped[int] = mapped_column(Integer, default=0)
    processed: Mapped[int] = mapped_column(Integer, default=0)
    new_count: Mapped[int] = mapped_column(Integer, default=0)
    drafted_count: Mapped[int] = mapped_column(Integer, default=0)
    auto_sent_count: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str] = mapped_column(Text, default="")
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class LLMCall(Base):
    """One Claude API call: which model, how many tokens, how long, what it cost.

    The thesis evaluation chapter reports cost and latency per operation and per
    model from this table — so every call in services/llm.py records a row, even
    the ones that fail.
    """

    __tablename__ = "llm_calls"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    operation: Mapped[str] = mapped_column(String(32), index=True)  # classify|draft|summarize|style
    model: Mapped[str] = mapped_column(String(64), index=True)
    operation_id: Mapped[str] = mapped_column(String(64), default="")
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_read_tokens: Mapped[int] = mapped_column(Integer, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    success: Mapped[bool] = mapped_column(Boolean, default=True)
    error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
