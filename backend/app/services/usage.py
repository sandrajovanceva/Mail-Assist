"""Token accounting for every Gemini call.

The thesis evaluation chapter reports cost and latency per operation and per
model. That is only possible if usage is recorded at call time, so every call
in `llm.py` writes an `LLMCall` row — including the ones that fail.

Note on cost: a free-tier API key is billed at $0. The dollar figures here are
Google's *paid list prices*, recorded so the evaluation can answer "what would
this cost at scale?" — which is the interesting question for the write-up, not
the $0 actually spent.
"""

import logging

from sqlalchemy.orm import Session

log = logging.getLogger(__name__)

# USD per 1M tokens (input, output), from Google's published pricing.
# Verified 2026-09-22 against https://ai.google.dev/gemini-api/docs/pricing
# Note: gemini-3.6/3.7/3.8-flash are at introductory rates through 2026-12-31,
# after which they double to $1.50 / $7.50.
PRICING: dict[str, tuple[float, float]] = {
    "gemini-3.8-flash": (0.75, 3.75),
    "gemini-3.7-flash": (0.75, 3.75),
    "gemini-3.6-flash": (0.75, 3.75),
    "gemini-3.5-flash": (1.50, 9.00),
    "gemini-3.5-flash-lite": (0.30, 2.50),
    "gemini-3.1-flash-lite": (0.25, 1.50),
    "gemini-3-flash-preview": (0.50, 3.00),
    "gemini-3.1-pro-preview": (2.00, 12.00),
    "gemini-2.5-pro": (1.25, 10.00),
    "gemini-2.5-flash": (0.30, 2.50),
    "gemini-2.5-flash-lite": (0.10, 0.40),
}


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """List-price cost of one call in USD. Unknown models cost 0 (and warn)."""
    prices = PRICING.get(model)
    if not prices:
        log.warning("No price on file for model %r — cost recorded as 0", model)
        return 0.0
    in_price, out_price = prices
    return (input_tokens * in_price + output_tokens * out_price) / 1_000_000


def record_call(
    db: Session | None,
    *,
    user_id: int | None,
    operation: str,
    model: str,
    operation_id: str = "",
    attempt: int = 1,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_read_tokens: int = 0,
    latency_ms: int = 0,
    success: bool = True,
    error: str = "",
) -> None:
    """Persist one call. Never raises — telemetry must not break the feature."""
    if db is None:
        return
    from ..models import LLMCall  # local import keeps services import-light

    try:
        db.add(
            LLMCall(
                user_id=user_id,
                operation=operation,
                model=model,
                operation_id=operation_id,
                attempt=attempt,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cache_read_tokens=cache_read_tokens,
                latency_ms=latency_ms,
                cost_usd=estimate_cost(model, input_tokens, output_tokens),
                success=success,
                error=error[:2000],
            )
        )
        db.commit()
    except Exception:  # pragma: no cover - telemetry is best effort
        log.exception("Failed to record LLM usage")
        db.rollback()
