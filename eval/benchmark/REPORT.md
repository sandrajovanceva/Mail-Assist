# Public email benchmark

Model: `gemini-3.5-flash-lite`. Coverage: 99/100.
Status: ALL MESSAGES ATTEMPTED; some have no valid prediction. Conditional metrics below exclude failures.
Attempted inputs: 100/100. Inputs without a valid prediction: 1.
Dataset seed: 20261109. Classifier input: From, Subject, first 4,000 body characters.

## Original corpus labels: spam detection

Accuracy: 0.919. Precision: 0.936. Recall: 0.898. F1: 0.917.
TP=44, FP=3, FN=5, TN=47. Positive class is spam.
Full-dataset spam accuracy counting missing/invalid predictions as incorrect: 0.910.
Full-dataset spam recall counting invalid spam predictions as misses: 0.880.
Majority-class baseline on completed examples: 0.505.
Descriptive 95% Wilson interval for accuracy: [0.849, 0.958].
This interval assumes independent examples and does not capture corpus shift or model pretraining overlap.
The original corpus supplies these binary labels. A balanced sample is not a natural inbox prevalence estimate.

## Agreement with AI-assisted four-category annotations

The following labels were assigned by the coding assistant before running predictions.
They are not human-verified ground truth. These metrics measure annotation agreement,
not verified personal-inbox accuracy. Original corpus spam labels were preserved.
Per-category support matters: zero-support classes and attention flags cannot be assessed from an incomplete sample.
Full-dataset four-category agreement counting missing/invalid predictions as incorrect: 0.770.

**Examples:** 99  
**Overall accuracy:** 77.8%

### Per-category

| Category | Support | Precision | Recall | F1 |
|---|---:|---:|---:|---:|
| important | 6 | 1.00 | 0.33 | 0.50 |
| routine | 26 | 0.93 | 0.50 | 0.65 |
| newsletter | 18 | 0.50 | 1.00 | 0.67 |
| spam | 49 | 0.94 | 0.90 | 0.92 |

**Macro-F1:** 0.683

### Confusion matrix (rows = true, columns = predicted)

| | important | routine | newsletter | spam |
|---|---:|---:|---:|---:|
| **important** | 2 | 1 | 3 | 0 |
| **routine** | 0 | 13 | 10 | 3 |
| **newsletter** | 0 | 0 | 18 | 0 |
| **spam** | 0 | 0 | 5 | 44 |

### `needs_attention` flag

- Precision: 1.00 (of those flagged, how many really needed you)
- Recall: 0.33 (of those that needed you, how many were flagged)
- F1: 0.50
- Missed (4): messages that needed attention but were not flagged

### Misclassified

- `easy_ham-01240` **routine → newsletter** — 'Re: K3B' (This is a mailing list discussion message regarding technical support.)
- `easy_ham-01038` **routine → newsletter** — 'Re: ALSA (almost) made easy' (This is a mailing list discussion about software packaging and bugs.)
- `easy_ham-00661` **routine → spam** — 'Re: Avast there matey' (This is an unsolicited mailing list message containing casual internet chatter and inappropriate humor.)
- `easy_ham-01527` **important → newsletter** — '[SAdev] [Bug 1052] New: bondedsender.com is a scam' (This is an automated bug tracking notification from a mailing list.)
- `easy_ham-01518` **routine → newsletter** — 'Re: [SAtalk] 2.42: est release?' (This is a mailing list discussion about software release updates.)
- `easy_ham-01522` **routine → newsletter** — "[SAdev] [Bug 1046] Errors from 'perl Makefile.PL'" (This is an automated bug tracker notification email from a development mailing list.)
- `easy_ham-00731` **routine → spam** — 'Re: Goodbye Global Warming' (The email appears to be unsolicited junk or off-topic conversational bickering.)
- `easy_ham-01305` **routine → newsletter** — 'Re: Apt 0.3 and 0.5' (This is a mailing list discussion message regarding software interoperability.)
- `spam-00477` **spam → newsletter** — 'Free Shipping on all orders at Blair.com' (This is a marketing email offering promotional discounts and free shipping.)
- `easy_ham-01736` **important → routine** — '[Spambayes] deleting "duplicate" spam before training?  good idea or\tbad?' (This is a technical mailing list discussion about spam handling that does not require urgent personal action.)
- `spam-00071` **spam → newsletter** — 'WebDesignHQ Newsletter' (This is a bulk marketing email promoting a software release and includes an unsubscribe link.)
- `spam-00291` **spam → newsletter** — 'The TBA Doctor Walks the Walk on Diabetes' (This is a marketing email sent to insurance professionals regarding diabetes case studies.)
- `easy_ham-00034` **routine → newsletter** — '[ILUG] Re: Sun Solaris' (This is a mailing list discussion thread rather than a personal message requiring action.)
- `spam-00239` **spam → newsletter** — 'NEW TECHNOLOGY - DIGITAL VIDEO RECORDER (Smart IP Technology)' (This is a promotional marketing email for a digital video recorder.)
- `easy_ham-01189` **important → newsletter** — 'hi' (This is a mailing list discussion about RPM packages and GStreamer, which does not require direct personal action.)
- `easy_ham-01461` **routine → newsletter** — 'Re: [SAtalk] Re: [SAdev] File::Spec and v2.41' (This is a technical discussion from a mailing list.)
- `easy_ham-00195` **routine → newsletter** — 'Re: [zzzzteana] Secondhand books online' (This is a mailing list discussion about secondhand books and is not personally addressed.)
- `easy_ham-01297` **routine → newsletter** — 'Re: xmms and .mp3 files.' (This is a technical discussion from a mailing list regarding MP3 support and plugins.)
- `easy_ham-01217` **important → newsletter** — 'Re: use new apt to do null to RH8 upgrade?' (This is a discussion post from a mailing list.)
- `spam-00498` **spam → newsletter** — '' (This is a promotional marketing email with discount offers and gift announcements from a store.)
- `easy_ham-00566` **routine → spam** — 'Bush blew take two' (This is unsolicited political commentary and link sharing from an unknown sender.)
- `easy_ham-00652` **routine → newsletter** — 'Re: Oh ma gawd - funny site of the day...' (This is an archival mailing list discussion with no personal action required.)