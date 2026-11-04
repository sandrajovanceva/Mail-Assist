"""Gemini-powered intelligence: classification, reply drafting, thread
summaries, and writing-style learning.

Model choices live in config so the thesis evaluation chapter can swap and
compare models by changing env vars only.

Two things shape this module beyond the prompts themselves:

* **Free-tier quotas.** A free Gemini key is rate limited (HTTP 429
  RESOURCE_EXHAUSTED) per minute *and* per day. Every call goes through
  `_generate`, which retries with exponential backoff and honours the server's
  own `retryDelay` hint before giving up with a message the UI can show.
* **Measurement.** Every call — successful or not — records an `LLMCall` row
  with tokens, latency and list-price cost, which is what the evaluation
  chapter reports on.
"""

import json
import logging
import re
import threading
import time
from typing import Literal
from uuid import uuid4

from google import genai
from google.genai import errors as genai_errors
from google.genai import types
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..config import settings
from ..models import AppSettings, DraftEdit, EmailMessage, KnowledgeItem
from . import usage
from .provider import ThreadMessage

log = logging.getLogger(__name__)

CATEGORIES = ["important", "routine", "newsletter", "spam"]

_client: genai.Client | None = None
# Sync classifies emails from a thread pool. Without this lock two threads can
# both find _client unset and both build one; the loser's client is then garbage
# collected, closing its HTTP connection underneath a thread that is still using
# it ("Cannot send a request, as the client has been closed").
_client_lock = threading.Lock()


def get_client() -> genai.Client:
    """Built on first use, not at import — the app (and the tests) must be able
    to start without a Gemini key, and only fail when something actually needs it."""
    global _client
    if _client is None:
        with _client_lock:
            if _client is None:  # re-check: another thread may have won the race
                if not settings.gemini_api_key:
                    raise RuntimeError(
                        "GEMINI_API_KEY is not set. Get a free key at "
                        "https://aistudio.google.com/apikey and put it in backend/.env"
                    )
                _client = genai.Client(api_key=settings.gemini_api_key)
    return _client


class QuotaExceeded(RuntimeError):
    """Raised when Gemini keeps returning 429 after our retries are spent.

    Routers turn this into a 429 so the UI can say "daily free-tier quota is
    gone" instead of a generic failure.
    """


class Classification(BaseModel):
    """Structured-output schema for `classify_email`."""

    category: Literal["important", "routine", "newsletter", "spam"] = Field(description="One of: important, routine, newsletter, spam.")
    needs_attention: bool = Field(
        description=(
            "True if the email needs the user's personal attention and an AI draft "
            "alone is not enough."
        )
    )
    reason: str = Field(description="One short sentence explaining the classification.")


def _retry_delay_hint(exc: Exception) -> float | None:
    """Gemini returns e.g. {"retryDelay": "21s"} on 429 — prefer it over guessing."""
    match = re.search(r'"retryDelay"\s*:\s*"(\d+(?:\.\d+)?)s"', str(exc))
    return float(match.group(1)) if match else None


def _is_retryable(exc: Exception) -> bool:
    if isinstance(exc, genai_errors.ServerError):
        return True
    return isinstance(exc, genai_errors.ClientError) and getattr(exc, "code", None) == 429


def _generate(
    *,
    operation: str,
    model: str,
    system: str,
    prompt: str,
    max_output_tokens: int,
    thinking_level: str = "",
    response_schema: type[BaseModel] | None = None,
    db: Session | None = None,
    user_id: int | None = None,
    timeout_seconds: int | None = None,
    max_attempts: int | None = None,
) -> str:
    """One Gemini call: retried on rate limits, always recorded. Returns text."""
    config = types.GenerateContentConfig(
        system_instruction=system,
        max_output_tokens=max_output_tokens,
        # Without an explicit timeout a stalled request hangs forever. Inside a
        # background sync that is worse than an error: the job stays "running",
        # and the one-sync-at-a-time guard then blocks every future sync.
        http_options=types.HttpOptions(timeout=(timeout_seconds or settings.llm_timeout_seconds) * 1000),
        # we never pass tools, and leaving this on makes the SDK log a warning
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
    )
    if response_schema is not None:
        config.response_mime_type = "application/json"
        config.response_schema = response_schema
    if thinking_level:
        config.thinking_config = types.ThinkingConfig(thinking_level=thinking_level)

    last_exc: Exception | None = None
    operation_id = uuid4().hex

    attempts = max_attempts or settings.llm_max_retries
    for attempt in range(attempts):
        attempt_started = time.monotonic()
        try:
            response = get_client().models.generate_content(
                model=model, contents=prompt, config=config
            )
        except Exception as exc:  # noqa: BLE001 - re-raised below once retries run out
            last_exc = exc
            usage.record_call(db, user_id=user_id, operation=operation, model=model,
                              operation_id=operation_id, attempt=attempt + 1,
                              latency_ms=int((time.monotonic() - attempt_started) * 1000),
                              success=False, error=str(exc))
            if not _is_retryable(exc) or attempt == attempts - 1:
                break
            delay = _retry_delay_hint(exc) or settings.llm_retry_base_delay * (2**attempt)
            log.warning(
                "%s: %s on attempt %d/%d, retrying in %.1fs",
                operation,
                type(exc).__name__,
                attempt + 1,
                settings.llm_max_retries,
                delay,
            )
            time.sleep(delay)
            continue

        latency_ms = int((time.monotonic() - attempt_started) * 1000)
        meta = response.usage_metadata
        # Thinking tokens are billed as output tokens, so fold them in.
        output_tokens = (getattr(meta, "candidates_token_count", 0) or 0) + (getattr(meta, "thoughts_token_count", 0) or 0)
        text = (response.text or "").strip()
        validation_error = ""
        try:
            if not text:
                raise ValueError("Gemini returned an empty response")
            if response_schema is not None:
                response_schema.model_validate_json(text)
        except ValueError as exc:
            validation_error = str(exc)
        usage.record_call(
            db,
            user_id=user_id,
            operation=operation,
            model=model,
            input_tokens=getattr(meta, "prompt_token_count", 0) or 0,
            operation_id=operation_id, attempt=attempt + 1,
            output_tokens=output_tokens,
            cache_read_tokens=getattr(meta, "cached_content_token_count", 0) or 0,
            latency_ms=latency_ms,
            success=not validation_error, error=validation_error,
        )
        if validation_error:
            raise RuntimeError(f"{operation}: {validation_error}")
        return text

    if _is_retryable(last_exc) and isinstance(last_exc, genai_errors.ClientError):
        raise QuotaExceeded(
            "Gemini rate limit reached. Free-tier keys have a per-minute and a "
            "per-day quota — wait a moment, or switch to a lite model in .env."
        ) from last_exc
    raise RuntimeError(f"{operation} failed: {last_exc}") from last_exc


# --------------------------------------------------------------------------
# operations
# --------------------------------------------------------------------------


def stream_assistant(system: str, prompt: str, schema: type[BaseModel], user_id: int):
    """Forward actual provider chunks; record one call, including interrupted streams."""
    from ..database import SessionLocal
    model = settings.assistant_model or settings.classifier_model
    started = time.monotonic()
    meta = None
    raw = ""
    success = False
    error = "Stream interrupted"
    stream = None
    try:
        stream = get_client().models.generate_content_stream(
            model=model, contents=prompt,
            config=types.GenerateContentConfig(system_instruction=system, max_output_tokens=3000,
                response_mime_type="application/json", response_schema=schema,
                http_options=types.HttpOptions(timeout=25000),
                automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True)),
        )
        for chunk in stream:
            if chunk.usage_metadata:
                meta = chunk.usage_metadata
            if chunk.text:
                raw += chunk.text
                yield chunk.text
        schema.model_validate_json(raw)
        success = True
        error = ""
    except Exception as exc:
        error = str(exc)
        raise
    finally:
        if stream is not None and hasattr(stream, "close"):
            stream.close()
        with SessionLocal() as db:
            usage.record_call(db, user_id=user_id, operation="assistant", model=model,
                operation_id=uuid4().hex, attempt=1, success=success, error=error,
                latency_ms=int((time.monotonic() - started) * 1000),
                input_tokens=getattr(meta, "prompt_token_count", 0) or 0,
                output_tokens=(getattr(meta, "candidates_token_count", 0) or 0) + (getattr(meta, "thoughts_token_count", 0) or 0),
                cache_read_tokens=getattr(meta, "cached_content_token_count", 0) or 0)


def classify_email(
    sender: str,
    subject: str,
    body: str,
    db: Session | None = None,
    user_id: int | None = None,
) -> dict:
    text = _generate(
        operation="classify",
        model=settings.classifier_model,
        system=(
            "You classify incoming emails for a personal email assistant. "
            "Categories: important (needs a real reply or action), routine (simple reply suffices), "
            "newsletter (bulk/marketing/updates), spam (unsolicited junk or phishing)."
        ),
        prompt=f"From: {sender}\nSubject: {subject}\n\n{body[:4000]}",
        max_output_tokens=512,
        thinking_level=settings.classifier_thinking_level,
        response_schema=Classification,
        db=db,
        user_id=user_id,
    )
    result = json.loads(text)
    if result.get("category") not in CATEGORIES:
        raise ValueError(f"Model returned unknown category {result.get('category')!r}")
    return result


def _knowledge_block(items: list[KnowledgeItem]) -> str:
    if not items:
        return ""
    parts = []
    for item in items:
        parts.append(f"[{item.kind}] {item.title}:\n{item.content}")
    return "\n\nUser-provided knowledge base (templates, tone rules, facts):\n" + "\n---\n".join(parts)


def analyze_style(
    sent_bodies: list[str], db: Session | None = None, user_id: int | None = None
) -> str:
    """Turn a sample of the user's sent emails into a reusable style guide."""
    sample = "\n\n=== NEXT EMAIL ===\n\n".join(sent_bodies[:30])
    return _generate(
        operation="style",
        model=settings.drafter_model,
        system=(
            "You analyze how a person writes email and produce a concise style guide another "
            "AI will follow to imitate them. Cover: typical greetings and sign-offs, formality, "
            "sentence length and structure, characteristic words or phrases, warmth/directness, "
            "emoji and punctuation habits, and language(s) used. Output only the style guide as "
            "a short bullet list - no preamble."
        ),
        prompt=f"Emails written by the user:\n\n{sample[:60000]}",
        max_output_tokens=1024,
        db=db,
        user_id=user_id,
    )


def refine_style_profile(
    current_profile: str,
    edits: list[DraftEdit],
    db: Session | None = None,
    user_id: int | None = None,
) -> str:
    """Fold AI drafts and the user's saved edits into the style guide.

    This is the visible half of the 'glass box' learning loop: the user picks
    when this runs and reviews the result, rather than a model silently
    drifting in the background.
    """
    examples = "\n\n".join(
        f"=== EXAMPLE {i} (subject: {edit.subject or 'n/a'}) ===\n"
        f"--- AI wrote ---\n{edit.original[:2000]}\n"
        f"--- User rewrote it as ---\n{edit.edited[:2000]}"
        for i, edit in enumerate(edits, start=1)
    )
    return _generate(
        operation="style",
        model=settings.drafter_model,
        system=(
            "You maintain a style guide that another AI follows to write email as a specific "
            "person. You are given the current guide and examples of replies that AI drafted "
            "alongside their saved edits, which may not have been sent. Do not infer that edited facts are verified. Infer what the edits reveal about "
            "their preferences - length, greetings, sign-offs, directness, formality, vocabulary, "
            "what they consistently delete. Output the complete revised style guide as a short "
            "bullet list, no preamble. Keep rules from the current guide that the examples do not "
            "contradict, and do not invent rules a single example cannot support. "
            "Distinguish reusable writing preferences from corrections specific to an email. "
            "Removing unverified facts, estimates, or commitments is a factual correction, "
            "not evidence that the user dislikes explanations or always wants short replies. "
            "Treat a pattern seen in only one example as tentative; do not turn it into an "
            "absolute preference. Never infer fixed sentence counts, word limits, or bans on "
            "detail from the length of an edited example. Prefer clear, direct writing without "
            "unnecessary repetition, while matching length and detail to the sender's request. "
            "Keep simple confirmations brief; provide full explanations when requested and "
            "answer questions for which information is available. Do not invent missing facts. "
            "Replace any existing inferred fixed-length rule with this context-dependent "
            "guidance rather than preserving an unsupported restriction."
        ),
        prompt=(
            f"Current style guide:\n{current_profile or '(empty)'}\n\n"
            f"Edits the user made to AI drafts:\n\n{examples[:40000]}"
        ),
        max_output_tokens=1024,
        db=db,
        user_id=user_id,
    )


def draft_reply(
    email: EmailMessage,
    tone: str,
    app_settings: AppSettings | None,
    knowledge: list[KnowledgeItem],
    extra_instructions: str = "",
    db: Session | None = None,
    user_id: int | None = None,
) -> str:
    user_name = app_settings.user_name if app_settings else ""
    signature = app_settings.signature if app_settings else ""
    style_profile = app_settings.style_profile if app_settings else ""

    system = (
        "You draft email replies on behalf of the user. Write only the body of the reply - "
        "no subject line, no commentary, no placeholders like [Your Name] (use the user's real "
        "name if known, otherwise end naturally without a name). Match the requested tone and "
        "match the reply's length and detail to the request.\n\n"
        # The reply is sent as text/plain, so Markdown arrives as literal
        # punctuation. Models reach for it unprompted when a message asks
        # several questions at once — exactly when structure matters most.
        "Write plain text only, as it will appear in an email client. Never use Markdown: "
        "no ** or _ for emphasis, no # headings, no - or * bullet characters, no backticks, "
        "no tables. If the reply needs several points, write them as ordinary sentences or "
        "as short lines each beginning with a number and a full stop. Answer every question "
        "the sender asked.\n\n"
        # The user writes without them and wants their replies to match.
        "Never use em dashes or en dashes (the — and – characters). Use a comma, a "
        "colon, brackets, or start a new sentence instead. A plain hyphen inside a compound "
        "word such as 'follow-up' is fine."
        + (f"\n\nThe user's name is: {user_name}." if user_name else "")
        + (f"\n\nEnd the email with this signature:\n{signature}" if signature else "")
        + (
            f"\n\nImitate the user's personal writing style:\n{style_profile}"
            if style_profile
            else ""
        )
        + _knowledge_block(knowledge)
        + (
            "\n\nCompleteness takes priority over inferred style-guide brevity rules. "
            "Keep simple confirmations brief, but give complete explanations when requested "
            "and answer every relevant question for which confirmed information is available. "
            "Do not impose fixed sentence or word limits from the learned style guide. "
            "Avoid repetition, not necessary detail. If information is missing, say what "
            "needs confirmation instead of inventing facts, availability, or commitments. "
            "Follow an explicit length request in Additional instructions from the user."
        )
        + (
            "\n\nReply language: Match the language of the sender's latest message, "
            "ignoring quoted earlier messages, signatures, and automatic footers when "
            "identifying that language. Do not choose a language from the sender's name "
            "or address. For Macedonian, write natural Macedonian using Cyrillic. For "
            "other languages, use their usual writing system. For mixed-language messages, "
            "use the dominant language of the actual request. If the language cannot be "
            "determined, use the user's learned preferred language if available, otherwise "
            "English. A language explicitly requested in Additional instructions from the "
            "user overrides this automatic choice. Otherwise, a style guide or template "
            "written in another language must not override the incoming message's language; "
            "adapt its style and relevant content to the reply language. Preserve the "
            "user's configured signature verbatim and do not translate personal names."
        )
    )

    prompt = (
        f"Draft a reply in a {tone} tone to this email:\n\n"
        f"From: {email.sender}\nSubject: {email.subject}\n\n{email.body_text[:8000]}"
    )
    if extra_instructions:
        prompt += f"\n\nAdditional instructions from the user: {extra_instructions}"

    return _generate(
        operation="draft",
        model=settings.drafter_model,
        system=system,
        prompt=prompt,
        max_output_tokens=4096,
        thinking_level=settings.drafter_thinking_level,
        db=db,
        user_id=user_id,
    )


def summarize_thread(
    subject: str,
    messages: list[ThreadMessage],
    db: Session | None = None,
    user_id: int | None = None,
) -> str:
    convo = "\n\n".join(
        f"--- {m.sender} ({m.date}) ---\n{m.body_text[:3000]}" for m in messages
    )
    return _generate(
        operation="summarize",
        model=settings.drafter_model,
        system=(
            "Summarize email threads for a busy user. Give a short paragraph covering what the "
            "thread is about, what has been decided, and what (if anything) is still waiting on the user."
        ),
        prompt=f"Subject: {subject}\n\n{convo[:30000]}",
        max_output_tokens=1024,
        db=db,
        user_id=user_id,
    )
