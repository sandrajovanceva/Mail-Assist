"""Usage accounting — the measurements the evaluation chapter reports."""

from app.security import decode_token
from app.services import usage


def test_cost_uses_published_prices():
    # gemini-2.5-flash-lite: $0.10 in / $0.40 out per 1M tokens
    cost = usage.estimate_cost("gemini-2.5-flash-lite", 1_000_000, 1_000_000)
    assert round(cost, 6) == 0.50


def test_unknown_models_cost_zero_rather_than_crash():
    assert usage.estimate_cost("some-model-we-never-priced", 1000, 1000) == 0.0


def test_record_call_is_a_no_op_without_a_session():
    usage.record_call(None, user_id=1, operation="draft", model="x")  # must not raise


def test_summary_groups_by_operation_and_model(client, auth, db, user_token):
    user_id = decode_token(user_token)
    for latency in (100, 200, 300):
        usage.record_call(
            db,
            user_id=user_id,
            operation="classify",
            model="gemini-2.5-flash-lite",
            input_tokens=1000,
            output_tokens=100,
            latency_ms=latency,
        )
    usage.record_call(
        db,
        user_id=user_id,
        operation="draft",
        model="gemini-3.8-flash",
        input_tokens=2000,
        output_tokens=500,
        latency_ms=900,
    )
    usage.record_call(
        db,
        user_id=user_id,
        operation="draft",
        model="gemini-3.8-flash",
        success=False,
        error="429",
        latency_ms=50,
    )

    body = client.get("/usage/summary", headers=auth).json()
    rows = {(r["operation"], r["model"]): r for r in body["rows"]}

    classify = rows[("classify", "gemini-2.5-flash-lite")]
    assert classify["calls"] == 3
    assert classify["input_tokens"] == 3000
    assert classify["avg_latency_ms"] == 200
    assert classify["p95_latency_ms"] == 300

    draft = rows[("draft", "gemini-3.8-flash")]
    assert draft["calls"] == 2
    assert draft["failures"] == 1
    # the failed call contributes no tokens and no latency to the average
    assert draft["avg_latency_ms"] == 900

    assert body["total_calls"] == 5
    assert body["total_cost_usd"] > 0


def test_summary_is_per_user(client, db, auth, user_token):
    from tests.conftest import register

    usage.record_call(
        db,
        user_id=decode_token(user_token),
        operation="draft",
        model="gemini-3.8-flash",
        input_tokens=10,
    )
    other = register(client, email="other@example.com")
    headers = {"Authorization": f"Bearer {other['token']}"}

    assert client.get("/usage/summary", headers=headers).json()["total_calls"] == 0
    assert client.get("/usage/summary", headers=auth).json()["total_calls"] == 1


def test_get_client_is_thread_safe(monkeypatch):
    """Concurrent first-use must produce exactly one client.

    Sync classifies emails from a thread pool; two threads racing to build the
    client left one of them holding a garbage-collected (closed) client, which
    failed mid-request with "Cannot send a request, as the client has been
    closed".
    """
    import threading
    from concurrent.futures import ThreadPoolExecutor

    from app.config import settings
    from app.services import llm

    monkeypatch.setattr(settings, "gemini_api_key", "test-key")
    monkeypatch.setattr(llm, "_client", None)

    built = []
    start = threading.Barrier(8)

    class FakeClient:
        def __init__(self, **kwargs):
            built.append(self)

    monkeypatch.setattr(llm.genai, "Client", FakeClient)

    def race():
        start.wait()
        return llm.get_client()

    with ThreadPoolExecutor(max_workers=8) as pool:
        clients = [f.result() for f in [pool.submit(race) for _ in range(8)]]

    assert len(built) == 1, f"built {len(built)} clients, expected 1"
    assert len({id(c) for c in clients}) == 1, "threads got different clients"
