"""Run real MailAssist predictions against a frozen public benchmark, resumably."""
import hashlib
import json
import os
import sys
import time
import random
import math
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / 'eval' / 'benchmark'
os.environ['DATABASE_URL'] = 'sqlite:///' + (OUT / 'evaluation.db').as_posix()
sys.path.insert(0, str(ROOT / 'backend'))
from app.config import settings
from app import models
from app.database import Base, engine, SessionLocal
from app.services import llm
from classification_eval import evaluate, prf

def main():
    Base.metadata.create_all(engine)
    settings.llm_max_retries = 2
    settings.llm_timeout_seconds = 35
    dataset = [json.loads(line) for line in (OUT / 'public_emails.jsonl').read_text(encoding='utf-8').splitlines()]
    random.Random(20261109).shuffle(dataset)
    annotations = json.loads((OUT / 'annotations.json').read_text(encoding='utf-8'))
    fingerprint = hashlib.sha256((json.dumps(dataset, sort_keys=True) + settings.classifier_model + Path(llm.__file__).read_text(encoding='utf-8')).encode()).hexdigest()
    path = OUT / 'predictions.jsonl'
    cache = {r['id']: r for r in [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()] if r['fingerprint']==fingerprint} if path.exists() else {}
    with SessionLocal() as db:
        for index, row in enumerate([] if '--report-only' in sys.argv else dataset, 1):
            if row['id'] in cache:
                continue
            try:
                started = time.monotonic()
                prediction = llm.classify_email(row['sender'], row['subject'], row['body'], db=db)
            except Exception as exc:
                failure = dict(id=row['id'], fingerprint=fingerprint, error=str(exc), timestamp=datetime.now(timezone.utc).isoformat())
                with (OUT/'failures.jsonl').open('a',encoding='utf-8') as handle:
                    handle.write(json.dumps(failure)+'\n')
                print('Failed:', row['id'], str(exc), flush=True)
                if isinstance(exc,llm.QuotaExceeded):
                    break
                time.sleep(5)
                continue
            result = {'id':row['id'], 'fingerprint':fingerprint, 'model':settings.classifier_model,
                      'timestamp':datetime.now(timezone.utc).isoformat(), 'latency_ms':round((time.monotonic()-started)*1000), **prediction}
            with path.open('a', encoding='utf-8') as handle:
                handle.write(json.dumps(result, ensure_ascii=False)+'\n')
            cache[row['id']] = result
            print(f"{index}/{len(dataset)} {row['id']}: {prediction['category']}", flush=True)
            time.sleep(5)
    scored = [dict(id=r['id'], subject=r['subject'], expected_category=annotations[r['id']]['category'],
                   expected_attention=annotations[r['id']]['needs_attention'], predicted_category=cache[r['id']]['category'],
                   predicted_attention=cache[r['id']]['needs_attention'],reason=cache[r['id']]['reason']) for r in dataset if r['id'] in cache]
    if not scored:
        return 1
    tp=fp=fn=tn=0
    for row in dataset:
        if row['id'] not in cache: continue
        actual, predicted = row['spam_label'], cache[row['id']]['category']=='spam'
        tp += actual and predicted
        fp += not actual and predicted
        fn += actual and not predicted
        tn += not actual and not predicted
    p,r,f=prf(tp,fp,fn)
    n = len(scored)
    accuracy = (tp+tn)/n
    z = 1.96
    centre = (accuracy+z*z/(2*n))/(1+z*z/n)
    radius = z*math.sqrt(accuracy*(1-accuracy)/n+z*z/(4*n*n))/(1+z*z/n)
    majority = max(sum(row['spam_label'] for row in dataset if row['id'] in cache),sum(not row['spam_label'] for row in dataset if row['id'] in cache))/n
    status = 'COMPLETE' if len(scored)==len(dataset) else 'INCOMPLETE: do not present these scores as full-dataset results'
    failures_path = OUT/'failures.jsonl'
    failed_ids = {r['id'] for r in [json.loads(line) for line in failures_path.read_text(encoding='utf-8').splitlines()] if r['fingerprint']==fingerprint} if failures_path.exists() else set()
    attempted_ids = set(cache) | failed_ids
    attempted_ids &= {r['id'] for r in dataset}
    if len(attempted_ids)==len(dataset) and len(scored)<len(dataset):
        status = 'ALL MESSAGES ATTEMPTED; some have no valid prediction. Conditional metrics below exclude failures'
    report = f'''# Public email benchmark

Model: `{settings.classifier_model}`. Coverage: {len(scored)}/{len(dataset)}.
Status: {status}.
Attempted inputs: {len(attempted_ids)}/{len(dataset)}. Inputs without a valid prediction: {len(dataset)-len(scored)}.
Dataset seed: 20261109. Classifier input: From, Subject, first 4,000 body characters.

## Original corpus labels: spam detection

Accuracy: {(tp+tn)/len(scored):.3f}. Precision: {p:.3f}. Recall: {r:.3f}. F1: {f:.3f}.
TP={tp}, FP={fp}, FN={fn}, TN={tn}. Positive class is spam.
Full-dataset spam accuracy counting missing/invalid predictions as incorrect: {(tp+tn)/len(dataset):.3f}.
Full-dataset spam recall counting invalid spam predictions as misses: {tp/sum(row['spam_label'] for row in dataset):.3f}.
Majority-class baseline on completed examples: {majority:.3f}.
Descriptive 95% Wilson interval for accuracy: [{centre-radius:.3f}, {centre+radius:.3f}].
This interval assumes independent examples and does not capture corpus shift or model pretraining overlap.
The original corpus supplies these binary labels. A balanced sample is not a natural inbox prevalence estimate.

## Agreement with AI-assisted four-category annotations

The following labels were assigned by the coding assistant before running predictions.
They are not human-verified ground truth. These metrics measure annotation agreement,
not verified personal-inbox accuracy. Original corpus spam labels were preserved.
Per-category support matters: zero-support classes and attention flags cannot be assessed from an incomplete sample.
Full-dataset four-category agreement counting missing/invalid predictions as incorrect: {sum(row['expected_category']==row['predicted_category'] for row in scored)/len(dataset):.3f}.

''' + evaluate(scored)
    (OUT / 'REPORT.md').write_text(report, encoding='utf-8')
    print(report, flush=True)
    return 0 if len(scored)==len(dataset) else 1

if __name__ == '__main__':
    raise SystemExit(main())
