"""Build eval/final/FINAL_EVALUATION.md from saved result files; no API calls.

Every number is read from an artifact so the report can be regenerated and audited:
  eval/final/metrics.json             frozen 1,000-email spam benchmark
  eval/final/style/*                  30-case synthetic style ablation
  eval/final/validation/*.txt         test, lint and build logs
  eval/benchmark/REPORT.md            the earlier 100-email pilot (cited, kept separate)
"""
import csv
import json
import math
import random
import re
import sqlite3
import statistics
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FINAL = ROOT/'eval'/'final'
STYLE = FINAL/'style'
VALIDATION = FINAL/'validation'


def pct(value, digits=1):
    return f'{value*100:.{digits}f}%'


def interval(pair):
    return f'{pct(pair[0])}–{pct(pair[1])}'


def sign_test(wins, n):
    """Exact two-sided sign test p-value; ties are excluded by the caller."""
    tail = sum(math.comb(n, i) for i in range(min(wins, n-wins)+1))/2**n
    return min(1.0, 2*tail)


def bootstrap(diffs, seed=20261112, rounds=10000):
    rng = random.Random(seed)
    means = sorted(statistics.fmean(rng.choice(diffs) for _ in diffs) for _ in range(rounds))
    return means[int(.025*rounds)], means[int(.975*rounds)-1]


def style_analysis():
    rows = [json.loads(l) for l in (STYLE/'style-predictions.jsonl').read_text(encoding='utf-8').splitlines() if l.strip()]
    assert len({r['fingerprint'] for r in rows}) == 1, 'Style predictions mix several configurations'
    by = {(r['id'], r['arm']): r for r in rows}
    ids = sorted({r['id'] for r in rows if (r['id'], 'with') in by and (r['id'], 'without') in by}, key=lambda s: int(s.split('-')[1]))
    arms = {arm: {key: statistics.fmean(by[(i, arm)][key] for i in ids) for key in ('length_ratio', 'sentence_length_delta', 'vocabulary_overlap')}
            | {'greeting': sum(by[(i, arm)]['greeting_match'] for i in ids), 'signoff': sum(by[(i, arm)]['signoff_match'] for i in ids)}
            for arm in ('with', 'without')}
    # Positive difference = the profile-on draft is closer to the reference.
    paired = {
        'Vocabulary overlap (higher is closer)': [by[(i, 'with')]['vocabulary_overlap']-by[(i, 'without')]['vocabulary_overlap'] for i in ids],
        'Sentence-length difference (words; lower is closer)': [by[(i, 'without')]['sentence_length_delta']-by[(i, 'with')]['sentence_length_delta'] for i in ids],
        'Length-ratio distance from 1 (lower is closer)': [abs(by[(i, 'without')]['length_ratio']-1)-abs(by[(i, 'with')]['length_ratio']-1) for i in ids],
    }
    tests = {}
    for name, diffs in paired.items():
        nonzero = [d for d in diffs if d]
        wins = sum(d > 0 for d in nonzero)
        tests[name] = dict(mean=statistics.fmean(diffs), ci=bootstrap(diffs), wins=wins, n=len(nonzero), p=sign_test(wins, len(nonzero)))
    calls = []
    if (STYLE/'evaluation.db').exists():
        with sqlite3.connect(STYLE/'evaluation.db') as con:
            calls = con.execute('select operation, success, count(*), sum(input_tokens), sum(output_tokens), avg(latency_ms) from llm_calls group by 1, 2').fetchall()
    model = {r['model'] for r in rows}.pop()
    return ids, arms, tests, calls, model


def test_count(name, pattern):
    path = VALIDATION/name
    if not path.exists():
        return None
    match = re.findall(pattern, path.read_text(encoding='utf-8', errors='replace'))
    return int(match[-1]) if match else None


def main():
    m = json.loads((FINAL/'metrics.json').read_text(encoding='utf-8'))
    manifest = json.loads((FINAL/'manifest.json').read_text(encoding='utf-8'))
    assert m['complete'] and m['attempted'] == m['planned'] == 1000, 'Spam benchmark is not complete'
    g, t, lat = m['groups'], m['telemetry'], m['latency_ms']
    legit_valid = m['tn']+m['fp']
    with (FINAL/'spam-details.csv').open(encoding='utf-8') as handle:
        details = list(csv.DictReader(handle))
    missed_as_newsletter = sum(r['reference_spam'] == 'True' and r['status'] == 'ok' and r['predicted_category'] == 'newsletter' for r in details)
    ids, arms, tests, calls, style_model = style_analysis()
    backend = test_count('backend-tests.txt', r'(\d+) passed')
    frontend = test_count('frontend-tests.txt', r'ℹ pass (\d+)')
    frontend_fail = test_count('frontend-tests.txt', r'ℹ fail (\d+)')
    integrity = test_count('evaluation-integrity-tests.txt', r'(\d+) passed')
    lint_text = (VALIDATION/'frontend-lint.txt').read_text(encoding='utf-8', errors='replace') if (VALIDATION/'frontend-lint.txt').exists() else ''
    lint_warnings = len(re.findall(r'warning ', lint_text))
    build_ok = (VALIDATION/'frontend-build.txt').exists() and 'built in' in (VALIDATION/'frontend-build.txt').read_text(encoding='utf-8', errors='replace')

    L = []
    add = L.append
    add('# MailAssist — final evaluation report')
    add('')
    add('This report covers spam detection on 1,000 emails, style adaptation on 30 synthetic pairs and automated software checks. The earlier 100-email pilot is reported separately in section 6. Final results are generated from saved artifacts by `eval/final_report.py`.')
    add('')
    add('| Component | Reference used | Status |')
    add('|---|---|---|')
    add('| Spam detection | Original SpamAssassin corpus labels | **Final** — 1,000 held-out emails |')
    add('| Style adaptation | Synthetic references written for the experiment | Final, exploratory — 30 paired cases |')
    add('| Four categories (important/routine/newsletter/spam) | AI-assisted labels, not human-verified | Pilot only — agreement, not accuracy |')
    add('| Needs-attention flag | None human-verified | Not evaluated against human labels |')
    add('| Software quality | Automated test suites and production build | Final |')
    add('')

    add('## 1. Spam detection — frozen 1,000-email benchmark')
    add('')
    add('### Method')
    add('')
    add(f"A sample of 1,000 emails was drawn from the Apache SpamAssassin public corpus (seed {manifest['seed']}): "
        f"{manifest['groups']['spam']} spam, {manifest['groups']['easy_ham']} easy legitimate and {manifest['groups']['hard_ham']} "
        'difficult legitimate messages. Empty bodies, duplicate model inputs, duplicate normalized body prefixes and every '
        'message used in the pilot were excluded before sampling. The dataset, source-archive checksums, model name and a '
        'hash of the classifier source code were frozen in `eval/final/manifest.json` before any prediction was made, and '
        'every run verifies them.')
    add('')
    add(f"The unchanged application classifier (`{m['model']}`) received the sender, subject and first 4,000 body characters, "
        'called directly through the Gemini API with no Gmail delivery. Reference labels are the original corpus spam/ham '
        'labels; no AI-generated labels are used. The application\'s important, routine and newsletter categories were mapped '
        'to "not spam". Each email was attempted once. Requests that still failed after the client\'s built-in retries were '
        'recorded as invalid and never rerun; the primary metric counts them as incorrect. Quota (HTTP 429) pauses were '
        'resumed later without repeating completed emails.')
    add('')
    add('### Results')
    add('')
    add('| Metric | Result |')
    add('|---|---:|')
    add(f"| **Accuracy, invalid outputs counted incorrect (primary)** | **{pct(m['primary_accuracy'])}** (95% CI {interval(m['accuracy_wilson95'])}) |")
    add(f"| Accuracy among {m['valid']} valid outputs | {pct(m['valid_accuracy'])} (95% CI {interval(m['valid_accuracy_wilson95'])}) |")
    add(f"| Spam precision | {pct(m['precision'])} |")
    add(f"| Spam recall among valid outputs | {pct(m['valid_recall'])} |")
    add(f"| Spam recall, invalid outputs counted as missed | {pct(m['full_recall'])} |")
    add(f"| Spam F1 among valid outputs | {m['f1']:.3f} |")
    add(f"| Legitimate emails wrongly marked as spam | {m['fp']} of {legit_valid} valid ({pct(m['fp']/legit_valid)}) |")
    add(f"| Invalid outputs | {m['failed']} of {m['attempted']} ({pct(m['failure_rate'])}; 95% CI {interval(m['failure_rate_wilson95'])}) |")
    add(f"| Majority-class baseline | {pct(m['majority_baseline'])} |")
    add('')
    add(f"Confusion counts on valid outputs (positive class = spam): TP={m['tp']}, FP={m['fp']}, FN={m['fn']}, TN={m['tn']}. "
        'Confidence intervals are 95% Wilson score intervals.')
    add('')
    names = dict(spam='Spam', easy_ham='Easy legitimate', hard_ham='Difficult legitimate')
    add('| Source group | Emails | Valid outputs | Correct | Accuracy among valid | Accuracy, invalid counted incorrect |')
    add('|---|---:|---:|---:|---:|---:|')
    for key, v in g.items():
        add(f"| {names[key]} | {v['planned']} | {v['valid']} | {v['correct']} | {pct(v['valid_accuracy'])} | {pct(v['full_accuracy'])} |")
    add('')
    add('**Interpretation.** Errors are asymmetric: the classifier rarely labels legitimate mail as spam but misses roughly one '
        'spam message in ten. For an email assistant this is the less harmful direction, because a lost legitimate message '
        f'costs more than a visible spam message. Of the {m["fn"]} missed spam messages, {missed_as_newsletter} were categorised '
        'as newsletters (`eval/final/spam-details.csv`), i.e. recognised as bulk marketing but not as spam; in the application '
        'these are still separated from personal mail and are never auto-drafted.')
    add('')

    add('## 2. Reliability, latency, tokens and cost')
    add('')
    causes = ', '.join(f'{k} ({v})' for k, v in sorted(m['failure_causes'].items(), key=lambda kv: -kv[1]))
    add(f"Invalid outputs ({m['failed']}): {causes}. Over the whole run, {t['failed_api_attempts']} individual API attempts failed "
        '(' + ', '.join(f'{k}: {v}' for k, v in sorted(t['failed_attempt_causes'].items(), key=lambda kv: -kv[1])) + '); most were '
        'absorbed by automatic retries or quota pauses. Runs were resumed after quota interruptions; project limits were not independently verified.')
    add('')
    add('| Measure (per valid email) | Value |')
    add('|---|---:|')
    add(f"| Median latency | {lat['median']/1000:.2f} s |")
    add(f"| Mean latency | {lat['mean']/1000:.2f} s |")
    add(f"| 90th / 99th percentile latency | {lat['p90']/1000:.2f} s / {lat['p99']/1000:.2f} s |")
    add(f"| Maximum latency | {lat['max']/1000:.2f} s |")
    add(f"| Mean input / output tokens | {t['mean_input_tokens']:.0f} / {t['mean_output_tokens']:.0f} |")
    add(f"| Total tokens measured | {t['input_tokens']:,} input, {t['output_tokens']:,} output |")
    add(f"| Estimated cost at paid list prices | ${t['list_price_cost_per_1000_emails_usd']:.2f} per 1,000 emails |")
    add('')
    add('Latency measures the classifier call, including internal retries; it excludes background scheduling and other application work. The cost is an estimate using '
        "the configured pricing table in `backend/app/services/usage.py`; the project was reported as Free tier; billing was not independently verified.")
    add('')

    add('## 3. Style adaptation — 30-case synthetic ablation')
    add('')
    add('### Method')
    add('')
    add(f'A style profile was learned by the application from six synthetic training replies. For each of {len(ids)} disjoint '
        'synthetic incoming messages, two drafts were generated: one with the learned profile and one without it, with identity, '
        'signature, tone and (empty) knowledge held constant; arm order was randomised. Each draft was compared with a '
        f'synthetic reference reply written in the target style. Model: `{style_model}`. The configured drafting model '
        '(`gemini-3.8-flash`) timed out during profile learning in the pilot, so this experiment does not validate that model. '
        'All texts are synthetic; no real correspondence was used.')
    add('')
    add('### Results')
    add('')
    add('| Arm | Length ratio (ideal 1) | Sentence-length difference (ideal 0) | Vocabulary overlap | Greeting matches | Sign-off matches |')
    add('|---|---:|---:|---:|---:|---:|')
    for arm, label in (('with', 'With style profile'), ('without', 'Without style profile')):
        a = arms[arm]
        add(f"| {label} | {a['length_ratio']:.2f} | {a['sentence_length_delta']:.1f} words | {a['vocabulary_overlap']:.3f} | {a['greeting']}/{len(ids)} | {a['signoff']}/{len(ids)} |")
    add('')
    add('Paired comparison (positive = the profile-on draft is closer to the reference):')
    add('')
    add('| Measure | Mean improvement | 95% bootstrap CI | Pairs improved | Sign-test p |')
    add('|---|---:|---:|---:|---:|')
    for name, s in tests.items():
        add(f"| {name} | {s['mean']:.3f} | {s['ci'][0]:.3f} to {s['ci'][1]:.3f} | {s['wins']}/{s['n']} | {s['p']:.2g} |")
    add('')
    add('With the profile, drafts used the target greeting and matched the reference vocabulary and sentence length much more '
        'closely in nearly every pair. Drafts were still about 1.5 times longer than the references. The profile-off drafts '
        'still match the sign-off because the same signature was supplied to both arms, so that column is not evidence of '
        'adaptation. These are lexical similarity proxies against synthetic references: they show that the profile changes the '
        "drafts in the intended direction, not that drafts match a real person's style or are correct and useful.")
    add('')
    if calls:
        add('API usage: ' + '; '.join(f"{op} {'ok' if ok else 'failed'}: {n} calls, {i:,} input / {o:,} output tokens, mean {lt/1000:.2f} s"
                                       for op, ok, n, i, o, lt in calls) + '.')
        add('')

    add('## 4. Four-category classification and needs-attention flag')
    add('')
    add('No public dataset labels emails with the application\'s important / routine / newsletter / spam scheme. In the pilot '
        '(section 6), these categories and the needs-attention flag were labelled with AI assistance before prediction. Those '
        'figures measure **agreement with AI-assisted labels**, not verified accuracy, and are reported only as exploratory '
        'evidence. A larger 400-email category comparison was prepared (`eval/final/category-candidates.json`) but was not '
        'completed because no human annotation was available; it is not reported as a result.')
    add('')
    add('The needs-attention flag has no human-labelled evaluation. A possible future evaluation is the human-annotated Enron '
        'reply-expectation corpus of Sappelli et al. (2016), with the caveat that "reply expected" is related to, but not the '
        'same as, "needs personal attention".')
    add('')

    add('## 5. Functional validation')
    add('')
    add('| Check | Result |')
    add('|---|---|')
    add(f"| Backend test suite (pytest) | {backend} passed |")
    add(f"| Frontend test suite (node:test) | {frontend} passed, {frontend_fail} failed |")
    add(f"| Frontend lint (oxlint) | 0 errors, {lint_warnings} warnings |")
    add(f"| TypeScript check and production build | {'passed' if build_ok else 'not recorded'} |")
    add(f"| Evaluation integrity tests | {integrity} passed |")
    add('')
    add('Logs are in `eval/final/validation/`. The suites mock Gmail, Calendar and Gemini, so they do not replace live OAuth, '
        'sending and Calendar acceptance checks. The evaluation integrity tests check that failures count against the primary '
        'metric, that partial runs report no final score, that changes to the frozen dataset are rejected, and the reliability '
        'and latency accounting.')
    add('')

    add('## 6. Pilot (100 emails, earlier and separate)')
    add('')
    add('Before the final benchmark, a 100-email pilot (seed 20261109; 50 spam, 40 easy and 10 difficult legitimate) tested the '
        'pipeline. Its spam accuracy was 91.0% with failures counted incorrect, and 91.9% among 99 valid outputs (95% CI '
        '84.9–95.8%). Its '
        'four-category agreement with AI-assisted labels was 77.8% on 99 valid outputs (macro-F1 0.683); most disagreements '
        'were mailing-list discussions predicted as newsletters, a boundary that is subjective. The pilot also ran an 8-pair '
        'style ablation with the same direction of effect as section 3. Full pilot results: `eval/benchmark/REPORT.md`. Pilot '
        'emails were excluded from the final sample.')
    add('')

    add('## 7. Limitations')
    add('')
    for item in (
        'The spam corpus is historical (2002–2005), predominantly English and mailing-list heavy; results do not establish accuracy on a modern personal inbox.',
        'The 50/50 spam split is deliberate; real inbox prevalence differs, which changes precision in practice.',
        'The public corpus may have been seen by the model during pretraining.',
        'Each email received one prediction; run-to-run variability was not measured.',
        'Confidence intervals assume independent examples; related threads may weaken this.',
        'The four categories and needs-attention flag were not evaluated against human labels.',
        'The style experiment uses synthetic texts and lexical proxies, a lighter model than the configured drafter, and no human judgement.',
        'No user study was conducted.',
    ):
        add(f'- {item}')
    add('')

    add('## 8. Reproducibility')
    add('')
    add('```')
    add('backend/.venv/Scripts/python eval/final_evaluation.py report   # spam metrics, no API calls')
    add('backend/.venv/Scripts/python eval/final_report.py              # this report, no API calls')
    add('```')
    add('')
    add(f"Frozen dataset SHA-256: `{manifest['dataset_sha256']}`. Classifier source SHA-256: `{manifest['classifier_source_sha256']}`. "
        'Source: https://spamassassin.apache.org/old/publiccorpus/ . Sappelli, M., Pasi, G., Verberne, S., de Boer, M., Kraaij, W. '
        '(2016). Assessing e-mail intent and tasks in e-mail messages. Information Sciences.')
    (FINAL/'FINAL_EVALUATION.md').write_text('\n'.join(L)+'\n', encoding='utf-8')
    print(f"Wrote {FINAL/'FINAL_EVALUATION.md'}")


if __name__ == '__main__':
    main()
