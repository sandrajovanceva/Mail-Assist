# Evaluation

Scripts that produce the numbers for the evaluation chapter. All three write
Markdown into `eval/results/` (git-ignored), ready to paste into the thesis.

Run them from the repository root, with the backend virtualenv active:

```powershell
backend\.venv\Scripts\python eval\classification_eval.py
backend\.venv\Scripts\python eval\style_eval.py --judge
backend\.venv\Scripts\python eval\cost_report.py
```

## 1. Classification accuracy

```powershell
python eval\classification_eval.py --model gemini-2.5-flash-lite
python eval\classification_eval.py --model gemini-3.5-flash-lite   # compare
```

Reports per-category precision/recall/F1, macro-F1, a confusion matrix, and the
`needs_attention` flag scored separately — that flag is what decides whether a
human gets interrupted, so its recall matters more than overall accuracy.

`dataset.jsonl` ships with 29 labelled examples (8 important, 8 routine,
7 newsletter, 6 spam). **Expand it with your own mail before reporting
results** — 29 examples gives roughly ±9% confidence intervals, which is thin
for a thesis claim. 100–150 hand-labelled examples is a defensible size, and
labelling your own inbox is also the honest test: the categories are
subjective, and your judgement is the ground truth the app is trying to match.

Results are cached per model in `results/<model>.jsonl`, so if a free-tier
daily quota runs out mid-run, re-running the same command tomorrow continues
from where it stopped. `--fresh` clears the cache, `--limit N` caps a run, and
`--sleep` (default 1s) spaces calls out under the per-minute limit.

## 2. Style matching

```powershell
copy eval\style_dataset.example.jsonl eval\style_dataset.jsonl
# fill it with real (incoming email, reply you actually sent) pairs
python eval\style_eval.py --judge
```

An **ablation**: each email is drafted twice, once with the learned style
profile injected and once without, and both are compared against the reply you
really sent. That comparison is the point — the absolute numbers mean little,
the gap between the two arms is the evidence that personalisation works.

Put the style guide the app learned (Settings → Your voice) into
`eval/style_profile.txt` so the "with profile" arm has something to use.

Metrics are lexical (length ratio, sentence length, vocabulary overlap,
greeting/sign-off match) plus an optional `--judge` arm where the model rates
voice similarity 1–5. Report both, and be explicit in the write-up that an
LLM judge is a weak proxy for human judgement — if you can get 2–3 people to
blind-rate a sample of drafts, that result is worth more than either metric.

## 3. Cost and latency

```powershell
python eval\cost_report.py --days 30
```

Aggregates the `llm_calls` table the app writes on every Gemini call: tokens,
latency (mean and p95), and list-price cost per operation and per model, plus
a projected cost per 1,000 emails triaged.

A free-tier key spends $0 in reality — the dollar figures are Google's published
paid-tier prices, recorded at call time so the report answers what the system
*would* cost to run at scale. Prices are in `backend/app/services/usage.py`;
they were verified on 2026-09-22 and the 3.6/3.7/3.8-flash models are at
introductory rates that double on 2027-01-01.

## Notes on quotas

Free-tier Gemini keys are limited per minute *and* per day, and the limits are
per-account (check yours at <https://aistudio.google.com/rate-limit>). Newer
flash models have much tighter daily caps than the lite models, which is why
`CLASSIFIER_MODEL` defaults to a lite model — classification is the operation
that runs on every single email.

If an evaluation run keeps stopping on quota, either switch models in `.env` or
enable prepay billing: at list price a 150-example classification run on
`gemini-2.5-flash-lite` costs well under one cent.


### Evaluation validity

Classification predictions are reused only when the message inputs, model,
thinking configuration, and classifier implementation fingerprint match.
Ground-truth labels are always read from the current dataset. Reports include
coverage; incomplete runs score only completed predictions and must not be
presented as full-dataset results.

Style evaluation requires a non-empty `eval/style_profile.txt` and
`eval/style_profile.sources.json`, a JSON array of the example IDs used to
learn that guide (for example `["training-001", "training-002"]`). Training
IDs must be non-empty and disjoint from the evaluation dataset IDs. Use
separate source emails too; an ID check cannot detect renamed copies.
Both comparison arms keep identity, signature, tone and knowledge fixed;
only the profile changes. Word metrics support Cyrillic and other Unicode letters.

Usage rows represent individual API attempts, including failed retries.
Empty or malformed outputs count as failures while retaining returned token
counts. Prices are configured list-price estimates, not measured billing;
unpriced models are explicitly shown as unknown rather than free.
