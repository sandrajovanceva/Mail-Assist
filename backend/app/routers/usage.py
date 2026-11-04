"""Token, latency and cost reporting — the data behind the evaluation chapter.

Everything here reads the `llm_calls` table that `services/llm.py` writes to,
so the numbers are measured rather than estimated.
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import LLMCall, User
from ..schemas import UsageRow, UsageSummary
from ..security import get_current_user
from ..services.usage import PRICING

router = APIRouter(prefix="/usage", tags=["usage"])


def _percentile(values: list[int], fraction: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    index = min(int(round(fraction * (len(ordered) - 1))), len(ordered) - 1)
    return ordered[index]


@router.get("/summary", response_model=UsageSummary)
def summary(
    days: int | None = Query(None, ge=1, le=365),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Spend and latency grouped by (operation, model).

    Aggregated in Python rather than SQL so the p95 is exact and the query
    works identically on SQLite and PostgreSQL.
    """
    query = db.query(LLMCall).filter(LLMCall.user_id == user.id)
    if days:
        from datetime import datetime, timedelta, timezone

        query = query.filter(
            LLMCall.created_at >= datetime.now(timezone.utc) - timedelta(days=days)
        )

    buckets: dict[tuple[str, str], dict] = {}
    for call in query.all():
        key = (call.operation, call.model)
        bucket = buckets.setdefault(
            key,
            {
                "calls": 0,
                "failures": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "cost_usd": 0.0,
                "latencies": [],
            },
        )
        bucket["calls"] += 1
        if not call.success:
            bucket["failures"] += 1
        bucket["input_tokens"] += call.input_tokens
        bucket["output_tokens"] += call.output_tokens
        bucket["cost_usd"] += call.cost_usd
        if call.success:
            bucket["latencies"].append(call.latency_ms)

    rows = [
        UsageRow(
            operation=operation,
            model=model,
            calls=b["calls"],
            failures=b["failures"],
            unpriced_calls=b["calls"] if model not in PRICING else 0,
            input_tokens=b["input_tokens"],
            output_tokens=b["output_tokens"],
            cost_usd=round(b["cost_usd"], 6) if model in PRICING else None,
            avg_latency_ms=int(sum(b["latencies"]) / len(b["latencies"])) if b["latencies"] else 0,
            p95_latency_ms=_percentile(b["latencies"], 0.95),
        )
        for (operation, model), b in sorted(buckets.items())
    ]

    return UsageSummary(
        rows=rows,
        total_calls=sum(r.calls for r in rows),
        unpriced_calls=sum(r.unpriced_calls for r in rows),
        total_cost_usd=round(sum(r.cost_usd for r in rows), 6) if all(r.cost_usd is not None for r in rows) else None,
        total_input_tokens=sum(r.input_tokens for r in rows),
        total_output_tokens=sum(r.output_tokens for r in rows),
    )
