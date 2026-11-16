# Pilot API usage and latency
Usage from the pilot evaluation database, including failed requests and retries. Excludes the final 1,000-email benchmark, the final 30-pair style experiment and unrelated app activity.
Latency below is per successful API attempt, not user-perceived end-to-end time. Failed attempts are counted separately.
| Operation | Model | Attempts | Failed | Input tokens | Output tokens | Mean ms | p95 ms | Configured USD estimate |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| classify | gemini-3.5-flash-lite | 125 | 10 | 62910 | 4508 | 963 | 1515 | 0.030143 |
| draft | gemini-3.5-flash-lite | 16 | 0 | 9598 | 485 | 751 | 1047 | 0.004092 |
| style | gemini-3.5-flash-lite | 1 | 0 | 239 | 173 | 1671 | 1671 | 0.000504 |
| style | gemini-3.8-flash | 2 | 2 | 0 | 0 | N/A | N/A | 0.000000 |
Dollar estimates use the application pricing table, which was not independently verified in this run. They are not a bill. The user confirmed a Free-tier project. No estimate of actual paid spending is made.