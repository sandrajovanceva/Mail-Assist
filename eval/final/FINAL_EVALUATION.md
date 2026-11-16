# MailAssist — final evaluation report

This report covers spam detection on 1,000 emails, style adaptation on 30 synthetic pairs and automated software checks. The earlier 100-email pilot is reported separately in section 6. Final results are generated from saved artifacts by `eval/final_report.py`.

| Component | Reference used | Status |
|---|---|---|
| Spam detection | Original SpamAssassin corpus labels | **Final** — 1,000 held-out emails |
| Style adaptation | Synthetic references written for the experiment | Final, exploratory — 30 paired cases |
| Four categories (important/routine/newsletter/spam) | AI-assisted labels, not human-verified | Pilot only — agreement, not accuracy |
| Needs-attention flag | None human-verified | Not evaluated against human labels |
| Software quality | Automated test suites and production build | Final |

## 1. Spam detection — frozen 1,000-email benchmark

### Method

A sample of 1,000 emails was drawn from the Apache SpamAssassin public corpus (seed 20261110): 500 spam, 400 easy legitimate and 100 difficult legitimate messages. Empty bodies, duplicate model inputs, duplicate normalized body prefixes and every message used in the pilot were excluded before sampling. The dataset, source-archive checksums, model name and a hash of the classifier source code were frozen in `eval/final/manifest.json` before any prediction was made, and every run verifies them.

The unchanged application classifier (`gemini-3.5-flash-lite`) received the sender, subject and first 4,000 body characters, called directly through the Gemini API with no Gmail delivery. Reference labels are the original corpus spam/ham labels; no AI-generated labels are used. The application's important, routine and newsletter categories were mapped to "not spam". Each email was attempted once. Requests that still failed after the client's built-in retries were recorded as invalid and never rerun; the primary metric counts them as incorrect. Quota (HTTP 429) pauses were resumed later without repeating completed emails.

### Results

| Metric | Result |
|---|---:|
| **Accuracy, invalid outputs counted incorrect (primary)** | **90.9%** (95% CI 89.0%–92.5%) |
| Accuracy among 977 valid outputs | 93.0% (95% CI 91.3%–94.5%) |
| Spam precision | 96.2% |
| Spam recall among valid outputs | 89.5% |
| Spam recall, invalid outputs counted as missed | 86.6% |
| Spam F1 among valid outputs | 0.927 |
| Legitimate emails wrongly marked as spam | 17 of 493 valid (3.4%) |
| Invalid outputs | 23 of 1000 (2.3%; 95% CI 1.5%–3.4%) |
| Majority-class baseline | 50.0% |

Confusion counts on valid outputs (positive class = spam): TP=433, FP=17, FN=51, TN=476. Confidence intervals are 95% Wilson score intervals.

| Source group | Emails | Valid outputs | Correct | Accuracy among valid | Accuracy, invalid counted incorrect |
|---|---:|---:|---:|---:|---:|
| Spam | 500 | 484 | 433 | 89.5% | 86.6% |
| Easy legitimate | 400 | 395 | 378 | 95.7% | 94.5% |
| Difficult legitimate | 100 | 98 | 98 | 100.0% | 98.0% |

**Interpretation.** Errors are asymmetric: the classifier rarely labels legitimate mail as spam but misses roughly one spam message in ten. For an email assistant this is the less harmful direction, because a lost legitimate message costs more than a visible spam message. Of the 51 missed spam messages, 50 were categorised as newsletters (`eval/final/spam-details.csv`), i.e. recognised as bulk marketing but not as spam; in the application these are still separated from personal mail and are never auto-drafted.

## 2. Reliability, latency, tokens and cost

Invalid outputs (23): empty model response (13), HTTP 503 model overloaded (9), network/DNS error (1). Over the whole run, 181 individual API attempts failed (HTTP 503 model overloaded: 93, HTTP 429 quota exceeded: 74, empty model response: 13, network/DNS error: 1); most were absorbed by automatic retries or quota pauses. Runs were resumed after quota interruptions; project limits were not independently verified.

| Measure (per valid email) | Value |
|---|---:|
| Median latency | 1.31 s |
| Mean latency | 2.62 s |
| 90th / 99th percentile latency | 6.08 s / 10.72 s |
| Maximum latency | 33.00 s |
| Mean input / output tokens | 511 / 39 |
| Total tokens measured | 499,706 input, 38,489 output |
| Estimated cost at paid list prices | $0.25 per 1,000 emails |

Latency measures the classifier call, including internal retries; it excludes background scheduling and other application work. The cost is an estimate using the configured pricing table in `backend/app/services/usage.py`; the project was reported as Free tier; billing was not independently verified.

## 3. Style adaptation — 30-case synthetic ablation

### Method

A style profile was learned by the application from six synthetic training replies. For each of 30 disjoint synthetic incoming messages, two drafts were generated: one with the learned profile and one without it, with identity, signature, tone and (empty) knowledge held constant; arm order was randomised. Each draft was compared with a synthetic reference reply written in the target style. Model: `gemini-3.5-flash-lite`. The configured drafting model (`gemini-3.8-flash`) timed out during profile learning in the pilot, so this experiment does not validate that model. All texts are synthetic; no real correspondence was used.

### Results

| Arm | Length ratio (ideal 1) | Sentence-length difference (ideal 0) | Vocabulary overlap | Greeting matches | Sign-off matches |
|---|---:|---:|---:|---:|---:|
| With style profile | 1.54 | 1.4 words | 0.515 | 30/30 | 30/30 |
| Without style profile | 2.09 | 5.8 words | 0.286 | 5/30 | 30/30 |

Paired comparison (positive = the profile-on draft is closer to the reference):

| Measure | Mean improvement | 95% bootstrap CI | Pairs improved | Sign-test p |
|---|---:|---:|---:|---:|
| Vocabulary overlap (higher is closer) | 0.229 | 0.182 to 0.278 | 29/30 | 5.8e-08 |
| Sentence-length difference (words; lower is closer) | 4.455 | 3.493 to 5.468 | 30/30 | 1.9e-09 |
| Length-ratio distance from 1 (lower is closer) | 0.575 | 0.253 to 0.928 | 19/29 | 0.14 |

With the profile, drafts used the target greeting and matched the reference vocabulary and sentence length much more closely in nearly every pair. Drafts were still about 1.5 times longer than the references. The profile-off drafts still match the sign-off because the same signature was supplied to both arms, so that column is not evidence of adaptation. These are lexical similarity proxies against synthetic references: they show that the profile changes the drafts in the intended direction, not that drafts match a real person's style or are correct and useful.

API usage: draft ok: 60 calls, 35,160 input / 1,952 output tokens, mean 2.04 s; style ok: 1 calls, 239 input / 144 output tokens, mean 2.39 s.

## 4. Four-category classification and needs-attention flag

No public dataset labels emails with the application's important / routine / newsletter / spam scheme. In the pilot (section 6), these categories and the needs-attention flag were labelled with AI assistance before prediction. Those figures measure **agreement with AI-assisted labels**, not verified accuracy, and are reported only as exploratory evidence. A larger 400-email category comparison was prepared (`eval/final/category-candidates.json`) but was not completed because no human annotation was available; it is not reported as a result.

The needs-attention flag has no human-labelled evaluation. A possible future evaluation is the human-annotated Enron reply-expectation corpus of Sappelli et al. (2016), with the caveat that "reply expected" is related to, but not the same as, "needs personal attention".

## 5. Functional validation

| Check | Result |
|---|---|
| Backend test suite (pytest) | 223 passed |
| Frontend test suite (node:test) | 23 passed, 0 failed |
| Frontend lint (oxlint) | 0 errors, 4 warnings |
| TypeScript check and production build | passed |
| Evaluation integrity tests | 5 passed |

Logs are in `eval/final/validation/`. The suites mock Gmail, Calendar and Gemini, so they do not replace live OAuth, sending and Calendar acceptance checks. The evaluation integrity tests check that failures count against the primary metric, that partial runs report no final score, that changes to the frozen dataset are rejected, and the reliability and latency accounting.

## 6. Pilot (100 emails, earlier and separate)

Before the final benchmark, a 100-email pilot (seed 20261109; 50 spam, 40 easy and 10 difficult legitimate) tested the pipeline. Its spam accuracy was 91.0% with failures counted incorrect, and 91.9% among 99 valid outputs (95% CI 84.9–95.8%). Its four-category agreement with AI-assisted labels was 77.8% on 99 valid outputs (macro-F1 0.683); most disagreements were mailing-list discussions predicted as newsletters, a boundary that is subjective. The pilot also ran an 8-pair style ablation with the same direction of effect as section 3. Full pilot results: `eval/benchmark/REPORT.md`. Pilot emails were excluded from the final sample.

## 7. Limitations

- The spam corpus is historical (2002–2005), predominantly English and mailing-list heavy; results do not establish accuracy on a modern personal inbox.
- The 50/50 spam split is deliberate; real inbox prevalence differs, which changes precision in practice.
- The public corpus may have been seen by the model during pretraining.
- Each email received one prediction; run-to-run variability was not measured.
- Confidence intervals assume independent examples; related threads may weaken this.
- The four categories and needs-attention flag were not evaluated against human labels.
- The style experiment uses synthetic texts and lexical proxies, a lighter model than the configured drafter, and no human judgement.
- No user study was conducted.

## 8. Reproducibility

```
backend/.venv/Scripts/python eval/final_evaluation.py report   # spam metrics, no API calls
backend/.venv/Scripts/python eval/final_report.py              # this report, no API calls
```

Frozen dataset SHA-256: `bf824daf9ffe6b8bd7e0050388d38e8d40c36b5978e046627ee8bda0210c5147`. Classifier source SHA-256: `30d8685eeb06866644ad9f3bb21242f58ca64fef9b61a71fd162fb41f1c5ed96`. Source: https://spamassassin.apache.org/old/publiccorpus/ . Sappelli, M., Pasi, G., Verberne, S., de Boer, M., Kraaij, W. (2016). Assessing e-mail intent and tasks in e-mail messages. Information Sciences.
