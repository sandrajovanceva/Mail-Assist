# Evaluation

The [final evaluation report](final/FINAL_EVALUATION.md) covers a 1,000-email
spam benchmark, a 30-pair synthetic style experiment and automated software checks.

- Spam accuracy: **90.9%**, counting failed responses as incorrect.
- Style profiles improved vocabulary overlap and sentence-length similarity to
  synthetic reference replies. This does not establish real-user style matching.
- Four-category classification and `needs_attention` have not been evaluated
  against human-verified labels.

## Reports and data

- [Final report](final/FINAL_EVALUATION.md): methods, results, limitations and validation.
- [Spam report](final/SPAM_REPORT.md): classification metrics and API reliability.
- [Frozen benchmark manifest](final/manifest.json): dataset and classifier fingerprints.
- [Pilot documentation](benchmark/README.md): earlier 100-email experiment, kept separate.
- [Pilot API usage](benchmark/USAGE_REPORT.md): usage from the pilot database only.

The spam benchmark uses the original SpamAssassin spam/ham labels. The style
experiment uses synthetic training and test messages. Pilot category annotations
were AI-assisted; agreement with those annotations is not verified accuracy.

## Regenerate reports

Run from the repository root with the backend dependencies installed:

```powershell
backend/.venv/Scripts/python eval/final_evaluation.py report
backend/.venv/Scripts/python eval/final_report.py
```

These commands use saved results and make no API requests. They require the
local result artifacts; API usage summaries also require the evaluation databases.

## Additional experiments

`classification_eval.py`, `style_eval.py` and `cost_report.py` support separate
experiments with custom datasets. Their outputs are not part of the final results
above. Style training examples must be separate from evaluation examples.

Running experiments can make API requests. Check the selected model and project
limits in Google AI Studio before starting. Cost figures use the application's
configured pricing table and are estimates, not billing records.
