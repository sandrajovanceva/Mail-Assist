"""Measure classification accuracy against a hand-labelled dataset.

    python eval/classification_eval.py --model gemini-2.5-flash-lite

Reports per-category precision / recall / F1, a confusion matrix, and the same
for the binary `needs_attention` flag, then writes a Markdown report you can
paste into the evaluation chapter.

Built for a free-tier key: results are appended to a per-model cache as they
come in, and a re-run skips everything already done. If the daily quota runs
out mid-way, run it again tomorrow and it picks up where it stopped.
"""

import sys

# Windows consoles default to cp1252; eval output contains arrows, emoji and
# Cyrillic subject lines, so force UTF-8 rather than crashing on a print().
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


import argparse
import hashlib
import json
import sys
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.config import settings  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.services import llm  # noqa: E402

CATEGORIES = ["important", "routine", "newsletter", "spam"]
RESULTS_DIR = Path(__file__).parent / "results"


def load_dataset(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def load_cache(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as handle:
        return {json.loads(line)["id"]: json.loads(line) for line in handle if line.strip()}


def prediction_fingerprint(row: dict) -> str:
    payload = {key: row[key] for key in ("sender", "subject", "body")}
    payload.update(model=settings.classifier_model, thinking=settings.classifier_thinking_level,
                   implementation=Path(llm.__file__).read_text(encoding="utf-8"))
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def score_current_labels(dataset: list[dict], cache: dict) -> list[dict]:
    return [{**cache[row["id"]], "expected_category": row["category"],
             "expected_attention": row["needs_attention"], "subject": row["subject"]}
            for row in dataset if row["id"] in cache
            and cache[row["id"]].get("fingerprint") == prediction_fingerprint(row)]


def prf(true_positive: int, false_positive: int, false_negative: int) -> tuple[float, float, float]:
    precision = true_positive / (true_positive + false_positive) if true_positive + false_positive else 0.0
    recall = true_positive / (true_positive + false_negative) if true_positive + false_negative else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return precision, recall, f1


def evaluate(rows: list[dict]) -> str:
    """Turn predictions into the report text."""
    matrix: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for row in rows:
        matrix[row["expected_category"]][row["predicted_category"]] += 1

    lines: list[str] = []
    correct = sum(1 for r in rows if r["expected_category"] == r["predicted_category"])
    lines.append(f"**Examples:** {len(rows)}  ")
    lines.append(f"**Overall accuracy:** {correct / len(rows):.1%}\n")

    lines.append("### Per-category\n")
    lines.append("| Category | Support | Precision | Recall | F1 |")
    lines.append("|---|---:|---:|---:|---:|")
    f1_scores = []
    for category in CATEGORIES:
        tp = matrix[category][category]
        fp = sum(matrix[other][category] for other in CATEGORIES if other != category)
        fn = sum(matrix[category][other] for other in CATEGORIES if other != category)
        support = tp + fn
        precision, recall, f1 = prf(tp, fp, fn)
        f1_scores.append(f1)
        lines.append(
            f"| {category} | {support} | {precision:.2f} | {recall:.2f} | {f1:.2f} |"
        )
    lines.append(f"\n**Macro-F1:** {sum(f1_scores) / len(f1_scores):.3f}\n")

    lines.append("### Confusion matrix (rows = true, columns = predicted)\n")
    lines.append("| | " + " | ".join(CATEGORIES) + " |")
    lines.append("|---|" + "---:|" * len(CATEGORIES))
    for category in CATEGORIES:
        cells = " | ".join(str(matrix[category][p]) for p in CATEGORIES)
        lines.append(f"| **{category}** | {cells} |")

    # the attention flag is what decides whether a human gets interrupted,
    # so it is reported separately from the 4-way category
    tp = sum(1 for r in rows if r["expected_attention"] and r["predicted_attention"])
    fp = sum(1 for r in rows if not r["expected_attention"] and r["predicted_attention"])
    fn = sum(1 for r in rows if r["expected_attention"] and not r["predicted_attention"])
    precision, recall, f1 = prf(tp, fp, fn)
    lines.append("\n### `needs_attention` flag\n")
    lines.append(f"- Precision: {precision:.2f} (of those flagged, how many really needed you)")
    lines.append(f"- Recall: {recall:.2f} (of those that needed you, how many were flagged)")
    lines.append(f"- F1: {f1:.2f}")
    lines.append(f"- Missed ({fn}): messages that needed attention but were not flagged")

    misses = [r for r in rows if r["expected_category"] != r["predicted_category"]]
    if misses:
        lines.append("\n### Misclassified\n")
        for row in misses:
            lines.append(
                f"- `{row['id']}` **{row['expected_category']} → {row['predicted_category']}** "
                f"— {row['subject']!r} ({row.get('reason', '')})"
            )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default=settings.classifier_model)
    parser.add_argument("--dataset", type=Path, default=Path(__file__).parent / "dataset.jsonl")
    parser.add_argument("--sleep", type=float, default=1.0, help="seconds between calls")
    parser.add_argument("--limit", type=int, default=0, help="stop after N new examples")
    parser.add_argument("--fresh", action="store_true", help="ignore cached predictions")
    args = parser.parse_args()

    settings.classifier_model = args.model  # what llm.classify_email reads
    RESULTS_DIR.mkdir(exist_ok=True)
    cache_path = RESULTS_DIR / f"{args.model}.jsonl"
    if args.fresh and cache_path.exists():
        cache_path.unlink()

    dataset = load_dataset(args.dataset)
    cache = load_cache(cache_path)
    todo = [row for row in dataset if cache.get(row["id"], {}).get("fingerprint") != prediction_fingerprint(row)]
    if args.limit:
        todo = todo[: args.limit]

    print(f"Model: {args.model}")
    print(f"Dataset: {len(dataset)} examples, {len(cache)} cached, {len(todo)} to classify\n")

    db = SessionLocal()
    try:
        for index, row in enumerate(todo, start=1):
            try:
                result = llm.classify_email(
                    row["sender"], row["subject"], row["body"], db=db, user_id=None
                )
            except llm.QuotaExceeded as exc:
                print(f"\nStopped at {index}/{len(todo)}: {exc}")
                print("Re-run this command later — finished examples are cached.")
                break
            except Exception as exc:  # noqa: BLE001 - keep going, report at the end
                print(f"  {row['id']}: FAILED ({exc})")
                continue

            record = {
                "id": row["id"],
                "fingerprint": prediction_fingerprint(row),
                "subject": row["subject"],
                "expected_category": row["category"],
                "predicted_category": result["category"],
                "expected_attention": row["needs_attention"],
                "predicted_attention": result["needs_attention"],
                "reason": result["reason"],
            }
            with cache_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record) + "\n")
            cache[row["id"]] = record

            mark = "ok " if record["expected_category"] == record["predicted_category"] else "MISS"
            print(f"  [{index}/{len(todo)}] {mark} {row['id']}: {result['category']}")
            if args.sleep:
                time.sleep(args.sleep)  # stay under the per-minute free-tier limit
    finally:
        db.close()

    scored = score_current_labels(dataset, cache)
    if not scored:
        print("\nNothing to score yet.")
        return 1

    report = f"Coverage: {len(scored)}/{len(dataset)} examples. Missing predictions are excluded from metrics.\n\n" + f"# Classification evaluation — `{args.model}`\n\n" + evaluate(scored)
    report_path = RESULTS_DIR / f"{args.model}-report.md"
    report_path.write_text(report, encoding="utf-8")
    print("\n" + report)
    print(f"\nWritten to {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
