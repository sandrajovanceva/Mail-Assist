"""Turn the labelled spreadsheet into the evaluation dataset.

    python eval/import_labels.py

Reads `eval/to_label.csv`, checks every label, and writes `eval/dataset.jsonl`
— the file `classification_eval.py` scores against.

Rows left blank are skipped with a warning rather than guessed at: a missing
label is missing data, and filling it in automatically would corrupt the
ground truth the whole evaluation rests on.
"""

import argparse
import csv
import json
import sys
from collections import Counter
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = Path(__file__).parent
CATEGORIES = {"important", "routine", "newsletter", "spam"}
YES = {"yes", "y", "true", "1", "da", "да"}
NO = {"no", "n", "false", "0", "ne", "не"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", type=Path, default=HERE / "to_label.csv")
    parser.add_argument("--out", type=Path, default=HERE / "dataset.jsonl")
    parser.add_argument("--append", action="store_true", help="add to the existing dataset")
    args = parser.parse_args()

    if not args.csv.exists():
        print(f"No {args.csv.name} — run export_for_labelling.py first.")
        return 1

    rows, skipped, problems = [], 0, []
    with args.csv.open(encoding="utf-8-sig", newline="") as handle:
        for line_no, row in enumerate(csv.DictReader(handle), start=2):
            category = (row.get("category") or "").strip().lower()
            attention = (row.get("needs_attention") or "").strip().lower()

            if not category and not attention:
                skipped += 1
                continue
            if category not in CATEGORIES:
                problems.append(f"  line {line_no}: category {category!r} is not one of {sorted(CATEGORIES)}")
                continue
            if attention not in YES | NO:
                problems.append(f"  line {line_no}: needs_attention {attention!r} should be yes or no")
                continue

            rows.append(
                {
                    "id": row["id"],
                    "sender": row["sender"],
                    "subject": row["subject"],
                    "body": row["body"],
                    "category": category,
                    "needs_attention": attention in YES,
                }
            )

    if problems:
        print(f"{len(problems)} row(s) need fixing:")
        print("\n".join(problems[:20]))
        print("\nNothing written. Fix those cells and run again.")
        return 1

    if not rows:
        print("No labelled rows found — the category column is still empty.")
        return 1

    existing = []
    if args.append and args.out.exists():
        with args.out.open(encoding="utf-8") as handle:
            existing = [json.loads(line) for line in handle if line.strip()]
        seen = {r["id"] for r in existing}
        rows = [r for r in rows if r["id"] not in seen]

    with args.out.open("w", encoding="utf-8") as handle:
        for row in existing + rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    total = len(existing) + len(rows)
    print(f"Wrote {total} labelled examples to {args.out.name} ({len(rows)} new)")
    if skipped:
        print(f"Skipped {skipped} unlabelled row(s).")

    spread = Counter(r["category"] for r in existing + rows)
    print("\nPer category:")
    for category in sorted(CATEGORIES):
        count = spread.get(category, 0)
        flag = "  <- thin" if count < 15 else ""
        print(f"  {category:11} {count:3}{flag}")
    attention = sum(1 for r in existing + rows if r["needs_attention"])
    print(f"\nneeds_attention: {attention} of {total}")
    if total < 100:
        print(f"\n{total} examples is on the small side — aim for 100+ before reporting results.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
