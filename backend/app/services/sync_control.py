"""Renew sync ownership independently of slow network calls and fence old workers."""
import logging
from datetime import datetime, timezone
from threading import Event, Thread

from sqlalchemy import update
from ..database import SessionLocal
from ..models import SyncLease, SyncJob

log = logging.getLogger(__name__)

class SyncSuperseded(RuntimeError):
    pass

def guard(db, user_id, job_id):
    # Updating takes a DB write lock until the caller commits its side effect.
    # A replacement worker cannot take ownership in the middle of this commit.
    if not db.execute(update(SyncLease).where(
        SyncLease.user_id == user_id, SyncLease.job_id == job_id
    ).values(heartbeat_at=datetime.now(timezone.utc))).rowcount:
        db.rollback()
        raise SyncSuperseded("This sync has been replaced by a newer worker")

class Heartbeat:
    def __init__(self, user_id, job_id):
        self.user_id, self.job_id = user_id, job_id
        self.stop_event = Event()
        self.thread = Thread(target=self._run, daemon=True)

    def start(self):
        self.thread.start()

    def _run(self):
        while not self.stop_event.wait(30):
            try:
                with SessionLocal() as db:
                    guard(db, self.user_id, self.job_id)
                    db.commit()
            except SyncSuperseded:
                return
            except Exception:
                log.exception("Could not renew sync heartbeat")

    def stop(self):
        self.stop_event.set()
        if self.thread.is_alive():
            self.thread.join(timeout=2)

def finish(job_id, error=None):
    # A fresh session works even after the worker's session failed to flush.
    try:
        with SessionLocal() as db:
            job = db.get(SyncJob, job_id)
            if job and job.status == "running":
                job.status = "error" if error is not None else "done"
                job.stage = job.status
                job.error = error or ""
                job.finished_at = datetime.now(timezone.utc)
                db.commit()
    except Exception:
        log.exception("Could not persist final state for sync %s", job_id)
