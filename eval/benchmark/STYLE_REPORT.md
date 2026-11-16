# Controlled synthetic style ablation
Model: `gemini-3.5-flash-lite`. Complete pairs: 8/8.
Six synthetic training replies; eight disjoint synthetic test replies. Not a personal-style accuracy measurement.
One profile-on and one profile-off draft per input; arm order randomized. Same identity, signature, tone and empty knowledge.
| Arm | Length ratio | Sentence length delta | Vocabulary overlap | Greeting | Sign-off | Judge |
|---|---:|---:|---:|---:|---:|---:|
| with | 1.25 | 0.9 | 0.551 | 8/8 | 8/8 | — |
| without | 2.19 | 5.3 | 0.284 | 0/8 | 8/8 | — |
Length ratio ideally 1; sentence-length delta ideally 0; other metrics higher means more similar. No human or LLM judge was used. These metrics do not measure factual correctness.
The configured gemini-3.8-flash model timed out during profile learning. This completed experiment uses the alternative lite model named above; it does not validate the configured drafting model. Sign-off matches are partly constrained by the identical supplied signature in both arms.