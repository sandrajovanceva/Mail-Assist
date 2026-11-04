"""Cost and latency report, straight from the `llm_calls` table.

    python eval/cost_report.py            # everything ever recorded
    python eval/cost_report.py --days 7

Every Gemini call the app makes is logged with tokens, latency and list-price
cost, so this is measured data rather than an estimate. On a free-tier key the
actual spend is $0 — the dollar column answers "what would this cost at scale?",
which is the question worth putting in the write-up.
"""

import sys

# Windows consoles default to cp1252; eval output contains arrows, emoji and
# Cyrillic subject lines, so force UTF-8 rather than crashing on a print().
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


import argparse
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.database import SessionLocal  # noqa: E402
from app.models import LLMCall
from app.services.usage import PRICING  # noqa: E402

RESULTS_DIR = Path(__file__).parent / "results"


def percentile(values: list[int], fraction: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    return ordered[min(int(round(fraction * (len(ordered) - 1))), len(ordered) - 1)]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=0, help="only the last N days")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        query = db.query(LLMCall)
        if args.days:
            query = query.filter(
                LLMCall.created_at >= datetime.now(timezone.utc) - timedelta(days=args.days)
            )
        calls = query.all()
    finally:
        db.close()

    if not calls:
        print("No calls recorded yet — use the app or run an eval script first.")
        return 1

    buckets: dict[tuple[str, str], list[LLMCall]] = defaultdict(list)
    for call in calls:
        buckets[(call.operation, call.model)].append(call)

    lines = [
        "# Cost and latency\n",
        f"{len(calls)} API attempts for {len({c.operation_id or str(c.id) for c in calls})} logical operations"
        + (f" over the last {args.days} days" if args.days else "")
        + f", {sum(1 for c in calls if not c.success)} failed.\n",
        "| Operation | Model | Calls | Fail | In tok | Out tok | Avg ms | p95 ms | Cost (USD) | Per call |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]

    for (operation, model), group in sorted(buckets.items()):
        latencies = [c.latency_ms for c in group if c.success]
        cost = sum(c.cost_usd for c in group)
        cost_text = f"{cost:.4f} | {cost / len(group):.6f}" if model in PRICING else "Unknown | Unknown"
        lines.append(
            f"| {operation} | `{model}` | {len(group)} | "
            f"{sum(1 for c in group if not c.success)} | "
            f"{sum(c.input_tokens for c in group):,} | "
            f"{sum(c.output_tokens for c in group):,} | "
            f"{int(statistics.mean(latencies)) if latencies else 0} | "
            f"{percentile(latencies, 0.95)} | "
            f"{cost_text} |"
        )

    unknown = sum(c.model not in PRICING for c in calls)
    if unknown:
        lines.append(f"\n{unknown} calls have unknown prices; totals below are known-price subtotals.")
    total = sum(c.cost_usd for c in calls if c.model in PRICING)
    lines.append(f"\n**Total (list price):** ${total:.4f}")

    classify = [c for c in calls if c.operation == "classify"]
    if classify and all(c.model in PRICING for c in classify):
        per_email = sum(c.cost_usd for c in classify) / len({c.operation_id or str(c.id) for c in classify})
        lines.append(
            f"\n**Triage cost:** ${per_email:.6f} per email "
            f"→ ${per_email * 1000:.2f} per 1,000 emails classified."
        )

    text = "\n".join(lines)
    RESULTS_DIR.mkdir(exist_ok=True)
    out = RESULTS_DIR / "cost-report.md"
    out.write_text(text, encoding="utf-8")
    print(text)
    print(f"\nWritten to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
