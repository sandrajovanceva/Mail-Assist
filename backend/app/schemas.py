from datetime import datetime

from pydantic import BaseModel, ConfigDict


class RegisterIn(BaseModel):
    email: str
    password: str
    name: str = ""


class LoginIn(BaseModel):
    email: str
    password: str


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    name: str


class AuthResponse(BaseModel):
    token: str
    user: UserOut


class AccountOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    provider: str
    email_address: str


class EmailOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    sender: str
    subject: str
    snippet: str
    body_text: str
    received_at: datetime
    category: str
    needs_attention: bool
    classification_reason: str
    summary: str
    thread_id: str
    is_read: bool
    is_archived: bool
    is_bulk: bool
    needs_reply: bool
    needs_reply_reason: str


class EmailPatch(BaseModel):
    is_read: bool | None = None
    is_archived: bool | None = None


class SyncJobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    status: str
    stage: str
    total: int
    processed: int
    new_count: int
    drafted_count: int
    auto_sent_count: int
    error: str
    finished_at: datetime | None


class DraftPreference(BaseModel):
    kind: str
    title: str
    content: str


class DraftOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email_id: int
    content: str
    original_content: str
    preferences_used: list[DraftPreference] | None = None
    tone: str
    status: str
    auto_sent: bool
    auto_send_eligible: bool
    created_at: datetime
    sent_at: datetime | None


class DraftCreate(BaseModel):
    tone: str | None = None
    instructions: str = ""


class DraftUpdate(BaseModel):
    content: str | None = None
    status: str | None = None


class DraftEditOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    draft_id: int
    subject: str
    original: str
    edited: str
    applied: bool
    created_at: datetime


class BulkSendOut(BaseModel):
    sent: int
    failed: int
    errors: list[str]


class ApplyEditsOut(BaseModel):
    applied: int
    style_profile: str


class KnowledgeItemIn(BaseModel):
    kind: str = "template"
    title: str
    content: str


class KnowledgeItemOut(KnowledgeItemIn):
    model_config = ConfigDict(from_attributes=True)

    id: int


class SettingsIn(BaseModel):
    user_name: str = ""
    signature: str = ""
    default_tone: str = "professional"
    style_profile: str = ""
    auto_draft_enabled: bool = True
    auto_draft_limit: int = 5
    auto_send_enabled: bool = False
    auto_send_categories: str = "routine"
    auto_send_daily_limit: int = 5


class SettingsOut(SettingsIn):
    model_config = ConfigDict(from_attributes=True)


class UsageRow(BaseModel):
    """One aggregated slice of LLM spend, for the evaluation chapter."""

    operation: str
    model: str
    calls: int
    failures: int
    unpriced_calls: int = 0
    input_tokens: int
    output_tokens: int
    cost_usd: float | None
    avg_latency_ms: int
    p95_latency_ms: int


class UsageSummary(BaseModel):
    rows: list[UsageRow]
    total_calls: int
    unpriced_calls: int = 0
    total_cost_usd: float | None
    total_input_tokens: int
    total_output_tokens: int
