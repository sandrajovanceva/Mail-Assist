# Held-out 1,000-email spam benchmark

Status: COMPLETE. Attempted 1000/1000; valid outputs 977; failed outputs 23.

Model: `gemini-3.5-flash-lite`. Original corpus labels; no AI annotation in this benchmark.

| Metric | Result |
|---|---:|
| Primary accuracy, invalid outputs counted incorrect | 90.9% |
| Accuracy among valid outputs | 93.0% |
| Spam precision | 96.2% |
| Spam recall among valid outputs | 89.5% |
| Spam F1 among valid outputs | 0.927 |
| Spam recall, failures counted as misses | 86.6% |
| Majority-class baseline | 50.0% |

95% Wilson interval for primary accuracy: 89.0%, 92.5%.

Binary confusion counts on valid outputs: TP=433, FP=17, FN=51, TN=476. Invalid outputs are reported separately.

95% Wilson interval for accuracy among valid outputs: 91.3%, 94.5%.

## Results by source group

| Group | Planned | Valid outputs | Correct | Accuracy among valid | Accuracy, failures incorrect |
|---|---:|---:|---:|---:|---:|
| Spam | 500 | 484 | 433 | 89.5% | 86.6% |
| Easy legitimate | 400 | 395 | 378 | 95.7% | 94.5% |
| Difficult legitimate | 100 | 98 | 98 | 100.0% | 98.0% |

## Reliability

Invalid outputs: 23 of 1000 attempted (2.3%; 95% Wilson interval 1.5%, 3.4%). Causes: empty model response: 13, HTTP 503 model overloaded: 9, network/DNS error: 1. Each input was attempted once; the client retried transient errors inside that attempt, and an input still failing afterwards was recorded as invalid and never rerun.

API attempts that failed, including retried attempts and quota pauses: 181 (HTTP 503 model overloaded: 93, HTTP 429 quota exceeded: 74, empty model response: 13, network/DNS error: 1). HTTP 429 quota responses paused the run and the input was retried after cooldown; they are not counted as invalid outputs.

## Latency, tokens and estimated cost

Latency per valid email, including in-call retries (n=977): median 1.31 s, mean 2.62 s, 90th percentile 6.08 s, 99th percentile 10.72 s, maximum 33.00 s.

Tokens measured by the API over 977 successful calls: 499,706 input and 38,489 output (mean 511 input and 39 output per email).

Estimated cost at Google's paid list prices for `gemini-3.5-flash-lite`: $0.2461 in total, about $0.25 per 1,000 emails. This is a hypothetical scaling estimate; the evaluation ran on a free-tier key and actually cost $0.

Sample: 500 spam, 400 easy legitimate and 100 difficult legitimate messages. Exact model-input and normalized body-prefix duplicates and pilot messages were excluded before sampling.

Primary results are final only after every input was attempted. Errors are retained, not rerun until they become correct. API quota interruptions can resume without repeating completed inputs.

Limitations: historical predominantly English public emails, mailing-list-heavy legitimate messages, deliberately balanced prevalence, possible model pretraining exposure, and one stochastic prediction per example. Confidence intervals assume independent examples; related threads may weaken this assumption. No personal-inbox accuracy claim is supported.

Source: https://spamassassin.apache.org/old/publiccorpus/readme.html