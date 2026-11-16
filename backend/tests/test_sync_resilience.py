from datetime import datetime, timedelta, timezone

import pytest

from app.database import SessionLocal
from app.models import SyncJob, SyncLease, User, Draft, EmailMessage
from app.security import decode_token
from app.services import autodraft, sync_control


def test_simultaneous_starts_share_one_job(client, auth, db, account, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    monkeypatch.setattr('app.routers.emails.BackgroundTasks.add_task', lambda *args: None)
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: client.post('/emails/sync', headers=auth), range(2)))
    assert all(response.status_code == 200 for response in responses)
    assert responses[0].json()['id'] == responses[1].json()['id']
    assert db.query(SyncJob).count() == 1

def test_slow_job_with_live_heartbeat_is_not_replaced(client, auth, db, account, user_token, monkeypatch):
    monkeypatch.setattr('app.routers.emails.BackgroundTasks.add_task', lambda *args: None)
    job = SyncJob(user_id=decode_token(user_token), started_at=datetime.now(timezone.utc)-timedelta(hours=1))
    db.add(job); db.flush()
    db.add(SyncLease(user_id=job.user_id, job_id=job.id, heartbeat_at=datetime.now(timezone.utc)))
    db.commit()
    assert client.post('/emails/sync', headers=auth).json()['id'] == job.id
    db.refresh(job)
    assert job.status == 'running'
    assert db.query(SyncJob).count() == 1

def test_dead_lease_is_replaced(client, auth, db, account, user_token, monkeypatch):
    monkeypatch.setattr('app.routers.emails.BackgroundTasks.add_task', lambda *args: None)
    old = SyncJob(user_id=decode_token(user_token))
    db.add(old); db.flush()
    db.add(SyncLease(user_id=old.user_id, job_id=old.id, heartbeat_at=datetime.now(timezone.utc)-timedelta(hours=1)))
    db.commit()
    new = client.post('/emails/sync', headers=auth).json()
    db.refresh(old)
    assert new['id'] != old.id
    assert old.status == 'error'
    assert db.get(SyncLease, old.user_id).job_id == new['id']

def test_heartbeat_renews_during_blocking_work(db, user_token):
    user_id = decode_token(user_token)
    job = SyncJob(user_id=user_id)
    db.add(job); db.flush()
    lease = SyncLease(user_id=user_id, job_id=job.id, heartbeat_at=datetime.now(timezone.utc)-timedelta(hours=1))
    db.add(lease); db.commit()
    heartbeat = sync_control.Heartbeat(user_id, job.id)
    class OneTick:
        calls = 0
        def wait(self, seconds):
            self.calls += 1
            return self.calls > 1
    heartbeat.stop_event = OneTick()
    heartbeat._run()
    db.refresh(lease)
    assert lease.heartbeat_at > datetime.now(timezone.utc).replace(tzinfo=None)-timedelta(seconds=5)

def test_replaced_worker_cannot_store_a_late_draft(db, email, user_token, monkeypatch):
    user_id = decode_token(user_token)
    old, new = SyncJob(user_id=user_id), SyncJob(user_id=user_id)
    db.add_all([old,new]); db.flush()
    old_id,new_id = old.id,new.id
    db.add(SyncLease(user_id=user_id,job_id=old_id));db.commit()
    def finish_model(*args, **kwargs):
        with SessionLocal() as other:
            other.query(SyncLease).filter_by(user_id=user_id).update({'job_id':new_id})
            other.commit()
        return 'Late reply'
    monkeypatch.setattr(autodraft.llm,'draft_reply',finish_model)
    with pytest.raises(sync_control.SyncSuperseded):
        autodraft.draft_for(db,user_id,email,None,before_store=lambda:sync_control.guard(db,user_id,old_id))
    assert db.query(Draft).count() == 0


def test_background_draft_does_not_duplicate_a_draft_created_during_generation(db, email, user_token, monkeypatch):
    email_id = email.id
    def finish_model(*args, **kwargs):
        with SessionLocal() as other:
            other.add(Draft(email_id=email_id, content='Saved while waiting', original_content='Saved while waiting'))
            other.commit()
        return 'Late model response'
    monkeypatch.setattr(autodraft.llm,'draft_reply',finish_model)
    assert autodraft.draft_for(db,decode_token(user_token),email,None) is None
    assert db.query(Draft).one().content == 'Saved while waiting'

def test_database_flush_error_finishes_job_using_clean_session(client, auth, db, account, monkeypatch):
    def failed_provider(worker_db, account):
        existing = worker_db.query(User).first()
        worker_db.add(User(email=existing.email, password_hash='duplicate'))
        worker_db.flush()  # puts the worker session in rollback-required state
    monkeypatch.setattr('app.routers.emails.get_provider',failed_provider)
    started = client.post('/emails/sync', headers=auth)
    assert started.status_code == 200
    status = client.get(f"/emails/sync/{started.json()['id']}",headers=auth).json()
    assert status['status'] == 'error'
    assert status['finished_at'] is not None
    assert status['error']

def test_counts_aggregate_all_pages_and_exclude_archived_and_other_users(client, auth, db, account, email):
    from tests.conftest import register
    for i in range(250):
        db.add(EmailMessage(account_id=account.id, provider_message_id=f'count-{i}',thread_id=f'c-{i}',sender='a@example.com',
            category='newsletter' if i%2==0 else 'important',is_archived=i<10,needs_attention=10<=i<13,needs_reply=10<=i<15))
    db.commit()
    counts = client.get('/emails/counts',headers=auth).json()
    assert counts == {'all':241,'attention':3,'reply':5,'routine':1,'important':120,'newsletter':120,'spam':0}
    other = register(client,email='counts-other@example.com')
    assert client.get('/emails/counts',headers={'Authorization':f"Bearer {other['token']}"}).json()['all'] == 0
