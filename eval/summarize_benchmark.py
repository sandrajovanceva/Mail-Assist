"""Export measured telemetry and a thesis-ready account of completed evidence."""
import json
import csv
import platform
import sqlite3
import statistics
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT/'eval'/'benchmark'
sys.path.insert(0, str(ROOT/'backend'))
from app.config import settings
from app.services.usage import PRICING

def main():
    connection = sqlite3.connect(OUT/'evaluation.db')
    connection.row_factory = sqlite3.Row
    calls = [dict(row) for row in connection.execute('select * from llm_calls')]
    connection.close()
    # Intentionally omit request error strings; only model/operation/usage metadata is exported.
    fields = ('operation','model','operation_id','attempt','input_tokens','output_tokens','latency_ms','success','cost_usd')
    exported = [{k:r[k] for k in fields} for r in calls]
    (OUT/'telemetry.json').write_text(json.dumps(exported,indent=2),encoding='utf-8')
    lines = ['# Pilot API usage and latency',
             'Usage from the pilot evaluation database, including failed requests and retries. Excludes the final 1,000-email benchmark, the final 30-pair style experiment and unrelated app activity.',
             'Latency below is per successful API attempt, not user-perceived end-to-end time. Failed attempts are counted separately.',
             '| Operation | Model | Attempts | Failed | Input tokens | Output tokens | Mean ms | p95 ms | Configured USD estimate |',
             '|---|---|---:|---:|---:|---:|---:|---:|---:|']
    for operation,model in sorted({(r['operation'],r['model']) for r in calls}):
        rows = [r for r in calls if r['operation']==operation and r['model']==model]
        times = sorted(r['latency_ms'] for r in rows if r['success'])
        mean = str(round(statistics.mean(times))) if times else 'N/A'
        p95 = str(times[min(round(.95*(len(times)-1)),len(times)-1)]) if times else 'N/A'
        cost = f"{sum(r['cost_usd'] for r in rows):.6f}" if model in PRICING else 'Unknown'
        lines.append(f"| {operation} | {model} | {len(rows)} | {sum(not r['success'] for r in rows)} | {sum(r['input_tokens'] for r in rows)} | {sum(r['output_tokens'] for r in rows)} | {mean} | {p95} | {cost} |")
    lines.append('Dollar estimates use the application pricing table, which was not independently verified in this run. They are not a bill. The user confirmed a Free-tier project. No estimate of actual paid spending is made.')
    (OUT/'USAGE_REPORT.md').write_text('\n'.join(lines),encoding='utf-8')
    labels = json.loads((OUT/'annotations.json').read_text(encoding='utf-8'))
    predictions = [json.loads(line) for line in (OUT/'predictions.jsonl').read_text(encoding='utf-8').splitlines()]
    latest_fingerprint = predictions[-1]['fingerprint'] if predictions else None
    current = {r['id']:r for r in predictions if r['fingerprint']==latest_fingerprint}
    with (OUT/'classification-details.csv').open('w',newline='',encoding='utf-8') as handle:
        writer = csv.writer(handle)
        writer.writerow(['id','reference_category','predicted_category','reference_attention','predicted_attention','reason'])
        for key,label in labels.items():
            prediction = current.get(key,{})
            writer.writerow([key,label['category'],prediction.get('category',''),label['needs_attention'],prediction.get('needs_attention',''),prediction.get('reason','')])
    manifest = {'generated_utc':datetime.now(timezone.utc).isoformat(),'python':platform.python_version(),
                'classifier':settings.classifier_model,'classifier_thinking_level':settings.classifier_thinking_level,
                'drafter':settings.drafter_model,'drafter_thinking_level':settings.drafter_thinking_level,
                'label_distribution':dict(Counter(r['category'] for r in labels.values())),
                'attention_positive':sum(r['needs_attention'] for r in labels.values()),
                'billing_status':'Free tier, reported by user; not verified through account API'}
    (OUT/'run_manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    classification=(OUT/'REPORT.md').read_text(encoding='utf-8')
    style=(OUT/'STYLE_REPORT.md').read_text(encoding='utf-8') if (OUT/'STYLE_REPORT.md').exists() else 'The controlled style experiment has not completed. No style improvement claim is supported.'
    text = '''# Evaluation chapter material

## Method

MailAssist was evaluated using a fixed sample of 100 real messages from the Apache
SpamAssassin public corpus: 50 spam, 40 easy legitimate messages and 10 difficult
legitimate messages. Sampling used seed 20261109. The application parser converted
HTML-only messages to readable text. The unchanged classifier received the sender,
subject and first 4,000 body characters without the reference labels. Evaluation
ran directly through the Gemini API using a separate database, without Gmail delivery.

Original corpus labels were used to evaluate binary spam detection. Important,
routine and newsletter predictions were grouped as non-spam for this comparison.
The coding assistant additionally annotated the four application categories and
the attention flag before prediction. These annotations were not verified by a
human; corresponding metrics measure agreement with AI-assisted annotations.
Accuracy, per-class precision, recall and F1, macro-F1 and a confusion matrix were
reported. Missing predictions were excluded with coverage explicitly reported.

## Classification results

''' + classification + '\n\n## Controlled style experiment\n\n' + style + '''

## Functional validation

The backend regression suite passed 201 tests and the frontend suite passed 19.
The TypeScript check and Vite production build passed. Frontend lint completed
with four warnings. These suites use mocked external services and therefore do
not replace live OAuth, sending and Calendar acceptance tests.

## Limitations

This sample is small, English-language and historical. Mailing-list content is
overrepresented, and the balanced spam/non-spam design does not reflect a natural
inbox distribution. Model exposure to the public corpus during pretraining is
unknown. There are only six assistant-annotated important examples. The distinction
between routine list discussion and newsletters is subjective. Recipient context
is incomplete, and attention labels reflect original receipt rather than current
deadlines. One generation per input does not estimate run-to-run variability.
No results establish accuracy on the author's personal inbox or writing style.
The controlled style experiment uses synthetic references and lexical proxies;
it is not a human assessment of correctness or usefulness. The separate usage
report distinguishes measured tokens/latency from configured price estimates.

## Sources and reproducibility

Apache SpamAssassin public corpus:
https://spamassassin.apache.org/old/publiccorpus/
Corpus documentation:
https://spamassassin.apache.org/old/publiccorpus/readme.html
Archive checksums, source-member identifiers, annotations, provenance, predictions,
environment metadata and raw validation logs accompany this report.
'''
    (OUT/'THESIS_EVALUATION.md').write_text(text,encoding='utf-8')
    print('Exported telemetry, run manifest and thesis evaluation material.')

if __name__=='__main__':
    main()
