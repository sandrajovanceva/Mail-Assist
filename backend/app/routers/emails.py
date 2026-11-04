import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..config import settings
from ..database import SessionLocal, get_db
from ..models import AppSettings, Draft, EmailAccount, EmailMessage, SyncJob, User
from ..schemas import EmailOut, EmailPatch, SyncJobOut
from ..security import get_current_user
from ..services import autodraft, autosend, llm, needs_reply
from ..services.provider import FetchedEmail, ProviderError
from ..services.registry import get_provider, real_account

log = logging.getLogger(__name__)
router = APIRouter(prefix="/emails", tags=["emails"])


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


# --------------------------------------------------------------------------
# background sync
# --------------------------------------------------------------------------


def _classify_one(user_id: int, fetched: FetchedEmail) -> dict:
    """Runs in a worker thread, so it opens its own session for usage logging."""
    db = SessionLocal()
    try:
        return llm.classify_email(
            fetched.sender, fetched.subject, fetched.body_text, db=db, user_id=user_id
        )
    finally:
        db.close()


def _run_sync(job_id: int, user_id: int) -> None:
    """The actual sync, run in a background thread with its own DB session.

    Classification is the slow part — one model call per new email — so it runs
    in a small thread pool. The pool stays small on purpose: a free-tier Gemini
    key rate-limits per minute, and hammering it just burns retries.
    """
    db = SessionLocal()
    job = db.get(SyncJob, job_id)
    try:
        account = real_account(db, user_id)
        provider = get_provider(db, account)

        # Retry enrichment failures already stored by previous syncs.
        retry_rows = db.query(EmailMessage).filter(
            EmailMessage.account_id == account.id, EmailMessage.category == ""
        ).order_by(EmailMessage.received_at.desc(), EmailMessage.id.desc()).limit(settings.sync_max_emails).all()
        for row in retry_rows:
            try:
                result = llm.classify_email(row.sender, row.subject, row.body_text, db=db, user_id=user_id)
                row.category = result["category"]
                row.needs_attention = result["needs_attention"]
                row.classification_reason = result["reason"]
                decision = needs_reply.decide(
                    sender=row.sender, category=row.category, is_bulk=row.is_bulk,
                    last_thread_sender=provider.thread_last_sender(row.thread_id),
                    account_address=account.email_address,
                )
                row.needs_reply = decision.needs_reply and not row.send_claimed
                row.needs_reply_reason = decision.reason
                db.commit()
            except Exception as exc:
                db.rollback()
                log.warning("Classification retry failed for email %s: %s", row.id, exc)

        job.stage = "fetching"
        db.commit()

        # Incremental where possible: only ask for what changed since last sync.
        incremental = provider.fetch_since(account.last_history_id)
        if incremental is not None:
            fetched, new_cursor = incremental
        else:
            new_cursor = provider.current_cursor()
            fetched = provider.fetch_recent(max_results=settings.sync_max_emails)

        known = {
            row[0]
            for row in db.query(EmailMessage.provider_message_id)
            .filter(EmailMessage.account_id == account.id)
            .all()
        }
        # de-dupe within the batch too — the history feed can report the same
        # message more than once
        seen: set[str] = set()
        fresh = [
            f
            for f in fetched
            if f.provider_message_id not in known
            and not (f.provider_message_id in seen or seen.add(f.provider_message_id))
        ]

        job.total = len(fresh)
        job.stage = "classifying" if fresh else "done"
        db.commit()

        results: list[dict | None] = [None] * len(fresh)
        if fresh:
            with ThreadPoolExecutor(max_workers=settings.sync_concurrency) as pool:
                futures = {
                    pool.submit(_classify_one, user_id, f): i for i, f in enumerate(fresh)
                }
                for future in futures:
                    index = futures[future]
                    try:
                        results[index] = future.result()
                    except Exception as exc:  # one bad email must not fail the sync
                        log.warning("Classification failed: %s", exc)
                        results[index] = {"error": str(exc)}

        app_settings = db.query(AppSettings).filter_by(user_id=user_id).first()
        stored: list[EmailMessage] = []

        for fetched_email, result in zip(fresh, results):
            email = EmailMessage(
                account_id=account.id,
                provider_message_id=fetched_email.provider_message_id,
                thread_id=fetched_email.thread_id,
                message_id_header=fetched_email.message_id_header,
                sender=fetched_email.sender,
                recipient=fetched_email.recipient,
                subject=fetched_email.subject,
                snippet=fetched_email.snippet,
                body_text=fetched_email.body_text,
                received_at=fetched_email.received_at,
                is_read=fetched_email.is_read,
            )
            if result and "error" not in result:
                email.category = result["category"]
                email.needs_attention = result["needs_attention"]
                email.classification_reason = result["reason"]
            elif result:
                email.classification_reason = f"classification failed: {result['error']}"

            # Decide whether a reply is wanted. Only consult the thread when the
            # free checks have not already ruled the message out — that keeps it
            # to at most one extra metadata call per candidate.
            cheap = needs_reply.decide(
                sender=fetched_email.sender,
                category=email.category,
                is_bulk=fetched_email.is_bulk,
                last_thread_sender=None,
                account_address=account.email_address,
            )
            if cheap.needs_reply:
                decision = needs_reply.decide(
                    sender=fetched_email.sender,
                    category=email.category,
                    is_bulk=fetched_email.is_bulk,
                    last_thread_sender=provider.thread_last_sender(fetched_email.thread_id),
                    account_address=account.email_address,
                )
            else:
                decision = cheap
            email.is_bulk = fetched_email.is_bulk
            email.needs_reply = decision.needs_reply
            email.needs_reply_reason = decision.reason
            # Commit per message against the unique index: if a concurrent sync
            # already stored this one, skip it instead of losing the whole batch.
            try:
                with db.begin_nested():
                    db.add(email)
                stored.append(email)
            except IntegrityError:
                log.info("Skipping %s — already stored by another sync", email.provider_message_id)
            job.processed += 1

        job.new_count = len(stored)
        account.last_history_id = new_cursor
        db.commit()

        # Draft replies ahead of time for mail that wants one, so the queue is
        # already populated when the user opens the app. Nothing is sent here.
        if app_settings is None or app_settings.auto_draft_enabled:
            limit = app_settings.auto_draft_limit if app_settings else 5
            # Deliberately not limited to this sync's messages: a draft that was
            # missed because a previous sync crashed, or because the feature was
            # added later, should still get written rather than lost forever.
            candidates = (
                db.query(EmailMessage)
                .outerjoin(Draft, Draft.email_id == EmailMessage.id)
                .filter(
                    EmailMessage.account_id == account.id,
                    EmailMessage.needs_reply.is_(True),
                    EmailMessage.category.in_(llm.CATEGORIES),
                    EmailMessage.send_claimed.is_(False),
                    EmailMessage.is_archived.is_(False),
                    Draft.id.is_(None),  # nothing drafted for it yet
                )
                .order_by(EmailMessage.received_at.desc(), EmailMessage.id.desc())
                .limit(limit)
                .all()
            )
            if candidates:
                job.stage = "drafting"
                db.commit()
            for email in candidates:
                try:
                    autodraft.draft_for(db, user_id, email, app_settings)
                    job.drafted_count += 1
                except Exception as exc:  # a failed draft must not fail the sync
                    log.warning("Auto-draft failed for email %s: %s", email.id, exc)
            db.commit()

        # Sending consumes existing untouched background drafts, including those
        # left from earlier syncs when drafting is now disabled.
        if app_settings and app_settings.auto_send_enabled:
            job.stage = "auto-sending"
            db.commit()
            send_candidates = (
                db.query(EmailMessage)
                .join(Draft, Draft.email_id == EmailMessage.id)
                .filter(
                    EmailMessage.account_id == account.id,
                    EmailMessage.is_archived.is_(False),
                    Draft.status == "pending",
                    Draft.auto_send_eligible.is_(True),
                )
                .order_by(Draft.created_at)
                .all()
            )
            for email in send_candidates:
                try:
                    if autosend.maybe_auto_send(db, user_id, email, app_settings, provider):
                        job.auto_sent_count += 1
                except Exception as exc:  # never let auto-send break the sync
                    log.warning("Auto-send failed for email %s: %s", email.id, exc)
            db.commit()

        job.status = "done"
        job.stage = "done"
    except Exception as exc:
        log.exception("Sync failed")
        job.status = "error"
        job.stage = "error"
        job.error = str(exc)
    finally:
        job.finished_at = datetime.now(timezone.utc)
        db.commit()
        db.close()


@router.post("/sync", response_model=SyncJobOut)
def sync_emails(
    background: BackgroundTasks,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Kick off an inbox sync and return immediately.

    Classifying a full inbox takes far longer than an HTTP request should, so
    the work happens in the background and the client polls `/emails/sync/{id}`.
    """
    try:
        real_account(db, user.id)
    except ProviderError as exc:
        raise HTTPException(400, str(exc)) from exc

    # One sync at a time — but a job whose worker died must not block the user
    # forever, so anything still "running" after a while is written off.
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=settings.sync_stale_after_minutes)
    for job in (
        db.query(SyncJob)
        .filter(SyncJob.user_id == user.id, SyncJob.status == "running")
        .all()
    ):
        if job.started_at.replace(tzinfo=timezone.utc) < cutoff:
            job.status = "error"
            job.stage = "error"
            job.error = "Sync timed out and was abandoned."
            job.finished_at = datetime.now(timezone.utc)
            db.commit()
            log.warning("Abandoned stale sync job %s", job.id)
        else:
            return job  # a live sync is already doing this work

    job = SyncJob(user_id=user.id)
    db.add(job)
    db.commit()
    db.refresh(job)

    background.add_task(_run_sync, job.id, user.id)
    return job


@router.get("/sync/{job_id}", response_model=SyncJobOut)
def sync_status(
    job_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    job = db.query(SyncJob).filter_by(id=job_id, user_id=user.id).first()
    if not job:
        raise HTTPException(404, "Sync job not found")
    return job


# --------------------------------------------------------------------------
# reading
# --------------------------------------------------------------------------


@router.get("", response_model=list[EmailOut])
def list_emails(
    category: str | None = None,
    needs_attention: bool | None = None,
    needs_reply_only: bool | None = None,
    unread: bool | None = None,
    archived: bool = False,
    q: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    query = (
        db.query(EmailMessage)
        .join(EmailAccount)
        .filter(EmailAccount.user_id == user.id, EmailMessage.is_archived.is_(archived))
        .order_by(EmailMessage.received_at.desc(), EmailMessage.id.desc())
    )
    if category:
        query = query.filter(EmailMessage.category == category)
    if needs_attention is not None:
        query = query.filter(EmailMessage.needs_attention.is_(needs_attention))
    if needs_reply_only is not None:
        query = query.filter(EmailMessage.needs_reply.is_(needs_reply_only))
    if unread is not None:
        query = query.filter(EmailMessage.is_read.is_(not unread))
    if q:
        like = f"%{q}%"
        query = query.filter(
            or_(
                EmailMessage.subject.ilike(like),
                EmailMessage.sender.ilike(like),
                EmailMessage.snippet.ilike(like),
                EmailMessage.body_text.ilike(like),
            )
        )
    return query.offset(offset).limit(limit).all()


@router.get("/{email_id}", response_model=EmailOut)
def get_email(
    email_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    return _owned_email(db, user, email_id)


@router.patch("/{email_id}", response_model=EmailOut)
def patch_email(
    email_id: int,
    body: EmailPatch,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Mark read/unread or archive — local mailbox state, not pushed to Gmail."""
    email = _owned_email(db, user, email_id)
    if body.is_read is not None:
        email.is_read = body.is_read
    if body.is_archived is not None:
        email.is_archived = body.is_archived
    db.commit()
    db.refresh(email)
    return email


@router.post("/{email_id}/summarize", response_model=EmailOut)
def summarize(
    email_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    """Summarize the whole thread this email belongs to."""
    email = _owned_email(db, user, email_id)
    try:
        provider = get_provider(db, email.account)
        thread = provider.fetch_thread(email.thread_id)
    except ProviderError as exc:
        raise HTTPException(400, str(exc)) from exc

    try:
        email.summary = llm.summarize_thread(
            email.subject, thread, db=db, user_id=user.id
        )
    except llm.QuotaExceeded as exc:
        raise HTTPException(429, str(exc)) from exc
    db.commit()
    db.refresh(email)
    return email
