"""Frozen held-out SpamAssassin benchmark; actual app calls, no Gmail delivery.

prepare: download, deduplicate and freeze 1,000 inputs, excluding the pilot.
run: resumable single-pass inference. Invalid outputs are retained as failures.
report: regenerate scores/CSV/telemetry without network calls.
"""
import argparse
import csv
import hashlib
import io
import json
import math
import os
import platform
import random
import sqlite3
import statistics
import sys
import tarfile
import time
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from email import policy
from email.parser import BytesParser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT/'eval'/'final'
OUT.mkdir(exist_ok=True)
os.environ['DATABASE_URL'] = 'sqlite:///' + (OUT/'evaluation.db').as_posix()
sys.path.insert(0,str(ROOT/'backend'))
from app.config import settings
from app.database import Base, engine, SessionLocal
from app import models
from app.services import llm
from app.services.gmail import html_to_text

SEED = 20261110
SOURCE = 'https://spamassassin.apache.org/old/publiccorpus/'
ARCHIVES = {'spam':['20030228_spam.tar.bz2','20050311_spam_2.tar.bz2'],
            'easy_ham':['20030228_easy_ham.tar.bz2','20030228_easy_ham_2.tar.bz2'],
            'hard_ham':['20030228_hard_ham.tar.bz2']}

def read_jsonl(path):
    return [json.loads(line) for line in path.read_text(encoding='utf-8').split('\n') if line.strip()] if path.exists() else []

def write_json(path,value):
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')

def digest(value):
    return hashlib.sha256(value).hexdigest()

def input_digest(row):
    # Match the input actually seen by classify_email, not archive filenames.
    return digest(json.dumps([row['sender'],row['subject'],row['body'][:4000]],ensure_ascii=False).encode())

def utc():
    return datetime.now(timezone.utc).isoformat()

def prepare():
    if (OUT/'manifest.json').exists():
        print('Frozen benchmark already exists; leaving it unchanged.')
        return
    pilot = read_jsonl(ROOT/'eval'/'benchmark'/'public_emails.jsonl')
    seen = {input_digest(r) for r in pilot}
    # Also reject identical normalized body prefixes despite different headers.
    bodies = {digest(' '.join(r['body'][:4000].split()).encode()) for r in pilot}
    chosen, sources, pool_sizes = [], [], {}
    for group,filenames in ARCHIVES.items():
        pool = []
        for filename in filenames:
            archive = ROOT/'eval'/'benchmark'/filename
            if not archive.exists():
                with urllib.request.urlopen(SOURCE+filename,timeout=60) as response:
                    archive.write_bytes(response.read())
            raw = archive.read_bytes()
            sources.append(dict(url=SOURCE+filename,sha256=digest(raw)))
            with tarfile.open(fileobj=io.BytesIO(raw),mode='r:bz2') as tf:
                for member in sorted(tf.getmembers(),key=lambda m:m.name):
                    if not member.isfile() or not member.name.split('/')[-1][:1].isdigit(): continue
                    message = BytesParser(policy=policy.default).parsebytes(tf.extractfile(member).read())
                    part = message.get_body(preferencelist=('plain','html'))
                    try:
                        body = part.get_content() if part else ''
                    except (LookupError,UnicodeError):
                        body = (part.get_payload(decode=True) or b'').decode('utf-8',errors='replace') if part else ''
                    if part and part.get_content_type()=='text/html': body = html_to_text(body)
                    row = dict(sender=str(message.get('From','')),subject=str(message.get('Subject','')),body=body,
                               spam_label=group=='spam',group=group,archive=filename,source_member=member.name)
                    key = input_digest(row)
                    body_key = digest(' '.join(body[:4000].split()).encode())
                    if not body.strip() or key in seen or body_key in bodies: continue
                    seen.add(key)
                    bodies.add(body_key)
                    row['id'] = key[:20]
                    pool.append(row)
        pool_sizes[group]=len(pool)
        count={'spam':500,'easy_ham':400,'hard_ham':100}[group]
        chosen.extend(random.Random(SEED).sample(pool,count))
    random.Random(SEED).shuffle(chosen)
    dataset = OUT/'emails.jsonl'
    dataset.write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in chosen),encoding='utf-8')
    write_json(OUT/'manifest.json',dict(created_utc=utc(),seed=SEED,sources=sources,pool_sizes=pool_sizes,
        count=len(chosen),groups=dict(Counter(r['group'] for r in chosen)),
        dataset_sha256=digest(dataset.read_bytes()),pilot_sha256=digest((ROOT/'eval'/'benchmark'/'public_emails.jsonl').read_bytes()),
        classifier_model=settings.classifier_model,thinking_level=settings.classifier_thinking_level,
        classifier_source_sha256=digest(Path(llm.__file__).read_bytes()),python=platform.python_version(),
        labels='Original corpus spam/ham labels; no AI labels used in the primary benchmark.',
        exclusions='Empty bodies, duplicate model inputs and normalized body prefixes, including all pilot examples.',
        primary_endpoint='Correct binary spam/ham predictions divided by all 1000 planned examples, including invalid outputs as incorrect. Not final until all inputs attempted.',
        secondary_endpoints=['Valid-output accuracy, precision, recall, F1','Failure rate','95% Wilson interval','Latency and measured tokens'],
        frozen_before_inference=True))
    print('Frozen:',len(chosen),'fresh emails;',pool_sizes,flush=True)

def frozen():
    manifest=json.loads((OUT/'manifest.json').read_text(encoding='utf-8'))
    assert digest((OUT/'emails.jsonl').read_bytes())==manifest['dataset_sha256'],'Dataset changed after freeze'
    assert digest(Path(llm.__file__).read_bytes())==manifest['classifier_source_sha256'],'Classifier changed after freeze'
    assert settings.classifier_model==manifest['classifier_model'],'Model changed after freeze'
    assert settings.classifier_thinking_level==manifest['thinking_level'],'Thinking configuration changed after freeze'
    return manifest,read_jsonl(OUT/'emails.jsonl')

def run(args):
    manifest,rows=frozen()
    Base.metadata.create_all(engine)
    settings.llm_max_retries=2
    settings.llm_timeout_seconds=35
    path=OUT/'predictions.jsonl'
    cache={r['id']:r for r in read_jsonl(path)}
    pending=[r for r in rows if r['id'] not in cache]
    if args.limit: pending=pending[:args.limit]
    last_start=0
    quota_stops=0
    with SessionLocal() as db:
        for row in pending:
            while True:
                delay=args.interval-(time.monotonic()-last_start)
                if delay>0: time.sleep(delay)
                last_start=time.monotonic()
                try:
                    result=llm.classify_email(row['sender'],row['subject'],row['body'],db=db)
                    record=dict(id=row['id'],status='ok',prediction=result,utc=utc(),latency_ms=round((time.monotonic()-last_start)*1000))
                    break
                except llm.QuotaExceeded:
                    quota_stops+=1
                    # Quota failures are transport failures, not classifier labels.
                    if quota_stops>=3:
                        print('Quota still unavailable after two cooldowns; saved progress can resume.',flush=True)
                        report()
                        return 2
                    print('Quota reached; cooling down for 61 seconds.',flush=True)
                    time.sleep(61)
                except Exception as exc:
                    record=dict(id=row['id'],status='error',error=str(exc),utc=utc(),latency_ms=round((time.monotonic()-last_start)*1000))
                    break
            with path.open('a',encoding='utf-8') as handle: handle.write(json.dumps(record,ensure_ascii=False)+'\n')
            cache[row['id']]=record
            quota_stops=0
            if len(cache)%10==0 or record['status']!='ok':
                print(f"{len(cache)}/{len(rows)} attempted; last={record['status']}",flush=True)
            if len(cache)%50==0: report(quiet=True)
    report()
    return 0 if len(cache)==len(rows) else 2

def wilson(correct,n):
    if not n:return [None,None]
    z=1.96;p=correct/n
    centre=(p+z*z/(2*n))/(1+z*z/n)
    radius=z*math.sqrt(p*(1-p)/n+z*z/(4*n*n))/(1+z*z/n)
    return [centre-radius,centre+radius]

def report(quiet=False):
    manifest,rows=frozen()
    cache={r['id']:r for r in read_jsonl(OUT/'predictions.jsonl')}
    valid=[r for r in rows if cache.get(r['id'],{}).get('status')=='ok']
    tp=sum(r['spam_label'] and cache[r['id']]['prediction']['category']=='spam' for r in valid)
    tn=sum(not r['spam_label'] and cache[r['id']]['prediction']['category']!='spam' for r in valid)
    fp=sum(not r['spam_label'] and cache[r['id']]['prediction']['category']=='spam' for r in valid)
    fn=sum(r['spam_label'] and cache[r['id']]['prediction']['category']!='spam' for r in valid)
    def ratio(a,b):return a/b if b else None
    precision=ratio(tp,tp+fp);recall=ratio(tp,tp+fn)
    f1=2*precision*recall/(precision+recall) if precision is not None and recall is not None and precision+recall else 0
    complete=len(cache)==len(rows)
    def cause(error):
        error=error or ''
        if '429' in error:return 'HTTP 429 quota exceeded'
        if 'empty response' in error:return 'empty model response'
        if '503' in error:return 'HTTP 503 model overloaded'
        if 'getaddrinfo' in error:return 'network/DNS error'
        return 'other'
    failures=[cache[r['id']] for r in rows if r['id'] in cache and cache[r['id']]['status']!='ok']
    failure_causes=dict(Counter(cause(f.get('error')) for f in failures))
    groups={}
    for group in ('spam','easy_ham','hard_ham'):
        members=[r for r in rows if r['group']==group]
        ok=[r for r in members if cache.get(r['id'],{}).get('status')=='ok']
        correct=sum((cache[r['id']]['prediction']['category']=='spam')==r['spam_label'] for r in ok)
        groups[group]=dict(planned=len(members),attempted=sum(r['id'] in cache for r in members),valid=len(ok),correct=correct,
                           valid_accuracy=ratio(correct,len(ok)),full_accuracy=ratio(correct,len(members)) if complete else None)
    # Per-email latency includes in-call retries; it is what the app user waits for.
    latencies=sorted(cache[r['id']]['latency_ms'] for r in valid if 'latency_ms' in cache[r['id']])
    def quantile(q):return latencies[min(len(latencies)-1,int(q*len(latencies)))] if latencies else None
    latency=dict(n=len(latencies),median=statistics.median(latencies) if latencies else None,mean=statistics.fmean(latencies) if latencies else None,
                 p90=quantile(.9),p99=quantile(.99),max=latencies[-1] if latencies else None)
    telemetry={}
    if (OUT/'evaluation.db').exists():
        # Every API attempt is logged by the app itself, including failed ones and retries.
        with sqlite3.connect(OUT/'evaluation.db') as con:
            has_table=con.execute("select 1 from sqlite_master where name='llm_calls'").fetchone()
            ok_calls=con.execute("select count(*),coalesce(sum(input_tokens),0),coalesce(sum(output_tokens),0),coalesce(sum(cost_usd),0) from llm_calls where operation='classify' and success=1").fetchone() if has_table else None
            failed_calls=con.execute("select error from llm_calls where operation='classify' and success=0").fetchall() if has_table else []
        if ok_calls:
            n_ok,tokens_in,tokens_out,cost=ok_calls
            telemetry=dict(successful_api_calls=n_ok,input_tokens=tokens_in,output_tokens=tokens_out,
                           mean_input_tokens=ratio(tokens_in,n_ok),mean_output_tokens=ratio(tokens_out,n_ok),
                           list_price_cost_usd=cost,list_price_cost_per_1000_emails_usd=ratio(cost*1000,n_ok),
                           failed_api_attempts=len(failed_calls),failed_attempt_causes=dict(Counter(cause(e) for (e,) in failed_calls)))
    metrics=dict(model=manifest['classifier_model'],planned=len(rows),attempted=len(cache),valid=len(valid),failed=len(cache)-len(valid),
                 complete=complete,tp=tp,tn=tn,fp=fp,fn=fn,primary_accuracy=ratio(tp+tn,len(rows)) if complete else None,
                 valid_accuracy=ratio(tp+tn,len(valid)),precision=precision,valid_recall=recall,f1=f1,
                 full_recall=ratio(tp,sum(r['spam_label'] for r in rows)) if complete else None,
                 accuracy_wilson95=wilson(tp+tn,len(rows)) if complete else [None,None],
                 valid_accuracy_wilson95=wilson(tp+tn,len(valid)),
                 failure_rate=ratio(len(failures),len(cache)),failure_rate_wilson95=wilson(len(failures),len(cache)),
                 failure_causes=failure_causes,groups=groups,latency_ms=latency,telemetry=telemetry,
                 majority_baseline=.5,generated_utc=utc())
    write_json(OUT/'metrics.json',metrics)
    def pct(value):return f'{value:.1%}' if value is not None else 'Not final'
    lines=['# Held-out 1,000-email spam benchmark','',f"Status: {'COMPLETE' if complete else 'INCOMPLETE'}. Attempted {len(cache)}/{len(rows)}; valid outputs {len(valid)}; failed outputs {len(cache)-len(valid)}.",'',
           f"Model: `{manifest['classifier_model']}`. Original corpus labels; no AI annotation in this benchmark.",'',
           '| Metric | Result |','|---|---:|',f"| Primary accuracy, invalid outputs counted incorrect | {pct(metrics['primary_accuracy'])} |",
           f"| Accuracy among valid outputs | {pct(metrics['valid_accuracy'])} |",f'| Spam precision | {pct(precision)} |',f'| Spam recall among valid outputs | {pct(recall)} |',f'| Spam F1 among valid outputs | {f1:.3f} |',
           f"| Spam recall, failures counted as misses | {pct(metrics['full_recall'])} |",'| Majority-class baseline | 50.0% |','',
           f"95% Wilson interval for primary accuracy: {', '.join(pct(v) for v in metrics['accuracy_wilson95'])}.",'',
           f'Binary confusion counts on valid outputs: TP={tp}, FP={fp}, FN={fn}, TN={tn}. Invalid outputs are reported separately.','',
           f"95% Wilson interval for accuracy among valid outputs: {', '.join(pct(v) for v in metrics['valid_accuracy_wilson95'])}.",'',
           '## Results by source group','','| Group | Planned | Valid outputs | Correct | Accuracy among valid | Accuracy, failures incorrect |','|---|---:|---:|---:|---:|---:|',
           *[f"| {dict(spam='Spam',easy_ham='Easy legitimate',hard_ham='Difficult legitimate')[g]} | {v['planned']} | {v['valid']} | {v['correct']} | {pct(v['valid_accuracy'])} | {pct(v['full_accuracy'])} |" for g,v in groups.items()],'',
           '## Reliability','',
           f"Invalid outputs: {len(failures)} of {len(cache)} attempted ({pct(metrics['failure_rate'])}; 95% Wilson interval {', '.join(pct(v) for v in metrics['failure_rate_wilson95'])}). "
           +'Causes: '+(', '.join(f'{k}: {v}' for k,v in sorted(failure_causes.items(),key=lambda kv:-kv[1])) or 'none')+'. '
           'Each input was attempted once; the client retried transient errors inside that attempt, and an input still failing afterwards was recorded as invalid and never rerun.','',
           *([f"API attempts that failed, including retried attempts and quota pauses: {telemetry['failed_api_attempts']} "
              +'('+', '.join(f'{k}: {v}' for k,v in sorted(telemetry['failed_attempt_causes'].items(),key=lambda kv:-kv[1]))+'). '
              'HTTP 429 quota responses paused the run and the input was retried after cooldown; they are not counted as invalid outputs.',''] if telemetry else []),
           '## Latency, tokens and estimated cost','',
           *([f"Latency per valid email, including in-call retries (n={latency['n']}): median {latency['median']/1000:.2f} s, mean {latency['mean']/1000:.2f} s, "
              f"90th percentile {latency['p90']/1000:.2f} s, 99th percentile {latency['p99']/1000:.2f} s, maximum {latency['max']/1000:.2f} s.",''] if latencies else []),
           *([f"Tokens measured by the API over {telemetry['successful_api_calls']} successful calls: {telemetry['input_tokens']:,} input and {telemetry['output_tokens']:,} output "
              f"(mean {telemetry['mean_input_tokens']:.0f} input and {telemetry['mean_output_tokens']:.0f} output per email).",'',
              f"Estimated cost at Google's paid list prices for `{manifest['classifier_model']}`: ${telemetry['list_price_cost_usd']:.4f} in total, about "
              f"${telemetry['list_price_cost_per_1000_emails_usd']:.2f} per 1,000 emails. This is a hypothetical scaling estimate; the evaluation ran on a free-tier key and actually cost $0.",''] if telemetry.get('successful_api_calls') else []),
           'Sample: 500 spam, 400 easy legitimate and 100 difficult legitimate messages. Exact model-input and normalized body-prefix duplicates and pilot messages were excluded before sampling.','',
           'Primary results are final only after every input was attempted. Errors are retained, not rerun until they become correct. API quota interruptions can resume without repeating completed inputs.','',
           'Limitations: historical predominantly English public emails, mailing-list-heavy legitimate messages, deliberately balanced prevalence, possible model pretraining exposure, and one stochastic prediction per example. Confidence intervals assume independent examples; related threads may weaken this assumption. No personal-inbox accuracy claim is supported.','',
           'Source: https://spamassassin.apache.org/old/publiccorpus/readme.html']
    (OUT/'SPAM_REPORT.md').write_text('\n'.join(lines),encoding='utf-8')
    with (OUT/'spam-details.csv').open('w',encoding='utf-8',newline='') as handle:
        writer=csv.writer(handle);writer.writerow(['id','group','reference_spam','status','predicted_category','predicted_attention','reason'])
        for row in rows:
            record=cache.get(row['id'],{});pred=record.get('prediction',{})
            writer.writerow([row['id'],row['group'],row['spam_label'],record.get('status','pending'),pred.get('category',''),pred.get('needs_attention',''),pred.get('reason',record.get('error',''))])
    if not quiet:print('\n'.join(lines),flush=True)

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['prepare','run','report'])
    parser.add_argument('--interval',type=float,default=4.1)
    parser.add_argument('--limit',type=int,default=0)
    args=parser.parse_args()
    if args.action=='prepare':prepare()
    elif args.action=='run':return run(args)
    else:report()
    return 0

if __name__=='__main__':raise SystemExit(main())
