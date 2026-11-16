"""Prepare disjoint candidate emails for coding-assistant category annotation.

Rules are only a sampling aid, not verified category labels. Candidate selections
must be reviewed before the category benchmark is frozen and run.
"""
import io
import json
import random
import re
import tarfile
import sys
from email import policy
from email.parser import BytesParser
from pathlib import Path
from final_evaluation import ROOT, OUT, ARCHIVES, input_digest, digest, read_jsonl
from app.services.gmail import html_to_text

def main():
    if (OUT/'category-candidates.json').exists() and '--refresh' not in sys.argv:
        print('Candidates already prepared.');return
    excluded=read_jsonl(OUT/'emails.jsonl')+read_jsonl(ROOT/'eval'/'benchmark'/'public_emails.jsonl')
    seen={input_digest(r) for r in excluded}
    bodies={digest(' '.join(r['body'][:4000].split()).encode()) for r in excluded}
    pools={k:[] for k in ('spam','important','routine','newsletter')}
    for group,filenames in ARCHIVES.items():
        for filename in filenames:
            with tarfile.open(ROOT/'eval'/'benchmark'/filename,mode='r:bz2') as tf:
                for member in sorted(tf.getmembers(),key=lambda m:m.name):
                    if not member.isfile() or not member.name.split('/')[-1][:1].isdigit():continue
                    msg=BytesParser(policy=policy.default).parsebytes(tf.extractfile(member).read())
                    part=msg.get_body(preferencelist=('plain','html'))
                    try:body=part.get_content() if part else ''
                    except (LookupError,UnicodeError):body=(part.get_payload(decode=True) or b'').decode('utf-8',errors='replace') if part else ''
                    if part and part.get_content_type()=='text/html':body=html_to_text(body)
                    row=dict(sender=str(msg.get('From','')),subject=str(msg.get('Subject','')),body=body,
                             archive=filename,source_member=member.name,spam_label=group=='spam')
                    key=input_digest(row);body_key=digest(' '.join(body[:4000].split()).encode())
                    if not body.strip() or key in seen or body_key in bodies:continue
                    seen.add(key);bodies.add(body_key);row['id']=key[:20]
                    # Review unquoted leading text, keeping quoted material in stored inputs.
                    lead='\n'.join(l for l in body[:4000].split('\n') if not l.lstrip().startswith('>'))[:1500]
                    action=re.search(r'(?i)(?:can|could|would) (?:you|someone|anyone)\b|please (?:send|review|confirm|help|check|fix|provide|let)|(?:how (?:do|can|should) I|does anyone|any ideas|deadline|due to expire)',lead)
                    news=re.search(r'(?i)newsletter|dispatch|digest|weekly|daily|CNET|ZDNET|lockergnome|winxpnews',row['subject']+' '+lead[:700]) or body.startswith('URL:')
                    if group=='spam':bucket='spam'
                    elif news:bucket='newsletter'
                    elif group=='easy_ham' and action and not body.startswith('URL:'):
                        bucket='important';row['sampling_evidence']=action.group()
                    elif group=='easy_ham' and not news and not action:bucket='routine'
                    else:continue
                    pools[bucket].append(row)
    # Review additional candidates so ambiguous cases can be replaced before inference.
    candidates={key:random.Random(20261110).sample(rows,min(len(rows),130 if key=='important' else 100)) for key,rows in pools.items()}
    (OUT/'category-candidates.json').write_text(json.dumps(candidates,ensure_ascii=False,indent=2),encoding='utf-8')
    print('Pool sizes:',{k:len(v) for k,v in pools.items()})
    print('Review candidates:',{k:len(v) for k,v in candidates.items()})

if __name__=='__main__':main()
