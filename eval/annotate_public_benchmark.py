"""Freeze coding-assistant annotations; this script does not call the tested model.

These annotations were assigned after inspecting the sampled subjects and bodies,
before obtaining classifier predictions. They are not human-verified labels.
"""
import hashlib
import json
from pathlib import Path

OUT = Path(__file__).parent / 'benchmark'
IMPORTANT = {
    'easy_ham-01189': 'Requests repository access and technical investigation before a release.',
    'easy_ham-01527': 'Bug report asks the assigned maintainer to change a trust-related default.',
    'easy_ham-01736': 'Asks for a technical decision about training data and contributing code.',
    'easy_ham-01639': 'Requests publishing training artifacts for other developers.',
    'easy_ham-01217': 'Requests troubleshooting of an upgrade failure.',
    'hard_ham-00003': 'Domain expiration requires the owner to decide whether to renew.',
}
NEWS = {'easy_ham-'+i for i in ('02007','01908','02020','00876','02372','02328','01861','01959','01889')}

def main():
    raw = (OUT / 'public_emails.jsonl').read_bytes()
    rows = [json.loads(line) for line in raw.decode('utf-8').splitlines()]
    annotations = {}
    for row in rows:
        key = row['id']
        if row['spam_label']:
            category, attention, reason = 'spam', False, 'Preserved original corpus spam label; junk does not require a personal reply.'
        elif key in IMPORTANT:
            category, attention, reason = 'important', True, IMPORTANT[key]
        elif key in NEWS or key.startswith('hard_ham-'):
            category, attention, reason = 'newsletter', False, 'Broadcast news, syndicated update, event announcement, or subscribed marketing; no individual response requested.'
        else:
            category, attention, reason = 'routine', False, 'Ordinary discussion, acknowledgement, or technical information; no substantive personal decision required.'
        annotations[key] = dict(category=category, needs_attention=attention, rationale=reason)
    (OUT / 'annotations.json').write_text(json.dumps(annotations, indent=2), encoding='utf-8')
    (OUT / 'annotation_provenance.json').write_text(json.dumps({
        'annotator':'OpenAI coding assistant (AI-assisted, not human verified)',
        'method':'Subject/body inspection and explicit rubric before classifier predictions; original spam/ham labels preserved.',
        'rubric':{'important':'Substantive reply, investigation, decision, or owner action required.',
                  'routine':'Ordinary correspondence/discussion or information needing at most a simple reply.',
                  'newsletter':'Broadcast news, bulk updates or subscribed marketing.',
                  'spam':'Original corpus spam label.',
                  'needs_attention':'Substantive personal action or decision; assessed at original receipt, not at evaluation date.'},
        'limitations':['Recipient role and subscription context are incompletely known.',
                      'Four-category and attention labels are subjective AI annotations.',
                      'Mailing-list questions interpreted from an involved recipient perspective.',
                      'Routine includes discussion requiring no reply because the application has no separate informational class.'],
        'dataset_sha256':hashlib.sha256(raw).hexdigest(),
        'annotations_sha256':hashlib.sha256((OUT/'annotations.json').read_bytes()).hexdigest()
    }, indent=2), encoding='utf-8')

if __name__ == '__main__':
    main()
