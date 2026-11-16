# MailAssist evaluation evidence

This directory contains real model outputs and reproducible evaluation inputs.
It is separate from the app's inbox and database. No messages are sent through Gmail.

## Public classification benchmark

Source: [Apache SpamAssassin public corpus](https://spamassassin.apache.org/old/publiccorpus/).
Read the [corpus documentation](https://spamassassin.apache.org/old/publiccorpus/readme.html).
The corpus explicitly prohibits sending its messages through a live mail system.
Message copyrights remain with their original senders.

The fixed sample contains 50 spam, 40 easy legitimate and 10 difficult legitimate
emails. Sampling uses Python `random.Random(20261109)` on sorted archive members.
Archive URLs and SHA-256 checksums are saved in `sources.json`; individual source
member names are in `public_emails.jsonl`. MIME text/plain is preferred, with
HTML converted through the application's `html_to_text` function when necessary.
The real application classifier receives the sender, subject and first 4,000 body
characters. Corpus filenames and reference labels are never passed to Gemini.

The original binary spam/ham labels support a spam detection benchmark. Mapping
the model's important/routine/newsletter outputs to non-spam makes this comparison
possible without changing the application's classifier prompt.

Additional category and attention labels are in `annotations.json`, with rationale
and provenance in `annotation_provenance.json`. The coding assistant assigned these
before classifier predictions. These are AI-assisted annotations, not human-verified
ground truth. Four-category and attention metrics therefore measure annotation
agreement. No claim about personal-inbox accuracy is supported.

Limitations: old English-language emails, substantial mailing-list content,
subjective recipient context, few important messages, a deliberately balanced
spam/ham sample, possible overlap with model pretraining, and one stochastic
generation per message. Newsletter versus ordinary list discussion is ambiguous.
Report misclassifications rather than relabelling examples to fit predictions.

## Reproduce from the repository root

```powershell
backend/.venv/Scripts/python eval/prepare_public_benchmark.py
backend/.venv/Scripts/python eval/annotate_public_benchmark.py
backend/.venv/Scripts/python eval/run_public_benchmark.py
backend/.venv/Scripts/python eval/run_controlled_style.py --model gemini-3.5-flash-lite
backend/.venv/Scripts/python eval/summarize_benchmark.py
```

The benchmark and style commands use the configured Gemini key and consume its
quota. They resume compatible cached predictions. `--report-only` regenerates reports
without API calls. Coverage below 100/100 is an incomplete run and must be stated
with every reported metric. Predictions include model, UTC time, latency, and
an implementation/input fingerprint. Raw predictions from older input versions
remain in the file for audit, but are excluded by the fingerprint check.

## Functional validation

The recorded backend suite passed 201 tests; the frontend suite passed 19 tests.
TypeScript and the Vite production build passed. Frontend lint reported four
warnings. Raw logs are saved alongside this file. Tests mock Gmail/Gemini;
these results do not establish live OAuth, delivery, or calendar integration.

## Style experiment

`run_controlled_style.py` runs a separate, explicitly synthetic style ablation.
It learns a profile from six synthetic training replies and compares profile-on
and profile-off drafts against eight held-out synthetic reference replies.
Identity, tone, signature and knowledge are held fixed. This tests adaptation
to a designed writing style, not the author's personal writing or human-rated
reply quality. Lexical similarities are descriptive proxies, not correctness.

## Cost and latency

The evaluation database is local and git-ignored. Telemetry includes failed and
retried requests as well as successful calls, including interrupted pilot runs.
Token counts and latency are measured. Dollar figures from the existing pricing
table are configured estimates, not verified billing. The user confirmed the
project is on the Free tier; account billing cannot be inferred from an API key.
