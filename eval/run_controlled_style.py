"""Synthetic style adaptation ablation. Never represent references as real sent mail."""
import hashlib
import argparse
import json
import os
import random
import sys
import time
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / 'eval' / 'benchmark'
os.environ['DATABASE_URL'] = 'sqlite:///' + (OUT / 'evaluation.db').as_posix()
sys.path.insert(0, str(ROOT / 'backend'))
from app.config import settings
from app.database import Base, engine, SessionLocal
from app.services import llm
from style_eval import compare, summarise

TRAIN = [
    ('Mira', 'The agenda arrived. I will review it before our call.'),
    ('Jon', 'The access request is with support. I will share their response.'),
    ('Lea', 'Please send the updated invoice. I will check the figures.'),
    ('Sam', 'Tuesday works for a short call. Please send the invitation.'),
    ('Nina', 'I need the final brief before estimating the work.'),
    ('Tom', 'The notes are clear. I will use them for the next revision.'),
]
TEST = [
    ('Ada', 'Did you receive the design files?', 'I received the design files. I will review them.'),
    ('Ben', 'Can you confirm the price today? The scope is not finalized.', 'I need the final scope before confirming the price.'),
    ('Cora', 'Please send your delivery estimate. We have not sent the requirements yet.', 'Please send the requirements. I will review them before estimating delivery.'),
    ('Dan', 'We have prepared an updated brief. Would you like us to send it?', 'Please send the updated brief. I will review it.'),
    ('Eva', 'Do you need our invoice number to investigate this?', 'Please send the invoice number. I will use it to investigate.'),
    ('Finn', 'Can you approve these changes without seeing the revised document?', 'Please send the revised document. I need to review it before approving the changes.'),
    ('Gia', 'Do you want the meeting notes as a PDF?', 'Please send the meeting notes as a PDF.'),
    ('Hugo', 'Should I send you the error message to help troubleshoot?', 'Please send the error message. I will review it to help troubleshoot.'),
]

def reply(name, body):
    return f'Hello {name},\n\n{body}\n\nThanks,\nAlex'

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', default=settings.drafter_model)
    parser.add_argument('--report-only', action='store_true')
    args = parser.parse_args()
    settings.drafter_model = args.model
    Base.metadata.create_all(engine)
    settings.llm_max_retries = 2
    settings.llm_timeout_seconds = 45
    inputs = {'source':'Synthetic examples authored by coding assistant; not real correspondence.',
              'train':[{'id':f'train-{i}', 'reply':reply(n,b)} for i,(n,b) in enumerate(TRAIN)],
              'test':[{'id':f'test-{i}', 'sender':n, 'incoming':b, 'reference':reply(n,r)} for i,(n,b,r) in enumerate(TEST)]}
    (OUT/'style-inputs.json').write_text(json.dumps(inputs,indent=2),encoding='utf-8')
    profile_path = OUT/f'controlled-style-profile-{args.model}.txt'
    path = OUT/'style-predictions.jsonl'
    with SessionLocal() as db:
        if not profile_path.exists():
            if args.report_only:
                print('No learned profile available for this model.')
                return 1
            try:
                profile_path.write_text(llm.analyze_style([r['reply'] for r in inputs['train']], db=db), encoding='utf-8')
            except Exception as exc:
                (OUT/'STYLE_STATUS.md').write_text(f'Style profile learning failed for `{args.model}`: {type(exc).__name__}. No successful style result for this model. See telemetry for failed attempts.', encoding='utf-8')
                print('Style profile learning failed:', exc, flush=True)
                return 1
            time.sleep(5)
        profile = profile_path.read_text(encoding='utf-8')
        fingerprint = hashlib.sha256((json.dumps(inputs,sort_keys=True)+profile+settings.drafter_model+Path(llm.__file__).read_text(encoding='utf-8')).encode()).hexdigest()
        cache = {(r['id'],r['arm']):r for r in [json.loads(l) for l in path.read_text(encoding='utf-8').splitlines()] if r['fingerprint']==fingerprint} if path.exists() else {}
        for row in ([] if args.report_only else inputs['test']):
            arms = ['with','without']
            random.Random(row['id']).shuffle(arms)
            for arm in arms:
                if (row['id'],arm) in cache: continue
                try:
                    draft = llm.draft_reply(SimpleNamespace(sender=row['sender']+' <sender@example.invalid>',subject='Project follow-up',body_text=row['incoming']),
                                            'professional', SimpleNamespace(user_name='Alex', signature='Alex', style_profile=profile if arm=='with' else ''), [], db=db)
                except Exception as exc:
                    print('Style run stopped:', exc,flush=True)
                    return 1
                record = dict(id=row['id'],arm=arm,model=settings.drafter_model,fingerprint=fingerprint,draft=draft,**compare(draft,row['reference']))
                with path.open('a',encoding='utf-8') as handle: handle.write(json.dumps(record)+'\n')
                cache[(row['id'],arm)] = record
                print(row['id'],arm,flush=True)
                time.sleep(5)
    paired = [r for r in inputs['test'] if (r['id'],'with') in cache and (r['id'],'without') in cache]
    report = ['# Controlled synthetic style ablation',f'Model: `{settings.drafter_model}`. Complete pairs: {len(paired)}/{len(TEST)}.',
              'Six synthetic training replies; eight disjoint synthetic test replies. Not a personal-style accuracy measurement.',
              'One profile-on and one profile-off draft per input; arm order randomized. Same identity, signature, tone and empty knowledge.',
              '| Arm | Length ratio | Sentence length delta | Vocabulary overlap | Greeting | Sign-off | Judge |',
              '|---|---:|---:|---:|---:|---:|---:|']
    if paired:
        for arm in ('with','without'): report.append(summarise([cache[(r['id'],arm)] for r in paired],arm))
    report.append('Length ratio ideally 1; sentence-length delta ideally 0; other metrics higher means more similar. No human or LLM judge was used. These metrics do not measure factual correctness.')
    report.append('The configured gemini-3.8-flash model timed out during profile learning. This completed experiment uses the alternative lite model named above; it does not validate the configured drafting model. Sign-off matches are partly constrained by the identical supplied signature in both arms.')
    (OUT/'STYLE_REPORT.md').write_text('\n'.join(report),encoding='utf-8')
    return 0

if __name__=='__main__':
    raise SystemExit(main())
