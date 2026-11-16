"""Download and deterministically sample SpamAssassin without sending any mail."""
import hashlib
import io
import json
import random
import tarfile
import urllib.request
import sys
from email import policy
from email.parser import BytesParser
from pathlib import Path

OUT = Path(__file__).parent / 'benchmark'
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'backend'))
from app.services.gmail import html_to_text
SOURCES = {'spam': ('20030228_spam.tar.bz2', 50),
           'easy_ham': ('20030228_easy_ham.tar.bz2', 40),
           'hard_ham': ('20030228_hard_ham.tar.bz2', 10)}

def main():
    OUT.mkdir(exist_ok=True)
    rows, manifest = [], []
    for group, (filename, count) in SOURCES.items():
        url = 'https://spamassassin.apache.org/old/publiccorpus/' + filename
        archive = OUT / filename
        if not archive.exists():
            with urllib.request.urlopen(url, timeout=45) as response:
                archive.write_bytes(response.read())
        raw = archive.read_bytes()
        manifest.append({'url': url, 'sha256': hashlib.sha256(raw).hexdigest()})
        with tarfile.open(fileobj=io.BytesIO(raw), mode='r:bz2') as tf:
            members = sorted([m for m in tf.getmembers() if m.isfile() and '/0' in m.name], key=lambda m: m.name)
            chosen = random.Random(20261109).sample(members, count)
            for member in chosen:
                message = BytesParser(policy=policy.default).parsebytes(tf.extractfile(member).read())
                part = message.get_body(preferencelist=('plain', 'html'))
                try:
                    body = part.get_content() if part else ''
                except (LookupError, UnicodeError):
                    body = (part.get_payload(decode=True) or b'').decode('utf-8', errors='replace') if part else ''
                if part and part.get_content_type() == 'text/html':
                    body = html_to_text(body)
                rows.append({'id': group + '-' + member.name.split('/')[-1].split('.')[0],
                             'sender': str(message.get('From', '')), 'subject': str(message.get('Subject', '')),
                             'body': body, 'source_member': member.name,
                             'spam_label': group == 'spam'})
    (OUT / 'public_emails.jsonl').write_text(''.join(json.dumps(r, ensure_ascii=False)+'\n' for r in rows), encoding='utf-8')
    (OUT / 'sources.json').write_text(json.dumps({'seed':20261109, 'archives':manifest}, indent=2), encoding='utf-8')
    print('Prepared', len(rows), 'messages')

if __name__ == '__main__':
    main()
